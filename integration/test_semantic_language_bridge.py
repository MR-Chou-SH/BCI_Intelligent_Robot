"""Offline acceptance tests for the M29 reusable language bridge."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from integration.semantic_intelligence import SemanticLanguageBridge, SemanticPlanner
from integration.semantic_bridge_cli import _parse_current_states


ROOT = Path(__file__).resolve().parents[1]
SCENE_PATH = ROOT / "research_analysis/m29_semantic_bridge_demo_20261002/attempt-01/current_scene_snapshot.json"


class FakeCompletionClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.messages = []

    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        self.messages.append(messages)
        if not self.responses:
            raise AssertionError("unexpected language model call")
        value = self.responses.pop(0)
        return {"content": json.dumps(value, ensure_ascii=False), "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def _plan(selected, actions, *, status="executable", alternatives=None):
    return {
        "status": status,
        "intent_summary": "Grounded fixture plan" if status == "executable" else "Several relations are plausible.",
        "selected_objects": list(selected),
        "actions": actions if status == "executable" else [],
        "natural_language_instruction": "Place the source on the destination." if status == "executable" else "",
        "assumptions": [],
        "ambiguity_reason": "Multiple scene relations remain plausible." if status == "ambiguous" else "",
        "alternatives": alternatives or [],
    }


def _phone_charger_plan(selected=("assist_phone", "assist_wireless_charger")):
    return _plan(selected, [
        {"type": "PICK", "object_id": "assist_phone"},
        {"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"},
    ])


def _free_phone_charger_plan():
    return _plan(("free_object_0", "free_object_1"), [
        {"type": "PICK", "object_id": "free_object_0"},
        {"type": "PLACE_ON", "object_id": "free_object_0", "target_id": "free_object_1"},
    ])


class SemanticLanguageBridgeTests(unittest.TestCase):
    def setUp(self):
        self.scene = json.loads(SCENE_PATH.read_text(encoding="utf-8"))

    def test_chinese_and_english_aliases_ground_to_the_same_stable_ids(self):
        client = FakeCompletionClient(_phone_charger_plan(), _phone_charger_plan())
        bridge = SemanticLanguageBridge(SemanticPlanner(client, "fake-model"))

        chinese = bridge.plan_nouns(self.scene, ["手机", "无线充电座"])
        english = bridge.plan_nouns(self.scene, ["smartphone", "wireless charging pad"])

        self.assertEqual(chinese["status"], "executable")
        self.assertEqual(english["status"], "executable")
        self.assertEqual([x["object_id"] for x in chinese["grounded_objects"]], ["assist_phone", "assist_wireless_charger"])
        self.assertEqual([x["object_id"] for x in english["grounded_objects"]], ["assist_phone", "assist_wireless_charger"])
        self.assertIn("手机", chinese["vla_instruction_zh"])
        self.assertIn("wireless charging pad", chinese["vla_instruction_en"])
        self.assertFalse(chinese["dispatch_allowed"])

    def test_closed_storage_plan_validates_ordered_open_pick_place_close(self):
        selected = ["assist_medicine_box", "assist_storage_box"]
        response = _plan(selected, [
            {"type": "OPEN", "object_id": "assist_storage_box"},
            {"type": "PICK", "object_id": "assist_medicine_box"},
            {"type": "PLACE_IN", "object_id": "assist_medicine_box", "target_id": "assist_storage_box"},
            {"type": "CLOSE", "object_id": "assist_storage_box"},
        ])
        client = FakeCompletionClient(response)
        bridge = SemanticLanguageBridge(SemanticPlanner(client, "fake-model"))

        result = bridge.plan_nouns(
            self.scene, ["小药盒", "收纳盒"],
            intent_context="Put the selected small medicine box into the selected storage box.",
            current_states={"assist_storage_box": {"lid": "closed"}},
        )

        self.assertEqual(result["status"], "executable")
        self.assertEqual([item["type"] for item in result["actions"]], ["OPEN", "PICK", "PLACE_IN", "CLOSE"])
        self.assertTrue(result["validation"]["valid"])

    def test_synonymous_labels_for_one_scene_object_are_coalesced(self):
        response = _plan(["assist_button_switch"], [
            {"type": "PRESS", "object_id": "assist_button_switch"},
        ])
        client = FakeCompletionClient(response)
        result = SemanticLanguageBridge(SemanticPlanner(client, "fake-model")).plan_nouns(
            self.scene, ["按钮", "开关"], intent_context="Press the selected button switch."
        )

        self.assertEqual(result["status"], "executable")
        self.assertEqual([item["object_id"] for item in result["grounded_objects"]], ["assist_button_switch", "assist_button_switch"])
        self.assertEqual(result["actions"], [{"type": "PRESS", "object_id": "assist_button_switch"}])
        self.assertTrue(result["validation"]["valid"])

    def test_strict_mode_accepts_one_noun_for_a_unary_invalid_action_request(self):
        response = {
            "status": "invalid",
            "intent_summary": "The selected object cannot be pressed.",
            "selected_objects": ["assist_medicine_box"],
            "actions": [],
            "natural_language_instruction": "",
            "assumptions": [],
            "ambiguity_reason": "The selected medicine box is not pressable.",
            "alternatives": [],
        }
        client = FakeCompletionClient(response)
        result = SemanticLanguageBridge(SemanticPlanner(client, "fake-model")).plan_nouns(
            self.scene, ["medicine box"], intent_context="Press the selected medicine box."
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["actions"], [])
        self.assertTrue(result["validation"]["valid"])

    def test_cli_parses_known_container_state_for_grounded_storage_plan(self):
        states = _parse_current_states('{"assist_storage_box":{"lid":"closed"}}')
        self.assertEqual(states, {"assist_storage_box": {"lid": "closed"}})

    def test_cli_rejects_malformed_current_state_json(self):
        with self.assertRaises(ValueError):
            _parse_current_states('["assist_storage_box"]')

    def test_unknown_strict_noun_fails_without_model_call(self):
        client = FakeCompletionClient()
        result = SemanticLanguageBridge(SemanticPlanner(client, "fake-model")).plan_nouns(
            self.scene, ["a never cataloged gadget", "无线充电座"], mode="strict"
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "unknown_object_in_strict_scene")
        self.assertEqual(client.messages, [])

    def test_free_noun_mode_labels_inferred_objects_unverified_and_never_dispatches(self):
        inference = {"objects": [
            {"object_id": "free_object_0", "object_type": "smartphone", "affordance_tags": ["movable"]},
            {"object_id": "free_object_1", "object_type": "wireless_charging_pad", "affordance_tags": ["fixed_surface", "charging_target"]},
        ]}
        client = FakeCompletionClient(inference, _free_phone_charger_plan())
        result = SemanticLanguageBridge(SemanticPlanner(client, "fake-model")).plan_nouns(
            self.scene, ["wrist communicator", "magnetic power pad"], mode="free"
        )

        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["mode"], "free_noun_unverified")
        self.assertEqual(result["free_noun_inference"]["trust"], "model_inferred_unverified")
        self.assertTrue(all(item["trust"] == "model_inferred_unverified" for item in result["grounded_objects"]))
        self.assertEqual(result["token_usage"]["total_tokens"], 30)
        self.assertFalse(result["dispatch_allowed"])
        self.assertNotIn("model-inferred", result["vla_instruction_en"])

    def test_free_affordance_inference_gets_one_schema_correction_retry(self):
        invalid = {"objects": [
            {"object_id": "free_object_0", "object_type": "smartphone", "affordance_tags": ["movable", "teleportable"]},
            {"object_id": "free_object_1", "object_type": "wireless_charging_pad", "affordance_tags": ["fixed_surface"]},
        ]}
        valid = {"objects": [
            {"object_id": "free_object_0", "object_type": "smartphone", "affordance_tags": ["movable"]},
            {"object_id": "free_object_1", "object_type": "wireless_charging_pad", "affordance_tags": ["fixed_surface"]},
        ]}
        client = FakeCompletionClient(invalid, valid, _free_phone_charger_plan())
        result = SemanticLanguageBridge(SemanticPlanner(client, "fake-model")).plan_nouns(
            self.scene, ["unknown source", "unknown target"], mode="free"
        )

        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["free_noun_inference"]["inference_attempt_count"], 2)
        self.assertEqual(len(client.messages), 3)
        self.assertIn("correction", json.loads(client.messages[1][1]["content"]))

    def test_duplicate_actions_fail_closed(self):
        duplicate = _plan(("assist_phone", "assist_wireless_charger"), [
            {"type": "PICK", "object_id": "assist_phone"},
            {"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"},
            {"type": "PLACE_ON", "object_id": "assist_phone", "target_id": "assist_wireless_charger"},
        ])
        client = FakeCompletionClient(duplicate, duplicate)
        result = SemanticLanguageBridge(SemanticPlanner(client, "fake-model")).plan_nouns(
            self.scene, ["手机", "无线充电座"]
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["actions"], [])
        self.assertFalse(result["dispatch_allowed"])


if __name__ == "__main__":
    unittest.main()
