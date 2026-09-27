"""M19 trigger-driven synthetic, historical and gated live EEG backends.

The three backends share the same request/decision boundary.  Only a class
index crosses back to Quest; this module never resolves a TargetId.
"""

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import time

import numpy as np

from eeg.acquisition.recorded_nd8_replay import RecordedND8Replay
from eeg.decoder.pseudo_online import (
    DEFAULT_PSEUDO_ONLINE_CONFIG,
    DecoderBackend,
    ReplayPacket,
    RollingEegBuffer,
)


CANDIDATE_CHANNELS = (2, 3, 4, 5, 7)
LIVE_CHANNEL_ADMISSION_SAMPLES = 60000
M19_ONSET_GUARD_SAMPLES = int(round(DEFAULT_PSEUDO_ONLINE_CONFIG.onset_guard_seconds * 1000.0))
M19_ANALYSIS_SAMPLES = int(DEFAULT_PSEUDO_ONLINE_CONFIG.analysis_sample_count)


class M19BackendError(RuntimeError):
    """A configured M19 EEG source cannot safely produce a decision."""


class M19LiveConfirmationRequired(M19BackendError):
    """The live backend was asked to open without explicit operator intent."""


class M19SyntheticEegBackend:
    """Deterministic decoder substitute for protocol and queue acceptance."""

    name = "synthetic"

    def __init__(self, decisions=()):
        self._decisions = deque(decisions)
        self.calls = []

    def decode(self, request):
        if not self._decisions:
            raise M19BackendError("synthetic backend has no injected decision")
        value = self._decisions.popleft()
        self.calls.append({
            "trialId": request.trial_id,
            "selectionId": request.selection_id,
            "pageId": request.page_id,
            "pageEpoch": request.page_epoch,
        })
        if isinstance(value, dict):
            value = value.get("classIndex") if value.get("decisionMade", True) else None
        if value is None:
            return {
                "decisionMade": False,
                "classIndex": None,
                "evidence": {"backend": self.name, "injected": "no_decision"},
            }
        if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1, 2):
            raise M19BackendError("synthetic decision must be class 0, 1, 2, or None")
        return {
            "decisionMade": True,
            "classIndex": value,
            "evidence": {"backend": self.name, "injectedClassIndex": value},
        }


