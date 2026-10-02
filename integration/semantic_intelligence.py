"""Constrained, analysis-only semantic planner for M27.

This module accepts the M20 SceneLayoutSnapshot payload and BCI-selected IDs,
then validates a model-produced task plan. It does not dispatch robot actions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from difflib import SequenceMatcher
import json
import os
import re
from typing import Any, Protocol
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PROMPT_VERSION = "m27-semantic-planner-prompt-v3"
SCHEMA_VERSION = "m27-structured-plan-v1"
SCENE_SCHEMA_VERSION = "m27-semantic-scene-v1"
ALLOWED_ACTIONS = (
    "PICK", "PLACE_ON", "PLACE_IN", "MOVE_TO", "OPEN", "CLOSE", "PRESS", "RELEASE"
)
OFFICIAL_CHAT_COMPLETION_MODELS = ("deepseek-flash", "deepseek-v4-pro")
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_OUTPUT_TOKENS = 1200
DEFAULT_TIMEOUT_SECONDS = 45


class PlannerInputError(ValueError):
    """The scene snapshot or selected-object input is malformed."""


class DeepSeekClientError(RuntimeError):
    """A sanitized DeepSeek request failure; response bodies are never attached."""


class CompletionClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model_id: str,
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]: ...


def _timestamp_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _tags(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise PlannerInputError("{} must be a string array".format(field))
    return [item.strip().lower() for item in value if item.strip()]


def _destination_category(tag: str) -> str | None:
    if tag.endswith("_destination") and tag != "placement_destination":
        return tag[: -len("_destination")].replace("_", " ")
    return None


def semantic_scene_from_snapshot(
    snapshot: dict[str, Any],
    current_states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Translate the production M20 SceneLayoutSnapshot shape to semantic facts.

    State not present in the snapshot stays unknown unless the caller provides
    an explicit current-state observation with the scene snapshot.
    """
    if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
        raise PlannerInputError("SceneLayoutSnapshot schemaVersion must be 1")
    scene_id = snapshot.get("sceneId")
    template_id = snapshot.get("templateId")
    objects = snapshot.get("objects")
    if not isinstance(scene_id, str) or not scene_id.strip():
        raise PlannerInputError("SceneLayoutSnapshot sceneId is required")
    if not isinstance(template_id, str) or not template_id.strip():
        raise PlannerInputError("SceneLayoutSnapshot templateId is required")
    if not isinstance(objects, list) or not objects:
        raise PlannerInputError("SceneLayoutSnapshot objects must be a non-empty array")
    current_states = current_states or {}
    if not isinstance(current_states, dict):
        raise PlannerInputError("current_states must be an object keyed by semanticId")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(objects):
        field = "objects[{}]".format(index)
        if not isinstance(item, dict):
            raise PlannerInputError(field + " must be an object")
        semantic_id = item.get("semanticId")
        object_type = item.get("objectType")
        role = item.get("role", "")
        source_kind = item.get("sourceKind", "")
        if not isinstance(semantic_id, str) or not semantic_id.strip():
            raise PlannerInputError(field + ".semanticId is required")
        if semantic_id in seen:
            raise PlannerInputError("duplicate semanticId: " + semantic_id)
        seen.add(semantic_id)
        if not isinstance(object_type, str) or not object_type.strip():
            raise PlannerInputError(field + ".objectType is required")
        if not isinstance(role, str) or not isinstance(source_kind, str):
            raise PlannerInputError(field + " role/sourceKind must be strings")
        tags = _tags(item.get("affordanceTags", []), field + ".affordanceTags")
        state = current_states.get(semantic_id, {})
        if state is None:
            state = {}
        if not isinstance(state, dict):
            raise PlannerInputError("current state for {} must be an object".format(semantic_id))
        position = item.get("positionMeters")
        dimensions = item.get("dimensionsMeters")
        normalized.append({
            "id": semantic_id,
            "target_id": str(item.get("targetId") or ""),
            "logical_block_id": str(item.get("logicalBlockId") or ""),
            "name": object_type.replace("_", " "),
            "object_type": object_type,
            "role": role,
            "source_kind": source_kind,
            "selectable": bool(item.get("selectable", False)),
            "fixed_pose": bool(item.get("fixedPose", False)),
            "position_meters": deepcopy(position),
            "dimensions_meters": deepcopy(dimensions),
            "affordance_tags": tags,
            "movable": "movable" in tags,
            "container": any(tag == "container" or tag.endswith("_container") for tag in tags),
            "openable": "openable" in tags or "hinged_lid" in tags,
            "closable": "closable" in tags or "hinged_lid" in tags,
            "support_surface": (
                "fixed_surface" in tags
                or "placement_destination" in tags
                or any(tag.endswith("_destination") for tag in tags)
            ),
            "charging_target": any("charger" in tag or "charging_target" in tag for tag in tags),
            "pressable": "pressable" in tags,
            "accepted_categories": sorted({
                category for tag in tags if (category := _destination_category(tag)) is not None
            }),
            "current_state": deepcopy(state),
        })
    return {
        "schema_version": SCENE_SCHEMA_VERSION,
        "scene_id": scene_id,
        "template_id": template_id,
        "snapshot_schema_version": snapshot["schemaVersion"],
        "random_seed": snapshot.get("randomSeed"),
        "coordinate_frame": deepcopy(snapshot.get("coordinateFrame", {})),
        "table": deepcopy(snapshot.get("table", {})),
        "candidate_order": list(snapshot.get("candidateOrderFarToNearLeftToRight", [])),
        "objects": normalized,
    }


def _system_prompt() -> str:
    return """You are a semantic task planner for a constrained tabletop manipulation research prototype.
Return exactly one JSON object matching schema m27-structured-plan-v1. Do not use markdown.
Use the exact lowercase status values `executable`, `ambiguous`, or `invalid`; use uppercase action types from the whitelist.
You do not control a robot; your output is an offline VLA-ready semantic plan only.

Use the supplied scene facts and BCI selected IDs. Preserve selected_objects in the exact input order.
Optional intent_context may clarify the task, but it never makes a missing object or unsupported affordance valid.
Do not invent objects, states, affordances, or user intent. Distinguish selected task objects from contextual targets.
Allowed actions: PICK, PLACE_ON, PLACE_IN, MOVE_TO, OPEN, CLOSE, PRESS, RELEASE.
Action fields: PICK={type,object_id}; OPEN/CLOSE/PRESS={type,object_id};
PLACE_ON/PLACE_IN/MOVE_TO/RELEASE={type,object_id,target_id}.
Use PLACE_ON for a supported surface relation and PLACE_IN only for a container.
Include OPEN/CLOSE only when the scene identifies an openable/closable object and the current state is known.
For an executable plan, include ordered actions and a concise natural_language_instruction.
Before choosing, enumerate relations supported by the BCI-selected objects. If two or more different selected-object
relations are affordance-valid and intent_context does not identify one destination, return status=ambiguous, actions=[],
at least two plausible, affordance-valid alternatives, and ambiguity_reason. Never pick the most salient destination
merely because it is a charger, container, or familiar object.
If intent_context names a destination that is not present in the supplied scene, return status=invalid with no actions;
do not substitute a different available destination.
For a closed container with known closed state, a multi-step storage plan should open it, pick the selected source,
place the source inside, then close it. If the container state is unknown, do not assume it is closed or open.
If the request is impossible or unsupported, return status=invalid, actions=[], and explain why.
The top-level keys are: status, intent_summary, selected_objects, actions,
natural_language_instruction, assumptions, ambiguity_reason, alternatives.
Every alternative must be an object with exactly these keys:
intent_summary, actions, natural_language_instruction. Do not omit its language instruction.
"""


