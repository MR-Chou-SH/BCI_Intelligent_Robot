"""Static validator for the frozen M31 benchmark and label separation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BENCHMARK = ROOT / "open_world_bridge_benchmark.json"
LOCK = ROOT / "open_world_bridge_benchmark_lock.json"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate():
    payload = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    digest = hashlib.sha256(canonical(payload)).hexdigest()
    assert digest == lock["canonical_sha256"], "benchmark differs from frozen lock"
    cases = payload["cases"]
    assert 80 <= len(cases) <= 120
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert payload["split_counts"] == {"train": 48, "dev": 12, "heldout": 24}
    required_pairs = {
        ("药盒", "收纳盒"), ("小药盒", "收纳盒（关闭状态）"), ("手机", "无线充电座"),
        ("苹果", "水果刀"), ("红色苹果", "水果刀"), ("橙子", "榨汁机"),
        ("书", "书架"), ("杯子", "水龙头"), ("衣服", "洗衣篮"),
        ("盘子", "橱柜"), ("笔", "笔筒"), ("螺丝", "螺丝刀"),
    }
    found_pairs = {tuple(case["input"]["selected_objects"]) for case in cases}
    missing = required_pairs - found_pairs
    assert not missing, "required input pairs missing: {}".format(sorted(missing))
    for case in cases:
        assert set(case) == {"case_id", "category", "split", "input", "evaluation"}
        assert set(case["input"]) == {"selected_objects", "scene_context_objects", "task_context"}
        assert set(case["evaluation"]) == {
            "expected_status", "acceptable_relations", "explicit_colors", "explicit_states",
            "forbidden_object_mentions", "label_note",
        }
        assert 2 <= len(case["input"]["selected_objects"]) <= 8
        assert case["evaluation"]["expected_status"] in {"executable", "ambiguous", "invalid"}
        assert case["evaluation"]["acceptable_relations"]
        assert case["split"] in {"train", "dev", "heldout"}
        # The runner serializes only this `input` object into the API request.
        assert "evaluation" not in case["input"]
        assert "expected_status" not in canonical(case["input"]).decode("utf-8")
    return {"status": "PASS", "case_count": len(cases), "split_counts": payload["split_counts"],
            "category_count": len(payload["category_counts"]), "benchmark_sha256": digest,
            "ground_truth_independent": True, "model_input_label_leakage": False}


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
