"""Recompute the M31 summary from saved records without contacting the API."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_m31_benchmark as runner


def main():
    benchmark = json.loads(runner.BENCHMARK.read_text(encoding="utf-8"))
    records = runner._load_records()
    frozen = json.loads(runner.PROMPT_FREEZE.read_text(encoding="utf-8"))
    summary = runner.write_summary(records, benchmark, primary_run_label=frozen["run_label"])
    print(json.dumps({key: summary[key] for key in (
        "primary_run_label", "case_count", "split_counts", "status_accuracy",
        "relation_accuracy_on_expected_executable_cases", "grounding_reference_correctness",
        "explicit_color_fidelity", "explicit_state_safe_outcome", "ambiguous_invalid_handling_accuracy",
        "bilingual_relation_cue_consistency", "high_level_step_relation_alignment",
        "api_latency_ms", "retry_rate", "repeatability",
    )}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
