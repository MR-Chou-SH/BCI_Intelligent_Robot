"""M13.5 baseline/shadow/active runtime hardening.

This is a PC-side orchestration layer around the existing M12, M13 and M8
seams.  It owns trial identity, fault rejection, mode semantics and logging;
it does not decode EEG or alter the frozen M13 policy.
"""

from dataclasses import dataclass
import math
import subprocess
from typing import Optional

from eeg.decoder.pseudo_online import stabilize
from integration.m13_dynamic_stopping import (
    DynamicStoppingDecision,
    DynamicStoppingInputError,
    DynamicStoppingPolicy,
    DynamicStoppingSnapshot,
)
from integration.m13_5_logging import M135SessionLogger
from integration.m13_m8_selection_integration import (
    M13_SLOT_TO_FINAL_LABEL,
    dynamic_stopping_decision_to_m8_final_decision,
    submit_dynamic_stopping_decision,
)
from integration.m12_context_eeg_fusion import ActiveSsvepCandidate
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


MODE_BASELINE = "baseline"
MODE_SHADOW = "shadow"
MODE_ACTIVE = "active"
RUNTIME_MODES = (MODE_BASELINE, MODE_SHADOW, MODE_ACTIVE)
M13_5_POLICY = {"fusedThreshold": 0.70, "marginThreshold": 0.20, "requiredConsecutive": 2, "eegConfirmation": "fused_top_equals_raw_eeg_top"}
DEFAULT_ACTIVE_TARGET_IDS = ("m9-vblock-red-01", "m9-vblock-green-01", "m9-vblock-blue-01")


class M135RuntimeError(RuntimeError):
    """A runtime event was rejected closed without inventing intent."""


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def build_default_active_candidates(mapping=None):
    mapping = load_virtual_block_target_mapping() if mapping is None else mapping
    return tuple(ActiveSsvepCandidate.from_frozen_mapping(index, target_id, mapping) for index, target_id in enumerate(DEFAULT_ACTIVE_TARGET_IDS))


def mapping_public(candidates):
    return [candidate.to_public_dict() for candidate in sorted(candidates, key=lambda item: item.slot_index)]


def _mapping_signature(candidates):
    return tuple((item.slot_index, item.target_id, item.logical_block_id, item.nominal_frequency_hz) for item in sorted(candidates, key=lambda value: value.slot_index))


def _argmax_entries(entries, getter):
    values = [float(getter(entry)) for entry in entries]
    maximum = max(values)
    return tuple(entry.candidate.logical_block_id for entry, value in zip(entries, values) if math.isclose(value, maximum, rel_tol=0.0, abs_tol=1e-12))


def _no_decision(evaluated_windows, snapshot, reason, provenance):
    last = snapshot
    return DynamicStoppingDecision(
        False,
        None,
        None,
        None,
        None if last is None else last.window_index,
        None if last is None else last.effective_acquisition_seconds,
        evaluated_windows,
        False,
        reason,
        {} if last is None else {entry.candidate.logical_block_id: entry.fused_evidence for entry in last.fused_evidence.entries},
        {} if last is None else {entry.candidate.logical_block_id: entry.eeg_evidence_score for entry in last.fused_evidence.entries},
        0.70,
        None,
        0,
        provenance,
    )


@dataclass(frozen=True)
class RuntimeTrialResult:
    trial_id: str
    selection_id: str
    runtime_mode: str
    baseline_decision: Optional[DynamicStoppingDecision]
    m13_decision: Optional[DynamicStoppingDecision]
    submission: Optional[dict]
    closed: bool

    def to_public_dict(self):
        return {
            "trialId": self.trial_id,
            "selectionId": self.selection_id,
            "runtimeMode": self.runtime_mode,
            "baselineDecision": None if self.baseline_decision is None else self.baseline_decision.to_public_dict(),
            "m13Decision": None if self.m13_decision is None else self.m13_decision.to_public_dict(),
            "submission": self.submission,
            "closed": self.closed,
        }


