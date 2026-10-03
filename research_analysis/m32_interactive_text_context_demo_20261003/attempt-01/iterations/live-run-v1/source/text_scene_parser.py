"""M32 natural-language text to structured semantic-scene adapter.

Scene mentions must be grounded in literal source spans. Colors and states are
retained as observed facts only when the model supplies source evidence whose
literal wording supports the normalized fact value. Affordance tags are kept
separate and marked as world-knowledge inference for the M30 Context engine.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from typing import Any

from integration.semantic_intelligence import SCENE_SCHEMA_VERSION


M32_SCENE_PARSER_VERSION = "m32-text-scene-parser-v1"
M32_SCENE_PARSER_SCHEMA_VERSION = "m32-text-scene-parse-v1"
MAX_SCENE_TEXT_CHARS = 6000
MAX_SCENE_OBJECTS = 16

ALLOWED_AFFORDANCE_TAGS = frozenset({
    "food", "cuttable", "juicable", "dish", "rinsable", "washable", "laundry",
    "container", "openable", "support_surface", "storage_surface", "charging_target",
    "chargeable", "cutting_tool", "knife", "tool", "juicing_appliance", "juicer",
    "water_source", "sink", "tap", "washing_equipment", "washing_machine", "detergent",
    "drying_rack", "drying_surface", "fastening_tool", "fastenable", "fastener",
    "screwdriver", "user_zone", "handover_target", "pressable", "button", "switch",
    "shelf", "book", "clothing", "furniture", "electronic_device", "cable", "appliance",
})

_COLOR_TERMS = {
    "red": ("red", "红色"),
    "blue": ("blue", "蓝色"),
    "green": ("green", "绿色"),
    "yellow": ("yellow", "黄色"),
    "orange": ("orange", "橙色"),
    "purple": ("purple", "紫色"),
    "pink": ("pink", "粉色"),
    "black": ("black", "黑色"),
    "white": ("white", "白色"),
    "brown": ("brown", "棕色", "褐色"),
    "gray": ("gray", "grey", "灰色"),
    "transparent": ("transparent", "透明"),
    "silver": ("silver", "银色"),
    "gold": ("gold", "金色"),
}

_STATE_TERMS = {
    "open": ("open", "打开", "开着", "开启"),
    "closed": ("closed", "shut", "关闭", "关着", "闭合"),
    "full": ("full", "装满", "满的", "已满"),
    "empty": ("empty", "空的", "空着"),
    "occupied": ("occupied", "有人占用", "已占用", "正在使用"),
    "unavailable": ("unavailable", "不可用", "被挡住", "blocked"),
    "on": ("on", "打开状态", "通电", "开启状态"),
    "off": ("off", "关闭状态", "断电", "关机"),
    "clean": ("clean", "干净", "已清洁"),
    "dirty": ("dirty", "脏的", "肮脏", "未清洁"),
    "wet": ("wet", "湿的", "潮湿"),
    "dry": ("dry", "干燥", "干的"),
    "charged": ("charged", "已充电", "充满电"),
}


class TextSceneParseError(ValueError):
    """A sanitized parse/schema error without request credentials or headers."""


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _contains_literal(source: str, span: str) -> bool:
    if not isinstance(span, str) or not span.strip():
        return False
    return _norm(span.strip()) in _norm(source)


def _same_source_clause(source: str, mention: str, evidence: str, fact_terms: tuple[str, ...]) -> bool:
    """Require object mention and fact wording in one quoted source clause."""
    if not isinstance(evidence, str) or not evidence.strip() or not _contains_literal(evidence, mention):
        return False
    if not _contains_literal(source, evidence):
        return False
    separators = r"[、,;；。!?！？\r\n]+|(?:和|以及|并且|与|及)"
    evidence_clauses = re.split(separators, evidence)
    return any(
        _contains_literal(clause, mention)
        and any(_norm(term) in _norm(clause) for term in fact_terms)
        for clause in evidence_clauses
    )


def _canonical_from_evidence(kind: str, value: Any, evidence: Any) -> str | None:
    if not isinstance(value, str) or not isinstance(evidence, str) or not evidence.strip():
        return None
    table = _COLOR_TERMS if kind == "color" else _STATE_TERMS
    value_norm = _norm(value.strip())
    evidence_norm = _norm(evidence)
    for canonical, terms in table.items():
        if value_norm in {_norm(term) for term in (canonical, *terms)}:
            if any(_norm(term) in evidence_norm for term in terms):
                return canonical
            return None
    return None


def _state_projection(state: str) -> tuple[str, Any]:
    if state in {"open", "closed"}:
        return "lid", state
    if state in {"full", "empty"}:
        return state, True
    if state in {"occupied", "unavailable"}:
        return "availability", state
    if state in {"on", "off"}:
        return "powered", state == "on"
    if state in {"clean", "dirty", "wet", "dry"}:
        return "condition", state
    return "charge_state", state


def _decode_content(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    content = response.get("content")
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _parse_errors(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["response_must_be_object"]
    errors = []
    if not isinstance(value.get("scene_type"), str) or not value["scene_type"].strip():
        errors.append("scene_type_required")
    objects = value.get("objects")
    if not isinstance(objects, list) or not 1 <= len(objects) <= MAX_SCENE_OBJECTS:
        errors.append("objects_must_be_array_with_1_to_16_entries")
        return errors
    for index, item in enumerate(objects):
        if not isinstance(item, dict):
            errors.append("object_{}_must_be_object".format(index))
            continue
        if not isinstance(item.get("mention"), str) or not item["mention"].strip():
            errors.append("object_{}_mention_required".format(index))
        if not isinstance(item.get("object_type"), str) or not item["object_type"].strip():
            errors.append("object_{}_type_required".format(index))
        aliases = item.get("aliases", [])
        tags = item.get("affordance_tags", [])
        facts = item.get("facts", [])
        if type(item.get("selectable")) is not bool:
            errors.append("object_{}_selectable_must_be_boolean".format(index))
        if not isinstance(aliases, list) or not all(isinstance(x, str) for x in aliases):
            errors.append("object_{}_aliases_invalid".format(index))
        if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags):
            errors.append("object_{}_tags_invalid".format(index))
        if not isinstance(facts, list) or not all(isinstance(x, dict) for x in facts):
            errors.append("object_{}_facts_invalid".format(index))
    return errors


def _system_prompt() -> str:
    tags = ", ".join(sorted(ALLOWED_AFFORDANCE_TAGS))
    return f"""Convert a user's natural-language description into a compact structured scene. Return exactly one JSON object, with no markdown or chain-of-thought.
