#!/usr/bin/env python3
"""Nested probability gate with separate frequency-band and harmonic safeguards."""

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import m36_logistic_evaluation as logistic
import m36_rule_evaluation as rules


ROOT = Path(__file__).resolve().parent
OUTER_PATH = ROOT / "outer_fold_assignments.csv"
INNER_PATH = ROOT / "inner_fold_assignments.csv"
PROB_PATH = ROOT / "logistic_reliability_oof.csv"
GUARDS = (
    (0.0, 0.0, 0.0),
    (2.0 / 3.0, 0.0, 0.0),
    (0.0, 2.0 / 3.0, 0.0),
    (2.0 / 3.0, 2.0 / 3.0, 0.0),
    (2.0 / 3.0, 2.0 / 3.0, 0.05),
    (2.0 / 3.0, 1.0, 0.05),
    (1.0, 2.0 / 3.0, 0.05),
    (1.0, 1.0, 0.10),
)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing to write empty output: {}".format(path))
    fields = list(dict.fromkeys(key for row in rows for key in row.keys()))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def guarded_outcome(trace, probabilities, context_slot, threshold, guard):
    min_band, min_harmonic, min_band_margin = guard
    chosen = None
    for index, point in enumerate(trace["points"][:trace["baselineIndex"]]):
        if point["evidenceSeconds"] + 1e-12 < rules.M35_MIN_EVIDENCE:
            continue
        if point["top"] != int(context_slot) or float(probabilities[index]) + 1e-12 < threshold:
            continue
        if point["bandAgreement"] + 1e-12 < min_band:
            continue
        if point["harmonicAgreement"] + 1e-12 < min_harmonic:
            continue
        if point["bandMinimumMargin"] + 1e-12 < min_band_margin:
            continue
        chosen = index
        break
    index = chosen if chosen is not None else trace["baselineIndex"]
    point = trace["points"][index]
    baseline = trace["points"][trace["baselineIndex"]]
    base_correct = int(baseline["top"] == trace["trueSlotIndex"])
    selected_correct = int(point["top"] == trace["trueSlotIndex"])
    applied = chosen is not None
    return {
        "selectedIndex": index,
        "selectedPrediction": point["top"],
        "selectedEvidenceSeconds": point["evidenceSeconds"],
        "baselinePrediction": baseline["top"],
        "baselineEvidenceSeconds": baseline["evidenceSeconds"],
        "baselineCorrect": base_correct,
        "selectedCorrect": selected_correct,
        "inducedWrongEarlyStop": int(applied and base_correct and not selected_correct),
        "safeFinalAtStop": int(point["top"] == trace["eegOnlyDecisionClass"]),
        "applied": int(applied),
        "gainSeconds": baseline["evidenceSeconds"] - point["evidenceSeconds"] if applied else 0.0,
        "rawTopAtStop": point["top"],
        "relativeMarginAtStop": point["relativeMargin"],
        "secondaryAgreementAtStop": point["secondaryAgreement"],
        "priorTopFlipsAtStop": point["priorTopFlips"],
        "stableUpdatesAtStop": point["stableUpdates"],
        "bandAgreementAtStop": point["bandAgreement"],
        "harmonicAgreementAtStop": point["harmonicAgreement"],
        "bandMinimumMarginAtStop": point["bandMinimumMargin"],
    }


def guard_metrics(traces, inner_folds, probability_by_trial, threshold, guard):
    inner_by_fold = {}
    congruent_apps = 0
    congruent_n = 0
    congruent_gain = 0.0
    applied_gains = []
    for fold_id, split in inner_folds.items():
        wrong_apps = 0
        induced = 0
        accuracy_delta = 0
        for trial_key in split["validation"]:
            trace = traces[trial_key]
            p = probability_by_trial[trial_key]
            congruent = guarded_outcome(trace, p, trace["trueSlotIndex"], threshold, guard)
            congruent_apps += congruent["applied"]
            congruent_n += 1
            congruent_gain += congruent["gainSeconds"]
            if congruent["applied"]:
                applied_gains.append(congruent["gainSeconds"])
            for target in range(3):
                if target == trace["trueSlotIndex"]:
                    continue
                result = guarded_outcome(trace, p, target, threshold, guard)
                wrong_apps += result["applied"]
                induced += result["inducedWrongEarlyStop"]
                accuracy_delta += result["selectedCorrect"] - result["baselineCorrect"]
        inner_by_fold[fold_id] = {
            "wrongContextApplications": wrong_apps,
            "inducedWrongEarlyStops": induced,
            "inducedRisk": float(induced) / wrong_apps if wrong_apps else 0.0,
            "accuracyDelta": accuracy_delta,
        }
    return {
        "innerTrials": congruent_n,
        "congruentApplications": congruent_apps,
        "congruentCoverage": float(congruent_apps) / congruent_n if congruent_n else 0.0,
        "congruentMeanGainSeconds": congruent_gain / congruent_n if congruent_n else 0.0,
        "congruentAppliedMeanGainSeconds": sum(applied_gains) / len(applied_gains) if applied_gains else 0.0,
        "maxInnerFoldInducedRisk": max((r["inducedRisk"] for r in inner_by_fold.values()), default=0.0),
        "minInnerFoldAccuracyDelta": min((r["accuracyDelta"] for r in inner_by_fold.values()), default=0),
        "innerFoldMetrics": inner_by_fold,
    }


