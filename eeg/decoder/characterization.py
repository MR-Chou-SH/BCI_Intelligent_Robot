"""M6.3a fixed-grid decoder window characterization."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .cca import predict, references
from .config import DecoderConfig
from .dataset import extract_epochs, load_dataset
from .fbcca import FbccaConfig, predict_fbcca
from .pipeline import evaluate_predictions


WINDOW_GRID_SECONDS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)


def validate_window_grid(windows=WINDOW_GRID_SECONDS):
    values = tuple(float(value) for value in windows)
    if values != tuple(sorted(set(values))) or any(value <= 0 for value in values):
        raise ValueError("window grid must be positive, unique and ascending")
    if not values:
        raise ValueError("window grid cannot be empty")
    return values


def _margin_summary(results, labels):
    margins = [float(item["scoreMargin"]) for item in results]
    per_class = {}
    for label in labels:
        values = [float(item["scoreMargin"]) for item in results if item["trueClass"] == label]
        per_class[label] = {"min": min(values), "median": float(np.median(values)),
                            "mean": float(np.mean(values)), "max": max(values)}
    return {"min": min(margins), "median": float(np.median(margins)),
            "mean": float(np.mean(margins)), "max": max(margins), "perClass": per_class}


def _run_window(epochs, seconds, base_config, fbcca_config, decoder):
    labels = ["target_left", "target_center", "target_right"]
    sample_count = int(round(seconds * base_config.decoder_sampling_rate_hz))
    refs = references(base_config.target_frequencies_hz, base_config.harmonic_count,
                      base_config.decoder_sampling_rate_hz, sample_count)
    results = []
    stability = []
    for epoch in epochs:
        signal = epoch.data[:, :sample_count]
        signal = signal - signal.mean(axis=1, keepdims=True)
        if decoder == "standard_cca":
            index, scores = predict(signal, refs)
            score_key = "scores"
            score_values = scores
        else:
            index, score_values, subband = predict_fbcca(signal, base_config.target_frequencies_hz,
                                                         base_config.harmonic_count,
                                                         base_config.decoder_sampling_rate_hz,
                                                         fbcca_config)
            score_key = "fusedScores"
        true_class = epoch.trial["targetId"]
        predicted = labels[index]
        score_values = [float(value) for value in score_values]
        results.append({"trialIndex": epoch.trial["trialIndex"], "trialId": epoch.trial["trialId"],
                        "trueClass": true_class, "trueFrequencyHz": epoch.trial["nominalFrequencyHz"],
                        "predictedClass": predicted, "predictedFrequencyHz": base_config.target_frequencies_hz[index],
                        score_key: {str(freq): value for freq, value in zip(base_config.target_frequencies_hz, score_values)},
                        "scoreMargin": max(score_values) - sorted(score_values)[-2],
                        "correct": predicted == true_class,
                        "sampleRange": {"startInclusive": epoch.start_sample,
                                        "stopExclusive": epoch.start_sample + sample_count},
                        "association": {"startEstimatedGlobalSampleIndex": epoch.start_association["estimatedGlobalSampleIndex"],
                                         "segmentId": epoch.start_association["timestampSegmentId"],
                                         "mapping": "software-derived estimate"}})
        stability.append({"trialIndex": epoch.trial["trialIndex"],
                          "signalRank": int(np.linalg.matrix_rank(signal)),
                          "referenceRank": int(np.linalg.matrix_rank(refs[0]))})
    evaluation = evaluate_predictions(results, labels)
    return {"windowSeconds": seconds, "decoder": decoder, "sampleCount": sample_count,
            "correct": evaluation["correct"], "total": evaluation["total"],
            "accuracy": evaluation["accuracy"], "perClass": evaluation["perClass"],
            "confusionMatrix": evaluation["confusionMatrix"], "margin": _margin_summary(results, labels),
            "trials": results, "numericalStability": stability}


def run_characterization(session, output=None, windows=WINDOW_GRID_SECONDS):
    windows = validate_window_grid(windows)
    base_config = DecoderConfig()
    fbcca_config = FbccaConfig()
    dataset = load_dataset(session)
    max_epochs = extract_epochs(dataset, base_config)
    max_duration = base_config.analysis_duration_seconds
    if any(window > max_duration for window in windows):
        raise ValueError("window exceeds frozen available analysis interval")
    if any(window < base_config.onset_guard_seconds for window in windows):
        raise ValueError("window must be positive after the fixed onset guard")
    rows = []
    for seconds in windows:
        for decoder in ("standard_cca", "fbcca"):
            rows.append(_run_window(max_epochs, seconds, base_config, fbcca_config, decoder))
    artifact = {"recordType": "m6_3a_window_characterization", "generatedUtc": datetime.now(timezone.utc).isoformat(),
                "session": str(dataset["session"]), "sessionId": dataset["manifest"]["sessionId"],
                "datasetClassCounts": dict(Counter(item.trial["targetId"] for item in max_epochs)),
                "controlledVariables": {"onsetGuardSeconds": base_config.onset_guard_seconds,
                                        "selectedChannels": dataset["selected_channels"],
                                        "rawSampleRateHz": dataset["sampling_rate_hz"],
                                        "targetFrequenciesHz": list(base_config.target_frequencies_hz),
                                        "harmonicCount": base_config.harmonic_count,
                                        "preprocessing": "demean_per_channel",
                                        "epochStartRule": "same_stimulus_start_plus_fixed_guard"},
                "windowGridSeconds": list(windows), "standardCcaConfig": base_config.to_dict(),
                "fbccaConfig": fbcca_config.to_dict(), "results": rows,
                "warnings": ["single_session_30_trials", "no_cross_session_or_online_validation",
                             "sample_association_is_software_derived_estimate",
                             "FBCCA uses NumPy rFFT raised-cosine filtering, not legacy Chebyshev-I filtfilt"],
                "evidenceBoundary": {"hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                                     "sampleAnchor": "unverified", "association": "software-derived estimate",
                                     "nominalStimulusFrequenciesOpticallyVerified": False}}
    if output:
        Path(output).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact
