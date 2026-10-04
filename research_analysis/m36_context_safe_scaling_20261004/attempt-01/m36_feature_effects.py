#!/usr/bin/env python3
"""Describe outer-train standardized logistic weights without using outer-test labels."""

import csv
import json
from collections import defaultdict
from pathlib import Path

import m36_logistic_evaluation as logistic
import m36_rule_evaluation as rules


ROOT = Path(__file__).resolve().parent
FAMILIES = {
    "score_geometry": {"top_score", "second_score", "third_score", "absolute_margin", "relative_margin", "top_second_ratio", "normalized_score_entropy", "score_concentration"},
    "temporal_trajectory": {"evidence_seconds", "window_index", "consecutive_same_top", "prior_top_flips", "time_since_last_top_flip", "top_score_slope_last3", "second_score_slope_last3", "relative_margin_slope_last3", "entropy_slope_last3", "margin_increasing_fraction", "top_score_increasing_fraction"},
    "filter_band_views": {"band_agreement_count", "band_agreement_fraction", "band_min_relative_margin"},
    "harmonic_views": {"harmonic_agreement_count", "harmonic_agreement_fraction", "h1_top_agrees", "h2_top_agrees"},
    "channel_subset_views": {"secondary_view_count", "secondary_view_agreement_count", "secondary_view_agreement_fraction", "secondary_view_margin_mean", "secondary_view_margin_min", "secondary_view_margin_std", "secondary_view_margin_delta"},
    "raw_top_identity": {"main_top_is_0", "main_top_is_1", "main_top_is_2"},
}


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def main():
    all_traces = rules.load_traces()
    outer_rows = read_csv(ROOT / "outer_fold_assignments.csv")
    policy_rows = read_csv(ROOT / "selected_logistic_policies.csv")
    header = next(csv.reader((ROOT / "eeg_trajectory_features.csv").open("r", encoding="utf-8")))
    feature_names = [name[len("feature_"):] for name in header if name.startswith("feature_")]
    assert len(feature_names) == 36
    traces_by_track = defaultdict(dict)
    for (track, trial_key), trace in all_traces.items():
        traces_by_track[track][trial_key] = trace
    policy_by_fold = {}
    for row in policy_rows:
        if row["groupingScheme"] == "acquisitionCampaignGroup" and row["safetyOperatingPoint"] == "SAFE-STRICT":
            policy_by_fold[(row["track"], row["outerFoldId"])] = row
    assignments = defaultdict(list)
    for row in outer_rows:
        if row["groupingScheme"] == "acquisitionCampaignGroup":
            assignments[(row["track"], row["outerFoldId"])].append(row)

    output = []
    for (track, outer_fold), rows in sorted(assignments.items()):
        first = rows[0]
        train_keys = json.loads(first["outerTrainTrialKeys"])
        test_keys = {row["trialKey"] for row in rows}
        assert not set(train_keys) & test_keys
        train_traces = {key: traces_by_track[track][key] for key in train_keys}
        x_train, y_train, _, _ = logistic.build_matrix(train_traces, train_keys)
        policy = policy_by_fold[(track, outer_fold)]
        model = logistic.fit_logistic(x_train, y_train, float(policy["selectedL2"]), class_balance=True)
        coefficients = model["coef"][1:]
        assert len(coefficients) == len(feature_names)
        for feature, coefficient in zip(feature_names, coefficients):
            output.append({
                "track": track,
                "groupingScheme": "acquisitionCampaignGroup",
                "outerFoldId": outer_fold,
                "outerTestGroup": first["outerTestGroup"],
                "selectedL2": policy["selectedL2"],
                "feature": feature,
                "standardizedLogitCoefficient": float(coefficient),
                "absoluteCoefficient": abs(float(coefficient)),
                "fitTrials": len(train_keys),
                "excludedOuterTestTrials": len(test_keys),
            })

    with (ROOT / "feature_coefficients_primary_campaign.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)

    grouped = defaultdict(list)
    for row in output:
        grouped[(row["track"], row["feature"])].append(row)
    feature_summary = []
    for (track, feature), rows in sorted(grouped.items()):
        values = [float(r["standardizedLogitCoefficient"]) for r in rows]
        feature_summary.append({
            "track": track,
            "feature": feature,
            "family": next(name for name, members in FAMILIES.items() if feature in members),
            "outerFoldCount": len(rows),
            "meanAbsoluteStandardizedCoefficient": sum(abs(x) for x in values) / len(values),
            "meanSignedStandardizedCoefficient": sum(values) / len(values),
            "positiveFoldFraction": sum(x > 0 for x in values) / len(values),
            "negativeFoldFraction": sum(x < 0 for x in values) / len(values),
        })
    family_summary = []
    for track in sorted({r["track"] for r in feature_summary}):
        for family in FAMILIES:
            rows = [r for r in feature_summary if r["track"] == track and r["family"] == family]
            family_summary.append({
                "track": track,
                "featureFamily": family,
                "featureCount": len(rows),
                "meanFeatureAbsoluteCoefficient": sum(r["meanAbsoluteStandardizedCoefficient"] for r in rows) / len(rows),
                "topFeaturesByAbsoluteCoefficient": sorted(rows, key=lambda r: r["meanAbsoluteStandardizedCoefficient"], reverse=True)[:4],
            })
    result = {
        "status": "PASS",
        "track": "primary acquisitionCampaignGroup only",
        "target": "safeTrue; labels used only for outer-train logistic fitting",
        "outerTrainOnly": True,
        "outerTestTrialsExcludedFromFit": True,
        "coefficientInterpretation": "descriptive standardized logistic weights; correlated features mean these are not causal or independent importance estimates",
        "outerModelCount": len(assignments),
        "featureCount": len(feature_names),
        "familySummary": family_summary,
        "topFeaturesByTrack": {
            track: sorted([r for r in feature_summary if r["track"] == track], key=lambda r: r["meanAbsoluteStandardizedCoefficient"], reverse=True)[:12]
            for track in sorted({r["track"] for r in feature_summary})
        },
    }
    (ROOT / "feature_effects_summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "models": len(assignments), "featuresPerModel": len(feature_names),
                      "topTrack1": result["topFeaturesByTrack"]["track1_5ch"][:5],
                      "topTrack2": result["topFeaturesByTrack"]["track2_common3"][:5]}, sort_keys=True))


if __name__ == "__main__":
    main()
