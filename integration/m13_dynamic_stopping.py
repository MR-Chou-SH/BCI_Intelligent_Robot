"""M13 context-aware dynamic stopping baseline.

M13 consumes repeated M12 ``FusedTargetEvidence`` values.  It deliberately
does not decode EEG, change the M6 window schedule, or resolve Quest targets.
The policy is a transparent engineering baseline: two consecutive windows
must pass the fused threshold, margin, and raw-EEG confirmation gate.
"""

from dataclasses import dataclass
import math
from typing import Mapping, Optional, Sequence, Tuple

from eeg.decoder.characterization import WINDOW_GRID_SECONDS
from eeg.decoder.config import DecoderConfig
from eeg.decoder.pseudo_online import DEFAULT_PSEUDO_ONLINE_CONFIG, SLIDING_STEP_SAMPLES
from integration.m12_context_eeg_fusion import (
    ActiveSsvepCandidate,
    FusedTargetEvidence,
)


M13_FUSED_EVIDENCE_THRESHOLD = 0.70
M13_MARGIN_THRESHOLD = 0.20
M13_REQUIRED_CONSECUTIVE = 2
M13_ONSET_GUARD_SECONDS = DecoderConfig().onset_guard_seconds
M13_ONLINE_ANALYSIS_WINDOW_SECONDS = DEFAULT_PSEUDO_ONLINE_CONFIG.analysis_duration_seconds
M13_ONLINE_STEP_SECONDS = SLIDING_STEP_SAMPLES / DEFAULT_PSEUDO_ONLINE_CONFIG.input_sampling_rate_hz
M13_ONLINE_FIRST_EFFECTIVE_SECONDS = M13_ONSET_GUARD_SECONDS + M13_ONLINE_ANALYSIS_WINDOW_SECONDS


class DynamicStoppingInputError(ValueError):
    """Evidence or trajectory metadata cannot be accepted safely."""


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _argmax_ids(entries, value_getter):
    values = [float(value_getter(entry)) for entry in entries]
    maximum = max(values)
    return tuple(
        entry.candidate.logical_block_id
        for entry, value in zip(entries, values)
        if math.isclose(value, maximum, rel_tol=0.0, abs_tol=1e-12)
    )