class M19HistoricalEegBackend:
    """Replay an immutable recorded onset window through the current FBCCA."""

    name = "historical"

    def __init__(self, session_root, trial_id=None, selected_channels=None, decoder=None):
        self.session_root = Path(session_root)
        self.replay = RecordedND8Replay(self.session_root, speed="max")
        self.trial_id = trial_id
        self.selected_channels = tuple(selected_channels or self._manifest_channels())
        if len(self.selected_channels) < 3 or any(
            isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < 8
            for index in self.selected_channels
        ):
            raise M19BackendError("historical selectedChannels must contain at least three ND8 channels")
        self.decoder = decoder or DecoderBackend("numpy_fbcca", DEFAULT_PSEUDO_ONLINE_CONFIG)
        self._decoded = None
        self._source_trial_id = None
        self._anchor_sample_index = None
        self._window_metadata = None

    def _manifest_channels(self):
        manifest = self.replay.manifest
        config = manifest.get("decoderConfiguration", {})
        channels = config.get("selectedChannels")
        if not isinstance(channels, list) or len(channels) < 3:
            config = manifest.get("configuration", {}).get("decoder", {})
            channels = config.get("selectedChannels")
        if not isinstance(channels, list) or len(channels) < 3:
            channels = list(CANDIDATE_CHANNELS)
        return tuple(int(index) for index in channels)

    def decode(self, request):
        if self._decoded is None:
            self._decoded = self._decode_one_recorded_window()
        return {
            "decisionMade": True,
            "classIndex": self._decoded["classIndex"],
            "confidence": self._decoded["confidence"],
            "evidence": {
                "backend": self.name,
                "decoder": self.decoder.name,
                "historicalTrialId": self._source_trial_id,
                "sourceSessionId": self.replay.manifest.get("sessionId"),
                "sampleAnchorSampleIndex": self._anchor_sample_index,
                "analysisWindowStartSample": self._window_metadata["analysisWindowStartSample"],
                "analysisWindowSampleCount": self._window_metadata["analysisWindowSampleCount"],
                "historicalGroundTruthReadByDecoder": False,
                "currentRequestSelectionId": request.selection_id,
            },
        }

    def _decode_one_recorded_window(self):
        onset_by_trial, anchor_by_trial = self._read_trial_anchors()
        eligible = []
        for identity, onset in onset_by_trial.items():
            anchor = anchor_by_trial.get(identity)
            if anchor is None:
                continue
            # Frequency presence only selects a real SSVEP recording epoch.
            # The archived class/slot is never passed to the decoder or used
            # to map the result into the current Quest page.
            if onset.get("frequencyHz") is None:
                continue
            if self.trial_id is not None and identity != self.trial_id:
                continue
            eligible.append((int(anchor["sampleAnchorSampleIndex"]), identity, anchor))
        if not eligible:
            raise M19BackendError("no recorded SSVEP onset with a valid sample anchor was found")
        anchor_sample, identity, anchor = min(eligible)
        first = anchor_sample + M19_ONSET_GUARD_SAMPLES
        stop = first + M19_ANALYSIS_SAMPLES

        buffer = RollingEegBuffer()
        selected = None
        for packet, continuity in self.replay.iter_packets():
            buffer.append(ReplayPacket(
                samples=np.asarray(packet.samples, dtype=float),
                first_sample=int(continuity.cumulative_first_sample_index),
                packet_sequence=int(packet.packet_sequence),
                logical_time_ns=int(packet.pc_receive_monotonic_ns),
                continuity_status=str(continuity.status),
            ))
            if buffer.stop_sample is not None and buffer.stop_sample >= stop:
                try:
                    selected = buffer.window(first, stop)[list(self.selected_channels)]
                except ValueError as error:
                    raise M19BackendError("recorded trial window crossed a continuity boundary") from error
                break
        if selected is None:
            raise M19BackendError("recorded ND8 stream ended before the frozen analysis window")
        class_index, scores = self.decoder.predict(selected)
        sorted_scores = sorted((float(value) for value in scores), reverse=True)
        confidence = None if len(sorted_scores) < 2 else sorted_scores[0] - sorted_scores[1]
        self._source_trial_id = identity
        self._anchor_sample_index = anchor_sample
        self._window_metadata = {
            "analysisWindowStartSample": first,
            "analysisWindowSampleCount": stop - first,
            "anchorPacketSequence": anchor.get("packetSequence"),
        }
        return {"classIndex": int(class_index), "confidence": confidence, "scores": scores}

    def _read_trial_anchors(self):
        event_name = self.replay.manifest.get("eventFile", "events.jsonl")
        event_path = self.session_root / event_name
        onsets, anchors = {}, {}
        if not event_path.is_file():
            raise M19BackendError("historical event log is missing: {}".format(event_path))
        with event_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = __import__("json").loads(line)
                except ValueError as error:
                    raise M19BackendError("invalid historical event JSON at line {}".format(line_number)) from error
                if not isinstance(event, dict):
                    continue
                identity = event.get("trialId")
                if not identity:
                    continue
                if event.get("eventType") == "STIMULUS_ONSET":
                    onsets[identity] = event
                elif event.get("eventType") == "TRIAL_SAMPLE_ANCHOR":
                    if event.get("sampleAnchorSampleIndex") is not None:
                        anchors[identity] = event
        return onsets, anchors


