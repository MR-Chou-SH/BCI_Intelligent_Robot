#!/usr/bin/env python3
"""Nested grouped evaluation of the frozen M35 and M36 deterministic gates."""

import csv
import itertools
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FEATURES = ROOT / "eeg_trajectory_features.csv"
LABELS = ROOT / "reliability_labels.csv"
OUTER = ROOT / "outer_fold_assignments.csv"
INNER = ROOT / "inner_fold_assignments.csv"
M35_REFERENCE = ROOT.parents[2] / "research_analysis" / "m35_controlled_context_causality_20261004" / "attempt-01" / "controlled_context_per_trial.csv"
Q_GRID = (0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95)
M35_MARGIN = 0.175
M35_MIN_EVIDENCE = 0.20
M35_ALPHA = 0.05
M35_FLOOR = 0.10
M35_STABLE = 4
TOL = 1e-12
TRACKS = ("track1_5ch", "track2_common3")
SCHEMES = ("acquisitionCampaignGroup", "recordingSessionGroup")
SAFETY_MODES = (("SAFE-STRICT", 0.0), ("SAFE-95", 0.05), ("SAFE-99", 0.01))


def csv_rows(path):
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        raise ValueError("refusing to write empty output: {}".format(path))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def load_traces():
    feature_rows = csv_rows(FEATURES)
    feature_names = [name for name in feature_rows[0] if name.startswith("feature_")]
    label_rows = csv_rows(LABELS)
    label_index = {}
    for row in label_rows:
        key = (row["track"], row["sessionKey"], row["trialId"], int(row["windowIndex"]))
        if key in label_index:
            raise ValueError("duplicate reliability label row: {}".format(key))
        label_index[key] = row
    traces = {}
    for row in feature_rows:
        track, session, trial_id = row["track"], row["sessionKey"], row["trialId"]
        window_index = int(row["windowIndex"])
        trial_key = session + "::" + trial_id
        key = (track, session, trial_id, window_index)
        label = label_index.get(key)
        if label is None:
            raise ValueError("feature has no separate label row: {}".format(key))
        trace_key = (track, trial_key)
        trace = traces.setdefault(trace_key, {
            "track": track,
            "trialKey": trial_key,
            "sessionKey": session,
            "sessionId": row["sessionId"],
            "trialId": trial_id,
            "trialIndex": int(row["trialIndex"]),
            "acquisitionCampaignGroup": row["acquisitionCampaignGroup"],
            "recordingSessionGroup": row["recordingSessionGroup"],
            "trueSlotIndex": int(label["trueSlotIndex"]),
            "eegOnlyDecisionClass": int(label["eegOnlyDecisionClass"]),
            "eegOnlyDecisionEvidenceSeconds": float(label["eegOnlyDecisionEvidenceSeconds"]),
            "points": [],
        })
        if trace["eegOnlyDecisionClass"] != int(label["eegOnlyDecisionClass"]) or abs(
                trace["eegOnlyDecisionEvidenceSeconds"] - float(label["eegOnlyDecisionEvidenceSeconds"])) > TOL:
            raise ValueError("trial baseline label changes across windows: {}".format(trace_key))
        point = {
            "evidenceSeconds": float(row["evidenceSeconds"]),
            "windowIndex": window_index,
            "scores": json.loads(row["mainScoresBySlot"]),
            "top": int(row["mainTopSlotIndex"]),
            "relativeMargin": float(row["mainRelativeMargin"]),
            "bandAgreement": float(row["feature_band_agreement_fraction"]),
            "harmonicAgreement": float(row["feature_harmonic_agreement_fraction"]),
            "bandMinimumMargin": float(row["feature_band_min_relative_margin"]),
            "secondaryAgreement": float(row["feature_secondary_view_agreement_fraction"]),
            "priorTopFlips": int(float(row["feature_prior_top_flips"])),
            "stableUpdates": int(float(row["feature_consecutive_same_top"])),
            "features": [float(row[name]) for name in feature_names],
            "safeTrue": int(label["safeTrue"]),
            "safeFinal": int(label["safeFinal"]),
        }
        trace["points"].append(point)
    for trace in traces.values():
        trace["points"].sort(key=lambda p: p["windowIndex"])
        if len(trace["points"]) != 9 or [p["windowIndex"] for p in trace["points"]] != list(range(9)):
            raise ValueError("trial does not have all nine windows: {}".format(trace["trialKey"]))
        trace["baselineIndex"] = eeg_only_stop_index(trace["points"])
        baseline = trace["points"][trace["baselineIndex"]]
        if baseline["top"] != trace["eegOnlyDecisionClass"] or abs(
                baseline["evidenceSeconds"] - trace["eegOnlyDecisionEvidenceSeconds"]) > TOL:
            raise ValueError("frozen EEG-only baseline does not match reliability labels: {}".format(trace["trialKey"]))
    if len(label_index) != len(feature_rows):
        raise ValueError("feature and label table row counts differ")
    return traces


