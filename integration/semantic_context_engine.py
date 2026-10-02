"""Scene-derived next-target priors for the M28 transfer study.

The engine sees only a validated semantic scene, observed task state, prior
selections, and the candidate list. Benchmark labels are scored by the runner
after this method returns and are never an engine argument.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import time
from typing import Any, Protocol

from integration.semantic_intelligence import (
    DeepSeekClientError,
    SemanticSceneCore,
    build_live_client,
    normalize_noun,
)


CONTEXT_PROMPT_VERSION = "m28-semantic-context-prompt-v1"
CONTEXT_SCHEMA_VERSION = "m28-semantic-context-v1"
MODEL_TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 1000
MAX_CORRECTION_RETRIES = 1
PRIOR_SOFTMAX_TEMPERATURE = 0.20
ACTIVE_MIN_SEMANTIC_SCORE = 0.60
ACTIVE_MIN_SCORE_MARGIN = 0.12
RELATION_TYPES = frozenset({"charge", "store", "place_on", "place_in", "handover", "press", "rinse", "support", "none"})
CONTEXT_STATUSES = frozenset({"informative", "ambiguous", "context_off", "invalid"})


class ContextCompletionClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model_id: str,
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]: ...


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _system_prompt() -> str:
    return """You score the semantic plausibility of candidate next objects in a tabletop task.
Return exactly one JSON object. Do not use markdown, chain-of-thought, or free-form explanations.
Use only scene object IDs and facts, current_task_state, selection_history, source_object_id,
and candidate_next_object_ids supplied in the request. The task may be ambiguous or complete.
Never infer or invent a missing object, state, affordance, or instruction. Do not assume that a
familiar object is intended merely because it is salient. If several candidates remain plausible,
return status=ambiguous. If no semantic evidence supports choosing, return context_off. If an
explicit requested destination is absent or the task is impossible, return invalid.

For every candidate ID, return exactly one candidate_scores entry with candidate_id,
semantic_score (a 0..1 ordinal plausibility score, NOT a calibrated probability), and relation_type.
Allowed relation_type values: charge, store, place_on, place_in, handover, press, rinse, support, none.
Scores need not sum to one. The local software will derive the ranking and an explicitly
uncalibrated softmax prior. Do not omit candidates and do not add any IDs.

