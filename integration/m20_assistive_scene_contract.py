"""Canonical M20 assistive-desk specification and MuJoCo scene checks.

The Unity Resources JSON is the only hand-authored scene definition. Unity
loads it directly; this module reads the same file to build the M20 MuJoCo
scene and produce machine-readable geometry/articulation evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
from typing import Any
import zlib


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC_PATH = (
    ROOT
    / "m7_unity6000"
    / "Assets"
    / "Resources"
    / "BCI"
    / "M20"
    / "assistive_desk_scene.json"
)
EXPECTED_CANDIDATE_ORDER = (
    "assist_medicine_box",
    "assist_storage_box",
    "assist_phone",
    "assist_button_switch",
    "assist_wireless_charger",
    "assist_user_zone",
)
EXPECTED_SLOTS = ((0, 7.2, 5), (1, 9.0, 4), (2, 12.0, 3))
GEOMETRY_TOLERANCE_METERS = 0.005
YAW_TOLERANCE_DEGREES = 2.0
LAYOUT_EDGE_CLEARANCE_METERS = 0.025
LAYOUT_ROW_TOLERANCE_METERS = 0.02
M9_GRASP_SOURCE_IDS = ("assist_medicine_box", "assist_phone")
M9_GRASP_REACHABLE_X_BOUNDS_METERS = (-0.24, 0.24)
M9_GRASP_REACHABLE_Y_BOUNDS_METERS = (0.0, 0.14)


class M20AssistiveSceneError(ValueError):
    """The canonical assistive scene violates a frozen M20 scene contract."""


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _vector(value: Any, field: str) -> tuple[float, float, float]:
    if not isinstance(value, dict) or not all(axis in value for axis in ("x", "y", "z")):
        raise M20AssistiveSceneError("{} must contain x/y/z".format(field))
    result = tuple(float(value[axis]) for axis in ("x", "y", "z"))
    if not all(math.isfinite(part) for part in result):
        raise M20AssistiveSceneError("{} must be finite".format(field))
    return result


def load_spec(path: Path | str = DEFAULT_SPEC_PATH) -> tuple[dict, str]:
    spec_path = Path(path)
    raw = spec_path.read_bytes()
    try:
        spec = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise M20AssistiveSceneError("invalid canonical JSON: {}".format(error)) from error
    if not isinstance(spec, dict):
        raise M20AssistiveSceneError("canonical scene root must be a JSON object")
    return spec, hashlib.sha256(raw).hexdigest()


def _entity_map(spec: dict) -> dict[str, dict]:
    entities = spec.get("entities")
    if not isinstance(entities, list) or not entities:
        raise M20AssistiveSceneError("entities must be a non-empty array")
    result: dict[str, dict] = {}
    target_ids: set[str] = set()
    logical_ids: set[str] = set()
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            raise M20AssistiveSceneError("entities[{}] must be an object".format(index))
        semantic_id = entity.get("semanticId")
        if not isinstance(semantic_id, str) or not semantic_id:
            raise M20AssistiveSceneError("entities[{}].semanticId is required".format(index))
        if semantic_id in result:
            raise M20AssistiveSceneError("duplicate semanticId: {}".format(semantic_id))
        result[semantic_id] = entity
        if entity.get("selectable"):
            target_id = entity.get("targetId")
            logical_id = entity.get("logicalBlockId")
            if not isinstance(target_id, str) or not target_id:
                raise M20AssistiveSceneError("{} needs an explicit TargetId".format(semantic_id))
            if target_id in target_ids:
                raise M20AssistiveSceneError("duplicate TargetId: {}".format(target_id))
            target_ids.add(target_id)
            if not isinstance(logical_id, str) or not logical_id:
                raise M20AssistiveSceneError("{} needs an explicit logicalBlockId".format(semantic_id))
            if logical_id in logical_ids:
                raise M20AssistiveSceneError("duplicate logicalBlockId: {}".format(logical_id))
            logical_ids.add(logical_id)
    return result


def spatial_candidate_order(spec: dict) -> tuple[str, ...]:
    """Return far-to-near rows, with each existing row sorted left-to-right.

    Rows retain the M20 acceptance rule: objects no more than 2 cm apart in
    table-local Y share a row; row membership is anchored to the farthest
    remaining candidate so the ordering is deterministic and transitive.
    """
    entities = _entity_map(spec)
    candidates = [entities[target_id] for target_id in EXPECTED_CANDIDATE_ORDER]
    candidates.sort(key=lambda item: (
        -_vector(item.get("positionMeters"), item["semanticId"] + ".positionMeters")[1],
        _vector(item.get("positionMeters"), item["semanticId"] + ".positionMeters")[0],
        item["semanticId"],
    ))
    rows: list[tuple[float, list[dict]]] = []
    for entity in candidates:
        y = _vector(entity["positionMeters"], entity["semanticId"] + ".positionMeters")[1]
        if not rows or abs(rows[-1][0] - y) > LAYOUT_ROW_TOLERANCE_METERS:
            rows.append((y, [entity]))
        else:
            rows[-1][1].append(entity)
    ordered: list[str] = []
    for _anchor_y, row in rows:
        row.sort(key=lambda item: (
            _vector(item["positionMeters"], item["semanticId"] + ".positionMeters")[0],
            item["semanticId"],
        ))
        ordered.extend(item["semanticId"] for item in row)
    return tuple(ordered)


def validate_spec(spec: dict) -> dict:
    """Validate IDs, dimensions, ordering, mirroring, bounds, and articulation."""
    errors: list[str] = []
    if spec.get("schemaVersion") != 1 or spec.get("sceneId") != "m20_daily_assistive_desk":
        raise M20AssistiveSceneError("unsupported M20 assistive scene schema or sceneId")
    frame = spec.get("frame", {})
    if (
        frame.get("unit") != "meter"
        or frame.get("x") != "user visual right"
        or frame.get("y") != "away from user toward robot"
        or frame.get("z") != "up"
        or frame.get("unityUsesNegativeScale") is not False
    ):
        errors.append("canonical table-local frame or Unity positive-scale adapter changed")

    table = spec.get("table", {})
    table_size = _vector(table.get("dimensionsMeters"), "table.dimensionsMeters")
    if any(value <= 0 for value in table_size):
        errors.append("table dimensions must be positive")
    table_top = table.get("mujocoTopSurfaceWorldZMeters")
    if not _finite_number(table_top):
        errors.append("table MuJoCo top surface height must be finite")

    slot_records = spec.get("slots")
    actual_slots = []
    if not isinstance(slot_records, list):
        errors.append("slots must be an array")
    else:
        for slot in slot_records:
            actual_slots.append(
                (
                    slot.get("slotIndex"),
                    float(slot.get("nominalFrequencyHz", float("nan"))),
                    slot.get("framesPerHalfCycle"),
                )
            )
        if tuple(actual_slots) != EXPECTED_SLOTS:
            errors.append("SSVEP slot/frequency/frame mapping must remain 0/1/2 = 7.2/9/12 Hz")

    entities = _entity_map(spec)
    order = tuple(spec.get("candidateOrderFarToNearLeftToRight", ()))
    if len(order) != len(EXPECTED_CANDIDATE_ORDER) or set(order) != set(EXPECTED_CANDIDATE_ORDER):
        errors.append("candidate order must contain each required semantic object exactly once")
    selectable = {
        semantic_id: entity
        for semantic_id, entity in entities.items()
        if entity.get("selectable") is True and entity.get("active") is True and entity.get("slotEligible") is True
    }
    if set(selectable) != set(EXPECTED_CANDIDATE_ORDER):
        errors.append("the six required candidates must be active, selectable, and slot eligible")
    if spec.get("candidatePageSize") != 3:
        errors.append("M16 active page size must remain three")

    centers: dict[str, tuple[float, float, float]] = {}
    extents: dict[str, tuple[float, float, float]] = {}
    collision_overrides = []
    for semantic_id, entity in entities.items():
        center = _vector(entity.get("positionMeters"), semantic_id + ".positionMeters")
        size = _vector(entity.get("dimensionsMeters"), semantic_id + ".dimensionsMeters")
        if any(component <= 0 for component in size):
            errors.append("{} has a non-positive dimension".format(semantic_id))
        collision = entity.get("mujocoCollisionGeometry")
        if collision is not None:
            collision_size = _vector(
                collision.get("dimensionsMeters"),
                semantic_id + ".mujocoCollisionGeometry.dimensionsMeters",
            )
            collision_offset = _vector(
                collision.get("centerOffsetMeters"),
                semantic_id + ".mujocoCollisionGeometry.centerOffsetMeters",
            )
            if any(component <= 0 for component in collision_size):
                errors.append("{} MuJoCo collision dimensions must be positive".format(semantic_id))
            if (
                abs(collision_size[0] - size[0]) > 1e-9
                or abs(collision_size[1] - size[1]) > 1e-9
                or collision_size[2] < size[2]
                or collision_size[2] - size[2] > GEOMETRY_TOLERANCE_METERS + 1e-9
            ):
                errors.append("{} MuJoCo collision override may only add up to 5 mm in Z".format(semantic_id))
            if (
                abs(collision_offset[0]) > 1e-9
                or abs(collision_offset[1]) > 1e-9
                or abs(collision_offset[2] - (collision_size[2] - size[2]) / 2.0) > 1e-9
            ):
                errors.append("{} MuJoCo collision override must preserve the visible lower support plane".format(semantic_id))
            if center[2] + collision_offset[2] - collision_size[2] / 2.0 < -1e-6:
                errors.append("{} MuJoCo collision override starts below the tabletop".format(semantic_id))
            collision_overrides.append({
                "semanticId": semantic_id,
                "visibleDimensionsMeters": list(size),
                "collisionDimensionsMeters": list(collision_size),
                "centerOffsetMeters": list(collision_offset),
                "lowerSupportPlanePreserved": abs(
                    collision_offset[2] - (collision_size[2] - size[2]) / 2.0
                ) <= 1e-9,
                "maximumThicknessIncreaseMeters": collision_size[2] - size[2],
            })
        yaw = entity.get("yawDegrees", 0.0)
        if not _finite_number(yaw):
            errors.append("{} yaw must be finite".format(semantic_id))
        centers[semantic_id] = center
        extents[semantic_id] = size
        if entity.get("selectable") and size[2] > 0:
            bottom = center[2] - size[2] / 2.0
            if bottom < -1e-6:
                errors.append("{} starts below the tabletop".format(semantic_id))
        if entity.get("selectable"):
            if abs(float(yaw)) > YAW_TOLERANCE_DEGREES:
                errors.append("{} must preserve the reference yaw".format(semantic_id))
            if abs(center[0]) + size[0] / 2.0 > table_size[0] / 2.0 + 1e-6:
                errors.append("{} exceeds the table left/right boundary".format(semantic_id))
            if abs(center[1]) + size[1] / 2.0 > table_size[1] / 2.0 + 1e-6:
                errors.append("{} exceeds the table near/far boundary".format(semantic_id))
            if semantic_id != "assist_user_zone" and (
                abs(center[0]) + size[0] / 2.0 > table_size[0] / 2.0 - LAYOUT_EDGE_CLEARANCE_METERS + 1e-6
                or abs(center[1]) + size[1] / 2.0 > table_size[1] / 2.0 - LAYOUT_EDGE_CLEARANCE_METERS + 1e-6
            ):
                errors.append("{} violates the tabletop safe edge clearance".format(semantic_id))
            if semantic_id in M9_GRASP_SOURCE_IDS and (
                center[0] < M9_GRASP_REACHABLE_X_BOUNDS_METERS[0] - 1e-6
                or center[0] > M9_GRASP_REACHABLE_X_BOUNDS_METERS[1] + 1e-6
                or center[1] < M9_GRASP_REACHABLE_Y_BOUNDS_METERS[0] - 1e-6
                or center[1] > M9_GRASP_REACHABLE_Y_BOUNDS_METERS[1] + 1e-6
            ):
                errors.append("{} lies outside the tested M9 grasp-reachable layout envelope".format(semantic_id))

    zone = centers.get("assist_user_zone")
    if zone is None or zone[1] >= 0:
        errors.append("USER ZONE must stay on the front/user side (smaller Y)")
    robot = spec.get("robot", {})
    robot_position = _vector(robot.get("basePositionMeters"), "robot.basePositionMeters")
    if not selectable or robot_position[1] <= max(value[1] for value in centers.values() if value is not None):
        errors.append("robot must remain behind/farther than all tabletop candidates")

    # Check initial candidate AABBs. Contact at a shared boundary is allowed;
    # positive-volume overlap is not.
    order_ids = [semantic_id for semantic_id in order if semantic_id in centers]
    for index, left_id in enumerate(order_ids):
        left_center = centers[left_id]
        left_size = extents[left_id]
        for right_id in order_ids[index + 1 :]:
            right_center = centers[right_id]
            right_size = extents[right_id]
            overlaps = all(
                abs(left_center[axis] - right_center[axis])
                < (left_size[axis] + right_size[axis]) / 2.0 - 1e-6
                for axis in range(3)
            )
            if overlaps:
                errors.append("initial candidates interpenetrate: {} / {}".format(left_id, right_id))

    # Preserve the established far-to-near row and left-to-right row semantics
    # while deriving the order from the actual frozen positions.
    expected_spatial_order = spatial_candidate_order(spec)
    if order != expected_spatial_order:
        errors.append("candidateOrderFarToNearLeftToRight does not match the actual spatial order")

    placement_checks = []
    table_size = _vector(table.get("dimensionsMeters"), "table.dimensionsMeters")
    for source_id in ("assist_medicine_box", "assist_phone"):
        source = entities[source_id]
        source_size = extents[source_id]
        source_placements = source.get("placements", [])
        if not source_placements:
            errors.append("{} must have at least one deterministic placement target".format(source_id))
            continue
        for item in source_placements:
            target_id = item.get("targetId")
            target = entities.get(target_id)
            pose = _vector(item.get("positionMeters"), "{}.placement.positionMeters".format(source_id))
            if target is None or target_id not in centers or target_id not in extents:
                errors.append("{} placement references an unknown target {!r}".format(source_id, target_id))
                continue
            target_center = centers[target_id]
            target_size = extents[target_id]
            object_bottom = pose[2] - source_size[2] / 2.0
            target_top = target_center[2] + target_size[2] / 2.0
            vertical_gap = object_bottom - target_top
            if abs(vertical_gap) > 1e-6:
                errors.append(
                    "{} placement on {} must rest on its top surface (gap={:.6f} m)".format(
                        source_id, target_id, vertical_gap
                    )
                )
            if (
                abs(pose[0]) + source_size[0] / 2.0 > table_size[0] / 2.0 + 1e-6
                or abs(pose[1]) + source_size[1] / 2.0 > table_size[1] / 2.0 + 1e-6
            ):
                errors.append("{} placement on {} exceeds the tabletop".format(source_id, target_id))

            if target_id == "assist_user_zone" and (
                abs(pose[0] - target_center[0]) + source_size[0] / 2.0 > target_size[0] / 2.0 + 1e-6
                or abs(pose[1] - target_center[1]) + source_size[1] / 2.0 > target_size[1] / 2.0 + 1e-6
            ):
                errors.append("{} USER ZONE placement exceeds the safe footprint".format(source_id))

            for other_id in EXPECTED_CANDIDATE_ORDER:
                if other_id in (source_id, target_id):
                    continue
                other_center = centers[other_id]
                other_size = extents[other_id]
                overlaps = all(
                    abs(pose[axis] - other_center[axis])
                    < (source_size[axis] + other_size[axis]) / 2.0 - 1e-6
                    for axis in range(3)
                )
                if overlaps:
                    errors.append(
                        "{} placement on {} interpenetrates {}".format(source_id, target_id, other_id)
                    )
            placement_checks.append(
                {
                    "sourceId": source_id,
                    "targetId": target_id,
                    "objectBottomLocalZMeters": object_bottom,
                    "targetTopLocalZMeters": target_top,
                    "verticalGapMeters": vertical_gap,
                    "restsOnTarget": abs(vertical_gap) <= 1e-6,
                }
            )

    phone_placements = entities["assist_phone"].get("placements", [])
    charger_pose = next(
        (
            _vector(item.get("positionMeters"), "phone charger pose")
            for item in phone_placements
            if item.get("targetId") == "assist_wireless_charger"
        ),
        None,
    )
    if charger_pose is None or any(
        abs(charger_pose[axis] - centers["assist_wireless_charger"][axis]) > 1e-6
        for axis in range(2)
    ):
        errors.append("phone charger placement must be centered on the charger pad")

    articulations = {item.get("jointId"): item for item in spec.get("articulations", [])}
    hinge = articulations.get("assist_storage_lid_hinge")
    if not hinge or hinge.get("kind") != "hinge" or hinge.get("entityId") != "assist_storage_lid":
        errors.append("storage lid requires an articulated hinge")
    else:
        hinge_axis = _vector(hinge.get("axis"), "storage hinge axis")
        if hinge_axis != (1.0, 0.0, 0.0):
            errors.append("storage hinge axis must follow the rear-edge X axis")
        hinge_range = hinge.get("rangeDegrees", {})
        if (
            float(hinge_range.get("minimum", 0.0)) > -95.0
            or abs(float(hinge_range.get("maximum", 1.0))) > 1e-6
            or abs(float(hinge.get("openDegrees", 0.0)) + 100.0) > 1e-6
        ):
            errors.append("storage lid range/open angle must preserve the 100 degree target")
        hinge_pivot = _vector(hinge.get("pivotPositionMeters"), "storage hinge pivot")
        storage_size = extents.get("assist_storage_box", (0.0, 0.0, 0.0))
        storage_center = centers.get("assist_storage_box", (0.0, 0.0, 0.0))
        if abs(hinge_pivot[1] - (storage_center[1] + storage_size[1] / 2.0)) > 1e-6:
            errors.append("storage hinge must sit along the rear edge")
        if abs(hinge_pivot[2] - (storage_center[2] + storage_size[2] / 2.0)) > 1e-6:
            errors.append("storage hinge pivot must align with the closed lid underside")

    slide = articulations.get("assist_button_cap_slide")
    if not slide or slide.get("kind") != "slide" or slide.get("entityId") != "assist_button_cap":
        errors.append("button cap requires a prismatic slide articulation")
    else:
        button_axis = _vector(slide.get("axis"), "button slide axis")
        slide_range = slide.get("rangeMeters", {})
        stroke = float(slide_range.get("maximum", 0.0)) - float(slide_range.get("minimum", 0.0))
        if button_axis != (0.0, 0.0, 1.0) or abs(stroke - 0.005) > 1e-6:
            errors.append("button travel must be a 5 mm vertical prismatic motion")
        button_entity = entities.get("assist_button_switch", {})
        cap_entity = entities.get("assist_button_cap", {})
        housing = button_entity.get("construction", {})
        aperture = housing.get("apertureMeters", {})
        cap_size = extents.get("assist_button_cap", (0.0, 0.0, 0.0))
        clearance = float(housing.get("capClearanceMeters", 0.0))
        if (
            float(aperture.get("x", 0.0)) < cap_size[0] + 2.0 * clearance
            or float(aperture.get("y", 0.0)) < cap_size[1] + 2.0 * clearance
        ):
            errors.append("button cap aperture does not clear its full 5 mm stroke")
        expected_unpressed_z = (
            centers["assist_button_switch"][2]
            + extents["assist_button_switch"][2] / 2.0
            + cap_size[2] / 2.0
        )
        if abs(centers["assist_button_cap"][2] - expected_unpressed_z) > 1e-6:
            errors.append("button cap resting pose must sit flush on the housing top")

    if errors:
        raise M20AssistiveSceneError("; ".join(errors))

    return {
        "status": "PASS",
        "sceneId": spec["sceneId"],
        "coordinateFrame": frame,
        "candidateOrder": list(order),
        "candidatePages": [list(order[:3]), list(order[3:])],
        "slotMapping": [
            {"slotIndex": slot, "frequencyHz": frequency, "framesPerHalfCycle": frames}
            for slot, frequency, frames in EXPECTED_SLOTS
        ],
        "tableSizeMeters": dict(table["dimensionsMeters"]),
        "candidateCount": len(order),
        "crossEngineTargets": list(order),
        "mujocoCollisionOverrides": collision_overrides,
        "geometryPositionToleranceMeters": GEOMETRY_TOLERANCE_METERS,
        "geometryDimensionToleranceMeters": GEOMETRY_TOLERANCE_METERS,
        "geometryDimensionToleranceRelative": 0.02,
        "yawToleranceDegrees": YAW_TOLERANCE_DEGREES,
        "mirrorChecks": {
            "candidateOrderMatchesSpatialPositions": order == expected_spatial_order,
            "userZoneInFront": zone[1] < 0,
            "userZoneCanonicalPosition": (
                abs(zone[0]) <= 1e-9
                and abs(zone[1] + 0.19) <= 1e-9
                and abs(zone[2] - 0.001) <= 1e-9
            ),
            "robotBehindCandidates": robot_position[1] > max(value[1] for value in centers.values()),
        },
        "articulations": {
            "storageHingeDegrees": articulations["assist_storage_lid_hinge"]["rangeDegrees"],
            "buttonTravelMeters": stroke if slide else None,
            "buttonCapHoleClearanceMeters": clearance if slide else None,
        },
        "placementChecks": {
            "medicineRestsOnUserZone": any(
                item["sourceId"] == "assist_medicine_box"
                and item["targetId"] == "assist_user_zone"
                and item["restsOnTarget"]
                for item in placement_checks
            ),
            "phoneRestsOnUserZone": any(
                item["sourceId"] == "assist_phone"
                and item["targetId"] == "assist_user_zone"
                and item["restsOnTarget"]
                for item in placement_checks
            ),
            "phoneCenteredAndRestsOnCharger": any(
                item["sourceId"] == "assist_phone"
                and item["targetId"] == "assist_wireless_charger"
                and item["restsOnTarget"]
                for item in placement_checks
            ),
            "allPlacementPosesCollisionClear": True,
            "placementPoses": placement_checks,
        },
    }


def _mujoco_module():
    try:
        import mujoco
    except ImportError as error:
        raise M20AssistiveSceneError("MuJoCo is unavailable in the selected Python runtime") from error
    return mujoco


def build_mujoco_scene(spec: dict):
    """Compile the existing FR3/UMI model with M20 table geometry and entities."""
    mujoco = _mujoco_module()
    robot_root = ROOT / "robot_arm"
    robot_path = str(robot_root)
    if robot_path not in sys.path:
        sys.path.insert(0, robot_path)
    try:
        from utils.gripper_scene import GRIPPER_XML
    except ImportError as error:
        raise M20AssistiveSceneError("existing M9 gripper scene assets are unavailable") from error

    scene = mujoco.MjSpec.from_file(GRIPPER_XML)
    scene.visual.global_.offwidth = 1440
    scene.visual.global_.offheight = 900
    table_top = float(spec["table"]["mujocoTopSurfaceWorldZMeters"])
    table_size = _vector(spec["table"]["dimensionsMeters"], "table.dimensionsMeters")
    robot_base = _vector(spec["robot"]["basePositionMeters"], "robot.basePositionMeters")
    scene.body("base").pos = [
        robot_base[0],
        robot_base[1],
        table_top + robot_base[2],
    ]

    scene.worldbody.add_geom(
        name="assist_tabletop",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[table_size[0] / 2.0, table_size[1] / 2.0, table_size[2] / 2.0],
        pos=[0.0, 0.0, table_top - table_size[2] / 2.0],
        rgba=[0.55, 0.37, 0.22, 1.0],
    )

    entities = _entity_map(spec)
    for semantic_id in EXPECTED_CANDIDATE_ORDER:
        entity = entities[semantic_id]
        center = _vector(entity["positionMeters"], semantic_id + ".positionMeters")
        size = _vector(entity["dimensionsMeters"], semantic_id + ".dimensionsMeters")
        rgba = entity["color"]
        body = scene.worldbody.add_body(
            name=semantic_id,
            pos=[center[0], center[1], table_top + center[2]],
        )
        if semantic_id == "assist_storage_box":
            _add_hollow_storage_base(mujoco, body, size, rgba, entity["construction"])
        elif semantic_id == "assist_button_switch":
            _add_button_housing(mujoco, body, size, rgba, entity["construction"])
        else:
            geom_type = (
                mujoco.mjtGeom.mjGEOM_CYLINDER
                if entity["geometry"] == "cylinder"
                else mujoco.mjtGeom.mjGEOM_BOX
            )
            collision = entity.get("mujocoCollisionGeometry")
            if collision is None:
                geom = body.add_geom(
                    name=semantic_id + "_geom",
                    type=geom_type,
                    size=[size[0] / 2.0, size[1] / 2.0, size[2] / 2.0],
                    rgba=[float(rgba[key]) for key in ("r", "g", "b", "a")],
                )
            else:
                collision_size = _vector(
                    collision["dimensionsMeters"], semantic_id + ".collisionDimensionsMeters"
                )
                collision_offset = _vector(
                    collision["centerOffsetMeters"], semantic_id + ".collisionCenterOffsetMeters"
                )
                geom = body.add_geom(
                    name=semantic_id + "_grasp_collision_geom",
                    type=geom_type,
                    size=[collision_size[0] / 2.0, collision_size[1] / 2.0, collision_size[2] / 2.0],
                    pos=list(collision_offset),
                    rgba=[float(rgba["r"]), float(rgba["g"]), float(rgba["b"]), 0.0],
                )
                visual_geom = body.add_geom(
                    name=semantic_id + "_visual_geom",
                    type=geom_type,
                    size=[size[0] / 2.0, size[1] / 2.0, size[2] / 2.0],
                    rgba=[float(rgba[key]) for key in ("r", "g", "b", "a")],
                )
                visual_geom.contype = 0
                visual_geom.conaffinity = 0
                visual_geom.density = 0.0
            if entity.get("role") == "movable_source":
                geom.density = 180.0
                body.add_freejoint(name=semantic_id + "_free")

    articulation = {
        item["jointId"]: item for item in spec.get("articulations", [])
    }
    lid_entity = entities["assist_storage_lid"]
    lid_size = _vector(lid_entity["dimensionsMeters"], "assist_storage_lid.dimensionsMeters")
    hinge = articulation["assist_storage_lid_hinge"]
    hinge_pivot = _vector(hinge["pivotPositionMeters"], "storage hinge pivot")
    lid_body = scene.worldbody.add_body(
        name="assist_storage_lid",
        pos=[hinge_pivot[0], hinge_pivot[1], table_top + hinge_pivot[2]],
    )
    degree_range = hinge["rangeDegrees"]
    lid_body.add_joint(
        name=hinge["jointId"],
        type=mujoco.mjtJoint.mjJNT_HINGE,
        pos=[0.0, 0.0, 0.0],
        axis=list(_vector(hinge["axis"], "storage hinge axis")),
        range=[
            math.radians(float(degree_range["minimum"])),
            math.radians(float(degree_range["maximum"])),
        ],
        limited=True,
        damping=0.2,
    )
    lid_geom = lid_body.add_geom(
        name="assist_storage_lid_geom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[lid_size[0] / 2.0, lid_size[1] / 2.0, lid_size[2] / 2.0],
        pos=[0.0, -lid_size[1] / 2.0, lid_size[2] / 2.0],
        rgba=[float(lid_entity["color"][key]) for key in ("r", "g", "b", "a")],
    )
    lid_geom.density = 250.0

    cap = entities["assist_button_cap"]
    cap_center = _vector(cap["positionMeters"], "assist_button_cap.positionMeters")
    cap_size = _vector(cap["dimensionsMeters"], "assist_button_cap.dimensionsMeters")
    slide = articulation["assist_button_cap_slide"]
    button_body = scene.worldbody.add_body(
        name="assist_button_cap",
        pos=[
            cap_center[0],
            cap_center[1],
            table_top + cap_center[2] - cap_size[2] / 2.0 - float(slide["unpressedMeters"]),
        ],
    )
    button_range = slide["rangeMeters"]
    button_body.add_joint(
        name=slide["jointId"],
        type=mujoco.mjtJoint.mjJNT_SLIDE,
        pos=[0.0, 0.0, 0.0],
        axis=list(_vector(slide["axis"], "button slide axis")),
        range=[float(button_range["minimum"]), float(button_range["maximum"])],
        limited=True,
        damping=1.0,
    )
    cap_geom = button_body.add_geom(
        name="assist_button_cap_geom",
        type=mujoco.mjtGeom.mjGEOM_CYLINDER,
        size=[cap_size[0] / 2.0, cap_size[2] / 2.0, 0.0],
        pos=[0.0, 0.0, cap_size[2] / 2.0],
        rgba=[float(cap["color"][key]) for key in ("r", "g", "b", "a")],
    )
    cap_geom.density = 100.0

    _add_table_supports(mujoco, scene, table_size, table_top)
    scene.worldbody.add_geom(
        name="assist_floor",
        type=mujoco.mjtGeom.mjGEOM_PLANE,
        size=[0.0, 0.0, 0.05],
        pos=[0.0, 0.0, -0.01],
        rgba=[0.84, 0.84, 0.84, 1.0],
    )
    scene.worldbody.add_light(
        name="assist_key_light",
        pos=[0.0, -0.4, 2.0],
        dir=[0.0, 0.0, -1.0],
        intensity=1.8,
        diffuse=[1.0, 1.0, 1.0],
        ambient=[0.4, 0.4, 0.4],
        specular=[0.0, 0.0, 0.0],
    )
    scene.add_equality(
        name="m20_phone_grasp_weld",
        type=mujoco.mjtEq.mjEQ_WELD,
        active=0,
        name1="umi_umi_gripper_base",
        name2="assist_phone",
        objtype=mujoco.mjtObj.mjOBJ_BODY,
    )
    model = scene.compile()
    data = mujoco.MjData(model)
    _set_joint_qpos(mujoco, model, data, articulation["assist_button_cap_slide"]["jointId"], float(slide["unpressedMeters"]))
    mujoco.mj_forward(model, data)
    return model, data


def _add_hollow_storage_base(mujoco, body, full_size, rgba, construction):
    hx, hy, hz = (value / 2.0 for value in full_size)
    wall = float(construction["wallThicknessMeters"])
    bottom = float(construction["bottomThicknessMeters"])
    color = [float(rgba[key]) for key in ("r", "g", "b", "a")]
    body.add_geom(
        name="assist_storage_bottom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[hx, hy, bottom / 2.0],
        pos=[0.0, 0.0, -hz + bottom / 2.0],
        rgba=color,
    )
    wall_half_z = (full_size[2] - bottom) / 2.0
    wall_center_z = -hz + bottom + wall_half_z
    body.add_geom(
        name="assist_storage_left_wall",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[wall / 2.0, hy, wall_half_z],
        pos=[-hx + wall / 2.0, 0.0, wall_center_z],
        rgba=color,
    )
    body.add_geom(
        name="assist_storage_right_wall",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[wall / 2.0, hy, wall_half_z],
        pos=[hx - wall / 2.0, 0.0, wall_center_z],
        rgba=color,
    )
    side_half_x = hx - wall
    for name, y in (
        ("assist_storage_front_wall", -hy + wall / 2.0),
        ("assist_storage_rear_wall", hy - wall / 2.0),
    ):
        body.add_geom(
            name=name,
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[side_half_x, wall / 2.0, wall_half_z],
            pos=[0.0, y, wall_center_z],
            rgba=color,
        )


def _add_button_housing(mujoco, body, full_size, rgba, construction):
    hx, hy, hz = (value / 2.0 for value in full_size)
    aperture = construction["apertureMeters"]
    hole_hx = float(aperture["x"]) / 2.0
    hole_hy = float(aperture["y"]) / 2.0
    bottom = float(construction["bottomThicknessMeters"])
    color = [float(rgba[key]) for key in ("r", "g", "b", "a")]
    body.add_geom(
        name="assist_button_housing_bottom",
        type=mujoco.mjtGeom.mjGEOM_BOX,
        size=[hx, hy, bottom / 2.0],
        pos=[0.0, 0.0, -hz + bottom / 2.0],
        rgba=color,
    )
    wall_half_z = (full_size[2] - bottom) / 2.0
    wall_center_z = -hz + bottom + wall_half_z
    for name, x in (
        ("assist_button_housing_left", -hx + (hx - hole_hx) / 2.0),
        ("assist_button_housing_right", hx - (hx - hole_hx) / 2.0),
    ):
        body.add_geom(
            name=name,
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[(hx - hole_hx) / 2.0, hy, wall_half_z],
            pos=[x, 0.0, wall_center_z],
            rgba=color,
        )
    for name, y in (
        ("assist_button_housing_front", -hy + (hy - hole_hy) / 2.0),
        ("assist_button_housing_rear", hy - (hy - hole_hy) / 2.0),
    ):
        body.add_geom(
            name=name,
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[hole_hx, (hy - hole_hy) / 2.0, wall_half_z],
            pos=[0.0, y, wall_center_z],
            rgba=color,
        )


def _add_table_supports(mujoco, scene, table_size, table_top):
    half_x, half_y = table_size[0] / 2.0, table_size[1] / 2.0
    apron_height = 0.08
    apron_thickness = 0.035
    leg_size = 0.055
    leg_height = table_top - table_size[2]
    if leg_height <= 0.1:
        return
    apron_center_z = table_top - table_size[2] - apron_height / 2.0
    rgba = [0.39, 0.25, 0.15, 1.0]
    for name, size, pos in (
        ("assist_apron_front", [table_size[0] - 0.13, apron_thickness, apron_height], [0.0, -half_y + 0.065, apron_center_z]),
        ("assist_apron_rear", [table_size[0] - 0.13, apron_thickness, apron_height], [0.0, half_y - 0.065, apron_center_z]),
        ("assist_apron_left", [apron_thickness, table_size[1] - 0.13, apron_height], [-half_x + 0.065, 0.0, apron_center_z]),
        ("assist_apron_right", [apron_thickness, table_size[1] - 0.13, apron_height], [half_x - 0.065, 0.0, apron_center_z]),
    ):
        scene.worldbody.add_geom(
            name=name,
            type=mujoco.mjtGeom.mjGEOM_BOX,
            size=[value / 2.0 for value in size],
            pos=pos,
            rgba=rgba,
        )
    leg_center_z = leg_height / 2.0
    for sx in (-1, 1):
        for sy in (-1, 1):
            scene.worldbody.add_geom(
                name="assist_leg_{}{}".format("L" if sx < 0 else "R", "F" if sy < 0 else "B"),
                type=mujoco.mjtGeom.mjGEOM_BOX,
                size=[leg_size / 2.0, leg_size / 2.0, leg_height / 2.0],
                pos=[sx * (half_x - 0.065), sy * (half_y - 0.065), leg_center_z],
                rgba=rgba,
            )


def _set_joint_qpos(mujoco, model, data, joint_name: str, value: float) -> None:
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, joint_name)
    if joint_id < 0:
        raise M20AssistiveSceneError("compiled MuJoCo scene is missing joint {}".format(joint_name))
    data.qpos[int(model.jnt_qposadr[joint_id])] = value


def _geom_name(mujoco, model, geom_id: int) -> str:
    return mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, int(geom_id)) or ""


def check_m9_grasp_pose_reachability(spec: dict) -> list[dict]:
    """Ask the existing FR3/UMI planner whether each movable source is graspable."""
    robot_path = str(ROOT / "robot_arm")
    if robot_path not in sys.path:
        sys.path.insert(0, robot_path)
    try:
        from control.gripper_planner import GripperGraspPlanner
    except ImportError as error:
        raise M20AssistiveSceneError("existing M9 grasp planner is unavailable") from error

    results = []
    for source_id in ("assist_medicine_box", "assist_phone"):
        model, data = build_mujoco_scene(spec)
        planner = GripperGraspPlanner(model, data, source_id, place=False)
        if not planner.ik_ok:
            raise M20AssistiveSceneError("existing M9 planner cannot reach the {} grasp pose".format(source_id))
        results.append(
            {
                "sourceId": source_id,
                "planner": "GripperGraspPlanner",
                "ikReachable": bool(planner.ik_ok),
                "preGraspPositionMeters": [float(value) for value in planner.flange_pre],
                "graspPositionMeters": [float(value) for value in planner.flange_pos],
            }
        )
    return results


def check_mujoco_scene(spec: dict, model, data) -> dict:
    """Check compiled body/geometry parity, contacts, and articulation travel."""
    mujoco = _mujoco_module()
    tolerance = GEOMETRY_TOLERANCE_METERS
    table_top = float(spec["table"]["mujocoTopSurfaceWorldZMeters"])
    table_size = _vector(spec["table"]["dimensionsMeters"], "table.dimensionsMeters")
    table_geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "assist_tabletop")
    if table_geom_id < 0:
        raise M20AssistiveSceneError("compiled MuJoCo model is missing the canonical tabletop")
    table_center = data.geom_xpos[table_geom_id]
    table_expected_center = (0.0, 0.0, table_top - table_size[2] / 2.0)
    table_position_error = tuple(
        abs(float(table_center[axis]) - table_expected_center[axis]) for axis in range(3)
    )
    table_actual_size = tuple(float(model.geom_size[table_geom_id][axis] * 2.0) for axis in range(3))
    table_dimension_error = tuple(abs(table_actual_size[axis] - table_size[axis]) for axis in range(3))
    if max(table_position_error) > tolerance or max(table_dimension_error) > tolerance:
        raise M20AssistiveSceneError(
            "MuJoCo tabletop position/dimension parity failed: position={} dimension={}".format(
                table_position_error, table_dimension_error
            )
        )
    robot_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    if robot_body_id < 0:
        raise M20AssistiveSceneError("compiled MuJoCo scene is missing the existing M9 robot base")
    robot_base = _vector(spec["robot"]["basePositionMeters"], "robot.basePositionMeters")
    robot_expected_world = (robot_base[0], robot_base[1], table_top + robot_base[2])
    robot_actual_world = data.xpos[robot_body_id]
    robot_position_error = tuple(
        abs(float(robot_actual_world[axis]) - robot_expected_world[axis]) for axis in range(3)
    )
    if max(robot_position_error) > tolerance:
        raise M20AssistiveSceneError(
            "existing M9 robot base position error {} exceeds {} m".format(robot_position_error, tolerance)
        )
    entity_map = _entity_map(spec)
    checks = []

    for semantic_id in EXPECTED_CANDIDATE_ORDER:
        entity = entity_map[semantic_id]
        body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, semantic_id)
        if body_id < 0:
            raise M20AssistiveSceneError("compiled MuJoCo model is missing {}".format(semantic_id))
        actual_center = data.xpos[body_id]
        expected = _vector(entity["positionMeters"], semantic_id + ".positionMeters")
        position_error = (
            abs(float(actual_center[0]) - expected[0]),
            abs(float(actual_center[1]) - expected[1]),
            abs(float(actual_center[2]) - table_top - expected[2]),
        )
        geom_start = int(model.body_geomadr[body_id])
        geom_count = int(model.body_geomnum[body_id])
        if geom_count <= 0:
            raise M20AssistiveSceneError("{} has no physical collision geometry".format(semantic_id))
        geom_ids = range(geom_start, geom_start + geom_count)
        min_corner = [float("inf")] * 3
        max_corner = [float("-inf")] * 3
        for geom_id in geom_ids:
            center = data.geom_xpos[geom_id]
            rotation = data.geom_xmat[geom_id].reshape((3, 3))
            half = model.geom_size[geom_id]
            if model.geom_type[geom_id] == mujoco.mjtGeom.mjGEOM_BOX:
                local_half = [float(half[axis]) for axis in range(3)]
            elif model.geom_type[geom_id] == mujoco.mjtGeom.mjGEOM_CYLINDER:
                local_half = [float(half[0]), float(half[0]), float(half[1])]
            else:
                raise M20AssistiveSceneError("{} has unsupported M20 geometry".format(_geom_name(mujoco, model, geom_id)))
            world_half = [
                sum(abs(float(rotation[row, col])) * local_half[col] for col in range(3))
                for row in range(3)
            ]
            for axis in range(3):
                min_corner[axis] = min(min_corner[axis], float(center[axis]) - world_half[axis])
                max_corner[axis] = max(max_corner[axis], float(center[axis]) + world_half[axis])
        actual_size = tuple(max_corner[axis] - min_corner[axis] for axis in range(3))
        expected_size = _vector(entity["dimensionsMeters"], semantic_id + ".dimensionsMeters")
        dimension_errors = tuple(abs(actual_size[axis] - expected_size[axis]) for axis in range(3))
        if max(position_error) > tolerance:
            raise M20AssistiveSceneError(
                "{} MuJoCo position error {} exceeds {} m".format(semantic_id, position_error, tolerance)
            )
        if any(
            dimension_errors[axis] > max(tolerance, expected_size[axis] * 0.02) + 1e-9
            for axis in range(3)
        ):
            raise M20AssistiveSceneError(
                "{} MuJoCo dimension error {} exceeds tolerance".format(semantic_id, dimension_errors)
            )
        checks.append(
            {
                "semanticId": semantic_id,
                "targetId": entity["targetId"],
                "positionMeters": {"x": expected[0], "y": expected[1], "z": expected[2]},
                "mujocoPositionErrorMeters": list(position_error),
                "dimensionsMeters": {"x": expected_size[0], "y": expected_size[1], "z": expected_size[2]},
                "mujocoDimensionErrorMeters": list(dimension_errors),
                "yawErrorDegrees": 0.0,
            }
        )

    # Articulated parts have body origins at their pivots/slide bases; compare
    # their real geom transforms separately from candidate body centers.
    initial_contact_count = int(data.ncon)
    contact_penetrations = []
    for contact_index in range(initial_contact_count):
        contact = data.contact[contact_index]
        geom_a = _geom_name(mujoco, model, contact.geom1)
        geom_b = _geom_name(mujoco, model, contact.geom2)
        if (geom_a.startswith("assist_") or geom_b.startswith("assist_")) and contact.dist < -1e-4:
            contact_penetrations.append(
                {"geom1": geom_a, "geom2": geom_b, "distanceMeters": float(contact.dist)}
            )
    if contact_penetrations:
        raise M20AssistiveSceneError("initial M20 contact interpenetration: {}".format(contact_penetrations))

    placement_pose_checks = []
    initial_qpos = data.qpos.copy()
    try:
        for source_id in ("assist_medicine_box", "assist_phone"):
            source = entity_map[source_id]
            source_size = _vector(source["dimensionsMeters"], source_id + ".dimensionsMeters")
            source_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, source_id)
            free_joint_id = mujoco.mj_name2id(
                model, mujoco.mjtObj.mjOBJ_JOINT, source_id + "_free"
            )
            if source_body_id < 0 or free_joint_id < 0:
                raise M20AssistiveSceneError("{} is missing its movable free joint".format(source_id))
            qpos_address = int(model.jnt_qposadr[free_joint_id])
            for placement in source.get("placements", []):
                target_id = placement["targetId"]
                target = entity_map[target_id]
                target_size = _vector(target["dimensionsMeters"], target_id + ".dimensionsMeters")
                target_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, target_id)
                pose = _vector(placement["positionMeters"], source_id + ".placement.positionMeters")
                data.qpos[qpos_address : qpos_address + 7] = [
                    pose[0], pose[1], table_top + pose[2], 1.0, 0.0, 0.0, 0.0
                ]
                mujoco.mj_forward(model, data)
                source_center = data.xpos[source_body_id]
                target_center = data.xpos[target_body_id]
                object_bottom = float(source_center[2]) - source_size[2] / 2.0
                target_top = float(target_center[2]) + target_size[2] / 2.0
                vertical_gap = object_bottom - target_top
                if abs(vertical_gap) > 1e-6:
                    raise M20AssistiveSceneError(
                        "{} placement on {} has a MuJoCo support gap of {:.6f} m".format(
                            source_id, target_id, vertical_gap
                        )
                    )

                source_geom_start = int(model.body_geomadr[source_body_id])
                source_geom_end = source_geom_start + int(model.body_geomnum[source_body_id])
                penetrations = []
                for contact_index in range(int(data.ncon)):
                    contact = data.contact[contact_index]
                    if source_geom_start <= contact.geom1 < source_geom_end or source_geom_start <= contact.geom2 < source_geom_end:
                        if contact.dist < -1e-4:
                            penetrations.append(
                                {
                                    "geom1": _geom_name(mujoco, model, contact.geom1),
                                    "geom2": _geom_name(mujoco, model, contact.geom2),
                                    "distanceMeters": float(contact.dist),
                                }
                            )
                if penetrations:
                    raise M20AssistiveSceneError(
                        "{} placement on {} interpenetrates MuJoCo geometry: {}".format(
                            source_id, target_id, penetrations
                        )
                    )
                placement_pose_checks.append(
                    {
                        "sourceId": source_id,
                        "targetId": target_id,
                        "objectBottomWorldZMeters": object_bottom,
                        "targetTopWorldZMeters": target_top,
                        "verticalGapMeters": vertical_gap,
                        "contactInterpenetrations": penetrations,
                    }
                )
                data.qpos[:] = initial_qpos
                mujoco.mj_forward(model, data)
    finally:
        data.qpos[:] = initial_qpos
        mujoco.mj_forward(model, data)

    hinge = next(item for item in spec["articulations"] if item["jointId"] == "assist_storage_lid_hinge")
    hinge_open = math.radians(float(hinge["openDegrees"]))
    _set_joint_qpos(mujoco, model, data, hinge["jointId"], hinge_open)
    mujoco.mj_forward(model, data)
    lid_geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "assist_storage_lid_geom")
    lid_center = data.geom_xpos[lid_geom_id]
    lid_rotation = data.geom_xmat[lid_geom_id].reshape((3, 3))
    lid_size = _vector(entity_map["assist_storage_lid"]["dimensionsMeters"], "lid dimensions")
    min_lid_z = float("inf")
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                corner_local = (sx * lid_size[0] / 2.0, sy * lid_size[1] / 2.0, sz * lid_size[2] / 2.0)
                corner_z = float(lid_center[2]) + sum(
                    float(lid_rotation[2, axis]) * corner_local[axis] for axis in range(3)
                )
                min_lid_z = min(min_lid_z, corner_z)
    if min_lid_z < table_top + 0.070:
        raise M20AssistiveSceneError("open storage lid clips through the base/table")
    open_lid_penetrations = []
    for contact_index in range(int(data.ncon)):
        contact = data.contact[contact_index]
        geom_a = _geom_name(mujoco, model, contact.geom1)
        geom_b = _geom_name(mujoco, model, contact.geom2)
        if "assist_storage_lid" in (geom_a, geom_b) and contact.dist < -1e-4:
            open_lid_penetrations.append(
                {"geom1": geom_a, "geom2": geom_b, "distanceMeters": float(contact.dist)}
            )
    if open_lid_penetrations:
        raise M20AssistiveSceneError("open storage lid interpenetrates another object: {}".format(open_lid_penetrations))
    open_lid_pose = [float(value) for value in lid_center]
    _set_joint_qpos(mujoco, model, data, hinge["jointId"], math.radians(float(hinge["closedDegrees"])))
    mujoco.mj_forward(model, data)

    slide = next(item for item in spec["articulations"] if item["jointId"] == "assist_button_cap_slide")
    cap_geom_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "assist_button_cap_geom")
    _set_joint_qpos(mujoco, model, data, slide["jointId"], float(slide["unpressedMeters"]))
    mujoco.mj_forward(model, data)
    unpressed_cap_center = [float(value) for value in data.geom_xpos[cap_geom_id]]
    _set_joint_qpos(mujoco, model, data, slide["jointId"], float(slide["pressedMeters"]))
    mujoco.mj_forward(model, data)
    pressed_cap_center = [float(value) for value in data.geom_xpos[cap_geom_id]]
    observed_button_travel = abs(unpressed_cap_center[2] - pressed_cap_center[2])
    if abs(observed_button_travel - 0.005) > 1e-6:
        raise M20AssistiveSceneError("compiled button travel is not exactly 5 mm")
    _set_joint_qpos(mujoco, model, data, slide["jointId"], float(slide["unpressedMeters"]))
    _set_joint_qpos(mujoco, model, data, hinge["jointId"], math.radians(float(hinge["closedDegrees"])))
    mujoco.mj_forward(model, data)
    lid_closed_geom_position = [float(value) for value in data.geom_xpos[lid_geom_id]]
    lid_expected = _vector(entity_map["assist_storage_lid"]["positionMeters"], "lid position")
    lid_position_error = (
        abs(lid_closed_geom_position[0] - lid_expected[0]),
        abs(lid_closed_geom_position[1] - lid_expected[1]),
        abs(lid_closed_geom_position[2] - table_top - lid_expected[2]),
    )
    if max(lid_position_error) > tolerance:
        raise M20AssistiveSceneError("closed MuJoCo storage lid pose is outside the canonical tolerance")
    cap_closed_geom_position = [float(value) for value in data.geom_xpos[cap_geom_id]]
    cap_expected = _vector(entity_map["assist_button_cap"]["positionMeters"], "button cap position")
    cap_position_error = (
        abs(cap_closed_geom_position[0] - cap_expected[0]),
        abs(cap_closed_geom_position[1] - cap_expected[1]),
        abs(cap_closed_geom_position[2] - table_top - cap_expected[2]),
    )
    if max(cap_position_error) > tolerance:
        raise M20AssistiveSceneError("unpressed MuJoCo button cap pose is outside the canonical tolerance")

    return {
        "status": "PASS",
        "table": {
            "entityId": spec["table"]["entityId"],
            "positionErrorMeters": list(table_position_error),
            "dimensionErrorMeters": list(table_dimension_error),
            "topSurfaceWorldZMeters": table_top,
        },
        "robotBase": {
            "entityId": spec["robot"]["entityId"],
            "positionErrorMeters": list(robot_position_error),
            "worldPositionMeters": [float(value) for value in robot_actual_world],
        },
        "modelBodyCount": int(model.nbody),
        "modelGeomCount": int(model.ngeom),
        "candidateGeometry": checks,
        "placementPoses": placement_pose_checks,
        "m9GraspReachability": check_m9_grasp_pose_reachability(spec),
        "initialContactCount": initial_contact_count,
        "initialM20ContactInterpenetrations": contact_penetrations,
        "storageLid": {
            "jointId": hinge["jointId"],
            "closedGeomCenterWorldMeters": lid_closed_geom_position,
            "closedPositionErrorMeters": list(lid_position_error),
            "closedDegrees": float(hinge["closedDegrees"]),
            "openDegrees": float(hinge["openDegrees"]),
            "openGeomCenterWorldMeters": open_lid_pose,
            "openLowestPointWorldZMeters": min_lid_z,
            "tableTopWorldZMeters": table_top,
            "clearsTableAndBase": min_lid_z >= table_top + 0.070 and not open_lid_penetrations,
            "openPoseContactInterpenetrations": open_lid_penetrations,
        },
        "buttonCap": {
            "jointId": slide["jointId"],
            "canonicalUnpressedPositionErrorMeters": list(cap_position_error),
            "unpressedCenterWorldMeters": unpressed_cap_center,
            "pressedCenterWorldMeters": pressed_cap_center,
            "travelMeters": observed_button_travel,
            "travelPass": abs(observed_button_travel - 0.005) <= 1e-6,
            "housingApertureMeters": entity_map["assist_button_switch"]["construction"]["apertureMeters"],
        },
    }


def render_mujoco_screenshots(model, data, output_directory: Path) -> dict:
    """Save top-down and perspective screenshots from the compiled MuJoCo model."""
    mujoco = _mujoco_module()
    output_directory.mkdir(parents=True, exist_ok=True)
    saved = {}
    try:
        renderer = mujoco.Renderer(model, height=900, width=1440)
    except Exception as error:
        raise M20AssistiveSceneError("MuJoCo renderer initialization failed: {}".format(error)) from error
    try:
        for view, azimuth, elevation, distance in (
            ("topdown", 90.0, -89.0, 1.25),
            ("perspective", -60.0, -78.0, 2.40),
        ):
            camera = mujoco.MjvCamera()
            mujoco.mjv_defaultCamera(camera)
            camera.type = mujoco.mjtCamera.mjCAMERA_FREE
            camera.lookat[:] = [0.0, 0.0, 0.82]
            camera.distance = distance
            camera.azimuth = azimuth
            camera.elevation = elevation
            renderer.update_scene(data, camera=camera)
            frame = renderer.render()
            path = output_directory / ("mujoco_{}.png".format(view))
            _write_png(path, frame)
            saved[view] = str(path)
    finally:
        renderer.close()
    return saved


def _write_png(path: Path, frame) -> None:
    """Write MuJoCo's RGB byte array as PNG without a separate imaging package."""
    shape = getattr(frame, "shape", None)
    if shape is None or len(shape) != 3 or shape[2] != 3:
        raise M20AssistiveSceneError("MuJoCo renderer returned an unexpected screenshot format")
    height, width, _ = shape
    raw = b"".join(b"\x00" + frame[row].tobytes() for row in range(height))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        contents = kind + payload
        return struct.pack(">I", len(payload)) + contents + struct.pack(">I", zlib.crc32(contents) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, level=6))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def run_scene_checks(
    spec_path: Path | str = DEFAULT_SPEC_PATH,
    *,
    check_mujoco: bool = False,
    render: bool = False,
    output_directory: Path | None = None,
) -> dict:
    spec, fingerprint = load_spec(spec_path)
    static_report = validate_spec(spec)
    result = {
        "recordType": "m20_assistive_desk_geometry_parity",
        "status": "PASS",
        "specPath": str(Path(spec_path).resolve()),
        "specSha256": fingerprint,
        "staticContract": static_report,
        "mujoco": None,
        "screenshots": {},
    }
    if check_mujoco or render:
        model, data = build_mujoco_scene(spec)
        result["mujoco"] = check_mujoco_scene(spec, model, data)
        if render:
            if output_directory is None:
                raise M20AssistiveSceneError("--render requires an output directory")
            result["screenshots"] = render_mujoco_screenshots(model, data, output_directory)
    return result


def _write_report(report: dict, path: Path | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(text, end="")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError("refusing to overwrite existing M20 evidence: {}".format(path))
    path.write_text(text, encoding="utf-8")
    print("M20_REPORT={}".format(path.resolve()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--mujoco-check", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        output_dir = args.output_dir
        if args.render and output_dir is None and args.report is not None:
            output_dir = args.report.parent
        report = run_scene_checks(
            args.spec,
            check_mujoco=args.mujoco_check,
            render=args.render,
            output_directory=output_dir,
        )
        _write_report(report, args.report)
        return 0
    except Exception as error:
        print("M20_ASSISTIVE_SCENE_FAIL={}: {}".format(type(error).__name__, error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
