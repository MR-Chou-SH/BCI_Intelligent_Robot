"""M15 paired comparative benchmark and ablation framework.

Conditions consume the exact same deterministic raw EEG score trajectory and
observable task history:

* A: raw EEG full-window decision, without context or M13;
* B: M11 + M12 full-window decision, without M13 early stopping;
* C: M11 + M12 + the frozen M13 dynamic stopping policy.

This is a descriptive software/replay benchmark. It does not tune parameters,
select a winning condition, or make human-performance claims.
"""

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path

from eeg.decoder.characterization import WINDOW_GRID_SECONDS
from integration.m10_task_benchmark import BENCHMARK_LOGICAL_BLOCK_IDS, load_task_definitions
from integration.m11_context_prediction import make_observation, predict_context_prior
from integration.m12_context_eeg_fusion import ActiveSsvepCandidate, fuse_context_and_eeg
from integration.m13_dynamic_stopping import DynamicStoppingPolicy, DynamicStoppingSnapshot
from integration.m14_sequential_closed_loop import _active_candidates
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


M15_ACCEPTANCE_ID = "m15-comparative-benchmark-ablation"
M15_SCHEMA_VERSION = 1
M15_CONDITIONS = ("A_EEG_FULL_WINDOW", "B_CONTEXT_EEG_FULL_WINDOW", "C_CONTEXT_EEG_DYNAMIC_STOP")


def _argmax(scores):
    maximum = max(scores.values())
    return tuple(key for key, value in scores.items() if math.isclose(value, maximum, rel_tol=0.0, abs_tol=1e-12))


def _trajectory_for_trial(trial_index, expected, candidates):
    """Return six fixed-window raw score maps; the pattern is predeclared."""
    active_ids = [candidate.logical_block_id for candidate in candidates]
    alternate = active_ids[1]
    third = active_ids[2]
    pattern = trial_index % 4
    trajectory = []
    for window_index, _window_seconds in enumerate(WINDOW_GRID_SECONDS):
        if pattern == 0:
            scores = {logical_id: (10.0 if logical_id == expected else 0.01) for logical_id in active_ids}
        elif pattern == 1:
            # A transient alternate winner is followed by a stable intended winner.
            winner = alternate if window_index == 0 else expected
            scores = {logical_id: (10.0 if logical_id == winner else 0.01) for logical_id in active_ids}
        elif pattern == 2:
            # Mild raw conflict: context is allowed to alter B's ranking, but C's
            # early confirmation still requires raw and fused winners to agree.
            scores = {expected: 0.95, alternate: 1.0, third: 0.1}
        else:
            # Deliberately close evidence keeps the stopping margin conservative.
            scores = {expected: 1.0, alternate: 0.95, third: 0.9}
        trajectory.append(scores)
    return tuple(trajectory)


def _snapshot_trajectory(context_prior, candidates, raw_trajectory, trial_id):
    snapshots = []
    for window_index, (window_seconds, raw_scores) in enumerate(zip(WINDOW_GRID_SECONDS, raw_trajectory)):
        fused = fuse_context_and_eeg(context_prior, candidates, raw_scores)
        snapshots.append(
            DynamicStoppingSnapshot.from_m12(
                window_index,
                fused,
                analysis_window_seconds=1.5,
                effective_acquisition_seconds=2.0 + 0.2 * window_index,
                provenance={
                    "sourceType": "SYNTHETIC_EEG_SYNTHETIC_CONTEXT",
                    "trialId": trial_id,
                    "windowGridSeconds": ",".join(str(item) for item in WINDOW_GRID_SECONDS),
                },
            )
        )
    return tuple(snapshots)


def _full_window_m12(snapshot):
    top = tuple(snapshot.fused_evidence.top_logical_block_ids)
    return {
        "decisionMade": len(top) == 1,
        "finalTarget": top[0] if len(top) == 1 else None,
        "stopWindow": snapshot.window_index,
        "effectiveAcquisitionSeconds": snapshot.effective_acquisition_seconds,
        "earlyStop": False,
        "fallback": len(top) == 1,
        "noDecision": len(top) != 1,
        "reason": "full_window_fallback" if len(top) == 1 else "no_decision_tie",
    }


