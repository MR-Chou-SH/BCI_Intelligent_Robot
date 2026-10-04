#!/usr/bin/env python3
"""Recompute paired M36 controlled-Context metrics and trial-cluster intervals."""

import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
PREDICTION_FILES = (
    "rule_oof_predictions.csv",
    "logistic_oof_predictions.csv",
    "view_guard_logistic_oof_predictions.csv",
)
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 36042026
TOL = 1e-12


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing to write empty result table: {}".format(path))
    fieldnames = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def wilson(successes, n, z=1.959963984540054):
    if n <= 0:
        return [None, None]
    p = float(successes) / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    half = (z * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n))) / denom
    return [max(0.0, center - half), min(1.0, center + half)]


def exact_mcnemar(b, c):
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def percentile(values, probability):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def bootstrap_mean_ci(per_trial_values, rng):
    values = [float(value) for value in per_trial_values]
    if not values:
        return [None, None]
    array = np.asarray(values, dtype=float)
    indices = rng.integers(0, len(array), size=(BOOTSTRAP_N, len(array)))
    samples = np.mean(array[indices], axis=1)
    return [float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))]


def build_predictions():
    features = read_csv(ROOT / "eeg_trajectory_features.csv")
    metadata = {}
    for row in features:
        key = (row["track"], row["sessionKey"], row["trialId"])
        item = {
            "acquisitionCampaignGroup": row["acquisitionCampaignGroup"],
            "recordingSessionGroup": row["recordingSessionGroup"],
        }
        if key in metadata and metadata[key] != item:
            raise ValueError("inconsistent group metadata for {}".format(key))
        metadata[key] = item
    outer = {}
    for row in read_csv(ROOT / "outer_fold_assignments.csv"):
        key = (row["track"], row["groupingScheme"], row["outerFoldId"])
        outer.setdefault(key, {"train": set(json.loads(row["outerTrainTrialKeys"])), "test": set(), "outerTestGroup": row["outerTestGroup"]})
        outer[key]["test"].add(row["trialKey"])
    all_rows = []
    for file_name in PREDICTION_FILES:
        path = ROOT / file_name
        if not path.exists():
            raise FileNotFoundError(path)
        for row in read_csv(path):
            trial_key = (row["track"], row["sessionKey"], row["trialId"])
            group = metadata[trial_key]
            row["acquisitionCampaignGroup"] = group["acquisitionCampaignGroup"]
            row["recordingSessionGroup"] = group["recordingSessionGroup"]
            assignment = outer[(row["track"], row["groupingScheme"], row["outerFoldId"])]
            if row["trialKey"] not in assignment["test"] or row["trialKey"] in assignment["train"]:
                raise ValueError("prediction is not held out from its recorded outer fold: {}".format(row))
            row["outerTestGroup"] = assignment["outerTestGroup"]
            all_rows.append(row)
    return all_rows


