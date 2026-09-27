"""M16 opt-in paged candidate browser and global ordered selection queue.

This module is deliberately independent of the legacy M8 grouped controller.
It owns page/session semantics only.  A submitted queue is converted into the
existing 1--3 selection confirmed-batch shape; robot and MuJoCo code remain
downstream consumers of that unchanged contract.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import count
from typing import Callable, Iterable, Optional, Tuple


PAGED_QUEUE_MODE = "paged_queue_v1"
SSVEP_FREQUENCIES_HZ = (7.2, 9.0, 12.0)
MAX_PAGE_SIZE = 3
MAX_DOWNSTREAM_BATCH_SIZE = 3


@dataclass(frozen=True)
class Candidate:
    logical_block_id: str
    target_id: str
    label: str = ""

    def __post_init__(self):
        if not self.logical_block_id or not self.target_id:
            raise ValueError("logical_block_id and target_id are required")


@dataclass(frozen=True)
class PageCandidate:
    candidate: Candidate
    slot_index: int
    nominal_frequency_hz: float
    active: bool
    selected: bool

    @property
    def logical_block_id(self):
        return self.candidate.logical_block_id

    @property
    def target_id(self):
        return self.candidate.target_id

    @property
    def label(self):
        return self.candidate.label


@dataclass(frozen=True)
class PageView:
    page_id: str
    page_index: int
    page_count: int
    page_epoch: int
    candidates: Tuple[PageCandidate, ...]


@dataclass(frozen=True)
class FrozenTrialCandidate:
    slot_index: int
    nominal_frequency_hz: float
    target_id: str
    logical_block_id: str
    active: bool


@dataclass(frozen=True)
class FrozenSelectionTrial:
    selection_id: str
    page_id: str
    page_epoch: int
    candidates: Tuple[FrozenTrialCandidate, ...]

    def candidate_for_slot(self, slot_index):
        for candidate in self.candidates:
            if candidate.slot_index == slot_index:
                return candidate
        return None


@dataclass(frozen=True)
class QueueEntry:
    selection_id: str
    candidate: Candidate
    page_id: str
    page_epoch: int
    slot_index: int
    nominal_frequency_hz: float
    build_slot_index: int

    @property
    def target_id(self):
        return self.candidate.target_id


@dataclass(frozen=True)
class SelectionResult:
    accepted: bool
    reason: str
    entry: Optional[QueueEntry] = None


@dataclass(frozen=True)
class CommittedBatch:
    batch_index: int
    selections: Tuple[QueueEntry, ...]


@dataclass(frozen=True)
class CommitPlan:
    ordered_selections: Tuple[QueueEntry, ...]
    batches: Tuple[CommittedBatch, ...]


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    reason: str
    plan: Optional[CommitPlan] = None


class PagedSelectionQueue:
    """State machine for one opt-in paged selection session.

    Page membership and order are frozen for the lifetime of a selection
    session.  A selected candidate remains in its original page/slot and is
    represented as inactive/selected until successful execution completion.
    """

    STATE_SELECTING = "selecting"
    STATE_EXECUTING = "executing"

    def __init__(
        self,
        candidates: Iterable[Candidate],
        page_size: int = MAX_PAGE_SIZE,
        selection_id_factory: Optional[Callable[[], str]] = None,
        event_sink: Optional[Callable[[str, dict], None]] = None,
    ):
        if page_size != MAX_PAGE_SIZE:
            raise ValueError("M16 SSVEP pages must contain at most three slots")
        normalized = tuple(candidates)
        if not normalized:
            raise ValueError("at least one candidate is required")
        target_ids = [candidate.target_id for candidate in normalized]
        logical_ids = [candidate.logical_block_id for candidate in normalized]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("duplicate TargetId in global candidate pool")
        if len(set(logical_ids)) != len(logical_ids):
            raise ValueError("duplicate logical block ID in global candidate pool")

        self.mode = PAGED_QUEUE_MODE
        self._all_candidates = normalized
        self._page_size = page_size
        self._selection_id_factory = selection_id_factory or self._default_selection_id_factory()
        self._event_sink = event_sink
        self._selection_counter = count(1)
        self._state = self.STATE_SELECTING
        self._current_page_index = 0
        self._page_epoch = 0
        self._selected_by_target = {}
        self._queue = []
        self._processed_target_ids = set()
        self._current_trial = None
        self._submitted_plan = None
        self._pages = self._build_pages(self._all_candidates)
        self._emit("page_changed", {"to": self._current_page_index, "pageEpoch": self._page_epoch, "page": self.page})

    @staticmethod
    def _default_selection_id_factory():
        counter = count(1)
        return lambda: "m16-selection-{:04d}".format(next(counter))

    @property
    def state(self):
        return self._state

    @property
    def current_page(self):
        return self.page

    @property
    def page(self):
        page_candidates = []
        for slot_index, candidate in enumerate(self._pages[self._current_page_index]):
            selected = candidate.target_id in self._selected_by_target
            page_candidates.append(
                PageCandidate(
                    candidate=candidate,
                    slot_index=slot_index,
                    nominal_frequency_hz=SSVEP_FREQUENCIES_HZ[slot_index],
                    active=not selected,
                    selected=selected,
                )
            )
        return PageView(
            page_id="m16-page-{}".format(self._current_page_index + 1),
            page_index=self._current_page_index,
            page_count=len(self._pages),
            page_epoch=self._page_epoch,
            candidates=tuple(page_candidates),
        )

    @property
    def page_count(self):
        return len(self._pages)

    @property
    def page_index(self):
        return self._current_page_index

    @property
    def page_epoch(self):
        return self._page_epoch

    @property
    def current_trial(self):
        return self._current_trial

    @property
    def queue(self):
        return tuple(self._queue)

    @property
    def selected_target_ids(self):
        return tuple(entry.target_id for entry in self._queue)

    @property
    def selected_labels(self):
        return tuple(entry.candidate.label for entry in self._queue)

    @property
    def processed_target_ids(self):
        return frozenset(self._processed_target_ids)

    @property
    def navigation_enabled(self):
        return self._state == self.STATE_SELECTING

    @property
    def undo_enabled(self):
        return self.navigation_enabled and bool(self._queue)

    @property
    def submit_enabled(self):
        return self.navigation_enabled and bool(self._queue)

    def navigate_next(self):
        if not self.navigation_enabled:
            return False
        if self._current_page_index >= len(self._pages) - 1:
            return False
        return self._navigate_to(self._current_page_index + 1)

    def navigate_previous(self):
        if not self.navigation_enabled:
            return False
        if self._current_page_index <= 0:
            return False
        return self._navigate_to(self._current_page_index - 1)

    def cancel_trial(self, selection_id, reason="cancelled"):
        """Invalidate one armed trial without changing page or global queue."""
        trial = self._current_trial
        if trial is None or trial.selection_id != selection_id:
            return False
        self._current_trial = None
        self._emit(
            "stale_rejected",
            {
                "selectionId": selection_id,
                "pageEpochOld": trial.page_epoch,
                "pageEpochCurrent": self._page_epoch,
                "reason": reason,
            },
        )
        return True

    def append_candidates(self, candidates):
        """Append new targets while preserving every existing page/slot assignment.

        A partial final page is filled left-to-right before further pages are
        added. The current page stays visible; a page epoch change invalidates
        any trial whose frozen active-slot set predates the update.
        """
        if not self.navigation_enabled:
            return False
        appended = tuple(candidates)
        if not appended:
            return False
        if any(not isinstance(candidate, Candidate) for candidate in appended):
            raise TypeError("appended candidates must be Candidate instances")
        known_target_ids = {candidate.target_id for candidate in self._all_candidates}
        known_logical_ids = {candidate.logical_block_id for candidate in self._all_candidates}
        for candidate in appended:
            if candidate.target_id in known_target_ids:
                raise ValueError("duplicate TargetId in appended candidate pool")
            if candidate.logical_block_id in known_logical_ids:
                raise ValueError("duplicate logical block ID in appended candidate pool")
            known_target_ids.add(candidate.target_id)
            known_logical_ids.add(candidate.logical_block_id)

        if self._current_trial is not None:
            self.cancel_trial(self._current_trial.selection_id, "candidate_added")

        pages = [list(page) for page in self._pages if page]
        pending = list(appended)
        if pages and len(pages[-1]) < self._page_size:
            available = self._page_size - len(pages[-1])
            pages[-1].extend(pending[:available])
            pending = pending[available:]
        while pending:
            pages.append(pending[: self._page_size])
            pending = pending[self._page_size :]
        self._pages = tuple(tuple(page) for page in pages) or (tuple(),)
        self._all_candidates = self._all_candidates + appended
        self._current_page_index = min(self._current_page_index, len(self._pages) - 1)
        self._page_epoch += 1
        self._emit(
            "candidates_appended",
            {"targetIds": tuple(candidate.target_id for candidate in appended), "page": self.page},
        )
        return True

    def _navigate_to(self, page_index):
        previous_page_index = self._current_page_index
        if self._current_trial is not None:
            old_selection_id = self._current_trial.selection_id
            old_epoch = self._current_trial.page_epoch
            self._current_trial = None
            self._emit(
                "stale_rejected",
                {
                    "selectionId": old_selection_id,
                    "pageEpochOld": old_epoch,
                    "pageEpochCurrent": old_epoch + 1,
                    "reason": "page_changed",
                },
            )
        self._current_page_index = page_index
        self._page_epoch += 1
        self._emit(
            "page_changed",
            {
                "from": previous_page_index,
                "to": page_index,
                "pageEpoch": self._page_epoch,
                "page": self.page,
            },
        )
        return True

    def open_trial(self, selection_id=None):
        if not self.navigation_enabled:
            raise RuntimeError("selection trial cannot open during execution")
        if self._current_trial is not None:
            raise RuntimeError("a selection trial is already open")
        selection_id = selection_id or self._selection_id_factory()
        frozen = tuple(
            FrozenTrialCandidate(
                slot_index=candidate.slot_index,
                nominal_frequency_hz=candidate.nominal_frequency_hz,
                target_id=candidate.target_id,
                logical_block_id=candidate.logical_block_id,
                active=candidate.active,
            )
            for candidate in self.page.candidates
        )
        self._current_trial = FrozenSelectionTrial(
            selection_id=selection_id,
            page_id=self.page.page_id,
            page_epoch=self.page_epoch,
            candidates=frozen,
        )
        self._emit(
            "trial_opened",
            {
                "selectionId": selection_id,
                "pageId": self.page.page_id,
                "pageEpoch": self.page_epoch,
                "candidates": frozen,
            },
        )
        return self._current_trial

    def accept_result(
        self,
        selection_id,
        slot_index,
        target_id=None,
        page_epoch=None,
    ):
        trial = self._current_trial
        if trial is None:
            return SelectionResult(False, "STALE_SELECTION_REJECTED")
        if selection_id != trial.selection_id:
            return self._reject_stale("selection_id_mismatch", selection_id, trial.page_epoch)
        if page_epoch is not None and page_epoch != trial.page_epoch:
            return self._reject_stale("page_changed", selection_id, trial.page_epoch)
        candidate = trial.candidate_for_slot(slot_index)
        if candidate is None:
            return SelectionResult(False, "slot_invalid")
        if candidate.target_id in self._selected_by_target:
            self._current_trial = None
            return SelectionResult(False, "duplicate_target")
        if not candidate.active:
            self._current_trial = None
            return SelectionResult(False, "target_inactive")
        if target_id is not None and target_id != candidate.target_id:
            return self._reject_stale("target_snapshot_mismatch", selection_id, trial.page_epoch)
        entry = QueueEntry(
            selection_id=selection_id,
            candidate=Candidate(
                logical_block_id=candidate.logical_block_id,
                target_id=candidate.target_id,
                label=next(
                    item.candidate.label
                    for item in self.page.candidates
                    if item.target_id == candidate.target_id
                ),
            ),
            page_id=trial.page_id,
            page_epoch=trial.page_epoch,
            slot_index=candidate.slot_index,
            nominal_frequency_hz=candidate.nominal_frequency_hz,
            build_slot_index=len(self._queue) if len(self._queue) < 4 else None,
        )
        self._queue.append(entry)
        self._selected_by_target[candidate.target_id] = entry
        self._current_trial = None
        self._emit("selected", {"targetId": entry.target_id, "queue": self.selected_target_ids})
        return SelectionResult(True, "accepted", entry)

    def select_slot(self, slot_index):
        trial = self.open_trial()
        candidate = trial.candidate_for_slot(slot_index)
        if candidate is None:
            raise ValueError("slot {} is not present on the current page".format(slot_index))
        return self.accept_result(trial.selection_id, slot_index, candidate.target_id, trial.page_epoch)

    def _reject_stale(self, reason, selection_id, epoch):
        self._emit(
            "stale_rejected",
            {
                "selectionId": selection_id,
                "pageEpochOld": epoch,
                "pageEpochCurrent": self.page_epoch,
                "reason": reason,
            },
        )
        return SelectionResult(False, "STALE_SELECTION_REJECTED")

    def undo_last(self):
        if not self.undo_enabled:
            return None
        entry = self._queue.pop()
        self._selected_by_target.pop(entry.target_id, None)
        self._emit("undo", {"targetId": entry.target_id, "queue": self.selected_target_ids})
        return entry

    def submit(self):
        if not self.navigation_enabled:
            return SubmitResult(False, "execution_in_progress")
        if not self._queue:
            self._emit("submit_noop", {"reason": "empty_queue"})
            return SubmitResult(False, "empty_queue")
        ordered = tuple(self._queue)
        batches = self._chunk_for_existing_batch_contract(ordered)
        plan = CommitPlan(ordered_selections=ordered, batches=batches)
        self._submitted_plan = plan
        self._state = self.STATE_EXECUTING
        self._current_trial = None
        self._emit(
            "submit",
            {
                "orderedTargets": tuple(entry.target_id for entry in ordered),
                "batchCount": len(batches),
            },
        )
        return SubmitResult(True, "submitted", plan)

    def complete_execution(self, processed_target_ids=None, success=True):
        if self._state != self.STATE_EXECUTING:
            return False
        plan = self._submitted_plan
        if not success or plan is None:
            self._state = self.STATE_SELECTING
            self._submitted_plan = None
            return False
        processed = tuple(processed_target_ids or (entry.target_id for entry in plan.ordered_selections))
        expected = tuple(entry.target_id for entry in plan.ordered_selections)
        if tuple(processed) != expected:
            raise ValueError("execution completion must preserve submitted target order")
        self._processed_target_ids.update(processed)
        self._queue = []
        self._selected_by_target = {}
        self._submitted_plan = None
        self._state = self.STATE_SELECTING
        self._page_epoch += 1
        remaining = tuple(
            candidate for candidate in self._all_candidates if candidate.target_id not in self._processed_target_ids
        )
        if not remaining:
            self._pages = (tuple(),)
            self._current_page_index = 0
        else:
            self._pages = self._build_pages(remaining)
            self._current_page_index = 0
        self._emit("execution_complete", {"processed": processed, "page": self.page})
        return True

    def downstream_batches(self):
        if self._submitted_plan is None:
            return tuple()
        return self._submitted_plan.batches

    def _build_pages(self, candidates):
        return tuple(
            tuple(candidates[start : start + self._page_size])
            for start in range(0, len(candidates), self._page_size)
        )

    @staticmethod
    def _chunk_for_existing_batch_contract(ordered):
        """Keep global order while satisfying M9's unique-slot batch schema."""
        batches = []
        current = []
        current_slots = set()
        for entry in ordered:
            if current and (
                len(current) >= MAX_DOWNSTREAM_BATCH_SIZE or entry.slot_index in current_slots
            ):
                batches.append(CommittedBatch(len(batches), tuple(current)))
                current = []
                current_slots = set()
            current.append(entry)
            current_slots.add(entry.slot_index)
        if current:
            batches.append(CommittedBatch(len(batches), tuple(current)))
        return tuple(batches)

    def _emit(self, event, payload):
        if self._event_sink is not None:
            self._event_sink(event, dict(payload))


