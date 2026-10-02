"""Run and score the frozen M28 benchmark using the live semantic Context engine.

Only ``case.model_input`` is passed to SemanticContextEngine. Evaluation labels
are read by this runner after the engine has returned. JSONL rows are append-only
so interrupted runs can resume without replacing prior observations.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from integration.semantic_context_engine import build_live_context_engine  # noqa: E402


BENCHMARK = OUT / "semantic_context_benchmark_v1.json"
LOCK = OUT / "semantic_context_benchmark_lock.json"
RESULTS = OUT / "semantic_context_engine_results.jsonl"
SUMMARY = OUT / "semantic_context_benchmark_summary.json"
QUALITY_CSV = OUT / "semantic_context_quality_distribution.csv"
CONFUSION_CSV = OUT / "semantic_context_confusion.csv"
REPEATABILITY = OUT / "semantic_context_repeatability.json"
LATENCY = OUT / "semantic_context_latency.json"
REPEAT_CASES = (
    "m20_phone_charge",
    "m20_phone_vague_destination",
    "m20_medicine_handover",
    "book_shelve_after_reading",
    "cup_to_sink_for_rinsing",
    "pen_in_stand",
    "adversarial_two_containers",
    "adversarial_book_wrong_surface",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def load_frozen_benchmark() -> tuple[dict[str, Any], str]:
    if not BENCHMARK.is_file() or not LOCK.is_file():
        raise FileNotFoundError("frozen M28 benchmark or lock is missing")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    actual_hash = _sha256(BENCHMARK)
    if actual_hash != lock.get("sha256"):
        raise ValueError("frozen benchmark SHA-256 does not match its lock")
    document = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) != int(lock.get("case_count", -1)):
        raise ValueError("benchmark case count does not match the freeze lock")
    forbidden_keys = {
        "evaluation_only", "expected_target_ids", "expected_top_candidate",
        "expected_relation_type", "true_label", "true_class", "future_eeg",
    }
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("model_input"), dict):
            raise ValueError("each benchmark case must contain a separate model_input object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or case_id in ids:
            raise ValueError("benchmark case IDs must be unique strings")
        ids.add(case_id)
        if forbidden_keys & set(_walk_keys(case["model_input"])):
            raise ValueError("benchmark evaluation labels leaked into model_input")
        if not isinstance(case.get("evaluation_only"), dict):
            raise ValueError("evaluation labels must be stored separately")
    return document, actual_hash


def _result_rows() -> list[dict[str, Any]]:
    if not RESULTS.exists():
        return []
    rows = []
    with RESULTS.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError("invalid existing JSONL row at line {}".format(line_number)) from error
    return rows


def _signature(result: dict[str, Any]) -> tuple[Any, ...]:
    relations = tuple(sorted(
        (row.get("candidate_id"), row.get("relation_type"))
        for row in result.get("candidate_scores", [])
    ))
    return (result.get("status"), result.get("coverage_decision"), tuple(result.get("candidate_ranking", [])), relations)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _score(document: dict[str, Any], rows: list[dict[str, Any]], benchmark_sha: str, metadata: dict[str, Any]) -> None:
    primary_by_id = {row["case_id"]: row for row in rows if row.get("call_kind") == "primary"}
    case_by_id = {case["case_id"]: case for case in document["cases"]}
    if set(primary_by_id) != set(case_by_id):
        missing = sorted(set(case_by_id) - set(primary_by_id))
        raise ValueError("cannot summarize until every frozen case has a primary result: {}".format(missing[:5]))

    primary = [primary_by_id[case["case_id"]] for case in document["cases"]]
    informative = [row for row in primary if row["evaluation_only"].get("informative_single_target")]
    top1_correct = [row for row in informative if row["result"].get("top_candidate") in row["evaluation_only"].get("expected_target_ids", [])]
    active = [row for row in primary if row["result"].get("coverage_decision") == "active"]
    active_correct = [row for row in active if row["result"].get("top_candidate") in row["evaluation_only"].get("expected_target_ids", [])]
    expected_ambiguous = [row for row in primary if row["evaluation_only"].get("expected_status") == "ambiguous"]
    ambiguous_correct = [row for row in expected_ambiguous if row["result"].get("status") in {"ambiguous", "context_off"} and row["result"].get("coverage_decision") != "active"]
    expected_invalid = [row for row in primary if row["evaluation_only"].get("expected_status") == "invalid"]
    invalid_rejected = [row for row in expected_invalid if row["result"].get("status") in {"invalid", "context_off"} and row["result"].get("coverage_decision") != "active"]
    api_evaluated = [row for row in rows if row.get("call_kind") in {"primary", "repeat"} and row["result"].get("provenance", {}).get("attempt_count", 0) > 0]
    retry_calls = [row for row in api_evaluated if row["result"].get("provenance", {}).get("attempt_count", 0) > 1]

    def rate(numerator: int, denominator: int) -> dict[str, Any]:
        return {"count": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}

    quality_rows: list[dict[str, Any]] = []
    confusion_counts: Counter[tuple[str, str, str]] = Counter()
    relation_status: dict[str, Counter[str]] = defaultdict(Counter)
    hallucinated = 0
    affordance_violations = 0
    for row in primary:
        evaluation = row["evaluation_only"]
        result = row["result"]
        expected_targets = evaluation.get("expected_target_ids", [])
        predicted = result.get("top_candidate") or ""
        relation = evaluation.get("expected_relation_type", "unknown")
        active_flag = result.get("coverage_decision") == "active"
        correct = bool(expected_targets and predicted in expected_targets)
        relation_status[relation][result.get("status", "missing")] += 1
        confusion_counts[(relation, expected_targets[0] if expected_targets else "NONE", predicted or "NONE")] += 1
        candidate_ids = set(row.get("candidate_ids", []))
        reported_ids = {item.get("candidate_id") for item in result.get("candidate_scores", [])}
        reported_ids.update(result.get("candidate_ranking", []))
        if reported_ids - candidate_ids:
            hallucinated += 1
        # The engine refuses any relation unsupported by the scene before it
        # returns scores; this field remains independently auditable per case.
        if row.get("validation_diagnostics", {}).get("affordance_violation"):
            affordance_violations += 1
        quality_rows.append({
            "case_id": row["case_id"], "scene_split": evaluation.get("scene_split", ""),
            "scene_family": row.get("scene_family", ""), "expected_relation_type": relation,
            "expected_status": evaluation.get("expected_status", ""), "engine_status": result.get("status", ""),
            "expected_target_ids": ";".join(expected_targets), "predicted_top_candidate": predicted,
            "target_correct": correct, "context_active": active_flag,
            "semantic_confidence": result.get("semantic_confidence"), "raw_margin": result.get("margin"),
            "prior_top_mass": result.get("prior_top_mass"), "prior_margin": result.get("prior_margin"),
            "entropy": result.get("entropy"), "api_latency_ms": row.get("api_latency_ms"),
            "attempt_count": result.get("provenance", {}).get("attempt_count", 0),
        })

    split_summary: dict[str, Any] = {}
    for split in sorted({row["evaluation_only"].get("scene_split", "unknown") for row in primary}):
        group = [row for row in primary if row["evaluation_only"].get("scene_split") == split]
        single = [row for row in group if row["evaluation_only"].get("informative_single_target")]
        hits = sum(row["result"].get("top_candidate") in row["evaluation_only"].get("expected_target_ids", []) for row in single)
        split_summary[split] = {
            "case_count": len(group), "informative_single_target_count": len(single),
            "top1_correct": rate(hits, len(single)),
            "active_count": sum(row["result"].get("coverage_decision") == "active" for row in group),
        }

    measured_calls = [row for row in rows if row.get("call_kind") in {"primary", "repeat"}]
    latencies = [float(row.get("api_latency_ms", 0.0)) for row in measured_calls]
    active_latencies = [float(row.get("api_latency_ms", 0.0)) for row in measured_calls if row["result"].get("coverage_decision") == "active"]
    all_expected_active = [row for row in primary if row["evaluation_only"].get("informative_single_target")]
    gains = [
        {
            "case_id": row["case_id"], "expected_relation_type": row["evaluation_only"].get("expected_relation_type"),
            "scene_split": row["evaluation_only"].get("scene_split"), "top1_correct": row["result"].get("top_candidate") in row["evaluation_only"].get("expected_target_ids", []),
            "active": row["result"].get("coverage_decision") == "active",
        }
        for row in all_expected_active
    ]
    repeat_rows = [row for row in rows if row.get("call_kind") == "repeat"]
    repeats_by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in repeat_rows:
        repeats_by_case[row["case_id"]].append(row)
    repeat_case_reports = {}
    for case_id, repeated in sorted(repeats_by_case.items()):
        first = primary_by_id[case_id]["result"]
        calls = [first, *[row["result"] for row in repeated]]
        signatures = [_signature(result) for result in calls]
        repeat_case_reports[case_id] = {
            "calls": len(signatures),
            "structural_consistency": len(set(signatures)) == 1,
            "top_candidate_sequence": [item.get("top_candidate") for item in calls],
            "status_sequence": [item.get("status") for item in calls],
            "api_latency_ms": [primary_by_id[case_id].get("api_latency_ms"), *[row.get("api_latency_ms") for row in repeated]],
        }
    repeatability = {
        "repeat_case_count": len(repeat_case_reports),
        "repeat_call_count": len(repeat_rows),
        "all_structures_consistent": all(item["structural_consistency"] for item in repeat_case_reports.values()),
        "consistent_case_count": sum(item["structural_consistency"] for item in repeat_case_reports.values()),
        "cases": repeat_case_reports,
    }

    summary = {
        "benchmark_id": document["benchmark_id"], "benchmark_sha256": benchmark_sha,
        "generated_at_utc": _utc_now(), "model": metadata,
        "evidence_label": "curated semantic benchmark performance; not population user-intent accuracy",
        "case_count": len(primary), "scene_split_summary": split_summary,
        "top1_target_precision_informative_single_target": rate(len(top1_correct), len(informative)),
        "top_k_target_recall_k3": rate(sum(bool(set(row["result"].get("candidate_ranking", [])[:3]) & set(row["evaluation_only"].get("expected_target_ids", []))) for row in informative), len(informative)),
        "active_context_precision": rate(len(active_correct), len(active)),
        "active_context_coverage": rate(len(active), len(primary)),
        "active_coverage_on_informative_cases": rate(sum(row["result"].get("coverage_decision") == "active" for row in informative), len(informative)),
        "ambiguity_accuracy": rate(len(ambiguous_correct), len(expected_ambiguous)),
        "invalid_case_rejection": rate(len(invalid_rejected), len(expected_invalid)),
        "expected_informative_active_count": sum(item["active"] for item in gains),
        "expected_informative_case_count": len(gains),
        "mean_prior_top_mass": statistics.mean(float(row["result"].get("prior_top_mass")) for row in primary if row["result"].get("prior_top_mass") is not None) if any(row["result"].get("prior_top_mass") is not None for row in primary) else None,
        "mean_normalized_entropy": statistics.mean(float(row["result"].get("entropy")) for row in primary if row["result"].get("entropy") is not None) if any(row["result"].get("entropy") is not None for row in primary) else None,
        "mean_raw_margin": statistics.mean(float(row["result"].get("margin")) for row in primary if row["result"].get("margin") is not None) if any(row["result"].get("margin") is not None for row in primary) else None,
        "hallucinated_object_case_count": hallucinated,
        "hallucinated_object_rate": hallucinated / len(primary) if primary else None,
        "affordance_violation_case_count": affordance_violations,
        "affordance_violation_rate": affordance_violations / len(primary) if primary else None,
        "result_status_counts": dict(Counter(row["result"].get("status", "missing") for row in primary)),
        "context_active_count": len(active), "context_off_or_abstained_count": len(primary) - len(active),
        "api_failure_count": sum(row["result"].get("reason_code") == "api_failure" for row in primary),
        "correction_retry": {"logical_calls_with_api": len(api_evaluated), "calls_requiring_retry": len(retry_calls), "retry_rate": len(retry_calls) / len(api_evaluated) if api_evaluated else None, "api_requests_total": sum(row["result"].get("provenance", {}).get("attempt_count", 0) for row in api_evaluated)},
        "confusion_matrix_by_relation_status": {relation: dict(counts) for relation, counts in sorted(relation_status.items())},
        "repeatability": repeatability,
    }
    summary["api_latency"] = {
        "n": len(latencies), "mean_ms": statistics.mean(latencies) if latencies else None,
        "median_ms": statistics.median(latencies) if latencies else None,
        "p90_ms": sorted(latencies)[math.ceil(0.90 * len(latencies)) - 1] if latencies else None,
        "p95_ms": sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1] if latencies else None,
        "active_n": len(active_latencies), "active_mean_ms": statistics.mean(active_latencies) if active_latencies else None,
        "precompute_before_trial_recommended": bool(latencies and statistics.median(latencies) > 300.0),
        "per_call_ms": [{"case_id": row["case_id"], "call_kind": row["call_kind"], "ms": row.get("api_latency_ms")} for row in measured_calls],
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPEATABILITY.write_text(json.dumps(repeatability, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    LATENCY.write_text(json.dumps(summary["api_latency"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(QUALITY_CSV, quality_rows, list(quality_rows[0]))
    confusion_rows = [
        {"expected_relation_type": key[0], "expected_target_id": key[1], "predicted_top_candidate": key[2], "count": count}
        for key, count in sorted(confusion_counts.items())
    ]
    _write_csv(CONFUSION_CSV, confusion_rows, ["expected_relation_type", "expected_target_id", "predicted_top_candidate", "count"])


def run(*, repeats: int = 2, summarize_only: bool = False) -> dict[str, Any]:
    document, benchmark_sha = load_frozen_benchmark()
    if repeats < 0 or repeats > 3:
        raise ValueError("repeat count must be from 0 through 3")
    rows = _result_rows()
    metadata_rows = [row for row in rows if row.get("record_type") == "run_metadata"]
    if summarize_only:
        if not metadata_rows:
            raise ValueError("summarize-only requested but run metadata is missing")
        metadata = metadata_rows[0]["model"]
        _score(document, rows, benchmark_sha, metadata)
        return json.loads(SUMMARY.read_text(encoding="utf-8"))
    engine, available_models, configured_model = build_live_context_engine()
    metadata = engine.metadata()
    metadata.update({"available_chat_models": available_models, "model_configured_by_environment": configured_model})
    if metadata_rows and metadata_rows[0].get("benchmark_sha256") != benchmark_sha:
        raise ValueError("existing result rows belong to a different frozen benchmark")
    if not metadata_rows:
        if rows:
            raise ValueError("existing results have no run metadata record; refusing to append")
        metadata_row = {"record_type": "run_metadata", "benchmark_sha256": benchmark_sha, "created_at_utc": _utc_now(), "model": metadata}
        with RESULTS.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(metadata_row, ensure_ascii=False, separators=(",", ":")) + "\n")
        rows.append(metadata_row)
    elif metadata_rows[0].get("model") != metadata:
        raise ValueError("resolved model/config changed from the original run; use a new attempt directory")

    completed = {row["case_id"] for row in rows if row.get("call_kind") == "primary"}
    case_by_id = {case["case_id"]: case for case in document["cases"]}
    for case in document["cases"]:
        case_id = case["case_id"]
        if case_id in completed:
            continue
        # Evaluation labels stay in this runner and are not added to model_input.
        model_input = case["model_input"]
        result = engine.predict(model_input)
        eval_only = case["evaluation_only"]
        record = {
            "record_type": "case_result", "call_kind": "primary", "case_id": case_id,
            "scene_family": case.get("scene_family"), "candidate_ids": list(model_input["candidate_next_object_ids"]),
            "evaluation_only": eval_only, "result": result,
            "api_latency_ms": result["provenance"]["api_latency_ms"],
            "validation_diagnostics": {"affordance_violation": any(
                "affordance" in " ".join(item.get("validation_errors", [])).lower()
                for item in result.get("provenance", {}).get("diagnostics", [])
            )},
            "completed_at_utc": _utc_now(),
        }
        with RESULTS.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        rows.append(record)
        completed.add(case_id)
        print("PRIMARY {}/{} {} -> {} ({})".format(len(completed), len(document["cases"]), case_id, result.get("top_candidate"), result.get("status")), flush=True)

    existing_repeats = Counter(row["case_id"] for row in rows if row.get("call_kind") == "repeat")
    for case_id in REPEAT_CASES:
        if case_id not in case_by_id:
            raise ValueError("declared repeat case not found in frozen benchmark: " + case_id)
        while existing_repeats[case_id] < repeats:
            case = case_by_id[case_id]
            model_input = case["model_input"]
            result = engine.predict(model_input)
            record = {
                "record_type": "case_result", "call_kind": "repeat", "replicate": existing_repeats[case_id] + 1,
                "case_id": case_id, "scene_family": case.get("scene_family"),
                "candidate_ids": list(model_input["candidate_next_object_ids"]),
                "evaluation_only": case["evaluation_only"], "result": result,
                "api_latency_ms": result["provenance"]["api_latency_ms"], "completed_at_utc": _utc_now(),
            }
            with RESULTS.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            rows.append(record)
            existing_repeats[case_id] += 1
            print("REPEAT {} {}/{} -> {} ({})".format(case_id, existing_repeats[case_id], repeats, result.get("top_candidate"), result.get("status")), flush=True)

    _score(document, rows, benchmark_sha, metadata)
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=2, help="repeat calls per declared repeatability case (0-3)")
    parser.add_argument("--summarize-only", action="store_true", help="recompute derived metrics from existing append-only results without API access")
    args = parser.parse_args()
    summary = run(repeats=args.repeats, summarize_only=args.summarize_only)
    print(json.dumps({
        "benchmark_sha256": summary["benchmark_sha256"],
        "case_count": summary["case_count"],
        "informative_top1": summary["top1_target_precision_informative_single_target"],
        "active_precision": summary["active_context_precision"],
        "active_coverage": summary["active_context_coverage"],
        "api_latency": summary["api_latency"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
