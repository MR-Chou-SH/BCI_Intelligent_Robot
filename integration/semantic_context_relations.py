"""Relational-affordance vocabulary and deterministic compatibility checks.

These checks validate object IDs, declared affordance tags and explicit state.
They do not infer or fill in missing object facts.
"""

from __future__ import annotations

from typing import Any


RELATION_SCHEMA_VERSION = "m30-relational-affordance-v1"
RELATION_TYPES = frozenset({
    "CHARGE_WITH", "STORE_IN", "STORE_ON", "PLACE_IN", "PLACE_ON",
    "HAND_OVER", "CUT_WITH", "JUICE_WITH", "RINSE_WITH", "WASH_WITH",
    "FILL_FROM", "POUR_INTO", "FASTEN_WITH", "PRESS", "OPEN", "CLOSE",
    "DISCARD_IN", "USE_WITH", "RELATED_TO", "OTHER", "NONE",
})

_TARGET_TAGS: dict[str, frozenset[str]] = {
    "CHARGE_WITH": frozenset({"charging_target"}),
    "STORE_IN": frozenset({"container"}),
    "PLACE_IN": frozenset({"container"}),
    "DISCARD_IN": frozenset({"container"}),
    "POUR_INTO": frozenset({"container", "pour_target"}),
    "STORE_ON": frozenset({"support_surface", "storage_surface"}),
    "PLACE_ON": frozenset({"support_surface"}),
    "HAND_OVER": frozenset({"user_zone", "handover_target"}),
    "CUT_WITH": frozenset({"cutting_tool", "knife", "tool"}),
    "JUICE_WITH": frozenset({"juicing_appliance", "juicer"}),
    "RINSE_WITH": frozenset({"water_source", "sink", "tap"}),
    "WASH_WITH": frozenset({"washing_equipment", "washing_machine", "detergent"}),
    "FILL_FROM": frozenset({"water_source", "sink", "tap"}),
    "FASTEN_WITH": frozenset({"fastening_tool", "screwdriver", "tool"}),
    "PRESS": frozenset({"pressable", "button", "switch"}),
}

_SOURCE_TAGS: dict[str, frozenset[str]] = {
    "CHARGE_WITH": frozenset({"chargeable"}),
    "CUT_WITH": frozenset({"cuttable", "food"}),
    "JUICE_WITH": frozenset({"juicable", "food"}),
    "RINSE_WITH": frozenset({"rinsable", "washable", "dish"}),
    "WASH_WITH": frozenset({"washable", "laundry"}),
    "FASTEN_WITH": frozenset({"fastenable", "fastener"}),
}

_CAPACITY_RELATIONS = frozenset({"CHARGE_WITH", "STORE_IN", "PLACE_IN", "DISCARD_IN", "POUR_INTO"})


def affordance_tags(obj: dict[str, Any] | None) -> set[str]:
    if not isinstance(obj, dict):
        return set()
    values = obj.get("affordance_tags", [])
    if not isinstance(values, list):
        return set()
    return {value.strip().casefold() for value in values if isinstance(value, str) and value.strip()}


def explicit_state(obj: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {}
    value = obj.get("current_state", {})
    return value if isinstance(value, dict) else {}


def _is_unavailable(state: dict[str, Any]) -> bool:
    if state.get("full") is True or state.get("available") is False or state.get("free") is False:
        return True
    return any(
        isinstance(state.get(field), str)
        and state[field].strip().casefold() in {"full", "occupied", "unavailable", "blocked", "not_available"}
        for field in ("availability", "occupancy", "status")
    )


def relation_compatibility(
    relation_type: str,
    source: dict[str, Any] | None,
    candidate: dict[str, Any],
) -> tuple[bool, str | None]:
    """Return deterministic compatibility from supplied tags and known state.

    A missing state stays unknown. In particular, an unspecified container lid
    does not invalidate a high-level STORE_IN relation.
    """
    relation = relation_type.upper() if isinstance(relation_type, str) else ""
    if relation not in RELATION_TYPES:
        return False, "relation_type_unknown"
    if relation in {"NONE", "RELATED_TO", "OTHER", "USE_WITH"}:
        return True, None

    target_tags = affordance_tags(candidate)
    source_tags = affordance_tags(source)
    required_target = _TARGET_TAGS.get(relation)
    if required_target and not target_tags.intersection(required_target):
        return False, "target_affordance_missing"
    required_source = _SOURCE_TAGS.get(relation)
    if required_source and source is not None and not source_tags.intersection(required_source):
        return False, "source_affordance_missing"

    state = explicit_state(candidate)
    if relation in _CAPACITY_RELATIONS and _is_unavailable(state):
        return False, "destination_unavailable_from_explicit_state"
    if relation in {"STORE_IN", "PLACE_IN", "DISCARD_IN", "POUR_INTO"}:
        lid = state.get("lid", state.get("is_open"))
        closed = lid is False or (isinstance(lid, str) and lid.strip().casefold() in {"closed", "shut"})
        if closed and "openable" not in target_tags:
            return False, "closed_non_openable_container"
    if relation == "OPEN" and "openable" not in target_tags:
        return False, "target_not_openable"
    if relation == "CLOSE":
        if "openable" not in target_tags:
            return False, "target_not_openable"
        lid = state.get("lid", state.get("is_open"))
        closed = lid is False or (isinstance(lid, str) and lid.strip().casefold() in {"closed", "shut"})
        if closed:
            return False, "target_already_closed"
    return True, None
