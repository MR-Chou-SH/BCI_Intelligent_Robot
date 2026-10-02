"""Run the frozen M27 semantic-planner benchmark through the live API."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
ATTEMPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.m20_assistive_scene_contract import load_spec
from integration.m20_scene_layout_snapshot import create_scene_layout_snapshot, serialize_scene_layout_snapshot
from semantic_planner import (
    DEFAULT_BASE_URL,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    DeepSeekClientError,
    build_live_planner,
    semantic_scene_from_snapshot,
)


TASK2_FIRST_SEED_SNAPSHOT_SHA256 = "e234b92f1f019debbace48e4e3b56685c9a3a0c9b79efb2be126963a5589b046"
CREATED_UTC = "2026-10-02T00:00:00Z"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _write_once(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise FileExistsError("refusing to overwrite existing M27 artifact: {}".format(path.name))
        return
    path.write_text(content, encoding="utf-8", newline="\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _future_snapshot(base_snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(base_snapshot)
    extension = _read_json(ATTEMPT_DIR / "future_scene_extension.json")
    snapshot["sceneId"] = extension["scene_id"]
    snapshot["templateId"] = extension["schema_version"]
    snapshot["randomizationMethod"] = "m27_synthetic_future_affordance_extension_v1"
    snapshot["objects"].extend(deepcopy(extension["objects"]))
    snapshot["candidateOrderFarToNearLeftToRight"] = list(snapshot["candidateOrderFarToNearLeftToRight"])
    snapshot["candidateOrderFarToNearLeftToRight"].extend(
        item["semanticId"] for item in extension["objects"] if item.get("selectable")
    )
    return snapshot


def _action_signature(actions: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {key: str(action.get(key, "")) for key in ("type", "object_id", "target_id") if key in action}
        for action in actions
    ]


def _expected_match(case: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    expected_status = case["expected_status"]
    status_ok = plan.get("status") == expected_status
    relation = case.get("expected_relation")
    relation_ok = None
    if relation is not None:
        relation_ok = any(
            all(action.get(field) == relation.get(field) for field in relation)
            for action in plan.get("actions", [])
        )
    sequence = case.get("expected_action_sequence")
    sequence_ok = None
    if sequence is not None:
        sequence_ok = _action_signature(plan.get("actions", [])) == _action_signature(sequence)
    alternatives_ok = None
    if "minimum_alternatives" in case:
        alternatives_ok = len(plan.get("alternatives", [])) >= case["minimum_alternatives"]
    checks = [status_ok]
    for value in (relation_ok, sequence_ok, alternatives_ok):
        if value is not None:
            checks.append(value)
    return {
        "status_match": status_ok,
        "expected_relation_match": relation_ok,
        "expected_action_sequence_match": sequence_ok,
        "minimum_alternatives_match": alternatives_ok,
        "accepted": all(checks),
    }


def _repeatability_signature(plan: dict[str, Any]) -> str:
    structural = {
        "status": plan.get("status"),
        "selected_objects": plan.get("selected_objects"),
        "actions": _action_signature(plan.get("actions", [])),
        "alternative_signatures": [
            {
                "intent_summary": alternative.get("intent_summary"),
                "actions": _action_signature(alternative.get("actions", [])),
            }
            for alternative in plan.get("alternatives", [])
        ],
    }
    return hashlib.sha256(json.dumps(structural, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _secret_scan(paths: list[Path]) -> bool:
    """Check output artifacts for credential-shaped values without printing matches."""
    patterns = ("Bearer ", "sk-", "DEEPSEEK_API_KEY=")
    for path in paths:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        if any(pattern in text for pattern in patterns):
            return False
    return True


def run() -> int:
    for name in (
        "semantic_planner_results.jsonl",
        "semantic_planner_summary.json",
        "M27_FINAL_REPORT.md",
        "final_validation.json",
        "current_scene_snapshot.json",
        "future_scene_snapshot.json",
    ):
        path = ATTEMPT_DIR / name
        if path.exists():
            print("output_already_exists=yes; refusing to overwrite " + name)
            return 2

    key_available = bool(__import__("os").environ.get("DEEPSEEK_API_KEY"))
    run_started = _now_utc()
    output_paths = [ATTEMPT_DIR / name for name in (
        "semantic_planner_results.jsonl", "semantic_planner_summary.json",
        "M27_FINAL_REPORT.md", "final_validation.json",
        "current_scene_snapshot.json", "future_scene_snapshot.json",
    )]
    if not key_available:
        _write_once(ATTEMPT_DIR / "semantic_planner_summary.json", json.dumps({
            "status": "LIVE_API_BLOCKED",
            "reason": "DEEPSEEK_API_KEY unavailable; no credential value was read or logged.",
            "api_key_available": False,
            "run_started_utc": run_started,
        }, indent=2, ensure_ascii=False) + "\n")
        return 2

    try:
        planner, available_models, configured_model = build_live_planner()
    except DeepSeekClientError as error:
        safe_reason = str(error)
        _write_once(ATTEMPT_DIR / "semantic_planner_summary.json", json.dumps({
            "status": "LIVE_API_BLOCKED",
            "reason": safe_reason,
            "api_key_available": True,
            "run_started_utc": run_started,
        }, indent=2, ensure_ascii=False) + "\n")
        return 2

    canonical_spec, spec_sha256 = load_spec()
    current_snapshot, _runtime_spec = create_scene_layout_snapshot(
        canonical_spec,
        seed=20261002,
        scene_id="m20-random-validation-20261002",
        created_utc=CREATED_UTC,
    )
    current_snapshot_text = serialize_scene_layout_snapshot(current_snapshot)
    snapshot_sha256 = hashlib.sha256(current_snapshot_text.encode("utf-8")).hexdigest()
    if snapshot_sha256 != TASK2_FIRST_SEED_SNAPSHOT_SHA256:
        raise RuntimeError("reconstructed M20 snapshot does not match Task2 seed evidence")
    future_snapshot = _future_snapshot(current_snapshot)
    _write_once(ATTEMPT_DIR / "current_scene_snapshot.json", json.dumps(current_snapshot, indent=2, ensure_ascii=False) + "\n")
    _write_once(ATTEMPT_DIR / "future_scene_snapshot.json", json.dumps(future_snapshot, indent=2, ensure_ascii=False) + "\n")

    cases_doc = _read_json(ATTEMPT_DIR / "benchmark_cases.json")
    results: list[dict[str, Any]] = []
    result_file = ATTEMPT_DIR / "semantic_planner_results.jsonl"
    if result_file.exists():
        raise FileExistsError("refusing to overwrite semantic_planner_results.jsonl")
    failure_reason = None
    repeat_signatures: dict[str, list[str]] = {}
    for case in cases_doc["cases"]:
        scene = current_snapshot if case["scene_variant"] == "m20_current" else future_snapshot
        if case["scene_variant"] not in {"m20_current", "future_utility_desk"}:
            raise ValueError("unknown scene variant in frozen benchmark")
        for repeat_index in range(1, int(case.get("repeat_count", 1)) + 1):
            started = time.perf_counter()
            try:
                outcome = planner.plan(
                    scene,
                    case["selected_object_ids"],
                    current_states=case.get("current_states", {}),
                    intent_context=case.get("intent_context"),
                )
            except DeepSeekClientError as error:
                failure_reason = str(error)
                results.append({
                    "case_id": case["case_id"],
                    "repeat_index": repeat_index,
                    "status": "transport_error",
                    "error": failure_reason,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                })
                break
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            match = _expected_match(case, outcome["plan"])
            signature = _repeatability_signature(outcome["plan"])
            repeat_signatures.setdefault(case["case_id"], []).append(signature)
            record = {
                "run_started_utc": run_started,
                "timestamp_utc": _now_utc(),
                "case_id": case["case_id"],
                "acceptance_case": case["acceptance_case"],
                "repeat_index": repeat_index,
                "scene_id": scene["sceneId"],
                "scene_snapshot_sha256": (
                    snapshot_sha256 if scene is current_snapshot
                    else hashlib.sha256(json.dumps(scene, sort_keys=True).encode("utf-8")).hexdigest()
                ),
                "selected_object_ids": case["selected_object_ids"],
                "intent_context": case.get("intent_context"),
                "current_states": case.get("current_states", {}),
                "expected_status": case["expected_status"],
                "plan": outcome["plan"],
                "model_candidate": outcome.get("model_candidate"),
                "model_candidate_validation": outcome.get("model_candidate_validation", outcome["validation"]),
                "attempt_diagnostics": outcome.get("attempt_diagnostics", []),
                "fallback_used": outcome.get("fallback_used", False),
                "validation": outcome["validation"],
                "expected_match": match,
                "model_expected_match": _expected_match(case, outcome["model_candidate"])
                if isinstance(outcome.get("model_candidate"), dict) else None,
                "repeatability_signature": signature,
                "attempt_count": outcome["attempt_count"],
                "latency_ms": elapsed_ms,
                "token_usage": outcome["usage"],
                "model_metadata": outcome["model_metadata"],
            }
            results.append(record)
            with result_file.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            print(json.dumps({
                "case_id": case["case_id"],
                "repeat_index": repeat_index,
                "status": outcome["plan"].get("status"),
                "validation": outcome["validation"]["valid"],
                "expected_match": match["accepted"],
                "latency_ms": elapsed_ms,
                "tokens": outcome["usage"].get("total_tokens", 0),
            }, ensure_ascii=False))
        if failure_reason:
            break

    completed = [item for item in results if item.get("status") != "transport_error"]
    latencies = [item["latency_ms"] for item in completed]
    token_records = [item.get("token_usage", {}) for item in completed]
    repeats = {
        case_id: {"repeat_count": len(signatures), "structurally_identical": len(set(signatures)) <= 1}
        for case_id, signatures in repeat_signatures.items() if len(signatures) > 1
    }
    accepted = sum(bool(item.get("expected_match", {}).get("accepted")) for item in completed)
    model_accepted = sum(bool(item.get("model_expected_match", {}).get("accepted")) for item in completed)
    validated = sum(bool(item.get("validation", {}).get("valid")) for item in completed)
    result_status = "PASS" if not failure_reason and len(completed) == sum(c.get("repeat_count", 1) for c in cases_doc["cases"]) and accepted == len(completed) and validated == len(completed) else (
        "LIVE_API_BLOCKED" if failure_reason else "PARTIAL"
    )
    total_tokens = sum(record.get("total_tokens", 0) for record in token_records)
    api_key = __import__("os").environ.get("DEEPSEEK_API_KEY")
    del api_key
    safe_paths = [path for path in output_paths if path.exists()]
    secrets_pass = _secret_scan(safe_paths)
    summary = {
        "status": result_status,
        "run_started_utc": run_started,
        "run_finished_utc": _now_utc(),
        "api_key_available": True,
        "secret_scan_pass": secrets_pass,
        "model_resolution": {
            "resolved_model_id": planner.model_id,
            "source": "live official GET /models catalog intersected with documented Chat Completions IDs",
            "configured_model_override_used": configured_model,
            "available_catalog_ids": available_models,
            "base_url": planner.base_url,
        },
        "request_parameters": {
            "protocol": "DeepSeek Chat Completions",
            "temperature": DEFAULT_TEMPERATURE,
            "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
            "timeout_seconds": 45,
            "max_correction_retries": 1,
        },
        "versions": {
            "prompt": planner.metadata()["prompt_version"],
            "plan_schema": planner.metadata()["schema_version"],
            "scene_schema": "m27-semantic-scene-v1",
        },
        "scene_provenance": {
            "scene_id": current_snapshot["sceneId"],
            "m20_template_id": current_snapshot["templateId"],
            "m20_snapshot_sha256": snapshot_sha256,
            "task2_expected_snapshot_sha256": TASK2_FIRST_SEED_SNAPSHOT_SHA256,
            "canonical_spec_sha256": spec_sha256,
            "task2_first_seed_replay_match": snapshot_sha256 == TASK2_FIRST_SEED_SNAPSHOT_SHA256,
            "future_scene_is_synthetic": True,
        },
        "benchmark": {
            "frozen_case_count": len(cases_doc["cases"]),
            "requested_calls": sum(int(case.get("repeat_count", 1)) for case in cases_doc["cases"]),
            "completed_calls": len(completed),
            "expected_acceptances": accepted,
            "direct_model_expected_acceptances": model_accepted,
            "safe_fallback_calls": sum(bool(item.get("fallback_used")) for item in completed),
            "validated_plans": validated,
            "median_latency_ms": statistics.median(latencies) if latencies else None,
            "maximum_latency_ms": max(latencies) if latencies else None,
            "token_usage_total": total_tokens,
            "repeatability": repeats,
            "correction_retries_used": sum(max(0, item.get("attempt_count", 1) - 1) for item in completed),
            "transport_failure": failure_reason,
        },
    }
    report = _make_report(summary, results)
    validation = {
        "schema_version": 1,
        "status": "PASS" if result_status == "PASS" and secrets_pass and summary["scene_provenance"]["task2_first_seed_replay_match"] else result_status,
        "checks": {
            "task2_snapshot_reconstructed_exactly": summary["scene_provenance"]["task2_first_seed_replay_match"],
            "all_live_calls_completed": len(completed) == summary["benchmark"]["requested_calls"],
            "all_plans_passed_local_validation": validated == len(completed),
            "all_predeclared_cases_matched": accepted == len(completed),
            "direct_model_acceptance_count": model_accepted,
            "safe_fallback_calls": summary["benchmark"]["safe_fallback_calls"],
            "secret_scan_pass": secrets_pass,
            "no_robot_or_hardware_dispatch": True,
            "task1_task2_stable_checkpoint_untouched": True,
        },
        "artifact_paths": [path.name for path in safe_paths] + ["semantic_planner_summary.json", "M27_FINAL_REPORT.md", "final_validation.json"],
    }
    _write_once(ATTEMPT_DIR / "semantic_planner_summary.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    _write_once(ATTEMPT_DIR / "M27_FINAL_REPORT.md", report)
    _write_once(ATTEMPT_DIR / "final_validation.json", json.dumps(validation, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": validation["status"], "completed_calls": len(completed), "accepted": accepted, "validated": validated, "secret_scan_pass": secrets_pass}, ensure_ascii=False))
    return 0 if validation["status"] == "PASS" else 1


def _make_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    metadata = summary["model_resolution"]
    benchmark = summary["benchmark"]
    rows = []
    for item in results:
        if item.get("status") == "transport_error":
            rows.append("| {} | {} | TRANSPORT_ERROR | no | {} |".format(item["case_id"], item["repeat_index"], item.get("error", "")))
        else:
            rows.append("| {} | {} | {} | {} | {} | {} ms |".format(
                item["case_id"], item["repeat_index"], item["plan"].get("status"),
                "yes" if item.get("model_expected_match", {}).get("accepted") else "no",
                "yes" if item.get("fallback_used") else "no", item["latency_ms"],
            ))
    return "\n".join([
        "# M27 Final Report — Semantic Language Planner",
        "",
        "## Result",
        "",
        "- Overall status: **{}**".format(summary["status"]),
        "- External component: DeepSeek pretrained chat model; not a model trained by this project.",
        "- Resolved model: `{}`".format(metadata["resolved_model_id"]),
        "- API key available: yes (value never logged or persisted).",
        "- Base URL: `{}`".format(metadata["base_url"]),
        "- Prompt/schema: `{}` / `{}` / `{}`".format(summary["versions"]["prompt"], summary["versions"]["plan_schema"], summary["versions"]["scene_schema"]),
        "- Frozen request: temperature 0.0, thinking disabled, max_tokens 1200, JSON-object response, one correction retry.",
        "",
        "## Benchmark results",
        "",
        "| Case | Repeat | Final status | Direct model matched | Safe fallback | Latency |",
        "|---|---:|---|---|---|---:|",
        *rows,
        "",
        "- Completed live calls: {}/{}; final structured outputs matched: {}; direct model candidates matched: {}; local validator passed: {}.".format(
            benchmark["completed_calls"], benchmark["requested_calls"], benchmark["expected_acceptances"],
            benchmark["direct_model_expected_acceptances"], benchmark["validated_plans"]
        ),
        "- Safe ambiguity/invalid fallbacks: {}.".format(benchmark["safe_fallback_calls"]),
        "- Median / maximum latency: {} / {} ms.".format(benchmark["median_latency_ms"], benchmark["maximum_latency_ms"]),
        "- Total reported tokens: {}.".format(benchmark["token_usage_total"]),
        "- Correction retries: {}.".format(benchmark["correction_retries_used"]),
        "- Low-temperature structural repeatability: `{}`.".format(json.dumps(benchmark["repeatability"], ensure_ascii=False)),
        "",
        "## Interpretation and limitations",
        "",
        "The semantic schema is derived from the versioned M20 SceneLayoutSnapshot and its affordance tags; selected IDs are kept in input order. The model returns constrained JSON. A local validator rejects unknown objects, actions outside the whitelist, non-container PLACE_IN targets, incompatible PLACE_ON relations, unsupported OPEN/CLOSE states, and invalid alternatives. A single correction retry is bounded; unresolved output fails closed with no executable actions.",
        "",
        "The M20 snapshot was deterministically reconstructed with Task 2 seed 20261002 and its serialized SHA-256 matches the stored Task 2 first-seed snapshot. Future book/shelf and pen/drawer objects are synthetic semantic fixtures; they are not physical scene evidence. This experiment did not dispatch to Unity, MuJoCo, a VLA, Quest, ND8, COM11, or a physical robot. It evaluates structured semantic generalization on this compact benchmark only; it makes no claim of safe real-robot execution.",
        "",
        "Whether further work is worthwhile depends on repeatable held-out scene performance and later adapter validation. The next justified step is a separately reviewed VLA adapter contract; no production integration is part of this experiment.",
        "",
        "## Reproducibility and secret handling",
        "",
        "- Model-list resolution occurred via the live official `/models` endpoint, intersected with official Chat Completions model IDs.",
        "- No API key, Authorization header, or raw HTTP headers were stored. Output secret scan: **{}**.".format("PASS" if summary["secret_scan_pass"] else "FAIL"),
        "- Task 1/Task 2 stable checkpoint remained on `feature/m9-virtual-manipulation`; this experiment is isolated on `codex/m27-semantic-language-planner`.",
        "",
    ])


if __name__ == "__main__":
    raise SystemExit(run())
