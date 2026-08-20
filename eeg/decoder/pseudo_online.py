"""Replay-only, event-locked pseudo-online SSVEP decoding infrastructure.

It intentionally models packet arrival and never exposes samples after a logical
decision point.  It is not a live ND8 runtime and does not establish end-to-end
latency or hardware timing.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .cca import predict, references
from .config import DecoderConfig
from .fbcca import FbccaConfig, predict_fbcca
from .filter_realization import predict_legacy_fbcca
from .pipeline import evaluate_predictions


LABELS = ("target_left", "target_center", "target_right")
# M6.5a frozen first-decision candidate: 0.5 s onset guard + 1.5 s EEG.
DEFAULT_PSEUDO_ONLINE_CONFIG = DecoderConfig(analysis_duration_seconds=1.5)
SLIDING_STEP_SAMPLES = 200


@dataclass(frozen=True)
class ReplayPacket:
    samples: np.ndarray
    first_sample: int
    packet_sequence: int
    logical_time_ns: int
    continuity_status: str


class RollingEegBuffer:
    """One contiguous EEG segment; reset on an incompatible packet boundary."""

    def __init__(self):
        self.clear()

    def clear(self):
        self._data = None
        self.start_sample = None
        self.stop_sample = None
        self.last_sequence = None
        self.segment = 0
        self.reset_reason = None

    def append(self, packet):
        values = np.asarray(packet.samples, dtype=float)
        if values.ndim != 2:
            raise ValueError("packet samples must be channels by samples")
        expected = self.stop_sample
        # A timestamp-delta anomaly is retained as evidence but is not itself a
        # packet/sample discontinuity. True loss/transition statuses reset.
        continuous = (self._data is not None and packet.continuity_status in ("continuous", "anomaly") and
                      packet.first_sample == expected and
                      packet.packet_sequence == self.last_sequence + 1)
        if self._data is None:
            self._data, self.start_sample = values.copy(), packet.first_sample
        elif continuous:
            self._data = np.concatenate((self._data, values), axis=1)
        else:
            self.segment += 1
            self.reset_reason = "continuity_boundary"
            self._data, self.start_sample = values.copy(), packet.first_sample
        self.stop_sample = packet.first_sample + values.shape[1]
        self.last_sequence = packet.packet_sequence

    def window(self, start_sample, stop_sample):
        if self._data is None or start_sample < self.start_sample or stop_sample > self.stop_sample:
            raise ValueError("insufficient_contiguous_history")
        if stop_sample <= start_sample:
            raise ValueError("invalid_window")
        return self._data[:, start_sample - self.start_sample:stop_sample - self.start_sample].copy()


class DecoderBackend:
    """Uniform frozen CCA/FBCCA prediction boundary; labels are not accepted."""

    def __init__(self, name, config=None, fbcca_config=None):
        if name not in ("standard_cca", "numpy_fbcca", "legacy_fbcca"):
            raise ValueError("unknown decoder backend")
        self.name, self.config = name, config or DEFAULT_PSEUDO_ONLINE_CONFIG
        self.fbcca_config = fbcca_config or FbccaConfig()

    def predict(self, epoch):
        epoch = np.asarray(epoch, dtype=float)
        epoch = epoch - epoch.mean(axis=1, keepdims=True)
        if self.name == "standard_cca":
            refs = references(self.config.target_frequencies_hz, self.config.harmonic_count,
                              self.config.decoder_sampling_rate_hz, epoch.shape[1])
            index, scores = predict(epoch, refs)
        elif self.name == "numpy_fbcca":
            index, scores, _ = predict_fbcca(epoch, self.config.target_frequencies_hz,
                                              self.config.harmonic_count,
                                              self.config.decoder_sampling_rate_hz, self.fbcca_config)
        else:
            index, scores, _, _ = predict_legacy_fbcca(epoch, self.config.target_frequencies_hz,
                                                        self.config.harmonic_count,
                                                        self.config.decoder_sampling_rate_hz, self.fbcca_config)
        return int(index), [float(value) for value in scores]


def replay_event_locked(packets, start_events, backend, selected_channels, config=None):
    """Replay one first decision per event after guard + analysis samples arrive."""
    config = config or DEFAULT_PSEUDO_ONLINE_CONFIG
    events = sorted(start_events, key=lambda item: item["eventKnownLogicalNs"])
    packets = sorted(packets, key=lambda item: item.logical_time_ns)
    buffer, pending, decisions, event_index = RollingEegBuffer(), [], [], 0
    for packet in packets:
        while event_index < len(events) and events[event_index]["eventKnownLogicalNs"] <= packet.logical_time_ns:
            pending.append(dict(events[event_index]))
            event_index += 1
        buffer.append(packet)
        remaining = []
        for event in pending:
            first = event["startSample"] + config.onset_guard_samples
            stop = first + config.analysis_sample_count
            if buffer.stop_sample < stop:
                remaining.append(event)
                continue
            try:
                data = buffer.window(first, stop)[selected_channels]
            except ValueError:
                event["bufferStatus"] = "continuity_or_history_rejected"
                remaining.append(event)
                continue
            started = time.perf_counter_ns()
            index, scores = backend.predict(data)
            compute_ns = time.perf_counter_ns() - started
            predicted = LABELS[index]
            decisions.append({
                "sessionId": event["sessionId"], "trialId": event["trialId"],
                "groundTruthLabel": event["groundTruthLabel"], "trueClass": event["groundTruthLabel"], "stimulusStart": {
                    "estimatedGlobalSampleIndex": event["startSample"],
                    "mapping": "software-derived estimate"},
                "decoder": backend.name, "guardSeconds": config.onset_guard_seconds,
                "windowSeconds": config.analysis_duration_seconds,
                "firstEligibleSample": stop, "firstEligibleTimeSeconds":
                    (config.onset_guard_samples + config.analysis_sample_count) / config.input_sampling_rate_hz,
                "actualDecisionLogicalTimeNs": packet.logical_time_ns,
                "samplesAvailableAtDecision": buffer.stop_sample,
                "predictedClass": predicted,
                "predictedFrequencyHz": config.target_frequencies_hz[index],
                "candidateScores": {str(freq): score for freq, score in zip(config.target_frequencies_hz, scores)},
                "correct": predicted == event["groundTruthLabel"],
                "computeDurationNs": compute_ns, "bufferStatus": "contiguous",
                "evidenceQualityFlags": ["software_derived_association", "historical_packet_replay",
                                         "no_future_packet_access", "not_true_online"],
            })
        pending = remaining
    # An event can be received after the final packet already supplied its full
    # window.  It may use that historical buffer, but never samples that arrive
    # after the event; the decision time is the event-known logical time.
    while event_index < len(events):
        pending.append(dict(events[event_index]))
        event_index += 1
    remaining = []
    for event in pending:
        first = event["startSample"] + config.onset_guard_samples
        stop = first + config.analysis_sample_count
        if buffer.stop_sample < stop:
            remaining.append(event)
            continue
        try:
            data = buffer.window(first, stop)[selected_channels]
        except ValueError:
            event["bufferStatus"] = "continuity_or_history_rejected"
            remaining.append(event)
            continue
        started = time.perf_counter_ns()
        index, scores = backend.predict(data)
        compute_ns = time.perf_counter_ns() - started
        predicted = LABELS[index]
        decisions.append({"sessionId": event["sessionId"], "trialId": event["trialId"],
                          "groundTruthLabel": event["groundTruthLabel"], "trueClass": event["groundTruthLabel"],
                          "stimulusStart": {"estimatedGlobalSampleIndex": event["startSample"],
                                            "mapping": "software-derived estimate"},
                          "decoder": backend.name, "guardSeconds": config.onset_guard_seconds,
                          "windowSeconds": config.analysis_duration_seconds, "firstEligibleSample": stop,
                          "firstEligibleTimeSeconds": (config.onset_guard_samples + config.analysis_sample_count) /
                                                      config.input_sampling_rate_hz,
                          "actualDecisionLogicalTimeNs": event["eventKnownLogicalNs"],
                          "samplesAvailableAtDecision": buffer.stop_sample, "predictedClass": predicted,
                          "predictedFrequencyHz": config.target_frequencies_hz[index],
                          "candidateScores": {str(freq): score for freq, score in zip(config.target_frequencies_hz, scores)},
                          "correct": predicted == event["groundTruthLabel"], "computeDurationNs": compute_ns,
                          "bufferStatus": "contiguous",
                          "evidenceQualityFlags": ["software_derived_association", "historical_packet_replay",
                                                   "no_future_packet_access", "not_true_online"]})
    pending = remaining
    return decisions, pending


def _jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_saved_replay(session, association_file, valid_trial_ids=None):
    """Build replay inputs from append-only historical evidence without writes."""
    session = Path(session)
    manifest = json.loads((session / "session-manifest.json").read_text(encoding="utf-8"))
    trials = {item["trialId"]: item for item in _jsonl(session / manifest["trialGroundTruthFile"])}
    associations = _jsonl(association_file)
    valid = set(valid_trial_ids) if valid_trial_ids is not None else set(trials)
    starts = [item for item in associations if item.get("associationValid") and
              item.get("stimulusEventType") == "stimulus_started_software" and item["trialId"] in valid]
    pc_events = _jsonl(session / "pc-stimulus-events.jsonl")
    known = {(item.get("originalQuestEvent", {}).get("trialId"), item.get("originalQuestEvent", {}).get("sequence")):
             item.get("pcReceiveMonotonicNs") for item in pc_events if item.get("recordType") == "stimulus_event_received"}
    events = [{"sessionId": item["sessionId"], "trialId": item["trialId"],
               "groundTruthLabel": trials[item["trialId"]]["targetId"],
               "startSample": int(item["estimatedGlobalSampleIndex"]),
               "eventKnownLogicalNs": int(known.get((item["trialId"], item.get("stimulusSequence")),
                                                     item["associatedPacketPcReceiveMonotonicNs"]))}
              for item in starts]
    raw, metadata = _jsonl(session / manifest["rawEegFile"]), _jsonl(session / "packet-metadata.jsonl")
    if len(raw) != len(metadata):
        raise ValueError("raw/metadata packet count mismatch")
    packets = [ReplayPacket(np.asarray(raw_item["samples"], dtype=float),
                            int(meta["continuity"]["cumulative_first_sample_index"]),
                            int(meta["packet"]["packet_sequence"]),
                            int(meta["packet"]["pc_receive_monotonic_ns"]),
                            meta["continuity"]["status"])
               for raw_item, meta in zip(raw, metadata)]
    return packets, events


def summarize(decisions):
    evaluation = evaluate_predictions(decisions, LABELS)
    durations = np.asarray([item["computeDurationNs"] / 1e6 for item in decisions], dtype=float)
    return {"correct": evaluation["correct"], "total": evaluation["total"], "accuracy": evaluation["accuracy"],
            "perClass": evaluation["perClass"], "confusionMatrix": evaluation["confusionMatrix"],
            "computeMilliseconds": ({"min": float(durations.min()), "median": float(np.median(durations)),
                                     "mean": float(durations.mean()), "p95": float(np.percentile(durations, 95)),
                                     "max": float(durations.max())} if len(durations) else None)}


def stabilize(predictions, required_consecutive):
    """Return the first fixed-consecutive decision; labels never affect timing."""
    if required_consecutive not in (1, 2, 3):
        raise ValueError("required_consecutive must be 1, 2, or 3")
    previous, run = None, 0
    for item in predictions:
        run = run + 1 if item["predictedClass"] == previous else 1
        previous = item["predictedClass"]
        if run >= required_consecutive:
            return {"finalDecisionLabel": previous, "decisionMade": True,
                    "decisionPredictionIndex": item["predictionIndex"],
                    "decisionRelativeTimeSeconds": item["relativeToStimulusStartSeconds"],
                    "correct": previous == item["groundTruthLabel"],
                    "preDecisionPredictionCount": item["predictionIndex"] + 1,
                    "reason": "fixed_consecutive_run"}
    return {"finalDecisionLabel": None, "decisionMade": False, "decisionPredictionIndex": None,
            "decisionRelativeTimeSeconds": None, "correct": False,
            "preDecisionPredictionCount": len(predictions), "reason": "no_sufficient_consecutive_run"}


def replay_continuous(packets, start_events, backend, selected_channels, config=None,
                      stimulation_samples=4000, step_samples=SLIDING_STEP_SAMPLES):
    """Causal 200-sample sliding replay; windows must remain inside stimulation."""
    config = config or DEFAULT_PSEUDO_ONLINE_CONFIG
    if step_samples <= 0:
        raise ValueError("step_samples must be positive")
    packets = sorted(packets, key=lambda item: item.logical_time_ns)
    events = sorted(start_events, key=lambda item: item["eventKnownLogicalNs"])
    buffer, states, event_index = RollingEegBuffer(), [], 0
    for packet in packets:
        while event_index < len(events) and events[event_index]["eventKnownLogicalNs"] <= packet.logical_time_ns:
            event = dict(events[event_index]); event["predictions"] = []
            event["nextStop"] = event["startSample"] + config.onset_guard_samples + config.analysis_sample_count
            states.append(event); event_index += 1
        buffer.append(packet)
        for state in states:
            latest = state["startSample"] + stimulation_samples
            while state["nextStop"] <= latest and buffer.stop_sample >= state["nextStop"]:
                start, stop = state["nextStop"] - config.analysis_sample_count, state["nextStop"]
                try:
                    data = buffer.window(start, stop)[selected_channels]
                except ValueError:
                    state["invalid"] = "continuity_or_history_rejected"; break
                started = time.perf_counter_ns(); index, scores = backend.predict(data)
                state["predictions"].append({"sessionId": state["sessionId"], "trialId": state["trialId"],
                    "groundTruthLabel": state["groundTruthLabel"], "decoder": backend.name,
                    "predictionIndex": len(state["predictions"]), "logicalPredictionTimeNs": packet.logical_time_ns,
                    "relativeToStimulusStartSeconds": (state["nextStop"] - state["startSample"]) / config.input_sampling_rate_hz,
                    "analysisWindowStart": start, "analysisWindowEnd": stop,
                    "candidateScores": {str(f): float(v) for f, v in zip(config.target_frequencies_hz, scores)},
                    "predictedClass": LABELS[index], "computeDurationNs": time.perf_counter_ns() - started,
                    "latestPacketSequence": packet.packet_sequence, "latestSampleAvailable": buffer.stop_sample,
                    "continuityState": "contiguous"})
                state["nextStop"] += step_samples
    trials = []
    for state in states:
        predictions = state["predictions"]
        changes = sum(a["predictedClass"] != b["predictedClass"] for a, b in zip(predictions, predictions[1:]))
        policies = {str(n): stabilize(predictions, n) for n in (1, 2, 3)}
        trials.append({"sessionId": state["sessionId"], "trialId": state["trialId"],
                       "groundTruthLabel": state["groundTruthLabel"], "predictionSequence": predictions,
                       "numberOfLabelChanges": changes, "invalidReason": state.get("invalid"), "policies": policies})
    return trials


def summarize_policies(trials):
    result = {}
    for policy in ("1", "2", "3"):
        values = [trial["policies"][policy] for trial in trials]
        made = [item for item in values if item["decisionMade"]]
        latency = np.asarray([item["decisionRelativeTimeSeconds"] for item in made], dtype=float)
        changes = [trial["numberOfLabelChanges"] for trial in trials]
        result[policy] = {"decisionCoverage": len(made) / len(trials) if trials else None,
                          "decisions": len(made), "validTrials": len(trials),
                          "accuracyAmongDecisions": sum(item["correct"] for item in made) / len(made) if made else None,
                          "correctAllValid": sum(item["correct"] for item in made) / len(trials) if trials else None,
                          "latencySeconds": ({"min": float(latency.min()), "median": float(np.median(latency)),
                              "mean": float(latency.mean()), "p95": float(np.percentile(latency, 95)), "max": float(latency.max())} if len(latency) else None),
                          "stability": {"zeroLabelChanges": sum(value == 0 for value in changes),
                                        "oneOrMoreLabelChanges": sum(value >= 1 for value in changes),
                                        "meanLabelChanges": float(np.mean(changes)) if changes else None}}
    return result