def aggregate(rows, group_fields, compute_bootstrap=True):
    buckets = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in group_fields)
        buckets[key].append(row)
    output = []
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    for key, group in sorted(buckets.items(), key=lambda item: tuple(str(x) for x in item[0])):
        first = group[0]
        condition = first["condition"]
        by_trial = defaultdict(list)
        for row in group:
            by_trial[row["trialKey"]].append(row)
        per_trial_gain = [sum(float(r["pairedGainSeconds"]) for r in values) / len(values) for values in by_trial.values()]
        per_trial_evidence = [sum(float(r["selectedEvidenceSeconds"]) for r in values) / len(values) for values in by_trial.values()]
        per_trial_base_evidence = [sum(float(r["baselineEvidenceSeconds"]) for r in values) / len(values) for values in by_trial.values()]
        per_trial_late_fraction = [sum(float(r["selectedEvidenceSeconds"]) <= 0.30 + TOL for r in values) / len(values) for values in by_trial.values()]
        applied_events = [r for r in group if int(r["contextApplied"])]
        correct_applied_events = sum(int(r["selectedCorrect"]) for r in applied_events)
        per_trial_apps = defaultdict(list)
        for row in applied_events:
            per_trial_apps[row["trialKey"]].append(row)
        app_trial_count = len(per_trial_apps)
        if condition == "INCONGRUENT_CONTEXT":
            bad_app_trials = sum(any(int(r["contextInducedWrongEarlyStop"]) for r in values) for values in per_trial_apps.values())
            precision_cluster = app_trial_count - bad_app_trials
            precision_cluster_ci = wilson(precision_cluster, app_trial_count)
            risk_cluster_ci = wilson(bad_app_trials, app_trial_count)
            per_trial_induced = [int(any(int(r["contextInducedWrongEarlyStop"]) for r in values)) for values in by_trial.values()]
            induced_trial_count = sum(per_trial_induced)
            induced_trial_incidence_ci = wilson(induced_trial_count, len(by_trial))
        else:
            good_app_trials = sum(all(int(r["selectedCorrect"]) for r in values) for values in per_trial_apps.values())
            precision_cluster = good_app_trials
            precision_cluster_ci = wilson(good_app_trials, app_trial_count)
            risk_cluster_ci = [None, None]
            induced_trial_count = sum(any(int(r["contextInducedWrongEarlyStop"]) for r in values) for values in by_trial.values())
            induced_trial_incidence_ci = wilson(induced_trial_count, len(by_trial))

        if condition == "INCONGRUENT_CONTEXT":
            per_trial_base_correct = [int(all(int(r["baselineCorrect"]) for r in values)) for values in by_trial.values()]
            per_trial_selected_correct = [int(all(int(r["selectedCorrect"]) for r in values)) for values in by_trial.values()]
        else:
            per_trial_base_correct = [int(values[0]["baselineCorrect"]) for values in by_trial.values()]
            per_trial_selected_correct = [int(values[0]["selectedCorrect"]) for values in by_trial.values()]
        baseline_wrong_assisted_correct = sum(x == 0 and y == 1 for x, y in zip(per_trial_base_correct, per_trial_selected_correct))
        baseline_correct_assisted_wrong = sum(x == 1 and y == 0 for x, y in zip(per_trial_base_correct, per_trial_selected_correct))
        q_top = first["qTop"] or ""
        replay_scenarios = len(group)
        real_trials = len(by_trial)
        app_count = len(applied_events)
        scenario_coverage = float(app_count) / replay_scenarios if replay_scenarios else 0.0
        precision_event = float(correct_applied_events) / app_count if app_count else None
        induced_event_count = sum(int(r["contextInducedWrongEarlyStop"]) for r in group)
        risk_event = float(induced_event_count) / app_count if app_count and condition == "INCONGRUENT_CONTEXT" else (0.0 if condition == "INCONGRUENT_CONTEXT" else None)
        row_out = {
            **dict(zip(group_fields, key)),
            "realEegTrials": real_trials,
            "contextReplayScenarios": replay_scenarios,
            "contextApplications": app_count,
            "uniqueTrialsWithAnyApplication": app_trial_count,
            "eligibleCongruentOpportunities": real_trials if condition == "CONGRUENT_CONTEXT" else "",
            "coveragePerReplayScenario": scenario_coverage,
            "applicationsPerRealTrial": float(app_count) / real_trials if real_trials else 0.0,
            "scenarioInterventionPrecision": precision_event,
            "trialClusterInterventionPrecision": float(precision_cluster) / app_trial_count if app_trial_count else None,
            "trialClusterInterventionPrecisionWilson95": json.dumps(precision_cluster_ci),
            "inducedWrongEarlyStopEvents": induced_event_count,
            "trialsWithAnyInducedWrongStop": induced_trial_count,
            "trialClusterInducedRisk": float(induced_trial_count) / app_trial_count if app_trial_count and condition == "INCONGRUENT_CONTEXT" else (0.0 if condition == "INCONGRUENT_CONTEXT" else None),
            "trialClusterInducedRiskWilson95": json.dumps(risk_cluster_ci),
            "inducedRiskPerEligibleRealTrial": float(induced_trial_count) / real_trials if real_trials else 0.0,
            "inducedRiskPerEligibleRealTrialWilson95": json.dumps(induced_trial_incidence_ci),
            "meanEvidenceSeconds": sum(per_trial_evidence) / real_trials if real_trials else None,
            "eegOnlyMeanEvidenceSeconds": sum(per_trial_base_evidence) / real_trials if real_trials else None,
            "overallMeanGainSeconds": sum(per_trial_gain) / real_trials if real_trials else None,
            "pairedMeanGainBootstrap95CI": json.dumps(bootstrap_mean_ci(per_trial_gain, rng) if compute_bootstrap else [None, None]),
            "appliedTrialMeanGainSeconds": sum(float(r["pairedGainSeconds"]) for r in applied_events) / app_count if app_count else None,
            "fractionScenariosLE030": sum(float(r["selectedEvidenceSeconds"]) <= 0.30 + TOL for r in group) / replay_scenarios if replay_scenarios else None,
            "fractionTrialsLE030": sum(per_trial_late_fraction) / real_trials if real_trials else None,
            "scenarioAccuracy": sum(int(r["selectedCorrect"]) for r in group) / replay_scenarios if replay_scenarios else None,
            "conservativePerTrialAccuracy": sum(per_trial_selected_correct) / real_trials if real_trials else None,
            "conservativePairedAccuracyDelta": sum(y - x for x, y in zip(per_trial_base_correct, per_trial_selected_correct)) / real_trials if real_trials else None,
            "mcnemarBaselineCorrectAssistedWrong": baseline_correct_assisted_wrong,
            "mcnemarBaselineWrongAssistedCorrect": baseline_wrong_assisted_correct,
            "mcnemarExactTwoSidedP": exact_mcnemar(baseline_correct_assisted_wrong, baseline_wrong_assisted_correct),
            "meanFinalClassPreservation": sum(int(r["safeFinalAtStop"]) for r in group) / replay_scenarios if replay_scenarios else None,
            "delayViolations": sum(float(r["selectedEvidenceSeconds"]) > float(r["baselineEvidenceSeconds"]) + TOL for r in group),
            "safetyTargetEmpiricallyMet": (
                (app_count > 0 and induced_event_count == 0) if first["safetyOperatingPoint"] == "SAFE-STRICT" and condition == "INCONGRUENT_CONTEXT" else
                (app_count > 0 and risk_event is not None and risk_event <= 0.05 + TOL) if first["safetyOperatingPoint"] == "SAFE-95" and condition == "INCONGRUENT_CONTEXT" else
                (app_count > 0 and risk_event is not None and risk_event <= 0.01 + TOL) if first["safetyOperatingPoint"] == "SAFE-99" and condition == "INCONGRUENT_CONTEXT" else
                None
            ),
        }
        output.append(row_out)
    return output


