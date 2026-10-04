#!/usr/bin/env python3
"""Focused deterministic audits for M36 generated artifacts."""

import argparse
import csv
import json
import math
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read_csv(name):
    with (ROOT / name).open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def check_folds():
    feature_rows = read_csv("eeg_trajectory_features.csv")
    expected_trials = defaultdict(set)
    for row in feature_rows:
        expected_trials[row["track"]].add((row["sessionKey"], row["trialId"]))
    outer = read_csv("outer_fold_assignments.csv")
    inner = read_csv("inner_fold_assignments.csv")
    checks = []

    outer_by_scheme = defaultdict(list)
    for row in outer:
        outer_by_scheme[(row["track"], row["groupingScheme"])].append(row)
        assert row["outerTrainTestGroupOverlap"] == "False", row
        assert row["outerTrainTestTrialOverlap"] == "False", row
        assert row["outerTestGroup"] not in json.loads(row["outerTrainGroups"]), row
        assert row["trialKey"] not in json.loads(row["outerTrainTrialKeys"]), row
        assert row["outerTestGroup"] == row["acquisitionCampaignGroup"] if row["groupingScheme"] == "acquisitionCampaignGroup" else row["outerTestGroup"] == row["recordingSessionGroup"]

    for (track, scheme), rows in outer_by_scheme.items():
        by_trial = Counter((r["sessionKey"], r["trialId"]) for r in rows)
        assert set(by_trial) == expected_trials[track], (track, scheme, len(by_trial), len(expected_trials[track]))
        assert all(count == 1 for count in by_trial.values()), (track, scheme, by_trial)
        by_fold = defaultdict(list)
        for row in rows:
            by_fold[row["outerFoldId"]].append(row)
        for fold_id, fold_rows in by_fold.items():
            test_groups = {r["outerTestGroup"] for r in fold_rows}
            assert len(test_groups) == 1, (fold_id, test_groups)
            training_groups = set(json.loads(fold_rows[0]["outerTrainGroups"]))
            assert not training_groups & test_groups, (fold_id, training_groups, test_groups)
            train_ids = set(json.loads(fold_rows[0]["outerTrainTrialKeys"]))
            test_ids = {r["trialKey"] for r in fold_rows}
            assert not train_ids & test_ids, fold_id
            assert all(json.loads(r["outerTrainTrialKeys"]) == json.loads(fold_rows[0]["outerTrainTrialKeys"]) for r in fold_rows)
        checks.append("{} {} outer groups and trial IDs are isolated; all {} trials tested once".format(track, scheme, len(by_trial)))

    inner_by_fold = defaultdict(list)
    for row in inner:
        key = (row["track"], row["groupingScheme"], row["outerFoldId"], row["innerFoldId"])
        inner_by_fold[key].append(row)
        assert row["innerTrainValidationTrialOverlap"] == "False", row
        train = set(json.loads(row["innerTrainTrialKeys"]))
        validation = set(json.loads(row["innerValidationTrialKeys"]))
        embargo = set(json.loads(row["embargoedTrialKeys"]))
        assert not train & validation, key
        assert not embargo & (train | validation), key
        outer_row = next(r for r in outer_by_scheme[(row["track"], row["groupingScheme"])] if r["outerFoldId"] == row["outerFoldId"])
        outer_train = set(json.loads(outer_row["outerTrainTrialKeys"]))
        assert train | validation | embargo <= outer_train, key
        assert row["outerTestGroup"] not in json.loads(row["innerTrainGroups"]), key
        if row["innerMethod"] == "leave_one_group_out":
            assert row["innerTrainValidationGroupOverlap"] == "False", key
            assert not set(json.loads(row["innerTrainGroups"])) & {row["validationGroup"]}, key

    by_outer_inner = defaultdict(lambda: defaultdict(list))
    for (track, scheme, outer_id, inner_id), rows in inner_by_fold.items():
        validation_ids = set(json.loads(rows[0]["innerValidationTrialKeys"]))
        for row in rows:
            assert row["validationTrialKey"] in validation_ids
        by_outer_inner[(track, scheme, outer_id)][inner_id] = rows
    for key, folds in by_outer_inner.items():
        outer_row = next(r for r in outer_by_scheme[(key[0], key[1])] if r["outerFoldId"] == key[2])
        expected_train = set(json.loads(outer_row["outerTrainTrialKeys"]))
        validations = [r["validationTrialKey"] for fold_rows in folds.values() for r in fold_rows]
        assert set(validations) == expected_train and len(validations) == len(set(validations)), (key, len(expected_train), len(validations))

    checks.append("{} inner validation folds have no train/validation/embargo leakage and cover each outer-train trial once".format(len(inner_by_fold)))
    summary = json.loads((ROOT / "fold_assignment_summary.json").read_text(encoding="utf-8"))
    assert summary["allWindowsStayWithTrial"] is True
    return checks


