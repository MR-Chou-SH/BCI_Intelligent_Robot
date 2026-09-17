"""M13.6 MuJoCo -> Quest visual-state protocol and software runner.

The existing TCP 11001 selection/control lifecycle is intentionally untouched.
This module uses a small UDP latest-state-wins stream on port 11002. Public
frames contain frozen logical block IDs only; MuJoCo ``obj_N`` names stay
inside the sampler's private binding layer.
"""

import argparse
from dataclasses import dataclass
import json
import math
import socket
import sys
import threading
import time
from typing import Callable, Mapping, Optional, Sequence, Tuple

from integration.m9_mujoco_execution import DEFAULT_M9_SCENE_BINDINGS


M13_6_PROTOCOL_VERSION = 1
M13_6_MESSAGE_TYPE = "m13_6_robot_world_state"
M13_6_UDP_PORT = 11002
M13_6_MAPPING_VERSION = "m9-virtual-block-logical-mapping-v1"
M13_6_DEFAULT_RATE_HZ = 30.0
M13_6_MUJOCO_TABLE_TOP_Z = 0.75
M13_6_DEFAULT_SCALE = 0.70
M13_6_VISUAL_BLOCK_EDGE_MUJOCO = 0.080
M13_6_VISUAL_BLOCK_EDGE_QUEST = M13_6_VISUAL_BLOCK_EDGE_MUJOCO * M13_6_DEFAULT_SCALE
M13_6_VISUAL_BLOCK_HALF_HEIGHT_MUJOCO = M13_6_VISUAL_BLOCK_EDGE_MUJOCO / 2.0
# The visual rig already owns the single MuJoCo-Z-up -> Unity-Y-up basis.
# Keep this legacy export for callers, but it is no longer an independent
# robot correction.
M13_6_ROBOT_BASE_YAW_OFFSET_DEGREES = 0.0
M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST = (
    (-0.320, 0.0425, 0.000),
    (-0.105, 0.0425, 0.000),
    (0.105, 0.0425, 0.000),
    (0.320, 0.0425, 0.000),
)
M13_6_SEQUENTIAL_PLACE_TARGETS = {
    "block_sim_01": (0.29, 0.24),
    "block_sim_02": (0.41, 0.24),
    "block_sim_03": (0.29, 0.36),
    "block_sim_04": (0.41, 0.36),
}
# M13.6 uses actual MuJoCo geometry rather than the old flat 2x2 fixture.
# The first column is red-on-bottom/green-on-top; the second is
# blue-on-bottom/yellow-on-top.  Z is derived from the compiled scene
# half-heights plus the tabletop height and a small release clearance.
M13_6_STACK_CLEARANCE_METERS = 0.010
M13_6_VISUAL_STACK_TARGETS = {
    "block_sim_01": {"position": (0.05, -0.08, M13_6_MUJOCO_TABLE_TOP_Z + M13_6_VISUAL_BLOCK_HALF_HEIGHT_MUJOCO), "support": None},
    "block_sim_02": {"position": (0.05, -0.08, M13_6_MUJOCO_TABLE_TOP_Z + 3.0 * M13_6_VISUAL_BLOCK_HALF_HEIGHT_MUJOCO + M13_6_STACK_CLEARANCE_METERS), "support": "block_sim_01"},
    "block_sim_03": {"position": (0.28, -0.08, M13_6_MUJOCO_TABLE_TOP_Z + M13_6_VISUAL_BLOCK_HALF_HEIGHT_MUJOCO), "support": None},
    "block_sim_04": {"position": (0.28, -0.08, M13_6_MUJOCO_TABLE_TOP_Z + 3.0 * M13_6_VISUAL_BLOCK_HALF_HEIGHT_MUJOCO + M13_6_STACK_CLEARANCE_METERS), "support": "block_sim_03"},
}
M13_6_VISUAL_EXECUTION_ORDER = (
    "block_sim_01", "block_sim_03", "block_sim_02", "block_sim_04",
)
M13_6_VISUAL_SEQUENTIAL_PLACE_TARGETS = {
    logical_id: tuple(target["position"][:2])
    for logical_id, target in M13_6_VISUAL_STACK_TARGETS.items()
}
M13_6_VISUAL_PLACE_REGION_HALF_SIZE = 0.060
_EPSILON = 1e-9


def _m13_6_resolve_stack_target(mujoco, model, data, logical_block_id, body_ids):
    """Resolve a stack target from the actual settled support body pose."""
    spec = M13_6_VISUAL_STACK_TARGETS[logical_block_id]
    nominal = tuple(float(value) for value in spec["position"])
    support_id = spec["support"]
    if support_id is None:
        return nominal

    from utils.gripper_scene import object_half_extents

    support_body_id = body_ids[support_id]
    support_name = DEFAULT_M9_SCENE_BINDINGS[support_id]
    target_name = DEFAULT_M9_SCENE_BINDINGS[logical_block_id]
    support_half_height = object_half_extents(model, support_name)[2]
    target_half_height = object_half_extents(model, target_name)[2]
    support_position = data.xpos[support_body_id]
    return (
        float(support_position[0]),
        float(support_position[1]),
        float(support_position[2] + support_half_height + target_half_height + M13_6_STACK_CLEARANCE_METERS),
    )


def _m13_6_set_object_collision_category(mujoco, model, logical_block_id, category):
    """Set one logical object's MuJoCo collision category without teleporting it."""
    simulator_name = DEFAULT_M9_SCENE_BINDINGS[logical_block_id]
    body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, simulator_name))
    first_geom = int(model.body_geomadr[body_id])
    geom_count = int(model.body_geomnum[body_id])
    for geom_id in range(first_geom, first_geom + geom_count):
        model.geom_contype[geom_id] = int(category)
        model.geom_conaffinity[geom_id] = int(category)


def _m13_6_prepare_stack_collision_categories(mujoco, model, logical_block_ids):
    """Use category 2 for resting blocks; category 1 is reserved for active grasp."""
    for geom_id in range(model.ngeom):
        if mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id) == "tabletop":
            model.geom_conaffinity[geom_id] = int(model.geom_conaffinity[geom_id]) | 2
    for logical_id in logical_block_ids:
        _m13_6_set_object_collision_category(mujoco, model, logical_id, 2)


def _quat_wxyz_to_matrix(quaternion):
    w, x, y, z = _quat_normalize(quaternion)
    return (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )


def _transpose_matrix_vector(matrix, vector):
    return tuple(sum(matrix[row][column] * vector[row] for row in range(3)) for column in range(3))


def _matrix_rpy_degrees(matrix):
    roll = math.degrees(math.atan2(matrix[2][1], matrix[2][2]))
    pitch = math.degrees(math.atan2(-matrix[2][0], math.sqrt(matrix[2][1] ** 2 + matrix[2][2] ** 2)))
    yaw = math.degrees(math.atan2(matrix[1][0], matrix[0][0]))
    return roll, pitch, yaw


def m13_6_gripper_block_relative_pose(mujoco, model, data, logical_block_id, body_ids=None):
    """Return the actual MuJoCo gripper->block relative pose for an invariant test."""
    body_ids = body_ids or {
        logical_id: int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, simulator_name))
        for logical_id, simulator_name in DEFAULT_M9_SCENE_BINDINGS.items()
    }
    gripper_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "umi_umi_gripper_base"))
    block_id = body_ids[logical_block_id]
    gripper_position = tuple(float(value) for value in data.xpos[gripper_id])
    block_position = tuple(float(value) for value in data.xpos[block_id])
    gripper_rotation = _quat_wxyz_to_matrix(data.xquat[gripper_id])
    delta = tuple(block_position[index] - gripper_position[index] for index in range(3))
    return {
        "positionGripperFrameMujoco": _transpose_matrix_vector(gripper_rotation, delta),
        "quaternionGripperFrameMujocoWxyz": _quat_wxyz_multiply(
            _quat_wxyz_conjugate(data.xquat[gripper_id]), data.xquat[block_id]
        ),
    }


def m13_6_collect_stack_metrics(mujoco, model, data, body_ids):
    """Collect logical-ID-only physical stack/contact/stability evidence."""
    from utils.gripper_scene import object_half_extents

    poses = {}
    for logical_id, body_id in body_ids.items():
        quaternion = tuple(float(value) for value in data.xquat[body_id])
        matrix = _quat_wxyz_to_matrix(quaternion)
        position = tuple(float(value) for value in data.xpos[body_id])
        velocity = tuple(float(value) for value in data.cvel[body_id])
        poses[logical_id] = {
            "positionMujoco": position,
            "quaternionMujocoWxyz": quaternion,
            "rpyDegrees": _matrix_rpy_degrees(matrix),
            "linearSpeedMetersPerSecond": math.sqrt(sum(value * value for value in velocity[3:])),
            "topTiltDegrees": math.degrees(math.acos(max(-1.0, min(1.0, matrix[2][2])))),
        }

    pairs = {}
    for logical_id, spec in M13_6_VISUAL_STACK_TARGETS.items():
        support = spec["support"]
        if support is None:
            continue
        first_body = body_ids[support]
        second_body = body_ids[logical_id]
        first_name = DEFAULT_M9_SCENE_BINDINGS[support]
        second_name = DEFAULT_M9_SCENE_BINDINGS[logical_id]
        first_geom = int(model.body_geomadr[first_body])
        second_geom = int(model.body_geomadr[second_body])
        contact_distances = []
        for index in range(data.ncon):
            contact = data.contact[index]
            if {int(contact.geom1), int(contact.geom2)} == {first_geom, second_geom}:
                contact_distances.append(float(contact.dist))
        first_position = poses[support]["positionMujoco"]
        second_position = poses[logical_id]["positionMujoco"]
        expected_z = first_position[2] + object_half_extents(model, first_name)[2] + object_half_extents(model, second_name)[2]
        pairs[support + "+" + logical_id] = {
            "contactCount": len(contact_distances),
            "minDistanceMeters": min(contact_distances) if contact_distances else None,
            "penetrationDepthMeters": max(0.0, -min(contact_distances)) if contact_distances else 0.0,
            "horizontalErrorMeters": math.dist(first_position[:2], second_position[:2]),
            "verticalGapMeters": second_position[2] - expected_z,
        }

    max_speed = max((pose["linearSpeedMetersPerSecond"] for pose in poses.values()), default=0.0)
    return {
        "poses": poses,
        "stackPairs": pairs,
        "maxLinearSpeedMetersPerSecond": max_speed,
        "stable": max_speed <= 0.05,
    }


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _vector(value, length, field):
    if isinstance(value, (str, bytes)) or not hasattr(value, "__len__") or len(value) != length or not all(_finite(item) for item in value):
        raise ValueError("{} must contain {} finite numbers".format(field, length))
    return tuple(float(item) for item in value)