def _user_prompt(
    scene: dict[str, Any],
    selected_object_ids: list[str],
    *,
    intent_context: str | None = None,
    correction: dict[str, Any] | None = None,
) -> str:
    request: dict[str, Any] = {
        "task": "Infer a plausible manipulation intent from selected objects and the current scene.",
        "schema_version": SCHEMA_VERSION,
        "selected_object_ids_in_bci_order": selected_object_ids,
        "allowed_action_vocabulary": list(ALLOWED_ACTIONS),
        "scene": scene,
    }
    if intent_context is not None:
        request["intent_context"] = intent_context
    if correction is not None:
        request["correction"] = correction
    return json.dumps(request, ensure_ascii=False, separators=(",", ":"))


def _normalize_structured_plan(candidate: Any) -> tuple[Any, list[str]]:
    """Normalize enum casing while preserving all model-selected content."""
    if not isinstance(candidate, dict):
        return candidate, []
    normalized = deepcopy(candidate)
    changes: list[str] = []
    status = normalized.get("status")
    if isinstance(status, str) and status != status.strip().lower():
        normalized["status"] = status.strip().lower()
        changes.append("status_enum_case")
    for location, actions in [("actions", normalized.get("actions"))]:
        if isinstance(actions, list):
            for index, action in enumerate(actions):
                if isinstance(action, dict) and isinstance(action.get("type"), str):
                    action_type = action["type"].strip().upper()
                    if action_type != action["type"]:
                        action["type"] = action_type
                        changes.append("{}_{}_type_case".format(location, index))
    alternatives = normalized.get("alternatives")
    if isinstance(alternatives, list):
        for alternative_index, alternative in enumerate(alternatives):
            actions = alternative.get("actions") if isinstance(alternative, dict) else None
            if isinstance(actions, list):
                for action_index, action in enumerate(actions):
                    if isinstance(action, dict) and isinstance(action.get("type"), str):
                        action_type = action["type"].strip().upper()
                        if action_type != action["type"]:
                            action["type"] = action_type
                            changes.append("alternative_{}_{}_type_case".format(alternative_index, action_index))
    return normalized, changes


def _fallback_invalid(selected: list[str], reason: str) -> dict[str, Any]:
    return {
        "status": "invalid",
        "intent_summary": "No validated plan was produced.",
        "selected_objects": list(selected),
        "actions": [],
        "natural_language_instruction": "",
        "assumptions": [],
        "ambiguity_reason": reason,
        "alternatives": [],
    }


def _semantic_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower().replace("_", " ")) if len(token) >= 3}


_GENERIC_CONTEXT_WORDS = {
    "the", "and", "with", "from", "onto", "into", "inside", "place", "put", "move", "selected",
    "compatible", "target", "surface", "object", "objects", "item", "items", "task", "side",
    "what", "these", "help", "decide", "about", "this", "that", "away", "small", "current",
}


def _target_context_tokens(obj: dict[str, Any]) -> set[str]:
    terms = _semantic_tokens(" ".join((obj["id"], obj["name"], obj["object_type"])))
    return terms - _GENERIC_CONTEXT_WORDS


def _selected_relation_candidates(
    scene: dict[str, Any], selected_object_ids: list[str], current_states: dict[str, dict[str, Any]] | None
) -> list[dict[str, str]]:
    objects = {item["id"]: item for item in scene["objects"]}
    selected = [objects[item] for item in selected_object_ids if item in objects]
    state = current_states or {}
    candidates: list[dict[str, str]] = []
    for source in selected:
        if not source["movable"]:
            continue
        for target in selected:
            if target["id"] == source["id"]:
                continue
            if target["support_surface"] and (
                not target["accepted_categories"]
                or any(category in _object_tokens(source) for category in target["accepted_categories"])
            ):
                candidates.append({"type": "PLACE_ON", "object_id": source["id"], "target_id": target["id"]})
            if target["container"]:
                if not target["openable"] or _state_lid(target, state) is not None:
                    candidates.append({"type": "PLACE_IN", "object_id": source["id"], "target_id": target["id"]})
    return candidates


def _missing_proper_destination(intent_context: str | None, scene: dict[str, Any]) -> str | None:
    if not intent_context:
        return None
    object_text = " ".join(
        "{} {} {}".format(item["id"], item["name"], item["object_type"])
        for item in scene["objects"]
    )
    known = _semantic_tokens(object_text)
    pattern = re.compile(r"\b(?:on|onto|in|into|inside|at|to)\s+(?:the\s+)?([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,2})\b")
    for match in pattern.finditer(intent_context):
        phrase = match.group(1).strip()
        tokens = _semantic_tokens(phrase)
        if tokens and not tokens.issubset(known):
            return phrase
    return None


def _fallback_ambiguous(
    selected: list[str],
    candidates: list[dict[str, str]],
    objects: dict[str, dict[str, Any]],
    current_states: dict[str, dict[str, Any]] | None,
) -> dict[str, Any]:
    alternatives = []
    for relation in candidates:
        source = objects[relation["object_id"]]
        target = objects[relation["target_id"]]
        if relation["type"] == "PLACE_ON":
            actions = [
                {"type": "PICK", "object_id": source["id"]},
                relation,
            ]
            instruction = "Place {} on {}.".format(source["name"], target["name"])
            summary = "Place the selected source on the supported surface {}.".format(target["id"])
        else:
            lid_state = _state_lid(target, current_states or {})
            actions = []
            if target["openable"] and lid_state is False:
                actions.append({"type": "OPEN", "object_id": target["id"]})
            actions.append({"type": "PICK", "object_id": source["id"]})
            actions.append(relation)
            if target["closable"] and lid_state is False:
                actions.append({"type": "CLOSE", "object_id": target["id"]})
            instruction = "Place {} inside {}{}.".format(
                source["name"], target["name"], " and close it" if target["closable"] and lid_state is False else ""
            )
            summary = "Store the selected source in the container {}.".format(target["id"])
        alternatives.append({
            "intent_summary": summary,
            "actions": actions,
            "natural_language_instruction": instruction,
        })
    return {
        "status": "ambiguous",
        "intent_summary": "Multiple selected-object relations remain plausible.",
        "selected_objects": list(selected),
        "actions": [],
        "natural_language_instruction": "",
        "assumptions": [],
        "ambiguity_reason": "The selected objects support multiple relations and the intent context does not choose one.",
        "alternatives": alternatives,
    }


def _object_tokens(obj: dict[str, Any]) -> str:
    return " ".join([obj["object_type"], *obj["affordance_tags"]]).lower().replace("_", " ")


def _state_lid(obj: dict[str, Any], mutable_states: dict[str, dict[str, Any]]) -> bool | None:
    state = mutable_states.get(obj["id"], {})
    value = state.get("lid", state.get("is_open"))
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"open", "opened"}:
            return True
        if lowered in {"closed", "shut"}:
            return False
    return None


