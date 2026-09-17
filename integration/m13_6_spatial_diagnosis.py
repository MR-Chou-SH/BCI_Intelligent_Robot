"""Read-only M13.6 spatial diagnosis for the Quest visual seam.

This tool does not stream link poses or change the planner. It reuses the
M13.6 single-run loader and captures five planner phases from the same
MuJoCo model/data. The Unity-side gripper estimate is the current serialized
FrankaRoot anchor plus the source robot-base-to-gripper FK displacement under
the same table transform.
"""

import argparse
import json
import math
from pathlib import Path
import re
from statistics import mean, median, pstdev

from integration.m13_6_visual_sync import (
    DEFAULT_M9_SCENE_BINDINGS,
    M13_6_VISUAL_PLACE_REGION_HALF_SIZE,
    M13_6_VISUAL_STACK_TARGETS,
    MujocoToQuestTransform,
    m13_6_quest_catalog_anchor_positions_mujoco,
    m13_6_visual_runtime_anchor_positions_mujoco,
    _load_mujoco_planner,
    _m13_6_prepare_stack_collision_categories,
    _m13_6_set_object_collision_category,
)


ROOT = Path(__file__).resolve().parents[1]
UNITY_FACTORY = ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M9FrankaVisualFactory.cs"
UNITY_BOOTSTRAP = ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M9VirtualManipulationBootstrap.cs"
UNITY_CATALOG = ROOT / "m7_unity6000/Assets/Resources/BCI/M9/virtual_blocks.json"


def _source_float(source, name):
    match = re.search(r"(?:const\s+float|VisualScale\s*=)\s+" + re.escape(name) + r"\s*=\s*([0-9.]+)f?", source)
    if match is None:
        raise RuntimeError("could not read Unity source constant {}".format(name))
    return float(match.group(1))


def _unity_reference_from_source():
    factory = UNITY_FACTORY.read_text(encoding="utf-8")
    bootstrap = UNITY_BOOTSTRAP.read_text(encoding="utf-8")
    visual_scale = _source_float(factory, "VisualScale")
    robot_base_y = _source_float(bootstrap, "M13_6RobotBaseYMeters")
    robot_base_z = _source_float(bootstrap, "M13_6RobotBaseZMeters")
    table_top_z = _source_float(bootstrap, "M13_6TableTopMujocoZMeters")
    robot_anchor = (
        0.0,
        visual_scale * (robot_base_z - table_top_z),
        -visual_scale * robot_base_y,
    )
    return {
        "visualScale": visual_scale,
        "robotBaseMujoco": (0.0, robot_base_y, robot_base_z),
        "tableTopMujocoZ": table_top_z,
        "robotAnchorTableLocal": robot_anchor,
        "factoryRootBasis": "Quaternion.Euler(-90f, 0f, 0f)",
        "factoryRootLocalPosition": (0.0, 0.0, 0.0),
    }


def _body_id(mujoco, model, simulator_name):
    body_id = int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, simulator_name))
    if body_id < 0:
        raise RuntimeError("missing MuJoCo body {}".format(simulator_name))
    return body_id


def _mapped_delta(transform, left, right):
    return tuple(a - b for a, b in zip(transform.position(left), transform.position(right)))


def _capture_pose(label, phase, mujoco, model, data, transform, anchor, body_ids):
    base_position = tuple(float(value) for value in data.xpos[body_ids["base"]])
    gripper_position = tuple(float(value) for value in data.xpos[body_ids["gripper"]])
    mapped_gripper = transform.position(gripper_position)
    mapped_base = transform.position(base_position)
    unity_gripper = tuple(anchor[index] + mapped_gripper[index] - mapped_base[index] for index in range(3))
    delta = tuple(mapped_gripper[index] - unity_gripper[index] for index in range(3))
    return {
        "label": label,
        "plannerPhase": phase,
        "simulationTimeSeconds": float(data.time),
        "jointPositionsRadians": [float(value) for value in data.qpos[:7]],
        "mujocoBasePosition": list(base_position),
        "mujocoGripperPosition": list(gripper_position),
        "mappedMujocoGripperWorldTable": list(mapped_gripper),
        "unityHierarchyGripperEstimate": list(unity_gripper),
        "deltaMuJoCoMinusUnity": list(delta),
        "deltaMagnitudeMeters": math.sqrt(sum(value * value for value in delta)),
    }


def _pairwise(points):
    result = {}
    ids = tuple(points)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            delta = tuple(points[right_id][axis] - points[left_id][axis] for axis in range(3))
            result[left_id + "+" + right_id] = {
                "delta": list(delta),
                "distanceMeters": math.sqrt(sum(value * value for value in delta)),
            }
    return result


