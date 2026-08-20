"""M6.7a pure formal-online admission, planning, validity, replacement and evaluation rules."""
from collections import Counter
from dataclasses import dataclass
import math
import time
import numpy as np

from eeg.dataset_acquisition.plan import generate_trial_plan
from .pseudo_online import DecoderBackend

CANDIDATE_CHANNELS = (2, 3, 4, 5, 7)
CHANNEL_RULE_VERSION = "m6_7a_channel_admission_v1"
FORMAL_MINIMUM_USABLE = 4
RAIL_VALUE = 375000.0
RAIL_FRACTION_LIMIT = 0.95


def channel_admission(samples, continuity_statuses=(), git_commit="unavailable", utc="unavailable"):
    """Pre-plan quality gate. ``samples`` is all eight SDK channels by sample."""
    values = np.asarray(samples, dtype=float)
    if values.ndim != 2 or values.shape[0] != 8: raise ValueError("expected 8 by N samples")
    continuity_ok = all(value in ("continuous", "anomaly") for value in continuity_statuses)
    records, selected, rejected = [], [], []
    for channel in CANDIDATE_CHANNELS:
        row = values[channel]; finite = bool(np.isfinite(row).all())
        std = float(np.std(row)) if finite else float("nan")
        rail_fraction = float(np.mean(np.isclose(np.abs(row), RAIL_VALUE, atol=1.0))) if finite else 1.0
        reasons = []
        if not finite: reasons.append("nonfinite")
        if finite and std <= 1e-9: reasons.append("constant")
        if rail_fraction >= RAIL_FRACTION_LIMIT: reasons.append("near_rail_or_placeholder")
        if not continuity_ok: reasons.append("gross_continuity_failure")
        usable = not reasons
        record = {"channel": channel, "usable": usable, "reasons": reasons, "finite": finite,
                  "standardDeviation": std, "railFraction": rail_fraction}
        records.append(record)
        (selected if usable else rejected).append(record)
    verdict = "READY" if len(selected) >= FORMAL_MINIMUM_USABLE else "CHANNEL CHECK FAILED"
    return {"recordType": "m6_7_channel_admission", "ruleVersion": CHANNEL_RULE_VERSION, "createdUtc": utc,
            "gitCommit": git_commit, "candidateChannels": list(CANDIDATE_CHANNELS), "channels": records,
            "selectedChannels": [item["channel"] for item in selected], "selectedChannelCount": len(selected),
            "rejectedChannels": rejected, "verdict": verdict, "frozen": verdict == "READY"}


def synthetic_warmup(selected_channels):
    """No EEG/label input: initialize NumPy FBCCA before a real plan is broadcast."""
    if len(selected_channels) < FORMAL_MINIMUM_USABLE: raise ValueError("warmup requires admitted channels")
    rng = np.random.default_rng(6701); epoch = rng.standard_normal((len(selected_channels), 1500))
    started = time.perf_counter_ns(); DecoderBackend("numpy_fbcca").predict(epoch)
    return {"warmupCompleted": True, "warmupComputeDurationNs": time.perf_counter_ns() - started,
            "usesRealEeg": False, "usesGroundTruth": False}


def generate_online_plan(session_id, seed, session_type):
    if session_type not in ("pilot_online", "formal_online"): raise ValueError("invalid session type")
    per_class = 3 if session_type == "pilot_online" else 10
    items = generate_trial_plan(session_id, seed, trials_per_class=per_class, maximum_consecutive=2,
                                trial_prefix="{}-trial".format(session_type))
    return {"sessionId": session_id, "planMode": "formal", "sessionType": session_type, "randomSeed": int(seed),
            "trials": [item.to_dict() for item in items]}


TECHNICAL_REASONS = {"invalid_association", "continuity_loss", "stale_sync", "nd8_disconnect",
                     "quest_disconnect", "malformed_or_missing_event", "missing_raw_eeg",
                     "missing_prediction_evidence", "decoder_exception"}


def technical_validity(trial):
    reason = trial.get("technicalReason")
    if reason in TECHNICAL_REASONS: return {"trialId": trial["trialId"], "technicalStatus": "technical_invalid", "reason": reason}
    if not trial.get("startAssociationValid") or not trial.get("stopAssociationValid"):
        return {"trialId": trial["trialId"], "technicalStatus": "technical_invalid", "reason": "invalid_association"}
    if trial.get("continuityCrossesRequiredWindow") or trial.get("staleSync"):
        return {"trialId": trial["trialId"], "technicalStatus": "technical_invalid", "reason": "continuity_loss" if trial.get("continuityCrossesRequiredWindow") else "stale_sync"}
    if trial.get("decoderException") or trial.get("missingRawEeg") or trial.get("missingStimulusEvent"):
        return {"trialId": trial["trialId"], "technicalStatus": "technical_invalid", "reason": "decoder_exception" if trial.get("decoderException") else "missing_raw_eeg" if trial.get("missingRawEeg") else "malformed_or_missing_event"}
    return {"trialId": trial["trialId"], "technicalStatus": "technical_valid", "reason": None}


def replacements(planned_trials, validity_records, maximum=3):
    invalid = [record for record in validity_records if record["technicalStatus"] == "technical_invalid"]
    if len(invalid) > maximum: return {"abort": True, "reason": "replacement_limit_exceeded", "records": []}
    source = {item["trialId"]: item for item in planned_trials}; records = []
    for index, invalid_record in enumerate(invalid, 1):
        original = source[invalid_record["trialId"]]
        records.append({"originalTrialId": original["trialId"], "originalTarget": original["targetId"],
                        "invalidReason": invalid_record["reason"], "replacementTrialId": "{}-replacement-{:03d}".format(original["sessionId"], index),
                        "replacementTarget": original["targetId"], "replacementOrder": index})
    return {"abort": False, "records": records}


def evaluate_formal(planned_trials, outcomes):
    truth = {item["trialId"]: item["targetId"] for item in planned_trials}; labels = ("target_left", "target_center", "target_right")
    per = {label: Counter(planned=0, technical_valid=0, technical_invalid=0, decisions=0, correct=0, incorrect=0, no_decision=0) for label in labels}
    matrix = {actual: {predicted: 0 for predicted in labels} for actual in labels}; totals = Counter(planned=len(planned_trials))
    for trial_id, target in truth.items(): per[target]["planned"] += 1
    for outcome in outcomes:
        target = truth[outcome["trialId"]]; valid = technical_validity(outcome)["technicalStatus"] == "technical_valid"
        if not valid: per[target]["technical_invalid"] += 1; totals["technical_invalid"] += 1; continue
        per[target]["technical_valid"] += 1; totals["technical_valid"] += 1
        predicted = outcome.get("finalDecisionLabel")
        if predicted is None: per[target]["no_decision"] += 1; totals["no_decision"] += 1; continue
        per[target]["decisions"] += 1; totals["decisions"] += 1; matrix[target][predicted] += 1
        key = "correct" if predicted == target else "incorrect"; per[target][key] += 1; totals[key] += 1
    valid = totals["technical_valid"]; decisions = totals["decisions"]
    return {"planned": totals["planned"], "technicalValid": valid, "technicalInvalid": totals["technical_invalid"],
            "decisions": decisions, "correct": totals["correct"], "incorrect": totals["incorrect"], "noDecision": totals["no_decision"],
            "decisionCoverage": decisions / valid if valid else None, "accuracy": totals["correct"] / valid if valid else None,
            "accuracyAmongDecisions": totals["correct"] / decisions if decisions else None,
            "perClass": {key: dict(value) for key, value in per.items()}, "confusionMatrix": matrix}
