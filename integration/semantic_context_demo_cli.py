"""Interactive natural-language scene and sequential M30 Context demonstration."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable

from integration.semantic_context_engine import SemanticContextEngine
from integration.semantic_intelligence import (
    DeepSeekClientError,
    ObjectGrounder,
    PlannerInputError,
    SemanticSceneCore,
    build_live_client,
)
from integration.text_scene_parser import TextSceneParseError, TextSceneParser


CURRENT_TASK_STATE = {
    "task_context": "Infer the next semantically related object from the complete accepted selection history and the remaining scene.",
}


def _ordered_selectable_ids(scene: dict[str, Any]) -> list[str]:
    objects = {
        item["id"]: item for item in scene.get("objects", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    selectable = {object_id for object_id, item in objects.items() if item.get("selectable", False)}
    order = [object_id for object_id in scene.get("candidate_order", []) if object_id in selectable]
    order.extend(object_id for object_id in objects if object_id in selectable and object_id not in order)
    return order


def remaining_selectable_candidates(scene: dict[str, Any], selection_history: list[str]) -> list[str]:
    """Return every selectable scene candidate except the full accepted history."""
    order = _ordered_selectable_ids(scene)
    selected = set(selection_history)
    if len(selected) != len(selection_history) or not selected.issubset(set(order)):
        raise ValueError("selection_history contains an unknown, non-selectable, or repeated object")
    return [object_id for object_id in order if object_id not in selected]


class SemanticContextSession:
    """Public selection-session boundary used by the interactive CLI."""

    def __init__(
        self,
        scene: dict[str, Any],
        engine: Any,
        *,
        current_task_state: dict[str, Any] | None = None,
    ) -> None:
        self.core = SemanticSceneCore(scene)
        self.scene = self.core.scene
        self.engine = engine
        self.current_task_state = dict(current_task_state or CURRENT_TASK_STATE)
        self.history: list[str] = []
        self.latest_context: dict[str, Any] | None = None
        self.submitted = False
        self._grounder = ObjectGrounder()

    def remaining_candidates(self) -> list[str]:
        return remaining_selectable_candidates(self.scene, self.history)

    def resolve(self, query: str) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            return {"status": "unknown", "query": query, "object_id": None, "candidates": []}
        query = query.strip()
        remaining = self.remaining_candidates()
        if query.isdecimal():
            index = int(query) - 1
            if 0 <= index < len(remaining):
                object_id = remaining[index]
                return {"status": "grounded", "query": query, "object_id": object_id,
                        "candidates": [{"object_id": object_id, "score": 1.0}]}
            return {"status": "unknown", "query": query, "object_id": None, "candidates": []}
        grounded = self._grounder.ground(query, self.core)
        if grounded.get("status") == "grounded" and grounded.get("object_id") not in remaining:
            grounded["status"] = "already_selected"
        return grounded

    def _recompute(self) -> dict[str, Any] | None:
        remaining = self.remaining_candidates()
        if not remaining:
            self.latest_context = None
            return None
        self.latest_context = self.engine.predict_next_target(
            scene=self.scene,
            selection_history=list(self.history),
            remaining_candidates=remaining,
            current_task_state=dict(self.current_task_state),
        )
        return self.latest_context

    def select(self, query: str) -> dict[str, Any]:
        if self.submitted:
            return {"status": "submitted"}
        grounded = self.resolve(query)
        if grounded.get("status") != "grounded":
            return grounded
        object_id = grounded["object_id"]
        self.history.append(object_id)
        return {"status": "selected", "object_id": object_id,
                "history": list(self.history), "context": self._recompute()}

    def undo(self) -> dict[str, Any]:
        if self.submitted:
            return {"status": "submitted", "history": list(self.history), "context": self.latest_context}
        if not self.history:
            return {"status": "empty_history", "history": [], "context": self.latest_context}
        removed = self.history.pop()
        # Always call the same M30 public API again, including after undoing to
        # empty history; printed rankings are never restored from a cache.
        context = self._recompute()
        return {"status": "undone", "removed_object_id": removed,
                "history": list(self.history), "context": context}

    def reset(self) -> dict[str, Any]:
        if self.submitted:
            return {"status": "submitted", "history": list(self.history), "context": self.latest_context}
        self.history.clear()
        context = self._recompute()
        return {"status": "reset", "history": [], "context": context}

    def context(self) -> dict[str, Any] | None:
        return self.latest_context

    def submit(self) -> dict[str, Any]:
        self.submitted = True
        return {
            "status": "submitted",
            "history": list(self.history),
            "display_names": [self.core.objects[item].get("name_zh") or self.core.objects[item].get("name", item)
                              for item in self.history],
            "dispatch_allowed": False,
        }


def _display_name(item: dict[str, Any]) -> str:
    return item.get("name_zh") or item.get("name") or item["id"]


def _render_scene(scene: dict[str, Any], output_fn: Callable[[str], None]) -> None:
    output_fn("[Scene Parsed]")
    output_fn("Scene type: {}".format(scene.get("scene_type", "unspecified")))
    for index, item in enumerate(scene.get("objects", []), 1):
        details = []
        details.append("selectable target" if item.get("selectable", False) else "context only")
        if item.get("color"):
            details.append("explicit color={}".format(item["color"]))
        if item.get("current_state"):
            details.append("explicit state={}".format(item["current_state"]))
        details.append("affordance tags=inferred")
        output_fn("{}. {} [{}] ({})".format(index, _display_name(item), item["id"], "; ".join(details)))
    output_fn("Selectable candidates: {}".format(len(_ordered_selectable_ids(scene))))
    output_fn("检查对象名称、显式颜色/状态和数量；输入 start 开始，edit 重新描述，q 退出。")


def _render_context(context: dict[str, Any] | None, scene: dict[str, Any], output_fn: Callable[[str], None]) -> None:
    if not context:
        output_fn("[Semantic Context] no remaining candidates")
        return
    names = {item["id"]: _display_name(item) for item in scene.get("objects", [])}
    candidate_ids = context.get("candidate_ids", [])
    scores = {row.get("candidate_id"): row for row in context.get("candidate_scores", [])
              if isinstance(row, dict)}
    q_values = {row.get("candidate_id"): row.get("q") for row in context.get("q_global", [])
                if isinstance(row, dict)}
    output_fn("[Semantic Context after accepted history]")
    output_fn("Candidate                 Relation        Semantic score     q_global     Rationale")
    output_fn("-" * 86)
    for candidate_id in candidate_ids:
        row = scores.get(candidate_id, {})
        relation = row.get("relation_type", "NONE")
        score = row.get("semantic_score")
        q_value = q_values.get(candidate_id)
        score_text = "—" if score is None else "{:.3f}".format(float(score))
        q_text = "—" if q_value is None else "{:.4f}".format(float(q_value))
        rationale = row.get("short_rationale_code", "")
        output_fn("{:<25} {:<15} {:>14} {:>12}     {}".format(
            names.get(candidate_id, candidate_id), relation, score_text, q_text, rationale
        ))
    output_fn("Status: {}".format(str(context.get("status", "unknown")).upper()))
    top_id = context.get("top_candidate")
    output_fn("Top candidate: {}".format(names.get(top_id, top_id) if top_id else "none"))
    output_fn("Top mass: {} | Margin: {} | Normalized entropy: {}".format(
        _fmt(context.get("prior_top_mass")), _fmt(context.get("prior_margin")),
        _fmt(context.get("normalized_entropy")),
    ))
    provenance = context.get("provenance", {})
    output_fn("DeepSeek latency: {} ms".format(_fmt(provenance.get("api_latency_ms"))))
    output_fn("q_global is an uncalibrated semantic prior, not a probability.")


def _fmt(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else "{:.3f}".format(float(value))


def _render_history(session: SemanticContextSession, output_fn: Callable[[str], None]) -> None:
    output_fn("Selection History:")
    if not session.history:
        output_fn("  (empty)")
        return
    for index, object_id in enumerate(session.history, 1):
        output_fn("{}. {}".format(index, _display_name(session.core.objects[object_id])))


def _print_help(output_fn: Callable[[str], None]) -> None:
    output_fn("Commands: help, scene, history, context, undo, reset, submit/done/end, q")
    output_fn("输入物体名称、中文/英文别名，或输入当前候选列表中的编号进行选择。")


def run_interactive(
    parser: TextSceneParser,
    engine: SemanticContextEngine,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    while True:
        try:
            scene_text = input_fn("请输入自然语言场景描述（q 退出）：\n> ")
        except EOFError:
            return 0
        if scene_text.strip().casefold() in {"q", "quit", "exit"}:
            return 0
        try:
            parsed = parser.parse(scene_text)
        except (TextSceneParseError, DeepSeekClientError, ValueError) as error:
            output_fn("[Scene Parse Error] {}".format(type(error).__name__))
            continue
        scene = parsed["scene"]
        if len([item for item in scene["objects"] if item.get("selectable", False)]) < 2:
            output_fn("[Scene Parse Error] at least two selectable objects are required")
            continue
        _render_scene(scene, output_fn)
        while True:
            try:
                command = input_fn("start / edit / q > ").strip().casefold()
            except EOFError:
                return 0
            if command in {"q", "quit", "exit"}:
                return 0
            if command == "edit":
                break
            if command == "start":
                session = SemanticContextSession(scene, engine)
                _print_help(output_fn)
                while not session.submitted:
                    try:
                        query = input_fn("请输入下一个已选择物体，或输入命令：\n> ").strip()
                    except EOFError:
                        return 0
                    command_key = query.casefold()
                    if command_key in {"q", "quit", "exit"}:
                        return 0
                    if command_key == "help":
                        _print_help(output_fn)
                    elif command_key == "scene":
                        _render_scene(scene, output_fn)
                    elif command_key == "history":
                        _render_history(session, output_fn)
                    elif command_key == "context":
                        _render_context(session.context(), scene, output_fn)
                    elif command_key == "undo":
                        result = session.undo()
                        output_fn("Undo: {}".format(result["status"]))
                        if result.get("status") == "undone":
                            output_fn("Context recomputed from the restored selection history.")
                        _render_history(session, output_fn)
                        _render_context(result.get("context"), scene, output_fn)
                    elif command_key == "reset":
                        result = session.reset()
                        output_fn("Selection history reset; Context recomputed from the empty history.")
                        _render_context(result.get("context"), scene, output_fn)
                    elif command_key in {"submit", "done", "end"}:
                        result = session.submit()
                        output_fn("[Final Selection History]")
                        _render_history(session, output_fn)
                        output_fn("Dispatch: disabled; no VLA or robot action was invoked.")
                    else:
                        result = session.select(query)
                        if result.get("status") == "ambiguous":
                            options = result.get("candidates", [])
                            output_fn("该名称匹配多个物体，请输入下面候选编号：")
                            for index, item in enumerate(options, 1):
                                object_id = item["object_id"]
                                output_fn("{}. {} [{}]".format(index, _display_name(session.core.objects[object_id]), object_id))
                            try:
                                choice = input_fn("选择 > ").strip()
                            except EOFError:
                                return 0
                            if choice.isdecimal() and 1 <= int(choice) <= len(options):
                                result = session.select(options[int(choice) - 1]["object_id"])
                            else:
                                result = {"status": "unknown"}
                        if result.get("status") == "selected":
                            output_fn("Selected: {}".format(_display_name(session.core.objects[result["object_id"]])))
                            _render_history(session, output_fn)
                            _render_context(result.get("context"), scene, output_fn)
                        elif result.get("status") == "already_selected":
                            output_fn("该物体已在 Selection History 中。")
                        else:
                            output_fn("未能唯一匹配该物体；请使用场景中的名称、别名或编号。")
                return 0
            output_fn("请输入 start、edit 或 q。")


def main(argv: list[str] | None = None) -> int:
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--base-url", default=os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com")
    args = argument_parser.parse_args(argv)
    try:
        client, model_id, _, _ = build_live_client()
        parser = TextSceneParser(client, model_id)
        engine = SemanticContextEngine(client, model_id, base_url=args.base_url, max_correction_retries=1)
    except (OSError, ValueError, DeepSeekClientError) as error:
        print("[Startup Error] {}".format(type(error).__name__), file=sys.stderr)
        return 2
    return run_interactive(parser, engine)


if __name__ == "__main__":
    raise SystemExit(main())