def _validate_action_sequence(
    actions: Any,
    *,
    objects: dict[str, dict[str, Any]],
    selected: set[str],
    initial_states: dict[str, dict[str, Any]],
    where: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(actions, list):
        return [where + ".actions must be an array"]
    states = deepcopy(initial_states)
    held: set[str] = set()
    placed: set[str] = set()

    for index, action in enumerate(actions):
        label = "{}[{}]".format(where, index)
        if not isinstance(action, dict):
            errors.append(label + " must be an object")
            continue
        action_type = action.get("type")
        if not isinstance(action_type, str) or action_type not in ALLOWED_ACTIONS:
            errors.append(label + ".type is not in the action whitelist")
            continue
        object_id = action.get("object_id")
        target_id = action.get("target_id")
        if not isinstance(object_id, str) or object_id not in objects:
            errors.append(label + " references an unknown object_id")
            continue
        obj = objects[object_id]
        target = objects.get(target_id) if isinstance(target_id, str) else None
        if action_type in {"PLACE_ON", "PLACE_IN", "MOVE_TO", "RELEASE"}:
            if target is None:
                errors.append(label + " references an unknown or missing target_id")
                continue

        if action_type in {"PICK", "PLACE_ON", "PLACE_IN", "MOVE_TO", "RELEASE"} and object_id not in selected:
            errors.append(label + " action source is not BCI-selected")
        if action_type == "PICK":
            if not obj["movable"]:
                errors.append(label + " PICK requires a movable object")
            elif object_id in held:
                errors.append(label + " object is already held")
            else:
                held.add(object_id)
        elif action_type == "PLACE_ON":
            if not obj["movable"]:
                errors.append(label + " PLACE_ON source must be movable")
            if not target["support_surface"]:
                errors.append(label + " PLACE_ON target is not a supported surface")
            elif target["accepted_categories"] and not any(
                category in _object_tokens(obj) for category in target["accepted_categories"]
            ):
                errors.append(label + " PLACE_ON target does not accept the source category")
            held.discard(object_id)
            placed.add(object_id)
        elif action_type == "PLACE_IN":
            if not obj["movable"]:
                errors.append(label + " PLACE_IN source must be movable")
            if not target["container"]:
                errors.append(label + " PLACE_IN target is not a container")
            elif target["openable"] and _state_lid(target, states) is not True:
                errors.append(label + " PLACE_IN requires the openable container to be known open")
            held.discard(object_id)
            placed.add(object_id)
        elif action_type == "MOVE_TO":
            if not obj["movable"]:
                errors.append(label + " MOVE_TO source must be movable")
            if not target["support_surface"] and not target["container"]:
                errors.append(label + " MOVE_TO target is not a supported destination")
            held.discard(object_id)
            placed.add(object_id)
        elif action_type == "OPEN":
            if not obj["openable"]:
                errors.append(label + " OPEN target is not openable")
            else:
                current = _state_lid(obj, states)
                if current is None:
                    errors.append(label + " OPEN requires a known current lid state")
                elif current:
                    errors.append(label + " OPEN target is already open")
                else:
                    states.setdefault(object_id, {})["lid"] = "open"
        elif action_type == "CLOSE":
            if not obj["closable"]:
                errors.append(label + " CLOSE target is not closable")
            else:
                current = _state_lid(obj, states)
                if current is None:
                    errors.append(label + " CLOSE requires a known current lid state")
                elif not current:
                    errors.append(label + " CLOSE target is already closed")
                else:
                    states.setdefault(object_id, {})["lid"] = "closed"
        elif action_type == "PRESS":
            if not obj["pressable"]:
                errors.append(label + " PRESS target is not pressable")
        elif action_type == "RELEASE":
            if object_id not in held:
                errors.append(label + " RELEASE requires a preceding PICK")
            if not target["support_surface"] and not target["container"]:
                errors.append(label + " RELEASE target is not a supported destination")
            held.discard(object_id)
            placed.add(object_id)
    return errors


def validate_plan(
    plan: Any,
    *,
    scene: dict[str, Any],
    selected_object_ids: list[str],
    current_states: dict[str, dict[str, Any]] | None = None,
    intent_context: str | None = None,
) -> dict[str, Any]:
    """Validate the public structured-plan contract against scene affordances."""
    errors: list[str] = []
    checks = {
        "schema_valid": False,
        "selected_object_identity": False,
        "object_grounding": False,
        "action_whitelist": False,
        "affordance_consistency": False,
        "state_consistency": False,
        "ambiguity_handling": False,
    }
    if not isinstance(plan, dict):
        return {"valid": False, "checks": checks, "errors": ["plan must be a JSON object"]}
    required = {
        "status", "intent_summary", "selected_objects", "actions",
        "natural_language_instruction", "assumptions", "ambiguity_reason", "alternatives",
    }
    if not required.issubset(plan):
        errors.append("plan is missing required top-level fields")
    status = plan.get("status")
    if status not in {"executable", "ambiguous", "invalid"}:
        errors.append("status must be executable, ambiguous, or invalid")
    strings = ("intent_summary", "natural_language_instruction")
    for field in strings:
        if not isinstance(plan.get(field), str):
            errors.append(field + " must be a string")
    if not isinstance(plan.get("assumptions"), list) or not all(
        isinstance(item, str) for item in plan.get("assumptions", [])
    ):
        errors.append("assumptions must be a string array")
    checks["schema_valid"] = not errors

    objects = {item["id"]: item for item in scene["objects"]}
    if plan.get("selected_objects") != selected_object_ids:
        errors.append("selected_objects must exactly preserve the BCI ID set and order")
    else:
        checks["selected_object_identity"] = True
    unknown_selected = [item for item in selected_object_ids if item not in objects]
    if unknown_selected:
        errors.append("selected input contains unknown object IDs")
    elif any(not objects[item]["selectable"] for item in selected_object_ids):
        errors.append("selected input contains a non-selectable scene object")
    else:
        checks["object_grounding"] = True

    actions = plan.get("actions")
    if status == "executable":
        if not isinstance(actions, list) or not actions:
            errors.append("executable plan must contain at least one action")
        if not plan.get("natural_language_instruction", "").strip():
            errors.append("executable plan needs a natural-language instruction")
    elif isinstance(actions, list) and actions:
        errors.append("ambiguous/invalid plan must not expose executable top-level actions")

    initial_states = current_states or {}
    missing_target = _missing_proper_destination(intent_context, scene)
    if missing_target and status in {"executable", "ambiguous"}:
        errors.append("intent_context names a destination absent from the scene: " + missing_target)
    relation_candidates = _selected_relation_candidates(scene, selected_object_ids, current_states)
    context_tokens = _semantic_tokens(intent_context or "") - _GENERIC_CONTEXT_WORDS
    mentioned_targets = {
        relation["target_id"] for relation in relation_candidates
        if _target_context_tokens(objects[relation["target_id"]]) & context_tokens
    }
    if status == "executable" and len(relation_candidates) > 1 and not mentioned_targets:
        errors.append("selected objects support multiple relations but intent_context names no destination; return ambiguous")
    if status == "invalid" and len(relation_candidates) > 1 and not mentioned_targets and not missing_target:
        errors.append("multiple supported relations remain; return ambiguous rather than invalid")
    if status == "executable" and mentioned_targets:
        relation_actions = [action for action in actions if isinstance(action, dict) and action.get("type") in {"PLACE_ON", "PLACE_IN", "MOVE_TO", "RELEASE"}] if isinstance(actions, list) else []
        if relation_actions and any(action.get("target_id") not in mentioned_targets for action in relation_actions):
            errors.append("executable relation target does not match the destination named in intent_context")
    action_errors = _validate_action_sequence(
        actions,
        objects=objects,
        selected=set(selected_object_ids),
        initial_states=initial_states,
        where="plan",
    )
    errors.extend(action_errors)
    checks["action_whitelist"] = isinstance(actions, list) and all(
        isinstance(item, dict) and item.get("type") in ALLOWED_ACTIONS for item in actions
    )
    checks["affordance_consistency"] = not action_errors
    checks["state_consistency"] = not any("state" in item.lower() or "known open" in item.lower() for item in action_errors)

    ambiguity_reason = plan.get("ambiguity_reason")
    alternatives = plan.get("alternatives")
    if not isinstance(alternatives, list):
        errors.append("alternatives must be an array")
        alternatives = []
    if status == "ambiguous":
        if not isinstance(ambiguity_reason, str) or not ambiguity_reason.strip():
            errors.append("ambiguous plan requires ambiguity_reason")
        if len(alternatives) < 2:
            errors.append("ambiguous plan requires at least two plausible alternatives")
        for index, alternative in enumerate(alternatives):
            if not isinstance(alternative, dict) or not isinstance(alternative.get("intent_summary"), str):
                errors.append("alternative {} needs an intent_summary".format(index))
                continue
            if not isinstance(alternative.get("natural_language_instruction"), str):
                errors.append("alternative {} needs a natural_language_instruction".format(index))
                continue
            errors.extend(_validate_action_sequence(
                alternative.get("actions"),
                objects=objects,
                selected=set(selected_object_ids),
                initial_states=initial_states,
                where="alternatives[{}]".format(index),
            ))
        checks["ambiguity_handling"] = (
            isinstance(ambiguity_reason, str) and bool(ambiguity_reason.strip())
            and len(alternatives) >= 2
        )
    elif status == "invalid":
        if not isinstance(ambiguity_reason, str) or not ambiguity_reason.strip():
            errors.append("invalid plan requires an explanatory ambiguity_reason")
        checks["ambiguity_handling"] = bool(isinstance(ambiguity_reason, str) and ambiguity_reason.strip())
    else:
        checks["ambiguity_handling"] = True

    return {"valid": not errors, "checks": checks, "errors": errors}


class SemanticPlanner:
    """Model client adapter plus structured plan validation and one correction retry."""

    def __init__(
        self,
        client: CompletionClient,
        model_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        temperature: float = DEFAULT_TEMPERATURE,
        max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
        max_correction_retries: int = 1,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required")
        if max_correction_retries < 0 or max_correction_retries > 1:
            raise ValueError("max_correction_retries must be between zero and one")
        self.client = client
        self.model_id = model_id
        self.base_url = sanitize_base_url(base_url)
        self.temperature = float(temperature)
        self.max_output_tokens = int(max_output_tokens)
        self.max_correction_retries = max_correction_retries

    def plan(
        self,
        snapshot: dict[str, Any],
        selected_object_ids: list[str],
        *,
        current_states: dict[str, dict[str, Any]] | None = None,
        intent_context: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(selected_object_ids, list) or not all(
            isinstance(item, str) and item for item in selected_object_ids
        ):
            raise PlannerInputError("selected_object_ids must be a non-empty ordered string array")
        if not selected_object_ids:
            raise PlannerInputError("at least one BCI-selected object is required")
        if len(set(selected_object_ids)) != len(selected_object_ids):
            raise PlannerInputError("selected_object_ids must not contain duplicates")
        if intent_context is not None and (not isinstance(intent_context, str) or len(intent_context) > 2000):
            raise PlannerInputError("intent_context must be a string of at most 2000 characters")
        scene = semantic_scene_from_snapshot(snapshot, current_states)
        objects = {item["id"]: item for item in scene["objects"]}
        input_errors = []
        for object_id in selected_object_ids:
            if object_id not in objects:
                input_errors.append("selected object does not exist in SceneLayoutSnapshot: " + object_id)
            elif not objects[object_id]["selectable"]:
                input_errors.append("selected scene object is not selectable: " + object_id)
        if input_errors:
            return {
                "plan": _fallback_invalid(selected_object_ids, "; ".join(input_errors)),
                "validation": {"valid": False, "checks": {}, "errors": input_errors},
                "attempt_count": 0,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "model_metadata": self.metadata(),
            }

        relation_candidates = _selected_relation_candidates(scene, selected_object_ids, current_states)
        missing_target = _missing_proper_destination(intent_context, scene)
        context_tokens = _semantic_tokens(intent_context or "") - _GENERIC_CONTEXT_WORDS
        mentioned_targets = {
            relation["target_id"] for relation in relation_candidates
            if _target_context_tokens(objects[relation["target_id"]]) & context_tokens
        }

        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        correction: dict[str, Any] | None = None
        last_errors: list[str] = []
        attempt_diagnostics: list[dict[str, Any]] = []
        normalization_events: list[dict[str, Any]] = []
        candidate: Any = None
        validation: dict[str, Any] = {"valid": False, "checks": {}, "errors": ["no model response"]}
        for attempt in range(self.max_correction_retries + 1):
            messages = [
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": _user_prompt(
                    scene, selected_object_ids, intent_context=intent_context, correction=correction
                )},
            ]
            response = self.client.complete(
                messages,
                model_id=self.model_id,
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
            )
            response_usage = response.get("usage") or {}
            for key in usage:
                value = response_usage.get(key, 0)
                if isinstance(value, int) and value >= 0:
                    usage[key] += value
            content = response.get("content")
            try:
                candidate = json.loads(content) if isinstance(content, str) else content
            except (TypeError, json.JSONDecodeError):
                candidate = None
            candidate, normalization_changes = _normalize_structured_plan(candidate)
            if normalization_changes:
                normalization_events.append({"attempt": attempt + 1, "changes": normalization_changes})
            validation = validate_plan(
                candidate,
                scene=scene,
                selected_object_ids=selected_object_ids,
                current_states=current_states,
                intent_context=intent_context,
            )
            if validation["valid"]:
                return {
                    "plan": candidate,
                    "model_candidate": candidate,
                    "validation": validation,
                    "attempt_count": attempt + 1,
                    "attempt_diagnostics": attempt_diagnostics,
                    "normalization_events": normalization_events,
                    "fallback_used": False,
                    "usage": usage,
                    "model_metadata": self.metadata(),
                }
            last_errors = validation["errors"] or ["response was not a valid structured plan"]
            attempt_diagnostics.append({
                "attempt": attempt + 1,
                "parsed_as_object": isinstance(candidate, dict),
                "response_characters": len(content) if isinstance(content, str) else None,
                "validation_errors": list(last_errors),
            })
            correction = {
                "instruction": "Correct the prior response using only the supplied scene and action contract. If no safe plan exists, return status invalid with no actions.",
                "previous_response": candidate if isinstance(candidate, dict) else str(content)[:2000],
                "validation_errors": last_errors,
            }
        if (
            relation_candidates
            and len(relation_candidates) > 1
            and not mentioned_targets
            and not missing_target
        ):
            fallback = _fallback_ambiguous(selected_object_ids, relation_candidates, objects, current_states)
            fallback_validation = validate_plan(
                fallback,
                scene=scene,
                selected_object_ids=selected_object_ids,
                current_states=current_states,
                intent_context=intent_context,
            )
            if fallback_validation["valid"]:
                return {
                    "plan": fallback,
                    "model_candidate": candidate if isinstance(candidate, dict) else None,
                    "model_candidate_validation": validation,
                    "validation": fallback_validation,
                    "attempt_count": self.max_correction_retries + 1,
                    "attempt_diagnostics": attempt_diagnostics,
                    "normalization_events": normalization_events,
                    "fallback_used": True,
                    "usage": usage,
                    "model_metadata": self.metadata(),
                }
        safe_invalid = _fallback_invalid(selected_object_ids, "Structured plan failed validation after bounded correction.")
        safe_validation = validate_plan(
            safe_invalid,
            scene=scene,
            selected_object_ids=selected_object_ids,
            current_states=current_states,
            intent_context=intent_context,
        )
        return {
            "plan": safe_invalid,
            "model_candidate": candidate if isinstance(candidate, dict) else None,
            "model_candidate_validation": validation,
            "validation": safe_validation,
            "attempt_count": self.max_correction_retries + 1,
            "attempt_diagnostics": attempt_diagnostics,
            "normalization_events": normalization_events,
            "fallback_used": True,
            "usage": usage,
            "model_metadata": self.metadata(),
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "base_url": self.base_url,
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "max_correction_retries": self.max_correction_retries,
            "thinking": "disabled",
        }


def sanitize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url is required")
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("base_url must be HTTPS without embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    return value.strip().rstrip("/")


class DeepSeekChatCompletionsClient:
    """Small stdlib-only DeepSeek Chat Completions client with redacted failures."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS):
        if not api_key:
            raise ValueError("API key is required")
        self._api_key = api_key
        self.base_url = sanitize_base_url(base_url)
        self.timeout_seconds = int(timeout_seconds)

    def _request_json(self, method: str, suffix: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + suffix,
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self._api_key,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise DeepSeekClientError("DeepSeek API returned HTTP {}".format(error.code)) from None
        except (URLError, TimeoutError, OSError, json.JSONDecodeError):
            raise DeepSeekClientError("DeepSeek API request failed") from None
        if not isinstance(payload, dict):
            raise DeepSeekClientError("DeepSeek API returned a non-object response")
        return payload

    def discover_chat_model(self, configured_model: str | None = None) -> tuple[str, list[str]]:
        payload = self._request_json("GET", "/models")
        records = payload.get("data")
        if not isinstance(records, list):
            raise DeepSeekClientError("DeepSeek model catalog has an invalid shape")
        available = [item.get("id") for item in records if isinstance(item, dict) and isinstance(item.get("id"), str)]
        chat_models = [model for model in OFFICIAL_CHAT_COMPLETION_MODELS if model in available]
        if configured_model:
            if configured_model not in available or configured_model not in OFFICIAL_CHAT_COMPLETION_MODELS:
                raise DeepSeekClientError("configured model is not an available documented chat-completion model")
            return configured_model, available
        if not chat_models:
            raise DeepSeekClientError("model catalog has no model listed by the official chat-completions contract")
        return chat_models[0], available

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model_id: str,
        temperature: float,
        max_output_tokens: int,
    ) -> dict[str, Any]:
        body = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "stream": False,
        }
        payload = self._request_json("POST", "/chat/completions", body)
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise DeepSeekClientError("DeepSeek completion response has no choice")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise DeepSeekClientError("DeepSeek completion response has no JSON content")
        return {
            "content": message["content"],
            "model": payload.get("model", model_id),
            "request_id": payload.get("id"),
            "usage": payload.get("usage", {}),
            "created": payload.get("created"),
        }


def build_live_planner() -> tuple[SemanticPlanner, list[str], bool]:
    """Resolve the configured official model without exposing the key."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekClientError("DEEPSEEK_API_KEY is unavailable")
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    client = DeepSeekChatCompletionsClient(api_key, base_url)
    configured = os.environ.get("DEEPSEEK_MODEL")
    model_id, available = client.discover_chat_model(configured)
    planner = SemanticPlanner(
        client,
        model_id,
        base_url=base_url,
        temperature=DEFAULT_TEMPERATURE,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        max_correction_retries=1,
    )
    return planner, available, bool(configured)


# Shared M28/M29 semantic layer. The M27 planner remains the plan-composition
# engine; these public adapters give it a reusable scene, grounding,
# validation, and language-rendering boundary.
_KNOWN_OBJECT_LABELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "assist_phone": ("手机", ("phone", "smartphone", "mobile phone", "cell phone", "手机", "智能手机")),
    "assist_wireless_charger": ("无线充电座", ("wireless charger", "wireless charging pad", "charger", "charging pad", "无线充电座", "无线充电器", "充电板")),
    "assist_medicine_box": ("小药盒", ("medicine box", "small medicine box", "pill box", "药盒", "小药盒")),
    "assist_storage_box": ("收纳盒", ("storage box", "storage container", "box", "收纳盒", "储物盒", "收纳箱", "盒子")),
    "assist_user_zone": ("用户区域", ("user zone", "user area", "user-side zone", "用户区域", "用户区", "用户放置区")),
    "assist_button_switch": ("按钮开关", ("button", "switch", "button switch", "按钮", "开关", "按钮开关")),
    "future_book_01": ("书", ("book", "novel", "书", "书本")),
    "future_book_shelf": ("书架", ("bookshelf", "book shelf", "书架")),
    "future_pen_01": ("笔", ("pen", "marker", "笔")),
    "future_pen_drawer": ("抽屉", ("drawer", "desk drawer", "抽屉")),
}


def normalize_noun(value: str) -> str:
    """Normalize bilingual aliases without translating or guessing."""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    normalized = normalized.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"[\s\u3000]+", " ", normalized)
    return normalized.strip(" \t\r\n.,，。:：;；!?！？")


