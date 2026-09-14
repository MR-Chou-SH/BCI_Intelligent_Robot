"""Software-only checks for the shared Unity virtual-block/M9 identity catalog."""

import json
from pathlib import Path
import re
import unittest

from integration.m9_robot_adapter import (
    CONFIRMED_BATCH_PROVENANCE,
    FROZEN_SELECTION_PROVENANCE,
    confirmed_batch_to_robot_requests,
)
from integration.m9_mujoco_execution import DEFAULT_M9_SCENE_BINDINGS, SceneBindingRegistry
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
UNITY_CATALOG_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "Resources"
    / "BCI"
    / "M9"
    / "virtual_blocks.json"
)
UNITY_SCENE_PATH = (
    REPOSITORY_ROOT
    / "m7_unity6000"
    / "Assets"
    / "PassthroughCameraApiSamples"
    / "MultiObjectDetection"
    / "M9VirtualManipulation.unity"
)
UNITY_BUILD_SETTINGS_PATH = REPOSITORY_ROOT / "m7_unity6000" / "ProjectSettings" / "EditorBuildSettings.asset"


def _load_catalog():
    with UNITY_CATALOG_PATH.open("r", encoding="utf-8") as source:
        return json.load(source)


class M9VirtualBlockContractTests(unittest.TestCase):
    def test_catalog_has_explicit_unique_virtual_id_mapping_and_fixed_slots(self):
        catalog = _load_catalog()
        self.assertEqual(1, catalog["schemaVersion"])
        self.assertEqual(
            [(0, 7.2, 5), (1, 9.0, 4), (2, 12.0, 3)],
            [
                (slot["slotIndex"], slot["nominalFrequencyHz"], slot["framesPerHalfCycle"])
                for slot in catalog["slots"]
            ],
        )
        self.assertEqual(4, len(catalog["blocks"]))

        target_ids = [block["targetId"] for block in catalog["blocks"]]
        logical_ids = [block["logicalBlockId"] for block in catalog["blocks"]]
        self.assertEqual(len(target_ids), len(set(target_ids)))
        self.assertEqual(len(logical_ids), len(set(logical_ids)))
        for block in catalog["blocks"]:
            self.assertEqual("virtual_block", block["sourceKind"])
            self.assertTrue(block["active"])
            self.assertTrue(block["selectable"])
            self.assertTrue(block["slotEligible"])
            self.assertTrue(block["targetId"].startswith("m9-vblock-"))
            self.assertFalse(block["targetId"].startswith("obj_"))
            self.assertTrue(block["logicalBlockId"].startswith("block_"))
            self.assertFalse(block["logicalBlockId"].startswith("obj_"))

    def test_synthetic_frozen_class_batch_maps_only_through_explicit_catalog(self):
        catalog = _load_catalog()
        blocks = sorted(
            (
                block
                for block in catalog["blocks"]
                if block["active"] and block["selectable"] and block["slotEligible"]
            ),
            key=lambda block: (block["localPositionMeters"]["x"], block["targetId"]),
        )
        self.assertGreaterEqual(len(blocks), 3)
        target_mapping = load_virtual_block_target_mapping()
        self.assertEqual(
            {block["targetId"]: block["logicalBlockId"] for block in catalog["blocks"]},
            target_mapping,
        )
        selections = [
            {
                "selectionId": "m9-virtual-test-selection-{}".format(slot),
                "predictedClassIndex": slot,
                "slotIndex": slot,
                "targetId": block["targetId"],
                "semanticLabel": block["semanticLabel"],
                "hasWorldPosition": True,
                "worldPosition": block["localPositionMeters"],
                "resolvedUtc": "2026-09-14T00:00:00Z",
                "provenance": FROZEN_SELECTION_PROVENANCE,
            }
            for slot, block in enumerate(blocks[:3])
        ]
        message = {
            "protocolVersion": 1,
            "messageType": "target_batch_confirmed",
            "batchId": "m9-virtual-test-batch-001",
            "confirmedBatch": {
                "batchId": "m9-virtual-test-batch-001",
                "groupId": "m8-group-virtual-test",
                "groupIndex": 1,
                "submittedUtc": "2026-09-14T00:00:01Z",
                "provenance": CONFIRMED_BATCH_PROVENANCE,
                "selections": selections,
            },
        }

        requests = confirmed_batch_to_robot_requests(message, target_mapping)
        self.assertEqual(3, len(requests))
        self.assertEqual(
            tuple(block["logicalBlockId"] for block in blocks[:3]),
            tuple(request.logical_block_id for request in requests),
        )
        self.assertEqual(
            tuple(block["targetId"] for block in blocks[:3]),
            tuple(request.source_target_id for request in requests),
        )
        self.assertEqual((0, 1, 2), tuple(request.slot_index for request in requests))
        self.assertTrue(
            all(request.selection_provenance == FROZEN_SELECTION_PROVENANCE for request in requests)
        )
        self.assertTrue(
            all(request.batch_provenance == CONFIRMED_BATCH_PROVENANCE for request in requests)
        )

        scene_bindings = SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS)
        self.assertEqual(set(DEFAULT_M9_SCENE_BINDINGS), set(target_mapping.values()))
        self.assertEqual(
            ("obj_0", "obj_1", "obj_2"),
            tuple(
                scene_bindings.resolve_binding(request.logical_block_id).simulator_object_name
                for request in requests
            ),
        )

        with self.assertRaisesRegex(ValueError, "not registered"):
            confirmed_batch_to_robot_requests(message, {"different-target": "block_red_01"})

    def test_dedicated_default_scene_keeps_real_target_scene_and_disables_real_detector_roots(self):
        scene = UNITY_SCENE_PATH.read_text(encoding="utf-8")
        settings = UNITY_BUILD_SETTINGS_PATH.read_text(encoding="utf-8")
        self.assertIn("guid: 03b7794c49a34c82bc0358c66c3da451", scene)
        self.assertIn("m_Name: M9VirtualManipulationBootstrap", scene)
        self.assertIn("- {fileID: 905041112}", scene.split("SceneRoots:", 1)[1])
        for prefab_instance, target_id, prefab_guid in (
            ("1721288808", "5464317018167149569", "749c2b64d9125458da0686ace4bec00b"),
            ("1019656467722773290", "8722207748505512710", "259c0c899b7e0c14aa431eefdc0aacc6"),
            ("7042133910910641334", "7042133912099342345", "946c4bcf5ee78c2438bd71dd2f9fe1f9"),
            ("7177215558053442464", "5228241647023865434", "927c1b782bb796b4b9b433d533cbccca"),
            ("1373946500", "7847449403238870662", "abfaad578d05e4a09838223134c41c46"),
        ):
            marker = "--- !u!1001 &{}".format(prefab_instance)
            section = scene.split(marker, 1)[1].split("--- !u!", 1)[0]
            self.assertIn(
                "target: {{fileID: {}, guid: {}, type: 3}}".format(target_id, prefab_guid),
                section,
            )
            self.assertRegex(section, r"propertyPath: m_IsActive\s+value: 0")

        scene_entries = [
            (enabled, path)
            for enabled, path in re.findall(r"- enabled: ([01])\s+path: ([^\r\n]+)", settings)
        ]
        self.assertEqual(
            ("1", "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity"),
            scene_entries[0],
        )
        self.assertIn(
            ("1", "Assets/PassthroughCameraApiSamples/MultiObjectDetection/MultiObjectDetection.unity"),
            scene_entries,
        )


if __name__ == "__main__":
    unittest.main()