def check_rules():
    summary = json.loads((ROOT / "rule_evaluation_summary.json").read_text(encoding="utf-8"))
    reproduction = json.loads((ROOT / "m35_rule_reproduction.json").read_text(encoding="utf-8"))
    decisions = read_csv("rule_oof_predictions.csv")
    metrics = read_csv("rule_risk_coverage.csv")
    assert summary["status"] == "PASS", summary
    assert reproduction["status"] == "PASS" and reproduction["matchedRows"] == 1416, reproduction
    assert summary["argmaxInvariant"].startswith("selectedPrediction always equals raw EEG argmax"), summary
    for row in decisions:
        assert int(row["selectedPrediction"]) == int(row["rawTopAtStop"]), row
        assert float(row["selectedEvidenceSeconds"]) <= float(row["baselineEvidenceSeconds"]) + 1e-12, row
        if row["condition"] == "NEUTRAL_CONTEXT":
            assert int(row["contextApplied"]) == 0, row
            assert int(row["selectedPrediction"]) == int(row["baselinePrediction"]), row
            assert abs(float(row["selectedEvidenceSeconds"]) - float(row["baselineEvidenceSeconds"])) <= 1e-12, row
        if int(row["contextApplied"]):
            assert float(row["pairedGainSeconds"]) > 0.0, row
    assert any(r["method"] == "M35_CONSERVATIVE" for r in decisions)
    assert any(r["method"] == "M36_MULTI_VIEW_RULE" and r["safetyOperatingPoint"] == "SAFE-STRICT" for r in decisions)
    assert all(int(r["realEegTrials"]) <= 118 for r in metrics)
    return [
        "M35 fixed policy reproduces 1,416 stored controlled-context decisions at q=.45/.95 across both grouping schemes",
        "all Context conditions preserve the raw EEG argmax and never delay the frozen EEG-only decision",
        "neutral Context is exact EEG-only fallback",
        "nested deterministic gate outputs cover primary and session-sensitivity OOF tracks",
    ]


def check_logistic():
    summary = json.loads((ROOT / "logistic_evaluation_summary.json").read_text(encoding="utf-8"))
    decisions = read_csv("logistic_oof_predictions.csv")
    reliability = read_csv("logistic_reliability_oof.csv")
    assert summary["status"] == "PASS", summary
    assert summary["reliabilityTarget"] == "safeTrue; eventual frozen decision consistency retained as a separate label and audit"
    feature_header = next(csv.reader((ROOT / "eeg_trajectory_features.csv").open("r", encoding="utf-8")))
    feature_names = [name for name in feature_header if name.startswith("feature_")]
    assert len(feature_names) == summary["featureCount"]
    assert not any("safeTrue" in name or "safeFinal" in name or "trueSlot" in name or "eegOnlyDecision" in name for name in feature_names)
    outer_membership = {}
    for row in read_csv("outer_fold_assignments.csv"):
        key = (row["track"], row["groupingScheme"], row["outerFoldId"])
        outer_membership.setdefault(key, {"train": set(json.loads(row["outerTrainTrialKeys"])), "test": set(), "testGroup": row["outerTestGroup"]})
        outer_membership[key]["test"].add(row["trialKey"])
    for r in reliability:
        membership = outer_membership[(r["track"], r["groupingScheme"], r["outerFoldId"])]
        assert r["trialKey"] in membership["test"] and r["trialKey"] not in membership["train"], r
        assert r["outerTestGroup"] == membership["testGroup"], r
    assert all(0.0 <= float(r["reliabilityProbability"]) <= 1.0 for r in reliability)
    assert len(reliability) == summary["outerOofTimepointRows"]
    for row in decisions:
        membership = outer_membership[(row["track"], row["groupingScheme"], row["outerFoldId"])]
        assert row["trialKey"] in membership["test"] and row["trialKey"] not in membership["train"], row
        assert int(row["selectedPrediction"]) == int(row["rawTopAtStop"]), row
        assert float(row["selectedEvidenceSeconds"]) <= float(row["baselineEvidenceSeconds"]) + 1e-12, row
        if row["condition"] == "NEUTRAL_CONTEXT":
            assert int(row["contextApplied"]) == 0, row
            assert int(row["selectedPrediction"]) == int(row["baselinePrediction"]), row
            assert abs(float(row["selectedEvidenceSeconds"]) - float(row["baselineEvidenceSeconds"])) <= 1e-12, row
        if int(row["contextApplied"]):
            assert float(row["pairedGainSeconds"]) > 0.0, row
    return [
        "logistic probabilities are out-of-fold and use only the 36 causal feature columns",
        "calibrators and thresholds are selected from nested training-only OOF scores",
        "all learned-gate outputs preserve the raw EEG argmax, do not delay, and neutral Context exactly falls back",
    ]


def check_view_guard():
    summary = json.loads((ROOT / "view_guard_logistic_summary.json").read_text(encoding="utf-8"))
    decisions = read_csv("view_guard_logistic_oof_predictions.csv")
    policies = read_csv("selected_view_guard_policies.csv")
    search = read_csv("view_guard_nested_search.csv")
    assert summary["status"] == "PASS"
    outer_membership = {}
    for row in read_csv("outer_fold_assignments.csv"):
        key = (row["track"], row["groupingScheme"], row["outerFoldId"])
        outer_membership.setdefault(key, {"train": set(json.loads(row["outerTrainTrialKeys"])), "test": set(), "testGroup": row["outerTestGroup"]})
        outer_membership[key]["test"].add(row["trialKey"])
    eligible = {
        (r["track"], r["groupingScheme"], r["outerFoldId"], r["safetyOperatingPoint"],
         float(r["threshold"]), float(r["minimumBandAgreement"]), float(r["minimumHarmonicAgreement"]), float(r["minimumBandMargin"]))
        for r in search if r["eligible"] == "True"
    }
    for policy in policies:
        key = (policy["track"], policy["groupingScheme"], policy["outerFoldId"], policy["safetyOperatingPoint"],
               float(policy["selectedReliabilityThreshold"]), float(policy["minimumBandAgreement"]),
               float(policy["minimumHarmonicAgreement"]), float(policy["minimumBandMargin"]))
        assert key in eligible, key
    for row in decisions:
        membership = outer_membership[(row["track"], row["groupingScheme"], row["outerFoldId"])]
        assert row["trialKey"] in membership["test"] and row["trialKey"] not in membership["train"], row
        assert int(row["selectedPrediction"]) == int(row["rawTopAtStop"]), row
        assert float(row["selectedEvidenceSeconds"]) <= float(row["baselineEvidenceSeconds"]) + 1e-12, row
        if row["condition"] == "NEUTRAL_CONTEXT":
            assert int(row["contextApplied"]) == 0, row
            assert int(row["selectedPrediction"]) == int(row["baselinePrediction"]), row
            assert abs(float(row["selectedEvidenceSeconds"]) - float(row["baselineEvidenceSeconds"])) <= 1e-12, row
    return [
        "separate-view guard and reliability thresholds are selected only from nested inner folds",
        "every output trial belongs to the recorded outer test group and is absent from outer training IDs",
        "view-guard Context never reranks EEG, never delays, and neutral fallback is exact",
    ]