def select_policy(traces, inner_folds, probability_by_trial, risk_limit):
    candidates = []
    for guard in GUARDS:
        for threshold in logistic.threshold_candidates(probability_by_trial):
            metrics = guard_metrics(traces, inner_folds, probability_by_trial, threshold, guard)
            eligible = metrics["maxInnerFoldInducedRisk"] <= risk_limit + 1e-12 and metrics["minInnerFoldAccuracyDelta"] >= 0
            candidates.append({"guard": guard, "threshold": threshold, "metrics": metrics, "eligible": eligible})
    eligible = [r for r in candidates if r["eligible"]]
    if not eligible:
        fallback = guard_metrics(traces, inner_folds, probability_by_trial, 1.01, (1.0, 1.0, 1.0))
        return 1.01, (1.0, 1.0, 1.0), fallback, candidates
    selected = max(eligible, key=lambda r: (
        r["metrics"]["congruentCoverage"],
        r["metrics"]["congruentAppliedMeanGainSeconds"],
        r["metrics"]["congruentMeanGainSeconds"],
        sum(r["guard"]),
        r["threshold"],
    ))
    return selected["threshold"], selected["guard"], selected["metrics"], candidates


def main():
    all_traces = rules.load_traces()
    traces_by_track = defaultdict(dict)
    for (track, trial_key), trace in all_traces.items():
        traces_by_track[track][trial_key] = trace
    outer_rows = read_csv(OUTER_PATH)
    inner_rows = read_csv(INNER_PATH)
    inner_all = rules.read_fold_splits(inner_rows)
    prob_rows = read_csv(PROB_PATH)
    test_probabilities = {}
    for row in prob_rows:
        key = (row["track"], row["groupingScheme"], row["outerFoldId"], row["trialKey"])
        test_probabilities.setdefault(key, np.zeros(9, dtype=float))[int(row["windowIndex"])] = float(row["reliabilityProbability"])

    decisions = []
    policy_rows = []
    search_rows = []
    for track in rules.TRACKS:
        track_traces = traces_by_track[track]
        for scheme in rules.SCHEMES:
            group_field = "acquisitionCampaignGroup" if scheme == "acquisitionCampaignGroup" else "recordingSessionGroup"
            outer_sub = [r for r in outer_rows if r["track"] == track and r["groupingScheme"] == scheme]
            folds = {}
            for row in outer_sub:
                folds.setdefault(row["outerFoldId"], {"train": json.loads(row["outerTrainTrialKeys"]), "test": []})
                folds[row["outerFoldId"]]["test"].append(row["trialKey"])
            for outer_fold, assignment in folds.items():
                train_keys = assignment["train"]
                test_keys = assignment["test"]
                train_traces = {key: track_traces[key] for key in train_keys}
                inner_folds = {
                    inner_id: split for (t, s, o, inner_id), split in inner_all.items()
                    if t == track and s == scheme and o == outer_fold
                }
                group_name = "acquisitionCampaignGroup" if scheme == "acquisitionCampaignGroup" else "recordingSessionGroup"
                best_l2, best_oof, _ = logistic.select_l2(train_traces, inner_folds, train_keys, group_name)
                calibrator, inner_probabilities = logistic.calibrated_inner_probabilities(train_traces, best_oof)
                for safety, risk_limit in rules.SAFETY_MODES:
                    threshold, guard, metrics, candidates = select_policy(train_traces, inner_folds, inner_probabilities, risk_limit)
                    selected = {
                        "track": track,
                        "groupingScheme": scheme,
                        "outerFoldId": outer_fold,
                        "method": "M36_LOGISTIC_VIEW_GUARDED",
                        "safetyOperatingPoint": safety,
                        "selectedL2": best_l2,
                        "selectedReliabilityThreshold": threshold,
                        "minimumBandAgreement": guard[0],
                        "minimumHarmonicAgreement": guard[1],
                        "minimumBandMargin": guard[2],
                        "innerCongruentCoverage": metrics["congruentCoverage"],
                        "innerCongruentMeanGainSeconds": metrics["congruentMeanGainSeconds"],
                        "innerCongruentAppliedMeanGainSeconds": metrics["congruentAppliedMeanGainSeconds"],
                        "innerMaxFoldInducedRisk": metrics["maxInnerFoldInducedRisk"],
                        "innerMinimumFoldAccuracyDelta": metrics["minInnerFoldAccuracyDelta"],
                        "thresholdGuardCandidateCount": len(candidates),
                    }
                    policy_rows.append(selected)
                    for candidate in candidates:
                        search_rows.append({
                            "track": track,
                            "groupingScheme": scheme,
                            "outerFoldId": outer_fold,
                            "safetyOperatingPoint": safety,
                            "l2": best_l2,
                            "threshold": candidate["threshold"],
                            "minimumBandAgreement": candidate["guard"][0],
                            "minimumHarmonicAgreement": candidate["guard"][1],
                            "minimumBandMargin": candidate["guard"][2],
                            "eligible": candidate["eligible"],
                            "innerCongruentCoverage": candidate["metrics"]["congruentCoverage"],
                            "innerCongruentMeanGainSeconds": candidate["metrics"]["congruentMeanGainSeconds"],
                            "innerInducedWrongEarlyStops": sum(m["inducedWrongEarlyStops"] for m in candidate["metrics"]["innerFoldMetrics"].values()),
                            "innerMaxFoldInducedRisk": candidate["metrics"]["maxInnerFoldInducedRisk"],
                            "innerMinimumFoldAccuracyDelta": candidate["metrics"]["minInnerFoldAccuracyDelta"],
                        })
                    for trial_key in test_keys:
                        trace = track_traces[trial_key]
                        p = test_probabilities[(track, scheme, outer_fold, trial_key)]
                        for q in rules.Q_GRID:
                            for context_slot in range(3):
                                condition = "CONGRUENT_CONTEXT" if context_slot == trace["trueSlotIndex"] else "INCONGRUENT_CONTEXT"
                                result = guarded_outcome(trace, p, context_slot, threshold, guard)
                                row = rules.emit_decision(trace, track, scheme, outer_fold, "M36_LOGISTIC_VIEW_GUARDED", safety,
                                                          context_slot, q, result, condition)
                                row["reliabilityProbability"] = float(p[result["selectedIndex"]])
                                row["minimumBandAgreement"] = guard[0]
                                row["minimumHarmonicAgreement"] = guard[1]
                                row["minimumBandMargin"] = guard[2]
                                row["bandAgreementAtStop"] = result["bandAgreementAtStop"]
                                row["harmonicAgreementAtStop"] = result["harmonicAgreementAtStop"]
                                row["bandMinimumMarginAtStop"] = result["bandMinimumMarginAtStop"]
                                decisions.append(row)
                        neutral = rules.outcome(trace, None, 1.0 / 3.0, rules.M35_PARAMS, include_multiview=False)
                        row = rules.emit_decision(trace, track, scheme, outer_fold, "M36_LOGISTIC_VIEW_GUARDED", safety,
                                                  None, None, neutral, "NEUTRAL_CONTEXT")
                        row["reliabilityProbability"] = ""
                        row["minimumBandAgreement"] = guard[0]
                        row["minimumHarmonicAgreement"] = guard[1]
                        row["minimumBandMargin"] = guard[2]
                        row["bandAgreementAtStop"] = trace["points"][trace["baselineIndex"]]["bandAgreement"]
                        row["harmonicAgreementAtStop"] = trace["points"][trace["baselineIndex"]]["harmonicAgreement"]
                        row["bandMinimumMarginAtStop"] = trace["points"][trace["baselineIndex"]]["bandMinimumMargin"]
                        decisions.append(row)

    write_csv(ROOT / "view_guard_logistic_oof_predictions.csv", decisions)
    write_csv(ROOT / "view_guard_nested_search.csv", search_rows)
    write_csv(ROOT / "selected_view_guard_policies.csv", policy_rows)
    write_csv(ROOT / "view_guard_risk_coverage.csv", rules.summarize_rows(decisions))
    summary = {
        "status": "PASS",
        "hypothesis": "separate harmonic and filter-band agreement prevents one consensus family from masking another family's disagreement",
        "guardGrid": [{"minBandAgreement": b, "minHarmonicAgreement": h, "minBandMargin": m} for b, h, m in GUARDS],
        "policyRows": len(policy_rows),
        "contextDecisionRows": len(decisions),
        "innerGuardThresholdRows": len(search_rows),
        "probabilitiesSource": PROB_PATH.name,
        "allProbabilitiesAreOuterOof": True,
        "argmaxInvariant": "selectedPrediction remains the raw EEG top; guard affects only authorization",
    }
    (ROOT / "view_guard_logistic_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "policyRows": len(policy_rows), "contextDecisionRows": len(decisions),
                      "searchRows": len(search_rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