def eeg_only_stop_index(points):
    for i, point in enumerate(points):
        if point["evidenceSeconds"] < M35_MIN_EVIDENCE - TOL:
            continue
        start = i - 2 + 1
        if start < 0:
            continue
        if all(points[j]["top"] == point["top"] for j in range(start, i + 1)) and point["relativeMargin"] + TOL >= M35_MARGIN:
            return i
    return len(points) - 1


def parameter_grid():
    for alpha, floor, stable, agreement, flip_limit, min_time in itertools.product(
            (0.0, 0.05), (0.05, 0.10, 0.175), (2, 3, 4),
            (0.50, 0.75, 1.00), (0, 1, None), (0.20, 0.25, 0.30)):
        yield {
            "alpha": alpha,
            "marginFloor": floor,
            "stableUpdates": stable,
            "minSecondaryAgreement": agreement,
            "maxPriorTopFlips": flip_limit,
            "minEvidenceSeconds": min_time,
        }


def find_authorization(trace, context_slot, q, params, include_multiview):
    if context_slot is None:
        return None
    baseline_index = trace["baselineIndex"]
    points = trace["points"]
    required_margin = max(float(params["marginFloor"]), M35_MARGIN - float(params["alpha"]) * (float(q) - 1.0 / 3.0))
    stable_required = int(params["stableUpdates"])
    for index in range(baseline_index):
        point = points[index]
        if point["evidenceSeconds"] + TOL < float(params["minEvidenceSeconds"]):
            continue
        if point["top"] != int(context_slot):
            continue
        if point["stableUpdates"] < stable_required or point["relativeMargin"] + TOL < required_margin:
            continue
        flip_limit = params.get("maxPriorTopFlips")
        if flip_limit is not None and point["priorTopFlips"] > int(flip_limit):
            continue
        if include_multiview and point["secondaryAgreement"] + TOL < float(params["minSecondaryAgreement"]):
            continue
        return index
    return None


M35_PARAMS = {
    "alpha": M35_ALPHA,
    "marginFloor": M35_FLOOR,
    "stableUpdates": M35_STABLE,
    "minSecondaryAgreement": 0.0,
    "maxPriorTopFlips": None,
    "minEvidenceSeconds": M35_MIN_EVIDENCE,
}


def outcome(trace, context_slot, q, params, include_multiview):
    auth_index = find_authorization(trace, context_slot, q, params, include_multiview)
    applied = auth_index is not None
    index = auth_index if applied else trace["baselineIndex"]
    point = trace["points"][index]
    baseline = trace["points"][trace["baselineIndex"]]
    baseline_correct = int(baseline["top"] == trace["trueSlotIndex"])
    selected_correct = int(point["top"] == trace["trueSlotIndex"])
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


def read_fold_splits(inner_rows):
    splits = {}
    for row in inner_rows:
        key = (row["track"], row["groupingScheme"], row["outerFoldId"], row["innerFoldId"])
        if key in splits:
            continue
        splits[key] = {
            "train": json.loads(row["innerTrainTrialKeys"]),
            "validation": json.loads(row["innerValidationTrialKeys"]),
            "embargoed": json.loads(row["embargoedTrialKeys"]),
            "method": row["innerMethod"],
        }
    return splits


