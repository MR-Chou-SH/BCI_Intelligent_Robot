"""Frozen A/B1 guard and bounded SSVEP decoder search; never loads held-out."""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT))
from eeg.decoder.fbcca import FilterBand, FbccaConfig, apply_filter_band, predict_fbcca  # noqa: E402
from m34_decoders import fit_etrcca, fit_tdca, predict_etrcca, predict_tdca  # noqa: E402


MANIFEST = json.loads((OUT / "data_manifest.json").read_text(encoding="utf-8"))
PROTOCOL = json.loads((OUT / "frozen_protocol.json").read_text(encoding="utf-8"))
FREQUENCIES = (7.2, 9.0, 12.0)
WINDOWS = PROTOCOL["fixedEvidenceWindowsSeconds"]
GUARDS = PROTOCOL["onsetGuardSearchSeconds"]
CHANNEL_SETS = [tuple(value) for value in PROTOCOL["boundedTrainDevSearch"]["channelSets"]]
BANDS = FbccaConfig().filter_bands
FILTER_WEIGHTS = FbccaConfig().weights()
SINGLE_BAND = (BANDS[0],)
ALL_TRIALS = MANIFEST["trials"]
SESSIONS = {item["sessionKey"]: item for item in MANIFEST["sessions"]}


def load_raw(session_key):
    session = SESSIONS[session_key]
    raw_name = "raw-eeg.jsonl" if session_key == "S7" else "raw-eeg-packets.jsonl"
    raw_path = Path(session["sourcePath"]) / raw_name
    expected = int(session["rawSampleCountPerChannel"])
    data = np.empty((int(session["channelCount"]), expected), dtype=np.float64)
    position = 0
    with raw_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            packet = json.loads(line)
            values = np.asarray(packet["samples"], dtype=np.float64)
            count = values.shape[1]
            if values.shape[0] != data.shape[0] or position + count > expected:
                raise ValueError(f"raw packet shape/count mismatch in {session_key}")
            data[:, position:position + count] = values
            position += count
    if position != expected:
        raise ValueError(f"raw sample count mismatch for {session_key}: {position} != {expected}")
    return data


def make_raw_epoch(raw, trial, guard_seconds, window_seconds, channels):
    rate = int(trial["sampleRateHz"])
    length = int(round(window_seconds * rate))
    start = int(trial["onsetGlobalSampleIndex"]) + int(round(guard_seconds * rate))
    stop = start + length
    if stop > int(trial["stopGlobalSampleIndex"]):
        raise ValueError(f"window exceeds associated stimulus span: {trial['sessionKey']}/{trial['trialId']}")
    epoch = raw[list(channels), start:stop]
    if epoch.shape != (len(channels), length):
        raise ValueError(f"unexpected epoch shape for {trial['sessionKey']}/{trial['trialId']}")
    return epoch


def filtered(epoch, band):
    value = apply_filter_band(epoch, band, 1000.0, padding_samples=500)
    return value - np.mean(value, axis=1, keepdims=True)


def metrics(true_labels, predicted):
    true_labels = np.asarray(true_labels, dtype=int)
    predicted = np.asarray(predicted, dtype=int)
    recalls = []
    confusion = [[0, 0, 0] for _ in range(3)]
    for truth, guess in zip(true_labels, predicted):
        confusion[int(truth)][int(guess)] += 1
    for class_id in range(3):
        denominator = int(np.sum(true_labels == class_id))
        recalls.append(sum(confusion[class_id]) and confusion[class_id][class_id] / denominator if denominator else 0.0)
    accuracy = float(np.mean(true_labels == predicted)) if len(true_labels) else 0.0
    return {
        "n": int(len(true_labels)),
        "correct": int(np.sum(true_labels == predicted)),
        "accuracy": accuracy,
        "balancedAccuracy": float(np.mean(recalls)),
        "perClassAccuracyBySlot": recalls,
        "confusionMatrixTrueSlotByPredictedSlot": confusion,
    }


def fbcca_config(variant):
    if variant == "three-band":
        return FbccaConfig()
    if variant == "single-wideband":
        return FbccaConfig(filter_bands=SINGLE_BAND)
    raise ValueError(variant)


