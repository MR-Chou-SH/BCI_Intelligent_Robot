from __future__ import annotations

import json
import unittest

from integration.semantic_context_engine import SemanticContextEngine
from integration.semantic_context_sequence import (
    paginate_candidates,
    project_all_pages,
    project_global_prior,
)


def _scene(*, full_container: bool = False) -> dict:
    objects = [
        {"id": "apple", "object_type": "apple", "name": "apple", "name_zh": "苹果", "aliases": ["red apple"],
         "affordance_tags": ["food", "cuttable", "juicable", "washable"], "selectable": True},
        {"id": "knife", "object_type": "knife", "name": "knife", "name_zh": "水果刀",
         "affordance_tags": ["cutting_tool", "tool"], "selectable": True},
        {"id": "plate", "object_type": "plate", "name": "plate", "name_zh": "盘子",
         "affordance_tags": ["support_surface", "dish"], "selectable": True},
        {"id": "sink", "object_type": "sink", "name": "sink", "name_zh": "水槽",
         "affordance_tags": ["sink", "water_source"], "selectable": True},
        {"id": "orange", "object_type": "orange", "name": "orange", "name_zh": "橙子",
         "affordance_tags": ["food", "juicable"], "selectable": True},
        {"id": "juicer", "object_type": "juicer", "name": "juicer", "name_zh": "榨汁机",
         "affordance_tags": ["juicer", "juicing_appliance"], "selectable": True},
        {"id": "box", "object_type": "storage_box", "name": "storage box", "name_zh": "收纳盒",
         "affordance_tags": ["container", "openable"], "selectable": True,
         "current_state": {"full": full_container} if full_container else {}},
    ]
    return {
        "schema_version": "m27-semantic-scene-v1",
        "scene_id": "m30-test-kitchen",
        "scene_type": "kitchen",
        "candidate_order": [item["id"] for item in objects],
        "objects": objects,
    }