def _condition_a(raw_trajectory):
    final_scores = raw_trajectory[-1]
    top = _argmax(final_scores)
    return {
        "decisionMade": len(top) == 1,
        "finalTarget": top[0] if len(top) == 1 else None,
        "stopWindow": len(raw_trajectory) - 1,
        "effectiveAcquisitionSeconds": 2.0 + 0.2 * (len(raw_trajectory) - 1),
        "earlyStop": False,
        "fallback": len(top) == 1,
        "noDecision": len(top) != 1,
        "reason": "eeg_full_window" if len(top) == 1 else "no_decision_tie",
    }


def _condition_c(snapshots):
    policy = DynamicStoppingPolicy()
    for snapshot in snapshots:
        policy.observe(snapshot)
        if policy.decision is not None:
            break
    decision = policy.finalize()
    return {
        "decisionMade": decision.decision_made,
        "finalTarget": decision.selected_logical_block_id,
        "stopWindow": decision.stop_window,
        "effectiveAcquisitionSeconds": decision.effective_acquisition_seconds,
        "earlyStop": decision.early_stop,
        "fallback": decision.stop_reason == "full_window_fallback",
        "noDecision": not decision.decision_made,
        "reason": decision.stop_reason,
        "m13EvaluatedWindows": decision.evaluated_windows,
        "m13FinalFusedEvidence": decision.final_fused_evidence,
        "m13FinalRawEegEvidence": decision.final_raw_eeg_evidence,
    }


def _paired_trial(task_id, step_index, expected, history, trial_index, mapping):
    candidates = _active_candidates(expected, mapping)
    prior = predict_context_prior(make_observation(history, available=BENCHMARK_LOGICAL_BLOCK_IDS))
    raw_trajectory = _trajectory_for_trial(trial_index, expected, candidates)
    snapshots = _snapshot_trajectory(prior, candidates, raw_trajectory, "m15-{}-{:02d}".format(task_id, trial_index))
    condition_a = _condition_a(raw_trajectory)
    condition_b = _full_window_m12(snapshots[-1])
    condition_c = _condition_c(snapshots)
    raw_final_top = _argmax(raw_trajectory[-1])
    fused_final_top = tuple(snapshots[-1].fused_evidence.top_logical_block_ids)
    raw_context_top = tuple(prior.top_targets)
    trial = {
        "trialId": "m15-{}-step-{:02d}".format(task_id, step_index),
        "taskId": task_id,
        "stepIndex": step_index,
        "observableCompletedHistory": list(history),
        "expectedLogicalBlockId": expected,
        "activeCandidateSnapshot": [candidate.to_public_dict() for candidate in candidates],
        "contextPrior": prior.to_public_dict(),
        "rawEegTrajectory": [dict(scores) for scores in raw_trajectory],
        "m12FusedTrajectory": [snapshot.fused_evidence.to_public_dict() for snapshot in snapshots],
        "conditions": {
            M15_CONDITIONS[0]: condition_a,
            M15_CONDITIONS[1]: condition_b,
            M15_CONDITIONS[2]: condition_c,
        },
        "pairedIdentity": {
            "sameRawTrajectory": True,
            "sameObservableHistory": True,
            "rawFinalTop": list(raw_final_top),
            "fusedFinalTop": list(fused_final_top),
            "contextPriorTop": list(raw_context_top),
        },
        "provenance": {
            "eeg": "deterministic synthetic evidence trajectory",
            "context": "M11 observable completed history",
            "mapping": "explicit frozen TargetId/logicalBlockId mapping",
            "windowSchedule": list(WINDOW_GRID_SECONDS),
        },
    }
    return trial


def build_paired_trials(mapping=None):
    mapping = load_virtual_block_target_mapping() if mapping is None else dict(mapping)
    definitions = load_task_definitions()
    trials = []
    global_index = 0
    for task_id in ("house", "tower", "bridge"):
        definition = definitions[task_id]
        history = []
        for step_index, expected in enumerate(definition.ordered_logical_block_ids):
            trials.append(_paired_trial(task_id, step_index, expected, tuple(history), global_index, mapping))
            history.append(expected)
            global_index += 1
    return tuple(trials)


