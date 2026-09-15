"""Read-only M13 replay over the existing M6.5b continuous result fixture.

The recorded EEG score trajectory is historical SSVEP evidence.  Context is a
deterministic synthetic overlay chosen from observable, predeclared M11
histories; it is not a context-aware human experiment.
"""

import argparse
import json
import math
from pathlib import Path

from integration.m11_context_prediction import make_observation, predict_context_prior
from integration.m12_context_eeg_fusion import fuse_fbcca_score_vector
from integration.m13_dynamic_stopping import DynamicStoppingSnapshot, DynamicStoppingPolicy
from integration.m13_trajectory_fixture import fixture_candidates


HISTORICAL_LABEL_TO_SLOT = {"target_left": 0, "target_center": 1, "target_right": 2}
CONTEXT_OVERLAY_HISTORIES = ((), ("block_sim_01",))
FREQUENCY_KEYS = ("7.2", "9.0", "12.0")


def _top(values):
    maximum = max(values.values())
    return tuple(key for key, value in values.items() if math.isclose(float(value), maximum, rel_tol=0.0, abs_tol=1e-12))


def _context_for_trial(ordinal):
    history = CONTEXT_OVERLAY_HISTORIES[ordinal % len(CONTEXT_OVERLAY_HISTORIES)]
    prior = predict_context_prior(make_observation(history))
    if not prior.valid or prior.terminal:
        raise ValueError("predeclared synthetic context overlay produced an invalid M11 prior")
    return history, prior


def _replay_trial(trial, ordinal, candidates):
    history, context_prior = _context_for_trial(ordinal)
    policy = DynamicStoppingPolicy()
    windows = []
    prediction_sequence = trial.get("predictionSequence")
    if not isinstance(prediction_sequence, list) or not prediction_sequence:
        policy.reject_invalid(0, 0.5, "invalid_evidence")
    else:
        for index, row in enumerate(prediction_sequence):
            try:
                if row.get("predictionIndex") != index:
                    raise ValueError("predictionIndex is not contiguous")
                score_map = row["candidateScores"]
                scores = [float(score_map[key]) for key in FREQUENCY_KEYS]
                if any(not math.isfinite(value) or value < 0.0 for value in scores):
                    raise ValueError("candidate score is not finite and nonnegative")
                effective_time = float(row["relativeToStimulusStartSeconds"])
                evidence = fuse_fbcca_score_vector(context_prior, candidates, scores)
                snapshot = DynamicStoppingSnapshot.from_m12(
                    index,
                    evidence,
                    analysis_window_seconds=1.5,
                    effective_acquisition_seconds=effective_time,
                    provenance={"source": "historical M6.5b result", "sessionId": trial.get("sessionId", ""), "trialId": trial.get("trialId", ""), "contextOverlayHistory": ",".join(history)},
                )
                policy.observe(snapshot)
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                policy.reject_invalid(index, float(row.get("relativeToStimulusStartSeconds", 0.5)), "invalid_evidence")
                break
            if policy.decision is not None:
                break

    decision = policy.finalize()
    final_row = prediction_sequence[-1] if isinstance(prediction_sequence, list) and prediction_sequence else {}
    final_scores = final_row.get("candidateScores", {}) if isinstance(final_row, dict) else {}
    raw_by_logical = {candidate.logical_block_id: float(final_scores[key]) for candidate, key in zip(candidates, FREQUENCY_KEYS) if key in final_scores}
    raw_top = _top(raw_by_logical) if raw_by_logical else ()
    fused_top = ()
    if prediction_sequence:
        try:
            final_evidence = fuse_fbcca_score_vector(context_prior, candidates, [float(final_scores[key]) for key in FREQUENCY_KEYS])
            fused_top = final_evidence.top_logical_block_ids
        except (KeyError, TypeError, ValueError):
            pass
    return {
        "sessionId": trial.get("sessionId"),
        "trialId": trial.get("trialId"),
        "groundTruthLabel": trial.get("groundTruthLabel"),
        "groundTruthUsedForContext": False,
        "contextOverlayHistory": list(history),
        "windows": [item.to_public_dict() for item in policy.evaluations],
        "decision": decision.to_public_dict(),
        "referenceEEGFullWindowTopLogicalBlockIds": list(raw_top),
        "referenceM12FullWindowTopLogicalBlockIds": list(fused_top),
        "sourceRecordType": "m6_5b_continuous_pseudo_online",
    }


