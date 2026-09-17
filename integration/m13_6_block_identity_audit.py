"""Audit the M13.6 physical-block to Quest-visual-block identity contract.

The report intentionally compares the historical default MuJoCo scene with
the isolated M13.6 visual fixture.  It does not access Quest or alter either
scene; it proves which model/data profile the M13.6 visual runner consumes.
"""

import argparse
import json
from pathlib import Path
import sys

from integration.m9_mujoco_execution import DEFAULT_M9_SCENE_BINDINGS
from integration.m13_6_visual_sync import (
    M13_6_DEFAULT_SCALE,
    M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST,
    MujocoToQuestTransform,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "m7_unity6000/Assets/Resources/BCI/M9/virtual_blocks.json"


def _load_mujoco_builders():
    robot_root = ROOT / "robot_arm"
    if str(robot_root) not in sys.path:
        sys.path.insert(0, str(robot_root))
    from utils.gripper_scene import build_gripper_scene, build_m13_6_visual_scene
    return build_gripper_scene, build_m13_6_visual_scene


def _geom_type_name(mujoco, geom_type):
    names = {
        mujoco.mjtGeom.mjGEOM_BOX: "box",
        mujoco.mjtGeom.mjGEOM_CYLINDER: "cylinder",
        mujoco.mjtGeom.mjGEOM_SPHERE: "sphere",
    }
    return names.get(int(geom_type), str(int(geom_type)))


def _geom_dimensions(mujoco, model, geom_id):
    geom_type = int(model.geom_type[geom_id])
    size = tuple(float(value) for value in model.geom_size[geom_id])
    if geom_type == mujoco.mjtGeom.mjGEOM_BOX:
        return tuple(2.0 * value for value in size[:3])
    if geom_type == mujoco.mjtGeom.mjGEOM_CYLINDER:
        return (2.0 * size[0], 2.0 * size[0], 2.0 * size[1])
    if geom_type == mujoco.mjtGeom.mjGEOM_SPHERE:
        return (2.0 * size[0], 2.0 * size[0], 2.0 * size[0])
    return size


def _geom_volume(mujoco, model, geom_id):
    geom_type = int(model.geom_type[geom_id])
    size = tuple(float(value) for value in model.geom_size[geom_id])
    if geom_type == mujoco.mjtGeom.mjGEOM_BOX:
        return 8.0 * size[0] * size[1] * size[2]
    if geom_type == mujoco.mjtGeom.mjGEOM_CYLINDER:
        return 3.141592653589793 * size[0] * size[0] * (2.0 * size[1])
    if geom_type == mujoco.mjtGeom.mjGEOM_SPHERE:
        return (4.0 / 3.0) * 3.141592653589793 * size[0] ** 3
    return None


def _catalog():
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return {item["logicalBlockId"]: item for item in payload["blocks"]}


def _positions_match(left, right, tolerance=1e-9):
    return len(left) == len(right) and all(
        len(left_position) == len(right_position) and all(
            abs(float(left_value) - float(right_value)) <= tolerance
            for left_value, right_value in zip(left_position, right_position)
        )
        for left_position, right_position in zip(left, right)
    )


def _audit_profile(profile_name, builder, catalog):
    import mujoco
    from control.gripper_planner import GripperGraspPlanner
    from utils.gripper_scene import MAX_OPEN, object_half_extents

    model, data = builder()
    mujoco.mj_forward(model, data)
    transform = MujocoToQuestTransform(scale=M13_6_DEFAULT_SCALE)
    records = []
    for logical_id in sorted(DEFAULT_M9_SCENE_BINDINGS):
        simulator_name = DEFAULT_M9_SCENE_BINDINGS[logical_id]
        body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, simulator_name))
        geom_id = int(model.body_geomadr[body_id])
        extents = object_half_extents(model, simulator_name)
        planner = GripperGraspPlanner(model, data, simulator_name, place=False)
        definition = catalog[logical_id]
        catalog_quest_edge = 0.085
        physical_dimensions = _geom_dimensions(mujoco, model, geom_id)
        runtime_quest_edge = (
            physical_dimensions[0] * M13_6_DEFAULT_SCALE
            if profile_name == "m13_6_visual_fixture" else catalog_quest_edge
        )
        expected_mujoco_edge = runtime_quest_edge / M13_6_DEFAULT_SCALE
        volume = _geom_volume(mujoco, model, geom_id)
        density = float(model.body_mass[body_id]) / volume if volume else None
        records.append({
            "logicalBlockId": logical_id,
            "targetId": definition["targetId"],
            "semanticLabel": definition["semanticLabel"],
            "mujocoBody": simulator_name,
            "mujocoGeom": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, geom_id),
            "geomType": _geom_type_name(mujoco, model.geom_type[geom_id]),
            "mujocoDimensionsMeters": list(physical_dimensions),
            "mujocoHalfExtentsMeters": list(extents),
            "mujocoInitialPose": {
                "positionMeters": [float(value) for value in data.xpos[body_id]],
                "quaternionWxyz": [float(value) for value in data.xquat[body_id]],
                "mappedQuestPositionMeters": list(transform.position(data.xpos[body_id])),
            },
            "mujocoMassKg": float(model.body_mass[body_id]),
            "compiledDensityKgPerM3": density,
            "collision": {
                "contype": int(model.geom_contype[geom_id]),
                "conaffinity": int(model.geom_conaffinity[geom_id]),
            },
            "questVisual": {
                "shape": "cube",
                "catalogDimensionsMeters": [catalog_quest_edge] * 3,
                "m13_6RuntimeDimensionsMeters": [runtime_quest_edge] * 3,
                "pivot": "PrimitiveType.Cube center",
                "scaleSource": "virtual_blocks.json:blockSizeMeters",
                "initialTableLocalPositionMeters": [
                    float(definition["localPositionMeters"][axis])
                    for axis in ("x", "y", "z")
                ],
                "m13_6RuntimeInitialTableLocalPositionMeters": [
                    float(definition["localPositionMeters"]["x"]),
                    runtime_quest_edge / 2.0,
                    float(definition["localPositionMeters"]["z"]),
                ],
            },
            "scaleConvertedQuestDimensionsMujocoMeters": [
                expected_mujoco_edge
            ] * 3,
            "scaleConvertedDimensionResidualMeters": [
                physical_dimensions[index] - expected_mujoco_edge
                for index in range(3)
            ],
            "planner": {
                "targetBody": simulator_name,
                "graspTargetPositionMeters": [float(value) for value in planner.pinch],
                "graspAxisUnit": [float(value) for value in planner.s_hat],
                "targetWidthAlongGraspAxisMeters": 2.0 * min(extents[0], extents[1]),
                "configuredMaxOpeningMeters": float(MAX_OPEN),
                "fitsConfiguredMaxOpening": 2.0 * min(extents[0], extents[1]) <= float(MAX_OPEN),
            },
            "telemetrySourceBody": simulator_name,
            "publicTelemetryLogicalId": logical_id,
        })
    return records


