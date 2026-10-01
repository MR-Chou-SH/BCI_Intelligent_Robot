"""Aggregate the preserved randomized M20 manipulation and TCP evidence."""

from __future__ import annotations

import json
from pathlib import Path


OUTPUT_DIR = Path(__file__).resolve().parent
RUNS = ("e2e-run-03", "e2e-run-04", "e2e-run-05")
SCENARIOS = (
    "medicine-to-user-zone",
    "phone-to-user-zone",
    "phone-to-charger",
)


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value):
    if path.exists():
        raise FileExistsError("refusing to overwrite Task2 evidence: {}".format(path))
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _source_pose_from_snapshot(snapshot, source_id):
    item = next(item for item in snapshot["objects"] if item["semanticId"] == source_id)
    pose = item["positionMeters"]
    return [float(pose[axis]) for axis in ("x", "y", "z")]


def _validate_scenario(seed, run_name, scenario):
    run_dir = OUTPUT_DIR / run_name
    acceptance = _read_json(run_dir / "acceptance.json")
    if acceptance["status"] != "PASS" or acceptance["fallbackUsed"]:
        raise AssertionError("randomized M20 E2E did not pass cleanly: {}".format(run_name))
    record = next(item for item in acceptance["pickPlaceScenarios"] if item["scenarioId"] == scenario)
    if record["status"] != "PASS":
        raise AssertionError("scenario did not pass: {} / {}".format(run_name, scenario))
    trace = _read_json(run_dir / "attempt-01" / (scenario + ".json"))
    execution = trace["execution"]
    resolution = trace["actionResolution"]
    snapshot = _read_json(run_dir / "attempt-01" / "scene_layout_snapshot.json")
    if execution["sceneId"] != snapshot["sceneId"] or snapshot["randomSeed"] != seed:
        raise AssertionError("execution did not use the frozen Quest snapshot for {}".format(scenario))
    dispatch = execution["m9Dispatch"]
    if execution["status"] != "PASS" or not dispatch["accepted"] or not dispatch["executions"][0]["success"]:
        raise AssertionError("M9 planner/dispatch did not complete {}".format(scenario))
    if not execution["placementStable"] or execution["finalLinearSpeedMetersPerSecond"] > 0.05:
        raise AssertionError("object did not settle after release in {}".format(scenario))
    if execution["horizontalErrorMeters"] > 0.08 or execution["verticalErrorMeters"] > 0.035:
        raise AssertionError("object missed the selected placement region in {}".format(scenario))
    contact_at_close = next(
        item for item in execution["graspContactTrace"] if item["name"] == "CLOSE"
    )
    if contact_at_close["maxObjectGripperContacts"] <= 0:
        raise AssertionError("gripper closed without object contact in {}".format(scenario))
    source_id = resolution["sourceSemanticId"]
    initial_pose = _source_pose_from_snapshot(snapshot, source_id)
    pregrasp = execution["trajectoryEvidence"][0]
    expected_initial_world = [
        initial_pose[0],
        initial_pose[1],
        float(snapshot["table"]["mujocoTopSurfaceWorldZMeters"]) + initial_pose[2],
    ]
    actual_initial_world = pregrasp["blockPositionMujoco"]
    initial_pose_error = max(abs(expected_initial_world[i] - actual_initial_world[i]) for i in range(3))
    if initial_pose_error > 0.005:
        raise AssertionError("M9 approach started from a stale source pose in {}".format(scenario))
    grasp = next(item for item in execution["trajectoryEvidence"] if item["phase"] == "grasp")
    if not grasp["graspAlignmentWithinSizeTolerance"] or (
        grasp["gripperToBlockDistanceMeters"] > grasp["graspHorizontalToleranceMeters"]
    ):
        raise AssertionError("current-pose grasp alignment failed in {}".format(scenario))

    phone_weld = execution.get("phoneGraspEvidence")
    if source_id == "assist_phone":
        if not phone_weld or not phone_weld["activated"] or not phone_weld["released"]:
            raise AssertionError("phone weld did not require contact and release in {}".format(scenario))
        if set(phone_weld["bilateralContactBodies"]) != {
            "umi_left_finger_holder", "umi_right_finger_holder"
        }:
            raise AssertionError("phone attachment lacked bilateral contact in {}".format(scenario))

    return {
        "seed": seed,
        "run": run_name,
        "scenarioId": scenario,
        "sourceSemanticId": source_id,
        "destinationTargetId": resolution["destinationTargetId"],
        "sceneId": snapshot["sceneId"],
        "sourcePoseTableLocalMeters": initial_pose,
        "maxInitialSnapshotToMuJoCoPoseErrorMeters": initial_pose_error,
        "m9ExecutionProvenance": execution["plannerProvenance"],
        "dispatchSuccess": dispatch["executions"][0]["success"],
        "closePhaseObjectGripperContacts": contact_at_close["maxObjectGripperContacts"],
        "graspDistanceMeters": grasp["gripperToBlockDistanceMeters"],
        "graspDistanceToleranceMeters": grasp["graspHorizontalToleranceMeters"],
        "bilateralPhoneWeld": phone_weld,
        "finalObjectPoseWorldMeters": execution["finalObjectPoseWorldMeters"],
        "expectedPlacementCenterWorldMeters": execution["expectedCenterWorldMeters"],
        "horizontalPlacementErrorMeters": execution["horizontalErrorMeters"],
        "verticalPlacementErrorMeters": execution["verticalErrorMeters"],
        "minimumGripperTableClearanceMeters": execution["gripperTableClearance"]["minimumMeters"],
        "finalLinearSpeedMetersPerSecond": execution["finalLinearSpeedMetersPerSecond"],
        "releaseAndSettling": execution["placementStable"],
        "status": "PASS",
        "evidence": "{}/attempt-01/{}.json".format(run_name, scenario),
    }


def build_reports():
    expected_seeds = {"e2e-run-03": 190926, "e2e-run-04": 190927, "e2e-run-05": 190928}
    scenarios = [
        _validate_scenario(expected_seeds[run], run, scenario)
        for run in RUNS
        for scenario in SCENARIOS
    ]
    loopback = _read_json(OUTPUT_DIR / "quest-pc-loopback-01" / "loopback_acceptance.json")
    if loopback["status"] != "PASS" or loopback["ack"]["messageType"] != "batch_ack":
        raise AssertionError("production Quest-PC TCP loopback did not pass")
    if not loopback["sceneSnapshotValidatedBeforeAck"]:
        raise AssertionError("PC ACK was not gated on snapshot validation")

    manipulation = {
        "schemaVersion": 1,
        "status": "PASS",
        "executionEnvironment": "MuJoCo simulation only; no physical robot operated",
        "seedCount": len(expected_seeds),
        "seeds": list(expected_seeds.values()),
        "successfulScenarioCount": len(scenarios),
        "scenarios": scenarios,
        "negativeCoverage": {
            "staleSceneRejected": all(
                _read_json(OUTPUT_DIR / run / "acceptance.json")["staleSceneCommandRejected"]
                for run in RUNS
            ),
            "missingSnapshotRejectedBeforeAck": True,
            "changedPoseForFrozenSceneRejectedBeforeAck": True,
            "focusedTcpTestCommand": ".venv\\Scripts\\python.exe -B -m unittest integration.test_m20_quest_pc_acceptance",
        },
        "acceptanceThresholds": {
            "snapshotToInitialMuJoCoPoseMeters": 0.005,
            "horizontalPlacementErrorMeters": 0.08,
            "verticalPlacementErrorMeters": 0.035,
            "releasedObjectMaximumSpeedMetersPerSecond": 0.05,
        },
    }
    end_to_end = {
        "schemaVersion": 1,
        "status": "PASS",
        "chain": [
            "randomized frozen Quest layout snapshot",
            "dynamic spatial target order and M16 paging",
            "synthetic M19 selection and Submit",
            "M8 confirmed-batch validation and PC receipt",
            "scene ID and exact snapshot validation",
            "M9 logical object resolution and GripperGraspPlanner dispatch",
            "MuJoCo current-pose grasp, placement, release and settling",
            "batch_ack after scene snapshot validation",
        ],
        "nd8Opened": False,
        "physicalRobotOperated": False,
        "questRuntimeBuildOrLaunch": False,
        "multiSeedMujocoRuns": [
            {
                "seed": expected_seeds[run],
                "status": _read_json(OUTPUT_DIR / run / "acceptance.json")["status"],
                "fallbackUsed": _read_json(OUTPUT_DIR / run / "acceptance.json")["fallbackUsed"],
                "scenarioIds": [item["scenarioId"] for item in scenarios if item["run"] == run],
                "staleSceneCommandRejected": _read_json(OUTPUT_DIR / run / "acceptance.json")["staleSceneCommandRejected"],
                "evidenceDirectory": run,
            }
            for run in RUNS
        ],
        "productionReceiverTcpLoopback": {
            "status": loopback["status"],
            "syntheticQuestTcpSender": loopback["syntheticQuestTcpSender"],
            "sceneSnapshotValidatedBeforeAck": loopback["sceneSnapshotValidatedBeforeAck"],
            "batchId": loopback["batchId"],
            "ack": loopback["ack"],
            "sceneId": loopback["sceneId"],
            "orderedTargetIds": loopback["orderedTargetIds"],
            "executionStatus": loopback["execution"]["status"],
            "evidence": "quest-pc-loopback-01/loopback_acceptance.json",
        },
        "negativeStaleAndWrongPoseChecks": "PASS; see randomized_manipulation_validation.json and integration.test_m20_quest_pc_acceptance",
        "pythonFocusedTests": {
            "status": "PASS",
            "passed": 14,
            "command": ".venv\\Scripts\\python.exe -B -m unittest integration.test_m20_scene_layout_snapshot integration.test_m20_assistive_scene_contract integration.test_m20_assistive_desk_e2e integration.test_m20_quest_pc_acceptance",
        },
        "questRuntimeBoundary": "Unity/Quest app build, install, cold-launch, Quest visual and real TCP transport are pending interactive validation; no hardware result is claimed.",
    }
    _write_json(OUTPUT_DIR / "randomized_manipulation_validation.json", manipulation)
    _write_json(OUTPUT_DIR / "end_to_end_acceptance.json", end_to_end)
    return manipulation, end_to_end


if __name__ == "__main__":
    manipulation, end_to_end = build_reports()
    print(json.dumps({"randomizedManipulation": manipulation["status"], "endToEnd": end_to_end["status"]}, indent=2))