def main():
    rows = build_predictions()
    if not rows:
        raise ValueError("no OOF Context rows")
    primary_fields = ("track", "groupingScheme", "method", "safetyOperatingPoint", "condition", "qTop")
    combined = aggregate(rows, primary_fields)
    write_csv(ROOT / "controlled_context_results.csv", combined)
    write_csv(ROOT / "congruent_results.csv", [r for r in combined if r["condition"] == "CONGRUENT_CONTEXT"])
    write_csv(ROOT / "incongruent_results.csv", [r for r in combined if r["condition"] == "INCONGRUENT_CONTEXT"])
    write_csv(ROOT / "neutral_results.csv", [r for r in combined if r["condition"] == "NEUTRAL_CONTEXT"])

    risk_rows = [r for r in combined if r["condition"] in ("CONGRUENT_CONTEXT", "INCONGRUENT_CONTEXT")]
    write_csv(ROOT / "risk_coverage.csv", risk_rows)
    group_fields = ("track", "groupingScheme", "outerTestGroup", "sessionKey", "method", "safetyOperatingPoint", "condition", "qTop")
    groups = aggregate(rows, group_fields, compute_bootstrap=False)
    write_csv(ROOT / "group_specific_results.csv", groups)

    historical = read_csv(ROOT.parents[2] / "research_analysis" / "m35_controlled_context_causality_20261004" / "attempt-01" / "controlled_context_per_trial.csv")
    dev = [r for r in historical if r["sessionKey"] in ("A", "B1") and r["condition"] == "CONGRUENT_CONTEXT" and abs(float(r["qTop"]) - 0.95) < TOL]
    historical_summary = {
        "source": "M35 controlled_context_per_trial.csv; historical A/B1 development split, not M36 OOF",
        "realEegTrials": len({(r["sessionKey"], r["trialId"]) for r in dev}),
        "appliedTrials": sum(int(r["contextApplied"]) for r in dev),
        "applicationRate": sum(int(r["contextApplied"]) for r in dev) / len(dev) if dev else None,
        "appliedMeanGainSeconds": sum(float(r["pairedGainSeconds"]) for r in dev if int(r["contextApplied"])) / max(1, sum(int(r["contextApplied"]) for r in dev)),
        "meanGainSeconds": sum(float(r["pairedGainSeconds"]) for r in dev) / len(dev) if dev else None,
    }
    (ROOT / "historical_m35_reference.json").write_text(json.dumps(historical_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    primary = [r for r in combined if r["groupingScheme"] == "acquisitionCampaignGroup" and r["qTop"] == "0.95" and r["condition"] in ("CONGRUENT_CONTEXT", "INCONGRUENT_CONTEXT")]
    summary = {
        "status": "PASS",
        "inputPredictionFiles": list(PREDICTION_FILES),
        "inputOofDecisionRows": len(rows),
        "aggregateRows": len(combined),
        "groupSpecificAggregateRows": len(groups),
        "bootstrap": {"unit": "real EEG trial; two wrong-target scenarios are averaged/clustered within trial", "replicates": BOOTSTRAP_N, "seed": BOOTSTRAP_SEED},
        "binomialInterval": "two-sided 95% Wilson; applied wrong-target trial clusters are the unit, not repeated q scenarios",
        "m35HistoricalReference": historical_summary,
        "primaryQ095": primary,
        "scenarioAccounting": "INCONGRUENT_CONTEXT has two wrong-target replay scenarios per real trial and q; they are not treated as independent EEG trials.",
    }
    (ROOT / "statistical_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # The final OOF table combines all evaluated gate families while retaining their source rows.
    with (ROOT / "oof_predictions.csv").open("w", newline="", encoding="utf-8") as stream:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row.keys()))
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({
        "status": "PASS",
        "oofRows": len(rows),
        "aggregateRows": len(combined),
        "groupSpecificRows": len(groups),
        "historicalM35DevelopmentApplicationRate": historical_summary["applicationRate"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