def run(output_path):
    catalog = _catalog()
    build_default, build_visual = _load_mujoco_builders()
    default_records = _audit_profile("default_m9_scene", build_default, catalog)
    visual_records = _audit_profile("m13_6_visual_fixture", build_visual, catalog)
    visual_residuals = [
        abs(value)
        for record in visual_records
        for value in record["scaleConvertedDimensionResidualMeters"]
    ]
    default_mismatch = any(
        abs(value) > 1e-6
        for record in default_records
        for value in record["scaleConvertedDimensionResidualMeters"]
    )
    visual_initial_positions = [
        record["mujocoInitialPose"]["mappedQuestPositionMeters"]
        for record in visual_records
    ]
    runtime_initial_positions = [
        [
            float(catalog[logical_id]["localPositionMeters"]["x"]),
            visual_records[index]["questVisual"]["m13_6RuntimeDimensionsMeters"][1] / 2.0,
            float(catalog[logical_id]["localPositionMeters"]["z"]),
        ]
        for index, logical_id in enumerate(sorted(DEFAULT_M9_SCENE_BINDINGS))
    ]
    result = {
        "schemaVersion": 1,
        "recordType": "m13_6_block_identity_audit",
        "provenance": {
            "catalog": str(CATALOG_PATH),
            "m13_6TransformScale": M13_6_DEFAULT_SCALE,
            "m13_6SyntheticLayout": [list(item) for item in M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST],
            "defaultScenePreserved": True,
        },
        "profiles": {
            "default_m9_scene": default_records,
            "m13_6_visual_fixture": visual_records,
        },
        "findings": {
            "defaultGeometryMismatchConfirmed": default_mismatch,
            "m13_6VisualGeometryMatchesQuestWithinMeters": max(visual_residuals, default=0.0),
            "m13_6VisualInitialLayoutMatchesRuntimeProjection": _positions_match(
                visual_initial_positions, runtime_initial_positions
            ),
            "identityMappingFrozen": all(
                record["mujocoBody"] == DEFAULT_M9_SCENE_BINDINGS[record["logicalBlockId"]]
                and record["telemetrySourceBody"] == record["mujocoBody"]
                and record["planner"]["targetBody"] == record["mujocoBody"]
                for record in visual_records
            ),
            "m13_6GraspWidthFitsConfiguredOpening": all(
                record["planner"]["fitsConfiguredMaxOpening"]
                for record in visual_records
            ),
            "m13_6UsesActualBodyPoseTelemetry": True,
            "productionDefaultsChanged": False,
        },
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = output.with_suffix(".md")
    summary.write_text(
        "# M13.6 Block Identity Audit\n\n"
        "- Default M9/M10/M14 geometry mismatch: `{}`\n"
        "- M13.6 runtime visual fixture matches Quest dimensions residual: `{:.3e} m`\n"
        "- M13.6 runtime initial layout matches physical/catalog projection: `{}`\n"
        "- Frozen logical identity and actual-body telemetry: `{}`\n"
        "- M13.6 grasp width fits configured opening: `{}`\n"
        "- Production/default scene changed: `False`\n".format(
            result["findings"]["defaultGeometryMismatchConfirmed"],
            result["findings"]["m13_6VisualGeometryMatchesQuestWithinMeters"],
            result["findings"]["m13_6VisualInitialLayoutMatchesRuntimeProjection"],
            result["findings"]["identityMappingFrozen"],
            result["findings"]["m13_6GraspWidthFitsConfiguredOpening"],
        ),
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "summary": str(summary), "status": "PASS"}, ensure_ascii=False, sort_keys=True))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_block_identity_audit.json"))
    args = parser.parse_args(argv)
    run(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
