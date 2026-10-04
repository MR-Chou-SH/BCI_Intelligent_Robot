#!/usr/bin/env python3
"""Create deterministic grouped outer and nested inner M36 fold assignments."""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FEATURES = ROOT / "eeg_trajectory_features.csv"
OUTER_CSV = ROOT / "outer_fold_assignments.csv"
INNER_CSV = ROOT / "inner_fold_assignments.csv"
SUMMARY_JSON = ROOT / "fold_assignment_summary.json"
SESSION_ORDER = {"A": 0, "B1": 1, "B2": 2, "S7": 3}
TRACKS = ("track1_5ch", "track2_common3")
SCHEMES = ("acquisitionCampaignGroup", "recordingSessionGroup")
CHRONOLOGICAL_BLOCKS = 5


def read_trials():
    trials = {}
    with FEATURES.open("r", newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            key = (row["track"], row["sessionKey"], row["trialId"])
            candidate = {
                "track": row["track"],
                "sessionKey": row["sessionKey"],
                "sessionId": row["sessionId"],
                "trialId": row["trialId"],
                "trialKey": row["sessionKey"] + "::" + row["trialId"],
                "trialIndex": int(row["trialIndex"]),
                "recordingSessionGroup": row["recordingSessionGroup"],
                "acquisitionCampaignGroup": row["acquisitionCampaignGroup"],
            }
            previous = trials.get(key)
            if previous is not None and previous != candidate:
                raise ValueError("trial metadata varies across windows: {}".format(key))
            trials[key] = candidate
    return list(trials.values())


def chronological_key(trial):
    return (SESSION_ORDER[trial["sessionKey"]], trial["trialIndex"], trial["trialKey"])


def unique_in_order(values):
    return list(dict.fromkeys(values))


def group_value(trial, scheme):
    return trial[scheme]


def build_inner_splits(train_trials, scheme):
    groups = unique_in_order(group_value(t, scheme) for t in sorted(train_trials, key=chronological_key))
    if len(groups) >= 2:
        splits = []
        for number, validation_group in enumerate(groups, start=1):
            validation = [t for t in train_trials if group_value(t, scheme) == validation_group]
            training = [t for t in train_trials if group_value(t, scheme) != validation_group]
            splits.append({
                "fold": "inner-{:02d}".format(number),
                "method": "leave_one_group_out",
                "validation": sorted(validation, key=chronological_key),
                "training": sorted(training, key=chronological_key),
                "embargoed": [],
            })
        return splits

    ordered = sorted(train_trials, key=chronological_key)
    if len(ordered) < 5:
        raise ValueError("too few trials for chronological inner validation: {}".format(len(ordered)))
    block_count = min(CHRONOLOGICAL_BLOCKS, max(2, len(ordered) // 5))
    splits = []
    for number in range(block_count):
        start = number * len(ordered) // block_count
        stop = (number + 1) * len(ordered) // block_count
        validation = ordered[start:stop]
        embargoed = []
        if start > 0:
            embargoed.append(ordered[start - 1])
        if stop < len(ordered):
            embargoed.append(ordered[stop])
        excluded = {t["trialKey"] for t in validation + embargoed}
        training = [t for t in ordered if t["trialKey"] not in excluded]
        splits.append({
            "fold": "inner-{:02d}".format(number + 1),
            "method": "chronological_blocked_{}_fold_embargo_1".format(block_count),
            "validation": validation,
            "training": training,
            "embargoed": embargoed,
        })
    return splits


def main():
    all_trials = read_trials()
    outer_rows = []
    inner_rows = []
    summary = {
        "schemaVersion": 1,
        "featureInput": FEATURES.name,
        "outerAssignments": OUTER_CSV.name,
        "innerAssignments": INNER_CSV.name,
        "tracks": {},
        "groupingSchemes": list(SCHEMES),
        "allWindowsStayWithTrial": True,
        "chronologicalFallback": {
            "blocks": CHRONOLOGICAL_BLOCKS,
            "boundaryEmbargoTrials": 1,
            "sortOrder": "A, B1, B2, S7 then trialIndex",
        },
    }

    for track in TRACKS:
        track_trials = [t for t in all_trials if t["track"] == track]
        expected = 88 if track == "track1_5ch" else 118
        if len(track_trials) != expected:
            raise ValueError("{} unique trials: {} != {}".format(track, len(track_trials), expected))
        summary["tracks"][track] = {"uniqueTrials": len(track_trials), "schemes": {}}

        for scheme in SCHEMES:
            ordered = sorted(track_trials, key=chronological_key)
            groups = unique_in_order(group_value(t, scheme) for t in ordered)
            scheme_outer_start = len(outer_rows)
            scheme_inner_start = len(inner_rows)
            fold_summaries = []

            for outer_number, test_group in enumerate(groups, start=1):
                fold_id = "{}|{}|outer-{:02d}".format(track, "campaign" if scheme.startswith("acquisition") else "session", outer_number)
                test_trials = [t for t in ordered if group_value(t, scheme) == test_group]
                train_trials = [t for t in ordered if group_value(t, scheme) != test_group]
                train_groups = unique_in_order(group_value(t, scheme) for t in train_trials)
                train_trial_keys = [t["trialKey"] for t in train_trials]
                test_trial_keys = [t["trialKey"] for t in test_trials]
                group_overlap = bool(set(train_groups) & {test_group})
                trial_overlap = bool(set(train_trial_keys) & set(test_trial_keys))
                if group_overlap or trial_overlap or not train_trials or not test_trials:
                    raise AssertionError("invalid outer split {}".format(fold_id))

                for trial in test_trials:
                    outer_rows.append({
                        "track": track,
                        "groupingScheme": scheme,
                        "outerFoldId": fold_id,
                        "sessionKey": trial["sessionKey"],
                        "sessionId": trial["sessionId"],
                        "trialId": trial["trialId"],
                        "trialKey": trial["trialKey"],
                        "trialIndex": trial["trialIndex"],
                        "recordingSessionGroup": trial["recordingSessionGroup"],
                        "acquisitionCampaignGroup": trial["acquisitionCampaignGroup"],
                        "outerTestGroup": test_group,
                        "outerTrainGroups": json.dumps(train_groups, separators=(",", ":")),
                        "outerTrainTrialKeys": json.dumps(train_trial_keys, separators=(",", ":")),
                        "outerTrainGroupCount": len(train_groups),
                        "outerTrainTestGroupOverlap": group_overlap,
                        "outerTrainTestTrialOverlap": trial_overlap,
                    })

                inner_splits = build_inner_splits(train_trials, scheme)
                inner_validation_counts = Counter()
                for inner in inner_splits:
                    val_keys = [t["trialKey"] for t in inner["validation"]]
                    train_keys = [t["trialKey"] for t in inner["training"]]
                    embargo_keys = [t["trialKey"] for t in inner["embargoed"]]
                    val_groups = unique_in_order(group_value(t, scheme) for t in inner["validation"])
                    inner_train_groups = unique_in_order(group_value(t, scheme) for t in inner["training"])
                    group_overlap_inner = bool(set(inner_train_groups) & set(val_groups))
                    trial_overlap_inner = bool(set(train_keys) & set(val_keys))
                    if group_overlap_inner and inner["method"] == "leave_one_group_out":
                        raise AssertionError("inner group overlap {} {}".format(fold_id, inner["fold"]))
                    if trial_overlap_inner or set(embargo_keys) & (set(val_keys) | set(train_keys)):
                        raise AssertionError("inner trial/embargo overlap {} {}".format(fold_id, inner["fold"]))
                    for trial in inner["validation"]:
                        inner_validation_counts[trial["trialKey"]] += 1
                        inner_rows.append({
                            "track": track,
                            "groupingScheme": scheme,
                            "outerFoldId": fold_id,
                            "innerFoldId": inner["fold"],
                            "innerMethod": inner["method"],
                            "validationTrialId": trial["trialId"],
                            "validationTrialKey": trial["trialKey"],
                            "validationSessionKey": trial["sessionKey"],
                            "validationGroup": group_value(trial, scheme),
                            "innerTrainGroups": json.dumps(inner_train_groups, separators=(",", ":")),
                            "innerTrainTrialKeys": json.dumps(train_keys, separators=(",", ":")),
                            "innerValidationTrialKeys": json.dumps(val_keys, separators=(",", ":")),
                            "embargoedTrialKeys": json.dumps(embargo_keys, separators=(",", ":")),
                            "innerTrainValidationGroupOverlap": group_overlap_inner,
                            "innerTrainValidationTrialOverlap": trial_overlap_inner,
                            "outerTestGroup": test_group,
                        })
                if set(inner_validation_counts) != set(train_trial_keys) or any(v != 1 for v in inner_validation_counts.values()):
                    raise AssertionError("inner validation does not cover outer train exactly once: {}".format(fold_id))

                methods = unique_in_order(s["method"] for s in inner_splits)
                fold_summaries.append({
                    "outerFoldId": fold_id,
                    "testGroup": test_group,
                    "outerTrainGroupCount": len(train_groups),
                    "outerTrainTrials": len(train_trials),
                    "outerTestTrials": len(test_trials),
                    "innerFoldCount": len(inner_splits),
                    "innerMethods": methods,
                    "outerGroupOverlap": group_overlap,
                    "outerTrialOverlap": trial_overlap,
                })

            outer_slice = outer_rows[scheme_outer_start:]
            seen = Counter(row["trialKey"] for row in outer_slice)
            if set(seen) != {t["trialKey"] for t in ordered} or any(value != 1 for value in seen.values()):
                raise AssertionError("outer folds do not test each trial exactly once: {} {}".format(track, scheme))
            summary["tracks"][track]["schemes"][scheme] = {
                "groupCount": len(groups),
                "outerFoldCount": len(fold_summaries),
                "outerAssignmentRows": len(outer_slice),
                "innerAssignmentRows": len(inner_rows) - scheme_inner_start,
                "outerTestedEveryTrialExactlyOnce": True,
                "folds": fold_summaries,
            }

    def write_csv(path, rows):
        if not rows:
            raise ValueError("refusing to write empty fold table: {}".format(path))
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    write_csv(OUTER_CSV, outer_rows)
    write_csv(INNER_CSV, inner_rows)
    with SUMMARY_JSON.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({
        "status": "PASS",
        "outerAssignmentRows": len(outer_rows),
        "innerAssignmentRows": len(inner_rows),
        "summary": SUMMARY_JSON.name,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