class SemanticSceneCore:
    """Validated M27 semantic scene plus task/history facts for M28 and M29."""

    def __init__(
        self,
        scene: dict[str, Any],
        *,
        selection_history: list[str] | None = None,
        current_task_state: dict[str, Any] | None = None,
    ) -> None:
        if not isinstance(scene, dict) or scene.get("schema_version") != SCENE_SCHEMA_VERSION:
            raise PlannerInputError("semantic scene schema_version must be " + SCENE_SCHEMA_VERSION)
        objects = scene.get("objects")
        if not isinstance(objects, list) or not objects:
            raise PlannerInputError("semantic scene objects must be a non-empty array")
        seen: set[str] = set()
        normalized_scene = deepcopy(scene)
        normalized_objects: list[dict[str, Any]] = []
        for index, item in enumerate(objects):
            field = "objects[{}]".format(index)
            if not isinstance(item, dict):
                raise PlannerInputError(field + " must be an object")
            object_id = item.get("id")
            if not isinstance(object_id, str) or not object_id.strip() or object_id in seen:
                raise PlannerInputError(field + ".id must be a unique non-empty string")
            seen.add(object_id)
            if not isinstance(item.get("object_type"), str) or not item["object_type"].strip():
                raise PlannerInputError(field + ".object_type is required")
            tags = _tags(item.get("affordance_tags", []), field + ".affordance_tags")
            supplied_aliases = item.get("aliases", [])
            if not isinstance(supplied_aliases, list) or not all(isinstance(x, str) for x in supplied_aliases):
                raise PlannerInputError(field + ".aliases must be a string array")
            supplied_zh = item.get("name_zh", item.get("display_name_zh"))
            known_zh, known_aliases = _KNOWN_OBJECT_LABELS.get(object_id, ("", ()))
            if not known_zh:
                known_zh, known_aliases = _KNOWN_OBJECT_LABELS.get(item["object_type"], ("", ()))
            entry = deepcopy(item)
            entry["affordance_tags"] = tags
            entry["name_zh"] = supplied_zh if isinstance(supplied_zh, str) and supplied_zh else known_zh
            entry["aliases"] = list(dict.fromkeys(
                [item.get("name", ""), item["object_type"].replace("_", " "), *known_aliases, *supplied_aliases]
            ))
            entry["aliases"] = [alias for alias in entry["aliases"] if isinstance(alias, str) and alias.strip()]
            normalized_objects.append(entry)
        normalized_scene["objects"] = normalized_objects
        order = normalized_scene.get("candidate_order", [])
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
            raise PlannerInputError("candidate_order must be a string array")
        if any(object_id not in seen for object_id in order):
            raise PlannerInputError("candidate_order references an unknown object")
        history = selection_history or []
        if not isinstance(history, list) or not all(isinstance(x, str) for x in history):
            raise PlannerInputError("selection_history must be a string array")
        if any(object_id not in seen for object_id in history):
            raise PlannerInputError("selection_history references an unknown object")
        if current_task_state is not None and not isinstance(current_task_state, dict):
            raise PlannerInputError("current_task_state must be an object")
        self.scene = normalized_scene
        self.objects = {item["id"]: item for item in normalized_objects}
        self.selection_history = list(history)
        self.current_task_state = deepcopy(current_task_state or {})

    @classmethod
    def from_snapshot(
        cls,
        snapshot: dict[str, Any],
        *,
        current_states: dict[str, dict[str, Any]] | None = None,
        selection_history: list[str] | None = None,
        current_task_state: dict[str, Any] | None = None,
    ) -> "SemanticSceneCore":
        return cls(
            semantic_scene_from_snapshot(snapshot, current_states),
            selection_history=selection_history,
            current_task_state=current_task_state,
        )

    def object(self, object_id: str) -> dict[str, Any]:
        if object_id not in self.objects:
            raise PlannerInputError("unknown semantic object id: " + object_id)
        return deepcopy(self.objects[object_id])

    def model_view(self) -> dict[str, Any]:
        return {
            "scene": deepcopy(self.scene),
            "selection_history": list(self.selection_history),
            "current_task_state": deepcopy(self.current_task_state),
        }