def run_replay(input_path):
    path = Path(input_path)
    with path.open("r", encoding="utf-8") as source:
        document = json.load(source)
    if document.get("recordType") != "m6_5b_continuous_pseudo_online":
        raise ValueError("historical fixture recordType is not the expected M6.5b result")
    if document.get("backend") != "numpy_fbcca":
        raise ValueError("historical fixture backend is not the expected pure NumPy FBCCA backend")
    if float(document.get("stepSeconds")) != 0.2 or float(document.get("stimulationSeconds")) != 4.0:
        raise ValueError("historical fixture timing does not match the existing M6.5b semantics")
    candidates = fixture_candidates()
    records = []
    sessions = document.get("sessions", {})
    for session_name in ("A", "B1", "B2"):
        for trial in sessions.get(session_name, {}).get("trials", []):
            records.append(_replay_trial(trial, len(records), candidates))

    early = [record for record in records if record["decision"]["earlyStop"]]
    stop_times = [record["decision"]["effectiveAcquisitionSeconds"] for record in early if record["decision"]["effectiveAcquisitionSeconds"] is not None]
    windows_consumed = [record["decision"]["evaluatedWindows"] for record in records if record["decision"]["evaluatedWindows"]]
    m12_agreements = [record for record in records if record["decision"]["selectedLogicalBlockId"] is not None and record["referenceM12FullWindowTopLogicalBlockIds"] and record["decision"]["selectedLogicalBlockId"] == record["referenceM12FullWindowTopLogicalBlockIds"][0]]
    eeg_agreements = [record for record in records if record["decision"]["selectedLogicalBlockId"] is not None and record["referenceEEGFullWindowTopLogicalBlockIds"] and record["decision"]["selectedLogicalBlockId"] == record["referenceEEGFullWindowTopLogicalBlockIds"][0]]
    descriptive_accuracy = []
    for record in records:
        label = record.get("groundTruthLabel")
        slot = HISTORICAL_LABEL_TO_SLOT.get(label)
        if slot is None or record["decision"]["selectedSlotIndex"] is None:
            continue
        descriptive_accuracy.append(record["decision"]["selectedSlotIndex"] == slot)
    reasons = {}
    for record in records:
        reason = record["decision"]["stopReason"]
        reasons[reason] = reasons.get(reason, 0) + 1
    summary = {
        "schemaVersion": 1,
        "recordType": "m13_historical_m6_5b_replay_summary",
        "status": "PASS",
        "historicalReplay": "YES",
        "inputPath": str(path),
        "inputRecordType": document["recordType"],
        "inputBackend": document["backend"],
        "trialCount": len(records),
        "sessionTrialCounts": {name: len(sessions.get(name, {}).get("trials", [])) for name in ("A", "B1", "B2")},
        "earlyStopCount": len(early),
        "earlyStopRate": len(early) / len(records) if records else 0.0,
        "meanEarlyStopWindowSeconds": sum(stop_times) / len(stop_times) if stop_times else None,
        "medianEarlyStopWindowSeconds": sorted(stop_times)[len(stop_times) // 2] if stop_times else None,
        "meanWindowsConsumed": sum(windows_consumed) / len(windows_consumed) if windows_consumed else None,
        "medianWindowsConsumed": sorted(windows_consumed)[len(windows_consumed) // 2] if windows_consumed else None,
        "agreementWithM12FullWindowTargetCount": len(m12_agreements),
        "agreementWithM12FullWindowTargetRate": len(m12_agreements) / len(records) if records else 0.0,
        "agreementWithEegFullWindowTargetCount": len(eeg_agreements),
        "agreementWithEegFullWindowTargetRate": len(eeg_agreements) / len(records) if records else 0.0,
        "noDecisionCount": sum(1 for record in records if not record["decision"]["decisionMade"]),
        "stopReasons": reasons,
        "descriptiveAccuracyAgainstRecordedClass": sum(descriptive_accuracy) / len(descriptive_accuracy) if descriptive_accuracy else None,
        "mapping": {"target_left": "slot0->block_sim_01", "target_center": "slot1->block_sim_02", "target_right": "slot2->block_sim_03"},
        "contextOverlayRule": "cycle only predeclared observable M11 histories (), (block_sim_01); groundTruthLabel is excluded from context input",
        "limitations": ["historical recorded EEG score trajectory plus deterministic synthetic context overlay", "not a context-aware human experiment", "descriptive replay only; no threshold, lambda, or decoder tuning", "no live latency or generalized accuracy claim"],
    }
    return summary, records


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--summary-path", required=True)
    args = parser.parse_args(argv)
    summary, records = run_replay(args.input)
    evidence_path = Path(args.evidence_path)
    summary_path = Path(args.summary_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "trialCount": summary["trialCount"], "earlyStopCount": summary["earlyStopCount"], "summaryPath": str(summary_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
