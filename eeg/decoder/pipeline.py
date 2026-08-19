"""M6.2a offline Standard CCA pipeline and JSON artifact writer."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .cca import predict, references
from .config import DecoderConfig
from .dataset import extract_epochs, load_dataset


def _confusion(trials, labels):
    return {true: {pred: sum(1 for item in trials if item["trueClass"] == true and item["predictedClass"] == pred)
                   for pred in labels} for true in labels}


def evaluate_predictions(trials, labels=("target_left", "target_center", "target_right")):
    """Evaluate explicit prediction records; validity/completeness is not used."""
    correct = sum(item["predictedClass"] == item["trueClass"] for item in trials)
    per_class = {label: {"correct": sum(item["trueClass"] == label and item["predictedClass"] == label for item in trials),
                         "total": sum(item["trueClass"] == label for item in trials)} for label in labels}
    for item in per_class.values():
        item["accuracy"] = item["correct"] / item["total"] if item["total"] else None
    return {"correct": correct, "total": len(trials),
            "accuracy": correct / len(trials) if trials else None,
            "perClass": per_class, "confusionMatrix": _confusion(trials, labels)}


def run_pipeline(session, output=None, config=None):
    config = config or DecoderConfig()
    dataset = load_dataset(session)
    epochs = extract_epochs(dataset, config)
    refs = references(config.target_frequencies_hz, config.harmonic_count,
                      config.decoder_sampling_rate_hz, config.analysis_sample_count)
    labels = ["target_left", "target_center", "target_right"]
    results = []
    for epoch in epochs:
        signal = epoch.data - epoch.data.mean(axis=1, keepdims=True)
        index, scores = predict(signal, refs)
        trial = epoch.trial
        predicted = labels[index]
        results.append({"trialIndex": trial["trialIndex"], "trialId": trial["trialId"],
                        "trueClass": trial["targetId"], "trueFrequencyHz": trial["nominalFrequencyHz"],
                        "predictedClass": predicted, "predictedFrequencyHz": config.target_frequencies_hz[index],
                        "scores": {str(freq): score for freq, score in zip(config.target_frequencies_hz, scores)},
                        "scoreMargin": float(max(scores) - sorted(scores)[-2]) if len(scores) > 1 else 0.0,
                        "correct": predicted == trial["targetId"],
                        "sampleRange": {"startInclusive": epoch.start_sample, "stopExclusive": epoch.stop_sample},
                        "association": {"startEstimatedGlobalSampleIndex": epoch.start_association["estimatedGlobalSampleIndex"],
                                         "stopEstimatedGlobalSampleIndex": epoch.stop_association["estimatedGlobalSampleIndex"],
                                         "segmentId": epoch.start_association["timestampSegmentId"],
                                         "mapping": "software-derived estimate"}})
    evaluation = evaluate_predictions(results, labels)
    artifact = {"recordType": "m6_2a_standard_cca_result", "generatedUtc": datetime.now(timezone.utc).isoformat(),
                "session": str(dataset["session"]), "sessionId": dataset["manifest"]["sessionId"],
                "datasetCompleteness": dataset["completeness"], "selectedChannels": dataset["selected_channels"],
                "rawSampleRateHz": dataset["sampling_rate_hz"], "decoderSampleRateHz": config.decoder_sampling_rate_hz,
                "config": config.to_dict(), "preprocessing": {"method": "demean_per_channel", "fitOnGroundTruth": False},
                "validEpochCount": len(epochs), "classCounts": dict(Counter(i["trueClass"] for i in results)),
                "overall": {"correct": evaluation["correct"], "total": evaluation["total"], "accuracy": evaluation["accuracy"]},
                "perClass": evaluation["perClass"], "confusionMatrix": evaluation["confusionMatrix"], "trials": results,
                "evidenceBoundary": {"hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                                     "sampleAnchor": "unverified", "association": "software-derived estimate"}}
    if output:
        Path(output).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact
