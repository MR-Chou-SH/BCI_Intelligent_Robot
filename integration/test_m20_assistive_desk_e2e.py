"""Focused software regressions for the M20 synthetic full-chain adapter."""

import unittest

from integration.m20_assistive_desk_e2e import (
    M20ActionResolutionError,
    candidates_from_spec,
    context_affordance_evidence,
    resolve_source_destination,
    selection_regression,
)
from integration.m16_paged_queue import PagedSelectionQueue
from integration.m20_assistive_scene_contract import DEFAULT_SPEC_PATH, load_spec


class M20AssistiveDeskE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec, _fingerprint = load_spec(DEFAULT_SPEC_PATH)

    def test_candidate_order_and_frequency_slots(self):
        candidates = candidates_from_spec(self.spec)
        self.assertEqual(
            tuple(item.target_id for item in candidates),
            (
                "assist_medicine_box",
                "assist_storage_box",
                "assist_phone",
                "assist_button_switch",
                "assist_wireless_charger",
                "assist_user_zone",
            ),
        )
        queue = PagedSelectionQueue(candidates)
        self.assertEqual(
            tuple(item.nominal_frequency_hz for item in queue.current_page.candidates),
            (7.2, 9.0, 12.0),
        )
        self.assertTrue(queue.navigate_next())
        self.assertEqual(
            tuple(item.nominal_frequency_hz for item in queue.current_page.candidates),
            (7.2, 9.0, 12.0),
        )

    def test_navigation_undo_submit_and_stale_result_fail_closed(self):
        report = selection_regression(self.spec)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            report["submit"]["orderedTargetIds"],
            ["assist_medicine_box", "assist_user_zone"],
        )
        self.assertEqual(report["staleResult"]["rejectedReason"], "STALE_SELECTION_REJECTED")
        self.assertTrue(report["activeSlotAndTriggerRearm"]["nextTriggerAccepted"])

    def test_context_affordance_and_strong_eeg_override(self):
        report = context_affordance_evidence(self.spec)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(
            [item["targetId"] for item in report["phoneAffordancePrior"]["vector"]],
            [
                "assist_wireless_charger",
                "assist_storage_box",
                "assist_medicine_box",
                "assist_button_switch",
            ],
        )
        self.assertEqual(report["alignedFusion"]["fusion"]["finalPrediction"], "block_assist_charger_05")
        self.assertEqual(report["neutralFusion"]["fusionMode"], "context_neutral")
        self.assertEqual(report["strongEegConflict"]["fusion"]["fusionMode"], "eeg_strong_override")

    def test_action_resolver_accepts_only_allowlisted_source_destination_pairs(self):
        queue = PagedSelectionQueue(candidates_from_spec(self.spec))
        queue.select_slot(2)
        self.assertTrue(queue.navigate_next())
        queue.select_slot(1)
        committed = queue.submit()
        action = resolve_source_destination(committed.plan.ordered_selections, self.spec)
        self.assertEqual(action["sourceTargetId"], "assist_phone")
        self.assertEqual(action["destinationTargetId"], "assist_wireless_charger")

        invalid_queue = PagedSelectionQueue(candidates_from_spec(self.spec))
        invalid_queue.select_slot(2)
        self.assertTrue(invalid_queue.navigate_next())
        invalid_queue.select_slot(0)
        invalid_plan = invalid_queue.submit().plan
        with self.assertRaises(M20ActionResolutionError):
            resolve_source_destination(invalid_plan.ordered_selections, self.spec)


if __name__ == "__main__":
    unittest.main()
