"""Offline structural repeatability audit of the saved M27 live responses."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
from typing import Any

from run_m27_benchmark import _repeatability_signature


ATTEMPT_DIR = Path(__file__).resolve().parent
RESULTS_PATH = ATTEMPT_DIR / "semantic_planner_results.jsonl"
JSON_OUTPUT = ATTEMPT_DIR / "M27_REPEATABILITY_REASSESSMENT.json"
REPORT_OUTPUT = ATTEMPT_DIR / "M27_REPEATABILITY_REASSESSMENT.md"
PROSE_FIELDS = (
    "intent_summary",
    "natural_language_instruction",
    "assumptions",
    "ambiguity_reason",
)


def _prose_signature(plan: dict[str, Any]) -> str:
    prose = {
        "top_level": {key: plan.get(key) for key in PROSE_FIELDS},
        "alternatives": [
            {key: alternative.get(key) for key in PROSE_FIELDS}
            for alternative in plan.get("alternatives", [])
        ],
    }
    return hashlib.sha256(
        json.dumps(prose, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def reassess() -> dict[str, Any]:
    if JSON_OUTPUT.exists() or REPORT_OUTPUT.exists():
        raise FileExistsError("refusing to overwrite M27 repeatability reassessment output")
    raw = RESULTS_PATH.read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    benchmark = json.loads((ATTEMPT_DIR / "benchmark_cases.json").read_text(encoding="utf-8"))
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if isinstance(row.get("plan"), dict):
            by_case[row["case_id"]].append(row)

    repeatability: dict[str, dict[str, Any]] = {}
    for case_id, case_rows in by_case.items():
        if len(case_rows) < 2:
            continue
        plans = [row["plan"] for row in sorted(case_rows, key=lambda row: row["repeat_index"])]
        repeatability[case_id] = {
            "repeat_count": len(plans),
            "structurally_identical": len({_repeatability_signature(plan) for plan in plans}) == 1,
            "exact_natural_language_identical": len({_prose_signature(plan) for plan in plans}) == 1,
        }

    expected_repeats = {
        case["case_id"] for case in benchmark["cases"] if int(case.get("repeat_count", 1)) > 1
    }
    expected_calls = sum(int(case.get("repeat_count", 1)) for case in benchmark["cases"])
    structure_pass = bool(repeatability) and all(
        entry["structurally_identical"] for entry in repeatability.values()
    )
    payload = {
        "schema_version": 1,
        "status": "PASS" if (
            structure_pass
            and set(repeatability) == expected_repeats
            and len(rows) == expected_calls
        ) else "FAIL",
        "metric_version": "structure-only-v2",
        "source_results": RESULTS_PATH.name,
        "source_results_sha256": hashlib.sha256(raw).hexdigest(),
        "source_live_calls_reused": len(rows),
        "expected_live_calls": expected_calls,
        "new_api_calls": 0,
        "structure_fields": [
            "status",
            "selected_objects in order",
            "ordered top-level actions (type/object_id/target_id)",
            "ordered alternative action sequences",
        ],
        "natural_language_fields_compared_separately": list(PROSE_FIELDS),
        "repeatability": repeatability,
    }
    report = [
        "# M27 Repeatability Reassessment",
        "",
        "## Result",
        "",
        "- Structural repeatability: **{}** across {} repeated cases.".format(
            "PASS" if structure_pass else "FAIL", len(repeatability)
        ),
        "- Saved live response rows reused: {}; new API calls: **0**.".format(len(rows)),
        "- Source result SHA-256: `{}`.".format(payload["source_results_sha256"]),
        "",
        "The first metric included `intent_summary` in its structural hash. That made a prose paraphrase appear to be a changed plan. This reassessment hashes status, selected-object order, ordered executable actions, and ordered alternative action sequences. Natural-language fields are compared separately and are not treated as executable-plan structure.",
        "",
        "## Repeated cases",
        "",
        "| Case | Repeats | Structure identical | Exact prose identical |",
        "|---|---:|---|---|",
    ]
    for case_id, entry in repeatability.items():
        report.append(
            "| {} | {} | {} | {} |".format(
                case_id,
                entry["repeat_count"],
                "yes" if entry["structurally_identical"] else "no",
                "yes" if entry["exact_natural_language_identical"] else "no",
            )
        )
    report.extend([
        "",
        "This is an offline reanalysis of the unchanged attempt-03 JSONL. It did not contact DeepSeek, alter the saved model responses, dispatch a robot action, or change the benchmark cases.",
        "",
    ])
    JSON_OUTPUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    REPORT_OUTPUT.write_text("\n".join(report), encoding="utf-8", newline="\n")
    return payload


if __name__ == "__main__":
    print(json.dumps(reassess(), indent=2, ensure_ascii=False))
