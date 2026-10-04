#!/usr/bin/env python3
"""M36 failure mining, session-shift summaries, oracle bounds, and stored-M33 replay."""

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import m36_rule_evaluation as rules


ROOT = Path(__file__).resolve().parent
M34 = ROOT.parents[2] / "research_analysis" / "m34_high_speed_ssvep_context_20261004" / "attempt-01"
TOL = 1e-12


def read_csv(path, encoding="utf-8"):
    with path.open("r", newline="", encoding=encoding) as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        with path.open("w", encoding="utf-8", newline="") as stream:
            stream.write("\n")
        return
    fields = list(dict.fromkeys(k for row in rows for k in row.keys()))
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_failure_cases():
    feature_rows = read_csv(ROOT / "eeg_trajectory_features.csv")
    traces = defaultdict(list)
    for row in feature_rows:
        meta_key = (row["track"], row["sessionKey"], row["trialId"])
        traces[meta_key].append(row)
    for trajectory in traces.values():
        trajectory.sort(key=lambda r: int(r["windowIndex"]))
    labels = defaultdict(list)
    for row in read_csv(ROOT / "reliability_labels.csv"):
        labels[(row["track"], row["sessionKey"], row["trialId"])].append(row)
    probabilities = defaultdict(dict)
    for row in read_csv(ROOT / "logistic_reliability_oof.csv"):
        probabilities[(row["track"], row["groupingScheme"], row["outerFoldId"], row["trialKey"])][int(row["windowIndex"])] = float(row["reliabilityProbability"])

    failure_rows = []
    failures = [r for r in read_csv(ROOT / "oof_predictions.csv")
                if r["condition"] == "INCONGRUENT_CONTEXT" and int(r["contextInducedWrongEarlyStop"]) == 1]
    for row in failures:
        meta_key = (row["track"], row["sessionKey"], row["trialId"])
        trajectory = traces[meta_key]
        label_rows = sorted(labels[meta_key], key=lambda r: int(r["windowIndex"]))
        target = int(row["trueSlotIndex"])
        stop_time = float(row["selectedEvidenceSeconds"])
        correction = next((float(x["evidenceSeconds"]) for x in trajectory
                           if float(x["evidenceSeconds"]) > stop_time + TOL and int(x["mainTopSlotIndex"]) == target), None)
        probs = probabilities.get((row["track"], row["groupingScheme"], row["outerFoldId"], row["trialKey"]), {})
        probability_trace = [probs.get(int(x["windowIndex"])) for x in trajectory]
        failure_rows.append({
            "track": row["track"],
            "groupingScheme": row["groupingScheme"],
            "outerFoldId": row["outerFoldId"],
            "method": row["method"],
            "safetyOperatingPoint": row["safetyOperatingPoint"],
            "trialKey": row["trialKey"],
            "sessionKey": row["sessionKey"],
            "sessionId": row["sessionId"],
            "trialId": row["trialId"],
            "acquisitionCampaignGroup": row["acquisitionCampaignGroup"],
            "outerTestGroup": row["outerTestGroup"],
            "trueSlotIndex": target,
            "wrongContextSlotIndex": row["contextSlotIndex"],
            "qTop": row["qTop"],
            "earlyStopEvidenceSeconds": stop_time,
            "earlyStopPrediction": row["selectedPrediction"],
            "contextInducedWrongEarlyStop": 1,
            "baselinePrediction": row["baselinePrediction"],
            "baselineEvidenceSeconds": row["baselineEvidenceSeconds"],
            "baselineWasCorrect": row["baselineCorrect"],
            "frozenEegOnlyFinalClass": label_rows[0]["eegOnlyDecisionClass"],
            "firstLaterCorrectRawTopEvidenceSeconds": correction,
            "selectedStopProbability": row.get("reliabilityProbability", ""),
            "rawTopTrajectory": json.dumps([int(x["mainTopSlotIndex"]) for x in trajectory], separators=(",", ":")),
            "scoreTrajectory": json.dumps([json.loads(x["mainScoresBySlot"]) for x in trajectory], separators=(",", ":")),
            "relativeMarginTrajectory": json.dumps([float(x["mainRelativeMargin"]) for x in trajectory], separators=(",", ":")),
            "bandAgreementTrajectory": json.dumps([float(x["feature_band_agreement_fraction"]) for x in trajectory], separators=(",", ":")),
            "harmonicAgreementTrajectory": json.dumps([float(x["feature_harmonic_agreement_fraction"]) for x in trajectory], separators=(",", ":")),
            "secondaryAgreementTrajectory": json.dumps([float(x["feature_secondary_view_agreement_fraction"]) for x in trajectory], separators=(",", ":")),
            "reliabilityProbabilityTrajectory": json.dumps(probability_trace, separators=(",", ":")),
        })
    write_csv(ROOT / "failure_cases.csv", failure_rows)
    unique = defaultdict(set)
    event_counts = Counter()
    for row in failure_rows:
        key = (row["method"], row["track"], row["groupingScheme"], row["safetyOperatingPoint"], row["sessionKey"], row["wrongContextSlotIndex"])
        unique[key].add((row["trialKey"], row["qTop"]))
        event_counts[key] += 1
    summary = []
    for key, pairs in sorted(unique.items()):
        trial_keys = {trial for trial, _ in pairs}
        summary.append({
            "method": key[0], "track": key[1], "groupingScheme": key[2], "safetyOperatingPoint": key[3],
            "sessionKey": key[4], "wrongContextSlotIndex": key[5],
            "failedTrialTargetPairs": len(trial_keys),
            "qReplayEvents": event_counts[key],
            "qValues": json.dumps(sorted({q for _, q in pairs if q}), separators=(",", ":")),
        })
    write_csv(ROOT / "failure_case_summary.csv", summary)
    return failure_rows, summary


def session_shift_analysis():
    rows = read_csv(ROOT / "eeg_trajectory_features.csv")
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["track"], row["sessionKey"], float(row["evidenceSeconds"]))].append(row)
    out = []
    metrics = {
        "topScore": "feature_top_score",
        "relativeMargin": "feature_relative_margin",
        "normalizedEntropy": "feature_normalized_score_entropy",
        "priorTopFlips": "feature_prior_top_flips",
        "bandAgreement": "feature_band_agreement_fraction",
        "harmonicAgreement": "feature_harmonic_agreement_fraction",
        "secondaryAgreement": "feature_secondary_view_agreement_fraction",
        "bandMinimumMargin": "feature_band_min_relative_margin",
    }
    for (track, session, evidence), group in sorted(grouped.items()):
        item = {"track": track, "sessionKey": session, "evidenceSeconds": evidence, "nRealTrials": len(group)}
        for name, column in metrics.items():
            values = [float(row[column]) for row in group]
            item[name + "Mean"] = float(np.mean(values))
            item[name + "Median"] = float(np.median(values))
            item[name + "Std"] = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
            item[name + "P10"] = float(np.quantile(values, 0.10))
            item[name + "P90"] = float(np.quantile(values, 0.90))
        out.append(item)

    # Standardized mean differences versus all other sessions at the same track/time.
    for row in out:
        peers = [x for x in out if x["track"] == row["track"] and abs(x["evidenceSeconds"] - row["evidenceSeconds"]) < TOL and x["sessionKey"] != row["sessionKey"]]
        for name in metrics:
            peer_means = [x[name + "Mean"] for x in peers]
            peer_stds = [x[name + "Std"] for x in peers]
            peer_n = [x["nRealTrials"] for x in peers]
            if not peers:
                row[name + "SmdVsOtherSessions"] = None
                continue
            peer_mean = float(np.average(peer_means, weights=peer_n))
            pooled_var = float(np.average([s * s for s in peer_stds], weights=peer_n))
            pooled_std = math.sqrt(max(pooled_var, 1e-12))
            row[name + "SmdVsOtherSessions"] = (row[name + "Mean"] - peer_mean) / pooled_std
    write_csv(ROOT / "session_shift_analysis.csv", out)

    calibration = read_csv(ROOT / "logistic_calibration_summary.csv")
    calib_summary = []
    for row in calibration:
        if row["nTimepoints"] == "0":
            continue
        calib_summary.append({
            "track": row["track"], "groupingScheme": row["groupingScheme"],
            "outerFoldId": row["outerFoldId"], "outerTestGroup": row["outerTestGroup"],
            "probabilityBinLower": row["lower"], "probabilityBinUpper": row["upper"],
            "nTimepoints": row["nTimepoints"], "meanPredictedProbability": row["meanPredictedProbability"],
            "observedSafeTrueRate": row["observedSafeTrueRate"], "observedSafeFinalRate": row["observedSafeFinalRate"],
        })
    write_csv(ROOT / "oof_calibration_by_group.csv", calib_summary)
    return out, calib_summary


def oracle_analysis(traces_flat):
    outer_rows = read_csv(ROOT / "outer_fold_assignments.csv")
    specs = (
        ("oracle_unconstrained_correct_top", 1, 0.0, 0.0, 0.0),
        ("oracle_stable_two", 2, 0.0, 0.0, 0.0),
        ("oracle_stable_two_margin005", 2, 0.05, 0.0, 0.0),
        ("oracle_stable_two_baseline_margin", 2, 0.175, 0.0, 0.0),
        ("oracle_multiview_band_harmonic", 2, 0.05, 2.0 / 3.0, 2.0 / 3.0),
    )
    per_trial = []
    for assignment in outer_rows:
        track = assignment["track"]
        trial_key = assignment["trialKey"]
        trace = traces_flat[(track, trial_key)]
        base_index = trace["baselineIndex"]
        baseline = trace["points"][base_index]
        for name, stable_required, min_margin, min_band, min_harmonic in specs:
            selected = None
            for index, point in enumerate(trace["points"][:base_index]):
                if point["evidenceSeconds"] + TOL < rules.M35_MIN_EVIDENCE:
                    continue
                if point["top"] != trace["trueSlotIndex"] or point["stableUpdates"] < stable_required:
                    continue
                if point["relativeMargin"] + TOL < min_margin:
                    continue
                if point["bandAgreement"] + TOL < min_band or point["harmonicAgreement"] + TOL < min_harmonic:
                    continue
                selected = index
                break
            chosen_index = selected if selected is not None else base_index
            chosen = trace["points"][chosen_index]
            per_trial.append({
                "track": track,
                "groupingScheme": assignment["groupingScheme"],
                "outerFoldId": assignment["outerFoldId"],
                "outerTestGroup": assignment["outerTestGroup"],
                "sessionKey": trace["sessionKey"],
                "sessionId": trace["sessionId"],
                "trialKey": trial_key,
                "trialId": trace["trialId"],
                "oracleCondition": name,
                "trueSlotIndex": trace["trueSlotIndex"],
                "eegOnlyPrediction": baseline["top"],
                "eegOnlyEvidenceSeconds": baseline["evidenceSeconds"],
                "oraclePrediction": chosen["top"],
                "oracleEvidenceSeconds": chosen["evidenceSeconds"],
                "oracleApplication": int(selected is not None),
                "oracleCorrect": int(chosen["top"] == trace["trueSlotIndex"]),
                "pairedGainSeconds": baseline["evidenceSeconds"] - chosen["evidenceSeconds"] if selected is not None else 0.0,
            })
    write_csv(ROOT / "oracle_per_trial.csv", per_trial)
    grouped = defaultdict(list)
    for row in per_trial:
        grouped[(row["track"], row["groupingScheme"], row["oracleCondition"])].append(row)
    summaries = []
    for key, group in sorted(grouped.items()):
        unique_trials = {row["trialKey"] for row in group}
        applied = [row for row in group if int(row["oracleApplication"])]
        summaries.append({
            "track": key[0], "groupingScheme": key[1], "oracleCondition": key[2],
            "realEegTrials": len(unique_trials),
            "meanOracleGainSeconds": sum(float(r["pairedGainSeconds"]) for r in group) / len(group),
            "meanGainAmongAcceleratedSeconds": sum(float(r["pairedGainSeconds"]) for r in applied) / len(applied) if applied else None,
            "applicationRate": len(applied) / len(group),
            "meanEvidenceSeconds": sum(float(r["oracleEvidenceSeconds"]) for r in group) / len(group),
            "<=300msFraction": sum(float(r["oracleEvidenceSeconds"]) <= 0.30 + TOL for r in group) / len(group),
            "accuracy": sum(int(r["oracleCorrect"]) for r in group) / len(group),
            "upperBound": True,
        })
    actual = read_csv(ROOT / "controlled_context_results.csv")
    achieved_comparisons = []
    for summary in summaries:
        candidates = [r for r in actual if r["track"] == summary["track"] and r["groupingScheme"] == summary["groupingScheme"]
                     and r["condition"] == "CONGRUENT_CONTEXT" and r["qTop"] == "0.95"]
        for row in candidates:
            if row["method"] == "M35_CONSERVATIVE" or row["safetyOperatingPoint"] == "SAFE-STRICT":
                achieved_gain = float(row["overallMeanGainSeconds"])
                achieved_comparisons.append({
                    "track": summary["track"],
                    "groupingScheme": summary["groupingScheme"],
                    "oracleCondition": summary["oracleCondition"],
                    "method": row["method"],
                    "safetyOperatingPoint": row["safetyOperatingPoint"],
                    "qTop": 0.95,
                    "achievedMeanGainSeconds": achieved_gain,
                    "oracleMeanGainSeconds": summary["meanOracleGainSeconds"],
                    "fractionOfOracleMeanGain": achieved_gain / summary["meanOracleGainSeconds"] if summary["meanOracleGainSeconds"] > 0 else None,
                    "achievedApplications": int(row["contextApplications"]),
                    "achievedWrongEarlyStops": int(row["inducedWrongEarlyStopEvents"]),
                })
    (ROOT / "oracle_summary.json").write_text(json.dumps({
        "status": "PASS",
        "specifications": [{"name": n, "stableUpdates": s, "minimumRelativeMargin": m, "minBandAgreement": b, "minHarmonicAgreement": h} for n, s, m, b, h in specs],
        "summaries": summaries,
        "achievedGateComparisonAtQ095": achieved_comparisons,
        "allTrialsOuterOofGrouped": True,
        "interpretation": "Upper bound because oracle uses the ground-truth class to identify early correct EEG tops; it never changes the emitted class.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summaries


def m33_secondary(traces_flat):
    source = read_csv(M34 / "context_heldout_per_trial_seed.csv", encoding="utf-8-sig")
    source = [r for r in source if r["scenario"] == "precomputed_before_eeg"]
    outer_rows = [r for r in read_csv(ROOT / "outer_fold_assignments.csv")
                  if r["track"] == "track2_common3" and r["groupingScheme"] == "acquisitionCampaignGroup"]
    fold_by_trial = {r["trialKey"]: r["outerFoldId"] for r in outer_rows}
    policies = {(r["track"], r["groupingScheme"], r["outerFoldId"], r["safetyOperatingPoint"]): r
                for r in read_csv(ROOT / "selected_logistic_policies.csv")}
    guard_policies = {(r["track"], r["groupingScheme"], r["outerFoldId"], r["safetyOperatingPoint"]): r
                      for r in read_csv(ROOT / "selected_view_guard_policies.csv")}
    rule_policies = {(r["track"], r["groupingScheme"], r["outerFoldId"], r["safetyOperatingPoint"]): json.loads(r["selectedParameters"])
                     for r in read_csv(ROOT / "selected_rule_policies.csv")}
    p_rows = read_csv(ROOT / "logistic_reliability_oof.csv")
    p_by_trial = defaultdict(lambda: [0.0] * 9)
    for row in p_rows:
        if row["groupingScheme"] == "acquisitionCampaignGroup":
            p_by_trial[(row["track"], row["outerFoldId"], row["trialKey"])][int(row["windowIndex"])] = float(row["reliabilityProbability"])

    per_seed = []
    for row in source:
        session = row["sessionKey"]
        trial_id = row["trialId"]
        trial_key = session + "::" + trial_id
        trace = traces_flat[("track2_common3", trial_key)]
        outer_fold = fold_by_trial[trial_key]
        p = p_by_trial[("track2_common3", outer_fold, trial_key)]
        semantic_available = int(row["semanticGateActive"] or 0) == 1 and int(row["alignmentFallback"] or 0) == 0
        context_slot = int(row["contextPredictedSlotIndex"]) if semantic_available and row["contextPredictedSlotIndex"] != "" else None
        q_page = json.loads(row["qPage"]) if row["qPage"] else []
        context_q = float(row["pageConditionalTopMass"]) if row["pageConditionalTopMass"] else (max(q_page) if q_page else 1.0 / 3.0)
        policies_to_test = [("M35_CONSERVATIVE", "FIXED", rules.M35_PARAMS, None)]
        for safety, _ in rules.SAFETY_MODES:
            logistic_policy = policies[("track2_common3", "acquisitionCampaignGroup", outer_fold, safety)]
            policies_to_test.append(("M36_LOGISTIC_SAFE_GATE", safety, None, {
                "threshold": float(logistic_policy["selectedReliabilityThreshold"]),
                "band": 0.0, "harmonic": 0.0, "bandMargin": 0.0,
            }))
            guarded = guard_policies[("track2_common3", "acquisitionCampaignGroup", outer_fold, safety)]
            policies_to_test.append(("M36_LOGISTIC_VIEW_GUARDED", safety, None, {
                "threshold": float(guarded["selectedReliabilityThreshold"]),
                "band": float(guarded["minimumBandAgreement"]),
                "harmonic": float(guarded["minimumHarmonicAgreement"]),
                "bandMargin": float(guarded["minimumBandMargin"]),
            }))
            rule = rule_policies[("track2_common3", "acquisitionCampaignGroup", outer_fold, safety)]
            policies_to_test.append(("M36_MULTI_VIEW_RULE", safety, rule, None))

        for method, safety, rule_params, log_params in policies_to_test:
            if not semantic_available or context_slot is None:
                result = rules.outcome(trace, None, context_q, rules.M35_PARAMS, include_multiview=False)
            elif method == "M35_CONSERVATIVE":
                result = rules.outcome(trace, context_slot, context_q, rule_params, include_multiview=False)
            elif method == "M36_MULTI_VIEW_RULE":
                result = rules.outcome(trace, context_slot, context_q, rule_params, include_multiview=True)
            else:
                selected = None
                for index, point in enumerate(trace["points"][:trace["baselineIndex"]]):
                    if point["evidenceSeconds"] + TOL < rules.M35_MIN_EVIDENCE or point["top"] != context_slot:
                        continue
                    if p[index] + TOL < log_params["threshold"]:
                        continue
                    if point["bandAgreement"] + TOL < log_params["band"] or point["harmonicAgreement"] + TOL < log_params["harmonic"]:
                        continue
                    if point["bandMinimumMargin"] + TOL < log_params["bandMargin"]:
                        continue
                    selected = index
                    break
                if selected is None:
                    result = rules.outcome(trace, None, context_q, rules.M35_PARAMS, include_multiview=False)
                else:
                    point = trace["points"][selected]
                    baseline = trace["points"][trace["baselineIndex"]]
                    result = {
                        "selectedIndex": selected,
                        "selectedPrediction": point["top"],
                        "selectedEvidenceSeconds": point["evidenceSeconds"],
                        "baselinePrediction": baseline["top"],
                        "baselineEvidenceSeconds": baseline["evidenceSeconds"],
                        "baselineCorrect": int(baseline["top"] == trace["trueSlotIndex"]),
                        "selectedCorrect": int(point["top"] == trace["trueSlotIndex"]),
                        "inducedWrongEarlyStop": int(baseline["top"] == trace["trueSlotIndex"] and point["top"] != trace["trueSlotIndex"]),
                        "safeFinalAtStop": int(point["top"] == trace["eegOnlyDecisionClass"]),
                        "applied": 1,
                        "gainSeconds": baseline["evidenceSeconds"] - point["evidenceSeconds"],
                        "rawTopAtStop": point["top"],
                        "relativeMarginAtStop": point["relativeMargin"],
                        "secondaryAgreementAtStop": point["secondaryAgreement"],
                        "priorTopFlipsAtStop": point["priorTopFlips"],
                        "stableUpdatesAtStop": point["stableUpdates"],
                    }
            per_seed.append({
                "scenario": row["scenario"],
                "sessionKey": session,
                "trialKey": trial_key,
                "trialId": trial_id,
                "assignmentSeed": row["assignmentSeed"],
                "track": "track2_common3",
                "groupingScheme": "acquisitionCampaignGroup",
                "outerFoldId": outer_fold,
                "method": method,
                "safetyOperatingPoint": safety,
                "semanticContextAvailable": int(semantic_available),
                "contextPredictedSlotIndex": context_slot if context_slot is not None else "",
                "contextConfidenceTopMass": context_q,
                "trueSlotIndex": trace["trueSlotIndex"],
                "baselinePrediction": result["baselinePrediction"],
                "baselineEvidenceSeconds": result["baselineEvidenceSeconds"],
                "selectedPrediction": result["selectedPrediction"],
                "selectedEvidenceSeconds": result["selectedEvidenceSeconds"],
                "contextAppliedByM36Gate": result["applied"],
                "pairedGainSeconds": result["gainSeconds"],
                "baselineCorrect": result["baselineCorrect"],
                "selectedCorrect": result["selectedCorrect"],
                "contextInducedWrongEarlyStop": result["inducedWrongEarlyStop"],
                "originalM33ContextApplied": int(row["contextApplied"] or 0),
                "originalM33ContextCausedError": int(row["contextCausedError"] or 0),
                "originalM33ContextCorrect": int(row["contextCorrect"] or 0),
                "originalM33PairedGainSeconds": row["pairedGainSeconds"],
                "exactEegOnlyFallback": int(not result["applied"]),
            })

    write_csv(ROOT / "real_m33_secondary_per_seed.csv", per_seed)
    grouped = defaultdict(list)
    for row in per_seed:
        grouped[(row["method"], row["safetyOperatingPoint"], row["sessionKey"])].append(row)
    summaries = []
    for key, group in sorted(grouped.items()):
        trial_keys = {r["trialKey"] for r in group}
        apps = [r for r in group if int(r["contextAppliedByM36Gate"])]
        summaries.append({
            "method": key[0], "safetyOperatingPoint": key[1], "sessionKey": key[2],
            "realEegTrials": len(trial_keys), "trialSeedReplayScenarios": len(group),
            "semanticContextAvailableScenarios": sum(int(r["semanticContextAvailable"]) for r in group),
            "M36ContextApplications": len(apps),
            "applicationRateAcrossTrialSeedScenarios": len(apps) / len(group) if group else None,
            "meanGainSeconds": sum(float(r["pairedGainSeconds"]) for r in group) / len(group) if group else None,
            "meanAppliedGainSeconds": sum(float(r["pairedGainSeconds"]) for r in apps) / len(apps) if apps else None,
            "contextInducedWrongEarlyStops": sum(int(r["contextInducedWrongEarlyStop"]) for r in group),
            "contextInducedWrongTrialSeedPairs": sum(int(r["contextInducedWrongEarlyStop"]) for r in group),
            "fallbackScenarioRate": sum(int(r["exactEegOnlyFallback"]) for r in group) / len(group) if group else None,
            "originalM33Applications": sum(int(r["originalM33ContextApplied"]) for r in group),
            "originalM33Errors": sum(int(r["originalM33ContextCausedError"]) for r in group),
            "originalM33MeanGainSeconds": sum(float(r["originalM33PairedGainSeconds"] or 0.0) for r in group) / len(group) if group else None,
            "trialSeedPairsAreNotIndependentRealTrials": True,
        })
    write_csv(ROOT / "real_m33_secondary_summary.csv", summaries)
    (ROOT / "real_m33_secondary_summary.json").write_text(json.dumps({
        "status": "PASS",
        "source": str(M34 / "context_heldout_per_trial_seed.csv"),
        "sourceScenario": "precomputed_before_eeg only; no semantic model calls",
        "sourceReplayRows": len(source),
        "realEegTrials": len({(r["sessionKey"], r["trialId"]) for r in source}),
        "seedReplayScenariosPerRealTrial": 100,
        "summaries": summaries,
        "interpretation": "M36 authorization gates were replayed against stored M33 predictions using the matching outer-group OOF EEG reliability probabilities. Seed replays are not independent EEG trials.",
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "summaries": summaries,
        "sourceReplayRows": len(source),
        "realEegTrials": len({(r["sessionKey"], r["trialId"]) for r in source}),
    }


def main():
    flat = rules.load_traces()
    failures, failure_summary = build_failure_cases()
    session_rows, calibration = session_shift_analysis()
    oracles = oracle_analysis(flat)
    m33 = m33_secondary(flat)
    result = {
        "status": "PASS",
        "failureCaseRows": len(failures),
        "failureClusterRows": len(failure_summary),
        "sessionShiftRows": len(session_rows),
        "oofCalibrationRows": len(calibration),
        "oracleSummaryRows": len(oracles),
        "storedM33SourceReplayScenarios": m33["sourceReplayRows"],
        "storedM33GateEvaluationRows": sum(r["trialSeedReplayScenarios"] for r in m33["summaries"]),
        "storedM33RealTrials": m33["realEegTrials"],
        "deepseekCalls": 0,
    }
    (ROOT / "diagnostics_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
