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


M30_CONTEXT_PROMPT_VERSION = "m30-sequential-relational-context-v4"
M30_CONTEXT_SCHEMA_VERSION = "m30-context-response-v1"
M30_CONTEXT_ENGINE_VERSION = "m30-context-precondition-guard-v1"
M30_SOFTMAX_TEMPERATURE = 0.20  # Frozen to M28's transform; not calibrated.
M30_TEMPERATURE = 0.0
M30_MAX_OUTPUT_TOKENS = 1800
M30_MAX_CORRECTION_RETRIES = 1
M30_STATUSES = frozenset({"informative", "ambiguous", "context_off", "invalid"})
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
    return f"""You rank the next selectable object from a structured scene and the complete already-accepted selection history.
Return exactly one JSON object. Do not use markdown, chain-of-thought, hidden reasoning, or prose outside JSON.
This is a pre-decision semantic Context prior, not a robot plan and not an EEG target label.

Use only facts explicitly present in scene objects, their declared affordance_tags/current_state, selection_history, current_task_state, and candidate_next_object_ids. Do not add objects, colors, states, affordances, or future selections. An absent state is unknown. Rank every remaining candidate exactly once. Use informative when one candidate has a clear task-consistent advantage from the current task and accepted history, even though other candidates may be plausible in a broader sense. Use ambiguous only when at least two distinct candidates are comparably plausible as the immediate next step for the same current task state and the task/history give no cue to prefer one. Do not mark a case ambiguous merely because multiple future steps could eventually be useful, because the scene contains several compatible objects, or because a more specific/default option exists. Do not claim a unique user intention when two current next steps remain equally supported. Unrelated distractors do not create ambiguity. If no useful semantic evidence exists, use context_off. Use invalid only for an impossible or contradictory supplied state.

For each candidate return: candidate_id, source_object_id (one already in selection_history or null), semantic_score (ordinal 0..1; not calibrated), relation_type (one of: {relations}), relation_confidence (ordinal 0..1; not calibrated), and short_rationale_code (one short snake_case token, no explanation).
Specific relation affordance requirements: {relation_rules}. Check the declared tags before assigning a specific relation. For a candidate that does not meet the required target tags, or a source that does not meet the required source tags, use NONE or RELATED_TO instead of guessing. A source_object_id must be an exact ID from selection_history; never use an unselected candidate as the source. Do not invent a relation for every distractor. Explicit full, occupied, unavailable, or blocked state rejects capacity-dependent relations. A closed non-openable container cannot be a STORE_IN/PLACE_IN/DISCARD_IN/POUR_INTO target. OPEN/CLOSE require openable and consistent known state.
Do not claim probabilities. The local program computes an uncalibrated softmax prior.

Required top-level keys: status, candidate_scores, reason_code. Status is one of informative, ambiguous, context_off, invalid. reason_code is a short snake_case token."""


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
) -> dict[str, Any]:
    prior = list(prior or [])
    rows = list(rows or [])
    ranking = list(ranking or [])
    info = prior_diagnostics(prior)
    return {
        "status": status,
        "reason_code": reason_code,
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
) -> list[str]:
    errors: list[str] = []
    if not isinstance(response, dict):
        return ["response must be a JSON object"]
    if response.get("status") not in M30_STATUSES:
        errors.append("status is not in the M30 Context enum")
    reason = response.get("reason_code")
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", reason):
        errors.append("reason_code must be a short snake_case token")
    items = response.get("candidate_scores")
    if not isinstance(items, list) or len(items) != len(candidate_ids):
        return errors + ["candidate_scores must contain each remaining candidate exactly once"]
    seen: set[str] = set()
    source_by_id = scene.objects
    relation_count = 0
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
        score = item.get("semantic_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or not 0.0 <= float(score) <= 1.0:
            errors.append(f"semantic_score_invalid:{candidate_id}")
        confidence = item.get("relation_confidence")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(float(confidence)) or not 0.0 <= float(confidence) <= 1.0:
            errors.append(f"relation_confidence_invalid:{candidate_id}")
        relation = item.get("relation_type")
        if not isinstance(relation, str) or relation.upper() not in RELATION_TYPES:
            errors.append(f"relation_type_invalid:{candidate_id}")
            continue
        relation = relation.upper()
        if relation != "NONE":
            relation_count += 1
        source_id = item.get("source_object_id")
        if source_id is not None and (not isinstance(source_id, str) or source_id not in history or source_id not in source_by_id):
            errors.append(f"source_not_in_accepted_history:{candidate_id}")
            continue
        if relation in _SOURCE_TAGS and source_id is None:
            errors.append(f"source_required_for_relation:{candidate_id}")
        source = source_by_id.get(source_id) if source_id else None
        compatible, why = relation_compatibility(relation, source, source_by_id[candidate_id])
        if not compatible:
            errors.append(f"state_or_affordance_contradiction:{candidate_id}:{why}")
        rationale = item.get("short_rationale_code")
        if not isinstance(rationale, str) or not re.fullmatch(r"[a-z0-9_]{1,48}", rationale):
            errors.append(f"short_rationale_code_invalid:{candidate_id}")
    if seen != set(candidate_ids):
        errors.append("candidate_ids_mismatch")
    if response.get("status") == "informative" and relation_count == 0:
        errors.append("informative_status_without_any_relation")
    return errors


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
        errors = _validate_response(response_obj, candidate_ids=candidate_ids, scene=core, history=history)
        if not errors:
            by_id = {row["candidate_id"]: row for row in response_obj["candidate_scores"]}
            ordered_rows = [dict(by_id[candidate_id]) for candidate_id in candidate_ids]
            for row in ordered_rows:
                row["relation_type"] = row["relation_type"].upper()
            scores = [float(row["semantic_score"]) for row in ordered_rows]
            prior = _softmax(scores)
            ranking = [candidate_ids[i] for i in sorted(range(len(candidate_ids)), key=lambda i: (-scores[i], i))]
            status = response_obj["status"]
            eligible = status == "informative" and any(row["relation_type"] != "NONE" for row in ordered_rows)
            return _candidate_result(status, response_obj["reason_code"], candidate_ids, prior=prior, rows=ordered_rows,
                                     ranking=ranking, eligible=eligible,
                                     total_latency_ms=(time.perf_counter()-started)*1000, api_latency_ms=api_latency_ms,
                                     model_id=model_id, base_url=base_url, attempts=attempts,
                                     retries=max(0, attempts-1), usage=usage, diagnostics=diagnostics)
        diagnostics.append({"attempt": attempts, "validation_errors": errors})
        correction = {
            "instruction": "Return a corrected JSON object. Use every supplied remaining candidate exactly once. For each reported validation error, set the relation to NONE or RELATED_TO unless the exact required tags and an accepted history source are present in the supplied facts. Do not infer missing tags or use an unselected source. If the task is ambiguous, impossible, complete, or has no useful relation, return ambiguous, invalid, or context_off as appropriate.",
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
