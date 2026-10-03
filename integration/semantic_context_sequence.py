"""M30 variable-dimensional, sequential next-target semantic Context.

The model receives only the structured scene, accepted history, current
pre-decision state, and all remaining candidate IDs. Evaluation labels belong
to the benchmark scorer and are not accepted by this API.
"""

from __future__ import annotations

import json
import math
import re
import time
from typing import Any

from integration.semantic_intelligence import (
    DeepSeekClientError,
    PlannerInputError,
    SemanticSceneCore,
    build_live_client,
)
from integration.semantic_context_relations import (
    RELATION_SCHEMA_VERSION,
    RELATION_TYPES,
    _SOURCE_TAGS,
    _TARGET_TAGS,
    relation_compatibility,
)


M30_CONTEXT_PROMPT_VERSION = "m33-task-state-context-v1"
M30_CONTEXT_SCHEMA_VERSION = "m33-task-state-context-response-v1"
M30_CONTEXT_ENGINE_VERSION = "m33-task-state-context-v1"
M30_SOFTMAX_TEMPERATURE = 0.20  # Frozen to M28's transform; not calibrated.
M30_TEMPERATURE = 0.0
M30_MAX_OUTPUT_TOKENS = 2200
M30_MAX_CORRECTION_RETRIES = 1
M30_STATUSES = frozenset({"informative", "ambiguous", "context_off", "invalid"})
M33_FINAL_SCORE_WEIGHTS = (0.70, 0.20, 0.10)
M33_MIN_TASK_STATE_CONFIDENCE = 0.50
M33_MIN_TASK_STATE_STABILITY = 0.50
M33_AMBIGUOUS_FINAL_MARGIN = 0.06
M33_AMBIGUOUS_CONTINUATION_MARGIN = 0.06
M33_AMBIGUOUS_HIGH_ENTROPY = 0.92
_EVALUATION_KEYS = frozenset({
    "evaluation_only", "expected_target", "expected_target_ids", "acceptable_next_targets",
    "expected_relation", "true_label", "future_selection", "future_eeg", "answer_key",
})


def _system_prompt() -> str:
    relations = ", ".join(sorted(RELATION_TYPES))
    tag_rules = []
    for relation in sorted(set(_SOURCE_TAGS) | set(_TARGET_TAGS)):
        requirements = []
        if relation in _SOURCE_TAGS:
            requirements.append("history source any-of " + "/".join(sorted(_SOURCE_TAGS[relation])))
        if relation in _TARGET_TAGS:
            requirements.append("candidate target any-of " + "/".join(sorted(_TARGET_TAGS[relation])))
        tag_rules.append(relation + " requires " + " and ".join(requirements))
    relation_rules = "; ".join(tag_rules)
    return f"""Infer a current task hypothesis and task progress from the COMPLETE ORDERED selection_history before ranking any remaining candidate.
Return exactly one JSON object. Do not use markdown, chain-of-thought, hidden reasoning, or prose outside JSON.
This is a pre-decision semantic Context prior, not a robot plan and not an EEG target label.

Use only facts explicitly present in scene objects, declared affordance_tags/current_state, the full ordered selection_history, current_task_state, and candidate_next_object_ids. Do not add objects, colors, states, affordances, or future selections. An absent state is unknown. Do not treat the most recent object as a new independent intent by default: an object selected later may be a tool or intermediate step for an earlier selected object. Infer the likely ongoing task and its progress from the whole sequence. Relational affordance is evidence, not the ranking rule. A candidate's task-continuation score must carry more weight than a pairwise relation; penalize a candidate that would switch to a different task.

First return task_state with hypothesis_code and progress_code (short snake_case tokens), hypothesis_summary and progress_summary (one short factual sentence each), confidence (0..1), and stability (0..1; confidence that this task-state interpretation is a single coherent reading of the full ordered history). Do not reveal reasoning.

For every candidate return exactly one row containing candidate_id, candidate_task_continuation_score (0..1), source_object_id (one ID already in selection_history or null), relation_type (one of: {relations}), relation_confidence (0..1), task_switch_penalty (0..1; higher means more likely to begin an unrelated task), final_semantic_score (0..1), and short_rationale_code (one short snake_case token). Compute final_semantic_score as 0.70*candidate_task_continuation_score + 0.20*relation_confidence + 0.10*(1-task_switch_penalty). This makes task continuation dominant; a strong pairwise relation alone must not promote an unrelated branch. Scores are ordinal, not calibrated probabilities.

Specific relation affordance requirements: {relation_rules}. Check declared tags and explicit state before assigning a specific relation. For an incompatible candidate relation use RELATED_TO or NONE; never let one candidate's invalid relation invalidate other rows. A source_object_id must be an exact ID from selection_history; never use an unselected candidate as the source. Do not invent a relation for every distractor. Explicit full, occupied, unavailable, or blocked state rejects capacity-dependent relations. A closed non-openable container cannot be a STORE_IN/PLACE_IN/DISCARD_IN/POUR_INTO target. OPEN/CLOSE require openable and consistent known state.

Use informative only if the task-state interpretation is sufficiently confident/stable and one immediate continuation has a clear advantage. Use ambiguous when two or more immediate continuations are comparably supported, task state is unstable, or uncertainty remains; do not force a sharp top candidate. Use context_off when no useful task-state evidence exists. Use invalid only for an impossible or contradictory supplied state. Unrelated distractors do not create ambiguity. Do not claim probabilities; local software computes an uncalibrated q_global prior.

Required top-level keys: status, task_state, candidate_scores, reason_code. Status is one of informative, ambiguous, context_off, invalid. reason_code is a short snake_case token."""


