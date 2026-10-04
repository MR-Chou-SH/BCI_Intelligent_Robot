#!/usr/bin/env python3
"""Nested group-OOF logistic EEG-reliability gate with training-only Platt calibration."""

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import m36_rule_evaluation as rules


ROOT = Path(__file__).resolve().parent
FEATURES_PATH = ROOT / "eeg_trajectory_features.csv"
LABELS_PATH = ROOT / "reliability_labels.csv"
OUTER_PATH = ROOT / "outer_fold_assignments.csv"
INNER_PATH = ROOT / "inner_fold_assignments.csv"
L2_GRID = (0.001, 0.01, 0.1, 1.0)
THRESHOLD_BASE = (0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.925, 0.95, 0.975, 0.99, 0.995, 1.0, 1.01)
MAX_ITER = 80
TOL = 1e-8


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing to write empty output: {}".format(path))
    with path.open("w", newline="", encoding="utf-8") as stream:
        fieldnames = list(dict.fromkeys(key for row in rows for key in row.keys()))
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sigmoid(values):
    values = np.asarray(values, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(values, -35.0, 35.0)))


def fit_logistic(x, y, l2, class_balance=False):
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.ndim == 1:
        x = x.reshape((-1, 1))
    if len(y) != len(x) or len(y) == 0:
        raise ValueError("empty or mismatched logistic training data")
    prevalence = float(np.mean(y))
    if prevalence <= 0.0 or prevalence >= 1.0:
        return {"constantProbability": prevalence, "mean": np.zeros(x.shape[1]), "scale": np.ones(x.shape[1]), "coef": np.zeros(x.shape[1] + 1)}
    mean = np.mean(x, axis=0)
    scale = np.std(x, axis=0)
    scale[scale < 1e-10] = 1.0
    z = np.clip((x - mean) / scale, -12.0, 12.0)
    design = np.column_stack((np.ones(len(z)), z))
    if class_balance:
        n0 = max(1, int(np.sum(y == 0.0)))
        n1 = max(1, int(np.sum(y == 1.0)))
        sample_weight = np.where(y == 1.0, len(y) / (2.0 * n1), len(y) / (2.0 * n0))
    else:
        sample_weight = np.ones(len(y), dtype=float)
    coef = np.zeros(design.shape[1], dtype=float)
    penalty = np.ones_like(coef) * float(l2)
    penalty[0] = 0.0
    converged = False
    for iteration in range(MAX_ITER):
        probability = sigmoid(design @ coef)
        variance = np.maximum(probability * (1.0 - probability), 1e-9)
        grad = (design.T @ (sample_weight * (probability - y))) / len(y) + penalty * coef
        weighted_design = design * (sample_weight * variance)[:, None]
        hessian = (design.T @ weighted_design) / len(y) + np.diag(penalty)
        try:
            delta = np.linalg.solve(hessian, grad)
        except np.linalg.LinAlgError:
            delta = np.linalg.lstsq(hessian, grad, rcond=None)[0]
        coef -= delta
        if float(np.linalg.norm(delta)) < TOL:
            converged = True
            break
    return {"mean": mean, "scale": scale, "coef": coef, "converged": converged, "iterations": iteration + 1}


def predict_logits(model, x):
    x = np.asarray(x, dtype=float)
    if x.ndim == 1:
        x = x.reshape((-1, 1))
    if "constantProbability" in model:
        p = min(1.0 - 1e-12, max(1e-12, float(model["constantProbability"])))
        return np.full(len(x), math.log(p / (1.0 - p)), dtype=float)
    z = np.clip((x - model["mean"]) / model["scale"], -12.0, 12.0)
    return np.column_stack((np.ones(len(z)), z)) @ model["coef"]


def build_matrix(traces, trial_keys):
    x_rows = []
    y_rows = []
    group_rows = []
    index_rows = []
    for trial_key in trial_keys:
        trace = traces[trial_key]
        for point in trace["points"]:
            x_rows.append(point["features"])
            y_rows.append(point["safeTrue"])
            group_rows.append(trace["acquisitionCampaignGroup"])
            index_rows.append((trial_key, point["windowIndex"]))
    return np.asarray(x_rows, dtype=float), np.asarray(y_rows, dtype=float), group_rows, index_rows


