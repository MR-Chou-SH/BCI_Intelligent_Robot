"""Run the M20 randomized-layout, fixed-seed, and MuJoCo pose-parity audit."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import mujoco

from integration.m16_paged_queue import Candidate, PagedSelectionQueue
from integration.m20_assistive_scene_contract import (
    DEFAULT_SPEC_PATH,
    EXPECTED_CANDIDATE_ORDER,
    build_mujoco_scene,
    check_mujoco_scene,
    load_spec,
    spatial_candidate_order,
    validate_spec,
)
from integration.m20_scene_layout_snapshot import (
    SCENE_TEMPLATE_ID,
    apply_scene_layout_snapshot,
    create_scene_layout_snapshot,
    serialize_scene_layout_snapshot,
)


ROOT = Path(__file__).resolve().parents[5]
OUTPUT_DIR = Path(__file__).resolve().parent
SEEDS = tuple(range(20261002, 20261022))
POSE_SYNC_TOLERANCE_METERS = 0.005
CREATED_UTC = "2026-10-02T00:00:00Z"
FREQUENCIES_HZ = (7.2, 9.0, 12.0)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _entity_map(spec):
    return {item["semanticId"]: item for item in spec["entities"]}


def _write_csv(path: Path, rows: list[dict]) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite Task2 evidence: {}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: dict) -> None:
    if path.exists():
        raise FileExistsError("refusing to overwrite Task2 evidence: {}".format(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run() -> dict:
    canonical, canonical_sha256 = load_spec(DEFAULT_SPEC_PATH)
    canonical_entities = _entity_map(canonical)
    canonical_snapshot, canonical_runtime = create_scene_layout_snapshot(
        canonical,
        seed=20261001,
        scene_id="m20-canonical-control",
        created_utc=CREATED_UTC,
    )
    del canonical_snapshot
    records = []
    pose_rows = []
    layout_hashes = set()
    candidate_positions_by_seed = {}

    for seed in SEEDS:
        scene_id = "m20-random-validation-{}".format(seed)
        snapshot, runtime_spec = create_scene_layout_snapshot(
            canonical, seed=seed, scene_id=scene_id, created_utc=CREATED_UTC
        )
        replay, replay_spec = create_scene_layout_snapshot(
            canonical, seed=seed, scene_id=scene_id, created_utc=CREATED_UTC
        )
        snapshot_json = serialize_scene_layout_snapshot(snapshot)
        same_seed_exact = snapshot_json == serialize_scene_layout_snapshot(replay)
        if not same_seed_exact:
            raise AssertionError("same seed did not reproduce the exact snapshot: {}".format(seed))

        validation = validate_spec(runtime_spec)
        model, data = build_mujoco_scene(runtime_spec)
        mujoco_report = check_mujoco_scene(runtime_spec, model, data)
        entities = _entity_map(runtime_spec)
        canonical_order = spatial_candidate_order(runtime_spec)
        if validation["status"] != "PASS" or mujoco_report["status"] != "PASS":
            raise AssertionError("scene validation failed for seed {}".format(seed))
        if set(canonical_order) != set(EXPECTED_CANDIDATE_ORDER):
            raise AssertionError("unexpected candidate semantic ID set for seed {}".format(seed))
        if runtime_spec["candidateOrderFarToNearLeftToRight"] != replay_spec["candidateOrderFarToNearLeftToRight"]:
            raise AssertionError("same-seed spatial order changed for seed {}".format(seed))
        if list(canonical_order) != snapshot["candidateOrderFarToNearLeftToRight"]:
            raise AssertionError("snapshot spatial order differs from runtime scene for seed {}".format(seed))

        user_zone = entities["assist_user_zone"]["positionMeters"]
        canonical_zone = canonical_entities["assist_user_zone"]["positionMeters"]
        user_zone_fixed = user_zone == canonical_zone
        if not user_zone_fixed:
            raise AssertionError("USER ZONE moved in seed {}".format(seed))
        randomized_ids = (
            "assist_medicine_box", "assist_storage_box", "assist_phone",
            "assist_button_switch", "assist_wireless_charger",
        )
        moved_ids = [
            semantic_id for semantic_id in randomized_ids
            if entities[semantic_id]["positionMeters"] != canonical_entities[semantic_id]["positionMeters"]
        ]
        if len(moved_ids) != len(randomized_ids):
            raise AssertionError("a selectable desktop object kept its baseline pose in seed {}".format(seed))

        candidates = tuple(
            Candidate(item["logicalBlockId"], item["targetId"], item["displayLabel"])
            for semantic_id in runtime_spec["candidateOrderFarToNearLeftToRight"]
            for item in (entities[semantic_id],)
        )
        queue = PagedSelectionQueue(candidates)
        before_queue = snapshot_json
        page_count_before = queue.page_count
        queue.select_slot(0)
        if queue.page_count > 1:
            if not queue.navigate_next() or not queue.navigate_previous():
                raise AssertionError("paging failed in seed {}".format(seed))
        submitted = queue.submit()
        if not submitted.accepted or submitted.plan is None or queue.page_count != page_count_before:
            raise AssertionError("Submit/paging contract failed in seed {}".format(seed))
        if before_queue != serialize_scene_layout_snapshot(snapshot):
            raise AssertionError("queue interaction mutated the frozen layout in seed {}".format(seed))
        actual_slots = [
            {"slotIndex": slot, "frequencyHz": frequency}
            for slot, frequency in enumerate(FREQUENCIES_HZ)
        ]
        if [(row["slotIndex"], row["frequencyHz"]) for row in actual_slots] != list(enumerate(FREQUENCIES_HZ)):
            raise AssertionError("slot-frequency mapping changed")

        reachability = mujoco_report["m9GraspReachability"]
        if {item["sourceId"] for item in reachability} != {"assist_medicine_box", "assist_phone"}:
            raise AssertionError("reachability coverage changed in seed {}".format(seed))
        if not all(item["ikReachable"] for item in reachability):
            raise AssertionError("M9 IK could not reach a graspable item in seed {}".format(seed))

        layout_projection = {
            semantic_id: entities[semantic_id]["positionMeters"]
            for semantic_id in randomized_ids
        }
        layout_hash = sha256_text(json.dumps(layout_projection, sort_keys=True, separators=(",", ":")))
        layout_hashes.add(layout_hash)
        candidate_positions_by_seed[seed] = layout_projection

        object_by_semantic_id = {row["semanticId"]: row for row in snapshot["objects"]}
        for semantic_id in EXPECTED_CANDIDATE_ORDER:
            entity = entities[semantic_id]
            expected_local = entity["positionMeters"]
            expected_world = (
                float(expected_local["x"]),
                float(expected_local["y"]),
                float(runtime_spec["table"]["mujocoTopSurfaceWorldZMeters"] + expected_local["z"]),
            )
            body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, semantic_id)
            if body_id < 0:
                raise AssertionError("MuJoCo scene lacks body {}".format(semantic_id))
            actual_world = tuple(float(value) for value in data.xpos[body_id])
            errors = tuple(abs(actual_world[index] - expected_world[index]) for index in range(3))
            within_tolerance = max(errors) <= POSE_SYNC_TOLERANCE_METERS
            if not within_tolerance:
                raise AssertionError("Quest snapshot/MuJoCo pose mismatch: {} seed {}".format(semantic_id, seed))
            snapshot_object = object_by_semantic_id[semantic_id]
            pose_rows.append({
                "sceneId": scene_id,
                "seed": seed,
                "evidenceSource": "frozen_Quest_scene_contract_snapshot_vs_compiled_MuJoCo_state",
                "semanticId": semantic_id,
                "targetId": snapshot_object["targetId"],
                "fixedPose": snapshot_object["fixedPose"],
                "tableLocalXMeters": expected_local["x"],
                "tableLocalYMeters": expected_local["y"],
                "tableLocalZMeters": expected_local["z"],
                "expectedMujocoWorldXMeters": expected_world[0],
                "expectedMujocoWorldYMeters": expected_world[1],
                "expectedMujocoWorldZMeters": expected_world[2],
                "actualMujocoWorldXMeters": actual_world[0],
                "actualMujocoWorldYMeters": actual_world[1],
                "actualMujocoWorldZMeters": actual_world[2],
                "errorXMeters": errors[0],
                "errorYMeters": errors[1],
                "errorZMeters": errors[2],
                "maxAbsoluteErrorMeters": max(errors),
                "predeclaredToleranceMeters": POSE_SYNC_TOLERANCE_METERS,
                "pass": within_tolerance,
            })

        records.append({
            "seed": seed,
            "sceneId": scene_id,
            "snapshotSha256": sha256_text(snapshot_json),
            "layoutSha256": layout_hash,
            "fallbackUsed": bool(snapshot["fallbackUsed"]),
            "sameSeedExactReplay": same_seed_exact,
            "differentFromCanonicalPositions": moved_ids,
            "userZoneFixed": user_zone_fixed,
            "candidateOrder": list(canonical_order),
            "spatialOrderMatchesSnapshot": list(canonical_order) == snapshot["candidateOrderFarToNearLeftToRight"],
            "boundsOverlapAndPlacementValidation": validation["status"],
            "mujocoGeometryAndPoseValidation": mujoco_report["status"],
            "m9GraspReachability": reachability,
            "pagingSubmitKeepsSnapshot": before_queue == serialize_scene_layout_snapshot(snapshot),
            "queuePageCount": queue.page_count,
            "submittedSelections": len(submitted.plan.ordered_selections),
            "slotFrequenciesHz": list(FREQUENCIES_HZ),
            "maxMuJoCoPoseErrorMeters": max(row["maxAbsoluteErrorMeters"] for row in pose_rows if row["seed"] == seed),
        })

    if len(layout_hashes) < 18:
        raise AssertionError("layout variability failed: only {} unique layouts".format(len(layout_hashes)))
    pairwise_pose_changes = {}
    for semantic_id in (
        "assist_medicine_box", "assist_storage_box", "assist_phone",
        "assist_button_switch", "assist_wireless_charger",
    ):
        distinct = {
            tuple(candidate_positions_by_seed[seed][semantic_id][axis] for axis in ("x", "y", "z"))
            for seed in SEEDS
        }
        pairwise_pose_changes[semantic_id] = len(distinct)
        if len(distinct) < 18:
            raise AssertionError("{} did not vary over enough seeds".format(semantic_id))

    result = {
        "schemaVersion": 1,
        "status": "PASS",
        "scope": "20 deterministic M20 layout seeds, frozen-snapshot page/Submit behavior, existing M9 IK, compiled MuJoCo pose parity",
        "canonicalSpecSha256": canonical_sha256,
        "seedCount": len(records),
        "seeds": list(SEEDS),
        "uniqueRandomizedLayouts": len(layout_hashes),
        "perObjectDistinctPoseCounts": pairwise_pose_changes,
        "fallbackCount": sum(item["fallbackUsed"] for item in records),
        "userZoneFixedForAllSeeds": all(item["userZoneFixed"] for item in records),
        "sameSeedReplayExactForAllSeeds": all(item["sameSeedExactReplay"] for item in records),
        "m9IkReachableForAllGraspableSourcesAndSeeds": all(
            all(item["ikReachable"] for item in seed["m9GraspReachability"])
            for seed in records
        ),
        "allMuJoCoPoseRowsPass": all(item["pass"] for item in pose_rows),
        "poseSyncToleranceMeters": POSE_SYNC_TOLERANCE_METERS,
        "questPoseEvidenceBoundary": "Quest coordinates are read from the exact runtime snapshot contract shape; physical Unity Transform values require Quest runtime evidence.",
        "seedResults": records,
    }
    _write_csv(OUTPUT_DIR / "quest_mujoco_pose_sync.csv", pose_rows)
    _write_json(OUTPUT_DIR / "randomization_validation.json", result)
    return result


if __name__ == "__main__":
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