class ObjectGrounder:
    """Ground a noun only when the scene catalog gives an unambiguous match."""

    def __init__(self, *, minimum_fuzzy_score: float = 0.84, ambiguity_margin: float = 0.08) -> None:
        self.minimum_fuzzy_score = float(minimum_fuzzy_score)
        self.ambiguity_margin = float(ambiguity_margin)

    def ground(self, noun: str, scene: SemanticSceneCore) -> dict[str, Any]:
        if not isinstance(noun, str) or not noun.strip():
            return {"status": "invalid", "query": noun, "object_id": None, "candidates": []}
        query = normalize_noun(noun)
        exact: list[dict[str, Any]] = []
        scored: list[dict[str, Any]] = []
        for item in scene.scene["objects"]:
            if not item.get("selectable", False):
                continue
            labels = [item["id"], item.get("name", ""), item.get("name_zh", ""), *item.get("aliases", [])]
            normalized = {normalize_noun(label) for label in labels if isinstance(label, str) and label.strip()}
            if query in normalized:
                exact.append({"object_id": item["id"], "score": 1.0})
                continue
            best = max((SequenceMatcher(None, query, label).ratio() for label in normalized), default=0.0)
            if best >= self.minimum_fuzzy_score:
                scored.append({"object_id": item["id"], "score": round(best, 6)})
        if len(exact) == 1:
            return {"status": "grounded", "query": noun, "object_id": exact[0]["object_id"], "candidates": exact}
        if len(exact) > 1:
            return {"status": "ambiguous", "query": noun, "object_id": None, "candidates": sorted(exact, key=lambda x: x["object_id"])}
        scored.sort(key=lambda x: (-x["score"], x["object_id"]))
        if not scored:
            return {"status": "unknown", "query": noun, "object_id": None, "candidates": []}
        if len(scored) > 1 and scored[0]["score"] - scored[1]["score"] < self.ambiguity_margin:
            return {"status": "ambiguous", "query": noun, "object_id": None, "candidates": scored[:5]}
        return {"status": "grounded", "query": noun, "object_id": scored[0]["object_id"], "candidates": scored[:5]}


