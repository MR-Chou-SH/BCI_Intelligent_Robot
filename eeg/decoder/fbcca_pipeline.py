"""M6.2b FBCCA pipeline reusing the formal loader, epochs and evaluator."""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import DecoderConfig
from .dataset import extract_epochs, load_dataset
from .fbcca import FbccaConfig, predict_fbcca
from .pipeline import evaluate_predictions


def run_fbcca_pipeline(session, output=None, config=None, fbcca_config=None):
    config = config or DecoderConfig()
    fbcca_config = fbcca_config or FbccaConfig()
    dataset = load_dataset(session)
    epochs = extract_epochs(dataset, config)
    labels = ["target_left", "target_center", "target_right"]
    results = []
    for epoch in epochs:
        signal = epoch.data - epoch.data.mean(axis=1, keepdims=True)
        index, fused, subband = predict_fbcca(signal, config.target_frequencies_hz,
                                               config.harmonic_count, config.decoder_sampling_rate_hz,
                                               fbcca_config)
        trial = epoch.trial
        results.append({"trialIndex": trial["trialIndex"], "trialId": trial["trialId"],
                        "trueClass": trial["targetId"], "trueFrequencyHz": trial["nominalFrequencyHz"],
                        "predictedClass": labels[index], "predictedFrequencyHz": config.target_frequencies_hz[index],
                        "fusedScores": {str(f): score for f, score in zip(config.target_frequencies_hz, fused)},
                        "subBandScores": [{str(f): score for f, score in zip(config.target_frequencies_hz, band)} for band in subband],
                        "scoreMargin": float(max(fused) - sorted(fused)[-2]),
                        "correct": labels[index] == trial["targetId"],
                        "sampleRange": {"startInclusive": epoch.start_sample, "stopExclusive": epoch.stop_sample},
                        "association": {"startEstimatedGlobalSampleIndex": epoch.start_association["estimatedGlobalSampleIndex"],
                                         "stopEstimatedGlobalSampleIndex": epoch.stop_association["estimatedGlobalSampleIndex"],
                                         "segmentId": epoch.start_association["timestampSegmentId"],
                                         "mapping": "software-derived estimate"}})
    evaluation = evaluate_predictions(results, labels)
    artifact = {"recordType": "m6_2b_fbcca_result", "generatedUtc": datetime.now(timezone.utc).isoformat(),
                "session": str(dataset["session"]), "sessionId": dataset["manifest"]["sessionId"],
                "selectedChannels": dataset["selected_channels"], "rawSampleRateHz": dataset["sampling_rate_hz"],
                "decoderSampleRateHz": config.decoder_sampling_rate_hz, "sharedConfig": config.to_dict(),
                "fbccaConfig": fbcca_config.to_dict(), "preprocessing": {"method": "demean_per_channel", "fitOnGroundTruth": False},
                "validEpochCount": len(epochs), "classCounts": dict(Counter(i["trueClass"] for i in results)),
                "overall": {key: evaluation[key] for key in ("correct", "total", "accuracy")},
                "perClass": evaluation["perClass"], "confusionMatrix": evaluation["confusionMatrix"], "trials": results,
                "evidenceBoundary": {"hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                                     "sampleAnchor": "unverified", "association": "software-derived estimate"}}
    if output:
        Path(output).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return artifact
