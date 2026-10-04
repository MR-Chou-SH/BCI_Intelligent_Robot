"""Reproduce the frozen M6 FBCCA fixed-window baseline on A/B1 only."""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from eeg.decoder.fbcca import predict_fbcca  # noqa: E402


MANIFEST = json.loads((OUT / "data_manifest.json").read_text(encoding="utf-8"))
FREQUENCIES = [7.2, 9.0, 12.0]
EXPECTED = {"A": 1.0, "B1": 28.0 / 29.0}


def load_session_trials(session_key):
    session = next(item for item in MANIFEST["sessions"] if item["sessionKey"] == session_key)
    trial_rows = [row for row in MANIFEST["trials"] if row["sessionKey"] == session_key]
    samples = []
    raw_path = Path(session["sourcePath"]) / ("raw-eeg-packets.jsonl" if session_key != "S7" else "raw-eeg.jsonl")
    with raw_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                packet = json.loads(line)
                samples.append(np.asarray(packet["samples"], dtype=np.float64))
    raw = np.concatenate(samples, axis=1)
    if raw.shape != (session["channelCount"], session["rawSampleCountPerChannel"]):
        raise ValueError(f"raw shape differs from manifest for {session_key}: {raw.shape}")
    return session, trial_rows, raw


def classify_trial(epoch, harmonics=3):
    start_ns = time.perf_counter_ns()
    predicted_index, scores, _ = predict_fbcca(
        epoch,
        FREQUENCIES,
        harmonics,
        1000.0,
    )
    compute_ns = time.perf_counter_ns() - start_ns
    return predicted_index, scores, compute_ns


def summarize(session_key, rows):
    confusion = {str(i): {str(j): 0 for j in range(3)} for i in range(3)}
    class_correct = Counter()
    class_total = Counter()
    correct = 0
    compute_ns = []
    for row in rows:
        true_idx = int(row["trueSlotIndex"])
        pred_idx = int(row["predictedSlotIndex"])
        confusion[str(true_idx)][str(pred_idx)] += 1
        class_total[true_idx] += 1
        class_correct[true_idx] += int(pred_idx == true_idx)
        correct += int(pred_idx == true_idx)
        compute_ns.append(int(row["computeNs"]))
    per_class = {str(i): (class_correct[i] / class_total[i] if class_total[i] else None) for i in range(3)}
    balanced = sum(value for value in per_class.values() if value is not None) / len(per_class)
    return {
        "sessionKey": session_key,
        "n": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows),
        "balancedAccuracy": balanced,
        "perClassAccuracyBySlot": per_class,
        "confusionMatrixTrueSlotByPredictedSlot": confusion,
        "meanAlgorithmComputeMs": float(np.mean(compute_ns) / 1e6),
        "medianAlgorithmComputeMs": float(np.median(compute_ns) / 1e6),
        "p90AlgorithmComputeMs": float(np.percentile(compute_ns, 90) / 1e6),
    }


def main():
    output_rows = []
    summaries = []
    for session_key in ("A", "B1"):
        session, trial_rows, raw = load_session_trials(session_key)
        selected_channels = [2, 3, 4, 5, 7]
        if not set(selected_channels).issubset(set(session["selectedHistoricalChannelIndices"])):
            raise ValueError(f"baseline channels not available for {session_key}")
        for trial in trial_rows:
            start = int(trial["onsetGlobalSampleIndex"] + round(0.5 * 1000.0))
            stop = start + int(round(1.5 * 1000.0))
            if stop > int(trial["stopGlobalSampleIndex"]):
                raise ValueError(f"baseline window exceeds stimulus segment: {session_key}/{trial['trialId']}")
            epoch = raw[selected_channels, start:stop]
            epoch = epoch - np.mean(epoch, axis=1, keepdims=True)
            predicted, scores, compute_ns = classify_trial(epoch, harmonics=3)
            output_rows.append({
                "sessionKey": session_key,
                "trialId": trial["trialId"],
                "trueSlotIndex": trial["slotIndex"],
                "trueFrequencyHz": trial["frequencyHz"],
                "predictedSlotIndex": predicted,
                "predictedFrequencyHz": FREQUENCIES[predicted],
                "scoresBySlot": json.dumps(scores, separators=(",", ":")),
                "guardSeconds": 0.5,
                "evidenceSeconds": 1.5,
                "nominalOnsetRelativeDecisionSeconds": 2.0,
                "computeNs": compute_ns,
            })
        session_rows = [row for row in output_rows if row["sessionKey"] == session_key]
        summary = summarize(session_key, session_rows)
        summary["expectedHistoricalAccuracy"] = EXPECTED[session_key]
        summary["historicalAccuracyMatch"] = abs(summary["accuracy"] - EXPECTED[session_key]) < 1e-12
        summaries.append(summary)

    result = {
        "status": "PASS" if all(item["historicalAccuracyMatch"] for item in summaries) else "MISMATCH_INVESTIGATE",
        "scope": "Train A and Dev B1 only; B2 and S7 were not evaluated.",
        "decoder": "eeg.decoder.fbcca.predict_fbcca",
        "configuration": {
            "channelsZeroBased": [2, 3, 4, 5, 7],
            "rawSamplingRateHz": 1000,
            "preprocessing": "per-channel de-mean; no resampling",
            "harmonics": 3,
            "filterBank": "existing default NumPy raised-cosine bands and weights",
            "guardSeconds": 0.5,
            "evidenceSeconds": 1.5,
            "frequenciesHzBySlot": FREQUENCIES,
        },
        "sessions": summaries,
    }
    (OUT / "baseline_reproduction.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    with (OUT / "baseline_reproduction_per_trial.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)
    print(json.dumps(result, indent=2))
    if result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
