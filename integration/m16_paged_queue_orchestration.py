"""Opt-in PC orchestration seam for M16 paged_queue_v1.

The legacy showcase and M8 grouped orchestrator are intentionally untouched.
This adapter only binds the M16 state machine to the existing newline M8
selection transport and emits existing <=3 confirmed batches on Submit.
"""

from integration.m16_paged_queue import (
    PagedSelectionQueue,
    confirmed_batch_payloads,
    page_to_authoritative_snapshot,
)


class PagedQueueTransportError(RuntimeError):
    pass


class PagedQueueQuestOrchestrator:
    def __init__(self, queue, transport):
        if not isinstance(queue, PagedSelectionQueue):
            raise TypeError("queue must be a PagedSelectionQueue")
        self.queue = queue
        self.transport = transport
        self._snapshot_version = 0
        self._selection_number = 0

    def open_selection(self):
        trial = self.queue.open_trial()
        if trial is None:
            raise PagedQueueTransportError("selection trial could not open")
        self._snapshot_version += 1
        snapshot = page_to_authoritative_snapshot(
            self.queue.current_page,
            "m16-{}-snapshot-{}".format(trial.page_id, self._snapshot_version),
            self._snapshot_version,
        )
        ack = self.transport.open_selection(trial.selection_id, snapshot=snapshot)
        if not ack.get("accepted"):
            raise PagedQueueTransportError("Quest rejected paged selection_open: {}".format(ack))
        return trial, snapshot, ack

    def resolve_slot(self, trial, slot_index):
        candidate = trial.candidate_for_slot(slot_index)
        if candidate is None:
            raise PagedQueueTransportError("slot is not present on the frozen page")
        ack = self.transport.submit_eeg_selection(trial.selection_id, slot_index)
        if not ack.get("accepted"):
            raise PagedQueueTransportError("Quest rejected paged EEG selection: {}".format(ack))
        result = self.queue.accept_result(
            trial.selection_id,
            slot_index,
            target_id=ack.get("resolvedTargetId") or candidate.target_id,
            page_epoch=trial.page_epoch,
        )
        if not result.accepted:
            raise PagedQueueTransportError("local paged queue rejected Quest ACK: {}".format(result.reason))
        return result.entry, ack

    def navigate_next(self):
        self._abort_active_trial_for_navigation()
        return self.queue.navigate_next()

    def navigate_previous(self):
        self._abort_active_trial_for_navigation()
        return self.queue.navigate_previous()

    def _abort_active_trial_for_navigation(self):
        trial = self.queue.current_trial
        if trial is not None:
            self.transport.abort_selection(trial.selection_id)

    def submit(self):
        result = self.queue.submit()
        if not result.accepted:
            return result, tuple()
        acknowledgements = []
        for payload in confirmed_batch_payloads(result.plan):
            acknowledgements.append(self.transport.close_batch(payload))
        return result, tuple(acknowledgements)

    def complete_execution(self):
        if not self.queue.downstream_batches():
            return False
        ordered = tuple(entry.target_id for entry in self.queue.downstream_batches()[0].selections)
        for batch in self.queue.downstream_batches()[1:]:
            ordered += tuple(entry.target_id for entry in batch.selections)
        return self.queue.complete_execution(ordered)

