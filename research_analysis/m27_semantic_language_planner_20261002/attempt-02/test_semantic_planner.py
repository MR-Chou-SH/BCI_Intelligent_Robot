"""Public-seam tests for the experimental M27 semantic planner."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.m20_assistive_scene_contract import load_spec
from integration.m20_scene_layout_snapshot import create_scene_layout_snapshot
from semantic_planner import SemanticPlanner, semantic_scene_from_snapshot


ATTEMPT_DIR = Path(__file__).resolve().parent


def m20_snapshot():
    spec, _fingerprint = load_spec()
    snapshot, _runtime = create_scene_layout_snapshot(
        spec,
        seed=20261002,
        scene_id="m20-random-validation-20261002",
        created_utc="2026-10-02T00:00:00Z",
    )
    return snapshot


def plan(status, selected, actions=(), *, instruction="", reason=None, alternatives=()):
    return {
        "status": status,
        "intent_summary": "complete the selected semantic task",
        "selected_objects": list(selected),
        "actions": list(actions),
        "natural_language_instruction": instruction,
        "assumptions": [],
        "ambiguity_reason": reason,
        "alternatives": list(alternatives),
    }


class FakeModelClient:
    """Fake only the external model boundary; exercise the public planner API."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        self.calls.append({
            "messages": messages,
            "model_id": model_id,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        })
        response = self.responses.pop(0)
        return {
            "content": json.dumps(response, ensure_ascii=False),
            "model": model_id,
            "usage": {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30},
        }


