from __future__ import annotations

import json
import unittest

from integration.semantic_intelligence import SemanticSceneCore
from integration.text_scene_parser import TextSceneParser


class FakeCompletionClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        self.calls.append(messages)
        return {"content": json.dumps(self.payload, ensure_ascii=False), "usage": {}}


class TextSceneParserPublicInterfaceTests(unittest.TestCase):
    def test_parse_keeps_only_text_grounded_objects_and_explicit_facts(self):
        source = "厨房里有红色苹果、关闭的收纳盒、垃圾桶和榨汁机。"
        client = FakeCompletionClient({
            "scene_type": "厨房",
            "scene_summary": "厨房里还存在水果刀和其他未提及物品。",
            "objects": [
                {
                    "mention": "厨房", "display_name": "厨房", "object_type": "room",
                    "selectable": False, "aliases": [], "affordance_tags": [], "facts": [],
                },
                {
                    "mention": "红色苹果", "display_name": "红色苹果", "object_type": "apple",
                    "selectable": True, "aliases": ["苹果", "apple"], "affordance_tags": ["food", "cuttable"],
                    "facts": [{"kind": "color", "value": "red", "evidence": "红色苹果"}],
                },
                {
                    "mention": "关闭的收纳盒", "display_name": "收纳盒", "object_type": "storage_box",
                    "selectable": True, "aliases": ["盒子", "box"], "affordance_tags": ["container", "openable"],
                    "facts": [
                        {"kind": "state", "value": "closed", "evidence": "关闭的收纳盒"},
                        {"kind": "color", "value": "red", "evidence": "红色苹果"},
                    ],
                },
                {
                    "mention": "水果刀", "display_name": "水果刀", "object_type": "knife",
                    "selectable": True, "aliases": ["knife"], "affordance_tags": ["knife", "cutting_tool"], "facts": [],
                },
                {
                    "mention": "垃圾桶", "display_name": "垃圾桶", "object_type": "trash_bin",
                    "selectable": True, "aliases": ["trash bin"], "affordance_tags": ["container"],
                    "facts": [{"kind": "state", "value": "closed", "evidence": "关闭的收纳盒、垃圾桶"}],
                },
            ],
        })

        parsed = TextSceneParser(client, "test-model").parse(source)

        scene = parsed["scene"]
        self.assertEqual(scene["schema_version"], "m27-semantic-scene-v1")
        self.assertEqual([obj["id"] for obj in scene["objects"]], ["obj_001", "obj_002", "obj_003", "obj_004"])
        self.assertFalse(scene["objects"][0]["selectable"])
        self.assertEqual(scene["candidate_order"], ["obj_002", "obj_003", "obj_004"])
        self.assertEqual(scene["objects"][1]["color"], "red")
        self.assertEqual(scene["objects"][1]["observed_facts"]["color"]["evidence"], "红色苹果")
        self.assertNotIn("color", scene["objects"][2])
        self.assertEqual(scene["objects"][2]["current_state"], {"lid": "closed"})
        self.assertNotIn("current_state", scene["objects"][3])
        self.assertNotIn("水果刀", scene["scene_summary"])
        self.assertEqual(parsed["diagnostics"]["rejected_object_count"], 1)
        self.assertEqual(parsed["diagnostics"]["rejected_fact_count"], 2)
        self.assertEqual(len(SemanticSceneCore(scene).objects), 4)
        self.assertIn("Do not invent", client.calls[0][0]["content"])
        model_input = json.loads(client.calls[0][1]["content"])
        self.assertEqual(set(model_input), {"schema_version", "scene_text", "correction"})
        self.assertEqual(model_input["scene_text"], source)
        self.assertNotIn("acceptable_next_targets", json.dumps(model_input, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