def check_results():
    summary = json.loads((ROOT / "statistical_summary.json").read_text(encoding="utf-8"))
    predictions = read_csv("oof_predictions.csv")
    results = read_csv("controlled_context_results.csv")
    assert summary["status"] == "PASS"
    assert summary["inputOofDecisionRows"] == len(predictions)
    assert summary["aggregateRows"] == len(results)
    assert summary["scenarioAccounting"].startswith("INCONGRUENT_CONTEXT has two wrong-target replay scenarios")
    expected_per_track = {"track1_5ch": 88, "track2_common3": 118}
    trial_sets = {}
    grouped = {}
    for row in predictions:
        key = (row["track"], row["groupingScheme"], row["method"], row["safetyOperatingPoint"], row["condition"], row["qTop"])
        grouped.setdefault(key, []).append(row)
        trial_sets.setdefault((row["track"], row["groupingScheme"]), set()).add(row["trialKey"])
        assert int(row["selectedPrediction"]) == int(row["rawTopAtStop"]), row
        assert float(row["selectedEvidenceSeconds"]) <= float(row["baselineEvidenceSeconds"]) + 1e-12, row
        if row["condition"] == "NEUTRAL_CONTEXT":
            assert int(row["contextApplied"]) == 0, row
            assert int(row["selectedPrediction"]) == int(row["baselinePrediction"]), row
            assert abs(float(row["selectedEvidenceSeconds"]) - float(row["baselineEvidenceSeconds"])) <= 1e-12, row
    for (track, scheme), trials in trial_sets.items():
        assert len(trials) == expected_per_track[track], (track, scheme, len(trials))
    result_map = {(r["track"], r["groupingScheme"], r["method"], r["safetyOperatingPoint"], r["condition"], r["qTop"]): r for r in results}
    for key, rows in grouped.items():
        result = result_map[key]
        trials = {r["trialKey"] for r in rows}
        assert len(trials) == int(result["realEegTrials"]), key
        assert len(rows) == int(result["contextReplayScenarios"]), key
        assert sum(int(r["contextApplied"]) for r in rows) == int(result["contextApplications"]), key
        assert sum(int(r["contextInducedWrongEarlyStop"]) for r in rows) == int(result["inducedWrongEarlyStopEvents"]), key
        if key[4] == "INCONGRUENT_CONTEXT":
            assert len(rows) == 2 * len(trials), key
    historical = summary["m35HistoricalReference"]
    assert historical["realEegTrials"] == 59 and historical["appliedTrials"] == 4
    assert abs(historical["applicationRate"] - 4.0 / 59.0) < 1e-12
    return [
        "both tracks account for all 88/118 real trials under both OOF grouping schemes",
        "both wrong-target replay cases are retained per trial and q without inflating EEG trial counts",
        "saved aggregate metrics recompute from OOF decisions; raw argmax, no-delay, and neutral fallback invariants hold",
        "historical M35 development reference is 4/59 (6.78%) and is labeled non-OOF",
    ]


def check_diagnostics():
    summary = json.loads((ROOT / "diagnostics_summary.json").read_text(encoding="utf-8"))
    failures = read_csv("failure_cases.csv")
    clusters = read_csv("failure_case_summary.csv")
    shifts = read_csv("session_shift_analysis.csv")
    calibration = read_csv("oof_calibration_by_group.csv")
    oracle = json.loads((ROOT / "oracle_summary.json").read_text(encoding="utf-8"))
    oracle_trials = read_csv("oracle_per_trial.csv")
    m33 = json.loads((ROOT / "real_m33_secondary_summary.json").read_text(encoding="utf-8"))
    m33_rows = read_csv("real_m33_secondary_per_seed.csv")
    m33_summary = read_csv("real_m33_secondary_summary.csv")

    assert summary["status"] == "PASS" and summary["deepseekCalls"] == 0
    assert summary["failureCaseRows"] == len(failures) and summary["failureClusterRows"] == len(clusters)
    assert summary["sessionShiftRows"] == len(shifts) == 63
    assert summary["oofCalibrationRows"] == len(calibration)
    assert summary["oracleSummaryRows"] == len(oracle["summaries"]) == 20
    assert summary["storedM33SourceReplayScenarios"] == m33["sourceReplayRows"] == 5900
    assert summary["storedM33GateEvaluationRows"] == len(m33_rows) == 59000
    assert summary["storedM33RealTrials"] == m33["realEegTrials"] == 59
    assert len(m33_summary) == len(m33["summaries"])
    assert m33["seedReplayScenariosPerRealTrial"] == 100
    assert all(r["trialSeedPairsAreNotIndependentRealTrials"] == "True" for r in m33_summary)

    outer_membership = defaultdict(dict)
    for row in read_csv("outer_fold_assignments.csv"):
        outer_membership[(row["track"], row["groupingScheme"], row["outerFoldId"])][row["trialKey"]] = row["outerTestGroup"]
    for row in failures:
        assert int(row["contextInducedWrongEarlyStop"]) == 1
        assert int(row["earlyStopPrediction"]) != int(row["trueSlotIndex"])
        key = (row["track"], row["groupingScheme"], row["outerFoldId"])
        assert outer_membership[key].get(row["trialKey"]) == row["outerTestGroup"], row
        for column in ("rawTopTrajectory", "scoreTrajectory", "relativeMarginTrajectory", "bandAgreementTrajectory",
                       "harmonicAgreementTrajectory", "secondaryAgreementTrajectory", "reliabilityProbabilityTrajectory"):
            assert len(json.loads(row[column])) == 9, (column, row["trialKey"])

    expected_trials = {"track1_5ch": 88, "track2_common3": 118}
    for row in oracle["summaries"]:
        assert row["upperBound"] is True
        assert row["realEegTrials"] == expected_trials[row["track"]]
    comparisons = oracle["achievedGateComparisonAtQ095"]
    assert comparisons
    for row in comparisons:
        assert row["qTop"] == 0.95
        if row["oracleMeanGainSeconds"] > 0:
            expected = row["achievedMeanGainSeconds"] / row["oracleMeanGainSeconds"]
            assert abs(row["fractionOfOracleMeanGain"] - expected) < 1e-12
        else:
            assert row["fractionOfOracleMeanGain"] is None
    return [
        "all induced-error records map to their recorded outer-test trial and contain all nine causal trace points",
        "session-shift and OOF calibration summaries retain group/timepoint denominators",
        "oracle bounds use complete outer-OOF trials and compare achieved gains without changing saved OOF decisions",
        "stored M33 replay is exactly 5,900 historical scenarios / 59 EEG trials; 59,000 method-policy evaluations are not treated as independent trials",
        "diagnostics make zero semantic-model calls",
    ]


