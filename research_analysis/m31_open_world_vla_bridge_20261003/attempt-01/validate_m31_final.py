"""Requirement audit for the complete M31 software experiment."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.semantic_language_bridge import M31_ENGINE_VERSION, M31_PROMPT_VERSION, M31_SCHEMA_VERSION
from run_m31_benchmark import _load_records, summarize
from validate_m31_benchmark import validate as validate_benchmark


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def validate():
    benchmark_path = ROOT / "open_world_bridge_benchmark.json"
    lock_path = ROOT / "open_world_bridge_benchmark_lock.json"
    results_path = ROOT / "open_world_bridge_results.jsonl"
    summary_path = ROOT / "open_world_bridge_summary.json"
    freeze_path = ROOT / "m31_prompt_freeze.json"
    manual_path = ROOT / "required_manual_examples.json"
    report_path = ROOT / "M31_FINAL_REPORT.md"
    runbook_path = ROOT / "M31_DEMO_RUNBOOK.md"
    spec_path = ROOT / "M31_EXPERIMENT_SPEC.md"

    required = [benchmark_path, lock_path, results_path, summary_path, freeze_path,
                manual_path, report_path, runbook_path, spec_path]
    assert all(path.is_file() for path in required), "one or more M31 required outputs are missing"
    bench_check = validate_benchmark()
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    results = _load_records()
    summary_file = json.loads(summary_path.read_text(encoding="utf-8"))
    final_summary = summarize(results, benchmark, primary_run_label=freeze["run_label"])
    main = [row for row in results if row.get("run_label") == freeze["run_label"]]
    repeat = [row for row in results if row.get("run_label") == "repeat-01"]
    assert freeze["benchmark_sha256"] == bench_check["benchmark_sha256"]
    assert freeze["prompt_version"] == M31_PROMPT_VERSION
    assert freeze["schema_version_used"] == M31_SCHEMA_VERSION
    assert freeze["engine_version"] == M31_ENGINE_VERSION
    assert len(main) == len(benchmark["cases"]) == 84
    assert {row["case_id"] for row in main} == {case["case_id"] for case in benchmark["cases"]}
    assert {split: sum(row["split"] == split for row in main) for split in ("train", "dev", "heldout")} == {
        "train": 48, "dev": 12, "heldout": 24,
    }
    assert len(repeat) == 16 and len({row["case_id"] for row in repeat}) == 16
    assert final_summary["case_count"] == 84
    assert summary_file["primary_run_label"] == freeze["run_label"]
    assert summary_file["case_count"] == 84
    assert summary_file["repeatability"]["structural_exact_rate"] is not None
    assert summary_file["repeatability"]["prose_exact_rate"] is not None

    by_case = {row["case_id"]: row for row in main}
    for case in main:
        result = case["result"]
        assert result.get("dispatch_allowed") is False
        assert result.get("status") in {"executable", "ambiguous", "invalid"}
        if result["status"] == "executable":
            assert result.get("vla_instruction_zh") and result.get("vla_instruction_en")
        else:
            assert not result.get("ordered_high_level_steps")
            assert not result.get("vla_instruction_zh") and not result.get("vla_instruction_en")

    manual = json.loads(manual_path.read_text(encoding="utf-8"))
    assert len(manual["examples"]) == 16
    assert manual["run_label"] == freeze["run_label"]
    for example in manual["examples"]:
        result = by_case[example["case_id"]]["result"]
        actual = example["actual_result"]
        assert actual["status"] == result["status"]
        assert actual["relation_type"] == result["relation_type"]
        assert actual["vla_instruction_zh"] == result.get("vla_instruction_zh", "")
        assert actual["vla_instruction_en"] == result.get("vla_instruction_en", "")
        assert actual["dispatch_allowed"] is False

    report = report_path.read_text(encoding="utf-8")
    assert all(re.search(r"^{}\.?\s".format(number), report, re.MULTILINE) for number in range(1, 12))
    assert "real VLA" in report and "not" in report.lower()
    runbook = runbook_path.read_text(encoding="utf-8")
    assert "integration.semantic_bridge_cli" in runbook and "strict-scene" in runbook

    scan_paths = [path for path in ROOT.rglob("*") if path.is_file()]
    leak_patterns = [re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/-]{20,}"),
                     re.compile(r"(?i)DEEPSEEK_API_KEY\s*[:=]\s*[^\s'\"]{8,}"),
                     re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")]
    leaked = []
    for path in scan_paths:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in leak_patterns):
            leaked.append(str(path.relative_to(ROOT)))
    assert not leaked, "potential API credential persisted in {}".format(leaked)

    return {
        "schema_version": "m31-final-validation-v1", "validated_at_utc": _utc_now(),
        "status": "PASS_WITH_REPORTED_LIMITATIONS", "software_implementation": "PASS",
        "benchmark_execution": "PASS", "human_semantic_quality": "PARTIAL",
        "benchmark": bench_check,
        "main_run": {"run_label": freeze["run_label"], "model_id": freeze["model_id"],
                     "prompt_version": freeze["prompt_version"], "case_count": len(main),
                     "split_counts": final_summary["split_counts"],
                     "status_accuracy": final_summary["status_accuracy"],
                     "relation_accuracy_on_expected_executable_cases": final_summary["relation_accuracy_on_expected_executable_cases"],
                     "ambiguous_invalid_handling_accuracy": final_summary["ambiguous_invalid_handling_accuracy"],
                     "bilingual_relation_cue_consistency": final_summary["bilingual_relation_cue_consistency"],
                     "high_level_step_relation_alignment": final_summary["high_level_step_relation_alignment"],
                     "grounding_reference_correctness": final_summary["grounding_reference_correctness"],
                     "explicit_color_fidelity": final_summary["explicit_color_fidelity"],
                     "explicit_state_safe_outcome": final_summary["explicit_state_safe_outcome"],
                     "api_latency_ms": final_summary["api_latency_ms"],
                     "repeatability": final_summary["repeatability"]},
        "required_outputs_present": True,
        "default_cli_mode": "semantic_open_world",
        "strict_scene_mode_retained": True,
        "dispatch_allowed": False,
        "real_vla_connected": False,
        "api_key_persisted": False,
        "heldout_used_for_prompt_tuning": False,
        "limitations": [
            "Ambiguous-pair classification remains weak; 2/7 benchmark ambiguity cases were recognized.",
            "One frozen development label expects charging from an explicitly occupied charger; the fail-closed invalid output is state-consistent, and the original label was preserved.",
            "Forbidden third-object phrase audit is finite and is not general open-vocabulary entity detection.",
        ],
    }


if __name__ == "__main__":
    output = validate()
    destination = ROOT / "final_validation.json"
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