class SemanticPlannerAcceptanceTests(unittest.TestCase):
    def test_phone_and_wireless_charger_make_a_grounded_place_on_plan(self):
        selected = ["assist_phone", "assist_wireless_charger"]
        client = FakeModelClient([plan(
            "executable",
            selected,
            [{"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"}],
            instruction="把手机放到无线充电座上。",
        )])

        result = SemanticPlanner(client, "test-model").plan(m20_snapshot(), selected)

        self.assertEqual(result["plan"]["status"], "executable")
        self.assertEqual(result["plan"]["selected_objects"], selected)
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["attempt_count"], 1)

    def test_closed_storage_allows_ordered_open_store_close_plan(self):
        selected = ["assist_medicine_box", "assist_storage_box"]
        actions = [
            {"type": "OPEN", "object_id": "assist_storage_box"},
            {"type": "PICK", "object_id": "assist_medicine_box"},
            {"type": "PLACE_IN", "object_id": "assist_medicine_box", "target_id": "assist_storage_box"},
            {"type": "CLOSE", "object_id": "assist_storage_box"},
        ]
        client = FakeModelClient([plan(
            "executable", selected, actions,
            instruction="打开收纳盒，把小药盒放进去，再关上收纳盒。",
        )])

        result = SemanticPlanner(client, "test-model").plan(
            m20_snapshot(), selected, current_states={"assist_storage_box": {"lid": "closed"}}
        )

        self.assertTrue(result["validation"]["valid"])
        self.assertEqual([item["type"] for item in result["plan"]["actions"]], ["OPEN", "PICK", "PLACE_IN", "CLOSE"])

    def test_ambiguous_scene_returns_alternatives_without_executable_actions(self):
        selected = ["assist_phone", "assist_wireless_charger", "assist_storage_box"]
        alternatives = [
            {"intent_summary": "charge the phone", "actions": [{"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"}], "natural_language_instruction": "把手机放到充电座上。"},
            {"intent_summary": "store the phone", "actions": [{"type": "PLACE_IN", "object_id": "assist_phone", "target_id": "assist_storage_box"}], "natural_language_instruction": "把手机放进收纳盒。"},
        ]
        client = FakeModelClient([plan(
            "ambiguous", selected, instruction="", reason="充电和收纳都符合当前场景，但没有目标线索。", alternatives=alternatives,
        )])

        result = SemanticPlanner(client, "test-model").plan(
            m20_snapshot(), selected, current_states={"assist_storage_box": {"lid": "open"}}
        )

        self.assertEqual(result["plan"]["status"], "ambiguous")
        self.assertEqual(result["plan"]["actions"], [])
        self.assertGreaterEqual(len(result["plan"]["alternatives"]), 2)
        self.assertTrue(result["validation"]["valid"])

    def test_multiple_selected_affordance_relations_cannot_be_resolved_by_salience(self):
        selected = ["assist_phone", "assist_wireless_charger", "assist_storage_box"]
        executable = plan(
            "executable", selected,
            [{"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"}],
            instruction="Place the phone on the charging pad.",
        )
        alternatives = [
            {"intent_summary": "charge phone", "actions": [{"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"}], "natural_language_instruction": "Place it on the charger."},
            {"intent_summary": "store phone", "actions": [{"type": "OPEN", "object_id": "assist_storage_box"}, {"type": "PICK", "object_id": "assist_phone"}, {"type": "PLACE_IN", "object_id": "assist_phone", "target_id": "assist_storage_box"}, {"type": "CLOSE", "object_id": "assist_storage_box"}], "natural_language_instruction": "Store it in the box."},
        ]
        corrected = plan("ambiguous", selected, reason="The scene supports charging or storage.", alternatives=alternatives)
        client = FakeModelClient([executable, corrected])

        result = SemanticPlanner(client, "test-model").plan(
            m20_snapshot(),
            selected,
            current_states={"assist_storage_box": {"lid": "closed"}},
            intent_context="Help decide what to do with these selected items.",
        )

        self.assertEqual(result["plan"]["status"], "ambiguous")
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["attempt_count"], 2)
        self.assertIn("multiple relations", result["attempt_diagnostics"][0]["validation_errors"][0])

    def test_ambiguous_affordance_fallback_stays_non_executable_after_two_bad_responses(self):
        selected = ["assist_phone", "assist_wireless_charger", "assist_storage_box"]
        bad = plan("invalid", selected, reason="No safe plan.")
        client = FakeModelClient([bad, bad])

        result = SemanticPlanner(client, "test-model").plan(
            m20_snapshot(),
            selected,
            current_states={"assist_storage_box": {"lid": "closed"}},
            intent_context="Help decide what to do with these selected items.",
        )

        self.assertEqual(result["plan"]["status"], "ambiguous")
        self.assertEqual(result["plan"]["actions"], [])
        self.assertTrue(result["validation"]["valid"])
        self.assertTrue(result["fallback_used"])
        self.assertEqual(len(result["plan"]["alternatives"]), 2)

    def test_affordance_conflict_gets_one_correction_then_fails_closed(self):
        selected = ["assist_phone", "assist_wireless_charger"]
        bad = plan(
            "executable", selected,
            [{"type": "PLACE_IN", "object_id": "assist_phone", "target_id": "assist_wireless_charger"}],
            instruction="把手机放进充电器。",
        )
        safe = plan("invalid", selected, reason="所选充电座不是容器，不能执行放入。")
        client = FakeModelClient([bad, safe])

        result = SemanticPlanner(client, "test-model", max_correction_retries=1).plan(m20_snapshot(), selected)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(result["plan"]["status"], "invalid")
        self.assertEqual(result["plan"]["actions"], [])
        self.assertEqual(result["attempt_count"], 2)

    def test_nonexistent_selected_object_is_rejected_before_model_call(self):
        client = FakeModelClient([])
        result = SemanticPlanner(client, "test-model").plan(m20_snapshot(), ["phantom_object"])

        self.assertEqual(result["plan"]["status"], "invalid")
        self.assertEqual(client.calls, [])
        self.assertFalse(result["validation"]["valid"])

    def test_missing_named_destination_cannot_be_hallucinated_into_a_plan(self):
        selected = ["assist_phone", "assist_wireless_charger"]
        hallucinated = plan(
            "executable", selected,
            [{"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "pixel_dock"}],
            instruction="把手机放到 Pixel Dock 上。",
        )
        safe = plan("invalid", selected, reason="场景中没有 Pixel Dock。")
        client = FakeModelClient([hallucinated, safe])

        result = SemanticPlanner(client, "test-model").plan(
            m20_snapshot(),
            selected,
            intent_context="Put the phone on the Pixel Dock.",
        )

        self.assertEqual(result["plan"]["status"], "invalid")
        self.assertEqual(result["plan"]["actions"], [])
        self.assertIn("intent_context", client.calls[0]["messages"][1]["content"])
        self.assertEqual(result["attempt_count"], 2)

    def test_scene_semantics_are_derived_from_snapshot_tags_and_state_input(self):
        scene = semantic_scene_from_snapshot(
            m20_snapshot(),
            current_states={"assist_storage_box": {"lid": "closed"}},
        )
        objects = {item["id"]: item for item in scene["objects"]}

        self.assertTrue(objects["assist_medicine_box"]["movable"])
        self.assertTrue(objects["assist_storage_box"]["container"])
        self.assertTrue(objects["assist_storage_box"]["openable"])
        self.assertEqual(objects["assist_storage_box"]["current_state"]["lid"], "closed")
        self.assertTrue(objects["assist_wireless_charger"]["charging_target"])


if __name__ == "__main__":
    unittest.main()