def _strict_sequence_errors(
    actions: Any,
    *,
    objects: dict[str, dict[str, Any]],
    selected: set[str],
    where: str,
) -> list[str]:
    """Check that an executable sequence has coherent hold/place transitions."""
    if not isinstance(actions, list):
        return []
    errors: list[str] = []
    held: set[str] = set()
    placed: set[str] = set()
    signatures: set[tuple[str, str, str]] = set()
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            continue
        action_type = action.get("type")
        object_id = action.get("object_id")
        target_id = action.get("target_id", "")
        if action_type not in ALLOWED_ACTIONS or object_id not in objects:
            continue
        label = "{}[{}]".format(where, index)
        signature = (str(action_type), str(object_id), str(target_id))
        if signature in signatures:
            errors.append(label + " duplicates an earlier action")
        signatures.add(signature)
        if action_type in {"PICK", "PLACE_ON", "PLACE_IN", "MOVE_TO", "RELEASE"} and object_id not in selected:
            errors.append(label + " source is not selected")
        if action_type == "PICK":
            if object_id in held:
                errors.append(label + " picks an already held object")
            elif object_id in placed:
                errors.append(label + " picks an object already placed in this plan")
            else:
                held.add(object_id)
        elif action_type in {"PLACE_ON", "PLACE_IN", "MOVE_TO", "RELEASE"}:
            if object_id == target_id:
                errors.append(label + " source and target must differ")
            if object_id not in held:
                errors.append(label + " requires a preceding PICK in this action sequence")
            if object_id in placed:
                errors.append(label + " moves an object already placed in this plan")
            held.discard(object_id)
            placed.add(object_id)
    return errors


class PlanValidator:
    """Strict M29 validator over the M27 schema, scene affordances, and state transitions."""

    def validate(
        self,
        plan: Any,
        *,
        scene: SemanticSceneCore,
        selected_object_ids: list[str],
        current_states: dict[str, dict[str, Any]] | None = None,
        intent_context: str | None = None,
    ) -> dict[str, Any]:
        base = validate_plan(
            plan,
            scene=scene.scene,
            selected_object_ids=selected_object_ids,
            current_states=current_states,
            intent_context=intent_context,
        )
        errors = list(base["errors"])
        if isinstance(plan, dict) and plan.get("status") == "executable":
            errors.extend(_strict_sequence_errors(
                plan.get("actions"), objects=scene.objects, selected=set(selected_object_ids), where="plan"
            ))
        if isinstance(plan, dict) and plan.get("status") == "ambiguous":
            for index, alternative in enumerate(plan.get("alternatives", [])):
                if isinstance(alternative, dict):
                    errors.extend(_strict_sequence_errors(
                        alternative.get("actions"), objects=scene.objects,
                        selected=set(selected_object_ids), where="alternatives[{}]".format(index)
                    ))
        return {"valid": not errors, "checks": base["checks"], "errors": errors}