def guard_search(raw_by_session):
    guard_rows = []
    for guard in GUARDS:
        for session_key in ("A", "B1"):
            trial_rows = [row for row in ALL_TRIALS if row["sessionKey"] == session_key]
            raw = raw_by_session[session_key]
            for window in WINDOWS:
                true, predicted, compute_ns = [], [], []
                for trial in trial_rows:
                    epoch = make_raw_epoch(raw, trial, guard, window, (2, 3, 4, 5, 7))
                    start_ns = time.perf_counter_ns()
                    pred, _, _ = predict_fbcca(epoch, FREQUENCIES, 3, 1000.0)
                    compute_ns.append(time.perf_counter_ns() - start_ns)
                    true.append(trial["slotIndex"])
                    predicted.append(pred)
                result = metrics(true, predicted)
                guard_rows.append({
                    "sessionKey": session_key,
                    "guardSeconds": guard,
                    "evidenceSeconds": window,
                    "nominalLoggedOnsetRelativeDecisionSeconds": guard + window,
                    **result,
                    "meanComputeMs": float(np.mean(compute_ns) / 1e6),
                    "medianComputeMs": float(np.median(compute_ns) / 1e6),
                    "p90ComputeMs": float(np.percentile(compute_ns, 90) / 1e6),
                })
    with (OUT / "onset_guard_search.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(guard_rows[0]))
        writer.writeheader()
        writer.writerows(guard_rows)

    short_windows = {0.3, 0.4, 0.5}
    candidates = []
    for guard in GUARDS:
        rows = [row for row in guard_rows if row["sessionKey"] == "B1" and row["guardSeconds"] == guard]
        short = [row["balancedAccuracy"] for row in rows if row["evidenceSeconds"] in short_windows]
        all_windows = [row["balancedAccuracy"] for row in rows]
        candidates.append({
            "guardSeconds": guard,
            "devMeanBalancedAccuracyAt030405": float(np.mean(short)),
            "devMeanBalancedAccuracyAllWindows": float(np.mean(all_windows)),
            "devAccuracyAt030405": {
                str(row["evidenceSeconds"]): row["accuracy"] for row in rows if row["evidenceSeconds"] in short_windows
            },
        })
    candidates.sort(key=lambda item: (
        -item["devMeanBalancedAccuracyAt030405"],
        -item["devMeanBalancedAccuracyAllWindows"],
        item["guardSeconds"],
    ))
    selected = candidates[0]["guardSeconds"]
    result = {
        "status": "PASS",
        "selectionSession": "B1",
        "reportedButNotSelectedSession": "A",
        "decoder": "existing default FBCCA, 5 channels, 3 harmonics",
        "selectionRule": PROTOCOL["guardSelectionRule"],
        "candidates": candidates,
        "selectedGuardSeconds": selected,
        "note": "This is a nominal software event-to-sample guard, not a physical visual latency measurement.",
    }
    (OUT / "onset_guard_search.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return selected, guard_rows


def precompute_train_epochs(raw_by_session, window, guard):
    result = {}
    for session_key in ("A", "B1"):
        raw = raw_by_session[session_key]
        rows = [row for row in ALL_TRIALS if row["sessionKey"] == session_key]
        epochs = []
        filtered_by_band = [[] for _ in BANDS]
        labels = []
        for trial in rows:
            epoch = make_raw_epoch(raw, trial, guard, window, tuple(range(8)))
            epoch = epoch - np.mean(epoch, axis=1, keepdims=True)
            epochs.append(epoch)
            for band_index, band in enumerate(BANDS):
                filtered_by_band[band_index].append(filtered(epoch, band))
            labels.append(int(trial["slotIndex"]))
        result[session_key] = {
            "rows": rows,
            "raw": np.asarray(epochs),
            "bands": [np.asarray(values) for values in filtered_by_band],
            "labels": np.asarray(labels, dtype=int),
        }
    return result


def test_epoch(raw_by_session, trial, guard, window, channels):
    return make_raw_epoch(raw_by_session[trial["sessionKey"]], trial, guard, window, channels)


def score_bands(predictor, band_models, epoch, band_variant, channel_ids):
    channels = list(channel_ids)
    if band_variant == "demean-only":
        return predictor(band_models[0], epoch[channels])
    predictions = []
    for model, band in zip(band_models, BANDS):
        predictions.append(predictor(model, filtered(epoch[channels], band)))
    return FILTER_WEIGHTS @ np.asarray(predictions)


def config_grid():
    grid = []
    for channels in CHANNEL_SETS:
        for harmonics in PROTOCOL["boundedTrainDevSearch"]["FBCCA"]["harmonicCounts"]:
            for variant in ("single-wideband", "three-band"):
                grid.append({"decoder": "FBCCA", "channels": channels, "harmonics": harmonics, "filterVariant": variant})
        for variant in ("demean-only", "three-band"):
            for components in PROTOCOL["boundedTrainDevSearch"]["eTRCA"]["ensembleComponentsPerClass"]:
                grid.append({"decoder": "eTRCA", "channels": channels, "filterVariant": variant, "components": components})
            for harmonics in PROTOCOL["boundedTrainDevSearch"]["TDCA"]["harmonicCounts"]:
                for delay_step in PROTOCOL["boundedTrainDevSearch"]["TDCA"]["delayStepSamplesAt1000Hz"]:
                    for components in PROTOCOL["boundedTrainDevSearch"]["TDCA"]["discriminantComponents"]:
                        for ridge in PROTOCOL["boundedTrainDevSearch"]["TDCA"]["relativeSwRidge"]:
                            grid.append({
                                "decoder": "TDCA",
                                "channels": channels,
                                "filterVariant": variant,
                                "harmonics": harmonics,
                                "delayCopies": 6,
                                "delayStepSamples": delay_step,
                                "components": components,
                                "relativeSwRidge": ridge,
                            })
    return grid


def fit_models(config, prepared):
    train = prepared["A"]
    channels = list(config["channels"])
    labels = train["labels"]
    models = []
    fit_ns = 0
    if config["decoder"] == "FBCCA":
        return None, 0
    if config["filterVariant"] == "demean-only":
        band_arrays = [train["raw"][:, channels, :]]
    else:
        band_arrays = [value[:, channels, :] for value in train["bands"]]
    for values in band_arrays:
        start_ns = time.perf_counter_ns()
        if config["decoder"] == "eTRCA":
            model = fit_etrcca(values, labels, component_count=config["components"], relative_ridge=1e-6)
        elif config["decoder"] == "TDCA":
            model = fit_tdca(
                values,
                labels,
                frequencies_hz=FREQUENCIES,
                sampling_rate_hz=1000.0,
                harmonic_count=config["harmonics"],
                delay_count=config["delayCopies"] - 1,
                delay_step_samples=config["delayStepSamples"],
                component_count=config["components"],
                relative_ridge=config["relativeSwRidge"],
            )
        else:
            raise ValueError(config["decoder"])
        fit_ns += time.perf_counter_ns() - start_ns
        models.append(model)
    return models, fit_ns


def predict_config(config, models, epoch, channels):
    decoder = config["decoder"]
    variant = config["filterVariant"]
    if decoder == "FBCCA":
        start_ns = time.perf_counter_ns()
        predicted, scores, _ = predict_fbcca(
            epoch[list(channels)],
            FREQUENCIES,
            int(config["harmonics"]),
            1000.0,
            config=fbcca_config(variant),
        )
        return np.asarray(scores, dtype=float), time.perf_counter_ns() - start_ns

    start_ns = time.perf_counter_ns()
    if variant == "demean-only":
        values = epoch[list(channels)]
        predictor = predict_etrcca if decoder == "eTRCA" else predict_tdca
        scores = predictor(models[0], values)
    else:
        parts = []
        predictor = predict_etrcca if decoder == "eTRCA" else predict_tdca
        for model, band in zip(models, BANDS):
            parts.append(predictor(model, filtered(epoch[list(channels)], band)))
        scores = FILTER_WEIGHTS @ np.asarray(parts)
    return np.asarray(scores, dtype=float), time.perf_counter_ns() - start_ns


def config_complexity(config):
    score = len(config["channels"])
    score += int(config.get("harmonics", 0))
    score += int(config.get("components", 0))
    score += int(config.get("delayCopies", 0))
    score += int(config.get("delayStepSamples", 0))
    score += int(config.get("relativeSwRidge", 0) * 1000)
    score += 3 if config["filterVariant"] in ("three-band", "three-band") else 0
    return score


def run_search(raw_by_session, guard):
    summary_rows = []
    trial_rows = []
    selected = {}
    grid = config_grid()
    for window in WINDOWS:
        prepared = precompute_train_epochs(raw_by_session, window, guard)
        dev_trials = [row for row in ALL_TRIALS if row["sessionKey"] == "B1"]
        for config_index, config in enumerate(grid):
            models, fit_ns = fit_models(config, prepared)
            config_key = f"{config['decoder']}-{config_index:03d}"
            session_true = []
            session_pred = []
            compute_times = []
            for trial in dev_trials:
                epoch = test_epoch(raw_by_session, trial, guard, window, tuple(range(8)))
                scores, compute_ns = predict_config(config, models, epoch, config["channels"])
                predicted = int(np.argmax(scores))
                true = int(trial["slotIndex"])
                session_true.append(true)
                session_pred.append(predicted)
                compute_times.append(compute_ns)
                trial_rows.append({
                    "configId": config_key,
                    "decoder": config["decoder"],
                    "config": json.dumps({key: list(value) if isinstance(value, tuple) else value for key, value in config.items()}, sort_keys=True, separators=(",", ":")),
                    "sessionKey": "B1",
                    "trialId": trial["trialId"],
                    "trueSlotIndex": true,
                    "predictedSlotIndex": predicted,
                    "scoresBySlot": json.dumps(scores.tolist(), separators=(",", ":")),
                    "evidenceSeconds": window,
                    "guardSeconds": guard,
                    "nominalLoggedOnsetRelativeDecisionSeconds": guard + window,
                    "computeNs": compute_ns,
                })
            result = metrics(session_true, session_pred)
            summary_rows.append({
                "configId": config_key,
                "decoder": config["decoder"],
                "config": json.dumps({key: list(value) if isinstance(value, tuple) else value for key, value in config.items()}, sort_keys=True, separators=(",", ":")),
                "evidenceSeconds": window,
                "guardSeconds": guard,
                "nominalLoggedOnsetRelativeDecisionSeconds": guard + window,
                **result,
                "meanComputeMs": float(np.mean(compute_times) / 1e6),
                "medianComputeMs": float(np.median(compute_times) / 1e6),
                "p90ComputeMs": float(np.percentile(compute_times, 90) / 1e6),
                "modelFitMs": fit_ns / 1e6,
                "configComplexity": config_complexity(config),
            })
        print(f"search_window_complete={window:.2f}s configs={len(grid)}", flush=True)

    with (OUT / "train_dev_search_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)
    with (OUT / "train_dev_search_per_trial.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(trial_rows[0]))
        writer.writeheader()
        writer.writerows(trial_rows)

    windows_for_selection = {0.3, 0.4, 0.5}
    for decoder in ("FBCCA", "eTRCA", "TDCA"):
        candidate_configs = [row for row in summary_rows if row["decoder"] == decoder]
        by_id = defaultdict(list)
        for row in candidate_configs:
            by_id[row["configId"]].append(row)
        ranked = []
        for config_id, rows in by_id.items():
            config = json.loads(rows[0]["config"])
            ranked.append({
                "configId": config_id,
                "config": config,
                "devMeanBalancedAccuracyAt030405": float(np.mean([
                    row["balancedAccuracy"] for row in rows if row["evidenceSeconds"] in windows_for_selection
                ])),
                "devMeanBalancedAccuracyAllWindows": float(np.mean([row["balancedAccuracy"] for row in rows])),
                "devMeanComputeMs": float(np.mean([row["meanComputeMs"] for row in rows])),
                "devMeanAccuracyAt030405": float(np.mean([
                    row["accuracy"] for row in rows if row["evidenceSeconds"] in windows_for_selection
                ])),
            })
        ranked.sort(key=lambda item: (
            -item["devMeanBalancedAccuracyAt030405"],
            -item["devMeanBalancedAccuracyAllWindows"],
            item["devMeanComputeMs"],
            config_complexity(item["config"]),
            item["configId"],
        ))
        selected[decoder] = {
            **ranked[0],
            "devPerWindow": [
                {
                    "evidenceSeconds": row["evidenceSeconds"],
                    "accuracy": row["accuracy"],
                    "balancedAccuracy": row["balancedAccuracy"],
                    "perClassAccuracyBySlot": json.loads(row["perClassAccuracyBySlot"])
                    if isinstance(row["perClassAccuracyBySlot"], str) else row["perClassAccuracyBySlot"],
                    "confusionMatrixTrueSlotByPredictedSlot": row["confusionMatrixTrueSlotByPredictedSlot"],
                    "meanComputeMs": row["meanComputeMs"],
                    "medianComputeMs": row["medianComputeMs"],
                    "p90ComputeMs": row["p90ComputeMs"],
                }
                for row in candidate_configs if row["configId"] == ranked[0]["configId"]
            ],
        }

    result = {
        "status": "PASS",
        "split": {"train": "A", "dev": "B1", "heldoutNotLoaded": ["B2", "S7"]},
        "frozenGuardSeconds": guard,
        "selectionRule": PROTOCOL["boundedTrainDevSearch"]["configurationSelectionRule"],
        "candidateConfigurationCount": len(grid),
        "candidateMetricsPerWindow": len(summary_rows),
        "selected": selected,
        "note": "These are development results only. They do not select or report held-out outcomes.",
    }
    (OUT / "selected_eeg_backbones.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    if set(row["trialId"] for row in ALL_TRIALS if row["sessionKey"] == "A") & set(
        row["trialId"] for row in ALL_TRIALS if row["sessionKey"] == "B1"
    ):
        raise SystemExit("train/dev trial overlap detected")
    raw_by_session = {key: load_raw(key) for key in ("A", "B1")}
    guard, _ = guard_search(raw_by_session)
    result = run_search(raw_by_session, guard)
    print(json.dumps({
        "status": result["status"],
        "selectedGuardSeconds": guard,
        "candidateConfigurations": result["candidateConfigurationCount"],
        "selected": {name: {"configId": item["configId"], "config": item["config"], "devMeanBalancedAccuracyAt030405": item["devMeanBalancedAccuracyAt030405"]} for name, item in result["selected"].items()},
    }, indent=2))


if __name__ == "__main__":
    main()
