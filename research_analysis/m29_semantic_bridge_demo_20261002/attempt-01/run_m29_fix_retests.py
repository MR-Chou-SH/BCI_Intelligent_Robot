"""Retest M29 cases affected by post-benchmark input/grounding corrections."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.semantic_intelligence import SemanticLanguageBridge, SemanticPlanner, build_live_client

import run_m29_benchmark as benchmark_runner

OUT = Path(__file__).resolve().parent
OUTPUT = OUT / "semantic_bridge_fix_retest.json"
CASE_IDS = (
    "m20_button_press_zh",
    "m20_button_press_en",
    "m20_invalid_press_medicine",
)


def run() -> dict:
    if OUTPUT.exists():
        raise FileExistsError("M29 fix-retest output already exists")
    benchmark, digest = benchmark_runner._read_locked_benchmark()
    cases = {case["case_id"]: case for case in benchmark["cases"]}
    client, model_id, available_models, configured = build_live_client()
    planner = SemanticPlanner(client, model_id, base_url=client.base_url, max_correction_retries=1)
    bridge = SemanticLanguageBridge(planner)
    records = []
    for case_id in CASE_IDS:
        case = cases[case_id]
        inputs = case["model_input"]
        started = time.perf_counter()
        result = bridge.plan_nouns(
            inputs["scene"], inputs["nouns"], mode=inputs.get("mode", "strict"),
            intent_context=inputs.get("intent_context"),
            current_states=inputs.get("current_states") or None,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        score = benchmark_runner._score(case, result, None)
        record = {
            "case_id": case_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "expected_status": case["evaluation_only"]["expected_status"],
            "expected_action_sequence": case["evaluation_only"].get("expected_action_sequence"),
            "latency_ms": round(elapsed_ms, 3),
            "score": score,
            "result": result,
        }
        records.append(record)
        print("{} status={} actions={} expected_status_match={}".format(
            case_id, result.get("status"), len(result.get("actions", [])), score["expected_status_match"]
        ), flush=True)
    output = {
        "record_type": "M29_POST_FIX_TARGETED_LIVE_RETEST",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "benchmark_id": benchmark["benchmark_id"],
        "benchmark_sha256": digest,
        "model": {
            "model_id": model_id,
            "base_url": client.base_url,
            "available_chat_models": available_models,
            "model_configured_by_environment": configured,
            "prompt_version": planner.metadata()["prompt_version"],
            "schema_version": planner.metadata()["schema_version"],
        },
        "records": records,
        "all_targeted_checks_pass": all(
            item["score"]["api_success"]
            and item["score"]["schema_valid"]
            and item["score"]["expected_status_match"]
            and item["score"]["action_sequence_match"]
            and item["score"]["validator_pass"]
            and item["score"]["invalid_rejection_pass"]
            for item in records
        ),
        "boundary": "Targeted live retests of input handling only; they are supplemental and do not replace the original frozen benchmark results.",
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
