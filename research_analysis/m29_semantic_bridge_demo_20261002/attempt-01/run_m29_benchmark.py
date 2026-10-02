"""Run the frozen M29 semantic bridge benchmark and score only after each call."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BENCHMARK = OUT / "semantic_bridge_benchmark.json"
LOCK = OUT / "semantic_bridge_benchmark_lock.json"
RESULTS = OUT / "semantic_bridge_results.jsonl"
SUMMARY = OUT / "semantic_bridge_summary.json"
REPEATABILITY = OUT / "semantic_bridge_repeatability.json"
REPEAT_CASE_IDS = {
    "m20_phone_charger_zh", "m20_ambiguous_relations", "m20_medicine_closed_box_zh",
    "book_to_bookshelf_en", "cup_on_serving_tray", "cup_ambiguous_put_away",
    "pen_to_closed_drawer", "free_watch_tray",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_locked_benchmark() -> tuple[dict[str, Any], str]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    digest = hashlib.sha256(BENCHMARK.read_bytes()).hexdigest()
    if digest != lock.get("sha256"):
        raise RuntimeError("M29 benchmark hash does not match its freeze lock")
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    if len(benchmark.get("cases", [])) != int(lock.get("case_count", -1)):
        raise RuntimeError("M29 benchmark case count does not match its freeze lock")
    if benchmark.get("case_count") != len(benchmark.get("cases", [])):
        raise RuntimeError("M29 benchmark has an inconsistent case_count")
    return benchmark, digest


def _already_done() -> set[tuple[str, str, int]]:
    done: set[tuple[str, str, int]] = set()
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("record_type") == "M29_SEMANTIC_BRIDGE_BENCHMARK_CALL":
                    done.add((row["case_id"], row["call_kind"], int(row["replicate"])))
    return done


def _append_result(row: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def _action_signature(actions: Any) -> list[str]:
    if not isinstance(actions, list):
        return []
    output = []
    for action in actions:
        if not isinstance(action, dict):
            output.append("<malformed>")
        elif action.get("target_id"):
            output.append("{}:{}:{}".format(action.get("type", ""), action.get("object_id", ""), action.get("target_id", "")))
        else:
            output.append("{}:{}".format(action.get("type", ""), action.get("object_id", "")))
    return output


def _score(case: dict[str, Any], result: dict[str, Any] | None, error_type: str | None) -> dict[str, Any]:
    expected = case["evaluation_only"]
    if result is None:
        return {
            "api_success": False, "schema_valid": False, "expected_status_match": False,
            "action_sequence_match": False, "target_grounding_match": False,
            "validator_pass": False, "ambiguity_policy_pass": False,
            "invalid_rejection_pass": False, "free_mode_safety_pass": False,
            "error_type": error_type,
        }
    status = result.get("status")
    actions = result.get("actions")
    grounded = result.get("grounded_objects")
    schema_valid = (
        status in {"executable", "ambiguous", "invalid"}
        and isinstance(actions, list) and isinstance(grounded, list)
        and isinstance(result.get("vla_instruction_zh"), str)
        and isinstance(result.get("vla_instruction_en"), str)
        and result.get("dispatch_allowed") is False
    )
    expected_status = expected.get("expected_status")
    status_match = status == expected_status if expected_status != "any_safe_status" else schema_valid
    expected_actions = expected.get("expected_action_sequence")
    actions_match = (
        _action_signature(actions) == expected_actions
        if expected_actions is not None else (status != "executable" or bool(actions))
    )
    expected_targets = set(expected.get("expected_target_ids", []))
    output_ids = {item.get("object_id") for item in grounded if isinstance(item, dict)}
    output_ids.update(
        action.get("target_id") for action in actions
        if isinstance(action, dict) and action.get("target_id")
    )
    validation = result.get("validation")
    validator_pass = isinstance(validation, dict) and validation.get("valid") is True
    ambiguity_pass = status != "ambiguous" or (not actions and len(result.get("alternatives", [])) >= 2)
    invalid_pass = status != "invalid" or not actions
    if case["model_input"].get("mode") == "free":
        inferred = result.get("free_noun_inference") or {}
        free_safety = (
            result.get("dispatch_allowed") is False
            and result.get("mode") in {"free_noun_unverified", "free_noun_scene_grounded"}
            and (result.get("mode") != "free_noun_unverified" or inferred.get("trust") == "model_inferred_unverified")
            and all(item.get("trust") == "model_inferred_unverified" for item in grounded)
        )
    else:
        free_safety = True
    inference_errors = result.get("inference_diagnostics", [])
    inference_transport_error = next(
        (item.split(":", 1)[1].strip() for item in inference_errors if "inference failed:" in item),
        None,
    )
    api_error_type = error_type or inference_transport_error
    return {
        "api_success": api_error_type is None, "schema_valid": schema_valid,
        "expected_status_match": status_match, "action_sequence_match": actions_match,
        "target_grounding_match": expected_targets.issubset(output_ids),
        "validator_pass": validator_pass, "ambiguity_policy_pass": ambiguity_pass,
        "invalid_rejection_pass": invalid_pass, "free_mode_safety_pass": free_safety,
        "error_type": api_error_type,
    }


def _append_score_corrections(benchmark: dict[str, Any]) -> None:
    case_by_id = {case["case_id"]: case for case in benchmark["cases"]}
    lines = RESULTS.read_text(encoding="utf-8").splitlines() if RESULTS.exists() else []
    corrections: dict[tuple[str, str, int], dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") == "M29_SEMANTIC_BRIDGE_BENCHMARK_CALL":
            calls.append(row)
        elif row.get("record_type") == "M29_SEMANTIC_BRIDGE_SCORE_CORRECTION":
            key = (row["case_id"], row["call_kind"], int(row["replicate"]))
            corrections[key] = row
    for row in calls:
        key = (row["case_id"], row["call_kind"], int(row["replicate"]))
        current_version = corrections.get(key, {}).get("score_version", row.get("score_version", 1))
        if current_version >= 2:
            continue
        prior_error = row.get("score", {}).get("error_type")
        correction = {
            "record_type": "M29_SEMANTIC_BRIDGE_SCORE_CORRECTION",
            "timestamp_utc": _utc_now(),
            "benchmark_sha256": row["benchmark_sha256"],
            "case_id": row["case_id"], "call_kind": row["call_kind"],
            "replicate": int(row["replicate"]), "score_version": 2,
            "reason": "Corrected action signature serialization and API failure classification; model input and response unchanged.",
            "score": _score(case_by_id[row["case_id"]], row.get("result"), prior_error),
        }
        _append_result(correction)


def _run_one(bridge: Any, case: dict[str, Any], *, call_kind: str, replicate: int, benchmark_sha: str) -> dict[str, Any]:
    model_input = case["model_input"]
    kwargs = {
        "snapshot": model_input["scene"],
        "nouns": list(model_input["nouns"]),
        "mode": model_input.get("mode", "strict"),
        "intent_context": model_input.get("intent_context"),
        "current_states": model_input.get("current_states") or None,
    }
    started = time.perf_counter()
    result = None
    error_type = None
    transport_retry_count = 0
    for attempt in range(2):
        try:
            result = bridge.plan_nouns(**kwargs)
            error_type = None
            break
        except Exception as error:
            error_type = type(error).__name__
            if attempt == 0:
                transport_retry_count = 1
    latency_ms = (time.perf_counter() - started) * 1000.0
    score = _score(case, result, error_type)
    inference = (result or {}).get("free_noun_inference") or {}
    failed_inference = (
        (result or {}).get("reason_code") == "free_noun_affordance_inference_failed"
        and any("inference failed:" in item for item in (result or {}).get("inference_diagnostics", []))
    )
    planner_requests = int((result or {}).get("attempt_count", 0))
    inference_requests = int(inference.get("inference_attempt_count", 0))
    if failed_inference:
        planner_requests = 0
        inference_requests = int((result or {}).get("attempt_count", 0))
    row = {
        "record_type": "M29_SEMANTIC_BRIDGE_BENCHMARK_CALL",
        "timestamp_utc": _utc_now(),
        "benchmark_id": "m29-standalone-semantic-language-bridge-v1",
        "benchmark_sha256": benchmark_sha,
        "case_id": case["case_id"], "scene_split": case["scene_split"],
        "scene_family": case["scene_family"], "call_kind": call_kind,
        "replicate": replicate, "latency_ms": round(latency_ms, 3),
        "transport_retry_count": transport_retry_count,
        "model_api_request_estimate": planner_requests + inference_requests,
        "schema_correction_retry_count": max(planner_requests - 1, 0) + max(inference_requests - 1, 0),
        "token_usage": (result or {}).get("token_usage", {}),
        "attempt_count": (result or {}).get("attempt_count", 0),
        "fallback_used": (result or {}).get("fallback_used", False),
        "score_version": 2, "score": score, "result": result,
    }
    _append_result(row)
    print("{} {} success={} {:.0f}ms".format(case["case_id"], call_kind, score["api_success"], latency_ms), flush=True)
    return row


def _rate(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    count = sum(bool(row["score"].get(key)) for row in rows)
    return {"count": count, "denominator": len(rows), "rate": count / len(rows) if rows else None}


def _consistency_signature(row: dict[str, Any]) -> dict[str, Any]:
    result = row.get("result") or {}
    return {
        "status": result.get("status"), "mode": result.get("mode"),
        "grounded_objects": [item.get("object_id") for item in result.get("grounded_objects", []) if isinstance(item, dict)],
        "actions": _action_signature(result.get("actions")),
        "alternatives": [_action_signature(item.get("actions")) for item in result.get("alternatives", []) if isinstance(item, dict)],
        "vla_instruction_zh": result.get("vla_instruction_zh"),
        "vla_instruction_en": result.get("vla_instruction_en"),
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def summarize(benchmark: dict[str, Any], benchmark_sha: str) -> dict[str, Any]:
    existing_model = {}
    if SUMMARY.is_file():
        try:
            existing_model = json.loads(SUMMARY.read_text(encoding="utf-8")).get("model", {})
        except (OSError, json.JSONDecodeError):
            existing_model = {}
    _append_score_corrections(benchmark)
    calls = []
    corrections = {}
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("record_type") == "M29_SEMANTIC_BRIDGE_BENCHMARK_CALL":
            calls.append(row)
        elif row.get("record_type") == "M29_SEMANTIC_BRIDGE_SCORE_CORRECTION":
            corrections[(row["case_id"], row["call_kind"], int(row["replicate"]))] = row
    all_rows = []
    for row in calls:
        key = (row["case_id"], row["call_kind"], int(row["replicate"]))
        latest = corrections.get(key)
        if latest:
            row["score"] = latest["score"]
            row["score_version"] = latest["score_version"]
        all_rows.append(row)
    primary = [row for row in all_rows if row["call_kind"] == "primary"]
    repeats = [row for row in all_rows if row["call_kind"] == "repeat"]
    expected_calls = len(benchmark["cases"]) + 2 * len(REPEAT_CASE_IDS)
    ids = {(row["case_id"], row["call_kind"], int(row["replicate"])) for row in all_rows}
    missing = []
    for case in benchmark["cases"]:
        key = (case["case_id"], "primary", 0)
        if key not in ids:
            missing.append(key)
        if case["case_id"] in REPEAT_CASE_IDS:
            for replicate in (1, 2):
                key = (case["case_id"], "repeat", replicate)
                if key not in ids:
                    missing.append(key)
    status_counts = Counter((row.get("result") or {}).get("status", "api_error") for row in primary)
    split_metrics = {}
    for split in sorted({row["scene_split"] for row in primary}):
        group = [row for row in primary if row["scene_split"] == split]
        split_metrics[split] = {
            "case_count": len(group), "api_success": _rate(group, "api_success"),
            "schema_valid": _rate(group, "schema_valid"),
            "expected_status_match": _rate(group, "expected_status_match"),
            "action_sequence_match": _rate(group, "action_sequence_match"),
            "validator_pass": _rate(group, "validator_pass"),
        }
    repeat_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in all_rows:
        if row["case_id"] in REPEAT_CASE_IDS:
            repeat_groups[row["case_id"]].append(row)
    repeat_summary = {}
    for case_id, group in sorted(repeat_groups.items()):
        signatures = [_consistency_signature(row) for row in group]
        structural = [
            {key: value for key, value in item.items() if key not in {"vla_instruction_zh", "vla_instruction_en"}}
            for item in signatures
        ]
        prose = [(item["vla_instruction_zh"], item["vla_instruction_en"]) for item in signatures]
        repeat_summary[case_id] = {
            "call_count": len(group),
            "structural_consistent": bool(structural) and all(item == structural[0] for item in structural[1:]),
            "exact_prose_consistent": bool(prose) and all(item == prose[0] for item in prose[1:]),
            "status_sequence": [item["status"] for item in signatures],
            "action_sequence": [item["actions"] for item in signatures],
            "grounding_sequence": [item["grounded_objects"] for item in signatures],
        }
    complete_repeat = [item for item in repeat_summary.values() if item["call_count"] == 3]
    latencies = [float(row["latency_ms"]) for row in primary if row.get("latency_ms") is not None]
    api_estimate = sum(int(row.get("model_api_request_estimate", 0)) for row in all_rows)
    correction_retries = sum(int(row.get("schema_correction_retry_count", 0)) for row in all_rows)
    api_failure_count = sum(
        row["score"].get("error_type") is not None
        or (
            (row.get("result") or {}).get("reason_code") == "free_noun_affordance_inference_failed"
            and any("inference failed:" in item for item in (row.get("result") or {}).get("inference_diagnostics", []))
        )
        for row in all_rows
    )
    input_contract_error_count = sum(
        row["score"].get("error_type") == "PlannerInputError" for row in all_rows
    )
    api_failure_count -= input_contract_error_count
    usage = {
        key: sum(int((row.get("token_usage") or {}).get(key, 0)) for row in all_rows)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }
    free_rows = [
        row for row in primary
        if next(case for case in benchmark["cases"] if case["case_id"] == row["case_id"])["model_input"].get("mode") == "free"
    ]
    summary = {
        "record_type": "M29_SEMANTIC_BRIDGE_BENCHMARK_SUMMARY",
        "generated_at_utc": _utc_now(),
        "benchmark_id": "m29-standalone-semantic-language-bridge-v1",
        "benchmark_sha256": benchmark_sha, "case_count": len(benchmark["cases"]),
        "primary_call_count": len(primary), "repeat_call_count": len(repeats),
        "expected_total_call_count": expected_calls, "missing_call_count": len(missing),
        "missing_calls": [list(item) for item in missing], "complete": not missing,
        "result_status_counts": dict(status_counts),
        "primary_metrics": {
            "api_success": _rate(primary, "api_success"),
            "schema_valid": _rate(primary, "schema_valid"),
            "expected_status_match": _rate(primary, "expected_status_match"),
            "exact_action_sequence_match": _rate(primary, "action_sequence_match"),
            "target_grounding_match": _rate(primary, "target_grounding_match"),
            "strict_validator_pass": _rate(primary, "validator_pass"),
            "ambiguity_policy_pass": _rate(primary, "ambiguity_policy_pass"),
            "invalid_rejection_pass": _rate(primary, "invalid_rejection_pass"),
            "free_mode_safety_pass": _rate(free_rows, "free_mode_safety_pass"),
        },
        "scene_split_metrics": split_metrics,
        "repeatability": {
            "selected_case_count": len(REPEAT_CASE_IDS),
            "repeat_call_count": len(repeats),
            "complete_case_count": len(complete_repeat),
            "structural_consistent_case_count": sum(item["structural_consistent"] for item in complete_repeat),
            "exact_prose_consistent_case_count": sum(item["exact_prose_consistent"] for item in complete_repeat),
            "cases": repeat_summary,
        },
        "latency_primary_ms": {
            "n": len(latencies), "mean": statistics.mean(latencies) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "p90": _percentile(latencies, 0.90), "p95": _percentile(latencies, 0.95),
        },
        "schema_correction_retry_count": correction_retries,
        "schema_correction_retry_rate": correction_retries / api_estimate if api_estimate else None,
        "api_failure_count": api_failure_count,
        "input_contract_error_count": input_contract_error_count,
        "model_api_request_estimate": api_estimate, "token_usage_total": usage,
        "evaluation_boundary": "Free-noun cases are scored for unverified labels, schema, and dispatch prohibition; inferred physical affordances are not validated as facts.",
    }
    if existing_model:
        summary["model"] = existing_model
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPEATABILITY.write_text(json.dumps(summary["repeatability"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def run(resume: bool = False) -> dict[str, Any]:
    benchmark, benchmark_sha = _read_locked_benchmark()
    if (RESULTS.exists() or SUMMARY.exists() or REPEATABILITY.exists()) and not resume:
        raise FileExistsError("M29 result outputs exist; pass --resume to continue the append-only run")
    from integration.semantic_intelligence import SemanticLanguageBridge, SemanticPlanner, build_live_client

    client, model_id, available_models, configured = build_live_client()
    base_url = client.base_url
    planner = SemanticPlanner(client, model_id, base_url=base_url, max_correction_retries=1)
    bridge = SemanticLanguageBridge(planner)
    print("M29 benchmark: {} cases, model {}, available {}".format(len(benchmark["cases"]), model_id, available_models), flush=True)
    done = _already_done()
    for case in benchmark["cases"]:
        if (case["case_id"], "primary", 0) not in done:
            _run_one(bridge, case, call_kind="primary", replicate=0, benchmark_sha=benchmark_sha)
        if case["case_id"] in REPEAT_CASE_IDS:
            for replicate in (1, 2):
                if (case["case_id"], "repeat", replicate) not in done:
                    _run_one(bridge, case, call_kind="repeat", replicate=replicate, benchmark_sha=benchmark_sha)
    summary = summarize(benchmark, benchmark_sha)
    summary["model"] = {
        "model_id": model_id, "base_url": base_url,
        "available_chat_models": available_models,
        "model_configured_by_environment": configured,
        "prompt_version": planner.metadata()["prompt_version"],
        "schema_version": planner.metadata()["schema_version"],
        "temperature": planner.temperature,
        "max_correction_retries": planner.max_correction_retries,
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="continue by skipping calls already recorded in append-only JSONL")
    args = parser.parse_args(argv)
    try:
        summary = run(resume=args.resume)
    except Exception as error:
        print("M29 benchmark stopped: {}".format(type(error).__name__), file=sys.stderr)
        return 2
    print(json.dumps({
        "complete": summary["complete"], "primary_metrics": summary["primary_metrics"],
        "scene_split_metrics": summary["scene_split_metrics"],
        "repeatability": summary["repeatability"],
        "latency_primary_ms": summary["latency_primary_ms"],
        "api_request_estimate": summary["model_api_request_estimate"],
        "schema_correction_retry_count": summary["schema_correction_retry_count"],
        "schema_correction_retry_rate": summary["schema_correction_retry_rate"],
        "api_failure_count": summary["api_failure_count"],
        "input_contract_error_count": summary["input_contract_error_count"],
        "token_usage_total": summary["token_usage_total"],
    }, ensure_ascii=False, indent=2))
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