@dataclass(frozen=True)
class DynamicStoppingSnapshot:
    """One time-indexed M12 result; M12 evidence remains the source of truth."""

    window_index: int
    analysis_window_seconds: float
    effective_acquisition_seconds: float
    fused_evidence: FusedTargetEvidence
    provenance: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self):
        if not isinstance(self.window_index, int) or isinstance(self.window_index, bool) or self.window_index < 0:
            raise DynamicStoppingInputError("windowIndex must be a nonnegative integer")
        if not _finite_number(self.analysis_window_seconds) or self.analysis_window_seconds <= 0.0:
            raise DynamicStoppingInputError("analysisWindowSeconds must be positive and finite")
        if not _finite_number(self.effective_acquisition_seconds) or self.effective_acquisition_seconds <= 0.0:
            raise DynamicStoppingInputError("effectiveAcquisitionSeconds must be positive and finite")
        if not isinstance(self.fused_evidence, FusedTargetEvidence):
            raise DynamicStoppingInputError("fusedEvidence must be an M12 FusedTargetEvidence")

    @classmethod
    def from_m12(cls, window_index, fused_evidence, analysis_window_seconds, effective_acquisition_seconds=None, provenance=None):
        if effective_acquisition_seconds is None:
            effective_acquisition_seconds = M13_ONSET_GUARD_SECONDS + float(analysis_window_seconds)
        pairs = tuple(sorted((str(key), str(value)) for key, value in (provenance or {}).items()))
        return cls(window_index, float(analysis_window_seconds), float(effective_acquisition_seconds), fused_evidence, pairs)

    def to_public_dict(self):
        return {
            "windowIndex": self.window_index,
            "analysisWindowSeconds": self.analysis_window_seconds,
            "effectiveAcquisitionSeconds": self.effective_acquisition_seconds,
            "fusedEvidence": self.fused_evidence.to_public_dict(),
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class WindowEvaluation:
    """Machine-readable policy state after one evaluated window."""

    snapshot: Optional[DynamicStoppingSnapshot]
    window_index: int
    eeg_scores: Mapping[str, float]
    eeg_top_logical_block_ids: Tuple[str, ...]
    fused_top_logical_block_ids: Tuple[str, ...]
    margin: Optional[float]
    threshold_pass: bool
    margin_pass: bool
    eeg_confirmation_pass: bool
    eligible: bool
    consecutive_count: int
    stop: bool
    reason: str

    def to_public_dict(self):
        fused = self.snapshot.fused_evidence if self.snapshot is not None else None
        return {
            "windowIndex": self.window_index,
            "windowTimeSeconds": None if self.snapshot is None else self.snapshot.effective_acquisition_seconds,
            "analysisWindowSeconds": None if self.snapshot is None else self.snapshot.analysis_window_seconds,
            "rawEegScores": dict(self.eeg_scores),
            "eegTopLogicalBlockIds": list(self.eeg_top_logical_block_ids),
            "contextPrior": [] if fused is None else [
                {"logicalBlockId": entry.candidate.logical_block_id, "contextPriorGlobal": entry.context_prior_global, "contextPriorActive": entry.context_prior_active, "contextPriorSoft": entry.context_prior_soft}
                for entry in fused.entries
            ],
            "fusedEvidence": {} if fused is None else {entry.candidate.logical_block_id: entry.fused_evidence for entry in fused.entries},
            "fusedTopLogicalBlockIds": list(self.fused_top_logical_block_ids),
            "fusedTopTargetIds": [] if fused is None else [entry.candidate.target_id for entry in fused.entries if entry.candidate.logical_block_id in self.fused_top_logical_block_ids],
            "margin": self.margin,
            "threshold": M13_FUSED_EVIDENCE_THRESHOLD,
            "marginThreshold": M13_MARGIN_THRESHOLD,
            "thresholdPass": self.threshold_pass,
            "marginPass": self.margin_pass,
            "eegConfirmationPass": self.eeg_confirmation_pass,
            "eligible": self.eligible,
            "consecutiveCount": self.consecutive_count,
            "stop": self.stop,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DynamicStoppingDecision:
    """M13 terminal result suitable for an explicit M8 adapter."""

    decision_made: bool
    selected_logical_block_id: Optional[str]
    selected_target_id: Optional[str]
    selected_slot_index: Optional[int]
    stop_window: Optional[int]
    effective_acquisition_seconds: Optional[float]
    evaluated_windows: int
    early_stop: bool
    stop_reason: str
    final_fused_evidence: Mapping[str, float]
    final_raw_eeg_evidence: Mapping[str, float]
    threshold: float
    margin: Optional[float]
    consecutive_count: int
    provenance: Mapping[str, object]

    def to_public_dict(self):
        return {
            "decisionMade": self.decision_made,
            "selectedLogicalBlockId": self.selected_logical_block_id,
            "selectedTargetId": self.selected_target_id,
            "selectedSlotIndex": self.selected_slot_index,
            "stopWindow": self.stop_window,
            "effectiveAcquisitionSeconds": self.effective_acquisition_seconds,
            "evaluatedWindows": self.evaluated_windows,
            "earlyStop": self.early_stop,
            "stopReason": self.stop_reason,
            "finalFusedEvidence": dict(self.final_fused_evidence),
            "finalRawEegEvidence": dict(self.final_raw_eeg_evidence),
            "threshold": self.threshold,
            "margin": self.margin,
            "consecutiveCount": self.consecutive_count,
            "provenance": dict(self.provenance),
        }


def _validate_evidence(evidence):
    if not isinstance(evidence, FusedTargetEvidence) or len(evidence.entries) != 3:
        raise DynamicStoppingInputError("M13 requires one valid M12 evidence entry per active slot")
    logical_ids = [entry.candidate.logical_block_id for entry in evidence.entries]
    if len(set(logical_ids)) != len(logical_ids):
        raise DynamicStoppingInputError("M12 evidence contains duplicate logical IDs")
    for entry in evidence.entries:
        for value in (entry.eeg_evidence_score, entry.fused_evidence, entry.context_prior_soft):
            if not _finite_number(value) or float(value) < 0.0:
                raise DynamicStoppingInputError("M12 evidence contains non-finite or negative values")
    fused_total = sum(float(entry.fused_evidence) for entry in evidence.entries)
    if not math.isclose(fused_total, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise DynamicStoppingInputError("M12 fused evidence must be normalized")
    return evidence


def _empty_decision(policy, reason):
    return DynamicStoppingDecision(
        False, None, None, None,
        None if not policy._evaluations else policy._evaluations[-1].window_index,
        None if not policy._evaluations else policy._evaluations[-1].snapshot.effective_acquisition_seconds if policy._evaluations[-1].snapshot else None,
        len(policy._evaluations), False, reason,
        {} if not policy._evaluations else dict(policy._evaluations[-1].to_public_dict().get("fusedEvidence", {})),
        {} if not policy._evaluations else dict(policy._evaluations[-1].eeg_scores),
        policy.fused_threshold, policy._evaluations[-1].margin if policy._evaluations else None,
        policy._evaluations[-1].consecutive_count if policy._evaluations else 0,
        {"policy": "M13 Context-aware Dynamic Stopping Baseline", "source": "M12 FusedTargetEvidence"},
    )


class DynamicStoppingPolicy:
    """Deterministic M13 v1 state machine."""

    def __init__(self, fused_threshold=M13_FUSED_EVIDENCE_THRESHOLD, margin_threshold=M13_MARGIN_THRESHOLD, required_consecutive=M13_REQUIRED_CONSECUTIVE):
        self.fused_threshold = float(fused_threshold)
        self.margin_threshold = float(margin_threshold)
        self.required_consecutive = int(required_consecutive)
        if self.fused_threshold != M13_FUSED_EVIDENCE_THRESHOLD or self.margin_threshold != M13_MARGIN_THRESHOLD or self.required_consecutive != M13_REQUIRED_CONSECUTIVE:
            raise DynamicStoppingInputError("M13 v1 uses its frozen engineering defaults; tuning is out of scope")
        self._evaluations = []
        self._candidate = None
        self._consecutive_count = 0
        self._decision = None
        self._invalid_seen = False

    @property
    def evaluations(self):
        return tuple(self._evaluations)

    @property
    def decision(self):
        return self._decision

    def _validate_index(self, window_index):
        expected = len(self._evaluations)
        if window_index != expected:
            raise DynamicStoppingInputError("windowIndex must be contiguous and start at zero")

    def observe(self, snapshot: DynamicStoppingSnapshot):
        if self._decision is not None:
            return self._evaluations[-1]
        if not isinstance(snapshot, DynamicStoppingSnapshot):
            raise DynamicStoppingInputError("observe requires a DynamicStoppingSnapshot")
        self._validate_index(snapshot.window_index)
        evidence = _validate_evidence(snapshot.fused_evidence)
        eeg_scores = {entry.candidate.logical_block_id: float(entry.eeg_evidence_score) for entry in evidence.entries}
        eeg_top = _argmax_ids(evidence.entries, lambda entry: entry.eeg_evidence_score)
        fused_top = tuple(evidence.top_logical_block_ids)
        if not fused_top:
            raise DynamicStoppingInputError("M12 evidence has no fused top target")
        fused_values = sorted((float(entry.fused_evidence) for entry in evidence.entries), reverse=True)
        margin = fused_values[0] - fused_values[1]
        threshold_pass = len(fused_top) == 1 and fused_values[0] >= self.fused_threshold
        margin_pass = len(fused_top) == 1 and margin >= self.margin_threshold
        eeg_confirmation_pass = len(fused_top) == 1 and len(eeg_top) == 1 and fused_top[0] == eeg_top[0]
        eligible = threshold_pass and margin_pass and eeg_confirmation_pass
        reason = "eligible_waiting_for_consecutive"
        if eligible:
            if self._candidate == fused_top[0]:
                self._consecutive_count += 1
            else:
                self._candidate = fused_top[0]
                self._consecutive_count = 1
            stop = self._consecutive_count >= self.required_consecutive
            reason = "early_stable_fused_evidence" if stop else "eligible_waiting_for_consecutive"
        else:
            self._candidate = None
            self._consecutive_count = 0
            stop = False
            if len(fused_top) > 1 or len(eeg_top) > 1:
                reason = "unresolved_tie"
            elif not eeg_confirmation_pass:
                reason = "eeg_confirmation_failed"
            elif not threshold_pass:
                reason = "fused_threshold_not_met"
            else:
                reason = "fused_margin_not_met"
        evaluation = WindowEvaluation(snapshot, snapshot.window_index, eeg_scores, eeg_top, fused_top, margin, threshold_pass, margin_pass, eeg_confirmation_pass, eligible, self._consecutive_count, stop, reason)
        self._evaluations.append(evaluation)
        if stop:
            self._decision = self._decision_from_evaluation(evaluation, True, "early_stable_fused_evidence")
        return evaluation

    def reject_invalid(self, window_index, effective_acquisition_seconds, reason="invalid_evidence"):
        if self._decision is not None:
            return self._evaluations[-1]
        self._validate_index(window_index)
        if not _finite_number(effective_acquisition_seconds) or effective_acquisition_seconds <= 0:
            raise DynamicStoppingInputError("invalid evidence needs a finite effective acquisition time")
        self._candidate = None
        self._consecutive_count = 0
        self._invalid_seen = True
        evaluation = WindowEvaluation(None, window_index, {}, (), (), None, False, False, False, False, 0, False, reason)
        self._evaluations.append(evaluation)
        return evaluation

    def _decision_from_evaluation(self, evaluation, early_stop, reason):
        evidence = evaluation.snapshot.fused_evidence
        entry = next(item for item in evidence.entries if item.candidate.logical_block_id == evaluation.fused_top_logical_block_ids[0])
        fused = {item.candidate.logical_block_id: item.fused_evidence for item in evidence.entries}
        raw = {item.candidate.logical_block_id: item.eeg_evidence_score for item in evidence.entries}
        return DynamicStoppingDecision(
            True, entry.candidate.logical_block_id, entry.candidate.target_id, entry.candidate.slot_index,
            evaluation.window_index, evaluation.snapshot.effective_acquisition_seconds, len(self._evaluations),
            early_stop, reason, fused, raw, self.fused_threshold, evaluation.margin,
            evaluation.consecutive_count,
            {"policy": "M13 Context-aware Dynamic Stopping Baseline", "source": "M12 FusedTargetEvidence", "identityAuthority": "slot_to_target_to_logical_mapping"},
        )

    def finalize(self):
        if self._decision is not None:
            return self._decision
        if not self._evaluations:
            self._decision = _empty_decision(self, "no_decision")
            return self._decision
        last = self._evaluations[-1]
        if self._invalid_seen or last.snapshot is None:
            self._decision = _empty_decision(self, "invalid_evidence")
            return self._decision
        if len(last.fused_top_logical_block_ids) != 1 or len(last.eeg_top_logical_block_ids) != 1:
            self._decision = _empty_decision(self, "no_decision_tie")
            return self._decision
        self._decision = self._decision_from_evaluation(last, False, "full_window_fallback")
        return self._decision


def run_dynamic_stopping(snapshots: Sequence[DynamicStoppingSnapshot]):
    policy = DynamicStoppingPolicy()
    for snapshot in snapshots:
        policy.observe(snapshot)
        if policy.decision is not None:
            break
    return policy, policy.finalize()


__all__ = [
    "M13_FUSED_EVIDENCE_THRESHOLD", "M13_MARGIN_THRESHOLD", "M13_REQUIRED_CONSECUTIVE",
    "M13_ONSET_GUARD_SECONDS", "M13_ONLINE_ANALYSIS_WINDOW_SECONDS", "M13_ONLINE_STEP_SECONDS",
    "M13_ONLINE_FIRST_EFFECTIVE_SECONDS", "WINDOW_GRID_SECONDS", "DynamicStoppingInputError",
    "DynamicStoppingSnapshot", "WindowEvaluation", "DynamicStoppingDecision", "DynamicStoppingPolicy",
    "run_dynamic_stopping",
]
