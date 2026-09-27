"""M12 context-prior × EEG-evidence fusion baseline.

This module consumes an M11 :class:`ContextPrior` and an existing three-slot
M6/FBCCA score vector.  It adds no decoder, filtering, calibration, transport,
or robot behavior.
"""

from dataclasses import dataclass
import math
from typing import Iterable, Mapping, Optional, Sequence, Tuple

from integration.m11_context_prediction import ContextPrior
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


ACTIVE_SLOT_FREQUENCIES_HZ = (7.2, 9.0, 12.0)
LAMBDA_CONTEXT = 0.5
EEG_SCORE_EPSILON = 1e-12
# Raw FBCCA evidence is not calibrated as a probability.  This is therefore
# deliberately a simple engineering margin gate, not a decoder threshold.
# The value reuses the frozen M13 margin scale and is validated by sensitivity
# analysis against the recorded Golden evidence.
STRONG_EEG_MARGIN_THRESHOLD = 0.20


class FusionInputError(ValueError):
    """The replay input cannot be safely interpreted under the frozen contract."""


@dataclass(frozen=True)
class ActiveSsvepCandidate:
    slot_index: int
    target_id: str
    logical_block_id: str
    nominal_frequency_hz: float

    @classmethod
    def from_frozen_mapping(cls, slot_index, target_id, mapping=None):
        mapping = load_virtual_block_target_mapping() if mapping is None else mapping
        if not isinstance(slot_index, int) or isinstance(slot_index, bool):
            raise FusionInputError("slotIndex must be an integer")
        if slot_index not in range(len(ACTIVE_SLOT_FREQUENCIES_HZ)):
            raise FusionInputError("slotIndex must be one of the three frozen active slots")
        if not isinstance(target_id, str) or not target_id:
            raise FusionInputError("targetId must be a non-empty string")
        try:
            logical_block_id = mapping[target_id]
        except KeyError as error:
            raise FusionInputError("targetId is absent from the explicit virtual mapping") from error
        return cls(slot_index, target_id, logical_block_id, ACTIVE_SLOT_FREQUENCIES_HZ[slot_index])

    def to_public_dict(self):
        return {
            "slotIndex": self.slot_index,
            "targetId": self.target_id,
            "logicalBlockId": self.logical_block_id,
            "nominalFrequencyHz": self.nominal_frequency_hz,
        }


@dataclass(frozen=True)
class FusedEvidenceEntry:
    candidate: ActiveSsvepCandidate
    eeg_evidence_score: float
    prepared_eeg_evidence_score: float
    context_prior_global: float
    context_prior_active: float
    context_prior_soft: float
    fused_evidence: float

    def to_public_dict(self):
        result = self.candidate.to_public_dict()
        result.update({
            "eegEvidenceScore": self.eeg_evidence_score,
            "preparedEegEvidenceScore": self.prepared_eeg_evidence_score,
            "contextPriorGlobal": self.context_prior_global,
            "contextPriorActive": self.context_prior_active,
            "contextPriorSoft": self.context_prior_soft,
            "normalizedFusedEvidence": self.fused_evidence,
        })
        return result


@dataclass(frozen=True)
class FusedTargetEvidence:
    entries: Tuple[FusedEvidenceEntry, ...]
    top_logical_block_ids: Tuple[str, ...]
    top_target_ids: Tuple[str, ...]
    tie: bool
    raw_eeg_top_logical_block_ids: Tuple[str, ...]
    eeg_confidence: float
    eeg_margin: float
    is_strong_eeg: bool
    context_preferred_logical_block_ids: Tuple[str, ...]
    context_strength: float
    fusion_mode: str
    override_reason: Optional[str]

    def to_public_dict(self):
        final_prediction = None if self.tie else self.top_logical_block_ids[0]
        raw_prediction = None if len(self.raw_eeg_top_logical_block_ids) != 1 else self.raw_eeg_top_logical_block_ids[0]
        context_prediction = None if len(self.context_preferred_logical_block_ids) != 1 else self.context_preferred_logical_block_ids[0]
        return {
            "entries": [entry.to_public_dict() for entry in self.entries],
            "topLogicalBlockIds": list(self.top_logical_block_ids),
            "topTargetIds": list(self.top_target_ids),
            "tie": self.tie,
            "rawEegTopLogicalBlockIds": list(self.raw_eeg_top_logical_block_ids),
            "rawEegPrediction": raw_prediction,
            "eegConfidence": self.eeg_confidence,
            "eegMargin": self.eeg_margin,
            "isStrongEeg": self.is_strong_eeg,
            "contextPreferredClass": context_prediction,
            "contextPreferredLogicalBlockIds": list(self.context_preferred_logical_block_ids),
            "contextStrength": self.context_strength,
            "finalPrediction": final_prediction,
            "fusionMode": self.fusion_mode,
            "overrideReason": self.override_reason,
        }