def safety_metrics_for_params(traces, inner_splits, params, group_field, include_multiview=True):
    by_validation_fold = {}
    by_group = defaultdict(lambda: {"wrongApps": 0, "inducedErrors": 0, "accuracyDelta": 0, "wrongScenarioCount": 0})
    congruent_apps = 0
    congruent_gain = 0.0
    congruent_gain_applied = []
    congruent_n = 0
    wrong_apps = 0
    induced_errors = 0
    accuracy_delta_total = 0
    for fold_id, fold in inner_splits.items():
        fold_wrong_apps = 0
        fold_induced = 0
        fold_delta = 0
        fold_scenario_count = 0
        for trial_key in fold["validation"]:
            trace = traces[trial_key]
            congruent_n += 1
            congruent = outcome(trace, trace["trueSlotIndex"], 0.95, params, include_multiview)
            congruent_apps += congruent["applied"]
            congruent_gain += congruent["gainSeconds"]
            if congruent["applied"]:
                congruent_gain_applied.append(congruent["gainSeconds"])
            for context_slot in range(3):
                if context_slot == trace["trueSlotIndex"]:
                    continue
                result = outcome(trace, context_slot, 0.95, params, include_multiview)
                fold_wrong_apps += result["applied"]
                fold_induced += result["inducedWrongEarlyStop"]
                fold_delta += result["selectedCorrect"] - result["baselineCorrect"]
                fold_scenario_count += 1
                wrong_apps += result["applied"]
                induced_errors += result["inducedWrongEarlyStop"]
                accuracy_delta_total += result["selectedCorrect"] - result["baselineCorrect"]
                group = trace[group_field]
                group_metrics = by_group[group]
                group_metrics["wrongApps"] += result["applied"]
                group_metrics["inducedErrors"] += result["inducedWrongEarlyStop"]
                group_metrics["accuracyDelta"] += result["selectedCorrect"] - result["baselineCorrect"]
                group_metrics["wrongScenarioCount"] += 1
        by_validation_fold[fold_id] = {
            "wrongApps": fold_wrong_apps,
            "inducedErrors": fold_induced,
            "accuracyDelta": fold_delta,
            "wrongScenarioCount": fold_scenario_count,
        }
    max_group_risk = 0.0
    min_group_delta = 0
    group_details = []
    for group, metrics in sorted(by_group.items()):
        risk = float(metrics["inducedErrors"]) / metrics["wrongApps"] if metrics["wrongApps"] else 0.0
        max_group_risk = max(max_group_risk, risk)
        min_group_delta = min(min_group_delta, metrics["accuracyDelta"])
        group_details.append({"group": group, **metrics, "inducedRisk": risk})
    overall_risk = float(induced_errors) / wrong_apps if wrong_apps else 0.0
    return {
        "innerValidationTrials": congruent_n,
        "congruentApplications": congruent_apps,
        "congruentCoverage": float(congruent_apps) / congruent_n if congruent_n else 0.0,
        "congruentMeanGainSeconds": congruent_gain / congruent_n if congruent_n else 0.0,
        "congruentAppliedMeanGainSeconds": sum(congruent_gain_applied) / len(congruent_gain_applied) if congruent_gain_applied else 0.0,
        "wrongContextApplications": wrong_apps,
        "wrongContextInducedErrors": induced_errors,
        "wrongContextInducedRisk": overall_risk,
        "maxGroupInducedRisk": max_group_risk,
        "minGroupAccuracyDelta": min_group_delta,
        "incongruentAccuracyDelta": accuracy_delta_total,
        "validationFoldMetrics": by_validation_fold,
        "groupMetrics": group_details,
    }


def select_rule(traces, inner_folds, safety_risk_limit, group_field):
    candidates = []
    for params in parameter_grid():
        metrics = safety_metrics_for_params(traces, inner_folds, params, group_field, include_multiview=True)
        eligible = metrics["maxGroupInducedRisk"] <= safety_risk_limit + TOL and metrics["minGroupAccuracyDelta"] >= 0
        candidates.append((eligible, metrics, params))
    eligible = [item for item in candidates if item[0]]
    if not eligible:
        fallback = {
            "alpha": 0.0,
            "marginFloor": 1.0,
            "stableUpdates": 9,
            "minSecondaryAgreement": 1.0,
            "maxPriorTopFlips": 0,
            "minEvidenceSeconds": 1.0,
        }
        metrics = safety_metrics_for_params(traces, inner_folds, fallback, group_field, include_multiview=True)
        return fallback, metrics, len(candidates), "no candidate met groupwise safety/accuracy constraints; exact EEG-only fallback"
    selected = max(eligible, key=lambda item: (
        item[1]["congruentCoverage"],
        item[1]["congruentAppliedMeanGainSeconds"],
        item[1]["congruentMeanGainSeconds"],
        -item[1]["wrongContextInducedRisk"],
    ))
    return selected[2], selected[1], len(candidates), "nested inner validation; max congruent coverage under groupwise induced-risk limit"