def select_l2(traces, inner_folds, train_keys, group_field):
    search = []
    score_map = {}
    for l2 in L2_GRID:
        logits_by_key = {}
        y_by_key = {}
        group_brier = defaultdict(list)
        group_logloss = defaultdict(list)
        for inner_id, split in inner_folds.items():
            x_train, y_train, _, _ = build_matrix(traces, split["train"])
            x_val, y_val, val_groups, val_index = build_matrix(traces, split["validation"])
            model = fit_logistic(x_train, y_train, l2, class_balance=True)
            logits = predict_logits(model, x_val)
            probabilities = sigmoid(logits)
            for i, ((trial_key, window_index), probability, label) in enumerate(zip(val_index, probabilities, y_val)):
                logits_by_key.setdefault(trial_key, {})[window_index] = float(logits[i])
                y_by_key.setdefault(trial_key, {})[window_index] = int(label)
                group = traces[trial_key][group_field]
                group_brier[group].append(float((probability - label) ** 2))
                clipped = min(1.0 - 1e-12, max(1e-12, float(probability)))
                group_logloss[group].append(float(-(label * math.log(clipped) + (1.0 - label) * math.log(1.0 - clipped))))
        if set(logits_by_key) != set(train_keys):
            raise ValueError("inner logistic OOF predictions do not cover outer-train trials for L2 {}".format(l2))
        per_group = {
            group: {
                "brier": float(np.mean(group_brier[group])),
                "logLoss": float(np.mean(group_logloss[group])),
            }
            for group in sorted(group_brier)
        }
        mean_group_brier = float(np.mean([value["brier"] for value in per_group.values()]))
        worst_group_brier = max(value["brier"] for value in per_group.values())
        mean_group_logloss = float(np.mean([value["logLoss"] for value in per_group.values()]))
        score_map[l2] = {"logits": logits_by_key, "labels": y_by_key, "groupMetrics": per_group}
        search.append({
            "stage": "logistic_l2",
            "l2": l2,
            "meanInnerGroupBrier": mean_group_brier,
            "worstInnerGroupBrier": worst_group_brier,
            "meanInnerGroupLogLoss": mean_group_logloss,
            "innerGroupMetrics": json.dumps(per_group, sort_keys=True, separators=(",", ":")),
            "innerTrialCount": len(logits_by_key),
        })
    best = min(L2_GRID, key=lambda value: (next(r["worstInnerGroupBrier"] for r in search if r["l2"] == value),
                                           next(r["meanInnerGroupBrier"] for r in search if r["l2"] == value),
                                           -value))
    return best, score_map[best], search


def fit_platt(oof_logits, y):
    return fit_logistic(np.asarray(oof_logits, dtype=float).reshape((-1, 1)), np.asarray(y, dtype=float), 0.01, class_balance=False)


def calibrate_logits(calibrator, logits):
    return sigmoid(predict_logits(calibrator, np.asarray(logits, dtype=float).reshape((-1, 1))))


def calibrated_inner_probabilities(traces, oof):
    all_logits = []
    all_labels = []
    for trial_key in sorted(oof["logits"]):
        for window_index in range(9):
            all_logits.append(oof["logits"][trial_key][window_index])
            all_labels.append(oof["labels"][trial_key][window_index])
    calibrator = fit_platt(all_logits, all_labels)
    probabilities = {
        trial_key: calibrate_logits(calibrator, [oof["logits"][trial_key][i] for i in range(9)])
        for trial_key in oof["logits"]
    }
    return calibrator, probabilities


