import unittest

from integration.m16_paged_queue import (
    Candidate,
    PagedSelectionQueue,
    confirmed_batch_payloads,
    page_to_authoritative_snapshot,
)
from integration.m9_robot_adapter import confirmed_batch_to_robot_requests
from integration.m8_selection_orchestration import QuestSelectionTcpServer


def _candidates(count):
    return tuple(
        Candidate(
            logical_block_id="block_{}_{}".format(chr(97 + index), index + 1),
            target_id="target-{}".format(chr(97 + index)),
            label=chr(65 + index),
        )
        for index in range(count)
    )


class M16PagedQueueTests(unittest.TestCase):
    def test_one_two_three_candidate_pages_keep_local_slots(self):
        for count, expected_page_count, expected_size in ((1, 1, 1), (2, 1, 2), (3, 1, 3)):
            queue = PagedSelectionQueue(_candidates(count))
            self.assertEqual(expected_page_count, queue.page_count)
            self.assertEqual(expected_size, len(queue.current_page.candidates))
            self.assertEqual(tuple(range(expected_size)), tuple(item.slot_index for item in queue.current_page.candidates))

    def test_candidate_counts_one_through_seven_use_dynamic_page_counts(self):
        for count in range(1, 8):
            queue = PagedSelectionQueue(_candidates(count))
            self.assertEqual((count + 2) // 3, queue.page_count)
            self.assertEqual(min(count, 3), len(queue.current_page.candidates))

    def test_append_candidates_preserves_old_pages_selected_state_and_order(self):
        queue = PagedSelectionQueue(_candidates(4))
        queue.select_slot(1)
        first_page_before = queue.current_page
        appended = tuple(
            Candidate("block_extra_{}".format(index), "target-extra-{}".format(index), "Extra {}".format(index))
            for index in range(3)
        )
        self.assertTrue(queue.append_candidates(appended))
        self.assertEqual(3, queue.page_count)
        self.assertEqual(tuple(item.target_id for item in first_page_before.candidates),
                         tuple(item.target_id for item in queue.current_page.candidates))
        self.assertEqual(1, queue.current_page.page_epoch)
        self.assertTrue(queue.current_page.candidates[1].selected)
        self.assertFalse(queue.current_page.candidates[1].active)
        self.assertTrue(queue.navigate_next())
        self.assertEqual(("target-d", "target-extra-0", "target-extra-1"),
                         tuple(item.target_id for item in queue.current_page.candidates))
        self.assertTrue(queue.navigate_next())
        self.assertEqual(("target-extra-2",), tuple(item.target_id for item in queue.current_page.candidates))

    def test_dynamic_append_cancels_pending_trial_and_rejects_its_old_result(self):
        queue = PagedSelectionQueue(_candidates(3))
        trial = queue.open_trial("pending-before-append")
        self.assertTrue(queue.append_candidates((_candidates(4)[3],)))
        result = queue.accept_result(trial.selection_id, 0, "target-a", trial.page_epoch)
        self.assertFalse(result.accepted)
        self.assertEqual("STALE_SELECTION_REJECTED", result.reason)

    def test_cancel_trial_invalidates_late_result_without_changing_page(self):
        queue = PagedSelectionQueue(_candidates(4))
        trial = queue.open_trial("pending-cancel")
        self.assertTrue(queue.cancel_trial(trial.selection_id, "undo"))
        self.assertEqual(0, queue.page_index)
        self.assertIsNone(queue.current_trial)
        result = queue.accept_result(trial.selection_id, 0, "target-a", trial.page_epoch)
        self.assertFalse(result.accepted)
        self.assertEqual("STALE_SELECTION_REJECTED", result.reason)

    def test_four_candidates_are_three_plus_one_without_reselection(self):
        queue = PagedSelectionQueue(_candidates(4))
        self.assertTrue(queue.navigate_next())
        self.assertEqual(("D",), tuple(item.label for item in queue.current_page.candidates))
        queue.select_slot(0)
        self.assertEqual(("target-d",), queue.selected_target_ids)

    def test_cross_page_selection_preserves_global_order(self):
        queue = PagedSelectionQueue(_candidates(8))
        queue.navigate_next()
        queue.navigate_next()
        queue.select_slot(0)
        queue.navigate_previous()
        queue.navigate_previous()
        queue.select_slot(1)
        queue.navigate_next()
        queue.select_slot(1)

        self.assertEqual(("target-g", "target-b", "target-e"), queue.selected_target_ids)
        self.assertEqual(("G", "B", "E"), queue.selected_labels)

    def test_page_structure_and_frequency_mapping_are_stable_until_execution(self):
        queue = PagedSelectionQueue(_candidates(8))
        first = queue.current_page
        self.assertEqual(("A", "B", "C"), tuple(item.label for item in first.candidates))
        self.assertEqual((7.2, 9.0, 12.0), tuple(item.nominal_frequency_hz for item in first.candidates))

        queue.navigate_next()
        queue.navigate_next()
        queue.select_slot(0)
        queue.navigate_previous()
        queue.navigate_previous()
        self.assertEqual(("A", "B", "C"), tuple(item.label for item in queue.current_page.candidates))
        self.assertFalse(queue.current_page.candidates[1].selected)
        queue.select_slot(1)
        self.assertTrue(queue.current_page.candidates[1].selected)
        self.assertFalse(queue.current_page.candidates[1].active)

    def test_navigation_invalidates_late_trial_result(self):
        queue = PagedSelectionQueue(_candidates(4))
        trial = queue.open_trial("old-trial")
        self.assertTrue(queue.navigate_next())
        result = queue.accept_result("old-trial", 0, "target-a", trial.page_epoch)
        self.assertFalse(result.accepted)
        self.assertEqual("STALE_SELECTION_REJECTED", result.reason)
        self.assertEqual((), queue.selected_target_ids)

    def test_old_page_class_zero_cannot_select_new_page_slot_zero(self):
        queue = PagedSelectionQueue(_candidates(8))
        old = queue.open_trial("page-one-trial")
        self.assertTrue(queue.navigate_next())
        result = queue.accept_result(old.selection_id, 0, "target-a", old.page_epoch)
        self.assertFalse(result.accepted)
        self.assertEqual((), queue.selected_target_ids)

    def test_undo_duplicate_and_submit_chunking_preserve_order(self):
        queue = PagedSelectionQueue(_candidates(8))
        queue.select_slot(0)
        queue.select_slot(1)
        queue.select_slot(2)
        queue.navigate_next()
        queue.select_slot(0)
        queue.navigate_next()
        queue.select_slot(0)
        self.assertEqual(("target-a", "target-b", "target-c", "target-d", "target-g"), queue.selected_target_ids)
        self.assertIsNotNone(queue.undo_last())
        self.assertEqual(("target-a", "target-b", "target-c", "target-d"), queue.selected_target_ids)
        queue.navigate_previous()
        duplicate = queue.select_slot(0)
        self.assertFalse(duplicate.accepted)
        self.assertEqual("duplicate_target", duplicate.reason)
        queue.navigate_next()
        submit = queue.submit()
        self.assertTrue(submit.accepted)
        self.assertEqual((3, 1), tuple(len(batch.selections) for batch in submit.plan.batches))
        self.assertFalse(queue.navigation_enabled)
        self.assertFalse(queue.undo_enabled)
        self.assertTrue(queue.complete_execution())
        self.assertEqual(("E", "F", "G"), tuple(item.label for item in queue.current_page.candidates))
        self.assertTrue(queue.navigate_next())
        self.assertEqual(("H",), tuple(item.label for item in queue.current_page.candidates))

    def test_empty_submit_is_noop_and_execution_blocks_navigation(self):
        queue = PagedSelectionQueue(_candidates(2))
        self.assertFalse(queue.submit().accepted)
        self.assertEqual("empty_queue", queue.submit().reason)
        queue.select_slot(0)
        self.assertTrue(queue.submit().accepted)
        self.assertFalse(queue.navigate_next())

    def test_old_page_has_no_selected_overlay_after_successful_execution_rebuild(self):
        queue = PagedSelectionQueue(_candidates(4))
        queue.select_slot(0)
        plan = queue.submit().plan
        self.assertTrue(queue.complete_execution(tuple(entry.target_id for entry in plan.ordered_selections)))
        self.assertEqual(("B", "C", "D"), tuple(item.label for item in queue.current_page.candidates))
        self.assertFalse(any(item.selected for item in queue.current_page.candidates))

    def test_selected_anchor_returns_to_original_page_as_selected(self):
        queue = PagedSelectionQueue(_candidates(8))
        queue.navigate_next()
        queue.navigate_next()
        queue.select_slot(0)
        queue.navigate_previous()
        queue.navigate_previous()
        queue.navigate_next()
        queue.navigate_next()
        self.assertTrue(queue.current_page.candidates[0].selected)
        self.assertFalse(queue.current_page.candidates[0].active)

    def test_submit_uses_existing_m9_batches_without_reordering(self):
        queue = PagedSelectionQueue(_candidates(5))
        queue.select_slot(1)
        queue.navigate_next()
        queue.select_slot(1)
        queue.select_slot(0)
        queue.navigate_previous()
        queue.select_slot(0)
        queue.select_slot(2)
        submit = queue.submit()

        payloads = confirmed_batch_payloads(submit.plan, batch_id_prefix="test-m16")
        mapping = {candidate.target_id: candidate.logical_block_id for candidate in _candidates(5)}
        requests = []
        for payload in payloads:
            requests.extend(confirmed_batch_to_robot_requests(payload, mapping))
        self.assertEqual(("target-b", "target-e", "target-d", "target-a", "target-c"),
                         tuple(request.source_target_id for request in requests))
        self.assertEqual(("block_b_2", "block_e_5", "block_d_4", "block_a_1", "block_c_3"),
                         tuple(request.logical_block_id for request in requests))

    def test_m8_snapshot_registry_preserves_optional_page_metadata(self):
        transport = QuestSelectionTcpServer("127.0.0.1", 0)
        record = transport.register_snapshot(
            "m16-selection-1",
            [{
                "slotIndex": 0,
                "targetId": "target-a",
                "logicalBlockId": "block_a_1",
                "nominalFrequencyHz": 7.2,
                "active": True,
            }],
            snapshot_id="m16-page-snapshot-1",
            snapshot_version=1,
            page_id="m16-page-1",
            page_epoch=3,
        )
        self.assertEqual("m16-page-1", record["pageId"])
        self.assertEqual(3, record["pageEpoch"])
        self.assertEqual("m16-page-1", transport.evidence["authoritativeSnapshots"][0]["pageId"])

    def test_partial_page_and_selected_identity_use_explicit_inactive_slots(self):
        queue = PagedSelectionQueue(_candidates(4))
        queue.navigate_next()
        queue.select_slot(0)
        snapshot = page_to_authoritative_snapshot(queue.current_page, "snapshot-2", 2)
        self.assertEqual("m16-page-2", snapshot["pageId"])
        self.assertEqual("target-d", snapshot["candidates"][0]["targetId"])
        self.assertFalse(snapshot["candidates"][0]["active"])
        self.assertIsNone(snapshot["candidates"][1]["targetId"])
        self.assertFalse(snapshot["candidates"][1]["active"])
        self.assertEqual(12.0, snapshot["candidates"][2]["nominalFrequencyHz"])
        transport = QuestSelectionTcpServer("127.0.0.1", 0)
        registered = transport.register_snapshot("m16-selection-2", snapshot["candidates"], "snapshot-2", 2)
        self.assertIsNone(registered["candidateSnapshot"][1]["targetId"])
        self.assertFalse(registered["candidateSnapshot"][1]["active"])


if __name__ == "__main__":
    unittest.main()