def summarize_rows(rows):
    by = defaultdict(list)
    for row in rows:
        key = (row["track"], row["groupingScheme"], row["method"], row["safetyOperatingPoint"], row["condition"], row["qTop"])
        by[key].append(row)
    summary = []
    for (track, scheme, method, safety, condition, q), group in sorted(by.items(), key=lambda item: str(item[0])):
        applied = [r for r in group if int(r["contextApplied"])]
        correct_applied = sum(int(r["selectedCorrect"]) for r in applied)
        wrong_applications = sum(int(r["condition"] == "INCONGRUENT_CONTEXT" and int(r["contextApplied"])) for r in group)
        induced = sum(int(r["contextInducedWrongEarlyStop"]) for r in group)
        scenario_count = len(group)
        real_trials = len(set(r["trialKey"] for r in group))
        gains = [float(r["pairedGainSeconds"]) for r in group]
        evidence = [float(r["selectedEvidenceSeconds"]) for r in group]
        context_apps = sum(int(r["contextApplied"]) for r in group)
        summary.append({
            "track": track,
            "groupingScheme": scheme,
            "method": method,
            "safetyOperatingPoint": safety,
            "condition": condition,
            "qTop": q,
            "realEegTrials": real_trials,
            "contextReplayScenarios": scenario_count,
            "contextApplications": context_apps,
            "eligibleCongruentOpportunities": real_trials if condition == "CONGRUENT_CONTEXT" else "",
            "applicationCoverage": float(context_apps) / real_trials if real_trials else 0.0,
            "interventionPrecision": float(correct_applied) / len(applied) if applied else None,
            "inducedWrongEarlyStops": induced,
            "wrongContextApplications": wrong_applications,
            "inducedRiskAmongWrongContextApplications": float(induced) / wrong_applications if wrong_applications else 0.0,
            "meanEvidenceSeconds": sum(evidence) / scenario_count if scenario_count else None,
            "overallMeanGainSeconds": sum(gains) / scenario_count if scenario_count else None,
            "appliedTrialMeanGainSeconds": sum(float(r["pairedGainSeconds"]) for r in applied) / len(applied) if applied else None,
            "fractionEvidenceLE030": sum(x <= 0.30 + TOL for x in evidence) / scenario_count if scenario_count else None,
            "accuracy": sum(int(r["selectedCorrect"]) for r in group) / scenario_count if scenario_count else None,
            "meanFinalClassPreservation": sum(int(r["safeFinalAtStop"]) for r in group) / scenario_count if scenario_count else None,
        })
    return summary


def emit_decision(trace, track, scheme, outer_fold, method, safety, context_slot, q, result, condition):
    return {
        "track": track,
        "groupingScheme": scheme,
        "outerFoldId": outer_fold,
        "method": method,
        "safetyOperatingPoint": safety,
        "trialKey": trace["trialKey"],
        "sessionKey": trace["sessionKey"],
        "sessionId": trace["sessionId"],
        "trialId": trace["trialId"],
        "trueSlotIndex": trace["trueSlotIndex"],
        "condition": condition,
        "qTop": "{:.2f}".format(q) if q is not None else "",
        "contextSlotIndex": context_slot if context_slot is not None else "",
        "baselinePrediction": result["baselinePrediction"],
        "baselineEvidenceSeconds": result["baselineEvidenceSeconds"],
        "selectedPrediction": result["selectedPrediction"],
        "selectedEvidenceSeconds": result["selectedEvidenceSeconds"],
        "rawTopAtStop": result["rawTopAtStop"],
        "contextApplied": result["applied"],
        "pairedGainSeconds": result["gainSeconds"],
        "baselineCorrect": result["baselineCorrect"],
        "selectedCorrect": result["selectedCorrect"],
        "contextInducedWrongEarlyStop": result["inducedWrongEarlyStop"],
        "safeFinalAtStop": result["safeFinalAtStop"],
        "relativeMarginAtStop": result["relativeMarginAtStop"],
        "secondaryAgreementAtStop": result["secondaryAgreementAtStop"],
        "priorTopFlipsAtStop": result["priorTopFlipsAtStop"],
        "stableUpdatesAtStop": result["stableUpdatesAtStop"],
    }