def prob_outcome(trace, probabilities, context_slot, threshold):
    chosen = None
    for index, point in enumerate(trace["points"][:trace["baselineIndex"]]):
        if point["evidenceSeconds"] + 1e-12 < rules.M35_MIN_EVIDENCE:
            continue
        if point["top"] == int(context_slot) and float(probabilities[index]) + 1e-12 >= threshold:
            chosen = index
            break
    index = chosen if chosen is not None else trace["baselineIndex"]
    point = trace["points"][index]
    baseline = trace["points"][trace["baselineIndex"]]
    baseline_correct = int(baseline["top"] == trace["trueSlotIndex"])
    selected_correct = int(point["top"] == trace["trueSlotIndex"])
    applied = chosen is not None
    return {
        "selectedIndex": index,
        "selectedPrediction": point["top"],
        "selectedEvidenceSeconds": point["evidenceSeconds"],
        "baselinePrediction": baseline["top"],
        "baselineEvidenceSeconds": baseline["evidenceSeconds"],
        "baselineCorrect": baseline_correct,
        "selectedCorrect": selected_correct,
        "inducedWrongEarlyStop": int(applied and baseline_correct and not selected_correct),
        "safeFinalAtStop": int(point["top"] == trace["eegOnlyDecisionClass"]),
        "applied": int(applied),
        "gainSeconds": baseline["evidenceSeconds"] - point["evidenceSeconds"] if applied else 0.0,
        "rawTopAtStop": point["top"],
        "relativeMarginAtStop": point["relativeMargin"],
        "secondaryAgreementAtStop": point["secondaryAgreement"],
        "priorTopFlipsAtStop": point["priorTopFlips"],
        "stableUpdatesAtStop": point["stableUpdates"],
    }


def threshold_metrics(traces, inner_folds, probabilities, threshold):
    fold_metrics = {}
    congruent_n = 0
    congruent_apps = 0
    congruent_gain = 0.0
    congruent_app_gains = []
    for inner_id, split in inner_folds.items():
        wrong_apps = 0
        induced = 0
        accuracy_delta = 0
        for trial_key in split["validation"]:
            trace = traces[trial_key]
            p = probabilities[trial_key]
            congruent = prob_outcome(trace, p, trace["trueSlotIndex"], threshold)
            congruent_n += 1
            congruent_apps += congruent["applied"]
            congruent_gain += congruent["gainSeconds"]
            if congruent["applied"]:
                congruent_app_gains.append(congruent["gainSeconds"])
            for context_slot in range(3):
                if context_slot == trace["trueSlotIndex"]:
                    continue
                outcome = prob_outcome(trace, p, context_slot, threshold)
                wrong_apps += outcome["applied"]
                induced += outcome["inducedWrongEarlyStop"]
                accuracy_delta += outcome["selectedCorrect"] - outcome["baselineCorrect"]
        fold_metrics[inner_id] = {
            "wrongContextApplications": wrong_apps,
            "inducedErrors": induced,
            "inducedRisk": float(induced) / wrong_apps if wrong_apps else 0.0,
            "accuracyDelta": accuracy_delta,
        }
    max_risk = max((value["inducedRisk"] for value in fold_metrics.values()), default=0.0)
    min_accuracy_delta = min((value["accuracyDelta"] for value in fold_metrics.values()), default=0)
    return {
        "innerTrials": congruent_n,
        "congruentApplications": congruent_apps,
        "congruentCoverage": float(congruent_apps) / congruent_n if congruent_n else 0.0,
        "congruentMeanGainSeconds": congruent_gain / congruent_n if congruent_n else 0.0,
        "congruentAppliedMeanGainSeconds": sum(congruent_app_gains) / len(congruent_app_gains) if congruent_app_gains else 0.0,
        "maxInnerFoldInducedRisk": max_risk,
        "minInnerFoldAccuracyDelta": min_accuracy_delta,
        "innerFoldMetrics": fold_metrics,
    }


def threshold_candidates(probabilities):
    values = set(THRESHOLD_BASE)
    all_values = np.concatenate([np.asarray(p, dtype=float) for p in probabilities.values()])
    for quantile in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99):
        values.add(round(float(np.quantile(all_values, quantile)), 6))
    return sorted(value for value in values if value >= 0.0)