def _validate_candidates(active_candidates: Iterable[ActiveSsvepCandidate]):
    candidates = tuple(active_candidates)
    if len(candidates) != len(ACTIVE_SLOT_FREQUENCIES_HZ):
        raise FusionInputError("exactly three active SSVEP candidates are required")
    if any(not isinstance(item, ActiveSsvepCandidate) for item in candidates):
        raise FusionInputError("active candidates must use the frozen candidate contract")
    if {item.slot_index for item in candidates} != {0, 1, 2}:
        raise FusionInputError("active candidates must contain slots 0, 1 and 2 exactly once")
    if len({item.target_id for item in candidates}) != len(candidates):
        raise FusionInputError("active target IDs must be unique")
    if len({item.logical_block_id for item in candidates}) != len(candidates):
        raise FusionInputError("active logical block IDs must be unique")
    for item in candidates:
        if item.nominal_frequency_hz != ACTIVE_SLOT_FREQUENCIES_HZ[item.slot_index]:
            raise FusionInputError("active slot frequency does not match the frozen mapping")
    return tuple(sorted(candidates, key=lambda item: item.slot_index))


def _validate_context_prior(context_prior: ContextPrior):
    if not isinstance(context_prior, ContextPrior):
        raise FusionInputError("contextPrior must be an M11 ContextPrior")
    if not context_prior.valid or context_prior.terminal:
        raise FusionInputError("contextPrior must be a valid nonterminal M11 prior")
    probabilities = context_prior.probability_map()
    if any(not isinstance(key, str) for key in probabilities):
        raise FusionInputError("contextPrior logical IDs must be strings")
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        or not math.isfinite(float(value)) or float(value) < 0.0
        for value in probabilities.values()
    ):
        raise FusionInputError("contextPrior probabilities must be finite and nonnegative")
    if not probabilities or not math.isclose(sum(probabilities.values()), 1.0, abs_tol=1e-12):
        raise FusionInputError("contextPrior probabilities must sum to one")
    return probabilities


def _validate_scores(active_candidates, eeg_scores_by_logical_block_id):
    if not isinstance(eeg_scores_by_logical_block_id, Mapping):
        raise FusionInputError("EEGEvidenceScore input must be a logical-ID mapping")
    expected = {item.logical_block_id for item in active_candidates}
    if set(eeg_scores_by_logical_block_id) != expected:
        raise FusionInputError("EEGEvidenceScore keys must match the active logical IDs exactly")
    scores = {}
    for logical_block_id in sorted(expected):
        value = eeg_scores_by_logical_block_id[logical_block_id]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FusionInputError("EEGEvidenceScore values must be numeric")
        value = float(value)
        if not math.isfinite(value) or value < 0.0:
            raise FusionInputError("EEGEvidenceScore values must be finite and nonnegative")
        scores[logical_block_id] = value
    return scores


