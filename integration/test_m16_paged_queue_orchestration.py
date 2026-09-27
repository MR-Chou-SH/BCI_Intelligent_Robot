import unittest

from integration.m16_paged_queue import Candidate, PagedSelectionQueue
from integration.m16_paged_queue_orchestration import PagedQueueQuestOrchestrator


class _FakeTransport:
    def __init__(self):
        self.opened = []
        self.resolved = []
        self.closed = []
        self.aborted = []

    def open_selection(self, selection_id, snapshot):
        self.opened.append((selection_id, snapshot))
        return {"accepted": True, "selectionId": selection_id}

    def submit_eeg_selection(self, selection_id, slot_index):
        snapshot = next(item[1] for item in self.opened if item[0] == selection_id)
        candidate = snapshot["candidates"][slot_index]
        self.resolved.append((selection_id, slot_index))
        return {"accepted": True, "resolvedTargetId": candidate["targetId"]}

    def abort_selection(self, selection_id):
        self.aborted.append(selection_id)
        return {"accepted": True}

    def close_batch(self, payload):
        self.closed.append(payload)
        return {"accepted": True, "batchId": payload["batchId"]}


def _pool():
    return tuple(Candidate("block_{}_{}".format(chr(97 + i), i + 1), "target-" + chr(97 + i), chr(65 + i))
                 for i in range(8))


class M16PagedQueueOrchestrationTests(unittest.TestCase):
    def test_p3_p1_p2_replay_uses_page_metadata_and_global_order(self):
        transport = _FakeTransport()
        adapter = PagedQueueQuestOrchestrator(PagedSelectionQueue(_pool()), transport)
        adapter.navigate_next()
        adapter.navigate_next()
        trial, snapshot, _ = adapter.open_selection()
        adapter.resolve_slot(trial, 0)
        self.assertEqual("m16-page-3", snapshot["pageId"])
        adapter.navigate_previous()
        adapter.navigate_previous()
        trial, _, _ = adapter.open_selection()
        adapter.resolve_slot(trial, 1)
        adapter.navigate_next()
        trial, _, _ = adapter.open_selection()
        adapter.resolve_slot(trial, 1)
        self.assertEqual(("target-g", "target-b", "target-e"), adapter.queue.selected_target_ids)
        plan, acks = adapter.submit()
        self.assertTrue(plan.accepted)
        self.assertEqual(("target-g", "target-b", "target-e"), tuple(
            item.target_id
            for batch in adapter.queue.downstream_batches()
            for item in batch.selections
        ))
        self.assertEqual(2, len(acks))

    def test_navigation_aborts_active_transport_trial_before_epoch_change(self):
        transport = _FakeTransport()
        adapter = PagedQueueQuestOrchestrator(PagedSelectionQueue(_pool()), transport)
        trial, _, _ = adapter.open_selection()
        self.assertTrue(adapter.navigate_next())
        self.assertEqual([trial.selection_id], transport.aborted)


if __name__ == "__main__":
    unittest.main()
