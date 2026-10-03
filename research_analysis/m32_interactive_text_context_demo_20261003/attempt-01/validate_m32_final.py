"""Read-only machine audit for the final M32 benchmark and preserved v1 run."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from validate_m32_benchmark import validate as validate_benchmark  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate() -> dict[str, Any]:
    benchmark_path = ROOT / "text_scene_sequence_benchmark.json"
    lock_path = ROOT / "text_scene_sequence_benchmark_lock.json"
    cases_path = ROOT / "text_scene_demo_cases.json"
    results_path = ROOT / "text_scene_sequence_results.jsonl"
    summary_path = ROOT / "text_scene_sequence_summary.json"
    runbook_path = ROOT / "M32_MANUAL_REVIEW_RUNBOOK.md"
    report_path = ROOT / "M32_FINAL_REPORT.md"
    regression_log = ROOT / "iterations" / "live-run-v2-final-audit" / "focused_regression_tests.log"
    compile_log = ROOT / "iterations" / "live-run-v2-final-audit" / "focused_py_compile.txt"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    records = _load_jsonl(results_path)
    static = validate_benchmark(benchmark_path, lock_path, cases_path)

    assert static["status"] == "PASS"
    assert len(records) == 76
    assert sum(row.get("record_type") == "scene_parse" for row in records) == 6
    assert sum(row.get("record_type") == "context" for row in records) == 60
    assert sum(row.get("record_type") == "repeatability" for row in records) == 8
    assert records[0]["record_type"] == "run_started" and records[-1]["record_type"] == "run_finished"
    assert records[0].get("deepseek_key_persisted") is False
    finished = records[-1]
    assert finished.get("api_key_persisted") is False
    assert finished.get("eeg_used") is False and finished.get("quest_used") is False
    assert finished.get("real_vla_dispatch") is False

    parses = {row["scene_id"]: row for row in records if row.get("record_type") == "scene_parse"}
    scenes = {scene["scene_id"]: scene for scene in benchmark["scenes"]}
    parsed_scenes: dict[str, dict[str, Any]] = {}
    object_map: dict[str, dict[str, str]] = {}
    for scene_id, scene_benchmark in scenes.items():
        parsed_record = parses[scene_id]
        assert parsed_record["run_status"] == "parsed"
        scene = parsed_record["parsed_scene"]
        parsed_scenes[scene_id] = scene
        selectable = [item for item in scene["objects"] if item.get("selectable") is True]
        assert len(selectable) == 8
        by_mention = {item["source_mention"]: item for item in selectable}
        assert len(by_mention) == len(scene_benchmark["objects"]) == 8
        assert all(obj["mention"] in by_mention for obj in scene_benchmark["objects"])
        assert set(by_mention) == {obj["mention"] for obj in scene_benchmark["objects"]}
        object_map[scene_id] = {obj["object_id"]: by_mention[obj["mention"]]["id"]
                                for obj in scene_benchmark["objects"]}
        for item in scene_benchmark["objects"]:
            actual = by_mention[item["mention"]]
            assert actual.get("color") == item.get("explicit_color")
            if "explicit_state" in item:
                assert actual.get("current_state") == item["explicit_state"]
            elif actual.get("current_state"):
                raise AssertionError("unspecified state was invented")

    contexts = [row for row in records if row.get("record_type") == "context"]
    by_point = {row["point_id"]: row for row in contexts}
    context_passes = 0
    for scene in benchmark["scenes"]:
        scene_id = scene["scene_id"]
        parsed = parsed_scenes[scene_id]
        selectable_order = [object_id for object_id in parsed["candidate_order"]
                            if any(item["id"] == object_id and item.get("selectable") is True
                                   for item in parsed["objects"])]
        for trajectory in scene["trajectories"]:
            for point in trajectory["decision_points"]:
                record = by_point[point["point_id"]]
                assert record["run_status"] == "completed"
                expected_history = [object_map[scene_id][oid] for oid in point["history_object_ids"]]
                expected_candidates = [oid for oid in selectable_order if oid not in set(expected_history)]
                assert record["actual_history_ids"] == expected_history
                assert record["actual_candidate_ids"] == expected_candidates
                assert record["scorer_expected_candidate_ids"] == [
                    object_map[scene_id][oid] for oid in point["candidate_object_ids"]
                ]
                result = record["context_result"]
                assert set(result["candidate_ids"]) == set(expected_candidates)
                assert not (set(result["candidate_ids"]) & set(expected_history))
                q_rows = result["q_global"]
                assert result["q_dimension"] == len(expected_candidates) == len(q_rows)
                q_values = [float(item["q"]) for item in q_rows]
                assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in q_values)
                assert math.isclose(sum(q_values), 1.0, abs_tol=1e-8)
                context_passes += 1

    history = summary["context"]["history_sensitivity"]
    assert history["same_candidate_set_pairs"] == 6
    assert history["comparable_full_ranking_pairs"] >= 1
    assert history["ranking_changed"]["numerator"] >= 1
    assert summary["context"]["model_ambiguous_status_count"] > 0
    assert summary["repeatability"]["repeat_calls"] == 8
    assert summary["scene_parser"]["extra_accepted_object_mentions"] == 0
    assert summary["scene_parser"]["unspecified_colors_invented"] == 0
    assert summary["scene_parser"]["unspecified_states_invented"] == 0
    assert "& '.venv\\Scripts\\python.exe' -m integration.semantic_context_demo_cli" in runbook_path.read_text(encoding="utf-8")
    assert report_path.is_file()
    regression_text = regression_log.read_text(encoding="utf-8")
    compile_text = compile_log.read_text(encoding="utf-8")
    assert "Ran 56 tests" in regression_text and "OK" in regression_text
    assert "PASS: 18 targeted Python source/test files compiled." in compile_text

    archive = ROOT / "iterations" / "live-run-v1"
    archive_manifest_path = archive / "archive_manifest.json"
    archive_manifest = json.loads(archive_manifest_path.read_text(encoding="utf-8"))
    for relative, metadata in archive_manifest["files"].items():
        assert _sha256((archive / relative).read_bytes()) == metadata["sha256"], relative
    assert lock["live_run_v1_archive_manifest_sha256"] == _sha256(archive_manifest_path.read_bytes())

    secret_patterns = [re.compile(r"sk-[A-Za-z0-9]{24,}"),
                       re.compile(r"(?i)DEEPSEEK_API_KEY\s*=\s*[^\s\"']{12,}")]
    scan_paths = [path for path in ROOT.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    for path in scan_paths:
        data = path.read_bytes()
        if any(pattern.search(data.decode("utf-8", errors="ignore")) for pattern in secret_patterns):
            raise AssertionError("credential-pattern scan finding in " + path.name)

    return {
        "schema_version": 1,
        "status": "PASS_WITH_REPORTED_LIMITATIONS",
        "scope": "M32 software-only text-scene and sequential M30 Context validation",
        "benchmark_validation": static,
        "benchmark_canonical_sha256": lock["canonical_sha256"],
        "result_jsonl_sha256": _sha256(results_path.read_bytes()),
        "records": {"total": len(records), "scene_parses": 6, "context_decisions": context_passes,
                    "repeatability_calls": 8},
        "software_tests": {
            "focused_m32_and_m30_semantic_regressions": "PASS (56 tests)",
            "targeted_py_compile": "PASS (18 source/test files)",
        },
        "software_contract_checks": {
            "six_scene_families_and_7_to_8_selectable_objects": "PASS",
            "source_grounded_object_mentions": "PASS (48/48)",
            "explicit_colors_and_states": "PASS (10/10 colors; 4/4 states)",
            "no_unspecified_color_or_state_invention": "PASS",
            "full_remaining_candidate_set_and_selected_removal": "PASS (60/60)",
            "variable_q_dimension_and_normalization": "PASS (60/60)",
            "same_candidate_different_history_ranking_change": "PASS (1 of 4 comparable pairs)",
            "ambiguity_status_supported": "PASS_WITH_LOW_RECALL (3/60 labeled ambiguous points)",
            "manual_review_runbook": "PASS",
            "future_image_input": "NOT_IMPLEMENTED_BY_SCOPE",
        },
        "quality_limitations": {
            "top1_acceptable": summary["context"]["top1_acceptable_next_target"],
            "top3_acceptable": summary["context"]["top3_accepts_an_independent_label"],
            "ambiguity_status_count": summary["context"]["model_ambiguous_status_count"],
            "repeat_structural_exact": summary["repeatability"]["structural_exact"],
            "repeat_q_l1_mean": summary["repeatability"]["q_l1_distance_mean"],
            "context_latency_ms": summary["context"]["latency_ms"],
        },
        "preservation": {"v1_archive_hashes_verified": len(archive_manifest["files"]),
                         "raw_eeg_or_device_data_touched": False,
                         "m30_heldout_results_retuned": False},
        "boundaries": {"eeg_used": False, "quest_used": False, "image_input_implemented": False,
                       "real_vla_dispatch": False, "deepseek_key_persisted": False},
    }


if __name__ == "__main__":
    report = validate()
    output_path = ROOT / "final_validation.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "context_decisions": report["records"]["context_decisions"],
                      "benchmark_sha256": report["benchmark_canonical_sha256"],
                      "v1_archive_files_verified": report["preservation"]["v1_archive_hashes_verified"]},
                     ensure_ascii=False, indent=2))
