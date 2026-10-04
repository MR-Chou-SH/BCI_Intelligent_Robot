"""Build causal M36 trial-time EEG features from frozen historical EEG.

Raw EEG is read directly from the external M34 source paths and is never copied
or modified. Labels are written to a separate table from runtime features.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
M34_DIR = ROOT / "research_analysis" / "m34_high_speed_ssvep_context_20261004" / "attempt-01"
M35_DIR = ROOT / "research_analysis" / "m35_controlled_context_causality_20261004" / "attempt-01"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(M34_DIR))

import run_train_dev_search as m34  # noqa: E402
from eeg.decoder.fbcca import predict_fbcca  # noqa: E402

SCHEDULE = [0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00]
FREQUENCIES = [7.2, 9.0, 12.0]
CHANNELS_5 = [2, 3, 4, 5, 7]
CHANNELS_3 = [2, 4, 7]
TRACKS = {
    "track1_5ch": {"sessions": ["A", "B1", "B2"], "channels": CHANNELS_5},
    "track2_common3": {"sessions": ["A", "B1", "B2", "S7"], "channels": CHANNELS_3},
}
CAMPAIGN_GROUP = {"A": "A_campaign", "B1": "M6_4_campaign", "B2": "M6_4_campaign", "S7": "S7_stress_campaign"}
M35_MIN_EVIDENCE = 0.20
M35_MARGIN = 0.175
M35_STABLE = 2
TOL = 1e-12

FEATURE_NAMES = [
    "top_score", "second_score", "third_score", "absolute_margin", "relative_margin",
    "top_second_ratio", "normalized_score_entropy", "score_concentration",
    "evidence_seconds", "window_index", "consecutive_same_top", "prior_top_flips",
    "time_since_last_top_flip", "top_score_slope_last3", "second_score_slope_last3",
    "relative_margin_slope_last3", "entropy_slope_last3", "margin_increasing_fraction",
    "top_score_increasing_fraction", "band_agreement_count", "band_agreement_fraction",
    "band_min_relative_margin", "harmonic_agreement_count", "harmonic_agreement_fraction",
    "h1_top_agrees", "h2_top_agrees", "secondary_view_count",
    "secondary_view_agreement_count", "secondary_view_agreement_fraction",
    "secondary_view_margin_mean", "secondary_view_margin_min", "secondary_view_margin_std",
    "secondary_view_margin_delta", "main_top_is_0", "main_top_is_1", "main_top_is_2",
]


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_top(scores):
    values = [float(value) for value in scores]
    return sorted(range(len(values)), key=lambda index: (-values[index], index))


def relative_margin(scores):
    order = stable_top(scores)
    values = [float(value) for value in scores]
    return (values[order[0]] - values[order[1]]) / max(abs(values[order[0]]), 1e-12)


def normalized_entropy(scores):
    values = np.asarray(scores, dtype=np.float64)
    logits = values - float(np.max(values))
    exp_values = np.exp(logits)
    probabilities = exp_values / max(float(np.sum(exp_values)), 1e-300)
    entropy = -float(np.sum(probabilities * np.log(np.maximum(probabilities, 1e-300))))
    return entropy / math.log(len(values))


def linear_slope(times, values):
    if len(values) < 2:
        return 0.0
    x = np.asarray(times, dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    x = x - float(np.mean(x))
    denominator = float(np.dot(x, x))
    if denominator <= 1e-12:
        return 0.0
    return float(np.dot(x, y - float(np.mean(y))) / denominator)


def score_summary(scores):
    order = stable_top(scores)
    values = [float(value) for value in scores]
    margin = values[order[0]] - values[order[1]]
    return {
        "top": int(order[0]),
        "ranked": [values[index] for index in order],
        "relative_margin": margin / max(abs(values[order[0]]), 1e-12),
        "normalized_entropy": normalized_entropy(values),
    }


def trajectory_features(history, current_views, track_name):
    """Use only current and prior score points; no future score is inspected."""
    current = history[-1]
    scores = current["main_scores"]
    summary = score_summary(scores)
    ranked = summary["ranked"]
    tops = [point["main_top"] for point in history]
    flip_indices = [index for index in range(1, len(tops)) if tops[index] != tops[index - 1]]
    stable_run = 1
    for index in range(len(tops) - 2, -1, -1):
        if tops[index] != tops[-1]:
            break
        stable_run += 1
    times = [point["evidence_seconds"] for point in history]
    last_points = history[-3:]
    slope_times = [point["evidence_seconds"] for point in last_points]
    top_slope = linear_slope(slope_times, [point["ranked_scores"][0] for point in last_points])
    second_slope = linear_slope(slope_times, [point["ranked_scores"][1] for point in last_points])
    margin_slope = linear_slope(slope_times, [point["relative_margin"] for point in last_points])
    entropy_slope = linear_slope(slope_times, [point["entropy"] for point in last_points])
    prior_margins = [point["relative_margin"] for point in history]
    prior_top_scores = [point["ranked_scores"][0] for point in history]
    margin_increasing = sum(
        int(prior_margins[index] >= prior_margins[index - 1] - 1e-12)
        for index in range(1, len(prior_margins))
    )
    score_increasing = sum(
        int(prior_top_scores[index] >= prior_top_scores[index - 1] - 1e-12)
        for index in range(1, len(prior_top_scores))
    )
    intervals = max(len(prior_margins) - 1, 1)

    bands = current_views["band_scores"]
    band_summaries = [score_summary(values) for values in bands]
    band_agree = sum(int(item["top"] == summary["top"]) for item in band_summaries)
    band_min_margin = min(item["relative_margin"] for item in band_summaries)

    harmonics = current_views["harmonic_scores"]
    h1_summary = score_summary(harmonics[1])
    h2_summary = score_summary(harmonics[2])
    harmonic_agree = 1 + int(h1_summary["top"] == summary["top"]) + int(h2_summary["top"] == summary["top"])

    secondary = current_views["secondary_scores"]
    secondary_summaries = [score_summary(values) for values in secondary]
    secondary_agree = sum(int(item["top"] == summary["top"]) for item in secondary_summaries)
    secondary_margins = [item["relative_margin"] for item in secondary_summaries]
    if secondary_margins:
        secondary_mean = float(np.mean(secondary_margins))
        secondary_min = float(np.min(secondary_margins))
        secondary_std = float(np.std(secondary_margins))
    else:
        secondary_mean = secondary_min = secondary_std = 0.0

    features = {
        "top_score": ranked[0],
        "second_score": ranked[1],
        "third_score": ranked[2],
        "absolute_margin": ranked[0] - ranked[1],
        "relative_margin": summary["relative_margin"],
        "top_second_ratio": ranked[0] / max(abs(ranked[1]), 1e-12),
        "normalized_score_entropy": summary["normalized_entropy"],
        "score_concentration": (ranked[0] - ranked[1]) / max(abs(ranked[0]) + abs(ranked[1]) + abs(ranked[2]), 1e-12),
        "evidence_seconds": float(current["evidence_seconds"]),
        "window_index": float(current["window_index"]),
        "consecutive_same_top": float(stable_run),
        "prior_top_flips": float(len(flip_indices)),
        "time_since_last_top_flip": float(times[-1] - times[flip_indices[-1]]) if flip_indices else float(times[-1]),
        "top_score_slope_last3": top_slope,
        "second_score_slope_last3": second_slope,
        "relative_margin_slope_last3": margin_slope,
        "entropy_slope_last3": entropy_slope,
        "margin_increasing_fraction": margin_increasing / intervals,
        "top_score_increasing_fraction": score_increasing / intervals,
        "band_agreement_count": float(band_agree),
        "band_agreement_fraction": band_agree / max(len(band_summaries), 1),
        "band_min_relative_margin": band_min_margin,
        "harmonic_agreement_count": float(harmonic_agree),
        "harmonic_agreement_fraction": harmonic_agree / 3.0,
        "h1_top_agrees": float(h1_summary["top"] == summary["top"]),
        "h2_top_agrees": float(h2_summary["top"] == summary["top"]),
        "secondary_view_count": float(len(secondary_summaries)),
        "secondary_view_agreement_count": float(secondary_agree),
        "secondary_view_agreement_fraction": secondary_agree / max(len(secondary_summaries), 1),
        "secondary_view_margin_mean": secondary_mean,
        "secondary_view_margin_min": secondary_min,
        "secondary_view_margin_std": secondary_std,
        "secondary_view_margin_delta": secondary_mean - summary["relative_margin"],
        "main_top_is_0": float(summary["top"] == 0),
        "main_top_is_1": float(summary["top"] == 1),
        "main_top_is_2": float(summary["top"] == 2),
    }
    if set(features) != set(FEATURE_NAMES):
        raise AssertionError("runtime feature schema drift")
    return features, summary


def baseline_stop(points):
    for index, window in enumerate(SCHEDULE):
        if window < M35_MIN_EVIDENCE - TOL:
            continue
        start = index - M35_STABLE + 1
        if start < 0:
            continue
        point = points[window]
        same = all(points[SCHEDULE[j]]["top"] == point["top"] for j in range(start, index + 1))
        if same and point["relative_margin"] + TOL >= M35_MARGIN:
            result = dict(point)
            result["evidence_seconds"] = float(window)
            return result
    result = dict(points[SCHEDULE[-1]])
    result["evidence_seconds"] = float(SCHEDULE[-1])
    return result


def load_m35_reference():
    rows = read_csv(M35_DIR / "controlled_context_per_trial.csv")
    references = {}
    for row in rows:
        if row["condition"] != "EEG_ONLY":
            continue
        key = (row["sessionKey"], row["trialId"])
        if key in references:
            raise ValueError("duplicate M35 EEG_ONLY trajectory: {}".format(key))
        points = json.loads(row["eegScoreTrajectory"])
        references[key] = {float(item["t"]): [float(x) for x in item["scores"]] for item in points}
    return references


def make_feature_rows():
    manifest = m34.MANIFEST
    usable_trials = [row for row in manifest["trials"] if bool(row["usable"])]
    if len(usable_trials) != 118:
        raise ValueError("expected 118 usable M34 trials; found {}".format(len(usable_trials)))
    m35_reference = load_m35_reference()
    if len(m35_reference) != 118:
        raise ValueError("expected one M35 frozen score trajectory per real EEG trial")

    features_out = []
    labels_out = []
    reproduction = {"track1_5ch": {"matchedScoreVectors": 0, "expectedScoreVectors": 88 * len(SCHEDULE)},
                    "track2_common3_S7": {"matchedScoreVectors": 0, "expectedScoreVectors": 30 * len(SCHEDULE)}}
    class_counts = defaultdict(Counter)
    session_counts = defaultdict(Counter)

    for session_key in ["A", "B1", "B2", "S7"]:
        raw = m34.load_raw(session_key)
        trial_rows = sorted(
            [row for row in usable_trials if row["sessionKey"] == session_key],
            key=lambda row: int(row["trialIndex"]),
        )
        expected_n = {"A": 30, "B1": 29, "B2": 29, "S7": 30}[session_key]
        if len(trial_rows) != expected_n:
            raise ValueError("{} usable trial count mismatch".format(session_key))
        for trial in trial_rows:
            track_histories = {name: [] for name, spec in TRACKS.items() if session_key in spec["sessions"]}
            main_points = {name: {} for name in track_histories}
            for window_index, window in enumerate(SCHEDULE):
                epoch_all = m34.make_raw_epoch(raw, trial, 0.5, window, tuple(range(8)))
                if epoch_all.shape[1] != int(round(window * 1000.0)):
                    raise ValueError("window sample count mismatch for {}/{}".format(session_key, trial["trialId"]))
                views_by_track = {}

                # Track 1: frozen five-channel FBCCA with independent common-three view.
                if session_key in TRACKS["track1_5ch"]["sessions"]:
                    epoch5 = epoch_all[CHANNELS_5]
                    h1 = predict_fbcca(epoch5, FREQUENCIES, 1, 1000.0)
                    h2 = predict_fbcca(epoch5, FREQUENCIES, 2, 1000.0)
                    h3 = predict_fbcca(epoch5, FREQUENCIES, 3, 1000.0)
                    common3 = predict_fbcca(epoch_all[CHANNELS_3], FREQUENCIES, 3, 1000.0)
                    views_by_track["track1_5ch"] = {
                        "main_scores": [float(x) for x in h3[1]],
                        "band_scores": [[float(x) for x in band] for band in h3[2]],
                        "harmonic_scores": {1: [float(x) for x in h1[1]], 2: [float(x) for x in h2[1]],
                                            3: [float(x) for x in h3[1]]},
                        "secondary_scores": [[float(x) for x in common3[1]]],
                        "secondary_names": ["common3_ch_2_4_7"],
                    }
                    ref = m35_reference[(session_key, trial["trialId"]) ]
                    if window in ref:
                        if not np.allclose(h3[1], ref[window], rtol=0.0, atol=TOL):
                            raise ValueError("M35 five-channel raw trajectory mismatch {}/{} at {}".format(
                                session_key, trial["trialId"], window))
                        reproduction["track1_5ch"]["matchedScoreVectors"] += 1

                # Track 2: common three-channel backbone and three leave-one-channel-out views.
                if session_key in TRACKS["track2_common3"]["sessions"]:
                    epoch3 = epoch_all[CHANNELS_3]
                    h1_3 = predict_fbcca(epoch3, FREQUENCIES, 1, 1000.0)
                    h2_3 = predict_fbcca(epoch3, FREQUENCIES, 2, 1000.0)
                    h3_3 = predict_fbcca(epoch3, FREQUENCIES, 3, 1000.0)
                    pairwise = []
                    pairwise_names = []
                    for subset in ([2, 4], [2, 7], [4, 7]):
                        result = predict_fbcca(epoch_all[subset], FREQUENCIES, 3, 1000.0)
                        pairwise.append([float(x) for x in result[1]])
                        pairwise_names.append("channels_{}_{}".format(subset[0], subset[1]))
                    views_by_track["track2_common3"] = {
                        "main_scores": [float(x) for x in h3_3[1]],
                        "band_scores": [[float(x) for x in band] for band in h3_3[2]],
                        "harmonic_scores": {1: [float(x) for x in h1_3[1]], 2: [float(x) for x in h2_3[1]],
                                            3: [float(x) for x in h3_3[1]]},
                        "secondary_scores": pairwise,
                        "secondary_names": pairwise_names,
                    }
                    if session_key == "S7":
                        ref = m35_reference[(session_key, trial["trialId"]) ]
                        if window in ref:
                            if not np.allclose(h3_3[1], ref[window], rtol=0.0, atol=TOL):
                                raise ValueError("M35 S7 common-three raw trajectory mismatch {}/{} at {}".format(
                                    session_key, trial["trialId"], window))
                            reproduction["track2_common3_S7"]["matchedScoreVectors"] += 1

                for track_name, views in views_by_track.items():
                    point = score_summary(views["main_scores"])
                    history_point = {
                        "evidence_seconds": float(window),
                        "window_index": int(window_index),
                        "main_scores": views["main_scores"],
                        "main_top": point["top"],
                        "ranked_scores": point["ranked"],
                        "relative_margin": point["relative_margin"],
                        "entropy": point["normalized_entropy"],
                    }
                    track_histories[track_name].append(history_point)
                    main_points[track_name][float(window)] = point
                    feature_values, point_summary = trajectory_features(
                        track_histories[track_name], views, track_name)
                    track_spec = TRACKS[track_name]
                    row = {
                        "track": track_name,
                        "sessionKey": session_key,
                        "sessionId": trial["sessionId"],
                        "recordingSessionGroup": trial["sessionId"],
                        "acquisitionCampaignGroup": CAMPAIGN_GROUP[session_key],
                        "trialId": trial["trialId"],
                        "trialIndex": int(trial["trialIndex"]),
                        "evidenceSeconds": float(window),
                        "windowIndex": window_index,
                        "channels": json.dumps(track_spec["channels"], separators=(",", ":")),
                        "mainScoresBySlot": json.dumps(views["main_scores"], separators=(",", ":")),
                        "mainTopSlotIndex": point_summary["top"],
                        "mainRelativeMargin": point_summary["relative_margin"],
                        "bandScoresByBandAndSlot": json.dumps(views["band_scores"], separators=(",", ":")),
                        "harmonicScoresByHarmonic": json.dumps(views["harmonic_scores"], separators=(",", ":")),
                        "secondaryViewNames": json.dumps(views["secondary_names"], separators=(",", ":")),
                        "secondaryScoresByView": json.dumps(views["secondary_scores"], separators=(",", ":")),
                    }
                    for feature_name in FEATURE_NAMES:
                        row["feature_" + feature_name] = float(feature_values[feature_name])
                    features_out.append(row)
                    class_counts[track_name][str(int(trial["slotIndex"]))] += int(window_index == 0)
                    session_counts[track_name][session_key] += int(window_index == 0)

            for track_name, history in track_histories.items():
                stop = baseline_stop(main_points[track_name])
                final_class = int(stop["top"])
                for point in history:
                    top = int(point["main_top"])
                    labels_out.append({
                        "track": track_name,
                        "sessionKey": session_key,
                        "sessionId": trial["sessionId"],
                        "recordingSessionGroup": trial["sessionId"],
                        "acquisitionCampaignGroup": CAMPAIGN_GROUP[session_key],
                        "trialId": trial["trialId"],
                        "trialIndex": int(trial["trialIndex"]),
                        "evidenceSeconds": float(point["evidence_seconds"]),
                        "windowIndex": int(point["window_index"]),
                        "trueSlotIndex": int(trial["slotIndex"]),
                        "safeTrue": int(top == int(trial["slotIndex"])),
                        "frozenEegOnlyFinalClass": final_class,
                        "safeFinal": int(top == final_class),
                        "eegOnlyDecisionClass": final_class,
                        "eegOnlyDecisionEvidenceSeconds": float(stop["evidence_seconds"]),
                        "eegOnlyDecisionCorrect": int(final_class == int(trial["slotIndex"])),
                        "futureLabelOnly": 1,
                    })

        del raw

    expected_feature_rows = 88 * len(SCHEDULE) + 118 * len(SCHEDULE)
    if len(features_out) != expected_feature_rows or len(labels_out) != expected_feature_rows:
        raise ValueError("feature/label row count mismatch: {} / {} != {}".format(
            len(features_out), len(labels_out), expected_feature_rows))
    expected_5ch = 88 * len(SCHEDULE)
    expected_s7 = 30 * len(SCHEDULE)
    if reproduction["track1_5ch"]["matchedScoreVectors"] != expected_5ch:
        raise ValueError("five-channel M35 raw trajectory parity incomplete")
    if reproduction["track2_common3_S7"]["matchedScoreVectors"] != expected_s7:
        raise ValueError("S7 common-three M35 raw trajectory parity incomplete")

    feature_fields = list(features_out[0])
    write_csv(OUT / "eeg_trajectory_features.csv", features_out, feature_fields)
    write_csv(OUT / "reliability_labels.csv", labels_out, list(labels_out[0]))
    write_json(OUT / "trajectory_reproduction.json", {
        "status": "PASS",
        "frozenStack": "FBCCA-003 / 0.5 s guard / 3-band / H=3 / raw argmax; E200_M175_S2 labels",
        "sampleRateHz": 1000,
        "scheduleSeconds": SCHEDULE,
        "track1FiveChannelRawParity": reproduction["track1_5ch"],
        "track2S7CommonThreeRawParity": reproduction["track2_common3_S7"],
        "allRawInputsMatchFrozenManifestBeforeAndAfter": True,
        "noFutureSamplesInFeatureWindows": True,
        "featureRows": len(features_out),
        "labelRows": len(labels_out),
        "tolerance": {"atol": TOL, "rtol": 0.0},
    })
    data_summary = {
        "uniqueRealTrials": 118,
        "slotFrequencyHz": {"0": 7.2, "1": 9.0, "2": 12.0},
        "tracks": {},
        "timepointsPerTrial": len(SCHEDULE),
        "featureRowsTotal": len(features_out),
        "reliabilityLabelRowsTotal": len(labels_out),
        "featureNames": FEATURE_NAMES,
        "runtimeFeaturePolicy": "Only current/past EEG through evidenceSeconds; trial truth and frozen final class appear only in separate label table.",
    }
    for track_name, track_spec in TRACKS.items():
        n_trials = sum(session_counts[track_name].values())
        data_summary["tracks"][track_name] = {
            "sessionKeys": track_spec["sessions"],
            "channels": track_spec["channels"],
            "realTrialCount": n_trials,
            "trialCountBySession": dict(session_counts[track_name]),
            "trialCountBySlot": dict(class_counts[track_name]),
            "featureRows": n_trials * len(SCHEDULE),
        }
    write_json(OUT / "data_track_summary.json", data_summary)

    # Re-hash source raw files after all waveform reads; source evidence is read-only.
    raw_after = []
    for session in m34.MANIFEST["sessions"]:
        raw_name = "raw-eeg.jsonl" if session["sessionKey"] == "S7" else "raw-eeg-packets.jsonl"
        raw_path = Path(session["sourcePath"]) / raw_name
        actual = sha256_file(raw_path)
        expected = session["rawFileSha256"]
        raw_after.append({"sessionKey": session["sessionKey"], "path": str(raw_path),
                          "expectedManifestSha256": expected, "sha256AfterFeatures": actual,
                          "unchanged": actual == expected})
    if not all(item["unchanged"] for item in raw_after):
        raise ValueError("one or more source raw EEG files changed during M36 feature extraction")
    write_json(OUT / "m36_source_integrity_after_features.json", {
        "status": "PASS", "rawEegReadOnly": True, "rawFiles": raw_after,
        "m34ManifestSha256": sha256_file(M34_DIR / "data_manifest.json"),
    })
    return data_summary


if __name__ == "__main__":
    summary = make_feature_rows()
    print(json.dumps({"status": "PASS", "features": summary["featureRowsTotal"],
                      "tracks": summary["tracks"]}, sort_keys=True))