Required keys: scene_type, scene_summary, objects. Each object must have mention (an exact contiguous quote from the input), display_name, object_type (lowercase snake_case), selectable (boolean), aliases (string array), affordance_tags, and facts. Preserve first-mention order. Extract only objects actually mentioned. If a proposed mention cannot be quoted exactly from the input, do not include it. Set selectable=true only for individual scene targets that a user could choose in an object-selection task. A room/scene name, or a surface/area used only as a location anchor (for example, "on the desk" or "around the counter"), is not a selectable target. If you retain such a grounded scene element for context, set selectable=false. An entity explicitly included in a list of selectable objects remains selectable even when it is furniture or a user zone. Do not infer targetability from scene type alone.
Color and state are observed facts only when explicitly stated in the text. For each explicit fact, use {{\"kind\":\"color\"|\"state\",\"value\":canonical_value,\"evidence\":exact_quote}}. The evidence quote must include the exact object mention and the explicit fact wording in one clause. Do not infer color/state from common knowledge, object type, or context. Missing facts remain absent. Broad affordance_tags are world-knowledge semantic inferences, not observed physical facts; choose only from: {tags}. Use [] when uncertain. Do not invent positions, contents, availability, task intent, or objects. Do not include evaluation labels, answer keys, future choices, or probabilities. Do not invent. Keep the output concise."""


class TextSceneParser:
    """Parse natural-language text into the shared M30 structured scene schema."""

    def __init__(self, client: Any, model_id: str, *, max_correction_retries: int = 1) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required")
        if max_correction_retries not in {0, 1}:
            raise ValueError("parser correction retries are bounded to zero or one")
        self.client = client
        self.model_id = model_id
        self.max_correction_retries = max_correction_retries

    def parse(self, scene_text: str) -> dict[str, Any]:
        if not isinstance(scene_text, str) or not scene_text.strip():
            raise TextSceneParseError("scene_text is required")
        if len(scene_text) > MAX_SCENE_TEXT_CHARS:
            raise TextSceneParseError("scene_text exceeds the 6000 character limit")

        started = time.perf_counter()
        correction = None
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        last_errors: list[str] = []
        attempts = 0
        raw: dict[str, Any] | None = None
        for attempt in range(self.max_correction_retries + 1):
            attempts += 1
            user_payload = {
                "schema_version": M32_SCENE_PARSER_SCHEMA_VERSION,
                "scene_text": scene_text,
                "correction": correction,
            }
            api_started = time.perf_counter()
            response = self.client.complete(
                [
                    {"role": "system", "content": _system_prompt()},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
                ],
                model_id=self.model_id,
                temperature=0.0,
                max_output_tokens=2800,
            )
            api_latency_ms = (time.perf_counter() - api_started) * 1000.0
            if isinstance(response, dict):
                response_usage = response.get("usage") or {}
                for key in usage:
                    value = response_usage.get(key, 0)
                    if isinstance(value, int) and value >= 0:
                        usage[key] += value
            raw = _decode_content(response)
            last_errors = _parse_errors(raw)
            if not last_errors:
                break
            correction = {
                "instruction": "Return only a corrected object using the required schema. Quote exact mentions and exact source evidence. Set selectable correctly under the system criteria. Remove unsupported objects/facts.",
                "validation_errors": last_errors,
                "prior_response": raw,
            }
        if raw is None or last_errors:
            raise TextSceneParseError("model response did not satisfy the structured scene schema")

        objects = []
        rejected_mentions: list[str] = []
        rejected_facts = 0
        rejected_tags = 0
        seen_fingerprints: set[tuple[str, str]] = set()
        for item in raw["objects"]:
            mention = item["mention"].strip()
            if not _contains_literal(scene_text, mention):
                rejected_mentions.append(mention)
                continue
            object_type = re.sub(r"[^a-z0-9]+", "_", item["object_type"].strip().casefold()).strip("_")
            if not object_type or len(object_type) > 64:
                rejected_mentions.append(mention)
                continue
            fingerprint = (_norm(mention), object_type)
            if fingerprint in seen_fingerprints:
                rejected_mentions.append(mention)
                continue
            seen_fingerprints.add(fingerprint)

            aliases = list(dict.fromkeys(
                [mention, *[alias.strip() for alias in item.get("aliases", []) if alias.strip()]]
            ))
            tags = []
            for tag in item.get("affordance_tags", []):
                normalized_tag = re.sub(r"[^a-z0-9]+", "_", tag.strip().casefold()).strip("_")
                if normalized_tag in ALLOWED_AFFORDANCE_TAGS:
                    tags.append(normalized_tag)
                else:
                    rejected_tags += 1

            observed_facts: dict[str, Any] = {}
            current_state: dict[str, Any] = {}
            color = None
            for fact in item.get("facts", []):
                kind = fact.get("kind")
                evidence = fact.get("evidence")
                canonical = _canonical_from_evidence(kind, fact.get("value"), evidence) if kind in {"color", "state"} else None
                fact_terms = (_COLOR_TERMS if kind == "color" else _STATE_TERMS).get(canonical, ()) if canonical else ()
                if canonical is None or not _same_source_clause(scene_text, mention, evidence, fact_terms):
                    rejected_facts += 1
                    continue
                if kind == "color":
                    if color is not None and color != canonical:
                        rejected_facts += 1
                        continue
                    color = canonical
                    observed_facts["color"] = {"value": canonical, "evidence": evidence, "source": "explicit_text"}
                else:
                    key, state_value = _state_projection(canonical)
                    if key in current_state and current_state[key] != state_value:
                        rejected_facts += 1
                        continue
                    current_state[key] = state_value
                    observed_facts.setdefault("state", {})[key] = {
                        "value": state_value, "evidence": evidence, "source": "explicit_text"
                    }

            display_name = item.get("display_name")
            if not isinstance(display_name, str) or not display_name.strip():
                display_name = mention
            obj = {
                "id": "obj_{:03d}".format(len(objects) + 1),
                "name": display_name.strip(),
                "name_zh": mention,
                "object_type": object_type,
                "aliases": aliases,
                "affordance_tags": list(dict.fromkeys(tags)),
                "affordance_tag_source": "world_knowledge_inference",
                "selectable": item["selectable"],
                "source_mention": mention,
            }
            if color is not None:
                obj["color"] = color
            if current_state:
                obj["current_state"] = current_state
            if observed_facts:
                obj["observed_facts"] = observed_facts
            objects.append(obj)

        if not objects:
            raise TextSceneParseError("no source-grounded scene objects were returned")

        scene_id = "text_scene_" + hashlib.sha256(scene_text.encode("utf-8")).hexdigest()[:16]
        scene_type = raw["scene_type"].strip()
        scene = {
            "schema_version": SCENE_SCHEMA_VERSION,
            "scene_id": scene_id,
            "template_id": "m32_natural_language_text",
            "scene_type": scene_type,
            "scene_summary": "Source-grounded mentions: " + ", ".join(obj["source_mention"] for obj in objects),
            "objects": objects,
            "candidate_order": [obj["id"] for obj in objects if obj["selectable"]],
            "scene_text_sha256": hashlib.sha256(scene_text.encode("utf-8")).hexdigest(),
            "parser_version": M32_SCENE_PARSER_VERSION,
        }
        return {
            "scene": scene,
            "diagnostics": {
                "raw_object_count": len(raw["objects"]),
                "accepted_object_count": len(objects),
                "accepted_selectable_count": sum(1 for obj in objects if obj["selectable"]),
                "rejected_object_count": len(rejected_mentions),
                "rejected_object_mentions": rejected_mentions,
                "rejected_fact_count": rejected_facts,
                "rejected_affordance_tag_count": rejected_tags,
                "schema_errors": last_errors,
                "attempts": attempts,
                "total_latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
            },
            "provenance": {
                "model_id": self.model_id,
                "prompt_version": M32_SCENE_PARSER_VERSION,
                "schema_version": M32_SCENE_PARSER_SCHEMA_VERSION,
                "api_latency_ms": round(api_latency_ms, 3),
                "temperature": 0.0,
                "attempts": attempts,
                "retries": max(0, attempts - 1),
                "usage": usage,
            },
        }