def _forbidden_identity(value):
    if isinstance(value, str):
        return "obj_" in value
    if isinstance(value, Mapping):
        return any(_forbidden_identity(key) or _forbidden_identity(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_forbidden_identity(item) for item in value)
    return False


def _quat_normalize(quaternion):
    values = _vector(quaternion, 4, "quaternion")
    norm = math.sqrt(sum(item * item for item in values))
    if norm <= _EPSILON:
        raise ValueError("quaternion must not be zero")
    return tuple(item / norm for item in values)


def _quat_multiply(left, right):
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return (
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
        lw * rw - lx * rx - ly * ry - lz * rz,
    )


def _quat_wxyz_multiply(left, right):
    """Multiply MuJoCo-style (w, x, y, z) quaternions."""
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def _quat_wxyz_conjugate(quaternion):
    w, x, y, z = _quat_normalize(quaternion)
    return w, -x, -y, -z


def _mujoco_wxyz_to_unity_xyzw(quaternion_wxyz):
    w, x, y, z = _quat_normalize(quaternion_wxyz)
    return x, y, z, w


@dataclass(frozen=True)
class MujocoToQuestTransform:
    """One uniform MuJoCo Z-up -> Unity Y-up mapping.

    The -90 degree X basis matches the existing M9 Franka visual root. The
    origin is the Quest workspace/table-center reference; table top is mapped
    to local Unity Y=0 before scale is applied.
    """

    scale: float = M13_6_DEFAULT_SCALE
    table_top_z_mujoco: float = M13_6_MUJOCO_TABLE_TOP_Z
    origin_quest: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def __post_init__(self):
        if not _finite(self.scale) or self.scale <= 0.0:
            raise ValueError("scale must be positive and finite")
        if not _finite(self.table_top_z_mujoco):
            raise ValueError("table_top_z_mujoco must be finite")
        _vector(self.origin_quest, 3, "origin_quest")

    def position(self, position_mujoco):
        x, y, z = _vector(position_mujoco, 3, "position_mujoco")
        ox, oy, oz = _vector(self.origin_quest, 3, "origin_quest")
        return (
            ox + self.scale * x,
            oy + self.scale * (z - self.table_top_z_mujoco),
            oz - self.scale * y,
        )

    def position_to_mujoco(self, position_quest):
        """Invert the same table-frame mapping for deterministic fixtures."""
        qx, qy, qz = _vector(position_quest, 3, "position_quest")
        ox, oy, oz = _vector(self.origin_quest, 3, "origin_quest")
        return (
            (qx - ox) / self.scale,
            -(qz - oz) / self.scale,
            (qy - oy) / self.scale + self.table_top_z_mujoco,
        )

    def orientation(self, quaternion_mujoco_wxyz):
        # Quaternion for R_x(-90 deg), represented in Unity x,y,z,w order.
        basis = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
        return _quat_normalize(_quat_multiply(basis, _mujoco_wxyz_to_unity_xyzw(quaternion_mujoco_wxyz)))

    def basis_vectors(self):
        """Return the named table-frame basis used by both robot and blocks."""
        origin = self.position((0.0, 0.0, self.table_top_z_mujoco))
        return {
            "mujoco_origin": origin,
            "mujoco_plus_x": tuple(a - b for a, b in zip(self.position((1.0, 0.0, self.table_top_z_mujoco)), origin)),
            "mujoco_plus_y": tuple(a - b for a, b in zip(self.position((0.0, 1.0, self.table_top_z_mujoco)), origin)),
            "mujoco_plus_z": tuple(a - b for a, b in zip(self.position((0.0, 0.0, self.table_top_z_mujoco + 1.0)), origin)),
        }


def m13_6_quest_catalog_anchor_positions_mujoco():
    """Return the Quest catalog anchors expressed in the MuJoCo table frame.

    M13.6 visual phases use these anchors only as a phase-start reference. It
    does not change the MuJoCo planner or the frozen logical block mapping.
    """
    transform = MujocoToQuestTransform()
    logical_ids = tuple(sorted(DEFAULT_M9_SCENE_BINDINGS))
    return {
        logical_id: transform.position_to_mujoco(M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST[index])
        for index, logical_id in enumerate(logical_ids)
    }


def m13_6_visual_runtime_anchor_positions_mujoco():
    """Return table-frame anchors for the grasp-safe M13.6 runtime cubes.

    The shared catalog remains the 85 mm startup presentation.  Once the
    M13.6 stream is active, the receiver applies the 56 mm runtime cube size;
    its table-supported center is therefore half that runtime size above the
    table.  Horizontal identity and spacing still come directly from the
    frozen catalog layout.
    """
    transform = MujocoToQuestTransform()
    logical_ids = tuple(sorted(DEFAULT_M9_SCENE_BINDINGS))
    runtime_y = M13_6_VISUAL_BLOCK_EDGE_QUEST / 2.0
    return {
        logical_id: transform.position_to_mujoco((
            M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST[index][0],
            runtime_y,
            M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST[index][2],
        ))
        for index, logical_id in enumerate(logical_ids)
    }


def m13_6_robot_base_orientation_fixture():
    """Describe the robot frame derived from the same table transform."""
    transform = MujocoToQuestTransform()
    mujoco_to_table_center = (0.0, -1.0, 0.0)
    table_center_direction_quest = tuple(
        a - b for a, b in zip(transform.position((0.0, -1.0, transform.table_top_z_mujoco)),
                               transform.position((0.0, 0.0, transform.table_top_z_mujoco)))
    )
    return {
        "mujocoBasePosition": (0.0, 0.4, 0.75),
        "mujocoTableCenterDirection": mujoco_to_table_center,
        "tableCenterDirectionQuest": table_center_direction_quest,
        "robotBaseQuaternionMujocoWxyz": (1.0, 0.0, 0.0, 0.0),
        "robotRootOrientationSource": "MujocoToQuestTransform.orientation(robot_base_quaternion)",
        "yawOffsetDegrees": M13_6_ROBOT_BASE_YAW_OFFSET_DEGREES,
        "passes": table_center_direction_quest == (0.0, 0.0, 0.7),
    }


@dataclass(frozen=True)
class BlockWorldState:
    logical_block_id: str
    position_mujoco: Tuple[float, float, float]
    quaternion_mujoco_wxyz: Tuple[float, float, float, float]

    def __post_init__(self):
        if not isinstance(self.logical_block_id, str) or not self.logical_block_id.startswith("block_sim_"):
            raise ValueError("block logical ID must use the frozen block_sim_* contract")
        if "obj_" in self.logical_block_id:
            raise ValueError("public block identity cannot expose obj_N")
        _vector(self.position_mujoco, 3, "position_mujoco")
        _quat_normalize(self.quaternion_mujoco_wxyz)

    def to_dict(self):
        return {
            "logicalBlockId": self.logical_block_id,
            "positionMujoco": list(self.position_mujoco),
            "quaternionMujocoWxyz": list(_quat_normalize(self.quaternion_mujoco_wxyz)),
        }


@dataclass(frozen=True)
class RobotWorldStateFrame:
    sequence: int
    simulation_time_seconds: float
    joint_positions_radians: Tuple[float, float, float, float, float, float, float]
    left_finger_position_meters: float
    right_finger_position_meters: float
    gripper_opening_meters: float
    robot_base_position_mujoco: Tuple[float, float, float]
    robot_base_quaternion_mujoco_wxyz: Tuple[float, float, float, float]
    blocks: Tuple[BlockWorldState, ...]
    simulation_state: str = "executing"
    sent_timestamp_unix_seconds: Optional[float] = None
    protocol_version: int = M13_6_PROTOCOL_VERSION
    mapping_version: str = M13_6_MAPPING_VERSION
    visual_block_size_quest_meters: Optional[Tuple[float, float, float]] = None

    def __post_init__(self):
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError("sequence must be a non-negative integer")
        if not _finite(self.simulation_time_seconds):
            raise ValueError("simulation time must be finite")
        _vector(self.joint_positions_radians, 7, "joint_positions_radians")
        for value, field in ((self.left_finger_position_meters, "left_finger_position_meters"), (self.right_finger_position_meters, "right_finger_position_meters"), (self.gripper_opening_meters, "gripper_opening_meters")):
            if not _finite(value):
                raise ValueError("{} must be finite".format(field))
        _vector(self.robot_base_position_mujoco, 3, "robot_base_position_mujoco")
        _quat_normalize(self.robot_base_quaternion_mujoco_wxyz)
        if self.visual_block_size_quest_meters is not None:
            size = _vector(self.visual_block_size_quest_meters, 3, "visual_block_size_quest_meters")
            if any(value <= 0.0 for value in size):
                raise ValueError("visual block size must be positive")
        if not self.blocks or len(self.blocks) != 4:
            raise ValueError("a full four-block state is required for startup recovery")
        if len({block.logical_block_id for block in self.blocks}) != 4:
            raise ValueError("block logical IDs must be unique")
        if _forbidden_identity(self.to_dict()):
            raise ValueError("visual state contains forbidden obj_N identity")

    def to_dict(self):
        payload = {
            "protocolVersion": self.protocol_version,
            "messageType": M13_6_MESSAGE_TYPE,
            "streamSource": "mujoco",
            "mappingVersion": self.mapping_version,
            "sequence": self.sequence,
            "simulationTimestampSeconds": self.simulation_time_seconds,
            "sentTimestampUnixSeconds": self.sent_timestamp_unix_seconds,
            "simulationState": self.simulation_state,
            "jointPositionsRadians": list(self.joint_positions_radians),
            "leftFingerPositionMeters": self.left_finger_position_meters,
            "rightFingerPositionMeters": self.right_finger_position_meters,
            "gripperOpeningMeters": self.gripper_opening_meters,
            "robotBasePositionMujoco": list(self.robot_base_position_mujoco),
            "robotBaseQuaternionMujocoWxyz": list(_quat_normalize(self.robot_base_quaternion_mujoco_wxyz)),
            "blocks": [block.to_dict() for block in self.blocks],
        }
        if _forbidden_identity(payload):
            raise ValueError("visual state contains forbidden obj_N identity")
        if self.visual_block_size_quest_meters is not None:
            payload["visualBlockSizeQuestMeters"] = list(self.visual_block_size_quest_meters)
        return payload

    def to_json_bytes(self):
        return (json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, Mapping) or payload.get("protocolVersion") != M13_6_PROTOCOL_VERSION or payload.get("messageType") != M13_6_MESSAGE_TYPE:
            raise ValueError("unsupported M13.6 visual-state protocol")
        blocks = tuple(BlockWorldState(item["logicalBlockId"], tuple(item["positionMujoco"]), tuple(item["quaternionMujocoWxyz"])) for item in payload.get("blocks", ()))
        return cls(
            sequence=int(payload["sequence"]),
            simulation_time_seconds=float(payload["simulationTimestampSeconds"]),
            joint_positions_radians=tuple(payload["jointPositionsRadians"]),
            left_finger_position_meters=float(payload["leftFingerPositionMeters"]),
            right_finger_position_meters=float(payload["rightFingerPositionMeters"]),
            gripper_opening_meters=float(payload["gripperOpeningMeters"]),
            robot_base_position_mujoco=tuple(payload["robotBasePositionMujoco"]),
            robot_base_quaternion_mujoco_wxyz=tuple(payload["robotBaseQuaternionMujocoWxyz"]),
            blocks=blocks,
            simulation_state=str(payload.get("simulationState", "executing")),
            sent_timestamp_unix_seconds=payload.get("sentTimestampUnixSeconds"),
            protocol_version=int(payload["protocolVersion"]),
            mapping_version=str(payload.get("mappingVersion", M13_6_MAPPING_VERSION)),
            visual_block_size_quest_meters=(
                tuple(payload["visualBlockSizeQuestMeters"])
                if payload.get("visualBlockSizeQuestMeters") is not None else None
            ),
        )

    @classmethod
    def from_json_bytes(cls, payload):
        return cls.from_dict(json.loads(payload.decode("utf-8")))


class LatestStateBuffer:
    """Sequence gate for the Quest receiver semantics, useful in PC tests."""

    def __init__(self):
        self.latest = None
        self.accepted_count = 0
        self.stale_count = 0
        self.duplicate_count = 0

    def accept(self, frame):
        if not isinstance(frame, RobotWorldStateFrame):
            raise TypeError("frame must be RobotWorldStateFrame")
        if self.latest is not None and frame.sequence <= self.latest.sequence:
            if frame.sequence == self.latest.sequence:
                self.duplicate_count += 1
            else:
                self.stale_count += 1
            return False
        self.latest = frame
        self.accepted_count += 1
        return True


class UdpLatestStateSender:
    """Small PC sender. UDP is separate from reliable selection TCP 11001."""

    def __init__(self, host="127.0.0.1", port=M13_6_UDP_PORT, sock=None):
        self.destination = (str(host), int(port))
        self._socket = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sent_count = 0
        self.last_send_timestamp = None
        self.intervals = []

    def send(self, frame):
        payload = frame.to_json_bytes()
        self._socket.sendto(payload, self.destination)
        now = time.monotonic()
        if self.last_send_timestamp is not None:
            self.intervals.append(now - self.last_send_timestamp)
        self.last_send_timestamp = now
        self.sent_count += 1
        return len(payload)

    def close(self):
        self._socket.close()

    @property
    def effective_rate_hz(self):
        if not self.intervals:
            return None
        average = sum(self.intervals) / len(self.intervals)
        return 1.0 / average if average > 0.0 else None


class TcpLatestStateSender:
    """Persistent TCP visual sender with replacement semantics.

    TCP is a compatibility transport only; M8 selection/control remains on
    TCP 11001.  Calls to ``send`` replace any not-yet-written payload so a
    slow link cannot accumulate a historical visual animation backlog.
    The receiver uses newline-delimited JSON on the same visual port (11002)
    in the TCP namespace, which is independent from the UDP socket.
    """

    def __init__(self, host="127.0.0.1", port=M13_6_UDP_PORT, sock=None):
        self.destination = (str(host), int(port))
        self._socket = sock or socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._socket.settimeout(2.0)
        if sock is None:
            self._socket.connect(self.destination)
        self.sent_count = 0
        self.last_send_timestamp = None
        self.intervals = []
        self._pending_payload = None
        self._condition = threading.Condition()
        self._closed = False
        self._error = None
        self._worker = threading.Thread(target=self._send_loop, name="M13.6TcpLatestStateSender", daemon=True)
        self._worker.start()

    def send(self, frame):
        payload = frame.to_json_bytes()
        with self._condition:
            if self._closed:
                raise RuntimeError("TCP visual sender is closed")
            if self._error is not None:
                raise ConnectionError("TCP visual sender failed: {}".format(self._error))
            self._pending_payload = payload
            self._condition.notify()
        return len(payload)

    def _send_loop(self):
        while True:
            with self._condition:
                while self._pending_payload is None and not self._closed:
                    self._condition.wait()
                if self._pending_payload is None and self._closed:
                    return
                payload = self._pending_payload
                self._pending_payload = None
            try:
                self._socket.sendall(payload)
            except OSError as error:
                with self._condition:
                    self._error = error
                    self._closed = True
                    self._pending_payload = None
                    self._condition.notify_all()
                return
            now = time.monotonic()
            if self.last_send_timestamp is not None:
                self.intervals.append(now - self.last_send_timestamp)
            self.last_send_timestamp = now
            self.sent_count += 1

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify()
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()

    @property
    def last_error(self):
        return self._error

    @property
    def effective_rate_hz(self):
        if not self.intervals:
            return None
        average = sum(self.intervals) / len(self.intervals)
        return 1.0 / average if average > 0.0 else None


class RecordingStateSink:
    """Deterministic sink for PC-only and MuJoCo software acceptance."""

    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(frame)
        return len(frame.to_json_bytes())

    def close(self):
        return None


class MujocoStateSampler:
    """Read a MuJoCo model/data pair without exposing simulator names."""

    def __init__(
        self,
        mujoco_module,
        model,
        data,
        scene_bindings=None,
        phase_anchor_positions_mujoco=None,
        visual_block_size_quest_meters=None,
    ):
        self.mujoco = mujoco_module
        self.model = model
        self.data = data
        self.scene_bindings = dict(scene_bindings or DEFAULT_M9_SCENE_BINDINGS)
        self.joint_addresses = tuple(int(model.jnt_qposadr[mujoco_module.mj_name2id(model, mujoco_module.mjtObj.mjOBJ_JOINT, "fr3_joint{}".format(index))]) for index in range(1, 8))
        self.finger_addresses = tuple(int(model.jnt_qposadr[mujoco_module.mj_name2id(model, mujoco_module.mjtObj.mjOBJ_JOINT, name)]) for name in ("umi_left_finger_joint", "umi_right_finger_joint"))
        self.body_ids = {logical_id: int(mujoco_module.mj_name2id(model, mujoco_module.mjtObj.mjOBJ_BODY, simulator_name)) for logical_id, simulator_name in self.scene_bindings.items()}
        self.robot_base_id = int(mujoco_module.mj_name2id(model, mujoco_module.mjtObj.mjOBJ_BODY, "base"))
        if any(value < 0 for value in self.body_ids.values()) or self.robot_base_id < 0:
            raise ValueError("M13.6 MuJoCo scene binding is incomplete")
        self.phase_anchor_positions_mujoco = dict(phase_anchor_positions_mujoco or {})
        self.visual_block_size_quest_meters = (
            tuple(float(value) for value in _vector(
                visual_block_size_quest_meters, 3, "visual_block_size_quest_meters"
            ))
            if visual_block_size_quest_meters is not None else None
        )
        if self.visual_block_size_quest_meters is not None and any(
            value <= 0.0 for value in self.visual_block_size_quest_meters
        ):
            raise ValueError("visual block size must be positive")
        if self.phase_anchor_positions_mujoco and set(self.phase_anchor_positions_mujoco) != set(self.body_ids):
            raise ValueError("phase block anchors must cover the frozen four logical block IDs")
        self.phase_initial_positions_mujoco = {
            logical_id: tuple(float(value) for value in self.data.xpos[body_id])
            for logical_id, body_id in self.body_ids.items()
        }
        self.phase_initial_rotations_mujoco = {
            logical_id: tuple(float(value) for value in self.data.xquat[body_id])
            for logical_id, body_id in self.body_ids.items()
        }

    def _block_pose(self, logical_id, body_id):
        position = tuple(float(value) for value in self.data.xpos[body_id])
        rotation = tuple(float(value) for value in self.data.xquat[body_id])
        if not self.phase_anchor_positions_mujoco:
            return position, rotation

        initial_position = self.phase_initial_positions_mujoco[logical_id]
        anchor_position = self.phase_anchor_positions_mujoco[logical_id]
        position = tuple(
            float(anchor_position[index] + position[index] - initial_position[index])
            for index in range(3)
        )
        initial_rotation = self.phase_initial_rotations_mujoco[logical_id]
        relative_rotation = _quat_wxyz_multiply(
            _quat_wxyz_conjugate(initial_rotation), rotation)
        rotation = _quat_normalize(relative_rotation)
        return position, rotation

    def frame(self, sequence, simulation_state="executing"):
        blocks = tuple(
            BlockWorldState(logical_id, *self._block_pose(logical_id, body_id))
            for logical_id, body_id in sorted(self.body_ids.items())
        )
        left_address, right_address = self.finger_addresses
        left = float(self.data.qpos[left_address])
        right = float(self.data.qpos[right_address])
        return RobotWorldStateFrame(
            sequence=int(sequence),
            simulation_time_seconds=float(self.data.time),
            joint_positions_radians=tuple(float(self.data.qpos[address]) for address in self.joint_addresses),
            left_finger_position_meters=left,
            right_finger_position_meters=right,
            gripper_opening_meters=abs(left) + abs(right),
            robot_base_position_mujoco=tuple(float(value) for value in self.data.xpos[self.robot_base_id]),
            robot_base_quaternion_mujoco_wxyz=tuple(float(value) for value in self.data.xquat[self.robot_base_id]),
            blocks=blocks,
            simulation_state=simulation_state,
            sent_timestamp_unix_seconds=time.time(),
            visual_block_size_quest_meters=self.visual_block_size_quest_meters,
        )


def _run_planner_stream(
    mujoco, model, data, planner, sink, sampler, start_sequence, rate_hz,
    max_steps, realtime=False, post_release_settle_steps=0,
    post_release_callback=None, viewer=None,
):
    steps_per_frame = max(1, int(round(1.0 / (float(rate_hz) * float(model.opt.timestep)))))
    sequence = int(start_sequence)
    emitted = 0
    release_callback_called = False
    interval_seconds = 1.0 / float(rate_hz) if realtime else None
    next_emit_time = time.monotonic() if realtime else None

    def sync_viewer():
        if viewer is None:
            return
        if not viewer.is_running():
            raise RuntimeError("M13.6 MuJoCo viewer was closed before the run completed")
        viewer.sync()

    def emit(frame):
        nonlocal emitted, next_emit_time
        if realtime:
            remaining = next_emit_time - time.monotonic()
            if remaining > 0.0:
                time.sleep(remaining)
        sink.send(frame)
        emitted += 1
        if realtime:
            next_emit_time += interval_seconds

    sync_viewer()
    emit(sampler.frame(sequence, "startup"))
    while True:
        active = planner.step()
        if planner.phase == "RELEASE" and not release_callback_called:
            release_callback_called = True
            if post_release_callback is not None:
                post_release_callback()
                mujoco.mj_forward(model, data)
        if not active or sequence - int(start_sequence) >= int(max_steps):
            break
        mujoco.mj_step(model, data)
        sync_viewer()
        sequence += 1
        if (sequence - int(start_sequence)) % steps_per_frame == 0:
            emit(sampler.frame(sequence, "executing"))
    # A stack must be judged after the released body has had time to settle
    # under MuJoCo gravity/collision, not at the instant the gripper opens.
    for _ in range(max(0, int(post_release_settle_steps))):
        mujoco.mj_step(model, data)
        sync_viewer()
        sequence += 1
        if (sequence - int(start_sequence)) % steps_per_frame == 0:
            emit(sampler.frame(sequence, "settling"))
    mujoco.mj_forward(model, data)
    sync_viewer()
    summary = planner.summary()
    final_sequence = sequence + 1
    emit(sampler.frame(final_sequence, "completed" if summary.get("result") in ("place_ok", "lift_ok") else "completed_with_result"))
    # Reserve one sequence after the final marker so a following planner's
    # startup frame cannot duplicate the previous step's terminal frame.
    return final_sequence + 1, emitted, steps_per_frame, summary


def _m13_6_launch_viewer(mujoco, model, data):
    """Open the debug viewer on the exact model/data used by the runner."""
    import mujoco.viewer

    return mujoco.viewer.launch_passive(model, data)


def _m13_6_wait_for_viewer(viewer):
    """Keep the final MuJoCo state visible until the user closes the viewer."""
    while viewer.is_running():
        viewer.sync()
        time.sleep(0.02)


def _load_mujoco_planner(logical_block_id, place, place_center=None,
                         place_region_center=None, place_region_half_size=None,
                         place_target_position=None, place_region_z_tolerance=None,
                         drop_clear=None, m13_6_visual_mode=False):
    if logical_block_id not in DEFAULT_M9_SCENE_BINDINGS:
        raise ValueError("unknown frozen logical block ID")
    source_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "robot_arm"
    source_root_string = str(source_root)
    if source_root_string not in sys.path:
        sys.path.insert(0, source_root_string)
    import mujoco
    from control.gripper_planner import GripperGraspPlanner
    from utils.gripper_scene import build_gripper_scene, build_m13_6_visual_scene

    model, data = (build_m13_6_visual_scene() if m13_6_visual_mode else build_gripper_scene())
    mujoco.mj_forward(model, data)
    simulator_object_name = DEFAULT_M9_SCENE_BINDINGS[logical_block_id]
    planner = GripperGraspPlanner(
        model,
        data,
        simulator_object_name,
        place=bool(place),
        place_center=place_center,
        place_region_center=place_region_center,
        place_region_half_size=place_region_half_size,
        place_target_position=place_target_position,
        place_region_z_tolerance=place_region_z_tolerance,
        **({} if drop_clear is None else {"drop_clear": drop_clear}),
    )
    return mujoco, model, data, planner


def run_mujoco_pick_place_stream(
    logical_block_id="block_sim_01",
    sink=None,
    rate_hz=M13_6_DEFAULT_RATE_HZ,
    max_steps=9000,
    place=True,
    start_sequence=0,
    realtime=False,
    rebase_blocks_to_quest_catalog=False,
    phase_anchor_positions_mujoco=None,
    m13_6_visual_mode=False,
    viewer=False,
):
    """Run the existing FR3/UMI planner while sampling its real MuJoCo state."""
    if not _finite(rate_hz) or rate_hz <= 0.0:
        raise ValueError("rate_hz must be positive")
    if isinstance(start_sequence, bool) or not isinstance(start_sequence, int) or start_sequence < 0:
        raise ValueError("start_sequence must be a non-negative integer")
    if sink is None:
        sink = UdpLatestStateSender()
    if phase_anchor_positions_mujoco is None and (rebase_blocks_to_quest_catalog or m13_6_visual_mode):
        phase_anchor_positions_mujoco = (
            m13_6_visual_runtime_anchor_positions_mujoco()
            if m13_6_visual_mode else m13_6_quest_catalog_anchor_positions_mujoco()
        )
    phase_rebased = phase_anchor_positions_mujoco is not None
    visual_mode = bool(m13_6_visual_mode) or phase_rebased or bool(rebase_blocks_to_quest_catalog)
    visual_target = M13_6_VISUAL_STACK_TARGETS[logical_block_id] if visual_mode else None
    mujoco, model, data, planner = _load_mujoco_planner(
        logical_block_id,
        place,
        place_center=visual_target["position"][:2] if visual_target is not None else None,
        place_region_center=visual_target["position"][:2] if visual_target is not None else None,
        place_region_half_size=(
            M13_6_VISUAL_PLACE_REGION_HALF_SIZE if visual_target is not None else None
        ),
        place_target_position=visual_target["position"] if visual_target is not None else None,
        place_region_z_tolerance=0.035 if visual_target is not None else None,
        drop_clear=0.0 if visual_target is not None else None,
        m13_6_visual_mode=visual_mode,
    )
    if visual_mode:
        _m13_6_prepare_stack_collision_categories(mujoco, model, DEFAULT_M9_SCENE_BINDINGS)
        _m13_6_set_object_collision_category(mujoco, model, logical_block_id, 1)
    sampler = MujocoStateSampler(
        mujoco,
        model,
        data,
        phase_anchor_positions_mujoco=phase_anchor_positions_mujoco,
        visual_block_size_quest_meters=(
            (M13_6_VISUAL_BLOCK_EDGE_QUEST,) * 3 if visual_mode else None
        ),
    )
    viewer_handle = _m13_6_launch_viewer(mujoco, model, data) if viewer else None
    try:
        final_sequence, emitted, steps_per_frame, summary = _run_planner_stream(
            mujoco, model, data, planner, sink, sampler, start_sequence, rate_hz, max_steps,
                realtime=realtime,
                post_release_settle_steps=(240 if visual_target is not None else 0),
                post_release_callback=(
                    lambda logical_id=logical_block_id: _m13_6_set_object_collision_category(
                        mujoco, model, logical_id, 2
                    )
                    if visual_target is not None else None
                ),
                viewer=viewer_handle,
        )
        final_frame = sampler.frame(final_sequence, "completed")
        if viewer_handle is not None:
            _m13_6_wait_for_viewer(viewer_handle)
    finally:
        if viewer_handle is not None:
            viewer_handle.close()
        sink.close()
    status = "completed" if summary.get("result") in ("place_ok", "lift_ok") else "completed_with_result"
    return {
        "status": status,
        "planner": summary,
        "logicalBlockId": logical_block_id,
        "framesEmitted": emitted,
        "simulationSteps": max(0, final_sequence - start_sequence - 1),
        "sequenceStart": start_sequence,
        "nextSequence": final_sequence + 1,
        "configuredRateHz": float(rate_hz),
        "realtimePlayback": bool(realtime),
        "phaseBlockRebased": phase_rebased,
        "finalBlockPositionsMujoco": {
            block.logical_block_id: list(block.position_mujoco)
            for block in final_frame.blocks
        },
        "finalSourceBlockPositionsMujoco": {
            logical_id: [float(value) for value in sampler.data.xpos[body_id]]
            for logical_id, body_id in sampler.body_ids.items()
        },
        "phaseAnchorPositionsMujoco": {
            logical_id: list(position)
            for logical_id, position in sampler.phase_anchor_positions_mujoco.items()
        },
        "effectiveRateHz": getattr(sink, "effective_rate_hz", None),
        "stepsPerFrame": steps_per_frame,
        "protocolVersion": M13_6_PROTOCOL_VERSION,
        "mappingVersion": M13_6_MAPPING_VERSION,
        "provenance": "MujocoStateSampler + existing GripperGraspPlanner",
        "publicIdentityLeak": False,
    }


def run_mujoco_sequential_stream(
    logical_block_ids=None,
    sink=None,
    rate_hz=M13_6_DEFAULT_RATE_HZ,
    max_steps_per_block=9000,
    start_sequence=0,
    realtime=False,
    rebase_blocks_to_quest_catalog=False,
    phase_anchor_positions_mujoco=None,
    initial_block_positions_mujoco=None,
    m13_6_visual_mode=False,
    viewer=False,
):
    """Run a continuous four-block software episode on one MuJoCo world."""
    logical_block_ids = tuple(logical_block_ids or ("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"))
    if set(logical_block_ids) != set(DEFAULT_M9_SCENE_BINDINGS) or len(logical_block_ids) != 4:
        raise ValueError("sequential stream must use the four frozen logical block IDs exactly once")
    if isinstance(start_sequence, bool) or not isinstance(start_sequence, int) or start_sequence < 0:
        raise ValueError("start_sequence must be a non-negative integer")
    if sink is None:
        sink = UdpLatestStateSender()
    source_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "robot_arm"
    source_root_string = str(source_root)
    if source_root_string not in sys.path:
        sys.path.insert(0, source_root_string)
    import mujoco
    from control.gripper_planner import GripperGraspPlanner
    from utils.gripper_scene import build_gripper_scene, build_m13_6_visual_scene
    visual_mode = bool(m13_6_visual_mode) or phase_anchor_positions_mujoco is not None or bool(rebase_blocks_to_quest_catalog)
    model, data = (build_m13_6_visual_scene() if visual_mode else build_gripper_scene())
    mujoco.mj_forward(model, data)
    if phase_anchor_positions_mujoco is None and (rebase_blocks_to_quest_catalog or m13_6_visual_mode):
        phase_anchor_positions_mujoco = (
            m13_6_visual_runtime_anchor_positions_mujoco()
            if m13_6_visual_mode else m13_6_quest_catalog_anchor_positions_mujoco()
        )
    phase_rebased = phase_anchor_positions_mujoco is not None
    if initial_block_positions_mujoco is not None:
        if not isinstance(initial_block_positions_mujoco, Mapping):
            raise ValueError("initial_block_positions_mujoco must be a mapping")
        unknown_ids = set(initial_block_positions_mujoco) - set(DEFAULT_M9_SCENE_BINDINGS)
        if unknown_ids:
            raise ValueError("initial block positions contain unknown logical block IDs")
        for logical_id, position in initial_block_positions_mujoco.items():
            position = _vector(position, 3, "initial block position")
            simulator_name = DEFAULT_M9_SCENE_BINDINGS[logical_id]
            body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, simulator_name))
            if body_id < 0:
                raise ValueError("initial block position binding is incomplete")
            joint_id = int(model.body_jntadr[body_id])
            if joint_id < 0:
                raise ValueError("initial block position binding is incomplete")
            qpos_address = int(model.jnt_qposadr[joint_id])
            data.qpos[qpos_address:qpos_address + 3] = position
            data.qpos[qpos_address + 3:qpos_address + 7] = (1.0, 0.0, 0.0, 0.0)
        mujoco.mj_forward(model, data)
    visual_mode = bool(m13_6_visual_mode) or phase_rebased or bool(rebase_blocks_to_quest_catalog)
    if visual_mode:
        _m13_6_prepare_stack_collision_categories(mujoco, model, logical_block_ids)
    sampler = MujocoStateSampler(
        mujoco,
        model,
        data,
        phase_anchor_positions_mujoco=phase_anchor_positions_mujoco,
        visual_block_size_quest_meters=(
            (M13_6_VISUAL_BLOCK_EDGE_QUEST,) * 3 if visual_mode else None
        ),
    )
    viewer_handle = _m13_6_launch_viewer(mujoco, model, data) if viewer else None
    sequence = start_sequence
    execution_logical_block_ids = logical_block_ids
    if m13_6_visual_mode and logical_block_ids == (
        "block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"
    ):
        # Place both bottom blocks before transporting either top block.  This
        # keeps a tall completed stack out of the next approach/lift corridor.
        execution_logical_block_ids = M13_6_VISUAL_EXECUTION_ORDER
    total_emitted = 0
    step_counts = []
    planner_summaries = []
    try:
        for logical_block_id in execution_logical_block_ids:
            visual_mode = bool(m13_6_visual_mode) or phase_rebased or bool(rebase_blocks_to_quest_catalog)
            if visual_mode:
                _m13_6_set_object_collision_category(
                    mujoco, model, logical_block_id, 1
                )
            stack_target = M13_6_VISUAL_STACK_TARGETS[logical_block_id] if visual_mode else None
            resolved_target_position = (
                _m13_6_resolve_stack_target(mujoco, model, data, logical_block_id, sampler.body_ids)
                if stack_target is not None else None
            )
            place_target = (
                resolved_target_position[:2]
                if stack_target is not None else M13_6_SEQUENTIAL_PLACE_TARGETS[logical_block_id]
            )
            planner = GripperGraspPlanner(
                model,
                data,
                DEFAULT_M9_SCENE_BINDINGS[logical_block_id],
                place=True,
                place_center=place_target,
                place_region_center=place_target if visual_mode else None,
                place_region_half_size=(
                    M13_6_VISUAL_PLACE_REGION_HALF_SIZE if visual_mode else None
                ),
                place_target_position=resolved_target_position,
                place_region_z_tolerance=0.035 if stack_target is not None else None,
                drop_clear=0.0 if stack_target is not None else None,
            )
            sequence, emitted, steps_per_frame, summary = _run_planner_stream(
                mujoco, model, data, planner, sink, sampler, sequence, rate_hz, max_steps_per_block,
                realtime=realtime,
                post_release_settle_steps=(240 if stack_target is not None else 0),
                post_release_callback=(
                    lambda logical_id=logical_block_id: _m13_6_set_object_collision_category(
                        mujoco, model, logical_id, 2
                    )
                    if stack_target is not None else None
                ),
                viewer=viewer_handle,
            )
            total_emitted += emitted
            step_counts.append(sequence)
            planner_summaries.append({"logicalBlockId": logical_block_id, **summary})
        final_frame = sampler.frame(sequence, "completed")
        if viewer_handle is not None:
            _m13_6_wait_for_viewer(viewer_handle)
    finally:
        if viewer_handle is not None:
            viewer_handle.close()
        sink.close()
    stack_metrics = (
        m13_6_collect_stack_metrics(mujoco, model, data, sampler.body_ids)
        if visual_mode else None
    )
    return {
        "status": "completed" if all(item.get("result") == "place_ok" for item in planner_summaries) else "completed_with_result",
        "steps": sequence - start_sequence,
        "sequenceStart": start_sequence,
        "nextSequence": sequence,
        "framesEmitted": total_emitted,
        "stepsPerFrame": steps_per_frame,
        "configuredRateHz": float(rate_hz),
        "realtimePlayback": bool(realtime),
        "phaseBlockRebased": phase_rebased,
        "finalBlockPositionsMujoco": {
            block.logical_block_id: list(block.position_mujoco)
            for block in final_frame.blocks
        },
        "finalSourceBlockPositionsMujoco": {
            logical_id: [float(value) for value in sampler.data.xpos[body_id]]
            for logical_id, body_id in sampler.body_ids.items()
        },
        "phaseAnchorPositionsMujoco": {
            logical_id: list(position)
            for logical_id, position in sampler.phase_anchor_positions_mujoco.items()
        },
        "logicalBlockIds": list(execution_logical_block_ids),
        "planners": planner_summaries,
        "stackMetrics": stack_metrics,
        "provenance": "one MuJoCo world + existing M14 logical sequence + GripperGraspPlanner",
        "publicIdentityLeak": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logical-block-id", default="block_sim_01")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=M13_6_UDP_PORT)
    parser.add_argument("--transport", choices=("udp", "tcp"), default="udp")
    parser.add_argument("--rate-hz", type=float, default=M13_6_DEFAULT_RATE_HZ)
    parser.add_argument("--max-steps", type=int, default=9000)
    parser.add_argument("--start-sequence", type=int, default=0)
    parser.add_argument("--realtime", action="store_true", help="pace telemetry on wall-clock at --rate-hz")
    parser.add_argument("--dry-run", action="store_true", help="run MuJoCo and retain frames in memory without UDP")
    parser.add_argument("--viewer", action="store_true", help="debug-only viewer on this runner's live MuJoCo model/data")
    parser.add_argument("--sequential", action="store_true", help="run the four-block M13.6 visual sequence")
    parser.add_argument("--max-steps-per-block", type=int, default=None)
    args = parser.parse_args(argv)
    if args.dry_run:
        sink = RecordingStateSink()
    elif args.transport == "tcp":
        sink = TcpLatestStateSender(args.host, args.port)
    else:
        sink = UdpLatestStateSender(args.host, args.port)
    visual_mode = bool(args.viewer)
    if args.sequential:
        result = run_mujoco_sequential_stream(
            sink=sink,
            rate_hz=args.rate_hz,
            max_steps_per_block=(args.max_steps_per_block or args.max_steps),
            start_sequence=args.start_sequence,
            realtime=args.realtime,
            m13_6_visual_mode=visual_mode,
            viewer=args.viewer,
        )
    else:
        result = run_mujoco_pick_place_stream(
            args.logical_block_id,
            sink=sink,
            rate_hz=args.rate_hz,
            max_steps=args.max_steps,
            start_sequence=args.start_sequence,
            realtime=args.realtime,
            m13_6_visual_mode=visual_mode,
            viewer=args.viewer,
        )
    result["dryRun"] = bool(args.dry_run)
    result["transport"] = args.transport
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in ("completed", "completed_with_result") else 1


__all__ = ["M13_6_PROTOCOL_VERSION", "M13_6_MESSAGE_TYPE", "M13_6_UDP_PORT", "M13_6_ROBOT_BASE_YAW_OFFSET_DEGREES", "M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST", "M13_6_STACK_CLEARANCE_METERS", "M13_6_VISUAL_STACK_TARGETS", "M13_6_VISUAL_EXECUTION_ORDER", "M13_6_VISUAL_SEQUENTIAL_PLACE_TARGETS", "MujocoToQuestTransform", "m13_6_quest_catalog_anchor_positions_mujoco", "m13_6_visual_runtime_anchor_positions_mujoco", "m13_6_robot_base_orientation_fixture", "m13_6_gripper_block_relative_pose", "m13_6_collect_stack_metrics", "BlockWorldState", "RobotWorldStateFrame", "LatestStateBuffer", "UdpLatestStateSender", "TcpLatestStateSender", "RecordingStateSink", "MujocoStateSampler", "run_mujoco_pick_place_stream", "run_mujoco_sequential_stream"]


if __name__ == "__main__":
    raise SystemExit(main())
