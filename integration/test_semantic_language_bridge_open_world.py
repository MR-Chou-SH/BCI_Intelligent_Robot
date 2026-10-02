"""Offline contracts for the M31 open-world language bridge."""

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from integration import semantic_bridge_cli
from integration.semantic_language_bridge import SemanticLanguageBridge, parse_object_descriptor


def _intent(selected_ids, *, status="executable", relation="PLACE_IN", source=None,
            target=None, zh="把药盒放进收纳盒里。", en="Put the medicine box into the storage box.",
            steps=None, referenced=None, custom_relation=None):
    source = source or selected_ids[0]
    target = target or selected_ids[1]
    return {
        "status": status,
        "selected_object_ids": list(selected_ids),
        "referenced_object_ids": list(referenced if referenced is not None else selected_ids),
        "source_object_id": source,
        "target_or_tool_object_id": target,
        "relation_type": relation,
        "custom_relation": custom_relation,
        "high_level_action": "place" if status == "executable" else "none",
        "ordered_high_level_steps": steps if steps is not None else ([
            {"action": relation, "object_id": source, "target_object_id": target}
        ] if status == "executable" else []),
        "assumptions": [],
        "ambiguity_reason": "Several relations are plausible." if status == "ambiguous" else "",
        "vla_instruction_zh": zh if status == "executable" else "",
        "vla_instruction_en": en if status == "executable" else "",
    }


