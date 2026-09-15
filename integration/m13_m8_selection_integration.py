"""Thin opt-in adapter from M13 decisions to the existing M8 seam.

This module does not speak Quest, derive TargetIds, or call a robot adapter.
It only converts a confirmed M13 slot result into the already frozen M8 final
decision vocabulary and delegates to ``submit_final_decision``.
"""

from integration.m8_selection_orchestration import M6_FINAL_LABEL_TO_CANONICAL_CLASS
from integration.m13_dynamic_stopping import DynamicStoppingDecision


M13_SLOT_TO_FINAL_LABEL = ("target_left", "target_center", "target_right")


def dynamic_stopping_decision_to_m8_final_decision(decision, trial_id, session_id=None):
    if not isinstance(decision, DynamicStoppingDecision):
        raise TypeError("decision must be a DynamicStoppingDecision")
    result = {
        "trialId": trial_id,
        "sessionId": session_id,
        "stabilizer": "m13_2_consecutive_fused_evidence",
        "decisionMade": bool(decision.decision_made),
        "finalDecisionLabel": None,
        "decisionPredictionIndex": decision.stop_window,
        "decisionRelativeTimeSeconds": decision.effective_acquisition_seconds,
        "reason": decision.stop_reason,
        "m13StopReason": decision.stop_reason,
        "m13SelectedLogicalBlockId": decision.selected_logical_block_id,
        "m13SelectedTargetId": decision.selected_target_id,
        "m13EvidenceProvenance": dict(decision.provenance),
    }
    if not decision.decision_made:
        return result
    slot = decision.selected_slot_index
    if not isinstance(slot, int) or slot < 0 or slot >= len(M13_SLOT_TO_FINAL_LABEL):
        raise ValueError("a made M13 decision must carry a valid frozen slot index")
    label = M13_SLOT_TO_FINAL_LABEL[slot]
    if M6_FINAL_LABEL_TO_CANONICAL_CLASS.get(label) != slot:
        raise ValueError("M13 slot mapping no longer matches the frozen M8 vocabulary")
    result["finalDecisionLabel"] = label
    return result


def submit_dynamic_stopping_decision(selection_orchestrator, trial_id, decision, session_id=None):
    """Delegate exactly once to the existing M8 final-decision lifecycle."""
    final_decision = dynamic_stopping_decision_to_m8_final_decision(decision, trial_id, session_id)
    return selection_orchestrator.submit_final_decision(final_decision)


__all__ = ["M13_SLOT_TO_FINAL_LABEL", "dynamic_stopping_decision_to_m8_final_decision", "submit_dynamic_stopping_decision"]