class VLAInstructionRenderer:
    """Render only validated actions into short English and Chinese imperatives."""

    def _action_phrases(self, actions: list[dict[str, Any]], scene: SemanticSceneCore) -> tuple[list[str], list[str]]:
        zh: list[str] = []
        en: list[str] = []
        for action in actions:
            kind = action["type"]
            source = scene.objects[action["object_id"]]
            source_zh = source.get("name_zh") or source.get("name") or source["id"]
            source_en = source.get("name") or source.get("object_type", "object").replace("_", " ")
            target = scene.objects.get(action.get("target_id"), {})
            target_zh = target.get("name_zh") or target.get("name") or target.get("id", "目标")
            target_en = target.get("name") or target.get("object_type", "target").replace("_", " ")
            if kind == "OPEN":
                zh.append("打开{}".format(source_zh)); en.append("Open the {}".format(source_en))
            elif kind == "CLOSE":
                zh.append("关上{}".format(source_zh)); en.append("Close the {}".format(source_en))
            elif kind == "PICK":
                zh.append("拿起{}".format(source_zh)); en.append("Pick up the {}".format(source_en))
            elif kind == "PRESS":
                zh.append("按下{}".format(source_zh)); en.append("Press the {}".format(source_en))
            elif kind == "PLACE_IN":
                zh.append("把{}放进{}里".format(source_zh, target_zh))
                en.append("Place the {} inside the {}".format(source_en, target_en))
            elif kind == "PLACE_ON":
                zh.append("把{}放到{}上".format(source_zh, target_zh))
                en.append("Place the {} on the {}".format(source_en, target_en))
            elif kind in {"MOVE_TO", "RELEASE"}:
                zh.append("把{}放到{}".format(source_zh, target_zh))
                en.append("Move the {} to the {}".format(source_en, target_en))
        return zh, en

    @staticmethod
    def _join(phrases: list[str], language: str) -> str:
        if not phrases:
            return ""
        if language == "zh":
            return "，然后".join(phrases) + "。"
        if len(phrases) == 1:
            return phrases[0] + "."
        continuation = phrases[-1][:1].lower() + phrases[-1][1:]
        return ", then ".join(phrases[:-1]) + ", then " + continuation + "."

    def render(self, plan: dict[str, Any], scene: SemanticSceneCore) -> dict[str, Any]:
        zh, en = self._action_phrases(plan.get("actions", []), scene)
        alternatives = []
        for item in plan.get("alternatives", []):
            alt_zh, alt_en = self._action_phrases(item.get("actions", []), scene)
            alternatives.append({
                "intent_summary": item.get("intent_summary", ""),
                "actions": deepcopy(item.get("actions", [])),
                "vla_instruction_zh": self._join(alt_zh, "zh"),
                "vla_instruction_en": self._join(alt_en, "en"),
            })
        return {
            "vla_instruction_zh": self._join(zh, "zh"),
            "vla_instruction_en": self._join(en, "en"),
            "alternatives": alternatives,
        }


