"""Build non-destructive corrected M27 summary/report from saved evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re


ATTEMPT_DIR = Path(__file__).resolve().parent
AUDIT_PATH = ATTEMPT_DIR / "M27_REPEATABILITY_REASSESSMENT.json"
SOURCE_SUMMARY = ATTEMPT_DIR / "semantic_planner_summary.json"
SOURCE_REPORT = ATTEMPT_DIR / "M27_FINAL_REPORT.md"
SUMMARY_OUTPUT = ATTEMPT_DIR / "semantic_planner_summary_reassessed.json"
REPORT_OUTPUT = ATTEMPT_DIR / "M27_FINAL_REPORT_REASSESSED.md"


def build() -> None:
    if SUMMARY_OUTPUT.exists() or REPORT_OUTPUT.exists():
        raise FileExistsError("refusing to overwrite reassessed M27 outputs")
    audit = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
    summary = json.loads(SOURCE_SUMMARY.read_text(encoding="utf-8"))
    report = SOURCE_REPORT.read_text(encoding="utf-8")
    if audit.get("status") != "PASS":
        raise ValueError("repeatability reassessment must pass before building final outputs")

    repeats = copy.deepcopy(audit["repeatability"])
    summary["benchmark"]["initial_repeatability_metric"] = summary["benchmark"]["repeatability"]
    summary["benchmark"]["repeatability"] = repeats
    summary["benchmark"]["repeatability_metric_version"] = audit["metric_version"]
    summary["benchmark"]["repeatability_source_results_sha256"] = audit["source_results_sha256"]

    old_bullet = re.compile(r"(?m)^- Low-temperature structural repeatability: `.*`\.$")
    replacement = (
        "- Low-temperature structural repeatability: corrected structure-only-v2 result is **PASS** across all five repeated cases; exact natural-language wording varied in all five. See `M27_REPEATABILITY_REASSESSMENT.md`."
    )
    report, count = old_bullet.subn(replacement, report)
    if count != 1:
        raise ValueError("expected exactly one original repeatability summary bullet")
    section = [
        "## Repeatability reassessment",
        "",
        "The initial signature included `intent_summary`, so it treated paraphrased prose as a changed plan. The corrected structure-only-v2 metric compares status, selected-object order, ordered executable actions, and ordered alternative action sequences. Across all five repeated cases, those structures are identical. Exact natural-language text varied for all five cases and is reported separately; it does not change the executable plan structure.",
        "",
        "The reassessment reuses the unchanged 14 saved live response rows. It made zero new API calls. The original attempt-03 report, summary, and JSONL remain preserved alongside these reassessed outputs.",
        "",
    ]
    marker = "## Interpretation and limitations"
    if report.count(marker) != 1:
        raise ValueError("expected one interpretation section marker")
    report = report.replace(marker, "\n".join(section) + marker)
    SUMMARY_OUTPUT.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    REPORT_OUTPUT.write_text(report, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    build()