class M135TrialRuntime:
    """One identity-scoped M13.5 trial with fail-closed state transitions."""

    def __init__(self, mode, orchestrator, logger, candidates=None):
        if mode not in RUNTIME_MODES:
            raise ValueError("runtime mode must be baseline, shadow, or active")
        self.mode = mode
        self.orchestrator = orchestrator
        self.logger = logger
        self.candidates = tuple(build_default_active_candidates() if candidates is None else candidates)
        if _mapping_signature(self.candidates) != _mapping_signature(build_default_active_candidates()):
            raise M135RuntimeError("active target mapping does not match the frozen slot/TargetId/logical-ID boundary")
        self._trial_id = None
        self._selection_id = None
        self._opened = False
        self._closed = False
        self._submitted = False
        self._submission = None
        self._submitted_decision = None
        self._snapshots = []
        self._seen_window_indices = set()
        self._baseline_rows = []
        self._m13_policy = None
        self._runtime_faults = []
        self._final_result = None

    @property
    def snapshots(self):
        return tuple(self._snapshots)

    @property
    def runtime_faults(self):
        return tuple(self._runtime_faults)

    def start_trial(self, selection_id, trial_id):
        if self._opened and not self._closed:
            raise M135RuntimeError("a trial is already active")
        if not selection_id or not trial_id:
            raise M135RuntimeError("selectionId and trialId are required")
        self._trial_id, self._selection_id = str(trial_id), str(selection_id)
        self._opened = False
        self._closed = False
        self._submitted = False
        self._submission = None
        self._submitted_decision = None
        self._snapshots = []
        self._seen_window_indices = set()
        self._baseline_rows = []
        self._m13_policy = None if self.mode == MODE_BASELINE else DynamicStoppingPolicy()
        self._runtime_faults = []
        self._final_result = None
        opened = bool(self.orchestrator.open_selection(self._selection_id, self._trial_id))
        self._opened = opened
        self.logger.append(
            "trial_started",
            trialId=self._trial_id,
            selectionId=self._selection_id,
            openAccepted=opened,
            mappingValid=True,
            mapping=list(mapping_public(self.candidates)),
            stateReset={"consecutiveCount": True, "candidateTarget": True, "previousDecision": True, "windowIndex": True, "previousEvidence": True},
            m13Invoked=self.mode != MODE_BASELINE,
            baselineSuppressionReason="baseline_mode" if self.mode == MODE_BASELINE else None,
        )
        if not opened:
            self._runtime_faults.append("selection_open_rejected")
            self._closed = True
            self.logger.append("trial_rejected", trialId=self._trial_id, selectionId=self._selection_id, reason="selection_open_rejected")
        return opened

    def _reject(self, reason, detail=None, fatal=True, window_index=None):
        if fatal and reason not in self._runtime_faults:
            self._runtime_faults.append(reason)
        self.logger.append(
            "window_rejected",
            trialId=self._trial_id,
            selectionId=self._selection_id,
            windowIndex=window_index,
            reasonCode=reason,
            detail=detail,
            fatal=bool(fatal),
            suppressionReason=reason,
        )
        return False

    def _raw_row(self, snapshot):
        entries = snapshot.fused_evidence.entries
        raw_top = _argmax_entries(entries, lambda entry: entry.eeg_evidence_score)
        top_entry = next((entry for entry in entries if entry.candidate.logical_block_id == raw_top[0]), None) if len(raw_top) == 1 else None
        label = None if top_entry is None else M13_SLOT_TO_FINAL_LABEL[top_entry.candidate.slot_index]
        return {
            "predictionIndex": snapshot.window_index,
            "relativeToStimulusStartSeconds": snapshot.effective_acquisition_seconds,
            "predictedClass": label,
            "rawTopLogicalBlockIds": list(raw_top),
            "rawScores": {entry.candidate.logical_block_id: entry.eeg_evidence_score for entry in entries},
            "groundTruthLabel": None,
        }

    def _mapping_matches(self, snapshot):
        return _mapping_signature(entry.candidate for entry in snapshot.fused_evidence.entries) == _mapping_signature(self.candidates)

    def observe(self, snapshot, trial_id=None, selection_id=None):
        if not isinstance(snapshot, DynamicStoppingSnapshot):
            return self._reject("malformed_evidence", "snapshot is not a DynamicStoppingSnapshot")
        if self._trial_id is None:
            return self._reject("no_active_trial", "window arrived before selection open")
        if trial_id != self._trial_id or selection_id != self._selection_id:
            return self._reject("stale_trial_or_selection", "evidence identifiers do not match the active trial", fatal=False, window_index=snapshot.window_index)
        if self._closed or self._submitted:
            self.logger.append("late_evidence_ignored", trialId=self._trial_id, selectionId=self._selection_id, windowIndex=snapshot.window_index, reasonCode="late_after_final_decision")
            return False
        if not self._opened:
            return self._reject("selection_not_open", "Quest selection was not accepted")
        if not self._mapping_matches(snapshot):
            return self._reject("target_snapshot_mismatch", "frozen candidate mapping differs from the current selection", window_index=snapshot.window_index)
        if snapshot.window_index in self._seen_window_indices:
            return self._reject("duplicate_window", "window index was already evaluated", fatal=False, window_index=snapshot.window_index)
        expected_index = len(self._snapshots)
        if snapshot.window_index != expected_index:
            return self._reject("out_of_order_or_missing_window", "expected window index {}, received {}".format(expected_index, snapshot.window_index), window_index=snapshot.window_index)

        raw_row = self._raw_row(snapshot)
        self._snapshots.append(snapshot)
        self._seen_window_indices.add(snapshot.window_index)
        self._baseline_rows.append(raw_row)
        evaluation = None
        if self._m13_policy is not None:
            try:
                evaluation = self._m13_policy.observe(snapshot)
            except DynamicStoppingInputError as error:
                self._runtime_faults.append("invalid_evidence")
                self.logger.append("window_rejected", trialId=self._trial_id, selectionId=self._selection_id, windowIndex=snapshot.window_index, reasonCode="invalid_evidence", detail=str(error), fatal=True, suppressionReason="invalid_evidence")
                return False
        self.logger.append(
            "window_evaluated",
            trialId=self._trial_id,
            selectionId=self._selection_id,
            windowIndex=snapshot.window_index,
            effectiveAcquisitionSeconds=snapshot.effective_acquisition_seconds,
            rawEegEvidence=raw_row["rawScores"],
            baselineRawTopLogicalBlockIds=raw_row["rawTopLogicalBlockIds"],
            m13State="suppressed" if self.mode == MODE_BASELINE else ("stopped" if self._m13_policy.decision is not None else "observing"),
            suppressionReason="baseline_mode" if self.mode == MODE_BASELINE else None,
            evaluation=None if evaluation is None else evaluation.to_public_dict(),
            hypotheticalDecision=None if self._m13_policy is None or self._m13_policy.decision is None else self._m13_policy.decision.to_public_dict(),
        )
        if self.mode == MODE_ACTIVE and self._m13_policy is not None and self._m13_policy.decision is not None:
            self._submit(self._m13_policy.decision, "m13_active")
        return True

    def _baseline_decision(self):
        if not self._baseline_rows:
            return _no_decision(0, None, "baseline_no_evidence", {"runtimeMode": MODE_BASELINE, "source": "M6 fixed consecutive baseline"})
        if any(len(item["rawTopLogicalBlockIds"]) != 1 or item["predictedClass"] is None for item in self._baseline_rows):
            return _no_decision(len(self._baseline_rows), self._snapshots[-1], "baseline_unresolved_tie", {"runtimeMode": MODE_BASELINE, "source": "M6 fixed consecutive baseline"})
        stabilized = stabilize(self._baseline_rows, 2)
        if not stabilized["decisionMade"]:
            return _no_decision(len(self._baseline_rows), self._snapshots[-1], "baseline_no_sufficient_consecutive_run", {"runtimeMode": MODE_BASELINE, "source": "M6 fixed consecutive baseline"})
        index = int(stabilized["decisionPredictionIndex"])
        snapshot = self._snapshots[index]
        entry = next(item for item in snapshot.fused_evidence.entries if item.candidate.slot_index == M13_SLOT_TO_FINAL_LABEL.index(stabilized["finalDecisionLabel"]))
        return DynamicStoppingDecision(
            True,
            entry.candidate.logical_block_id,
            entry.candidate.target_id,
            entry.candidate.slot_index,
            index,
            snapshot.effective_acquisition_seconds,
            len(self._baseline_rows),
            index < len(self._baseline_rows) - 1,
            "baseline_fixed_consecutive",
            {item.candidate.logical_block_id: item.fused_evidence for item in snapshot.fused_evidence.entries},
            {item.candidate.logical_block_id: item.eeg_evidence_score for item in snapshot.fused_evidence.entries},
            0.70,
            None,
            2,
            {"runtimeMode": MODE_BASELINE, "source": "M6 fixed consecutive baseline", "m13Invoked": False},
        )

    def _submit(self, decision, source):
        if self._submitted:
            self.logger.append("duplicate_suppressed", trialId=self._trial_id, selectionId=self._selection_id, reasonCode="post_decision_lock", submissionSource=source)
            return self._submission
        payload = dynamic_stopping_decision_to_m8_final_decision(decision, self._trial_id, self.logger.session_id)
        if source == "baseline":
            payload["stabilizer"] = "2-Consecutive"
            payload["m13Invoked"] = False
        try:
            result = self.orchestrator.submit_final_decision(payload)
        except Exception as error:
            result = {"status": "submission_exception", "reason": str(error)}
        self._submitted = True
        self._submission = result
        self._submitted_decision = decision
        self.logger.append(
            "final_submission",
            trialId=self._trial_id,
            selectionId=self._selection_id,
            submissionSource=source,
            decisionMade=decision.decision_made,
            selectedLogicalBlockId=decision.selected_logical_block_id,
            selectedTargetId=decision.selected_target_id,
            selectedSlotIndex=decision.selected_slot_index,
            stopReason=decision.stop_reason,
            earlyStop=decision.early_stop,
            effectiveAcquisitionSeconds=decision.effective_acquisition_seconds,
            m8Result=result,
            submitAttempted=True,
            submitSuccess=result.get("status") in ("quest_accepted", "no_decision"),
        )
        return result

    def finalize_trial(self):
        if self._final_result is not None:
            self.logger.append("duplicate_finalize_suppressed", trialId=self._trial_id, selectionId=self._selection_id, reasonCode="trial_already_closed")
            return self._final_result
        if self._trial_id is None:
            raise M135RuntimeError("cannot finalize before trial start")
        if not self._opened:
            self._closed = True
            self._final_result = RuntimeTrialResult(self._trial_id, self._selection_id, self.mode, None, None, None, True)
            return self._final_result

        baseline = self._baseline_decision()
        m13_decision = None
        if self.mode != MODE_BASELINE:
            if self._runtime_faults:
                m13_decision = _no_decision(len(self._snapshots), self._snapshots[-1] if self._snapshots else None, "runtime_fault:" + self._runtime_faults[0], {"runtimeMode": self.mode, "source": "M13.5 fail-closed runtime", "faults": list(self._runtime_faults)})
            else:
                m13_decision = self._m13_policy.finalize()

        if self.mode == MODE_BASELINE:
            if not self._submitted:
                self._submit(baseline, "baseline")
        elif self.mode == MODE_SHADOW:
            self.logger.append(
                "shadow_hypothetical_decision",
                trialId=self._trial_id,
                selectionId=self._selection_id,
                hypotheticalDecision=m13_decision.to_public_dict(),
                baselineDecision=baseline.to_public_dict(),
                wouldAffectFinalSelection=False,
                targetAgreement=(m13_decision.selected_logical_block_id == baseline.selected_logical_block_id),
            )
            if not self._submitted:
                self._submit(baseline, "baseline")
        else:
            if not self._submitted:
                self._submit(m13_decision, "m13_active")

        last = self._snapshots[-1] if self._snapshots else None
        m12_target = None
        if last is not None and len(last.fused_evidence.top_logical_block_ids) == 1:
            m12_target = last.fused_evidence.top_logical_block_ids[0]
        self._closed = True
        self.logger.append(
            "trial_closed",
            trialId=self._trial_id,
            selectionId=self._selection_id,
            baselineFinalLogicalBlockId=baseline.selected_logical_block_id,
            baselineDecisionMade=baseline.decision_made,
            baselineStopWindow=baseline.stop_window,
            baselineEffectiveAcquisitionSeconds=baseline.effective_acquisition_seconds,
            baselineEvaluatedWindows=len(self._baseline_rows),
            m13FinalLogicalBlockId=None if m13_decision is None else m13_decision.selected_logical_block_id,
            m13DecisionMade=None if m13_decision is None else m13_decision.decision_made,
            m13EarlyStop=None if m13_decision is None else m13_decision.early_stop,
            m13StopWindow=None if m13_decision is None else m13_decision.stop_window,
            m13EffectiveAcquisitionSeconds=None if m13_decision is None else m13_decision.effective_acquisition_seconds,
            m13StopReason=None if m13_decision is None else m13_decision.stop_reason,
            m13EvaluatedWindows=None if m13_decision is None else m13_decision.evaluated_windows,
            m12FullWindowLogicalBlockId=m12_target,
            submitted=self._submitted,
            submission=self._submission,
            runtimeFaults=list(self._runtime_faults),
            evaluatedWindowCount=len(self._snapshots),
            mappingValid=True,
        )
        self._final_result = RuntimeTrialResult(self._trial_id, self._selection_id, self.mode, baseline, m13_decision, self._submission, True)
        return self._final_result


__all__ = ["MODE_BASELINE", "MODE_SHADOW", "MODE_ACTIVE", "RUNTIME_MODES", "M13_5_POLICY", "M135RuntimeError", "M135SessionLogger", "M135TrialRuntime", "RuntimeTrialResult", "build_default_active_candidates", "mapping_public"]
