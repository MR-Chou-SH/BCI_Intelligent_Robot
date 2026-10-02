"""Standalone open-world Semantic-to-VLA bridge demo (M31)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from integration.semantic_intelligence import (
    DeepSeekClientError,
    SemanticLanguageBridge as StrictSceneLanguageBridge,
    SemanticPlanner,
    build_live_client,
)
from integration.semantic_language_bridge import SemanticLanguageBridge


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENE = ROOT / "research_analysis/m29_semantic_bridge_demo_20261002/attempt-01/current_scene_snapshot.json"


def _load_scene(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("scene snapshot must be a JSON object")
    return value


def _parse_current_states(value: str) -> dict[str, dict[str, Any]]:
    """Keep the M29 strict-mode state argument compatible with its existing tests."""
    try:
        states = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("--states-json must be valid JSON") from error
    if not isinstance(states, dict) or any(
        not isinstance(object_id, str) or not isinstance(state, dict)
        for object_id, state in states.items()
    ):
        raise ValueError("--states-json must map scene object IDs to state objects")
    return states


def _format_result(result: dict[str, Any], as_json: bool = False) -> str:
    if as_json:
        return json.dumps(result, ensure_ascii=False, indent=2)
    if result.get("mode") == "semantic_open_world":
        objects = {
            item.get("object_id"): item.get("descriptor", item.get("object_name", ""))
            for item in [*result.get("selected_objects", []), *result.get("scene_context_objects", [])]
        }
        source_id = result.get("source_object_id")
        target_id = result.get("target_or_tool_object_id")
        lines = ["[Status] {}".format(result.get("status", "UNKNOWN").upper())]
        lines.append("[Mode] semantic_open_world")
        lines.append("[Semantic Relation] {}".format(result.get("relation_type", "NONE")))
        if result.get("custom_relation"):
            lines.append("[Custom Relation] {}".format(result["custom_relation"]))
        if source_id or target_id:
            lines.append("[Structured Intent] source={} | target/tool={} | action={}".format(
                objects.get(source_id, source_id or ""),
                objects.get(target_id, target_id or ""),
                result.get("high_level_action", "none"),
            ))
        for index, step in enumerate(result.get("ordered_high_level_steps", []), 1):
            target = step.get("target_object_id")
            suffix = " -> {}".format(objects.get(target, target)) if target else ""
            lines.append("[High-level Step {}] {}({}){}".format(
                index, step.get("action", ""), objects.get(step.get("object_id"), step.get("object_id", "")), suffix
            ))
        if result.get("ambiguity_reason"):
            lines.append("[Ambiguity] {}".format(result["ambiguity_reason"]))
        for assumption in result.get("assumptions", []):
            lines.append("[Assumption] {}".format(assumption))
        if result.get("vla_instruction_zh"):
            lines.append("[VLA Language - zh] {}".format(result["vla_instruction_zh"]))
        if result.get("vla_instruction_en"):
            lines.append("[VLA Language - en] {}".format(result["vla_instruction_en"]))
        if result.get("reason_code"):
            lines.append("[Reason] {}".format(result["reason_code"]))
        lines.append("[Scene Grounding] not required in semantic mode")
        lines.append("[Dispatch] disabled")
        provenance = result.get("provenance", {})
        if provenance:
            lines.append("[Model] {} | latency={} ms | retries={}".format(
                provenance.get("model_id", "unknown"), provenance.get("api_latency_ms", 0),
                provenance.get("retries", 0),
            ))
        return "\n".join(lines)

    lines = ["[Status] {}".format(result.get("status", "UNKNOWN").upper())]
    mode = result.get("mode", "strict_scene")
    lines.append("[Mode] {}".format(mode))
    for item in result.get("grounded_objects", []):
        lines.append("[Grounding] {} = {} ({})".format(item.get("input", ""), item.get("object_id", ""), item.get("trust", "unknown")))
    inference = result.get("free_noun_inference")
    if inference:
        lines.append("[Trust] model-inferred affordances are unverified; dispatch is disabled.")
        for item in inference.get("inferred_affordances", []):
            lines.append("[Inferred] {}: {} / {}".format(item["object_id"], item["object_type"], ", ".join(item["affordance_tags"]) or "no affordance asserted"))
    if result.get("actions"):
        lines.append("[Structured Plan]")
        for index, action in enumerate(result["actions"], 1):
            suffix = " -> {}".format(action["target_id"]) if action.get("target_id") else ""
            lines.append("{}. {}({}){}".format(index, action.get("type"), action.get("object_id"), suffix))
    if result.get("alternatives"):
        lines.append("[Plausible Alternatives]")
        for index, item in enumerate(result["alternatives"], 1):
            lines.append("{}. {}".format(index, item.get("intent_summary", "")))
            if item.get("vla_instruction_zh"):
                lines.append("   zh: {}".format(item["vla_instruction_zh"]))
            if item.get("vla_instruction_en"):
                lines.append("   en: {}".format(item["vla_instruction_en"]))
    if result.get("vla_instruction_zh"):
        lines.append("[VLA Language - zh] {}".format(result["vla_instruction_zh"]))
    if result.get("vla_instruction_en"):
        lines.append("[VLA Language - en] {}".format(result["vla_instruction_en"]))
    if result.get("reason_code"):
        lines.append("[Reason] {}".format(result["reason_code"]))
    lines.append("[Dispatch] disabled")
    return "\n".join(lines)


def _run_semantic(bridge: SemanticLanguageBridge, nouns: list[str], *,
                  scene_context: list[str], intent: str | None, as_json: bool) -> int:
    try:
        result = bridge.generate_instruction(nouns, scene_context=scene_context or None, task_context=intent)
    except (ValueError, DeepSeekClientError) as error:
        print("[Bridge Error] {}".format(type(error).__name__), file=sys.stderr)
        return 2
    print(_format_result(result, as_json=as_json))
    return 2 if result.get("reason_code") in {
        "invalid_input", "api_failure", "invalid_model_output_after_bounded_retry"
    } else 0


def _run_strict_pair(
    bridge: StrictSceneLanguageBridge,
    scene: dict[str, Any],
    nouns: list[str],
    *,
    mode: str,
    intent: str | None,
    current_states: dict[str, dict[str, Any]],
    as_json: bool,
) -> int:
    try:
        result = bridge.plan_nouns(
            scene, nouns, mode="free" if mode == "free" else "strict", intent_context=intent,
            current_states=current_states or None,
        )
    except (ValueError, DeepSeekClientError) as error:
        print("[Bridge Error] {}".format(type(error).__name__), file=sys.stderr)
        return 2
    print(_format_result(result, as_json=as_json))
    return 0


def _interactive_objects() -> list[str] | None:
    objects = []
    while len(objects) < 8:
        ordinal = len(objects) + 1
        prompt = "请输入物体{}（至少输入两个；第 3 个起可直接回车提交）：\n> ".format(ordinal)
        value = input(prompt).strip()
        if value.casefold() in {"q", "quit", "exit"}:
            return None
        if not value:
            if len(objects) >= 2:
                return objects
            print("至少需要两个物体。")
            continue
        objects.append(value)
    return objects


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nouns", nargs="*", help="selected object descriptors; semantic mode accepts 2-8")
    parser.add_argument("--mode", choices=("semantic", "strict-scene", "strict", "free"), default="semantic",
                        help="semantic (default) uses open-world reasoning; strict-scene keeps M29 validation")
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE, help="M20 snapshot used by strict-scene mode")
    parser.add_argument("--scene-context", nargs="*", default=[], help="optional explicitly observed context objects in semantic mode")
    parser.add_argument("--intent", help="optional short task instruction")
    parser.add_argument("--states-json", default="{}", help="M29 strict-scene state mapping")
    parser.add_argument("--json", action="store_true", help="print the structured result as JSON")
    args = parser.parse_args(argv)
    mode = "strict-scene" if args.mode == "strict" else args.mode
    if mode == "semantic" and args.nouns and not 2 <= len(args.nouns) <= 8:
        parser.error("semantic mode requires 2 to 8 object descriptors")
    if mode == "free" and args.nouns and len(args.nouns) != 2:
        parser.error("free mode requires exactly two nouns")
    if mode in {"strict-scene", "free"} and args.nouns and len(args.nouns) < 1:
        parser.error("strict-scene mode requires at least one noun")
    if mode == "semantic" and args.states_json != "{}":
        parser.error("--states-json is only supported by strict-scene mode; put explicit facts in descriptors")

    try:
        client, model_id, _, _ = build_live_client()
        base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        if mode == "semantic":
            bridge = SemanticLanguageBridge(client, model_id, base_url=base_url)
            scene = None
            current_states = {}
        else:
            scene = _load_scene(args.scene)
            current_states = _parse_current_states(args.states_json)
            bridge = StrictSceneLanguageBridge(SemanticPlanner(client, model_id, base_url=base_url, max_correction_retries=1))
    except (OSError, ValueError, DeepSeekClientError) as error:
        print("[Startup Error] {}".format(type(error).__name__), file=sys.stderr)
        return 2

    if args.nouns:
        nouns = args.nouns
    elif mode == "semantic":
        nouns = _interactive_objects()
        if nouns is None:
            return 0
    else:
        print("Semantic Bridge Demo | mode={}".format(mode))
        nouns = []
        for ordinal in (1, 2):
            value = input("\n请输入第{}个物体：\n> ".format(ordinal)).strip()
            if value.casefold() in {"q", "quit", "exit"}:
                return 0
            if not value:
                print("请输入非空物体名称。")
                return 2
            nouns.append(value)

    if mode == "semantic":
        return _run_semantic(bridge, nouns, scene_context=args.scene_context, intent=args.intent, as_json=args.json)
    return _run_strict_pair(bridge, scene or {}, nouns, mode=mode, intent=args.intent,
                            current_states=current_states, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