def _reject_evaluation_fields(value: Any, path: str = "input") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _EVALUATION_KEYS:
                raise ValueError(f"evaluation-only field forbidden in model input at {path}.{key}")
            _reject_evaluation_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_evaluation_fields(child, f"{path}[{index}]")


def _softmax(scores: list[float], temperature: float = M30_SOFTMAX_TEMPERATURE) -> list[float]:
    if not scores:
        return []
    if temperature <= 0.0:
        raise ValueError("softmax temperature must be positive")
    scaled = [score / temperature for score in scores]
    high = max(scaled)
    weights = [math.exp(value - high) for value in scaled]
    total = sum(weights)
    return [weight / total for weight in weights]


def prior_diagnostics(prior: list[float]) -> dict[str, float | None]:
    if not prior:
        return {"top_mass": None, "margin": None, "normalized_entropy": None}
    ordered = sorted(prior, reverse=True)
    entropy = None
    if len(prior) > 1:
        entropy = -sum(value * math.log(max(value, 1e-15)) for value in prior) / math.log(len(prior))
    return {
        "top_mass": ordered[0],
        "margin": ordered[0] - ordered[1] if len(ordered) > 1 else 1.0,
        "normalized_entropy": entropy,
    }


def _selectable_order(scene: SemanticSceneCore) -> list[str]:
    all_selectable = [item["id"] for item in scene.scene["objects"] if item.get("selectable", False)]
    requested_order = scene.scene.get("candidate_order", [])
    if not requested_order:
        return all_selectable
    order = [item for item in requested_order if item in all_selectable]
    order.extend(item for item in all_selectable if item not in set(order))
    return order