def select_threshold(traces, inner_folds, probabilities, risk_limit):
    candidates = []
    for threshold in threshold_candidates(probabilities):
        metrics = threshold_metrics(traces, inner_folds, probabilities, threshold)
        eligible = metrics["maxInnerFoldInducedRisk"] <= risk_limit + 1e-12 and metrics["minInnerFoldAccuracyDelta"] >= 0
        candidates.append((eligible, threshold, metrics))
    eligible = [item for item in candidates if item[0]]
    if not eligible:
        threshold = 1.01
        return threshold, threshold_metrics(traces, inner_folds, probabilities, threshold), candidates
    selected = max(eligible, key=lambda item: (
        item[2]["congruentCoverage"],
        item[2]["congruentAppliedMeanGainSeconds"],
        item[2]["congruentMeanGainSeconds"],
        item[1],
    ))
    return selected[1], selected[2], candidates


def calibration_bins(rows):
    bins = []
    for lower in np.linspace(0.0, 0.9, 10):
        upper = lower + 0.1
        selected = [r for r in rows if lower <= float(r["reliabilityProbability"]) < upper or (upper >= 1.0 and float(r["reliabilityProbability"]) <= upper)]
        bins.append({
            "lower": float(lower),
            "upper": float(upper),
            "nTimepoints": len(selected),
            "meanPredictedProbability": float(np.mean([float(r["reliabilityProbability"]) for r in selected])) if selected else None,
            "observedSafeTrueRate": float(np.mean([int(r["safeTrue"]) for r in selected])) if selected else None,
            "observedSafeFinalRate": float(np.mean([int(r["safeFinal"]) for r in selected])) if selected else None,
        })
    return bins