def confirmed_batch_payloads(plan, batch_id_prefix="m16-paged-batch"):
    """Serialize a CommitPlan into unchanged M8/M9 batch-sized payloads."""
    if not isinstance(plan, CommitPlan):
        raise TypeError("plan must be a CommitPlan")
    submitted_utc = datetime.now(timezone.utc).isoformat()
    payloads = []
    for batch in plan.batches:
        batch_id = "{}-{}".format(batch_id_prefix, batch.batch_index + 1)
        selections = []
        for entry in batch.selections:
            selections.append(
                {
                    "selectionId": entry.selection_id,
                    "targetId": entry.target_id,
                    "logicalBlockId": entry.candidate.logical_block_id,
                    "predictedClassIndex": entry.slot_index,
                    "slotIndex": entry.slot_index,
                    "semanticLabel": entry.candidate.label or None,
                    "resolvedUtc": submitted_utc,
                    "buildSlotIndex": entry.build_slot_index,
                    "provenance": "quest_frozen_selection_snapshot",
                }
            )
        confirmed = {
            "batchId": batch_id,
            "groupId": "m16-paged-session",
            "groupIndex": batch.batch_index,
            "submittedUtc": submitted_utc,
            "provenance": "quest_confirmed_target_batch",
            "selections": selections,
        }
        payloads.append(
            {
                "protocolVersion": 1,
                "messageType": "target_batch_confirmed",
                "batchId": batch_id,
                "confirmedBatch": confirmed,
            }
        )
    return tuple(payloads)