def build_live_client() -> tuple[DeepSeekChatCompletionsClient, str, list[str], bool]:
    """Create the shared live client from environment-only secret configuration."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise DeepSeekClientError("DEEPSEEK_API_KEY is unavailable")
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    client = DeepSeekChatCompletionsClient(api_key, base_url)
    configured = os.environ.get("DEEPSEEK_MODEL")
    model_id, available = client.discover_chat_model(configured)
    return client, model_id, available, bool(configured)


class SemanticLanguageBridge:
    """Standalone grounded planner facade; output is descriptive, never dispatched."""

    def __init__(
        self,
        planner: SemanticPlanner,
        *,
        grounder: ObjectGrounder | None = None,
        validator: PlanValidator | None = None,
        renderer: VLAInstructionRenderer | None = None,
    ) -> None:
        self.planner = planner
        self.grounder = grounder or ObjectGrounder()
        self.validator = validator or PlanValidator()
        self.renderer = renderer or VLAInstructionRenderer()
        self.free_noun_inferencer = FreeNounAffordanceInferencer()

    def plan_nouns(
        self,
        snapshot: dict[str, Any],
        nouns: list[str],
        *,
        intent_context: str | None = None,
        current_states: dict[str, dict[str, Any]] | None = None,
        mode: str = "strict",
    ) -> dict[str, Any]:
        if mode not in {"strict", "free"}:
            raise PlannerInputError("mode must be strict or free")
        if not isinstance(nouns, list) or not nouns or not all(isinstance(x, str) and x.strip() for x in nouns):
            raise PlannerInputError("at least one non-empty object noun is required")
        if mode == "free" and len(nouns) != 2:
            raise PlannerInputError("free-noun inference accepts exactly two nouns")
        scene = SemanticSceneCore.from_snapshot(snapshot, current_states=current_states)
        grounded = [self.grounder.ground(noun, scene) for noun in nouns]
        if any(item["status"] == "ambiguous" for item in grounded):
            return self._non_executable("ambiguous", "grounding_ambiguous", grounded, "A noun matches multiple scene objects.")
        if any(item["status"] != "grounded" for item in grounded) and mode == "strict":
            return self._non_executable("invalid", "unknown_object_in_strict_scene", grounded, "One or more nouns are not grounded in the active scene.")
        inferred_metadata: dict[str, Any] | None = None
        output_mode = "strict_scene"
        if any(item["status"] != "grounded" for item in grounded):
            inferred = self.free_noun_inferencer.infer(nouns, self.planner)
            if not inferred["valid"]:
                failed = self._non_executable("invalid", "free_noun_affordance_inference_failed", grounded, "The model could not produce validated provisional object facts.")
                failed["mode"] = "free_noun_unverified"
                failed["attempt_count"] = inferred["attempt_count"]
                failed["inference_diagnostics"] = inferred["errors"]
                failed["model_metadata"] = self.planner.metadata()
                failed["token_usage"] = inferred["usage"]
                return failed
            snapshot = self.free_noun_inferencer.to_snapshot(nouns, inferred["objects"])
            scene = SemanticSceneCore.from_snapshot(snapshot)
            selected = [item["id"] for item in inferred["objects"]]
            grounded = [
                {"status": "grounded", "query": noun, "object_id": object_id, "candidates": [{"object_id": object_id, "score": 1.0}]}
                for noun, object_id in zip(nouns, selected)
            ]
            inferred_metadata = {
                "trust": "model_inferred_unverified",
                "inference_attempt_count": inferred["attempt_count"],
                "inference_prompt_version": FREE_NOUN_PROMPT_VERSION,
                "token_usage": inferred["usage"],
                "inferred_affordances": [
                    {"object_id": item["id"], "object_type": item["object_type"], "affordance_tags": item["affordance_tags"]}
                    for item in inferred["objects"]
                ],
            }
            output_mode = "free_noun_unverified"
            current_states = None
        else:
            # Different labels can refer to the same scene object, such as
            # "button" and "switch". Keep all grounding evidence for display,
            # but send each stable scene ID to the planner only once.
            selected = list(dict.fromkeys(item["object_id"] for item in grounded))
            if mode == "free":
                output_mode = "free_noun_scene_grounded"
        result = self.planner.plan(snapshot, selected, current_states=current_states, intent_context=intent_context)
        checked = self.validator.validate(
            result["plan"], scene=scene, selected_object_ids=selected,
            current_states=current_states, intent_context=intent_context,
        )
        plan = deepcopy(result["plan"])
        if not checked["valid"] and plan.get("status") != "invalid":
            plan = _fallback_invalid(selected, "Strict plan validation failed: " + "; ".join(checked["errors"]))
        rendered = self.renderer.render(plan, scene)
        output = {
            "status": plan["status"],
            "mode": output_mode,
            "grounded_objects": [
                {"input": noun, "object_id": item["object_id"], "trust": "scene_grounded" if output_mode != "free_noun_unverified" else "model_inferred_unverified"}
                for noun, item in zip(nouns, grounded)
            ],
            "intent_summary": plan.get("intent_summary", ""),
            "actions": deepcopy(plan.get("actions", [])),
            "alternatives": rendered["alternatives"],
            "vla_instruction_zh": rendered["vla_instruction_zh"],
            "vla_instruction_en": rendered["vla_instruction_en"],
            "assumptions": list(plan.get("assumptions", [])),
            "reason_code": "plan_validated" if plan["status"] == "executable" else (
                "ambiguous_plan" if plan["status"] == "ambiguous" else "invalid_plan"
            ),
            "validation": checked,
            "attempt_count": result.get("attempt_count", 0),
            "fallback_used": result.get("fallback_used", False),
            "token_usage": {
                key: int(result.get("usage", {}).get(key, 0)) + int((inferred_metadata or {}).get("token_usage", {}).get(key, 0))
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            },
            "model_metadata": result.get("model_metadata", self.planner.metadata()),
            "dispatch_allowed": False,
        }
        if inferred_metadata is not None:
            output["free_noun_inference"] = inferred_metadata
        return output

    @staticmethod
    def _non_executable(status: str, reason: str, grounded: list[dict[str, Any]], message: str) -> dict[str, Any]:
        return {
            "status": status, "mode": "strict_scene", "grounded_objects": grounded,
            "intent_summary": message, "actions": [], "alternatives": [],
            "vla_instruction_zh": "", "vla_instruction_en": "", "assumptions": [],
            "reason_code": reason, "validation": {"valid": False, "errors": [message]},
            "attempt_count": 0, "fallback_used": True, "token_usage": {},
            "model_metadata": {}, "dispatch_allowed": False,
        }


FREE_NOUN_PROMPT_VERSION = "m29-free-noun-affordance-prompt-v1"
FREE_NOUN_AFFORDANCES = frozenset({
    "movable", "container", "openable", "closable", "fixed_surface",
    "placement_destination", "user_zone", "charging_target", "pressable",
})


class FreeNounAffordanceInferencer:
    """Infer provisional catalog facts for an explicitly unverified demo mode."""

    @staticmethod
    def _prompt(nouns: list[str], correction: dict[str, Any] | None = None) -> list[dict[str, str]]:
        system = (
            "Infer only broad object category and physical affordance tags for the supplied noun labels. "
            "This is an unverified language demo, not physical observation. Return exactly one JSON object with an objects array. "
            "Use exactly object IDs free_object_0 and free_object_1, preserving the corresponding input order. "
            "Each object entry has exactly object_id, object_type, affordance_tags. object_type is lowercase snake_case. "
            "Allowed affordances only: movable, container, openable, closable, fixed_surface, placement_destination, "
            "user_zone, charging_target, pressable. Do not infer current state, dimensions, location, or an action plan. "
            "If facts are uncertain, use an empty affordance list."
        )
        request: dict[str, Any] = {
            "prompt_version": FREE_NOUN_PROMPT_VERSION,
            "nouns_in_order": nouns,
            "required_object_ids_in_order": ["free_object_0", "free_object_1"],
        }
        if correction is not None:
            request["correction"] = correction
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(request, ensure_ascii=False, separators=(",", ":"))},
        ]

    @staticmethod
    def _validate(candidate: Any) -> tuple[list[dict[str, Any]], list[str]]:
        errors: list[str] = []
        if not isinstance(candidate, dict) or set(candidate) != {"objects"}:
            return [], ["response must contain only an objects array"]
        objects = candidate.get("objects")
        required_ids = ["free_object_0", "free_object_1"]
        if not isinstance(objects, list) or len(objects) != 2:
            return [], ["objects must contain exactly two entries"]
        by_id: dict[str, dict[str, Any]] = {}
        for index, item in enumerate(objects):
            if not isinstance(item, dict) or set(item) != {"object_id", "object_type", "affordance_tags"}:
                errors.append("objects[{}] must have the exact inference schema".format(index))
                continue
            object_id = item.get("object_id")
            object_type = item.get("object_type")
            tags = item.get("affordance_tags")
            if object_id not in required_ids or object_id in by_id:
                errors.append("object ID is unknown or duplicated")
                continue
            if not isinstance(object_type, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", object_type):
                errors.append("object_type must be lowercase snake_case")
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                errors.append("affordance_tags must be a string array")
            elif len(tags) != len(set(tags)) or not set(tags).issubset(FREE_NOUN_AFFORDANCES):
                errors.append("affordance_tags contains duplicates or unsupported values")
            by_id[object_id] = item
        if set(by_id) != set(required_ids):
            errors.append("response must include both exact object IDs")
        if errors:
            return [], errors
        return [
            {"id": object_id, "object_type": by_id[object_id]["object_type"], "affordance_tags": list(by_id[object_id]["affordance_tags"])}
            for object_id in required_ids
        ], []

    def infer(self, nouns: list[str], planner: SemanticPlanner) -> dict[str, Any]:
        correction = None
        errors: list[str] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for attempt in range(2):
            try:
                response = planner.client.complete(
                    self._prompt(nouns, correction), model_id=planner.model_id,
                    temperature=0.0, max_output_tokens=400,
                )
                for key in usage:
                    value = (response.get("usage") or {}).get(key, 0)
                    if isinstance(value, int) and value >= 0:
                        usage[key] += value
                content = response.get("content")
            except Exception as error:
                return {"valid": False, "objects": [], "attempt_count": attempt + 1, "errors": ["affordance inference failed: " + type(error).__name__], "usage": usage}
            try:
                candidate = json.loads(content) if isinstance(content, str) else content
            except (TypeError, json.JSONDecodeError):
                candidate = None
            objects, errors = self._validate(candidate)
            if not errors:
                return {"valid": True, "objects": objects, "attempt_count": attempt + 1, "errors": [], "usage": usage}
            correction = {"instruction": "Correct the response to the exact IDs, keys, and affordance enum.", "validation_errors": errors}
        return {"valid": False, "objects": [], "attempt_count": 2, "errors": errors, "usage": usage}

    @staticmethod
    def to_snapshot(nouns: list[str], inferred_objects: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "sceneId": "m29-free-noun-unverified",
            "templateId": "m29_free_noun_inference",
            "candidateOrderFarToNearLeftToRight": [item["id"] for item in inferred_objects],
            "objects": [
                {
                    "semanticId": item["id"],
                    "objectType": item["object_type"],
                    "role": "model_inferred_unverified",
                    "sourceKind": "m29_free_noun_inference",
                    "selectable": True,
                    "fixedPose": False,
                    "affordanceTags": item["affordance_tags"],
                    "positionMeters": None,
                    "dimensionsMeters": None,
                }
                for item in inferred_objects
            ],
        }