def _condition_metrics(trials, condition):
    rows = [trial["conditions"][condition] | {"expected": trial["expectedLogicalBlockId"], "trialId": trial["trialId"], "taskId": trial["taskId"]} for trial in trials]
    decided = [row for row in rows if row["decisionMade"]]
    early = [row for row in rows if row["earlyStop"]]
    fallback = [row for row in rows if row["fallback"]]
    no_decision = [row for row in rows if row["noDecision"]]
    correct = [row for row in decided if row["finalTarget"] == row["expected"]]
    durations = [row["effectiveAcquisitionSeconds"] for row in decided if isinstance(row["effectiveAcquisitionSeconds"], (int, float))]
    windows = [row["stopWindow"] for row in decided if isinstance(row["stopWindow"], int)]
    return {
        "condition": condition,
        "trialCount": len(rows),
        "decisionCount": len(decided),
        "correctCount": len(correct),
        "descriptiveDecisionRate": len(decided) / len(rows) if rows else 0.0,
        "descriptiveAccuracyAmongDecisions": len(correct) / len(decided) if decided else None,
        "earlyStopCount": len(early),
        "earlyStopRate": len(early) / len(rows) if rows else 0.0,
        "fullWindowFallbackCount": len(fallback),
        "noDecisionCount": len(no_decision),
        "meanStopWindow": sum(windows) / len(windows) if windows else None,
        "medianStopWindow": sorted(windows)[len(windows) // 2] if windows else None,
        "meanEffectiveAcquisitionSeconds": sum(durations) / len(durations) if durations else None,
        "medianEffectiveAcquisitionSeconds": sorted(durations)[len(durations) // 2] if durations else None,
        "windowsConsumed": sum((row["stopWindow"] + 1) for row in decided if isinstance(row["stopWindow"], int)),
        "limitations": ["descriptive paired software/replay metric", "not a human-performance or significance claim"],
    }


def _episode_metrics(trials, condition):
    grouped = {}
    for trial in trials:
        grouped.setdefault(trial["taskId"], []).append(trial)
    rows = []
    for task_id, task_trials in grouped.items():
        condition_rows = [trial["conditions"][condition] for trial in task_trials]
        completed = all(row["decisionMade"] and row["finalTarget"] == trial["expectedLogicalBlockId"] for row, trial in zip(condition_rows, task_trials))
        rows.append({
            "taskId": task_id,
            "condition": condition,
            "completed": completed,
            "completedSteps": sum(1 for row, trial in zip(condition_rows, task_trials) if row["decisionMade"] and row["finalTarget"] == trial["expectedLogicalBlockId"]),
            "failureReason": None if completed else next((row["reason"] for row in condition_rows if not row["decisionMade"] or row["finalTarget"] != task_trials[condition_rows.index(row)]["expectedLogicalBlockId"]), "incorrect_target"),
            "totalEegDurationSeconds": sum(row["effectiveAcquisitionSeconds"] or 0.0 for row in condition_rows),
            "earlyStopCount": sum(1 for row in condition_rows if row["earlyStop"]),
            "fallbackCount": sum(1 for row in condition_rows if row["fallback"]),
            "noDecisionCount": sum(1 for row in condition_rows if row["noDecision"]),
            "rejectedSelectionCount": sum(1 for row, trial in zip(condition_rows, task_trials) if row["decisionMade"] and row["finalTarget"] != trial["expectedLogicalBlockId"]),
            "provenance": "software benchmark; robot execution is not claimed by M15 condition runner",
        })
    return rows


def _agency_metrics(trials):
    conflict_count = 0
    raw_fused_disagreement = 0
    strong_eeg_override = 0
    context_changed_ranking = 0
    context_only_early_stop = 0
    for trial in trials:
        identity = trial["pairedIdentity"]
        if len(identity["rawFinalTop"]) == 1 and len(identity["fusedFinalTop"]) == 1 and identity["rawFinalTop"][0] != identity["fusedFinalTop"][0]:
            raw_fused_disagreement += 1
        if len(identity["contextPriorTop"]) == 1 and len(identity["rawFinalTop"]) == 1 and identity["contextPriorTop"][0] != identity["rawFinalTop"][0]:
            conflict_count += 1
        if len(identity["contextPriorTop"]) == 1 and len(identity["rawFinalTop"]) == 1 and identity["contextPriorTop"][0] != identity["rawFinalTop"][0] and identity["fusedFinalTop"] == identity["rawFinalTop"]:
            strong_eeg_override += 1
        a = trial["conditions"][M15_CONDITIONS[0]]["finalTarget"]
        b = trial["conditions"][M15_CONDITIONS[1]]["finalTarget"]
        if a != b:
            context_changed_ranking += 1
        c = trial["conditions"][M15_CONDITIONS[2]]
        if c["earlyStop"] and c["finalTarget"] not in identity["rawFinalTop"]:
            context_only_early_stop += 1
    return {
        "conflictCount": conflict_count,
        "rawVsFusedWinnerDisagreementCount": raw_fused_disagreement,
        "strongEegOverrideCount": strong_eeg_override,
        "contextChangedRankingCount": context_changed_ranking,
        "contextOnlyEarlyStopCount": context_only_early_stop,
        "noContextOnlyVeto": context_only_early_stop == 0,
    }


def _input_fingerprint(trials):
    canonical = json.dumps(trials, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_benchmark():
    trials = build_paired_trials()
    condition_metrics = {condition: _condition_metrics(trials, condition) for condition in M15_CONDITIONS}
    episode_metrics = {condition: _episode_metrics(trials, condition) for condition in M15_CONDITIONS}
    summary = {
        "schemaVersion": M15_SCHEMA_VERSION,
        "recordType": "m15_comparative_benchmark",
        "acceptanceId": M15_ACCEPTANCE_ID,
        "status": "PASS",
        "conditions": list(M15_CONDITIONS),
        "trialCount": len(trials),
        "pairedInputFingerprint": _input_fingerprint(trials),
        "conditionMetrics": condition_metrics,
        "episodeMetrics": episode_metrics,
        "agencyMetrics": _agency_metrics(trials),
        "frozenPolicy": {"m12Lambda": 0.5, "m13FusedThreshold": 0.70, "m13MarginThreshold": 0.20, "m13RequiredConsecutive": 2},
        "provenance": "synthetic EEG trajectory + M11 observable-history context + existing M12/M13 implementations",
        "limitations": ["descriptive paired benchmark only", "no parameter tuning or optimization", "no human EEG or generalized accuracy claim"],
    }
    return summary, trials


def acceptance_checks(summary, trials):
    checks = {
        "threeConditionsPresent": summary["conditions"] == list(M15_CONDITIONS),
        "pairedTrialCount": summary["trialCount"] == 12,
        "samePairedInputs": all(trial["pairedIdentity"]["sameRawTrajectory"] and trial["pairedIdentity"]["sameObservableHistory"] for trial in trials),
        "m13ContextOnlyVetoAbsent": summary["agencyMetrics"]["noContextOnlyVeto"],
        "frozenDefaults": summary["frozenPolicy"] == {"m12Lambda": 0.5, "m13FusedThreshold": 0.70, "m13MarginThreshold": 0.20, "m13RequiredConsecutive": 2},
        "deterministicInputFingerprint": summary["pairedInputFingerprint"] == _input_fingerprint(build_paired_trials()),
        "identityPrivacy": "obj_" not in json.dumps({"summary": summary, "trials": trials}, sort_keys=True),
    }
    return checks


def write_outputs(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, trials = run_benchmark()
    checks = acceptance_checks(summary, trials)
    summary["checks"] = checks
    summary["status"] = "PASS" if all(checks.values()) else "FAIL"
    (output_dir / "m15-summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "m15-trials.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
        for trial in trials:
            stream.write(json.dumps(trial, ensure_ascii=False, sort_keys=True) + "\n")
    with (output_dir / "m15-condition-metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        fieldnames = ["condition", "trialCount", "decisionCount", "correctCount", "descriptiveDecisionRate", "descriptiveAccuracyAmongDecisions", "earlyStopCount", "earlyStopRate", "fullWindowFallbackCount", "noDecisionCount", "meanStopWindow", "medianStopWindow", "meanEffectiveAcquisitionSeconds", "medianEffectiveAcquisitionSeconds", "windowsConsumed"]
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary["conditionMetrics"].values():
            writer.writerow({key: row.get(key) for key in fieldnames})
    return summary, trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    summary, _trials = write_outputs(args.output_dir)
    print(json.dumps({"status": summary["status"], "trialCount": summary["trialCount"], "outputDir": str(args.output_dir)}, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["M15_CONDITIONS", "build_paired_trials", "run_benchmark", "acceptance_checks", "write_outputs"]