Required keys: status, candidate_scores, reason_code. Status is one of informative, ambiguous,
context_off, invalid. reason_code is one short snake_case token.
"""


def _user_prompt(context: dict[str, Any], correction: dict[str, Any] | None = None) -> str:
    request = {"schema_version": CONTEXT_SCHEMA_VERSION, "context_input": context}
    if correction is not None:
        request["correction"] = correction
    return json.dumps(request, ensure_ascii=False, separators=(",", ":"))


def _uniform_prior(count: int) -> list[float]:
    return [1.0 / count] * count if count else []


def _softmax_prior(scores: list[float]) -> list[float]:
    if not scores:
        return []
    scaled = [score / PRIOR_SOFTMAX_TEMPERATURE for score in scores]
    high = max(scaled)
    weights = [math.exp(value - high) for value in scaled]
    total = sum(weights)
    return [value / total for value in weights]


def _normalized_entropy(prior: list[float]) -> float | None:
    if len(prior) < 2:
        return None
    entropy = -sum(value * math.log(value) for value in prior if value > 0.0)
    return entropy / math.log(len(prior))


_CATEGORY_SYNONYMS = {
    "phone": {"phone", "smartphone", "mobile phone", "cell phone"},
    "smartphone": {"phone", "smartphone", "mobile phone", "cell phone"},
}


def _source_accepts(target: dict[str, Any], source: dict[str, Any] | None) -> bool:
    accepted = target.get("accepted_categories", [])
    if not accepted or source is None:
        return True
    source_terms = {
        normalize_noun(value)
        for value in (source.get("object_type", ""), *source.get("affordance_tags", []))
        if isinstance(value, str) and value.strip()
    }
    for category in accepted:
        if not isinstance(category, str):
            continue
        normalized = normalize_noun(category)
        aliases = _CATEGORY_SYNONYMS.get(normalized, {normalized})
        if any(alias in source_terms for alias in aliases):
            return True
    return False


def _target_state(target: dict[str, Any]) -> dict[str, Any]:
    state = target.get("current_state", {})
    return state if isinstance(state, dict) else {}


def _destination_unavailable(target: dict[str, Any]) -> bool:
    state = _target_state(target)
    if state.get("full") is True or state.get("available") is False or state.get("free") is False:
        return True
    for field in ("availability", "occupancy", "status"):
        value = state.get(field)
        if isinstance(value, str) and value.strip().lower() in {"full", "occupied", "unavailable", "not_available"}:
            return True
    return False


def _relation_supported(relation: str, target: dict[str, Any], source: dict[str, Any] | None) -> bool:
    if relation == "none":
        return True
    if relation == "charge":
        return bool(target.get("charging_target")) and _source_accepts(target, source)
    if relation == "store":
        if target.get("container"):
            return not _destination_unavailable(target)
        return bool(target.get("support_surface")) and not _destination_unavailable(target) and _source_accepts(target, source)
    if relation == "place_on":
        return bool(target.get("support_surface")) and not _destination_unavailable(target) and _source_accepts(target, source)
    if relation == "place_in":
        if not target.get("container") or _destination_unavailable(target):
            return False
        state = _target_state(target)
        lid = state.get("lid", state.get("is_open"))
        closed = lid is False or (isinstance(lid, str) and lid.strip().lower() in {"closed", "shut"})
        return not closed or bool(target.get("openable"))
    if relation == "handover":
        return bool("user_zone" in target.get("affordance_tags", []) or target.get("support_surface"))
    if relation == "press":
        return bool(target.get("pressable"))
    if relation == "rinse":
        return bool({"water_source", "sink"} & set(target.get("affordance_tags", [])))
    if relation == "support":
        return bool(target.get("support_surface"))
    return False


def _validate_model_response(
    candidate: Any,
    *,
    context: dict[str, Any],
    scene: SemanticSceneCore,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(candidate, dict):
        return ["response must be a JSON object"]
    if candidate.get("status") not in CONTEXT_STATUSES:
        errors.append("status is outside the Context status enum")
    reason = candidate.get("reason_code")
    if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", reason):
        errors.append("reason_code must be a short snake_case token")
    scores = candidate.get("candidate_scores")
    expected = context["candidate_next_object_ids"]
    if not isinstance(scores, list) or len(scores) != len(expected):
        return errors + ["candidate_scores must contain every candidate exactly once"]
    seen: set[str] = set()
    source_id = context.get("source_object_id")
    source = scene.objects.get(source_id) if isinstance(source_id, str) else None
    if isinstance(source_id, str) and source is None:
        errors.append("source_object_id is not in the scene")
    for index, item in enumerate(scores):
        if not isinstance(item, dict):
            errors.append("candidate_scores[{}] must be an object".format(index))
            continue
        candidate_id = item.get("candidate_id")
        if not isinstance(candidate_id, str) or candidate_id not in expected:
            errors.append("candidate_scores contains an unknown candidate ID")
            continue
        if candidate_id in seen:
            errors.append("candidate_scores contains a duplicate candidate ID")
        seen.add(candidate_id)
        score = item.get("semantic_score")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or not 0.0 <= float(score) <= 1.0:
            errors.append("semantic_score must be a finite number from 0 through 1")
        relation = item.get("relation_type")
        if relation not in RELATION_TYPES:
            errors.append("relation_type is outside the relation enum")
        elif candidate_id in scene.objects and relation != "none":
            if not _relation_supported(relation, scene.objects[candidate_id], source):
                errors.append("relation_type violates candidate affordances: " + candidate_id)
    if seen != set(expected):
        errors.append("candidate_scores IDs do not match candidate_next_object_ids")
    return errors


class SemanticContextEngine:
    """Rank next-object candidates from pre-decision scene and task semantics."""

    def __init__(
        self,
        client: ContextCompletionClient,
        model_id: str,
        *,
        base_url: str = "https://api.deepseek.com",
        max_correction_retries: int = MAX_CORRECTION_RETRIES,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required")
        if max_correction_retries < 0 or max_correction_retries > MAX_CORRECTION_RETRIES:
            raise ValueError("Context correction retries are bounded to zero or one")
        self.client = client
        self.model_id = model_id
        self.base_url = base_url.rstrip("/")
        self.max_correction_retries = max_correction_retries

    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "base_url": self.base_url,
            "prompt_version": CONTEXT_PROMPT_VERSION,
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "temperature": MODEL_TEMPERATURE,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "max_correction_retries": self.max_correction_retries,
            "semantic_score_interpretation": "ordinal plausibility; uncalibrated",
            "prior_transform": "softmax(semantic_score / 0.20); not calibrated",
        }

    def predict(self, context_input: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            if not isinstance(context_input, dict):
                raise ValueError("context_input must be an object")
            scene_data = context_input.get("scene")
            scene = SemanticSceneCore(
                scene_data,
                selection_history=context_input.get("selection_history", []),
                current_task_state=context_input.get("current_task_state", {}),
            )
            candidates = context_input.get("candidate_next_object_ids")
            if not isinstance(candidates, list) or len(candidates) < 2 or not all(isinstance(x, str) for x in candidates):
                raise ValueError("at least two candidate_next_object_ids are required")
            if len(set(candidates)) != len(candidates):
                raise ValueError("candidate_next_object_ids must be unique")
            unknown = [item for item in candidates if item not in scene.objects or not scene.objects[item].get("selectable", False)]
            if unknown:
                return self._result("invalid", "candidate_not_grounded", context_input, [], [], 0, [], started)
            source_id = context_input.get("source_object_id")
            if source_id is not None and (not isinstance(source_id, str) or source_id not in scene.objects):
                return self._result("invalid", "source_not_grounded", context_input, [], [], 0, [], started)
            task = context_input.get("current_task_state", {})
            if not isinstance(task, dict):
                return self._result("invalid", "task_state_invalid", context_input, [], [], 0, [], started)
            if task.get("phase") in {"complete", "completed", "done"}:
                return self._result("context_off", "task_already_complete", context_input, [], _uniform_prior(len(candidates)), 0, [], started)
            requested = task.get("requested_destination_alias")
            if isinstance(requested, str) and requested.strip():
                from integration.semantic_intelligence import ObjectGrounder
                grounded = ObjectGrounder().ground(requested, scene)
                if grounded["status"] != "grounded":
                    return self._result("invalid", "requested_destination_missing", context_input, [], [], 0, [], started)
                if grounded["object_id"] not in candidates:
                    return self._result("invalid", "requested_destination_not_candidate", context_input, [], [], 0, [], started)
            valid_candidates = [
                object_id for object_id in candidates
                if self._candidate_has_semantic_relation(scene, source_id, object_id)
            ]
            required_relation = task.get("required_relation")
            if required_relation is not None and (
                not isinstance(required_relation, str)
                or required_relation not in RELATION_TYPES
                or required_relation == "none"
            ):
                return self._result("invalid", "required_relation_invalid", context_input, [], [], 0, [], started)
            if required_relation and not any(
                self._candidate_supports_required_relation(scene, source_id, object_id, required_relation)
                for object_id in candidates
            ):
                return self._result("invalid", "required_relation_incompatible_with_scene", context_input, [], [], 0, [], started)
            if not valid_candidates:
                return self._result("context_off", "no_affordance_compatible_candidate", context_input, [], _uniform_prior(len(candidates)), 0, [], started)
        except (ValueError, TypeError, KeyError) as error:
            return self._result("invalid", "invalid_context_input", {}, [], [], 0, [], started, diagnostic=type(error).__name__)

        correction: dict[str, Any] | None = None
        diagnostics: list[dict[str, Any]] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        response_candidate: Any = None
        for attempt in range(self.max_correction_retries + 1):
            messages = [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(context_input, correction)},
            ]
            try:
                response = self.client.complete(
                    messages,
                    model_id=self.model_id,
                    temperature=MODEL_TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                )
            except DeepSeekClientError:
                return self._result("context_off", "api_failure", context_input, diagnostics, _uniform_prior(len(candidates)), attempt, [], started, usage=usage)
            for key in usage:
                value = (response.get("usage") or {}).get(key, 0)
                if isinstance(value, int) and value >= 0:
                    usage[key] += value
            content = response.get("content")
            try:
                response_candidate = json.loads(content) if isinstance(content, str) else content
            except (TypeError, json.JSONDecodeError):
                response_candidate = None
            errors = _validate_model_response(response_candidate, context=context_input, scene=scene)
            if not errors:
                score_by_id = {row["candidate_id"]: float(row["semantic_score"]) for row in response_candidate["candidate_scores"]}
                relation_by_id = {row["candidate_id"]: row["relation_type"] for row in response_candidate["candidate_scores"]}
                ordered = sorted(candidates, key=lambda object_id: (-score_by_id[object_id], candidates.index(object_id)))
                raw_scores = [score_by_id[object_id] for object_id in candidates]
                prior = _softmax_prior(raw_scores)
                sorted_scores = sorted(raw_scores, reverse=True)
                confidence = sorted_scores[0]
                margin = sorted_scores[0] - sorted_scores[1]
                model_status = response_candidate["status"]
                required_relation = context_input.get("current_task_state", {}).get("required_relation")
                requested_alias = context_input.get("current_task_state", {}).get("requested_destination_alias")
                if model_status == "informative" and required_relation and relation_by_id[ordered[0]] != required_relation:
                    errors = ["top-ranked candidate does not satisfy the required relation"]
                if model_status == "informative" and requested_alias:
                    from integration.semantic_intelligence import ObjectGrounder
                    requested_id = ObjectGrounder().ground(requested_alias, scene).get("object_id")
                    if requested_id is not None and ordered[0] != requested_id:
                        errors = ["top-ranked candidate does not match the requested destination"]
                if errors:
                    diagnostics.append({"attempt": attempt + 1, "validation_errors": errors})
                    correction = {
                        "instruction": "Correct the response so its top-ranked candidate obeys the required relation and explicit destination.",
                        "prior_response": response_candidate,
                        "validation_errors": errors,
                    }
                    continue
                gate_active = (
                    model_status == "informative"
                    and confidence >= ACTIVE_MIN_SEMANTIC_SCORE
                    and margin >= ACTIVE_MIN_SCORE_MARGIN
                )
                decision = "active" if gate_active else "off"
                reason_code = response_candidate["reason_code"]
                if model_status == "informative" and not gate_active:
                    reason_code = "below_fixed_active_confidence_gate"
                result_rows = [
                    {"candidate_id": object_id, "semantic_score": score_by_id[object_id], "relation_type": relation_by_id[object_id]}
                    for object_id in ordered
                ]
                return self._result(
                    model_status, reason_code, context_input, diagnostics, prior, attempt + 1,
                    result_rows, started, usage=usage, confidence=confidence, margin=margin,
                    ranking=ordered, coverage_decision=decision,
                )
            diagnostics.append({"attempt": attempt + 1, "validation_errors": errors})
            correction = {
                "instruction": "Return a corrected object matching the schema, using only the supplied IDs and affordances. If uncertain, choose ambiguous or context_off.",
                "prior_response": response_candidate if isinstance(response_candidate, dict) else None,
                "validation_errors": errors,
            }
        return self._result(
            "context_off", "model_output_invalid_after_bounded_retry", context_input, diagnostics,
            _uniform_prior(len(candidates)), self.max_correction_retries + 1, [], started, usage=usage,
        )

    @staticmethod
    def _candidate_supports_required_relation(scene: SemanticSceneCore, source_id: str | None, candidate_id: str, relation: str) -> bool:
        source = scene.objects.get(source_id) if source_id else None
        return relation in RELATION_TYPES and _relation_supported(relation, scene.objects[candidate_id], source)

    @classmethod
    def _candidate_has_semantic_relation(cls, scene: SemanticSceneCore, source_id: str | None, candidate_id: str) -> bool:
        return any(cls._candidate_supports_required_relation(scene, source_id, candidate_id, relation) for relation in RELATION_TYPES if relation != "none")

    def _result(
        self,
        status: str,
        reason_code: str,
        context_input: dict[str, Any],
        diagnostics: list[dict[str, Any]],
        prior: list[float],
        attempt_count: int,
        score_rows: list[dict[str, Any]],
        started: float,
        *,
        usage: dict[str, int] | None = None,
        confidence: float | None = None,
        margin: float | None = None,
        ranking: list[str] | None = None,
        coverage_decision: str = "off",
        diagnostic: str | None = None,
    ) -> dict[str, Any]:
        candidates = context_input.get("candidate_next_object_ids", []) if isinstance(context_input, dict) else []
        if not prior:
            prior = _uniform_prior(len(candidates))
        ranking = ranking or [row["candidate_id"] for row in score_rows]
        ordered_priors = sorted(prior, reverse=True)
        raw_margin = margin
        output = {
            "status": status,
            "candidate_scores": score_rows,
            "candidate_ranking": ranking,
            "context_prior": prior,
            "prior_interpretation": "uncalibrated softmax over semantic plausibility scores; temperature=0.20",
            "top_candidate": ranking[0] if status == "informative" and ranking else None,
            "semantic_confidence": confidence,
            "entropy": _normalized_entropy(prior),
            "margin": raw_margin,
            "prior_top_mass": ordered_priors[0] if ordered_priors else None,
            "prior_margin": ordered_priors[0] - ordered_priors[1] if len(ordered_priors) > 1 else None,
            "coverage_decision": coverage_decision,
            "reason_code": reason_code,
            "provenance": {
                "model_id": self.model_id,
                "base_url": self.base_url,
                "prompt_version": CONTEXT_PROMPT_VERSION,
                "schema_version": CONTEXT_SCHEMA_VERSION,
                "temperature": MODEL_TEMPERATURE,
                "prior_transform_temperature": PRIOR_SOFTMAX_TEMPERATURE,
                "timestamp_utc": _now_utc(),
                "api_latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "attempt_count": attempt_count,
                "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "diagnostics": diagnostics,
            },
        }
        if diagnostic:
            output["input_diagnostic"] = diagnostic
        return output


def build_live_context_engine() -> tuple[SemanticContextEngine, list[str], bool]:
    client, model_id, available, configured = build_live_client()
    engine = SemanticContextEngine(client, model_id, max_correction_retries=MAX_CORRECTION_RETRIES)
    return engine, available, configured