class FakeSequentialContextClient:
    def __init__(self, *, invalid_first: bool = False, dynamic_history: bool = True):
        self.messages: list[list[dict[str, str]]] = []
        self.invalid_first = invalid_first
        self.dynamic_history = dynamic_history

    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        self.messages.append(messages)
        request = json.loads(messages[1]["content"])
        context = request["context_input"]
        candidate_ids = context["candidate_next_object_ids"]
        history = context["selection_history"]
        if self.invalid_first and len(self.messages) == 1:
            rows = [{"candidate_id": "invented", "source_object_id": None, "candidate_task_continuation_score": 1.0,
                     "task_switch_penalty": 0.0,
                     "relation_type": "RELATED_TO", "relation_confidence": 0.9, "short_rationale_code": "invented"}]
            rows.extend({"candidate_id": candidate, "source_object_id": None, "candidate_task_continuation_score": 0.1,
                         "task_switch_penalty": 0.8,
                         "relation_type": "NONE", "relation_confidence": 0.0, "short_rationale_code": "no_relation"}
                        for candidate in candidate_ids[1:])
        else:
            last = history[-1] if history else None
            rows = []
            for candidate in candidate_ids:
                score, relation = 0.25, "NONE"
                if candidate == "knife":
                    score, relation = (0.91 if last == "apple" else 0.30), "CUT_WITH"
                elif candidate == "plate":
                    score, relation = (0.58 if last == "apple" else 0.93 if last == "knife" else 0.70), "PLACE_ON"
                elif candidate == "sink":
                    score, relation = 0.62, "RINSE_WITH"
                elif candidate == "juicer":
                    score, relation = 0.74 if last == "orange" else 0.37, "JUICE_WITH" if last == "orange" else "NONE"
                elif candidate == "box":
                    box = next(item for item in context["scene"]["objects"] if item["id"] == "box")
                    if box.get("current_state", {}).get("full") is True and request.get("correction"):
                        score, relation = 0.20, "NONE"
                    else:
                        score, relation = 0.66, "STORE_IN"
                rows.append({
                    "candidate_id": candidate,
                    "candidate_task_continuation_score": score,
                    "source_object_id": last,
                    "task_switch_penalty": 0.1 if score >= 0.5 else 0.8,
                    "relation_type": relation,
                    "relation_confidence": score if relation != "NONE" else 0.0,
                    "short_rationale_code": "test_relation" if relation != "NONE" else "no_clear_relation",
                })
        ordered_task = "_then_".join(history) if history else "no_history"
        payload = {
            "status": "informative",
            "task_state": {
                "hypothesis_code": "task_" + ordered_task,
                "hypothesis_summary": "The accepted ordered history defines the active task hypothesis.",
                "progress_code": "step_" + str(len(history)),
                "progress_summary": "The accepted history contains {} completed selection steps.".format(len(history)),
                "confidence": 0.9,
                "stability": 0.9,
            },
            "candidate_scores": rows,
            "reason_code": "history_sensitive",
        }
        return {"content": json.dumps(payload), "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}


class SemanticContextSequenceTests(unittest.TestCase):
    def test_variable_dimensional_prior_excludes_selected_ids_and_normalizes(self):
        engine = SemanticContextEngine(FakeSequentialContextClient(), "fake")
        result = engine.predict_next_target(scene=_scene(), selection_history=["apple"])
        self.assertEqual(result["status"], "informative")
        self.assertEqual(result["q_dimension"], 6)
        self.assertNotIn("apple", result["candidate_ids"])
        self.assertAlmostEqual(sum(result["context_prior"]), 1.0)
        self.assertEqual([row["candidate_id"] for row in result["q_global"]], result["candidate_ids"])

    def test_identical_sequential_input_reuses_exact_session_result_without_mutable_aliasing(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        first = engine.predict_next_target(scene=_scene(), selection_history=["apple", "knife"])
        expected_status = first["status"]
        expected_task_state = json.dumps(first["task_state"], sort_keys=True)
        expected_ranking = list(first["candidate_ranking"])
        expected_prior = list(first["context_prior"])
        expected_scores = json.dumps(first["candidate_scores"], sort_keys=True)
        first["candidate_scores"][0]["semantic_score"] = 999.0
        repeated = engine.predict_next_target(scene=_scene(), selection_history=["apple", "knife"])
        self.assertEqual(repeated["status"], expected_status)
        self.assertEqual(json.dumps(repeated["task_state"], sort_keys=True), expected_task_state)
        self.assertEqual(repeated["candidate_ranking"], expected_ranking)
        self.assertEqual(repeated["context_prior"], expected_prior)
        self.assertEqual(json.dumps(repeated["candidate_scores"], sort_keys=True), expected_scores)
        self.assertTrue(repeated["provenance"]["cache_hit"])
        self.assertEqual(repeated["provenance"]["api_latency_ms"], 0.0)
        self.assertEqual(len(client.messages), 1)
        engine.clear_sequence_result_cache()
        engine.predict_next_target(scene=_scene(), selection_history=["apple", "knife"])
        self.assertEqual(len(client.messages), 2)

    def test_history_is_sent_and_changes_same_candidates_prior_ranking(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        first = engine.predict_next_target(scene=_scene(), selection_history=["apple"])
        second = engine.predict_next_target(scene=_scene(), selection_history=["apple", "knife"])
        self.assertEqual(second["candidate_ids"], [x for x in first["candidate_ids"] if x != "knife"])
        first_plate = first["context_prior"][first["candidate_ids"].index("plate")]
        second_plate = second["context_prior"][second["candidate_ids"].index("plate")]
        self.assertGreater(second_plate, first_plate)
        second_request = json.loads(client.messages[1][1]["content"])["context_input"]
        self.assertEqual(second_request["selection_history"], ["apple", "knife"])

    def test_model_input_does_not_receive_evaluation_labels_or_unspecified_color(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        scene = _scene()
        result = engine.predict_next_target(scene=scene, selection_history=["apple"])
        self.assertEqual(result["status"], "informative")
        serialized = client.messages[0][1]["content"]
        self.assertNotIn("evaluation_only", serialized)
        self.assertNotIn("expected_target_ids", serialized)
        self.assertNotIn('"color"', serialized)

    def test_evaluation_only_fields_are_rejected_before_model_call(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        scene = _scene()
        scene["evaluation_only"] = {"expected_target_ids": ["knife"]}
        result = engine.predict_next_target(scene=scene, selection_history=["apple"])
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(client.messages, [])

    def test_unknown_model_candidate_gets_one_bounded_retry(self):
        client = FakeSequentialContextClient(invalid_first=True)
        engine = SemanticContextEngine(client, "fake")
        result = engine.predict_next_target(scene=_scene(), selection_history=["apple"])
        self.assertEqual(result["status"], "informative")
        self.assertEqual(result["provenance"]["attempts"], 2)
        self.assertEqual(result["provenance"]["retries"], 1)
        self.assertTrue(any("unknown_candidate_id" in str(item) for item in result["provenance"]["diagnostics"]))

    def test_full_container_relation_only_downgrades_that_candidate(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        result = engine.predict_next_target(scene=_scene(full_container=True), selection_history=["apple"])
        self.assertEqual(result["status"], "informative")
        self.assertEqual(result["provenance"]["attempts"], 1)
        box = next(row for row in result["candidate_scores"] if row["candidate_id"] == "box")
        self.assertEqual(box["relation_type"], "NONE")
        self.assertTrue(any("destination_unavailable_from_explicit_state" in str(item)
                            for item in result["provenance"]["diagnostics"]))
        self.assertGreater(len(set(round(value, 8) for value in result["context_prior"])), 1)

    def test_source_tag_relation_requires_a_selected_source_id(self):
        class MissingSourceClient:
            def __init__(self):
                self.messages = []

            def complete(self, messages, **kwargs):
                self.messages.append(messages)
                request = json.loads(messages[1]["content"])
                candidates = request["context_input"]["candidate_next_object_ids"]
                rows = [{
                    "candidate_id": candidate,
                    "source_object_id": None,
                    "candidate_task_continuation_score": 0.9 if candidate == "knife" else 0.1,
                    "task_switch_penalty": 0.1 if candidate == "knife" else 0.9,
                    "relation_type": "CUT_WITH" if candidate == "knife" else "NONE",
                    "relation_confidence": 0.8 if candidate == "knife" else 0.0,
                    "short_rationale_code": "cutting_tool" if candidate == "knife" else "no_relation",
                } for candidate in candidates]
                return {"content": json.dumps({"status": "informative", "task_state": {
                                                "hypothesis_code": "apple_prep", "hypothesis_summary": "Prepare the apple.",
                                                "progress_code": "tool_needed", "progress_summary": "A cutting tool is the next step.",
                                                "confidence": 0.9, "stability": 0.9}, "candidate_scores": rows,
                                                "reason_code": "selected_source_missing"})}

        client = MissingSourceClient()
        result = SemanticContextEngine(client, "fake").predict_next_target(scene=_scene(), selection_history=["apple"])
        self.assertEqual(result["provenance"]["attempts"], 1)
        knife = next(row for row in result["candidate_scores"] if row["candidate_id"] == "knife")
        self.assertEqual(knife["relation_type"], "NONE")
        self.assertEqual(result["status"], "informative")
        self.assertIn("source_required_for_relation", str(result["provenance"]["diagnostics"]))

    def test_red_apple_then_knife_task_state_uses_ordered_history_and_same_candidates(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        forward = engine.predict_next_target(scene=_scene(), selection_history=["apple", "knife"])
        reversed_order = engine.predict_next_target(scene=_scene(), selection_history=["knife", "apple"])
        self.assertEqual(forward["candidate_ids"], reversed_order["candidate_ids"])
        self.assertEqual(forward["task_state"]["hypothesis_code"], "task_apple_then_knife")
        self.assertIn("2 completed selection steps", forward["task_state"]["progress_summary"])
        self.assertNotEqual(forward["task_state"]["hypothesis_code"], reversed_order["task_state"]["hypothesis_code"])
        self.assertNotEqual(forward["candidate_ranking"], reversed_order["candidate_ranking"])
        self.assertNotEqual(forward["context_prior"], reversed_order["context_prior"])
        self.assertEqual(forward["task_state"]["progress_code"], reversed_order["task_state"]["progress_code"])

    def test_ambiguous_status_has_normalized_q_but_is_not_eligible(self):
        class AmbiguousClient(FakeSequentialContextClient):
            def complete(self, messages, **kwargs):
                response = super().complete(messages, **kwargs)
                parsed = json.loads(response["content"])
                parsed["status"] = "ambiguous"
                response["content"] = json.dumps(parsed)
                return response
        result = SemanticContextEngine(AmbiguousClient(), "fake").predict_next_target(scene=_scene(), selection_history=["apple"])
        self.assertEqual(result["status"], "ambiguous")
        self.assertFalse(result["eligible_informative"])
        self.assertAlmostEqual(sum(result["context_prior"]), 1.0)

    def test_completed_task_and_single_remaining_choice_do_not_call_model(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        done = engine.predict_next_target(scene=_scene(), selection_history=["apple"], current_task_state={"phase": "complete"})
        one_left = engine.predict_next_target(scene=_scene(), selection_history=["apple", "knife", "plate", "sink", "orange", "juicer"])
        self.assertEqual(done["status"], "context_off")
        self.assertEqual(done["q_dimension"], 6)
        self.assertEqual(one_left["q_dimension"], 1)
        self.assertEqual(one_left["context_prior"], [1.0])
        self.assertEqual(client.messages, [])

    def test_unavailable_required_tool_fails_closed_before_model_call(self):
        client = FakeSequentialContextClient()
        result = SemanticContextEngine(client, "fake").predict_next_target(
            scene=_scene(), selection_history=["apple"],
            current_task_state={"phase": "in_progress", "required_tool_available": False},
        )
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "required_tool_unavailable")
        self.assertEqual(result["provenance"]["attempts"], 0)
        self.assertEqual(client.messages, [])

    def test_missing_required_target_affordance_fails_closed_before_model_call(self):
        client = FakeSequentialContextClient()
        result = SemanticContextEngine(client, "fake").predict_next_target(
            scene=_scene(), selection_history=["apple"],
            current_task_state={"phase": "in_progress", "required_target_affordance": "charging_target"},
        )
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "required_target_affordance_absent")
        self.assertEqual(result["provenance"]["attempts"], 0)
        self.assertEqual(client.messages, [])

    def test_unknown_or_duplicate_selection_is_invalid(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        duplicate = engine.predict_next_target(scene=_scene(), selection_history=["apple", "apple"])
        unknown = engine.predict_next_target(scene=_scene(), selection_history=["not_in_scene"])
        self.assertEqual(duplicate["status"], "invalid")
        self.assertEqual(unknown["status"], "invalid")
        self.assertEqual(client.messages, [])

    def test_page_projection_is_deterministic_and_supports_two_choice_final_page(self):
        q_global = [{"candidate_id": f"o{i}", "q": 1.0 / 8} for i in range(8)]
        pages = paginate_candidates([f"o{i}" for i in range(8)])
        self.assertEqual([len(page) for page in pages], [3, 3, 2])
        projected = project_all_pages(q_global)
        self.assertEqual([row["q_page"] for row in projected], [[1 / 3] * 3, [1 / 3] * 3, [0.5, 0.5]])
        self.assertTrue(all(abs(sum(row["q_page"]) - 1.0) < 1e-12 for row in projected))
        self.assertEqual(project_global_prior(q_global, ["o6", "o7"])["page_size"], 2)

    def test_page_projection_rejects_fake_or_duplicate_candidates(self):
        q_global = [{"candidate_id": "a", "q": 0.4}, {"candidate_id": "b", "q": 0.6}]
        with self.assertRaises(ValueError):
            project_global_prior(q_global, ["a", "missing"])
        with self.assertRaises(ValueError):
            project_global_prior(q_global, ["a", "a"])
        with self.assertRaises(ValueError):
            paginate_candidates(["a", "a"])


if __name__ == "__main__":
    unittest.main()
