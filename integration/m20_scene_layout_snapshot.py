"""Versioned M20 Quest scene-layout snapshot and bounded randomizer.

The Quest instance is authoritative at runtime and sends the resulting JSON
snapshot with each confirmed batch. This module provides the matching Python
contract for deterministic validation and MuJoCo replay; Python does not
re-randomize an accepted Quest snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import random
import secrets
from typing import Any
import uuid

from integration.m20_assistive_scene_contract import (
    EXPECTED_CANDIDATE_ORDER,
    LAYOUT_EDGE_CLEARANCE_METERS,
    M9_GRASP_REACHABLE_X_BOUNDS_METERS,
    M9_GRASP_REACHABLE_Y_BOUNDS_METERS,
    M9_GRASP_SOURCE_IDS,
    M20AssistiveSceneError,
    _entity_map,
    _vector,
    spatial_candidate_order,
    validate_spec,
)


SNAPSHOT_SCHEMA_VERSION = 1
SCENE_TEMPLATE_ID = "m20_daily_assistive_desk"
COORDINATE_TRANSFORM_ID = "m20_table_local_to_mujoco_v1"
OBJECT_CLEARANCE_METERS = 0.02
ROBOT_BASE_KEEP_OUT_RADIUS_METERS = 0.085
MAX_LAYOUT_ATTEMPTS = 96
MAX_POSITION_ATTEMPTS = 256
RANDOMIZED_OBJECT_IDS = (
    "assist_storage_box",
    "assist_phone",
    "assist_button_switch",
    "assist_medicine_box",
    "assist_wireless_charger",
)
FALLBACK_LAYOUT = {
    "assist_medicine_box": (-0.24, 0.13),
    "assist_storage_box": (0.00, 0.12),
    "assist_phone": (0.24, 0.03),
    "assist_button_switch": (-0.25, 0.015),
    "assist_wireless_charger": (0.25, 0.17),
}


def _position_dict(position: tuple[float, float, float]) -> dict[str, float]:
    return {axis: float(position[index]) for index, axis in enumerate(("x", "y", "z"))}


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _box_clearance_ok(center, size, other_center, other_size, clearance=OBJECT_CLEARANCE_METERS):
    return any(
        abs(center[axis] - other_center[axis])
        >= (size[axis] + other_size[axis]) / 2.0 + clearance
        for axis in range(3)
    )


def _robot_base_clear(center, size, robot_base):
    radius = math.hypot(size[0] / 2.0, size[1] / 2.0)
    required = ROBOT_BASE_KEEP_OUT_RADIUS_METERS + radius + OBJECT_CLEARANCE_METERS
    return math.hypot(center[0] - robot_base[0], center[1] - robot_base[1]) >= required


def _candidate_position_is_valid(entity, position, table_size, occupied, zone, robot_base):
    size = _vector(entity["dimensionsMeters"], entity["semanticId"] + ".dimensionsMeters")
    x, y, z = position
    if entity["semanticId"] in M9_GRASP_SOURCE_IDS and (
        x < M9_GRASP_REACHABLE_X_BOUNDS_METERS[0]
        or x > M9_GRASP_REACHABLE_X_BOUNDS_METERS[1]
        or y < M9_GRASP_REACHABLE_Y_BOUNDS_METERS[0]
        or y > M9_GRASP_REACHABLE_Y_BOUNDS_METERS[1]
    ):
        return False
    if abs(x) + size[0] / 2.0 > table_size[0] / 2.0 - LAYOUT_EDGE_CLEARANCE_METERS:
        return False
    if abs(y) + size[1] / 2.0 > table_size[1] / 2.0 - LAYOUT_EDGE_CLEARANCE_METERS:
        return False
    center = (x, y, z)
    if not _robot_base_clear(center, size, robot_base):
        return False
    if not _box_clearance_ok(center, size, zone[0], zone[1]):
        return False
    return all(_box_clearance_ok(center, size, other_center, other_size) for other_center, other_size in occupied)


def _set_entity_position(entity, position):
    entity["positionMeters"] = _position_dict(position)


def _make_snapshot(spec, seed, scene_id, created_utc, method, fallback_used):
    entities = _entity_map(spec)
    objects = []
    for semantic_id in (item["semanticId"] for item in spec["entities"]):
        entity = entities[semantic_id]
        objects.append({
            "semanticId": semantic_id,
            "targetId": entity.get("targetId", ""),
            "logicalBlockId": entity.get("logicalBlockId", ""),
            "objectType": entity.get("semanticLabel", entity.get("sourceKind", "unknown")),
            "role": entity.get("role", ""),
            "sourceKind": entity.get("sourceKind", ""),
            "selectable": bool(entity.get("selectable", False)),
            "fixedPose": semantic_id == "assist_user_zone",
            "positionMeters": deepcopy(entity["positionMeters"]),
            "dimensionsMeters": deepcopy(entity["dimensionsMeters"]),
            "yawDegrees": float(entity.get("yawDegrees", 0.0)),
            "affordanceTags": list(entity.get("affordanceTags", [])),
        })
    return {
        "schemaVersion": SNAPSHOT_SCHEMA_VERSION,
        "sceneId": str(scene_id),
        "templateId": SCENE_TEMPLATE_ID,
        "randomSeed": int(seed),
        "createdUtc": str(created_utc),
        "randomizationMethod": str(method),
        "fallbackUsed": bool(fallback_used),
        "coordinateFrame": {
            "id": COORDINATE_TRANSFORM_ID,
            "unit": "meter",
            "origin": "center of the tabletop top surface",
            "x": "user visual right",
            "y": "away from user toward robot",
            "z": "up",
            "unityTableRootLocalPositionAdapter": "(-x, z, -y)",
            "mujocoWorldPositionAdapter": "(x, y, tableTopWorldZ + z)",
        },
        "table": {
            "dimensionsMeters": deepcopy(spec["table"]["dimensionsMeters"]),
            "mujocoTopSurfaceWorldZMeters": float(spec["table"]["mujocoTopSurfaceWorldZMeters"]),
        },
        "candidateOrderFarToNearLeftToRight": list(spec["candidateOrderFarToNearLeftToRight"]),
        "objects": objects,
    }


def _randomized_positions(spec: dict[str, Any], rng: random.Random) -> dict[str, tuple[float, float, float]] | None:
    entities = _entity_map(spec)
    table_size = _vector(spec["table"]["dimensionsMeters"], "table.dimensionsMeters")
    robot_base = _vector(spec["robot"]["basePositionMeters"], "robot.basePositionMeters")
    zone_entity = entities["assist_user_zone"]
    zone_center = _vector(zone_entity["positionMeters"], "assist_user_zone.positionMeters")
    zone_size = _vector(zone_entity["dimensionsMeters"], "assist_user_zone.dimensionsMeters")
    occupied = []
    positions = {}
    for semantic_id in RANDOMIZED_OBJECT_IDS:
        entity = entities[semantic_id]
        size = _vector(entity["dimensionsMeters"], semantic_id + ".dimensionsMeters")
        min_x = -table_size[0] / 2.0 + size[0] / 2.0 + LAYOUT_EDGE_CLEARANCE_METERS
        max_x = table_size[0] / 2.0 - size[0] / 2.0 - LAYOUT_EDGE_CLEARANCE_METERS
        min_y = max(
            -table_size[1] / 2.0 + size[1] / 2.0 + LAYOUT_EDGE_CLEARANCE_METERS,
            zone_center[1] + zone_size[1] / 2.0 + size[1] / 2.0 + OBJECT_CLEARANCE_METERS,
        )
        max_y = table_size[1] / 2.0 - size[1] / 2.0 - LAYOUT_EDGE_CLEARANCE_METERS
        if semantic_id in M9_GRASP_SOURCE_IDS:
            min_x = max(min_x, M9_GRASP_REACHABLE_X_BOUNDS_METERS[0])
            max_x = min(max_x, M9_GRASP_REACHABLE_X_BOUNDS_METERS[1])
            min_y = max(min_y, M9_GRASP_REACHABLE_Y_BOUNDS_METERS[0])
            max_y = min(max_y, M9_GRASP_REACHABLE_Y_BOUNDS_METERS[1])
        for _attempt in range(MAX_POSITION_ATTEMPTS):
            candidate = (rng.uniform(min_x, max_x), rng.uniform(min_y, max_y),
                         _vector(entity["positionMeters"], semantic_id + ".positionMeters")[2])
            if _candidate_position_is_valid(
                entity, candidate, table_size, occupied, (zone_center, zone_size), robot_base
            ):
                positions[semantic_id] = candidate
                occupied.append((candidate, size))
                break
        else:
            return None
    return positions


def _apply_positions_to_spec(canonical_spec: dict[str, Any], positions: dict[str, tuple[float, float, float]]) -> dict[str, Any]:
    result = deepcopy(canonical_spec)
    original_entities = _entity_map(canonical_spec)
    entities = _entity_map(result)
    deltas = {}
    for semantic_id, position in positions.items():
        original = _vector(original_entities[semantic_id]["positionMeters"], semantic_id + ".positionMeters")
        delta = tuple(position[index] - original[index] for index in range(3))
        deltas[semantic_id] = delta
        _set_entity_position(entities[semantic_id], position)

    for child_id, parent_id in (("assist_storage_lid", "assist_storage_box"),
                                ("assist_button_cap", "assist_button_switch")):
        original = _vector(original_entities[child_id]["positionMeters"], child_id + ".positionMeters")
        delta = deltas[parent_id]
        _set_entity_position(entities[child_id], tuple(original[index] + delta[index] for index in range(3)))

    for articulation in result.get("articulations", []):
        if articulation.get("jointId") == "assist_storage_lid_hinge":
            original_pivot = _vector(articulation["pivotPositionMeters"], "storage hinge pivot")
            delta = deltas["assist_storage_box"]
            articulation["pivotPositionMeters"] = _position_dict(
                tuple(original_pivot[index] + delta[index] for index in range(3))
            )

    # Preserve each allowlisted placement's target-relative offset. USER ZONE
    # stays fixed; movable fixed-surface targets such as the charger carry the
    # phone placement along with their own randomized pose.
    for semantic_id, entity in entities.items():
        for placement in entity.get("placements", []):
            target_id = placement["targetId"]
            if target_id not in deltas:
                continue
            original_pose = _vector(placement["positionMeters"], "placement.positionMeters")
            delta = deltas[target_id]
            placement["positionMeters"] = _position_dict(
                (original_pose[0] + delta[0], original_pose[1] + delta[1], original_pose[2])
            )

    result["candidateOrderFarToNearLeftToRight"] = list(spatial_candidate_order(result))
    return result


def create_scene_layout_snapshot(
    spec: dict[str, Any],
    seed: int | None = None,
    *,
    scene_id: str | None = None,
    created_utc: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create and validate one randomized snapshot and its exact MuJoCo spec.

    A fresh random seed is used unless a deterministic developer/test seed is
    supplied. Bounded placement failure falls back to a validated known layout
    and is made explicit in the snapshot metadata.
    """
    canonical = deepcopy(spec)
    validate_spec(canonical)
    if seed is None:
        seed = secrets.randbits(31)
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 0x7FFFFFFF:
        raise M20AssistiveSceneError("randomSeed must be an integer in [0, 2^31-1]")
    final_scene_id = str(scene_id or "m20-" + uuid.uuid4().hex)
    if not final_scene_id.strip() or final_scene_id == SCENE_TEMPLATE_ID:
        raise M20AssistiveSceneError("runtime sceneId must be a unique non-empty session identity")
    final_timestamp = created_utc or _timestamp_utc()
    rng = random.Random(seed)
    runtime_spec = None
    for _layout_attempt in range(MAX_LAYOUT_ATTEMPTS):
        positions = _randomized_positions(canonical, rng)
        if positions is None:
            continue
        candidate = _apply_positions_to_spec(canonical, positions)
        try:
            validate_spec(candidate)
        except M20AssistiveSceneError:
            continue
        runtime_spec = candidate
        break

    fallback_used = runtime_spec is None
    if fallback_used:
        fallback = {
            semantic_id: (
                FALLBACK_LAYOUT[semantic_id][0],
                FALLBACK_LAYOUT[semantic_id][1],
                _vector(_entity_map(canonical)[semantic_id]["positionMeters"], semantic_id + ".positionMeters")[2],
            )
            for semantic_id in RANDOMIZED_OBJECT_IDS
        }
        runtime_spec = _apply_positions_to_spec(canonical, fallback)
        validate_spec(runtime_spec)

    snapshot = _make_snapshot(
        runtime_spec,
        seed,
        final_scene_id,
        final_timestamp,
        "bounded_uniform_rejection_v1" if not fallback_used else "validated_fallback_v1",
        fallback_used,
    )
    return snapshot, runtime_spec


