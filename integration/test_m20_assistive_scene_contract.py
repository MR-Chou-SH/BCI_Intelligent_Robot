from __future__ import annotations

import unittest
from pathlib import Path

from integration.m20_assistive_scene_contract import (
    EXPECTED_CANDIDATE_ORDER,
    DEFAULT_SPEC_PATH,
    M20AssistiveSceneError,
    ROOT,
    check_mujoco_scene,
    build_mujoco_scene,
    load_spec,
    validate_spec,
)


class M20AssistiveSceneContractTests(unittest.TestCase):
    def test_canonical_layout_and_frozen_slot_mapping(self) -> None:
        spec, fingerprint = load_spec(DEFAULT_SPEC_PATH)
        report = validate_spec(spec)

        self.assertEqual(len(fingerprint), 64)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(tuple(report["candidateOrder"]), EXPECTED_CANDIDATE_ORDER)
        self.assertEqual(report["candidatePages"], [list(EXPECTED_CANDIDATE_ORDER[:3]), list(EXPECTED_CANDIDATE_ORDER[3:])])
        self.assertEqual(
            [(item["slotIndex"], item["frequencyHz"], item["framesPerHalfCycle"]) for item in report["slotMapping"]],
            [(0, 7.2, 5), (1, 9.0, 4), (2, 12.0, 3)],
        )
        self.assertTrue(all(report["mirrorChecks"].values()))
        self.assertTrue(
            all(value for key, value in report["placementChecks"].items() if key != "placementPoses")
        )
        self.assertEqual(
            {(item["sourceId"], item["targetId"]) for item in report["placementChecks"]["placementPoses"]},
            {
                ("assist_medicine_box", "assist_user_zone"),
                ("assist_phone", "assist_user_zone"),
                ("assist_phone", "assist_wireless_charger"),
            },
        )
        self.assertTrue(all(item["restsOnTarget"] for item in report["placementChecks"]["placementPoses"]))

        invalid_spec = {**spec, "entities": [dict(item) for item in spec["entities"]]}
        phone = next(item for item in invalid_spec["entities"] if item["semanticId"] == "assist_phone")
        phone["placements"] = [dict(item) for item in phone["placements"]]
        charger_placement = next(
            item for item in phone["placements"] if item["targetId"] == "assist_wireless_charger"
        )
        charger_placement["positionMeters"] = {**charger_placement["positionMeters"], "z": 0.004}
        with self.assertRaisesRegex(M20AssistiveSceneError, "must rest on its top surface"):
            validate_spec(invalid_spec)

    def test_new_unity_scene_selects_m20_paged_profile_and_preserves_m9_entry(self) -> None:
        unity_root = ROOT / "m7_unity6000"
        legacy_scene = unity_root / "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity"
        m20_scene = unity_root / "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M20DailyAssistiveDesk.unity"
        build_settings = unity_root / "ProjectSettings/EditorBuildSettings.asset"
        legacy_text = legacy_scene.read_text(encoding="utf-8")
        m20_text = m20_scene.read_text(encoding="utf-8")
        build_text = build_settings.read_text(encoding="utf-8")

        self.assertIn("m_selectionInteractionMode: 2", legacy_text)
        self.assertNotIn("m_useM20AssistiveDeskProfile: 1", legacy_text)
        self.assertIn("m_selectionInteractionMode: 1", m20_text)
        self.assertIn("m_useM20AssistiveDeskProfile: 1", m20_text)
        self.assertIn("path: Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity\n    guid:", build_text)
        self.assertIn("path: Assets/PassthroughCameraApiSamples/MultiObjectDetection/M20DailyAssistiveDesk.unity\n", build_text)
        m20_entry = build_text.split("path: Assets/PassthroughCameraApiSamples/MultiObjectDetection/M20DailyAssistiveDesk.unity\n", 1)[0]
        self.assertIn("- enabled: 0\n", m20_entry[-24:])

    def test_compiled_mujoco_scene_matches_shared_entities_and_articulations(self) -> None:
        spec, _ = load_spec(DEFAULT_SPEC_PATH)
        model, data = build_mujoco_scene(spec)
        report = check_mujoco_scene(spec, model, data)

        self.assertEqual(report["status"], "PASS")
        self.assertLessEqual(max(report["table"]["positionErrorMeters"]), 0.005)
        self.assertLessEqual(max(report["table"]["dimensionErrorMeters"]), 0.005)
        self.assertLessEqual(max(report["robotBase"]["positionErrorMeters"]), 0.005)
        self.assertEqual(
            [item["semanticId"] for item in report["candidateGeometry"]],
            list(EXPECTED_CANDIDATE_ORDER),
        )
        self.assertEqual(report["initialM20ContactInterpenetrations"], [])
        self.assertEqual(
            {(item["sourceId"], item["targetId"]) for item in report["placementPoses"]},
            {
                ("assist_medicine_box", "assist_user_zone"),
                ("assist_phone", "assist_user_zone"),
                ("assist_phone", "assist_wireless_charger"),
            },
        )
        self.assertTrue(all(item["contactInterpenetrations"] == [] for item in report["placementPoses"]))
        self.assertTrue(all(abs(item["verticalGapMeters"]) <= 1e-6 for item in report["placementPoses"]))
        self.assertEqual(
            [item["sourceId"] for item in report["m9GraspReachability"]],
            ["assist_medicine_box", "assist_phone"],
        )
        self.assertTrue(all(item["ikReachable"] for item in report["m9GraspReachability"]))
        self.assertEqual(report["storageLid"]["openPoseContactInterpenetrations"], [])
        self.assertTrue(report["storageLid"]["clearsTableAndBase"])
        self.assertAlmostEqual(report["buttonCap"]["travelMeters"], 0.005, places=6)


if __name__ == "__main__":
    unittest.main()