def check_presentation():
    manifest = json.loads((ROOT / "figures_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS" and manifest["figureCount"] == 12
    expected_figures = {
        "fig01_risk_coverage.svg", "fig02_coverage_overall_gain.svg", "fig03_precision_coverage.svg",
        "fig04_applied_gain_coverage.svg", "fig05_fast_fraction_coverage.svg", "fig06_group_accuracy_risk.svg",
        "fig07_oracle_vs_achieved.svg", "fig08_congruent_success_trace.svg", "fig09_incongruent_rejection_trace.svg",
        "fig10_wrong_context_failure_trace.svg", "fig11_oof_reliability_calibration.svg", "fig12_feature_session_shift.svg",
    }
    figure_rows = manifest["figures"]
    assert {Path(r["file"]).name for r in figure_rows} == expected_figures
    for row in figure_rows:
        path = ROOT / row["file"]
        assert path.is_file() and path.stat().st_size == row["bytes"] > 0
        assert ET.parse(path).getroot().tag.endswith("svg"), path

    safety_frontier = json.loads((ROOT / "q_sweep_safety_frontier.json").read_text(encoding="utf-8"))
    safety_rows = read_csv("q_sweep_safety_frontier.csv")
    assert safety_frontier["status"] == "PASS" and len(safety_rows) == 4 * 2 * 11
    best_zero_wrong = safety_frontier["bestCommonPointWithNoWrongContextApplications"]
    assert best_zero_wrong["method"] == "M36_MULTI_VIEW_RULE" and best_zero_wrong["qTop"] == 0.5
    assert best_zero_wrong["track1Applications"] == 5 and best_zero_wrong["track2Applications"] == 4
    assert best_zero_wrong["track1WrongContextApplications"] == 0 and best_zero_wrong["track2WrongContextApplications"] == 0
    q055_t1 = next(r for r in safety_rows if r["track"] == "track1_5ch" and r["method"] == "M36_MULTI_VIEW_RULE" and r["qTop"] == "0.55")
    q055_t2 = next(r for r in safety_rows if r["track"] == "track2_common3" and r["method"] == "M36_MULTI_VIEW_RULE" and r["qTop"] == "0.55")
    assert q055_t1["congruentApplications"] == "8" and q055_t2["congruentApplications"] == "7"
    assert q055_t1["wrongContextApplications"] == "1" and q055_t1["wrongContextInterventionPrecision"] == "0.0"
    assert q055_t1["inducedWrongEarlyStops"] == q055_t2["inducedWrongEarlyStops"] == "0"

    effects = json.loads((ROOT / "feature_effects_summary.json").read_text(encoding="utf-8"))
    coefficients = read_csv("feature_coefficients_primary_campaign.csv")
    assert effects["status"] == "PASS" and effects["outerTrainOnly"] and effects["outerTestTrialsExcludedFromFit"]
    assert effects["featureCount"] == 36 and effects["outerModelCount"] == 5 and len(coefficients) == 5 * 36
    assignments = defaultdict(list)
    for row in read_csv("outer_fold_assignments.csv"):
        if row["groupingScheme"] == "acquisitionCampaignGroup":
            assignments[(row["track"], row["outerFoldId"])].append(row)
    coefficient_counts = Counter()
    for row in coefficients:
        key = (row["track"], row["outerFoldId"])
        fold_rows = assignments[key]
        train = set(json.loads(fold_rows[0]["outerTrainTrialKeys"]))
        test = {item["trialKey"] for item in fold_rows}
        assert not train & test
        assert int(row["fitTrials"]) == len(train) and int(row["excludedOuterTestTrials"]) == len(test)
        assert row["outerTestGroup"] == fold_rows[0]["outerTestGroup"]
        assert math.isfinite(float(row["standardizedLogitCoefficient"]))
        coefficient_counts[key] += 1
    assert set(coefficient_counts.values()) == {36}

    table = read_csv("primary_success_table.csv")
    assert len(table) == 13
    aggregate = {(r["track"], r["groupingScheme"], r["method"], r["safetyOperatingPoint"], r["condition"], r["qTop"]): r
                 for r in read_csv("controlled_context_results.csv")}
    for row in table:
        track, method, safety = row["track"], row["method"], row["safetyOperatingPoint"]
        if row["evidenceSource"].startswith("primary acquisition-campaign"):
            for condition, column, target in (
                ("CONGRUENT_CONTEXT", "coverage", "coveragePerReplayScenario"),
                ("CONGRUENT_CONTEXT", "applications", "contextApplications"),
                ("CONGRUENT_CONTEXT", "accuracy", "conservativePerTrialAccuracy"),
                ("CONGRUENT_CONTEXT", "meanEvidenceSeconds", "meanEvidenceSeconds"),
                ("CONGRUENT_CONTEXT", "overallMeanGainSeconds", "overallMeanGainSeconds"),
                ("CONGRUENT_CONTEXT", "fractionEvidenceLE030", "fractionTrialsLE030"),
                ("INCONGRUENT_CONTEXT", "wrongContextApplications", "contextApplications"),
                ("INCONGRUENT_CONTEXT", "wrongContextInducedErrors", "inducedWrongEarlyStopEvents"),
            ):
                source = aggregate[(track, "acquisitionCampaignGroup", method, safety, condition, "0.95")]
                assert abs(float(row[column]) - float(source[target])) < 1e-12, (track, method, column)
            oracle = json.loads((ROOT / "oracle_summary.json").read_text(encoding="utf-8"))
            oracle_source = next(r for r in oracle["summaries"] if r["track"] == track and r["groupingScheme"] == "acquisitionCampaignGroup" and r["oracleCondition"] == "oracle_multiview_band_harmonic")
            expected_efficiency = float(row["overallMeanGainSeconds"]) / oracle_source["meanOracleGainSeconds"]
            assert abs(float(row["oracleMeanGainSeconds"]) - oracle_source["meanOracleGainSeconds"]) < 1e-12
            assert abs(float(row["oracleEfficiency"]) - expected_efficiency) < 1e-12
        elif method == "EEG_ONLY":
            neutral = [r for r in read_csv("oof_predictions.csv") if r["track"] == track and r["groupingScheme"] == "acquisitionCampaignGroup"
                       and r["method"] == "M35_CONSERVATIVE" and r["condition"] == "NEUTRAL_CONTEXT" and not r["qTop"]]
            by_trial = {r["trialKey"]: r for r in neutral}
            assert len(by_trial) == int(row["realEegTrials"])
            expected_acc = sum(int(r["baselineCorrect"]) for r in by_trial.values()) / len(by_trial)
            expected_time = sum(float(r["baselineEvidenceSeconds"]) for r in by_trial.values()) / len(by_trial)
            assert abs(float(row["accuracy"]) - expected_acc) < 1e-12
            assert abs(float(row["meanEvidenceSeconds"]) - expected_time) < 1e-12
        elif method == "ORACLE_MULTIVIEW_UPPER_BOUND":
            oracle = json.loads((ROOT / "oracle_summary.json").read_text(encoding="utf-8"))
            source = next(r for r in oracle["summaries"] if r["track"] == track and r["groupingScheme"] == "acquisitionCampaignGroup" and r["oracleCondition"] == "oracle_multiview_band_harmonic")
            assert abs(float(row["overallMeanGainSeconds"]) - source["meanOracleGainSeconds"]) < 1e-12
        elif method == "STORED_M33_THROUGH_M36_LOGISTIC_VIEW_GUARD":
            replay = [r for r in read_csv("real_m33_secondary_per_seed.csv") if r["method"] == "M36_LOGISTIC_VIEW_GUARDED" and r["safetyOperatingPoint"] == "SAFE-STRICT"]
            assert len(replay) == int(row["contextReplayScenarios"]) == 5900
            assert sum(int(r["contextAppliedByM36Gate"]) for r in replay) == int(row["applications"])
            expected_gain = sum(float(r["pairedGainSeconds"]) for r in replay) / len(replay)
            assert abs(float(row["overallMeanGainSeconds"]) - expected_gain) < 1e-12

    examples = json.loads((ROOT / "representative_examples.json").read_text(encoding="utf-8"))
    decisions = read_csv("oof_predictions.csv")
    def has_oof(example, predicate):
        return any(r["trialKey"] == example["trialKey"] and r["track"] == "track2_common3"
                   and r["groupingScheme"] == "acquisitionCampaignGroup" and r["method"] == example["method"]
                   and r["qTop"] == "0.95" and predicate(r) for r in decisions)
    success = examples["congruent_success"]
    assert has_oof(success, lambda r: r["condition"] == "CONGRUENT_CONTEXT" and int(r["contextApplied"]) == 1 and int(r["selectedCorrect"]) == 1)
    reject = examples["safe_incongruent_rejection"]
    assert has_oof(reject, lambda r: r["condition"] == "INCONGRUENT_CONTEXT" and int(r["contextApplied"]) == 0 and int(r["baselineCorrect"]) == 1)
    failure = examples["wrong_context_failure"]
    assert any(r["trialKey"] == failure["trialKey"] and r["track"] == "track2_common3" and r["groupingScheme"] == "acquisitionCampaignGroup"
               and r["method"] == failure["method"] and r["safetyOperatingPoint"] == "SAFE-STRICT" and r["condition"] == "INCONGRUENT_CONTEXT"
               and r["qTop"] == "0.95" and int(r["contextInducedWrongEarlyStop"]) == 1 for r in decisions)

    report_path = ROOT / "M36_FINAL_REPORT.md"
    assert report_path.is_file()
    report = report_path.read_text(encoding="utf-8")
    for question in range(1, 23):
        assert "## {}.".format(question) in report, "missing report answer {}".format(question)
    for field in ("TOTAL REAL EEG TRIALS USED", "TRACK 1 TRIALS", "TRACK 2 TRIALS", "BEST GATE",
                  "BEST SAFE APPLICATION RATE", "M35 APPLICATION RATE BASELINE", "INTERVENTION PRECISION",
                  "CONTEXT-INDUCED WRONG EARLY STOPS", "EEG-ONLY MEAN EVIDENCE", "M36 CONTEXT MEAN EVIDENCE",
                  "OVERALL MEAN GAIN", "APPLIED-TRIAL MEAN GAIN", "<=300MS EEG-ONLY FRACTION",
                  "<=300MS M36 FRACTION", "ORACLE MEAN GAIN", "ORACLE EFFICIENCY",
                  "REAL M33 APPLICATION RATE THROUGH M36 GATE", "REAL M33 MEAN GAIN THROUGH M36 GATE",
                  "CROSS-SESSION ROBUST", "MAIN REMAINING BOTTLENECK", "PROSPECTIVE DATA REQUIRED",
                  "FINAL COMMIT", "REMOTE HASH VERIFIED"):
        assert field in report, "missing completion summary field: {}".format(field)
    return [
        "all 12 required figures are valid, manifested SVG files",
        "central success table metrics recompute from OOF, oracle, and stored-M33 tables",
        "representative success, rejection, and failure traces match saved OOF decisions",
        "final report answers all 22 questions and includes every required completion field",
    ]


def main():
    parser = argparse.ArgumentParser()
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--folds-only", action="store_true")
    choices.add_argument("--rules-only", action="store_true")
    choices.add_argument("--logistic-only", action="store_true")
    choices.add_argument("--view-guard-only", action="store_true")
    choices.add_argument("--results-only", action="store_true")
    choices.add_argument("--diagnostics-only", action="store_true")
    choices.add_argument("--presentation-only", action="store_true")
    args = parser.parse_args()
    checks = check_folds() if args.folds_only else (
        check_rules() if args.rules_only else (check_logistic() if args.logistic_only else
        (check_view_guard() if args.view_guard_only else (check_diagnostics() if args.diagnostics_only else
        (check_presentation() if args.presentation_only else check_results())))))
    print(json.dumps({"status": "PASS", "checks": checks}, sort_keys=True))


if __name__ == "__main__":
    main()
