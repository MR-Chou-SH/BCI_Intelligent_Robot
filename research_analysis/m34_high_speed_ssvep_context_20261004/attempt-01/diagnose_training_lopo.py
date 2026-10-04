"""Training-session-only leave-one-trial-out diagnostic for transfer analysis.

This diagnostic does not inspect B2/S7 and does not select the final model.
Each A trial is excluded from calibration when it is used as the fold test.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT))
import run_train_dev_search as search  # noqa: E402
from eeg.decoder.fbcca import apply_filter_band, predict_fbcca  # noqa: E402
from m34_decoders import fit_etrcca, fit_tdca, predict_etrcca, predict_tdca  # noqa: E402


def fit_predict_fold(decoder, config, train_epochs, train_labels, test_epoch):
    channels = list(config["channels"])
    if config["filterVariant"] == "demean-only":
        train_bands = [train_epochs[:, channels, :]]
        test_bands = [test_epoch[channels]]
        weights = np.asarray([1.0])
    else:
        train_bands = [
            np.asarray([apply_filter_band(x, band, 1000.0, 500) for x in train_epochs[:, channels, :]])
            for band in search.BANDS
        ]
        test_bands = [apply_filter_band(test_epoch[channels], band, 1000.0, 500) for band in search.BANDS]
        weights = search.FILTER_WEIGHTS

    models = []
    for values in train_bands:
        values = values - np.mean(values, axis=-1, keepdims=True)
        if decoder == "eTRCA":
            models.append(fit_etrcca(values, train_labels, component_count=config["components"], relative_ridge=1e-6))
        else:
            models.append(fit_tdca(
                values,
                train_labels,
                frequencies_hz=search.FREQUENCIES,
                sampling_rate_hz=1000.0,
                harmonic_count=config["harmonics"],
                delay_count=config["delayCopies"] - 1,
                delay_step_samples=config["delayStepSamples"],
                component_count=config["components"],
                relative_ridge=config["relativeSwRidge"],
            ))
    per_band_scores = []
    start_ns = time.perf_counter_ns()
    for model, values in zip(models, test_bands):
        values = values - np.mean(values, axis=-1, keepdims=True)
        per_band_scores.append(predict_etrcca(model, values) if decoder == "eTRCA" else predict_tdca(model, values))
    scores = weights @ np.asarray(per_band_scores)
    return int(np.argmax(scores)), scores, time.perf_counter_ns() - start_ns


def main():
    selected = json.loads((OUT / "selected_eeg_backbones.json").read_text(encoding="utf-8"))["selected"]
    guard = float(json.loads((OUT / "onset_guard_search.json").read_text(encoding="utf-8"))["selectedGuardSeconds"])
    trials = [row for row in search.ALL_TRIALS if row["sessionKey"] == "A"]
    raw = search.load_raw("A")
    output = []

    for window in (0.3, 0.5, 1.0):
        epochs = np.asarray([
            search.make_raw_epoch(raw, trial, guard, window, tuple(range(8)))
            for trial in trials
        ])
        labels = np.asarray([trial["slotIndex"] for trial in trials], dtype=int)
        for trial_index, trial in enumerate(trials):
            keep = np.arange(len(trials)) != trial_index
            for decoder in ("FBCCA", "eTRCA", "TDCA"):
                if decoder == "FBCCA":
                    start_ns = time.perf_counter_ns()
                    predicted, scores, _ = predict_fbcca(
                        epochs[trial_index][[2, 3, 4, 5, 7]],
                        search.FREQUENCIES,
                        3,
                        1000.0,
                    )
                    compute_ns = time.perf_counter_ns() - start_ns
                    config_id = "historical-5ch-3h-three-band"
                else:
                    config = selected[decoder]["config"]
                    predicted, scores, compute_ns = fit_predict_fold(
                        decoder,
                        config,
                        epochs[keep],
                        labels[keep],
                        epochs[trial_index],
                    )
                    config_id = selected[decoder]["configId"]
                output.append({
                    "sessionKey": "A",
                    "fold": "leave-one-trial-out",
                    "trialId": trial["trialId"],
                    "decoder": decoder,
                    "configId": config_id,
                    "trueSlotIndex": int(labels[trial_index]),
                    "predictedSlotIndex": int(predicted),
                    "scoresBySlot": json.dumps(np.asarray(scores).tolist(), separators=(",", ":")),
                    "evidenceSeconds": window,
                    "guardSeconds": guard,
                    "computeNs": int(compute_ns),
                })
    with (OUT / "training_lopo_per_trial.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output[0]))
        writer.writeheader()
        writer.writerows(output)
    summaries = []
    for decoder in ("FBCCA", "eTRCA", "TDCA"):
        for window in (0.3, 0.5, 1.0):
            rows = [row for row in output if row["decoder"] == decoder and row["evidenceSeconds"] == window]
            result = search.metrics([row["trueSlotIndex"] for row in rows], [row["predictedSlotIndex"] for row in rows])
            summaries.append({"sessionKey": "A", "fold": "leave-one-trial-out", "decoder": decoder, "evidenceSeconds": window, **result})
    result = {
        "status": "PASS",
        "scope": "Train A only. Each test trial was absent from that fold's supervised templates. No B1/B2/S7 labels were used.",
        "guardSeconds": guard,
        "summaries": summaries,
        "interpretation": "Diagnostic of within-session versus A-to-B1 transfer only; not used to select or report final held-out performance.",
    }
    (OUT / "training_lopo_summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
