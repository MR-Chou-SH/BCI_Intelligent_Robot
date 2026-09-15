"""Descriptive M15 A/B/C comparison on the registered M6.5b result fixture.

Only recorded EEG score trajectories are historical.  The task context is the
same predeclared synthetic M11 overlay used by the existing M13 replay.  This
module never changes M12/M13 defaults and never presents the result as a human
context-aware experiment.
"""

import argparse
import json
from pathlib import Path

from integration.m11_context_prediction import make_observation, predict_context_prior
from integration.m12_context_eeg_fusion import fuse_fbcca_score_vector
from integration.m13_dynamic_stopping import DynamicStoppingPolicy, DynamicStoppingSnapshot
from integration.m13_historical_replay import CONTEXT_OVERLAY_HISTORIES, FREQUENCY_KEYS, HISTORICAL_LABEL_TO_SLOT
from integration.m13_trajectory_fixture import fixture_candidates
from integration.m15_comparative_benchmark import _condition_a, _condition_c, _condition_metrics, _full_window_m12


def run_historical_comparison(input_path):
    path = Path(input_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("recordType") != "m6_5b_continuous_pseudo_online" or document.get("backend") != "numpy_fbcca":
        raise ValueError("input is not the registered M6.5b NumPy FBCCA result fixture")
    candidates = fixture_candidates()
    trials = []
    ordinal = 0
    for session_name in ("A", "B1", "B2"):
        for source_trial in document.get("sessions", {}).get(session_name, {}).get("trials", []):
            history = CONTEXT_OVERLAY_HISTORIES[ordinal % len(CONTEXT_OVERLAY_HISTORIES)]
            prior = predict_context_prior(make_observation(history))
            snapshots = []
            raw_trajectory = []
            for index, row in enumerate(source_trial.get("predictionSequence", [])):
                raw_scores = {candidate.logical_block_id: float(row["candidateScores"][key]) for candidate, key in zip(candidates, FREQUENCY_KEYS)}
                raw_trajectory.append(raw_scores)
                fused = fuse_fbcca_score_vector(prior, candidates, [raw_scores[item.logical_block_id] for item in candidates])
                snapshots.append(DynamicStoppingSnapshot.from_m12(index, fused, 1.5, float(row["relativeToStimulusStartSeconds"]), {"sourceType": "HISTORICAL_REPLAY", "session": session_name, "trialId": source_trial.get("trialId", ""), "contextOverlayHistory": ",".join(history)}))
            if not snapshots:
                policy = DynamicStoppingPolicy()
                policy.reject_invalid(0, 0.5, "invalid_evidence")
                condition_c = {"decisionMade": False, "finalTarget": None, "stopWindow": 0, "effectiveAcquisitionSeconds": 0.5, "earlyStop": False, "fallback": False, "noDecision": True, "reason": "invalid_evidence"}
                condition_b = condition_c
                condition_a = condition_c
            else:
                condition_a = _condition_a(raw_trajectory)
                condition_b = _full_window_m12(snapshots[-1])
                condition_c = _condition_c(tuple(snapshots))
            label = source_trial.get("groundTruthLabel")
            slot = HISTORICAL_LABEL_TO_SLOT.get(label)
            expected = candidates[slot].logical_block_id if slot is not None else None
            trials.append({
                "trialId": "historical-{}-{}".format(session_name, source_trial.get("trialId", ordinal)),
                "taskId": "historical-{}".format(session_name),
                "stepIndex": ordinal,
                "expectedLogicalBlockId": expected,
                "recordedGroundTruthLabel": label,
                "groundTruthUsedForContext": False,
                "contextOverlayHistory": list(history),
                "contextPrior": prior.to_public_dict(),
                "conditions": {
                    "A_EEG_FULL_WINDOW": condition_a,
                    "B_CONTEXT_EEG_FULL_WINDOW": condition_b,
                    "C_CONTEXT_EEG_DYNAMIC_STOP": condition_c,
                },
                "provenance": {"eeg": "recorded historical M6.5b NumPy FBCCA scores", "context": "synthetic predeclared M11 overlay", "inputPath": str(path)},
            })
            ordinal += 1
    summary = {
        "schemaVersion": 1,
        "recordType": "m15_historical_replay_comparison",
        "status": "PASS",
        "historicalReplay": "YES",
        "inputPath": str(path),
        "inputBackend": document.get("backend"),
        "trialCount": len(trials),
        "sessionTrialCounts": {name: len(document.get("sessions", {}).get(name, {}).get("trials", [])) for name in ("A", "B1", "B2")},
        "conditionMetrics": {condition: _condition_metrics(trials, condition) for condition in ("A_EEG_FULL_WINDOW", "B_CONTEXT_EEG_FULL_WINDOW", "C_CONTEXT_EEG_DYNAMIC_STOP")},
        "limitations": ["historical recorded EEG plus synthetic context overlay", "not a context-aware human experiment", "descriptive only; no tuning or statistical claim", "no live latency claim"],
    }
    return summary, trials


def write_outputs(input_path, output_dir):
    summary, trials = run_historical_comparison(input_path)
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (root / "trials.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in trials), encoding="utf-8")
    return summary, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    summary, _trials = write_outputs(args.input, args.output_dir)
    print(json.dumps({"status": summary["status"], "trialCount": summary["trialCount"], "outputDir": str(args.output_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run_historical_comparison", "write_outputs"]
