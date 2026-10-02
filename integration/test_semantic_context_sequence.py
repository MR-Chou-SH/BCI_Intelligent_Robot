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
            rows = [{"candidate_id": "invented", "source_object_id": None, "semantic_score": 1.0,
                     "relation_type": "RELATED_TO", "relation_confidence": 0.9, "short_rationale_code": "invented"}]
            rows.extend({"candidate_id": candidate, "source_object_id": None, "semantic_score": 0.1,
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
                    "source_object_id": last,
                    "semantic_score": score,
                    "relation_type": relation,
                    "relation_confidence": score if relation != "NONE" else 0.0,
                    "short_rationale_code": "test_relation" if relation != "NONE" else "no_clear_relation",
                })
        payload = {"status": "informative", "candidate_scores": rows, "reason_code": "history_sensitive"}
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

    def test_full_container_relation_is_corrected_and_contradiction_logged(self):
        client = FakeSequentialContextClient()
        engine = SemanticContextEngine(client, "fake")
        result = engine.predict_next_target(scene=_scene(full_container=True), selection_history=["apple"])
        self.assertEqual(result["status"], "informative")
        self.assertEqual(result["provenance"]["attempts"], 2)
        self.assertTrue(any("destination_unavailable_from_explicit_state" in str(item)
                            for item in result["provenance"]["diagnostics"]))

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
                    "semantic_score": 0.9 if candidate == "knife" else 0.1,
                    "relation_type": "CUT_WITH" if candidate == "knife" else "NONE",
                    "relation_confidence": 0.8 if candidate == "knife" else 0.0,
                    "short_rationale_code": "cutting_tool" if candidate == "knife" else "no_relation",
                } for candidate in candidates]
                return {"content": json.dumps({"status": "informative", "candidate_scores": rows,
                                                "reason_code": "selected_source_missing"})}

        client = MissingSourceClient()
        result = SemanticContextEngine(client, "fake").predict_next_target(scene=_scene(), selection_history=["apple"])
        self.assertEqual(result["status"], "context_off")
        self.assertEqual(result["provenance"]["attempts"], 2)
        self.assertFalse(result["eligible_informative"])
        self.assertIn("source_required_for_relation", str(result["provenance"]["diagnostics"]))
        correction = json.loads(client.messages[1][1]["content"])["correction"]
        self.assertIn("accepted history source", correction["instruction"])

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