def _candidate_result(
    status: str,
    reason_code: str,
    candidate_ids: list[str],
    *,
    prior: list[float] | None = None,
    rows: list[dict[str, Any]] | None = None,
    ranking: list[str] | None = None,
    eligible: bool = False,
    api_latency_ms: float = 0.0,
    total_latency_ms: float = 0.0,
    model_id: str = "",
    base_url: str = "",
    attempts: int = 0,
    retries: int = 0,
    usage: dict[str, int] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    task_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prior = list(prior or [])
    rows = list(rows or [])
    ranking = list(ranking or [])
    info = prior_diagnostics(prior)
    return {
        "status": status,
        "reason_code": reason_code,
        "task_state": dict(task_state or {
            "hypothesis_code": "unknown",
            "hypothesis_summary": "Task state unavailable.",
            "progress_code": "unknown",
            "progress_summary": "Task progress unavailable.",
            "confidence": 0.0,
            "stability": 0.0,
        }),
        "candidate_ids": list(candidate_ids),
        "candidate_scores": rows,
        "candidate_ranking": ranking,
        "context_prior": prior,
        "q_global": [
            {"candidate_id": candidate_id, "q": prior[index]}
            for index, candidate_id in enumerate(candidate_ids)
        ] if len(prior) == len(candidate_ids) else [],
        "q_dimension": len(prior),
        "prior_interpretation": "uncalibrated softmax of ordinal semantic scores; not a calibrated probability",
        "softmax_temperature": M30_SOFTMAX_TEMPERATURE,
        "top_candidate": ranking[0] if status == "informative" and ranking else None,
        "prior_top_mass": info["top_mass"],
        "prior_margin": info["margin"],
        "normalized_entropy": info["normalized_entropy"],
        "eligible_informative": bool(eligible),
        "provenance": {
            "model_id": model_id,
            "base_url": base_url,
            "prompt_version": M30_CONTEXT_PROMPT_VERSION,
            "engine_version": M30_CONTEXT_ENGINE_VERSION,
            "schema_version": M30_CONTEXT_SCHEMA_VERSION,
            "relation_schema_version": RELATION_SCHEMA_VERSION,
            "temperature": M30_TEMPERATURE,
            "softmax_temperature": M30_SOFTMAX_TEMPERATURE,
            "api_latency_ms": round(api_latency_ms, 3),
            "total_latency_ms": round(total_latency_ms, 3),
            "attempts": attempts,
            "retries": retries,
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "diagnostics": diagnostics or [],
        },
    }


def _validate_response(
    response: Any,
    *,
    candidate_ids: list[str],
    scene: SemanticSceneCore,
    history: list[str],
    row_warnings: list[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    warnings = row_warnings if row_warnings is not None else []
    if not isinstance(response, dict):
        return ["response must be a JSON object"]
    if response.get("status") not in M30_STATUSES:
        errors.append("status is not in the M30 Context enum")
    reason = response.get("reason_code")
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", reason):
        errors.append("reason_code must be a short snake_case token")
    task_state = response.get("task_state")
    if not isinstance(task_state, dict):
        errors.append("task_state must be an object")
    else:
        for key in ("hypothesis_code", "progress_code"):
            if not isinstance(task_state.get(key), str) or not re.fullmatch(r"[a-z0-9_]{1,48}", task_state[key]):
                errors.append(f"task_state.{key} must be a short snake_case token")
        for key in ("hypothesis_summary", "progress_summary"):
            if not isinstance(task_state.get(key), str) or not task_state[key].strip() or len(task_state[key]) > 240:
                errors.append(f"task_state.{key} must be a short factual sentence")
        for key in ("confidence", "stability"):
            value = task_state.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                errors.append(f"task_state.{key} must be a finite number from 0 through 1")
    items = response.get("candidate_scores")
    if not isinstance(items, list) or len(items) != len(candidate_ids):
        return errors + ["candidate_scores must contain each remaining candidate exactly once"]
    seen: set[str] = set()
    source_by_id = scene.objects
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"candidate_scores[{index}] must be an object")
            continue
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in candidate_ids:
            errors.append("unknown_candidate_id")
            continue
        if candidate_id in seen:
            errors.append("duplicate_candidate_id")
        seen.add(candidate_id)
        for key in ("candidate_task_continuation_score", "relation_confidence", "task_switch_penalty"):
            value = item.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
                warnings.append(f"{key}_invalid:{candidate_id}")
                item[key] = 0.0 if key != "task_switch_penalty" else 1.0
        relation = item.get("relation_type")
        if not isinstance(relation, str) or relation.upper() not in RELATION_TYPES:
            warnings.append(f"relation_type_invalid:{candidate_id}")
            item["relation_type"] = "NONE"
            item["relation_confidence"] = 0.0
            item["source_object_id"] = None
            relation = "NONE"
        relation = relation.upper()
        source_id = item.get("source_object_id")
        if source_id is not None and (not isinstance(source_id, str) or source_id not in history or source_id not in source_by_id):
            warnings.append(f"source_not_in_accepted_history:{candidate_id}")
            item["source_object_id"] = None
            item["relation_type"] = "NONE"
            item["relation_confidence"] = 0.0
            source_id = None
            relation = "NONE"
        if relation in _SOURCE_TAGS and source_id is None:
            warnings.append(f"source_required_for_relation:{candidate_id}")
            item["relation_type"] = "NONE"
            item["relation_confidence"] = 0.0
        source = source_by_id.get(source_id) if source_id else None
        compatible, why = relation_compatibility(relation, source, source_by_id[candidate_id])
        if not compatible:
            warnings.append(f"candidate_relation_downgraded:{candidate_id}:{why}")
            item["relation_type"] = "NONE"
            item["relation_confidence"] = 0.0
            item["source_object_id"] = None
        rationale = item.get("short_rationale_code")
        if not isinstance(rationale, str) or not re.fullmatch(r"[a-z0-9_]{1,48}", rationale):
            warnings.append(f"short_rationale_code_invalid:{candidate_id}")
            item["short_rationale_code"] = "conservative_fallback"
    if seen != set(candidate_ids):
        errors.append("candidate_ids_mismatch")
    return errors


def _canonical_candidate_score(row: dict[str, Any]) -> float:
    continuation, relation_confidence, task_switch_penalty = M33_FINAL_SCORE_WEIGHTS
    return (
        continuation * float(row["candidate_task_continuation_score"])
        + relation_confidence * float(row["relation_confidence"])
        + task_switch_penalty * (1.0 - float(row["task_switch_penalty"]))
    )


def predict_next_target(
    client: Any,
    model_id: str,
    *,
    scene: dict[str, Any] | SemanticSceneCore,
    selection_history: list[str],
    remaining_candidates: list[str] | None = None,
    current_task_state: dict[str, Any] | None = None,
    base_url: str = "https://api.deepseek.com",
    max_correction_retries: int = M30_MAX_CORRECTION_RETRIES,
) -> dict[str, Any]:
    """Generate a frozen, variable-dimensional prior for the next selection."""
    started = time.perf_counter()
    if max_correction_retries < 0 or max_correction_retries > M30_MAX_CORRECTION_RETRIES:
        raise ValueError("M30 correction retries are bounded to zero or one")
    history = list(selection_history) if isinstance(selection_history, list) else []
    try:
        if isinstance(scene, SemanticSceneCore):
            scene_data = scene.scene
        elif isinstance(scene, dict):
            scene_data = scene
        else:
            raise ValueError("scene must be a structured semantic scene")
        if not isinstance(selection_history, list) or not all(isinstance(x, str) for x in selection_history):
            raise ValueError("selection_history must be a string array")
        if len(selection_history) != len(set(selection_history)):
            raise ValueError("selection_history cannot select an object more than once")
        if current_task_state is not None and not isinstance(current_task_state, dict):
            raise ValueError("current_task_state must be an object")
        task_state = current_task_state or {}
        _reject_evaluation_fields({"scene": scene_data, "selection_history": selection_history, "current_task_state": task_state})
        core = SemanticSceneCore(scene_data, selection_history=selection_history, current_task_state=task_state)
        order = _selectable_order(core)
        unknown_history = [item for item in history if item not in core.objects or item not in order]
        if unknown_history:
            raise ValueError("selection_history contains an unknown or non-selectable object")
        expected_remaining = [item for item in order if item not in set(history)]
        if remaining_candidates is None:
            candidate_ids = expected_remaining
        elif not isinstance(remaining_candidates, list) or not all(isinstance(x, str) for x in remaining_candidates):
            raise ValueError("remaining_candidates must be a string array")
        else:
            if len(remaining_candidates) != len(set(remaining_candidates)) or set(remaining_candidates) != set(expected_remaining):
                raise ValueError("remaining_candidates must equal all selectable, not-yet-selected scene objects")
            candidate_ids = expected_remaining
        if not candidate_ids:
            return _candidate_result("context_off", "no_remaining_candidates", [], total_latency_ms=(time.perf_counter()-started)*1000, model_id=model_id, base_url=base_url)
        if task_state.get("phase") in {"complete", "completed", "done"} or task_state.get("task_complete") is True:
            prior = [1.0 / len(candidate_ids)] * len(candidate_ids)
            return _candidate_result("context_off", "task_already_complete", candidate_ids, prior=prior,
                                     total_latency_ms=(time.perf_counter()-started)*1000, model_id=model_id, base_url=base_url)
        if task_state.get("required_tool_available") is False:
            return _candidate_result("invalid", "required_tool_unavailable", candidate_ids,
                                     total_latency_ms=(time.perf_counter()-started)*1000,
                                     model_id=model_id, base_url=base_url)
        required_affordance = task_state.get("required_target_affordance")
        if isinstance(required_affordance, str) and required_affordance.strip():
            required_tag = required_affordance.strip().casefold()
            target_available = any(
                required_tag in {tag.casefold() for tag in core.objects[candidate_id].get("affordance_tags", [])}
                for candidate_id in candidate_ids
            )
            if not target_available:
                return _candidate_result("invalid", "required_target_affordance_absent", candidate_ids,
                                         total_latency_ms=(time.perf_counter()-started)*1000,
                                         model_id=model_id, base_url=base_url)
        if len(candidate_ids) == 1:
            return _candidate_result("context_off", "single_remaining_choice_no_context_needed", candidate_ids, prior=[1.0],
                                     total_latency_ms=(time.perf_counter()-started)*1000, model_id=model_id, base_url=base_url)
        context = {
            "scene": core.scene,
            "selection_history": list(history),
            "current_task_state": core.current_task_state,
            "candidate_next_object_ids": list(candidate_ids),
        }
        _reject_evaluation_fields(context)
    except (ValueError, TypeError, KeyError, PlannerInputError) as error:
        return _candidate_result("invalid", "invalid_context_input", [], total_latency_ms=(time.perf_counter()-started)*1000,
                                 model_id=model_id, base_url=base_url,
                                 diagnostics=[{"error_type": type(error).__name__}])

    correction: dict[str, Any] | None = None
    diagnostics: list[dict[str, Any]] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    api_latency_ms = 0.0
    response_obj: Any = None
    attempts = 0
    for attempt in range(max_correction_retries + 1):
        attempts += 1
        messages = [
            {"role": "system", "content": _system_prompt()},
            {"role": "user", "content": json.dumps({
                "schema_version": M30_CONTEXT_SCHEMA_VERSION,
                "context_input": context,
                "correction": correction,
            }, ensure_ascii=False, separators=(",", ":"))},
        ]
        api_started = time.perf_counter()
        try:
            response = client.complete(messages, model_id=model_id, temperature=M30_TEMPERATURE,
                                       max_output_tokens=M30_MAX_OUTPUT_TOKENS)
        except DeepSeekClientError as error:
            api_latency_ms += (time.perf_counter() - api_started) * 1000.0
            diagnostics.append({"attempt": attempts, "error_type": type(error).__name__})
            prior = [1.0 / len(candidate_ids)] * len(candidate_ids)
            return _candidate_result("context_off", "api_failure", candidate_ids, prior=prior,
                                     total_latency_ms=(time.perf_counter()-started)*1000,
                                     api_latency_ms=api_latency_ms, model_id=model_id, base_url=base_url,
                                     attempts=attempts, retries=max(0, attempts-1), usage=usage, diagnostics=diagnostics)
        api_latency_ms += (time.perf_counter() - api_started) * 1000.0
        for key in usage:
            value = (response.get("usage") or {}).get(key, 0)
            if isinstance(value, int) and value >= 0:
                usage[key] += value
        content = response.get("content")
        try:
            response_obj = json.loads(content) if isinstance(content, str) else content
        except (TypeError, json.JSONDecodeError):
            response_obj = None
        row_warnings: list[str] = []
        errors = _validate_response(response_obj, candidate_ids=candidate_ids, scene=core, history=history,
                                    row_warnings=row_warnings)
        if not errors:
            by_id = {row["candidate_id"]: row for row in response_obj["candidate_scores"]}
            ordered_rows = [dict(by_id[candidate_id]) for candidate_id in candidate_ids]
            for row in ordered_rows:
                row["relation_type"] = row["relation_type"].upper()
                score = _canonical_candidate_score(row)
                row["final_semantic_score"] = score
                # Keep the existing consumer field while M32 and downstream callers migrate.
                row["semantic_score"] = score
            scores = [float(row["final_semantic_score"]) for row in ordered_rows]
            prior = _softmax(scores)
            ranking = [candidate_ids[i] for i in sorted(range(len(candidate_ids)), key=lambda i: (-scores[i], i))]
            task_state_result = dict(response_obj["task_state"])
            task_confidence = float(task_state_result["confidence"])
            task_stability = float(task_state_result["stability"])
            info = prior_diagnostics(prior)
            top_row = next((row for row in ordered_rows if ranking and row["candidate_id"] == ranking[0]), {})
            model_status = response_obj["status"]
            status = model_status
            reason_code = response_obj["reason_code"]
            if model_status == "informative" and (
                task_confidence < M33_MIN_TASK_STATE_CONFIDENCE
                or task_stability < M33_MIN_TASK_STATE_STABILITY
                or (info["margin"] is not None and info["margin"] < M33_AMBIGUOUS_FINAL_MARGIN)
                or (info["normalized_entropy"] is not None and info["normalized_entropy"] > M33_AMBIGUOUS_HIGH_ENTROPY)
            ):
                status = "ambiguous"
                reason_code = "task_state_or_continuation_uncertain"
            if model_status == "informative" and info["margin"] is not None:
                continuation_scores = sorted(
                    (float(row["candidate_task_continuation_score"]) for row in ordered_rows), reverse=True
                )
                if len(continuation_scores) > 1 and continuation_scores[0] - continuation_scores[1] < M33_AMBIGUOUS_CONTINUATION_MARGIN:
                    status = "ambiguous"
                    reason_code = "task_continuation_tie"
            if status in {"ambiguous", "context_off", "invalid"}:
                prior = [1.0 / len(candidate_ids)] * len(candidate_ids)
            eligible = (
                status == "informative"
                and task_confidence >= M33_MIN_TASK_STATE_CONFIDENCE
                and task_stability >= M33_MIN_TASK_STATE_STABILITY
                and float(top_row.get("task_switch_penalty", 1.0)) <= 0.50
                and any(row["relation_type"] != "NONE" for row in ordered_rows)
            )
            if row_warnings:
                diagnostics.append({"attempt": attempts, "candidate_row_downgrades": row_warnings})
            return _candidate_result(status, reason_code, candidate_ids, prior=prior, rows=ordered_rows,
                                     ranking=ranking, eligible=eligible,
                                     total_latency_ms=(time.perf_counter()-started)*1000, api_latency_ms=api_latency_ms,
                                     model_id=model_id, base_url=base_url, attempts=attempts,
                                     retries=max(0, attempts-1), usage=usage, diagnostics=diagnostics,
                                     task_state=task_state_result)
        diagnostics.append({"attempt": attempts, "validation_errors": errors})
        correction = {
            "instruction": "Return a corrected JSON object matching the required task_state and candidate row schema. Use every supplied remaining candidate exactly once. For each reported top-level/schema error, correct it. Candidate relation/precondition disagreements should be represented as NONE or RELATED_TO; they do not invalidate other candidate rows. Do not infer missing tags or use an unselected source. If the task is ambiguous, impossible, complete, or has no useful evidence, return ambiguous, invalid, or context_off as appropriate.",
            "validation_errors": errors,
            "prior_response": response_obj if isinstance(response_obj, dict) else None,
        }

    uniform = [1.0 / len(candidate_ids)] * len(candidate_ids)
    return _candidate_result("context_off", "invalid_model_output_after_bounded_retry", candidate_ids,
                             prior=uniform, total_latency_ms=(time.perf_counter()-started)*1000,
                             api_latency_ms=api_latency_ms, model_id=model_id, base_url=base_url,
                             attempts=attempts, retries=max(0, attempts-1), usage=usage, diagnostics=diagnostics)


def build_live_sequence_context_engine() -> tuple[Any, str, list[str], bool]:
    """Return a live client/model pair while keeping key handling in shared code."""
    client, model_id, available, configured = build_live_client()
    return client, model_id, available, configured


def paginate_candidates(candidate_ids: list[str], page_size: int = 3) -> list[list[str]]:
    if not isinstance(candidate_ids, list) or not all(isinstance(x, str) and x for x in candidate_ids):
        raise ValueError("candidate_ids must be a non-empty-string list")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_ids must be unique")
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        raise ValueError("page_size must be a positive integer")
    return [candidate_ids[index:index+page_size] for index in range(0, len(candidate_ids), page_size)]


def project_global_prior(q_global: list[dict[str, Any]], page_candidate_ids: list[str]) -> dict[str, Any]:
    """Project and renormalize a global prior onto one deterministic EEG page."""
    if not isinstance(q_global, list) or not q_global:
        raise ValueError("q_global must be a non-empty candidate/value array")
    global_ids: list[str] = []
    global_q: dict[str, float] = {}
    for row in q_global:
        if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str):
            raise ValueError("each q_global entry needs candidate_id and q")
        candidate_id = row["candidate_id"]
        value = row.get("q")
        if candidate_id in global_q:
            raise ValueError("q_global contains duplicate candidate IDs")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0.0:
            raise ValueError("q_global values must be finite and nonnegative")
        global_ids.append(candidate_id)
        global_q[candidate_id] = float(value)
    if not math.isclose(sum(global_q.values()), 1.0, abs_tol=1e-8):
        raise ValueError("q_global must sum to one")
    if not isinstance(page_candidate_ids, list) or not 1 <= len(page_candidate_ids) <= 3:
        raise ValueError("an EEG page must contain one to three valid candidates")
    if len(page_candidate_ids) != len(set(page_candidate_ids)):
        raise ValueError("EEG page candidate IDs must be unique")
    if any(candidate_id not in global_q for candidate_id in page_candidate_ids):
        raise ValueError("EEG page includes an object missing from q_global")
    selected = [global_q[candidate_id] for candidate_id in page_candidate_ids]
    total = sum(selected)
    if total <= 0.0:
        projected = [1.0 / len(selected)] * len(selected)
    else:
        projected = [value / total for value in selected]
    return {
        "page_candidate_ids": list(page_candidate_ids),
        "q_page": projected,
        "q_page_rows": [{"candidate_id": candidate_id, "q": projected[index]}
                        for index, candidate_id in enumerate(page_candidate_ids)],
        "page_size": len(page_candidate_ids),
        "global_candidate_order_preserved": [item for item in global_ids if item in set(page_candidate_ids)] == page_candidate_ids,
        "prior_interpretation": "renormalized projection of uncalibrated q_global; not a calibrated probability",
    }


def project_all_pages(q_global: list[dict[str, Any]], page_size: int = 3) -> list[dict[str, Any]]:
    ids = [row["candidate_id"] for row in q_global]
    return [project_global_prior(q_global, page) for page in paginate_candidates(ids, page_size)]
