from __future__ import annotations

import unittest

from integration.semantic_context_demo_cli import (
    SemanticContextSession, remaining_selectable_candidates, run_interactive,
)
from integration.semantic_context_engine import SemanticContextEngine
from integration.semantic_intelligence import SCENE_SCHEMA_VERSION


def _scene():
    objects = [
        {"id": "obj_001", "name": "苹果", "name_zh": "苹果", "object_type": "apple", "aliases": ["apple"], "affordance_tags": ["food"], "selectable": True},
        {"id": "obj_002", "name": "水果刀", "name_zh": "水果刀", "object_type": "knife", "aliases": ["knife"], "affordance_tags": ["knife", "cutting_tool"], "selectable": True},
        {"id": "obj_003", "name": "盘子", "name_zh": "盘子", "object_type": "plate", "aliases": ["plate"], "affordance_tags": ["dish", "support_surface"], "selectable": True},
        {"id": "obj_004", "name": "收纳盒", "name_zh": "收纳盒", "object_type": "box", "aliases": ["box"], "affordance_tags": ["container"], "selectable": True},
    ]
    return {"schema_version": SCENE_SCHEMA_VERSION, "scene_id": "test-scene", "objects": objects,
            "candidate_order": [obj["id"] for obj in objects]}


class RecordingEngine:
    def __init__(self):
        self.calls = []

    def predict_next_target(self, *, scene, selection_history, remaining_candidates, current_task_state):
        self.calls.append({"history": list(selection_history), "candidates": list(remaining_candidates)})
        order = list(remaining_candidates)
        if selection_history and selection_history[-1] == "obj_002":
            order.reverse()
        scores = [0.9 - 0.1 * index for index, _ in enumerate(order)]
        total = sum(scores)
        rows = [{"candidate_id": object_id, "semantic_score": score, "relation_type": "RELATED_TO",
                 "relation_confidence": 0.5, "short_rationale_code": "test_reason"}
                for object_id, score in zip(order, scores)]
        q = [score / total for score in scores]
        return {"status": "informative", "candidate_ids": list(remaining_candidates),
                "candidate_scores": rows, "candidate_ranking": order,
                "q_global": [{"candidate_id": object_id, "q": value}
                             for object_id, value in zip(order, q)],
                "q_dimension": len(order), "prior_top_mass": max(q), "prior_margin": 0.1,
                "normalized_entropy": 0.5, "provenance": {"api_latency_ms": 12.0}}


class SequenceCompletionClient:
    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        import json

        request = json.loads(messages[-1]["content"])
        context = request["context_input"]
        candidates = context["candidate_next_object_ids"]
        order = list(candidates)
        if context["selection_history"] and context["selection_history"][-1] == "obj_002":
            order.reverse()
        scores = [0.9 - index * 0.1 for index in range(len(order))]
        return {"content": json.dumps({
            "status": "context_off", "reason_code": "no_clear_relation",
            "task_state": {
                "hypothesis_code": "uncertain", "hypothesis_summary": "No clear task hypothesis is available.",
                "progress_code": "unknown", "progress_summary": "The task progress is unknown.",
                "confidence": 0.2, "stability": 0.2,
            },
            "candidate_scores": [
                {"candidate_id": object_id, "source_object_id": None,
                 "candidate_task_continuation_score": score, "task_switch_penalty": 0.8,
                 "relation_type": "NONE", "relation_confidence": 0.0,
                 "short_rationale_code": "no_clear_link"}
                for object_id, score in zip(order, scores)
            ],
        }), "usage": {}}


class SemanticContextSessionPublicInterfaceTests(unittest.TestCase):
    def test_candidate_helper_uses_all_selectable_objects_and_skips_scene_context(self):
        scene = _scene()
        scene["objects"].append({
            "id": "room", "name": "厨房", "name_zh": "厨房", "object_type": "room",
            "aliases": [], "affordance_tags": [], "selectable": False,
        })
        scene["candidate_order"].append("room")

        remaining = remaining_selectable_candidates(scene, ["obj_001"])

        self.assertEqual(remaining, ["obj_002", "obj_003", "obj_004"])
        with self.assertRaises(ValueError):
            remaining_selectable_candidates(scene, ["room"])

    def test_selection_shrinks_global_prior_and_undo_recomputes_from_restored_history(self):
        engine = RecordingEngine()
        session = SemanticContextSession(_scene(), engine)

        first = session.select("苹果")
        self.assertEqual(first["status"], "selected")
        self.assertEqual(first["context"]["q_dimension"], 3)
        second = session.select("knife")
        self.assertEqual(second["status"], "selected")
        self.assertEqual(second["context"]["q_dimension"], 2)
        ranking_after_second = second["context"]["candidate_ranking"]

        restored = session.undo()

        self.assertEqual(restored["history"], ["obj_001"])
        self.assertEqual(restored["context"]["q_dimension"], 3)
        self.assertNotEqual(restored["context"]["candidate_ranking"], ranking_after_second)
        self.assertEqual(engine.calls[-1]["history"], ["obj_001"])
        self.assertNotIn("obj_001", engine.calls[-1]["candidates"])

    def test_alias_collision_is_reported_as_ambiguous_and_submit_never_dispatches(self):
        scene = _scene()
        scene["objects"][2]["aliases"].append("box")
        session = SemanticContextSession(scene, RecordingEngine())

        ambiguous = session.select("box")
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual({item["object_id"] for item in ambiguous["candidates"]}, {"obj_003", "obj_004"})
        result = session.submit()
        self.assertFalse(result["dispatch_allowed"])
        self.assertEqual(session.select("苹果")["status"], "submitted")

    def test_real_m30_public_engine_api_returns_normalized_prior_over_all_remaining_ids(self):
        engine = SemanticContextEngine(SequenceCompletionClient(), "fake-model")
        session = SemanticContextSession(_scene(), engine)

        first = session.select("苹果")["context"]
        second = session.select("水果刀")["context"]

        self.assertEqual(first["q_dimension"], 3)
        self.assertEqual(second["q_dimension"], 2)
        self.assertAlmostEqual(sum(row["q"] for row in first["q_global"]), 1.0)
        self.assertAlmostEqual(sum(row["q"] for row in second["q_global"]), 1.0)
        self.assertNotIn("obj_001", [row["candidate_id"] for row in first["q_global"]])
        self.assertNotIn("obj_002", [row["candidate_id"] for row in second["q_global"]])

    def test_interactive_runner_supports_scene_review_selection_undo_and_terminal_submit(self):
        class StubParser:
            def parse(self, _text):
                return {"scene": _scene(), "diagnostics": {}, "provenance": {}}

        answers = iter(["这是一个厨房场景", "start", "1", "undo", "submit"])
        outputs = []
        result = run_interactive(StubParser(), RecordingEngine(),
                                 input_fn=lambda _prompt: next(answers), output_fn=outputs.append)

        self.assertEqual(result, 0)
        self.assertTrue(any("[Scene Parsed]" in line for line in outputs))
        self.assertTrue(any("Undo: undone" in line for line in outputs))
        self.assertTrue(any("Context recomputed" in line for line in outputs))
        self.assertTrue(any("Dispatch: disabled" in line for line in outputs))


if __name__ == "__main__":
    unittest.main()
