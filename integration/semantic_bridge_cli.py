"""Standalone interactive demo for the reusable semantic-to-VLA language bridge."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from integration.semantic_intelligence import (
    DeepSeekClientError,
    SemanticLanguageBridge,
    SemanticPlanner,
    build_live_client,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENE = ROOT / "research_analysis/m29_semantic_bridge_demo_20261002/attempt-01/current_scene_snapshot.json"


def _load_scene(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("scene snapshot must be a JSON object")
    return value


def _parse_current_states(value: str) -> dict[str, dict[str, Any]]:
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


def _run_pair(
    bridge: SemanticLanguageBridge,
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
            scene, nouns, mode=mode, intent_context=intent,
            current_states=current_states or None,
        )
    except (ValueError, DeepSeekClientError) as error:
        print("[Bridge Error] {}".format(type(error).__name__), file=sys.stderr)
        return 2
    print(_format_result(result, as_json=as_json))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nouns", nargs="*", help="object nouns; free mode requires exactly two; omit to use interactive prompts")
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE, help="M20 SceneLayoutSnapshot JSON (strict scene catalog)")
    parser.add_argument("--mode", choices=("strict", "free"), default="strict", help="strict grounded catalog or experimental unverified free nouns")
    parser.add_argument("--intent", help="optional short task instruction")
    parser.add_argument("--states-json", default="{}", help="current object state mapping, for example '{\"assist_storage_box\":{\"lid\":\"closed\"}}'")
    parser.add_argument("--json", action="store_true", help="print the structured result as JSON")
    args = parser.parse_args(argv)
    if args.mode == "free" and args.nouns and len(args.nouns) != 2:
        parser.error("free mode requires exactly two nouns")
    if args.mode == "strict" and args.nouns and len(args.nouns) < 1:
        parser.error("strict mode requires at least one noun")
    try:
        scene = _load_scene(args.scene)
        current_states = _parse_current_states(args.states_json)
        client, model_id, _, _ = build_live_client()
        base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
        planner = SemanticPlanner(client, model_id, base_url=base_url, max_correction_retries=1)
        bridge = SemanticLanguageBridge(planner)
    except (OSError, ValueError, DeepSeekClientError) as error:
        print("[Startup Error] {}".format(type(error).__name__), file=sys.stderr)
        return 2

    if args.nouns:
        return _run_pair(
            bridge, scene, args.nouns, mode=args.mode, intent=args.intent,
            current_states=current_states, as_json=args.json,
        )

    print("Semantic Bridge Demo")
    print("模式: {} | 输入两个物体名称；输入 q 退出。".format(args.mode))
    while True:
        first = input("\n请输入第一个物体：\n> ").strip()
        if first.casefold() in {"q", "quit", "exit"}:
            return 0
        if not first:
            continue
        second = input("请输入第二个物体：\n> ").strip()
        if second.casefold() in {"q", "quit", "exit"}:
            return 0
        if not second:
            print("请输入两个非空物体名称。")
            continue
        _run_pair(
            bridge, scene, [first, second], mode=args.mode, intent=args.intent,
            current_states=current_states, as_json=args.json,
        )


if __name__ == "__main__":
    raise SystemExit(main())