def run(output_path):
    source_reference = _unity_reference_from_source()
    transform = MujocoToQuestTransform(scale=source_reference["visualScale"])
    anchor = source_reference["robotAnchorTableLocal"]
    target = M13_6_VISUAL_STACK_TARGETS["block_sim_01"]
    mujoco, model, data, planner = _load_mujoco_planner(
        "block_sim_01",
        True,
        place_center=target["position"][:2],
        place_region_center=target["position"][:2],
        place_region_half_size=M13_6_VISUAL_PLACE_REGION_HALF_SIZE,
        place_target_position=target["position"],
        place_region_z_tolerance=0.035,
        drop_clear=0.0,
        m13_6_visual_mode=True,
    )
    mujoco.mj_forward(model, data)
    body_ids = {
        "base": _body_id(mujoco, model, "base"),
        "gripper": _body_id(mujoco, model, "umi_umi_gripper_base"),
    }
    block_ids = {
        logical_id: _body_id(mujoco, model, simulator_name)
        for logical_id, simulator_name in DEFAULT_M9_SCENE_BINDINGS.items()
    }
    first_block_positions_mujoco = {
        logical_id: tuple(float(value) for value in data.xpos[body_id])
        for logical_id, body_id in block_ids.items()
    }
    _m13_6_prepare_stack_collision_categories(mujoco, model, DEFAULT_M9_SCENE_BINDINGS)
    _m13_6_set_object_collision_category(mujoco, model, "block_sim_01", 1)

    captures = {"home": _capture_pose("home", planner.phase, mujoco, model, data, transform, anchor, body_ids)}
    phase_labels = {
        "APPROACH": "approach",
        "SETTLE": "grasp",
        "LIFT": "lift",
        "PLACE": "transport",
    }
    steps = 0
    while planner.phase != "DONE" and steps < 9000:
        label = phase_labels.get(planner.phase)
        if label is not None and label not in captures:
            captures[label] = _capture_pose(label, planner.phase, mujoco, model, data, transform, anchor, body_ids)
        active = planner.step()
        if not active:
            break
        mujoco.mj_step(model, data)
        steps += 1
    for label, phase in phase_labels.items():
        output_label = phase
        if output_label not in captures and planner.phase == "DONE":
            captures[output_label] = _capture_pose(output_label, planner.phase, mujoco, model, data, transform, anchor, body_ids)

    ordered = [captures[label] for label in ("home", "approach", "grasp", "lift", "transport") if label in captures]
    deltas = [item["deltaMuJoCoMinusUnity"] for item in ordered]
    median_delta = [median(values) for values in zip(*deltas)] if deltas else [None, None, None]
    residuals = [
        math.sqrt(sum((delta[axis] - median_delta[axis]) ** 2 for axis in range(3)))
        for delta in deltas
    ]

    catalog = json.loads(UNITY_CATALOG.read_text(encoding="utf-8"))
    catalog_points = {
        item["logicalBlockId"]: [
            item["localPositionMeters"]["x"],
            item["localPositionMeters"]["y"],
            item["localPositionMeters"]["z"],
        ]
        for item in catalog["blocks"]
    }
    mujoco_first_raw = {
        logical_id: list(transform.position(position))
        for logical_id, position in first_block_positions_mujoco.items()
    }
    visual_anchor_positions_mujoco = m13_6_visual_runtime_anchor_positions_mujoco()
    mujoco_first_visual = {
        logical_id: list(transform.position(position))
        for logical_id, position in visual_anchor_positions_mujoco.items()
    }
    block_delta = {
        logical_id: [mujoco_first_visual[logical_id][axis] - catalog_points[logical_id][axis] for axis in range(3)]
        for logical_id in catalog_points
    }
    runtime_projection_points = {
        logical_id: list(transform.position(position))
        for logical_id, position in visual_anchor_positions_mujoco.items()
    }
    runtime_delta = {
        logical_id: [mujoco_first_visual[logical_id][axis] - runtime_projection_points[logical_id][axis] for axis in range(3)]
        for logical_id in catalog_points
    }
    result = {
        "source": {
            "unityFactory": str(UNITY_FACTORY),
            "unityBootstrap": str(UNITY_BOOTSTRAP),
            "unityCatalog": str(UNITY_CATALOG),
            **source_reference,
        },
        "simulation": {
            "sameM13_6Loader": True,
            "plannerResult": planner.summary(),
            "steps": steps,
            "modelTimestepSeconds": float(model.opt.timestep),
        },
        "gripperFrames": ordered,
        "fixedTranslationAnalysis": {
            "frameCount": len(ordered),
            "meanDelta": [mean(values) for values in zip(*deltas)] if deltas else [None, None, None],
            "medianDelta": median_delta,
            "deltaPopulationStd": [pstdev(values) for values in zip(*deltas)] if deltas else [None, None, None],
            "maxResidualAfterMedianMeters": max(residuals, default=None),
            "residualsMeters": residuals,
            "fixedTranslationHypothesis": bool(residuals) and max(residuals) < 1e-6,
        },
        "blocks": {
            "mujocoFirstRawMappedTablePositions": mujoco_first_raw,
            "mujocoFirstVisualMappedTablePositions": mujoco_first_visual,
            "questCatalogExpectedPositions": catalog_points,
            "m13_6RuntimeVisualExpectedPositions": runtime_projection_points,
            "mujocoFirstMinusQuestCatalog": block_delta,
            "mujocoFirstMinusM13_6RuntimeProjection": runtime_delta,
            "mujocoFirstVisualPairwise": _pairwise(mujoco_first_visual),
            "questCatalogPairwise": _pairwise(catalog_points),
            "startupToFirstTelemetryJumpMeters": {
                logical_id: math.sqrt(sum(
                    (mujoco_first_visual[logical_id][axis] - runtime_projection_points[logical_id][axis]) ** 2
                    for axis in range(3)
                ))
                for logical_id in catalog_points
            },
            "startupVisualUsesCatalogUntilTelemetry": True,
            "telemetryApplyPath": "M13_6VisualSyncReceiver.MujocoPosition -> Table.TransformPoint",
        },
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "status": "PASS", "frames": len(ordered)}, ensure_ascii=False, sort_keys=True))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_spatial_diagnosis.json"))
    args = parser.parse_args(argv)
    run(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