class FakeCompletionClient:
    base_url = "https://api.example.invalid"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        self.calls.append({"messages": messages, "model_id": model_id,
                           "temperature": temperature, "max_output_tokens": max_output_tokens})
        if not self.responses:
            raise AssertionError("unexpected completion call")
        return {"content": json.dumps(self.responses.pop(0), ensure_ascii=False),
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


class SemanticLanguageBridgeOpenWorldTests(unittest.TestCase):
    def test_descriptor_parser_keeps_only_explicit_color_and_state(self):
        plain = parse_object_descriptor("苹果", object_id="a")
        red = parse_object_descriptor("红色苹果", object_id="b")
        closed = parse_object_descriptor("收纳盒（关闭状态）", object_id="c")
        occupied = parse_object_descriptor("occupied charger", object_id="d")

        self.assertIsNone(plain["color"])
        self.assertEqual(red["color"], "red")
        self.assertEqual(red["object_name"], "苹果")
        self.assertEqual(closed["current_state"]["container_state"], "closed")
        self.assertIs(closed["current_state"]["is_open"], False)
        self.assertEqual(occupied["current_state"]["occupancy"], "occupied")

    def test_open_world_pair_is_not_scene_catalog_rejected_and_never_dispatches(self):
        response = _intent(["selected_0", "selected_1"])
        client = FakeCompletionClient(response)
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(["药盒", "收纳盒"])

        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["relation_type"], "PLACE_IN")
        self.assertEqual(result["mode"], "semantic_open_world")
        self.assertFalse(result["dispatch_allowed"])
        self.assertEqual(result["provenance"]["attempts"], 1)

    def test_short_natural_action_label_and_null_nonambiguous_reason_are_normalized(self):
        response = _intent(["selected_0", "selected_1"], relation="PLACE_IN")
        response["high_level_action"] = "place the medicine box into the storage box"
        response["ambiguity_reason"] = None
        response["ordered_high_level_steps"][0]["action"] = "place"
        result = SemanticLanguageBridge(FakeCompletionClient(response), "fake-model").generate_instruction(
            ["药盒", "收纳盒"]
        )
        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["ambiguity_reason"], "")
        self.assertEqual(result["provenance"]["attempts"], 1)

    def test_reversed_input_order_is_preserved_but_semantic_roles_can_reverse(self):
        response = _intent(["selected_0", "selected_1"], source="selected_1", target="selected_0",
                           relation="CUT_WITH", zh="抓取苹果，并使用水果刀将苹果切开。",
                           en="Pick up the apple and cut it with the fruit knife.",
                           steps=[{"action": "CUT_WITH", "object_id": "selected_1", "target_object_id": "selected_0"}])
        result = SemanticLanguageBridge(FakeCompletionClient(response), "fake-model").generate_instruction(
            ["水果刀", "苹果"]
        )

        self.assertEqual(result["selected_object_ids"], ["selected_0", "selected_1"])
        self.assertEqual(result["source_object_id"], "selected_1")
        self.assertEqual(result["relation_type"], "CUT_WITH")

    def test_explicit_color_must_be_preserved_bilingually(self):
        response = _intent(["selected_0", "selected_1"], relation="CUT_WITH",
                           source="selected_0", target="selected_1",
                           zh="抓取红色苹果，并使用水果刀将它切开。",
                           en="Pick up the red apple and cut it with the fruit knife.",
                           steps=[{"action": "CUT_WITH", "object_id": "selected_0", "target_object_id": "selected_1"}])
        client = FakeCompletionClient(response)
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(["红色苹果", "水果刀"])

        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["selected_objects"][0]["color"], "red")

    def test_unobserved_color_claim_is_rejected(self):
        response = _intent(["selected_0", "selected_1"], relation="CUT_WITH",
                           source="selected_0", target="selected_1",
                           zh="抓取红色苹果，并使用水果刀将它切开。",
                           en="Pick up the red apple and cut it with the fruit knife.",
                           steps=[{"action": "CUT_WITH", "object_id": "selected_0", "target_object_id": "selected_1"}])
        corrected = _intent(["selected_0", "selected_1"], relation="CUT_WITH",
                            source="selected_0", target="selected_1",
                            zh="抓取苹果，并使用水果刀将它切开。",
                            en="Pick up the apple and cut it with the fruit knife.",
                            steps=[{"action": "CUT_WITH", "object_id": "selected_0", "target_object_id": "selected_1"}])
        client = FakeCompletionClient(response, corrected)
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(["苹果", "水果刀"])

        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["provenance"]["attempts"], 2)
        correction = json.loads(client.calls[1]["messages"][1]["content"])
        self.assertIn("unobserved_color_claim:red", correction["correction"]["validation_errors"])

    def test_explicitly_closed_container_allows_high_level_opening_sequence(self):
        response = _intent(["selected_0", "selected_1"], relation="PLACE_IN",
                           source="selected_0", target="selected_1",
                           zh="打开收纳盒，把药盒放进去，然后关闭收纳盒。",
                           en="Open the storage box, place the medicine box inside, then close the box.",
                           steps=[{"action": "OPEN", "object_id": "selected_1"},
                                  {"action": "PLACE_IN", "object_id": "selected_0", "target_object_id": "selected_1"},
                                  {"action": "CLOSE", "object_id": "selected_1"}])
        result = SemanticLanguageBridge(FakeCompletionClient(response), "fake-model").generate_instruction(
            ["药盒", "收纳盒（关闭状态）"]
        )

        self.assertEqual(result["status"], "executable")
        self.assertEqual([step["action"] for step in result["ordered_high_level_steps"]], ["OPEN", "PLACE_IN", "CLOSE"])

    def test_unknown_container_state_does_not_prevent_simple_storage_language(self):
        response = _intent(["selected_0", "selected_1"])
        result = SemanticLanguageBridge(FakeCompletionClient(response), "fake-model").generate_instruction(
            ["药盒", "收纳盒"]
        )
        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["selected_objects"][1]["current_state"], {})

    def test_open_close_step_without_observed_state_or_request_is_corrected(self):
        bad = _intent(["selected_0", "selected_1"], relation="PLACE_IN",
                      zh="打开收纳盒，把药盒放进去。", en="Open the storage box and put the medicine box inside.",
                      steps=[{"action": "OPEN", "object_id": "selected_1"},
                             {"action": "PLACE_IN", "object_id": "selected_0", "target_object_id": "selected_1"}])
        good = _intent(["selected_0", "selected_1"])
        client = FakeCompletionClient(bad, good)
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(["药盒", "收纳盒"])
        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["provenance"]["attempts"], 2)

    def test_scene_context_object_is_allowed_but_hallucinated_id_gets_corrected(self):
        contextual = _intent(["selected_0", "selected_1"], referenced=["selected_0", "selected_1", "context_0"])
        contextual["ordered_high_level_steps"][0]["target_object_id"] = "context_0"
        contextual["target_or_tool_object_id"] = "context_0"
        accepted = SemanticLanguageBridge(FakeCompletionClient(contextual), "fake-model").generate_instruction(
            ["药盒", "收纳盒"], scene_context=["柜子"]
        )
        self.assertEqual(accepted["status"], "executable")
        self.assertEqual(accepted["provenance"]["attempts"], 1)

        bad = _intent(["selected_0", "selected_1"], referenced=["selected_0", "selected_1", "context_9"])
        bad["ordered_high_level_steps"][0]["target_object_id"] = "context_9"
        bad["target_or_tool_object_id"] = "context_9"
        good = _intent(["selected_0", "selected_1"])
        result = SemanticLanguageBridge(FakeCompletionClient(bad, good), "fake-model").generate_instruction(
            ["药盒", "收纳盒"], scene_context=["柜子"]
        )
        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["provenance"]["attempts"], 2)

    def test_executable_intent_must_use_every_selected_object(self):
        bad = _intent(["selected_0", "selected_1"], referenced=["selected_0"])
        bad["source_object_id"] = "selected_0"
        bad["target_or_tool_object_id"] = "selected_0"
        bad["ordered_high_level_steps"] = [{"action": "PICK", "object_id": "selected_0"}]
        good = _intent(["selected_0", "selected_1"])
        client = FakeCompletionClient(bad, good)
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(["药盒", "收纳盒"])
        self.assertEqual(result["status"], "executable")
        self.assertEqual(result["provenance"]["attempts"], 2)
        self.assertIn("executable_intent_ignores_selected_object",
                      client.calls[1]["messages"][1]["content"])

    def test_known_occupied_charger_cannot_be_returned_as_executable(self):
        bad = _intent(["selected_0", "selected_1"], relation="CHARGE_WITH",
                      source="selected_1", target="selected_0",
                      zh="把手机放在充电器上充电。", en="Place the phone on the charger to charge it.",
                      steps=[{"action": "CHARGE", "object_id": "selected_1", "target_object_id": "selected_0"}])
        invalid = _intent(["selected_0", "selected_1"], status="invalid", relation="NONE")
        invalid["source_object_id"] = None
        invalid["target_or_tool_object_id"] = None
        invalid["referenced_object_ids"] = []
        client = FakeCompletionClient(bad, invalid)
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(
            ["occupied charger", "phone"]
        )
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["relation_type"], "NONE")

    def test_ambiguous_and_invalid_results_must_not_commit_instructions(self):
        ambiguous = _intent(["selected_0", "selected_1"], status="ambiguous", relation="NONE",
                            source=None, target=None)
        invalid = _intent(["selected_0", "selected_1"], status="invalid", relation="NONE",
                          source=None, target=None)
        ambiguous["ambiguity_reason"] = "Both objects could be stored together in several ways."
        results = [SemanticLanguageBridge(FakeCompletionClient(value), "fake-model").generate_instruction(
            ["杯子", "桌子"]
        ) for value in (ambiguous, invalid)]
        self.assertEqual([item["status"] for item in results], ["ambiguous", "invalid"])
        self.assertTrue(all(not item["ordered_high_level_steps"] for item in results))

    def test_custom_world_relation_is_supported_without_robot_skill_mapping(self):
        response = _intent(["selected_0", "selected_1"], relation="CUSTOM", custom_relation="thread_through",
                           source="selected_0", target="selected_1", zh="将绳子穿过扣环。",
                           en="Thread the cord through the eyelet.",
                           steps=[{"action": "CUSTOM", "object_id": "selected_0", "target_object_id": "selected_1"}])
        result = SemanticLanguageBridge(FakeCompletionClient(response), "fake-model").generate_instruction(["绳子", "扣环"])
        self.assertEqual(result["relation_type"], "CUSTOM")
        self.assertEqual(result["custom_relation"], "thread_through")

    def test_low_level_motion_fields_and_unknown_references_fail_closed(self):
        response = _intent(["selected_0", "selected_1"])
        response["ordered_high_level_steps"][0]["pose_xyz"] = [0.0, 0.0, 0.0]
        result = SemanticLanguageBridge(FakeCompletionClient(response, response), "fake-model").generate_instruction(
            ["药盒", "收纳盒"]
        )
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "invalid_model_output_after_bounded_retry")
        self.assertFalse(result["dispatch_allowed"])

    def test_bad_input_fails_without_calling_model(self):
        client = FakeCompletionClient()
        result = SemanticLanguageBridge(client, "fake-model").generate_instruction(["only one object"])
        self.assertEqual(result["reason_code"], "invalid_input")
        self.assertEqual(client.calls, [])
        self.assertFalse(result["dispatch_allowed"])

    def test_cli_defaults_to_open_world_and_does_not_require_a_scene_file(self):
        client = FakeCompletionClient(_intent(["selected_0", "selected_1"]))
        output = StringIO()
        with patch.object(semantic_bridge_cli, "build_live_client", return_value=(client, "fake-model", [], False)):
            with redirect_stdout(output):
                exit_code = semantic_bridge_cli.main(["药盒", "收纳盒"])

        self.assertEqual(exit_code, 0)
        self.assertIn("[Mode] semantic_open_world", output.getvalue())
        self.assertIn("[Semantic Relation] PLACE_IN", output.getvalue())
        self.assertIn("[Scene Grounding] not required", output.getvalue())
        self.assertIn("[Dispatch] disabled", output.getvalue())
        self.assertEqual(len(client.calls), 1)

    def test_cli_treats_semantic_invalid_as_a_successful_classification(self):
        invalid = _intent(["selected_0", "selected_1"], status="invalid", relation="NONE")
        invalid["source_object_id"] = None
        invalid["target_or_tool_object_id"] = None
        invalid["referenced_object_ids"] = []
        client = FakeCompletionClient(invalid)
        output = StringIO()
        with patch.object(semantic_bridge_cli, "build_live_client", return_value=(client, "fake-model", [], False)):
            with redirect_stdout(output):
                exit_code = semantic_bridge_cli.main(["--intent", "Use a book to charge the battery.", "书", "电池"])

        self.assertEqual(exit_code, 0)
        self.assertIn("[Status] INVALID", output.getvalue())
        self.assertIn("[Dispatch] disabled", output.getvalue())


if __name__ == "__main__":
    unittest.main()