def page_to_authoritative_snapshot(page, snapshot_id, snapshot_version):
    """Serialize a PageView into the existing fixed-three-slot M8 snapshot.

    Empty page slots are explicitly inactive with null identity. A selected
    target keeps its real identity but is sent with ``active=false``; the M16
    Unity binding understands that opt-in presentation state while legacy mode
    continues to use its original inactive-slot contract.
    """
    if page is None or not snapshot_id or int(snapshot_version) < 1:
        raise ValueError("page, snapshot_id and positive snapshot_version are required")
    candidates = []
    for slot_index in range(MAX_PAGE_SIZE):
        if slot_index < len(page.candidates):
            item = page.candidates[slot_index]
            candidates.append(
                {
                    "slotIndex": slot_index,
                    "targetId": item.target_id,
                    "logicalBlockId": item.logical_block_id,
                    "nominalFrequencyHz": item.nominal_frequency_hz,
                    "active": item.active,
                }
            )
        else:
            candidates.append(
                {
                    "slotIndex": slot_index,
                    "targetId": None,
                    "logicalBlockId": None,
                    "nominalFrequencyHz": SSVEP_FREQUENCIES_HZ[slot_index],
                    "active": False,
                }
            )
    return {
        "snapshotId": str(snapshot_id),
        "snapshotVersion": int(snapshot_version),
        "pageId": page.page_id,
        "pageEpoch": page.page_epoch,
        "candidates": tuple(candidates),
    }
