"""Static integrity and leakage checks for the frozen M32 benchmark."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
BENCHMARK = ROOT / "text_scene_sequence_benchmark.json"
LOCK = ROOT / "text_scene_sequence_benchmark_lock.json"
CASES = ROOT / "text_scene_demo_cases.json"


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate(
    benchmark_path: Path = BENCHMARK,
    lock_path: Path = LOCK,
    cases_path: Path = CASES,
) -> dict[str, Any]:
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    demo_cases = json.loads(cases_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(_canonical(benchmark)).hexdigest()
    assert digest == lock["canonical_sha256"], "benchmark differs from frozen lock"
    scenes = benchmark["scenes"]
    assert len(scenes) == benchmark["scene_count"] == 6
    assert len({scene["family"] for scene in scenes}) == benchmark["scene_family_count"] == 6
    assert benchmark["trajectory_count"] == 12
    assert benchmark["decision_point_count"] == 60
    assert 60 <= benchmark["decision_point_count"] <= 100
    assert len(benchmark["repeatability_point_ids"]) == 8

    point_by_id = {}
    scene_sensitivity_pairs = {}
    for scene in scenes:
        assert 7 <= len(scene["objects"]) <= 8
        assert len(scene["selectable_object_ids"]) == len(scene["objects"])
        objects = {item["object_id"]: item for item in scene["objects"]}
        assert len(objects) == len(scene["objects"])
        text = scene["scene_text"]
        for item in scene["objects"]:
            assert item["mention"] and item["mention"] in text
            assert item["object_id"] in scene["selectable_object_ids"]
            assert "expected_color" not in item and "expected_state" not in item
        assert len(scene["trajectories"]) == 2
        for path in scene["trajectories"]:
            sequence = path["selection_order"]
            assert len(sequence) == 5 and len(set(sequence)) == len(sequence)
            assert set(sequence) <= set(objects)
            assert len(path["decision_points"]) == 5
            for round_index, point in enumerate(path["decision_points"], 1):
                expected_history = sequence[:round_index]
                expected_candidates = [oid for oid in scene["selectable_object_ids"] if oid not in set(expected_history)]
                assert point["selection_round"] == round_index
                assert point["history_object_ids"] == expected_history
                assert point["candidate_object_ids"] == expected_candidates
                assert len(point["candidate_object_ids"]) == len(objects) - round_index
                assert not set(expected_history).intersection(point["candidate_object_ids"])
                acceptable = point["acceptable_next_targets"]
                assert acceptable and len(acceptable) == len(set(acceptable))
                assert set(acceptable) <= set(expected_candidates)
                point_by_id[point["point_id"]] = (scene, path, point)
                pair_id = point.get("history_sensitivity_pair_id")
                if pair_id:
                    scene_sensitivity_pairs.setdefault(pair_id, []).append((scene, point))

    assert len(point_by_id) == 60
    assert set(benchmark["repeatability_point_ids"]) <= set(point_by_id)
    assert len(scene_sensitivity_pairs) == 6
    for pair_id, members in scene_sensitivity_pairs.items():
        assert len(members) == 2, pair_id
        left_scene, left = members[0]
        right_scene, right = members[1]
        assert left_scene["scene_id"] == right_scene["scene_id"]
        assert set(left["history_object_ids"]) == set(right["history_object_ids"])
        assert left["history_object_ids"] != right["history_object_ids"]
        assert left["candidate_object_ids"] == right["candidate_object_ids"]

    assert len(demo_cases["scenes"]) == 6
    # Only raw text and manually authored expectation data are in the benchmark;
    # the live runner separately constructs requests from parser output.
    forbidden_model_input_keys = {"acceptable_next_targets", "expected_color", "expected_state", "label_note"}
    assert not any(key in benchmark["model_input_contract"] for key in forbidden_model_input_keys)
    return {
        "status": "PASS",
        "benchmark_sha256": digest,
        "scene_count": len(scenes),
        "scene_family_count": len({scene["family"] for scene in scenes}),
        "trajectory_count": sum(len(scene["trajectories"]) for scene in scenes),
        "decision_point_count": len(point_by_id),
        "repeatability_point_count": len(benchmark["repeatability_point_ids"]),
        "history_sensitivity_pairs": len(scene_sensitivity_pairs),
        "independent_labels_frozen_before_live_calls": lock["ground_truth_authored_before_live_model_calls"],
        "model_input_label_leakage": False,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