def fuse_context_and_eeg(
    context_prior,
    active_candidates,
    eeg_scores_by_logical_block_id,
    *,
    strong_eeg_margin_threshold=STRONG_EEG_MARGIN_THRESHOLD,
):
    """Fuse an M11 context prior with nonnegative, uncalibrated EEG evidence."""
    candidates = _validate_candidates(active_candidates)
    raw_scores = _validate_scores(candidates, eeg_scores_by_logical_block_id)

    if isinstance(strong_eeg_margin_threshold, bool) or not isinstance(strong_eeg_margin_threshold, (int, float)):
        raise FusionInputError("strongEegMarginThreshold must be numeric")
    strong_eeg_margin_threshold = float(strong_eeg_margin_threshold)
    if not math.isfinite(strong_eeg_margin_threshold) or strong_eeg_margin_threshold < 0.0:
        raise FusionInputError("strongEegMarginThreshold must be finite and nonnegative")

    context_available = context_prior is not None
    global_context = _validate_context_prior(context_prior) if context_available else {
        item.logical_block_id: 1.0 / len(candidates) for item in candidates
    }

    raw_values = sorted(raw_scores.values(), reverse=True)
    raw_maximum = raw_values[0]
    raw_top = tuple(
        item.logical_block_id
        for item in candidates
        if math.isclose(raw_scores[item.logical_block_id], raw_maximum, rel_tol=0.0, abs_tol=1e-12)
    )
    raw_margin = raw_values[0] - raw_values[1]
    raw_total = sum(raw_values)
    raw_confidence = raw_maximum / raw_total if raw_total > 0.0 else 1.0 / len(candidates)
    is_strong_eeg = len(raw_top) == 1 and raw_margin >= strong_eeg_margin_threshold

    active_mass = sum(global_context.get(item.logical_block_id, 0.0) for item in candidates)
    if active_mass > 0.0:
        active_context = {
            item.logical_block_id: global_context.get(item.logical_block_id, 0.0) / active_mass
            for item in candidates
        }
    else:
        active_context = {item.logical_block_id: 1.0 / len(candidates) for item in candidates}
    context_values = sorted(active_context.values(), reverse=True)
    context_maximum = context_values[0]
    context_preferred = tuple(
        item.logical_block_id
        for item in candidates
        if math.isclose(active_context[item.logical_block_id], context_maximum, rel_tol=0.0, abs_tol=1e-12)
    )
    context_strength = context_values[0] - context_values[1]
    uniform = 1.0 / len(candidates)
    softened_context = {
        item.logical_block_id: (1.0 - LAMBDA_CONTEXT) * uniform
        + LAMBDA_CONTEXT * active_context[item.logical_block_id]
        for item in candidates
    }
    prepared_scores = {
        item.logical_block_id: max(raw_scores[item.logical_block_id], EEG_SCORE_EPSILON)
        for item in candidates
    }
    context_unnormalized = {
        item.logical_block_id: softened_context[item.logical_block_id]
        * prepared_scores[item.logical_block_id]
        for item in candidates
    }
    context_total = sum(context_unnormalized.values())
    context_fused = {logical_id: value / context_total for logical_id, value in context_unnormalized.items()}
    if is_strong_eeg:
        # Context remains recorded as evidence, but cannot change a uniquely
        # strong raw EEG ordering.  This is a policy gate, not a decoder edit.
        total = sum(prepared_scores.values())
        fused = {logical_id: value / total for logical_id, value in prepared_scores.items()}
        fusion_mode = "eeg_strong_override"
        override_reason = "unique_raw_eeg_top_margin_gte_{:.2f}".format(strong_eeg_margin_threshold)
    else:
        fused = context_fused
        if not context_available or context_strength <= 1e-12:
            fusion_mode = "context_neutral"
            override_reason = None
        elif len(raw_top) == 1 and len(context_preferred) == 1 and raw_top[0] != context_preferred[0]:
            fusion_mode = "context_conflict_weak_eeg"
            override_reason = "context_prior_allowed_for_non_strong_eeg"
        else:
            fusion_mode = "context_assisted"
            override_reason = None
    maximum = max(fused.values())
    top_logical = tuple(
        item.logical_block_id
        for item in candidates
        if math.isclose(fused[item.logical_block_id], maximum, rel_tol=0.0, abs_tol=1e-12)
    )
    entries = tuple(
        FusedEvidenceEntry(
            candidate=item,
            eeg_evidence_score=raw_scores[item.logical_block_id],
            prepared_eeg_evidence_score=prepared_scores[item.logical_block_id],
            context_prior_global=global_context.get(item.logical_block_id, 0.0),
            context_prior_active=active_context[item.logical_block_id],
            context_prior_soft=softened_context[item.logical_block_id],
            fused_evidence=fused[item.logical_block_id],
        )
        for item in candidates
    )
    target_by_logical = {item.logical_block_id: item.target_id for item in candidates}
    return FusedTargetEvidence(
        entries=entries,
        top_logical_block_ids=top_logical,
        top_target_ids=tuple(target_by_logical[item] for item in top_logical),
        tie=len(top_logical) > 1,
        raw_eeg_top_logical_block_ids=raw_top,
        eeg_confidence=raw_confidence,
        eeg_margin=raw_margin,
        is_strong_eeg=is_strong_eeg,
        context_preferred_logical_block_ids=context_preferred,
        context_strength=context_strength,
        fusion_mode=fusion_mode,
        override_reason=override_reason,
    )


def fuse_fbcca_score_vector(context_prior, active_candidates, fused_score_vector: Sequence[float]):
    """Read-only adapter for the existing ``predict_fbcca`` fused score vector."""
    candidates = _validate_candidates(active_candidates)
    if not isinstance(fused_score_vector, Sequence) or isinstance(fused_score_vector, (str, bytes)):
        raise FusionInputError("FBCCA fused score vector must be a three-value sequence")
    if len(fused_score_vector) != len(candidates):
        raise FusionInputError("FBCCA fused score vector must contain one value per active slot")
    by_logical = {
        item.logical_block_id: fused_score_vector[item.slot_index]
        for item in candidates
    }
    return fuse_context_and_eeg(context_prior, candidates, by_logical)


__all__ = [
    "ACTIVE_SLOT_FREQUENCIES_HZ",
    "EEG_SCORE_EPSILON",
    "STRONG_EEG_MARGIN_THRESHOLD",
    "FusionInputError",
    "ActiveSsvepCandidate",
    "FusedEvidenceEntry",
    "FusedTargetEvidence",
    "fuse_context_and_eeg",
    "fuse_fbcca_score_vector",
]