class M19LiveContinuousEegSource:
    """Raw-first continuous ND8 wrapper over the accepted M13.7 source seam."""

    def __init__(self, com_port, session_root=None):
        from integration.m13_7_golden_session import HumanSessionRecorder
        from integration.m13_7_live_launcher import GoldenPacketPipeline, LiveND8Source

        self.com_port = str(com_port)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.session_root = Path(session_root) if session_root else (
            Path("artifacts") / ("m19_live_nd8_" + stamp)
        )
        session_id = "m19-live-" + stamp.lower().replace("t", "-").replace("z", "")
        self.recorder = HumanSessionRecorder(
            self.session_root,
            session_id,
            "live_nd8",
            configuration={
                "launcher": "integration.m19_paged_live_eeg_demo",
                "samplingRateHz": 1000,
                "channelCount": 8,
                "decoder": {
                    "backend": "numpy_fbcca",
                    "candidateChannels": list(CANDIDATE_CHANNELS),
                    "onsetGuardSeconds": M19_ONSET_GUARD_SAMPLES / 1000.0,
                    "analysisWindowSeconds": M19_ANALYSIS_SAMPLES / 1000.0,
                },
            },
        )
        self.pipeline = GoldenPacketPipeline(selected_channels=CANDIDATE_CHANNELS)
        self.source = LiveND8Source(
            self.com_port,
            self.recorder,
            self.pipeline,
            nominal_sampling_rate_hz=1000.0,
        )
        self._opened = False
        self._closed = False
        self.channel_admission = None

    def open(self):
        if self._opened:
            return
        self.source.open_port()
        self.source.start_streaming()
        self._opened = True

    def admit_channels(self, timeout_seconds=70.0):
        if not self._opened or self._closed:
            raise M19BackendError("continuous ND8 source must be open before channel admission")
        self.pipeline.wait_for_stop_sample(LIVE_CHANNEL_ADMISSION_SAMPLES, timeout_seconds)
        start = int(self.pipeline.buffer.start_sample or 0)
        values = self.pipeline.buffer.window(start, start + LIVE_CHANNEL_ADMISSION_SAMPLES)
        from eeg.decoder.formal_online import channel_admission

        self.channel_admission = channel_admission(
            values,
            self.pipeline.continuity_statuses,
            self.recorder.manifest.get("softwareCommit", "unavailable"),
            datetime.now(timezone.utc).isoformat(),
        )
        self.channel_admission["baselineSamplesPerChannel"] = LIVE_CHANNEL_ADMISSION_SAMPLES
        if self.channel_admission["verdict"] != "READY":
            self.recorder._touch_manifest(channelAdmission=self.channel_admission, selectedChannels=[])
            raise M19BackendError("live ND8 channel admission failed: {}".format(self.channel_admission["verdict"]))
        self.pipeline.selected_channels = tuple(self.channel_admission["selectedChannels"])
        self.recorder._touch_manifest(
            channelAdmission=self.channel_admission,
            selectedChannels=list(self.pipeline.selected_channels),
        )
        return dict(self.channel_admission)

    def decode(self, request, timeout_seconds=10.0):
        if not self._opened or self._closed:
            raise M19BackendError("continuous ND8 source is not running")
        if self.channel_admission is None or self.channel_admission.get("verdict") != "READY":
            raise M19BackendError("live ND8 channel admission must pass before a trial")
        # M19 user-trigger semantics: arm at PC request handling time and use
        # the first ND8 packet delivered to the pipeline after arming.
        # Do not compare different monotonic clock domains.
        self.pipeline.mark_next_segment(None, None)
        anchor = self.pipeline.wait_for_next_segment_anchor(timeout_seconds)
        anchor_sample = int(anchor[1].cumulative_first_sample_index)
        window_start = anchor_sample + M19_ONSET_GUARD_SAMPLES
        window_stop = window_start + M19_ANALYSIS_SAMPLES
        self.pipeline.wait_for_stop_sample(window_stop, timeout_seconds + 3.0)
        prediction = self.pipeline.predict(anchor)
        if prediction is None:
            raise M19BackendError("live ND8 did not provide the frozen guarded analysis window")
        return {
            "decisionMade": True,
            "classIndex": int(prediction["predictedClass"]),
            "confidence": None,
            "evidence": {
                **prediction,
                "backend": "live-nd8",
                "sampleAnchorBasis": "first_packet_received_at_or_after_PC_software_trigger",
                "physicalOpticalTimingVerified": False,
                "hardwareTimingVerified": False,
                "selectedChannels": list(self.pipeline.selected_channels),
                "sourceSessionId": self.recorder.session_id,
            },
        }

    def close(self):
        if self._closed:
            return
        try:
            self.source.stop()
        except RuntimeError:
            pass
        finally:
            self.source.close()
            self._closed = True


class M19LiveNd8Backend:
    """Explicitly gated live backend; construction alone never opens COM."""

    name = "live-nd8"

    def __init__(self, com_port, confirm_live_human=False, source_factory=None, session_root=None):
        self.com_port = str(com_port or "").strip()
        self.confirm_live_human = bool(confirm_live_human)
        self.source_factory = source_factory
        self.session_root = session_root
        self.source = None
        self._closed = False

    def open(self):
        if not self.confirm_live_human:
            raise M19LiveConfirmationRequired(
                "live-nd8 requires --confirm-live-human before a source can be constructed"
            )
        if not self.com_port:
            raise M19BackendError("live-nd8 requires a configured --com port")
        if self.source is not None:
            return self
        factory = self.source_factory or (
            lambda port: M19LiveContinuousEegSource(port, self.session_root)
        )
        self.source = factory(self.com_port)
        self.source.open()
        if callable(getattr(self.source, "admit_channels", None)):
            self.source.admit_channels()
        return self

    def decode(self, request):
        if self.source is None or self._closed:
            raise M19BackendError("live-nd8 source is not open")
        return self.source.decode(request)

    def close(self):
        if self.source is not None and not self._closed:
            self.source.close()
            self._closed = True


def sha256_file(path):
    """Hash a source artifact in chunks without modifying it."""
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "CANDIDATE_CHANNELS", "LIVE_CHANNEL_ADMISSION_SAMPLES", "M19_ANALYSIS_SAMPLES",
    "M19BackendError", "M19HistoricalEegBackend", "M19LiveConfirmationRequired",
    "M19LiveContinuousEegSource", "M19LiveNd8Backend", "M19SyntheticEegBackend",
    "M19_ONSET_GUARD_SAMPLES", "sha256_file",
]
