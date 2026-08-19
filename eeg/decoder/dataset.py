"""Formal M6.1b dataset loading and deterministic epoch extraction."""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import DecoderConfig


@dataclass(frozen=True)
class TrialEpoch:
    trial: dict
    data: np.ndarray
    start_sample: int
    stop_sample: int
    start_association: dict
    stop_association: dict


def _jsonl(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _usable_channels(summary):
    return [item["channelIndex"] for item in summary.get("channels", [])
            if item.get("quality") == "usable"]


def load_dataset(session):
    session = Path(session)
    manifest = json.loads((session / "session-manifest.json").read_text(encoding="utf-8"))
    completeness = json.loads((session / "dataset-completeness.json").read_text(encoding="utf-8"))
    if completeness.get("status") != "complete":
        raise ValueError("dataset completeness status is not complete")
    if completeness.get("classificationPerformed"):
        raise ValueError("dataset already reports classificationPerformed")
    trials = sorted(_jsonl(session / manifest["trialGroundTruthFile"]), key=lambda x: x["trialIndex"])
    associations = _jsonl(session / "derived-association.jsonl")
    by_trial = {}
    for record in associations:
        if not record.get("associationValid"):
            raise ValueError("invalid association for {}".format(record.get("trialId")))
        by_trial.setdefault(record["trialId"], {})[record["stimulusEventType"]] = record
    raw = _jsonl(session / manifest["rawEegFile"])
    if not raw:
        raise ValueError("raw EEG is empty")
    channels = np.concatenate([np.asarray(packet["samples"], dtype=float) for packet in raw], axis=1)
    summary = json.loads((session / "analysis" / "signal-quality-summary.json").read_text(encoding="utf-8"))
    selected = _usable_channels(summary)
    if not selected:
        raise ValueError("no usable channels in signal quality summary")
    return {"session": session, "manifest": manifest, "completeness": completeness,
            "trials": trials, "associations": by_trial, "raw": channels,
            "sampling_rate_hz": float(manifest.get("samplingRateHz", 1000.0)),
            "selected_channels": selected, "quality_summary": summary}


def extract_epochs(dataset, config=None):
    config = config or DecoderConfig()
    if dataset["sampling_rate_hz"] != config.input_sampling_rate_hz:
        raise ValueError("unexpected raw sampling rate")
    epochs = []
    for trial in dataset["trials"]:
        pair = dataset["associations"].get(trial["trialId"], {})
        start = pair.get("stimulus_started_software")
        stop = pair.get("stimulus_stopped_software")
        if not start or not stop or start.get("timestampSegmentId") != stop.get("timestampSegmentId"):
            raise ValueError("missing or inconsistent association for {}".format(trial["trialId"]))
        if start.get("packetContinuityStatus") != "continuous" or stop.get("packetContinuityStatus") not in ("continuous", "anomaly"):
            raise ValueError("invalid continuity status for {}".format(trial["trialId"]))
        start_sample = int(start["estimatedGlobalSampleIndex"]) + config.onset_guard_samples
        stop_sample = start_sample + config.analysis_sample_count
        associated_stop = int(stop["estimatedGlobalSampleIndex"])
        if associated_stop <= start_sample or stop_sample > associated_stop:
            raise ValueError("analysis range is outside stimulation interval for {}".format(trial["trialId"]))
        if start_sample < 0 or stop_sample > dataset["raw"].shape[1]:
            raise ValueError("analysis range out of raw bounds for {}".format(trial["trialId"]))
        data = dataset["raw"][dataset["selected_channels"], start_sample:stop_sample]
        if data.shape != (len(dataset["selected_channels"]), config.analysis_sample_count):
            raise ValueError("unexpected epoch shape for {}".format(trial["trialId"]))
        epochs.append(TrialEpoch(trial, data, start_sample, stop_sample, start, stop))
    return epochs
