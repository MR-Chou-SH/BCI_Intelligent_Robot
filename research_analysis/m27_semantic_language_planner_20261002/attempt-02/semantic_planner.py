"""Constrained, analysis-only semantic planner for M27.

This module accepts the M20 SceneLayoutSnapshot payload and BCI-selected IDs,
then validates a model-produced task plan. It does not dispatch robot actions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


PROMPT_VERSION = "m27-semantic-planner-prompt-v2"
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
            actions = [relation]
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