def main():
    traces_flat = rules.load_traces()
    traces_by_track = defaultdict(dict)
    for (track, trial_key), trace in traces_flat.items():
        traces_by_track[track][trial_key] = trace
    outer_rows = read_csv(OUTER_PATH)
    inner_rows = read_csv(INNER_PATH)
    inner_all = rules.read_fold_splits(inner_rows)
    decisions = []
    search_rows = []
    selected_rows = []
    reliability_rows = []
    calibration_rows = []

    for track in rules.TRACKS:
        track_traces = traces_by_track[track]
        for scheme in rules.SCHEMES:
            group_field = "acquisitionCampaignGroup" if scheme == "acquisitionCampaignGroup" else "recordingSessionGroup"
            outer_sub = [r for r in outer_rows if r["track"] == track and r["groupingScheme"] == scheme]
            outer_folds = {}
            for row in outer_sub:
                outer_folds.setdefault(row["outerFoldId"], {"train": json.loads(row["outerTrainTrialKeys"]), "test": []})
                outer_folds[row["outerFoldId"]]["test"].append(row["trialKey"])

            for outer_fold, assignment in outer_folds.items():
                train_keys = assignment["train"]
                test_keys = assignment["test"]
                train_traces = {key: track_traces[key] for key in train_keys}
                test_traces = [track_traces[key] for key in test_keys]
                inner_folds = {
                    inner_id: split for (inner_track, inner_scheme, inner_outer, inner_id), split in inner_all.items()
                    if inner_track == track and inner_scheme == scheme and inner_outer == outer_fold
                }

                best_l2, best_oof, l2_search = select_l2(train_traces, inner_folds, train_keys, group_field)
                search_rows.extend({
                    "track": track, "groupingScheme": scheme, "outerFoldId": outer_fold,
                    "method": "M36_LOGISTIC_SAFE_GATE", **row,
                } for row in l2_search)
                calibrator, inner_probabilities = calibrated_inner_probabilities(train_traces, best_oof)
                threshold_sets = {}
                for safety, risk_limit in rules.SAFETY_MODES:
                    threshold, metrics, candidates = select_threshold(train_traces, inner_folds, inner_probabilities, risk_limit)
                    threshold_sets[safety] = threshold
                    selected_rows.append({
                        "track": track,
                        "groupingScheme": scheme,
                        "outerFoldId": outer_fold,
                        "method": "M36_LOGISTIC_SAFE_GATE",
                        "safetyOperatingPoint": safety,
                        "selectedL2": best_l2,
                        "selectedReliabilityThreshold": threshold,
                        "plattParameters": json.dumps({
                            "mean": np.asarray(calibrator["mean"]).tolist(),
                            "scale": np.asarray(calibrator["scale"]).tolist(),
                            "coef": np.asarray(calibrator["coef"]).tolist(),
                        }, sort_keys=True, separators=(",", ":")),
                        "innerCongruentCoverage": metrics["congruentCoverage"],
                        "innerCongruentMeanGainSeconds": metrics["congruentMeanGainSeconds"],
                        "innerCongruentAppliedMeanGainSeconds": metrics["congruentAppliedMeanGainSeconds"],
                        "innerMaxFoldInducedRisk": metrics["maxInnerFoldInducedRisk"],
                        "innerMinimumFoldAccuracyDelta": metrics["minInnerFoldAccuracyDelta"],
                        "thresholdCandidateCount": len(candidates),
                        "thresholdSelection": "nested inner OOF calibrated probability; maximize congruent coverage under groupwise induced-risk constraint",
                    })
                    for threshold_candidate in candidates:
                        search_rows.append({
                            "track": track,
                            "groupingScheme": scheme,
                            "outerFoldId": outer_fold,
                            "method": "M36_LOGISTIC_SAFE_GATE",
                            "stage": "risk_threshold",
                            "riskTarget": safety,
                            "threshold": threshold_candidate[1],
                            "eligible": threshold_candidate[0],
                            "innerCongruentCoverage": threshold_candidate[2]["congruentCoverage"],
                            "innerCongruentMeanGainSeconds": threshold_candidate[2]["congruentMeanGainSeconds"],
                            "innerAppliedMeanGainSeconds": threshold_candidate[2]["congruentAppliedMeanGainSeconds"],
                            "innerMaxFoldInducedRisk": threshold_candidate[2]["maxInnerFoldInducedRisk"],
                            "innerMinimumFoldAccuracyDelta": threshold_candidate[2]["minInnerFoldAccuracyDelta"],
                            "selectedL2": best_l2,
                        })

                # Refit the reliability model on all outer-training trials; calibrator stays inner-OOF.
                x_train, y_train, _, _ = build_matrix(train_traces, train_keys)
                outer_model = fit_logistic(x_train, y_train, best_l2, class_balance=True)
                x_test, y_test, _, test_index = build_matrix({t["trialKey"]: t for t in test_traces}, test_keys)
                test_logits = predict_logits(outer_model, x_test)
                test_prob = calibrate_logits(calibrator, test_logits)
                prob_by_trial = {key: np.zeros(9, dtype=float) for key in test_keys}
                for (trial_key, window_index), probability, label in zip(test_index, test_prob, y_test):
                    prob_by_trial[trial_key][window_index] = float(probability)
                    trace = track_traces[trial_key]
                    reliability_rows.append({
                        "track": track,
                        "groupingScheme": scheme,
                        "outerFoldId": outer_fold,
                        "trialKey": trial_key,
                        "sessionKey": trace["sessionKey"],
                        "sessionId": trace["sessionId"],
                        "trialId": trace["trialId"],
                        "windowIndex": window_index,
                        "evidenceSeconds": trace["points"][window_index]["evidenceSeconds"],
                        "reliabilityProbability": float(probability),
                        "safeTrue": int(label),
                        "safeFinal": trace["points"][window_index]["safeFinal"],
                        "outerTestGroup": trace[group_field],
                        "trainedWithoutTrialOrOuterGroup": True,
                    })

                for safety, threshold in threshold_sets.items():
                    for trace in test_traces:
                        p = prob_by_trial[trace["trialKey"]]
                        for q in rules.Q_GRID:
                            for context_slot in range(3):
                                condition = "CONGRUENT_CONTEXT" if context_slot == trace["trueSlotIndex"] else "INCONGRUENT_CONTEXT"
                                result = prob_outcome(trace, p, context_slot, threshold)
                                row = rules.emit_decision(trace, track, scheme, outer_fold, "M36_LOGISTIC_SAFE_GATE", safety,
                                                          context_slot, q, result, condition)
                                row["reliabilityProbability"] = float(p[result["selectedIndex"]])
                                decisions.append(row)
                        neutral = rules.outcome(trace, None, 1.0 / 3.0, rules.M35_PARAMS, include_multiview=False)
                        neutral_row = rules.emit_decision(trace, track, scheme, outer_fold, "M36_LOGISTIC_SAFE_GATE", safety,
                                                          None, None, neutral, "NEUTRAL_CONTEXT")
                        neutral_row["reliabilityProbability"] = ""
                        decisions.append(neutral_row)

                # Calibration is assessed only on outer-test OOF predictions.
                reliability_subset = [r for r in reliability_rows if r["track"] == track and r["groupingScheme"] == scheme and r["outerFoldId"] == outer_fold]
                bins = calibration_bins(reliability_subset)
                for b in bins:
                    calibration_rows.append({
                        "track": track, "groupingScheme": scheme, "outerFoldId": outer_fold,
                        "outerTestGroup": ",".join(sorted(set(r["outerTestGroup"] for r in reliability_subset))),
                        **b,
                    })

    # Per-timepoint OOF calibration summary and selected-gate context replay.
    for row in reliability_rows:
        row["reliabilityProbability"] = "{:.12g}".format(float(row["reliabilityProbability"]))
    write_csv(ROOT / "logistic_oof_predictions.csv", decisions)
    write_csv(ROOT / "nested_logistic_search.csv", search_rows)
    write_csv(ROOT / "selected_logistic_policies.csv", selected_rows)
    write_csv(ROOT / "logistic_reliability_oof.csv", reliability_rows)
    write_csv(ROOT / "logistic_calibration_summary.csv", calibration_rows)
    write_csv(ROOT / "logistic_risk_coverage.csv", rules.summarize_rows(decisions))

    # Add learned-gate decisions to the unified all-method OOF table.
    prior_path = ROOT / "rule_oof_predictions.csv"
    prior_rows = read_csv(prior_path)
    all_fields = list(dict.fromkeys([field for row in prior_rows + decisions for field in row.keys()]))
    with (ROOT / "oof_predictions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=all_fields)
        writer.writeheader()
        writer.writerows(prior_rows + decisions)

    for row in search_rows:
        row.setdefault("stage", "")
        row.setdefault("riskTarget", "")
        row.setdefault("threshold", "")
        row.setdefault("eligible", "")
        row.setdefault("innerMaxFoldInducedRisk", "")
        row.setdefault("innerMinimumFoldAccuracyDelta", "")
        row.setdefault("innerGroupMetrics", "")
        row.setdefault("innerTrialCount", "")
        row.setdefault("meanInnerGroupBrier", "")
        row.setdefault("worstInnerGroupBrier", "")
        row.setdefault("meanInnerGroupLogLoss", "")
        row.setdefault("innerCongruentCoverage", "")
        row.setdefault("innerCongruentMeanGainSeconds", "")
        row.setdefault("innerAppliedMeanGainSeconds", "")
        row.setdefault("selectedL2", "")
    summary = {
        "status": "PASS",
        "reliabilityTarget": "safeTrue; eventual frozen decision consistency retained as a separate label and audit",
        "featureCount": len(traces_flat[next(iter(traces_flat))]["points"][0]["features"]),
        "featureSource": "feature_* fields only; target labels are loaded from a separate table and never enter X",
        "model": "NumPy L2 logistic regression; class-balanced train loss; nested group OOF Brier selection; Platt calibration on inner OOF logits",
        "l2Grid": list(L2_GRID),
        "treeAvailability": "NOT_AVAILABLE: sklearn/scipy/tree libraries not installed in approved local runtime; no dependency installation",
        "trainingAndValidationIsolation": "outer test trials and groups are excluded from scaler, fit, calibrator, and threshold selection",
        "outerOofTimepointRows": len(reliability_rows),
        "contextDecisionRows": len(decisions),
        "selectedPolicyRows": len(selected_rows),
        "selectedPolicies": selected_rows,
    }
    (ROOT / "logistic_evaluation_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "outerOofTimepointRows": len(reliability_rows),
        "contextDecisionRows": len(decisions),
        "selectedPolicyRows": len(selected_rows),
        "unifiedOofRows": len(prior_rows) + len(decisions),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