def verify_m35_reproduction(rows):
    if not M35_REFERENCE.exists():
        return {"status": "NOT_AVAILABLE", "matchedRows": 0, "reason": "M35 controlled result table unavailable"}
    ref_rows = csv_rows(M35_REFERENCE)
    references = {}
    for row in ref_rows:
        if row["condition"] not in ("CONGRUENT_CONTEXT", "INCONGRUENT_CONTEXT", "NEUTRAL_CONTEXT"):
            continue
        key = (row["sessionKey"], row["trialId"], row["condition"], row["qTop"], row["contextSlotIndex"])
        if key in references:
            raise ValueError("duplicate M35 comparison row: {}".format(key))
        references[key] = row
    compared = 0
    for row in rows:
        if row["method"] != "M35_CONSERVATIVE" or row["qTop"] not in ("0.45", "0.95"):
            continue
        session = row["sessionKey"]
        # M35 used the frozen 5-channel track for A/B1/B2 and S7's common-three view.
        if row["track"] == "track2_common3" and session != "S7":
            continue
        if row["track"] == "track1_5ch" and session == "S7":
            continue
        ref_q = row["qTop"] if row["condition"] != "NEUTRAL_CONTEXT" else "0.3333333333333333"
        key = (session, row["trialId"], row["condition"], ref_q, str(row["contextSlotIndex"]) if row["contextSlotIndex"] != "" else "")
        ref = references.get(key)
        if ref is None:
            continue
        if int(row["selectedPrediction"]) != int(ref["assistedPrediction"]):
            raise ValueError("M35 prediction mismatch {}".format(key))
        if abs(float(row["selectedEvidenceSeconds"]) - float(ref["assistedEvidenceSeconds"])) > TOL:
            raise ValueError("M35 evidence mismatch {}".format(key))
        if int(row["contextApplied"]) != int(ref["contextApplied"]):
            raise ValueError("M35 application mismatch {}".format(key))
        compared += 1
    return {"status": "PASS" if compared else "NOT_AVAILABLE", "matchedRows": compared,
            "comparedStrengths": [0.45, 0.95], "comparedTrackPolicy": "5ch for A/B1/B2; common-three for S7"}


