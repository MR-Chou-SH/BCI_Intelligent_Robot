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
_EPSILON = 1e-9


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _vector(value, length, field):
    if not isinstance(value, (list, tuple)) or len(value) != length or not all(_finite(item) for item in value):
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

    def orientation(self, quaternion_mujoco_wxyz):
        # Quaternion for R_x(-90 deg), represented in Unity x,y,z,w order.
        basis = (-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5))
        return _quat_normalize(_quat_multiply(basis, _mujoco_wxyz_to_unity_xyzw(quaternion_mujoco_wxyz)))


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

    def __init__(self, mujoco_module, model, data, scene_bindings=None):
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

    def frame(self, sequence, simulation_state="executing"):
        blocks = tuple(BlockWorldState(logical_id, tuple(float(value) for value in self.data.xpos[body_id]), tuple(float(value) for value in self.data.xquat[body_id])) for logical_id, body_id in sorted(self.body_ids.items()))
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
        )


def _run_planner_stream(mujoco, model, data, planner, sink, sampler, start_sequence, rate_hz, max_steps):
    steps_per_frame = max(1, int(round(1.0 / (float(rate_hz) * float(model.opt.timestep)))))
    sequence = int(start_sequence)
    emitted = 0
    sink.send(sampler.frame(sequence, "startup"))
    emitted += 1
    while planner.step() and sequence - int(start_sequence) < int(max_steps):
        mujoco.mj_step(model, data)
        sequence += 1
        if (sequence - int(start_sequence)) % steps_per_frame == 0:
            sink.send(sampler.frame(sequence, "executing"))
            emitted += 1
    mujoco.mj_forward(model, data)
    summary = planner.summary()
    final_sequence = sequence + 1
    sink.send(sampler.frame(final_sequence, "completed" if summary.get("result") in ("place_ok", "lift_ok") else "completed_with_result"))
    emitted += 1
    # Reserve one sequence after the final marker so a following planner's
    # startup frame cannot duplicate the previous step's terminal frame.
    return final_sequence + 1, emitted, steps_per_frame, summary


def _load_mujoco_planner(logical_block_id, place):
    if logical_block_id not in DEFAULT_M9_SCENE_BINDINGS:
        raise ValueError("unknown frozen logical block ID")
    source_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "robot_arm"
    source_root_string = str(source_root)
    if source_root_string not in sys.path:
        sys.path.insert(0, source_root_string)
    import mujoco
    from control.gripper_planner import GripperGraspPlanner
    from utils.gripper_scene import build_gripper_scene

    model, data = build_gripper_scene()
    mujoco.mj_forward(model, data)
    simulator_object_name = DEFAULT_M9_SCENE_BINDINGS[logical_block_id]
    planner = GripperGraspPlanner(model, data, simulator_object_name, place=bool(place))
    return mujoco, model, data, planner


def run_mujoco_pick_place_stream(logical_block_id="block_sim_01", sink=None, rate_hz=M13_6_DEFAULT_RATE_HZ, max_steps=9000, place=True):
    """Run the existing FR3/UMI planner while sampling its real MuJoCo state."""
    if not _finite(rate_hz) or rate_hz <= 0.0:
        raise ValueError("rate_hz must be positive")
    if sink is None:
        sink = UdpLatestStateSender()
    mujoco, model, data, planner = _load_mujoco_planner(logical_block_id, place)
    sampler = MujocoStateSampler(mujoco, model, data)
    try:
        final_sequence, emitted, steps_per_frame, summary = _run_planner_stream(mujoco, model, data, planner, sink, sampler, 0, rate_hz, max_steps)
    finally:
        sink.close()
    status = "completed" if summary.get("result") in ("place_ok", "lift_ok") else "completed_with_result"
    return {"status": status, "planner": summary, "logicalBlockId": logical_block_id, "framesEmitted": emitted, "simulationSteps": max(0, final_sequence - 1), "configuredRateHz": float(rate_hz), "effectiveRateHz": getattr(sink, "effective_rate_hz", None), "stepsPerFrame": steps_per_frame, "protocolVersion": M13_6_PROTOCOL_VERSION, "mappingVersion": M13_6_MAPPING_VERSION, "provenance": "MujocoStateSampler + existing GripperGraspPlanner", "publicIdentityLeak": False}


def run_mujoco_sequential_stream(logical_block_ids=None, sink=None, rate_hz=M13_6_DEFAULT_RATE_HZ, max_steps_per_block=9000):
    """Run a continuous four-block software episode on one MuJoCo world."""
    logical_block_ids = tuple(logical_block_ids or ("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"))
    if set(logical_block_ids) != set(DEFAULT_M9_SCENE_BINDINGS) or len(logical_block_ids) != 4:
        raise ValueError("sequential stream must use the four frozen logical block IDs exactly once")
    if sink is None:
        sink = UdpLatestStateSender()
    source_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "robot_arm"
    source_root_string = str(source_root)
    if source_root_string not in sys.path:
        sys.path.insert(0, source_root_string)
    import mujoco
    from control.gripper_planner import GripperGraspPlanner
    from utils.gripper_scene import build_gripper_scene
    model, data = build_gripper_scene()
    mujoco.mj_forward(model, data)
    sampler = MujocoStateSampler(mujoco, model, data)
    sequence = 0
    total_emitted = 0
    step_counts = []
    planner_summaries = []
    try:
        for logical_block_id in logical_block_ids:
            planner = GripperGraspPlanner(model, data, DEFAULT_M9_SCENE_BINDINGS[logical_block_id], place=True)
            sequence, emitted, steps_per_frame, summary = _run_planner_stream(mujoco, model, data, planner, sink, sampler, sequence, rate_hz, max_steps_per_block)
            total_emitted += emitted
            step_counts.append(sequence)
            planner_summaries.append({"logicalBlockId": logical_block_id, **summary})
    finally:
        sink.close()
    return {"status": "completed" if all(item.get("result") == "place_ok" for item in planner_summaries) else "completed_with_result", "steps": sequence, "framesEmitted": total_emitted, "stepsPerFrame": steps_per_frame, "configuredRateHz": float(rate_hz), "logicalBlockIds": list(logical_block_ids), "planners": planner_summaries, "provenance": "one MuJoCo world + existing M14 logical sequence + GripperGraspPlanner", "publicIdentityLeak": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logical-block-id", default="block_sim_01")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=M13_6_UDP_PORT)
    parser.add_argument("--rate-hz", type=float, default=M13_6_DEFAULT_RATE_HZ)
    parser.add_argument("--max-steps", type=int, default=9000)
    parser.add_argument("--dry-run", action="store_true", help="run MuJoCo and retain frames in memory without UDP")
    args = parser.parse_args(argv)
    sink = RecordingStateSink() if args.dry_run else UdpLatestStateSender(args.host, args.port)
    result = run_mujoco_pick_place_stream(args.logical_block_id, sink=sink, rate_hz=args.rate_hz, max_steps=args.max_steps)
    result["dryRun"] = bool(args.dry_run)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in ("completed", "completed_with_result") else 1


__all__ = ["M13_6_PROTOCOL_VERSION", "M13_6_MESSAGE_TYPE", "M13_6_UDP_PORT", "MujocoToQuestTransform", "BlockWorldState", "RobotWorldStateFrame", "LatestStateBuffer", "UdpLatestStateSender", "RecordingStateSink", "MujocoStateSampler", "run_mujoco_pick_place_stream", "run_mujoco_sequential_stream"]


if __name__ == "__main__":
    raise SystemExit(main())
