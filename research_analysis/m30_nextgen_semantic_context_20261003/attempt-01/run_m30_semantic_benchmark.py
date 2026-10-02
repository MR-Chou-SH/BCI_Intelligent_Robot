"""Run the frozen M30 benchmark against the shared environment-only DeepSeek client."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from integration.semantic_context_engine import SemanticContextEngine
from integration.semantic_context_relations import RELATION_TYPES, relation_compatibility
from integration.semantic_context_sequence import (
    M30_CONTEXT_ENGINE_VERSION,
    M30_CONTEXT_PROMPT_VERSION,
    build_live_sequence_context_engine,
    project_all_pages,
)
from validate_m30_benchmark import OUT, validate


RESULTS = OUT / "semantic_context_results.jsonl"
REPEATABILITY = OUT / "semantic_context_repeatability.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()


def _local_measurements(model_input: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    objects = {item["id"]: item for item in model_input["scene"]["objects"]}
    candidate_rows = result.get("candidate_scores", [])

    started = time.perf_counter()
    relation_checks = 0
    compatibility_rejections = 0
    history = model_input["selection_history"]
    for source_id in history:
        for candidate_id in model_input["candidate_next_object_ids"]:
            for relation in RELATION_TYPES:
                compatible, _ = relation_compatibility(relation, objects.get(source_id), objects[candidate_id])
                relation_checks += 1
                compatibility_rejections += int(not compatible)
    relation_validation_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    scores = [float(row["semantic_score"]) for row in candidate_rows
              if isinstance(row.get("semantic_score"), (int, float))]
    if scores:
        scaled = [score / 0.20 for score in scores]
        high = max(scaled)
        weights = [math.exp(value - high) for value in scaled]
        total = sum(weights)
        rebuilt_prior = [value / total for value in weights]
    else:
        rebuilt_prior = []
    q_construction_ms = (time.perf_counter() - started) * 1000.0

    started = time.perf_counter()
    try:
        projections = project_all_pages(result.get("q_global", [])) if result.get("q_global") else []
    except (ValueError, TypeError, KeyError):
        projections = []
    page_projection_ms = (time.perf_counter() - started) * 1000.0
    return {
        "deterministicRelationValidationMs": round(relation_validation_ms, 6),
        "deterministicRelationChecks": relation_checks,
        "deterministicRelationRejections": compatibility_rejections,
        "qConstructionReplayMs": round(q_construction_ms, 6),
        "qConstructionReplayMatches": (
            len(rebuilt_prior) == len(result.get("context_prior", []))
            and all(math.isclose(a, b, abs_tol=1e-10)
                    for a, b in zip(rebuilt_prior, result.get("context_prior", [])))
        ),
        "allPageProjectionMs": round(page_projection_ms, 6),
        "pageCount": len(projections),
    }


def _score_row(
    episode: dict[str, Any], point: dict[str, Any], result: dict[str, Any],
    model_input_sha256: str, local: dict[str, Any], *, call_kind: str = "primary",
) -> dict[str, Any]:
    labels = point["evaluation_only"]
    ranking = result.get("candidate_ranking", [])
    top_id = result.get("top_candidate")
    ranked_top_id = top_id or (ranking[0] if ranking else None)
    accepted = labels.get("acceptable_next_targets", [])
    relations = labels.get("acceptable_relations_by_target", {}).get(ranked_top_id, []) if ranked_top_id else []
    top_row = next((row for row in result.get("candidate_scores", []) if row.get("candidate_id") == ranked_top_id), None)
    diagnostics = result.get("provenance", {}).get("diagnostics", [])
    all_validation_errors = [
        error for item in diagnostics for error in item.get("validation_errors", [])
    ]
    return {
        "recordType": "m30_semantic_context_result",
        "callKind": call_kind,
        "episodeId": episode["episode_id"],
        "roundIndex": point["round_index"],
        "sceneFamily": episode["scene_family"],
        "split": episode["split"],
        "modelInputSha256": model_input_sha256,
        "selectionHistory": point["model_input"]["selection_history"],
        "candidateCount": len(point["model_input"]["candidate_next_object_ids"]),
        "expectedStatus": labels["expected_status"],
        "acceptableNextTargets": accepted,
        "expectedRelationsForTop": relations,
        "predictedStatus": result.get("status"),
        "reasonCode": result.get("reason_code"),
        "topCandidate": top_id,
        "rankedTopCandidate": ranked_top_id,
        "candidateRanking": ranking,
        "top1Correct": ranked_top_id in accepted if accepted else None,
        "top3Recall": bool(set(ranking[:3]).intersection(accepted)) if accepted else None,
        "relationTypeCorrect": top_row.get("relation_type") in relations if top_row and relations else None,
        "expectedStatusMatch": result.get("status") == labels["expected_status"],
        "eligibleInformative": result.get("eligible_informative", False),
        "contextPrior": result.get("context_prior", []),
        "qGlobal": result.get("q_global", []),
        "qDimension": result.get("q_dimension", 0),
        "priorTopMass": result.get("prior_top_mass"),
        "priorMargin": result.get("prior_margin"),
        "normalizedEntropy": result.get("normalized_entropy"),
        "candidateScores": result.get("candidate_scores", []),
        "apiLatencyMs": result.get("provenance", {}).get("api_latency_ms", 0.0),
        "totalLatencyMs": result.get("provenance", {}).get("total_latency_ms", 0.0),
        "attempts": result.get("provenance", {}).get("attempts", 0),
        "correctionRetries": result.get("provenance", {}).get("retries", 0),
        "hallucinatedCandidateAttempt": any("unknown_candidate_id" in error or "candidate_ids_mismatch" in error
                                             for error in all_validation_errors),
        "stateContradictionAttempt": any("state_or_affordance_contradiction" in error
                                         for error in all_validation_errors),
        "diagnostics": diagnostics,
        "localMeasurements": local,
        "model": result.get("provenance", {}).get("model_id", ""),
        "promptVersion": result.get("provenance", {}).get("prompt_version", ""),
        "engineVersion": result.get("provenance", {}).get("engine_version", ""),
    }


def _load_existing(path: Path, benchmark_sha: str, model_id: str | None = None,
                   prompt_version: str | None = None,
                   engine_version: str | None = None) -> tuple[set[tuple[str, int]], bool]:
    rows = _jsonl_rows(path)
    if not rows:
        return set(), False
    metadata = next((row for row in rows if row.get("recordType") == "m30_run_metadata"), None)
    if metadata is None or metadata.get("benchmarkSha256") != benchmark_sha:
        raise ValueError("existing M30 results do not match the frozen benchmark lock")
    if model_id and metadata.get("modelId") != model_id:
        raise ValueError("existing M30 results were produced by a different model")
    if prompt_version and metadata.get("promptVersion") != prompt_version:
        raise ValueError("existing M30 results were produced by a different prompt version")
    if engine_version and metadata.get("engineVersion") != engine_version:
        raise ValueError("existing M30 results were produced by a different engine version")
    completed = {
        (row["episodeId"], int(row["roundIndex"]))
        for row in rows if row.get("recordType") == "m30_semantic_context_result" and row.get("callKind") == "primary"
    }
    return completed, True


def run_primary(limit: int | None = None, resume: bool = False) -> dict[str, Any]:
    validation = validate()
    benchmark = json.loads((OUT / "semantic_context_sequence_benchmark_v2.json").read_text(encoding="utf-8"))
    client, model_id, _available_models, configured_model = build_live_sequence_context_engine()
    engine = SemanticContextEngine(client, model_id, base_url=client.base_url, max_correction_retries=1)
    completed, exists = _load_existing(RESULTS, validation["sha256"], model_id if resume else None,
                                       M30_CONTEXT_PROMPT_VERSION if resume else None,
                                       M30_CONTEXT_ENGINE_VERSION if resume else None)
    if exists and not resume:
        raise FileExistsError("semantic_context_results.jsonl exists; pass --resume only for the same frozen run")
    if not exists:
        _append_jsonl(RESULTS, {
            "recordType": "m30_run_metadata",
            "createdAtUtc": _utc_now(),
            "benchmarkSha256": validation["sha256"],
            "benchmarkId": validation["benchmarkId"],
            "episodeCount": validation["episodeCount"],
            "decisionPointCount": validation["decisionPointCount"],
            "modelId": model_id,
            "baseUrl": client.base_url,
            "configuredModelWasSelected": bool(configured_model),
            "promptVersion": M30_CONTEXT_PROMPT_VERSION,
            "engineVersion": M30_CONTEXT_ENGINE_VERSION,
            "softmaxTemperature": 0.20,
            "evaluationOnlySentToModel": False,
            "apiKeyPersisted": False,
        })

    all_points = [(episode, point) for episode in benchmark["episodes"] for point in episode["decision_points"]]
    pending = [(episode, point) for episode, point in all_points
               if (episode["episode_id"], int(point["round_index"])) not in completed]
    if limit is not None:
        pending = pending[:limit]
    if not pending:
        return {"status": "PASS", "completed": len(completed), "remaining": 0, "modelId": model_id}

    for index, (episode, point) in enumerate(pending, 1):
        model_input = point["model_input"]
        result = engine.predict_next_target(
            scene=model_input["scene"],
            selection_history=model_input["selection_history"],
            remaining_candidates=model_input["candidate_next_object_ids"],
            current_task_state=model_input["current_task_state"],
        )
        local = _local_measurements(model_input, result)
        record = _score_row(episode, point, result, _canonical_sha(model_input), local)
        record["recordedAtUtc"] = _utc_now()
        _append_jsonl(RESULTS, record)
        print(json.dumps({
            "completedThisRun": index,
            "pendingThisRun": len(pending),
            "episodeId": episode["episode_id"],
            "roundIndex": point["round_index"],
            "status": result.get("status"),
            "topCandidate": result.get("top_candidate"),
            "apiLatencyMs": result.get("provenance", {}).get("api_latency_ms", 0.0),
            "attempts": result.get("provenance", {}).get("attempts", 0),
        }, ensure_ascii=False), flush=True)
    done_rows = [row for row in _jsonl_rows(RESULTS)
                 if row.get("recordType") == "m30_semantic_context_result" and row.get("callKind") == "primary"]
    return {
        "status": "PASS" if len(done_rows) == len(all_points) else "PARTIAL",
        "completed": len(done_rows),
        "expected": len(all_points),
        "remaining": len(all_points) - len(done_rows),
        "modelId": model_id,
        "meanApiLatencyMs": statistics.mean(float(row["apiLatencyMs"]) for row in done_rows) if done_rows else None,
    }


def run_repeatability() -> dict[str, Any]:
    validation = validate()
    benchmark = json.loads((OUT / "semantic_context_sequence_benchmark_v2.json").read_text(encoding="utf-8"))
    client, model_id, _available_models, _configured_model = build_live_sequence_context_engine()
    engine = SemanticContextEngine(client, model_id, base_url=client.base_url, max_correction_retries=1)
    primary_rows = [row for row in _jsonl_rows(RESULTS)
                    if row.get("recordType") == "m30_semantic_context_result" and row.get("callKind") == "primary"]
    if len(primary_rows) != validation["decisionPointCount"]:
        raise ValueError("repeatability requires the complete primary benchmark run")
    if REPEATABILITY.exists():
        raise FileExistsError("repeatability output exists; refusing to overwrite")
    source_by_key = {(row["episodeId"], int(row["roundIndex"])): row for row in primary_rows}
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    by_family: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for episode in benchmark["episodes"]:
        by_family.setdefault(episode["scene_family"], []).append((episode, episode["decision_points"][0]))
    for family in sorted(by_family):
        episode, first_point = by_family[family][0]
        selected.append((episode, first_point))
        if len(episode["decision_points"]) > 1:
            selected.append((episode, episode["decision_points"][-1]))

    _append_jsonl(REPEATABILITY, {
        "recordType": "m30_repeatability_metadata",
        "createdAtUtc": _utc_now(),
        "benchmarkSha256": validation["sha256"],
        "modelId": model_id,
        "selectedPointCount": len(selected),
        "apiKeyPersisted": False,
    })
    repeat_rows = []
    for episode, point in selected:
        key = (episode["episode_id"], int(point["round_index"]))
        original = source_by_key[key]
        model_input = point["model_input"]
        result = engine.predict_next_target(
            scene=model_input["scene"], selection_history=model_input["selection_history"],
            remaining_candidates=model_input["candidate_next_object_ids"],
            current_task_state=model_input["current_task_state"],
        )
        local = _local_measurements(model_input, result)
        scored = _score_row(episode, point, result, _canonical_sha(model_input), local, call_kind="repeat")
        original_rank = original.get("candidateRanking", [])
        repeat_rank = scored.get("candidateRanking", [])
        scored.update({
            "recordType": "m30_repeatability_result",
            "originalStatus": original.get("predictedStatus"),
            "originalTopCandidate": original.get("topCandidate"),
            "originalCandidateRanking": original_rank,
            "sameStatus": original.get("predictedStatus") == result.get("status"),
            "sameTopCandidate": original.get("rankedTopCandidate", (original.get("candidateRanking") or [None])[0])
                == scored.get("rankedTopCandidate"),
            "sameFullRanking": original_rank == repeat_rank,
            "repeatPriorL1Difference": sum(abs(float(a) - float(b)) for a, b in zip(original.get("contextPrior", []), result.get("context_prior", [])))
                if len(original.get("contextPrior", [])) == len(result.get("context_prior", [])) else None,
            "recordedAtUtc": _utc_now(),
        })
        repeat_rows.append(scored)
        _append_jsonl(REPEATABILITY, scored)
    return {
        "status": "PASS",
        "n": len(repeat_rows),
        "sameStatusRate": statistics.mean(row["sameStatus"] for row in repeat_rows),
        "sameTopCandidateRate": statistics.mean(row["sameTopCandidate"] for row in repeat_rows),
        "sameFullRankingRate": statistics.mean(row["sameFullRanking"] for row in repeat_rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="run at most this many missing primary points")
    parser.add_argument("--resume", action="store_true", help="resume a matching, incomplete result file")
    parser.add_argument("--repeatability", action="store_true", help="run the predeclared repeatability subset")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be a positive integer")
    result = run_repeatability() if args.repeatability else run_primary(args.limit, args.resume)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"PASS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