def apply_scene_layout_snapshot(spec: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate a received snapshot and produce the matching MuJoCo scene spec."""
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != SNAPSHOT_SCHEMA_VERSION:
        raise M20AssistiveSceneError("unsupported SceneLayoutSnapshot schemaVersion")
    if snapshot.get("templateId") != SCENE_TEMPLATE_ID:
        raise M20AssistiveSceneError("SceneLayoutSnapshot templateId mismatch")
    scene_id = snapshot.get("sceneId")
    if not isinstance(scene_id, str) or not scene_id.strip() or scene_id == SCENE_TEMPLATE_ID:
        raise M20AssistiveSceneError("SceneLayoutSnapshot runtime sceneId is invalid")
    if (isinstance(snapshot.get("randomSeed"), bool) or not isinstance(snapshot.get("randomSeed"), int)
            or not 0 <= snapshot["randomSeed"] <= 0x7FFFFFFF):
        raise M20AssistiveSceneError("SceneLayoutSnapshot randomSeed is invalid")
    frame = snapshot.get("coordinateFrame")
    expected_frame = {
        "id": COORDINATE_TRANSFORM_ID,
        "unit": "meter",
        "origin": "center of the tabletop top surface",
        "x": "user visual right",
        "y": "away from user toward robot",
        "z": "up",
        "unityTableRootLocalPositionAdapter": "(-x, z, -y)",
        "mujocoWorldPositionAdapter": "(x, y, tableTopWorldZ + z)",
    }
    if not isinstance(frame, dict) or any(frame.get(key) != value for key, value in expected_frame.items()):
        raise M20AssistiveSceneError("SceneLayoutSnapshot coordinate transform is unknown")
    canonical = deepcopy(spec)
    snapshot_table = snapshot.get("table")
    if not isinstance(snapshot_table, dict) or snapshot_table.get("dimensionsMeters") != canonical.get("table", {}).get("dimensionsMeters"):
        raise M20AssistiveSceneError("SceneLayoutSnapshot table dimensions do not match the M20 template")
    if abs(float(snapshot_table.get("mujocoTopSurfaceWorldZMeters", float("nan"))) -
           float(canonical["table"]["mujocoTopSurfaceWorldZMeters"])) > 1e-9:
        raise M20AssistiveSceneError("SceneLayoutSnapshot tabletop transform height does not match the M20 template")
    objects = snapshot.get("objects")
    if not isinstance(objects, list):
        raise M20AssistiveSceneError("SceneLayoutSnapshot objects must be an array")
    canonical_entities = _entity_map(canonical)
    seen = set()
    positions = {}
    for index, item in enumerate(objects):
        if not isinstance(item, dict):
            raise M20AssistiveSceneError("SceneLayoutSnapshot objects[{}] must be an object".format(index))
        semantic_id = item.get("semanticId")
        if semantic_id not in canonical_entities:
            raise M20AssistiveSceneError("SceneLayoutSnapshot contains unknown semanticId {!r}".format(semantic_id))
        if semantic_id in seen:
            raise M20AssistiveSceneError("SceneLayoutSnapshot contains duplicate semanticId {!r}".format(semantic_id))
        seen.add(semantic_id)
        expected = canonical_entities[semantic_id]
        for field in ("targetId", "logicalBlockId", "role", "sourceKind"):
            if item.get(field, "") != expected.get(field, ""):
                raise M20AssistiveSceneError("SceneLayoutSnapshot {} identity mismatch for {}".format(field, semantic_id))
        if item.get("dimensionsMeters") != expected.get("dimensionsMeters"):
            raise M20AssistiveSceneError("SceneLayoutSnapshot footprint mismatch for {}".format(semantic_id))
        expected_type = expected.get("semanticLabel", expected.get("sourceKind", "unknown"))
        if item.get("objectType") != expected_type:
            raise M20AssistiveSceneError("SceneLayoutSnapshot object class mismatch for {}".format(semantic_id))
        if item.get("selectable") is not bool(expected.get("selectable", False)):
            raise M20AssistiveSceneError("SceneLayoutSnapshot selectable identity mismatch for {}".format(semantic_id))
        if abs(float(item.get("yawDegrees", float("nan"))) - float(expected.get("yawDegrees", 0.0))) > 1e-9:
            raise M20AssistiveSceneError("SceneLayoutSnapshot orientation mismatch for {}".format(semantic_id))
        if sorted(item.get("affordanceTags", [])) != sorted(expected.get("affordanceTags", [])):
            raise M20AssistiveSceneError("SceneLayoutSnapshot affordance mismatch for {}".format(semantic_id))
        expected_fixed = semantic_id == "assist_user_zone"
        if item.get("fixedPose") is not expected_fixed:
            raise M20AssistiveSceneError("SceneLayoutSnapshot fixed-pose flag mismatch for {}".format(semantic_id))
        position = _vector(item.get("positionMeters"), semantic_id + ".positionMeters")
        if expected_fixed and position != _vector(expected["positionMeters"], semantic_id + ".positionMeters"):
            raise M20AssistiveSceneError("SceneLayoutSnapshot moved the fixed USER ZONE")
        positions[semantic_id] = position
    if seen != set(canonical_entities):
        raise M20AssistiveSceneError("SceneLayoutSnapshot is missing semantic object poses")

    dynamic_positions = {
        semantic_id: positions[semantic_id]
        for semantic_id in RANDOMIZED_OBJECT_IDS
    }
    result = _apply_positions_to_spec(canonical, dynamic_positions)
    # The snapshot also carries articulated parts explicitly; check these are
    # coherent with the owner translation before using the same positions.
    resolved = _entity_map(result)
    for semantic_id in ("assist_storage_lid", "assist_button_cap"):
        if positions[semantic_id] != _vector(resolved[semantic_id]["positionMeters"], semantic_id + ".positionMeters"):
            raise M20AssistiveSceneError("SceneLayoutSnapshot articulated part pose is inconsistent: {}".format(semantic_id))
    result["candidateOrderFarToNearLeftToRight"] = list(spatial_candidate_order(result))
    if snapshot.get("candidateOrderFarToNearLeftToRight") != result["candidateOrderFarToNearLeftToRight"]:
        raise M20AssistiveSceneError("SceneLayoutSnapshot candidate order does not match its object poses")
    validate_spec(result)
    result["runtimeSceneId"] = scene_id
    result["layoutSeed"] = int(snapshot["randomSeed"])
    return result


def serialize_scene_layout_snapshot(snapshot: dict[str, Any]) -> str:
    """Stable JSON serialization for Quest batch payloads and evidence files."""
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def parse_scene_layout_snapshot(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return deepcopy(value)
    if not isinstance(value, str) or not value:
        raise M20AssistiveSceneError("sceneLayoutSnapshotJson is required")
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise M20AssistiveSceneError("sceneLayoutSnapshotJson is invalid JSON") from error
    if not isinstance(parsed, dict):
        raise M20AssistiveSceneError("SceneLayoutSnapshot root must be an object")
    return parsed


class M20SceneSnapshotRegistry:
    """Bind PC execution to one immutable Quest scene for a process session."""

    def __init__(self):
        self.scene_id: str | None = None
        self.snapshot_sha256: str | None = None
        self.snapshot: dict[str, Any] | None = None

    def accept_confirmed_batch(self, batch: dict[str, Any], canonical_spec: dict[str, Any]):
        if not isinstance(batch, dict):
            raise M20AssistiveSceneError("confirmed M20 batch must be an object")
        scene_id = batch.get("sceneId")
        snapshot = parse_scene_layout_snapshot(batch.get("sceneLayoutSnapshotJson"))
        if scene_id != snapshot.get("sceneId"):
            raise M20AssistiveSceneError("confirmed batch sceneId does not match its SceneLayoutSnapshot")
        runtime_spec = apply_scene_layout_snapshot(canonical_spec, snapshot)
        snapshot_hash = hashlib.sha256(serialize_scene_layout_snapshot(snapshot).encode("utf-8")).hexdigest()
        if self.scene_id is None:
            self.scene_id = scene_id
            self.snapshot_sha256 = snapshot_hash
            self.snapshot = snapshot
        elif scene_id != self.scene_id:
            raise M20AssistiveSceneError("stale scene command rejected: batch sceneId is not the active Quest scene")
        elif snapshot_hash != self.snapshot_sha256:
            raise M20AssistiveSceneError("scene snapshot changed during a frozen M20 session")

        selectable = {
            item["targetId"]: item
            for item in snapshot["objects"]
            if item.get("selectable") is True
        }
        selections = batch.get("selections")
        if not isinstance(selections, list) or not selections:
            raise M20AssistiveSceneError("confirmed M20 batch contains no selections")
        seen_targets = set()
        seen_slots = set()
        for selection in selections:
            target_id = selection.get("targetId") if isinstance(selection, dict) else None
            if target_id not in selectable:
                raise M20AssistiveSceneError("confirmed M20 batch contains unknown TargetId {!r}".format(target_id))
            if target_id in seen_targets:
                raise M20AssistiveSceneError("confirmed M20 batch contains duplicate TargetId {!r}".format(target_id))
            semantic = selectable[target_id]
            if (selection.get("logicalBlockId") is not None and
                    selection.get("logicalBlockId") != semantic.get("logicalBlockId")):
                raise M20AssistiveSceneError("confirmed M20 batch logical ID does not match its snapshot TargetId")
            slot = selection.get("slotIndex")
            if isinstance(slot, bool) or not isinstance(slot, int) or slot not in (0, 1, 2) or slot in seen_slots:
                raise M20AssistiveSceneError("confirmed M20 batch slot mapping is invalid or duplicated")
            if selection.get("predictedClassIndex") != slot:
                raise M20AssistiveSceneError("confirmed M20 batch class index and slot index disagree")
            seen_targets.add(target_id)
            seen_slots.add(slot)
        return snapshot, runtime_spec