def main():
    traces = load_traces()
    outer_rows = csv_rows(OUTER)
    inner_rows = csv_rows(INNER)
    inner_splits_all = read_fold_splits(inner_rows)
    decisions = []
    search_rows = []
    selected_rows = []
    selected_config_by_outer = {}

    for track in TRACKS:
        track_traces = {key[1]: value for key, value in traces.items() if key[0] == track}
        for scheme in SCHEMES:
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
                    inner_id: split for (inner_track, inner_scheme, inner_outer, inner_id), split in inner_splits_all.items()
                    if inner_track == track and inner_scheme == scheme and inner_outer == outer_fold
                }
                for safety, risk_limit in SAFETY_MODES:
                    group_field = "acquisitionCampaignGroup" if scheme == "acquisitionCampaignGroup" else "recordingSessionGroup"
                    params, train_metrics, candidate_count, selection_reason = select_rule(
                        train_traces, inner_folds, risk_limit, group_field)
                    selected_config_by_outer[(track, scheme, outer_fold, safety)] = params
                    selected_rows.append({
                        "track": track,
                        "groupingScheme": scheme,
                        "outerFoldId": outer_fold,
                        "safetyOperatingPoint": safety,
                        "selectedMethod": "M36_MULTI_VIEW_RULE",
                        "candidateCount": candidate_count,
                        "selectedParameters": json.dumps(params, sort_keys=True, separators=(",", ":")),
                        "innerCongruentCoverage": train_metrics["congruentCoverage"],
                        "innerCongruentMeanGainSeconds": train_metrics["congruentMeanGainSeconds"],
                        "innerCongruentAppliedMeanGainSeconds": train_metrics["congruentAppliedMeanGainSeconds"],
                        "innerWrongContextApplications": train_metrics["wrongContextApplications"],
                        "innerInducedWrongEarlyStops": train_metrics["wrongContextInducedErrors"],
                        "innerWorstGroupInducedRisk": train_metrics["maxGroupInducedRisk"],
                        "innerMinimumGroupAccuracyDelta": train_metrics["minGroupAccuracyDelta"],
                        "selectionReason": selection_reason,
                    })

                for params in parameter_grid():
                    train_metrics = safety_metrics_for_params(train_traces, inner_folds, params, group_field, include_multiview=True)
                    search_rows.append({
                        "track": track,
                        "groupingScheme": scheme,
                        "outerFoldId": outer_fold,
                        "method": "M36_MULTI_VIEW_RULE",
                        "parameters": json.dumps(params, sort_keys=True, separators=(",", ":")),
                        "innerCongruentCoverage": train_metrics["congruentCoverage"],
                        "innerCongruentMeanGainSeconds": train_metrics["congruentMeanGainSeconds"],
                        "innerCongruentAppliedMeanGainSeconds": train_metrics["congruentAppliedMeanGainSeconds"],
                        "innerWrongContextApplications": train_metrics["wrongContextApplications"],
                        "innerInducedWrongEarlyStops": train_metrics["wrongContextInducedErrors"],
                        "innerWorstGroupInducedRisk": train_metrics["maxGroupInducedRisk"],
                        "innerMinimumGroupAccuracyDelta": train_metrics["minGroupAccuracyDelta"],
                    })

                for trace in test_traces:
                    # M35 policy is fixed and not selected from the outer test fold.
                    for q in Q_GRID:
                        for context_slot in range(3):
                            condition = "CONGRUENT_CONTEXT" if context_slot == trace["trueSlotIndex"] else "INCONGRUENT_CONTEXT"
                            result = outcome(trace, context_slot, q, M35_PARAMS, include_multiview=False)
                            decisions.append(emit_decision(trace, track, scheme, outer_fold, "M35_CONSERVATIVE", "FIXED", context_slot, q, result, condition))
                    # Nested-selected M36 deterministic rules.
                    for safety, _ in SAFETY_MODES:
                        params = selected_config_by_outer[(track, scheme, outer_fold, safety)]
                        for q in Q_GRID:
                            for context_slot in range(3):
                                condition = "CONGRUENT_CONTEXT" if context_slot == trace["trueSlotIndex"] else "INCONGRUENT_CONTEXT"
                                result = outcome(trace, context_slot, q, params, include_multiview=True)
                                decisions.append(emit_decision(trace, track, scheme, outer_fold, "M36_MULTI_VIEW_RULE", safety, context_slot, q, result, condition))
                        neutral = outcome(trace, None, 1.0 / 3.0, params, include_multiview=True)
                        decisions.append(emit_decision(trace, track, scheme, outer_fold, "M36_MULTI_VIEW_RULE", safety, None, None, neutral, "NEUTRAL_CONTEXT"))
                    # M35 neutral reference check is exact fallback.
                    neutral_m35 = outcome(trace, None, 1.0 / 3.0, M35_PARAMS, include_multiview=False)
                    decisions.append(emit_decision(trace, track, scheme, outer_fold, "M35_CONSERVATIVE", "FIXED", None, None, neutral_m35, "NEUTRAL_CONTEXT"))

    decisions_path = ROOT / "rule_oof_predictions.csv"
    search_path = ROOT / "nested_cv_search.csv"
    selected_path = ROOT / "selected_rule_policies.csv"
    metrics_path = ROOT / "rule_risk_coverage.csv"
    write_csv(decisions_path, decisions)
    write_csv(search_path, search_rows)
    write_csv(selected_path, selected_rows)
    write_csv(metrics_path, summarize_rows(decisions))
    reproduction = verify_m35_reproduction(decisions)
    reproduction_path = ROOT / "m35_rule_reproduction.json"
    reproduction_path.write_text(json.dumps(reproduction, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = {
        "status": "PASS" if reproduction["status"] == "PASS" else "CHECK",
        "realTrialsByTrack": {track: len({key[1] for key in traces if key[0] == track}) for track in TRACKS},
        "decisionRows": len(decisions),
        "nestedSearchRows": len(search_rows),
        "selectedPolicyRows": len(selected_rows),
        "m35Reproduction": reproduction,
        "contextSweep": list(Q_GRID),
        "argmaxInvariant": "selectedPrediction always equals raw EEG argmax at selectedEvidenceSeconds; Context only gates authorization",
    }
    (ROOT / "rule_evaluation_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
