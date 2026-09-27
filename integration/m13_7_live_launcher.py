"""M13.7 Golden Protocol live/replay session launcher.

The versioned JSON protocol is the authority for block and trial order.  This
module adds a new launcher beside the historical M8 bounded CLI; it does not
change that CLI's 6/9-stage semantics.  The live source is dependency-injected
and never opens COM11 until the explicit LIVE HUMAN confirmation gate passes.
"""

from dataclasses import replace
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Callable, Iterable, Optional

import numpy as np

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.decoder.pseudo_online import DecoderBackend, ReplayPacket, RollingEegBuffer
from eeg.sample_association.models import PacketContinuityRecord
from integration.m12_context_eeg_fusion import fuse_context_and_eeg
from integration.m13_5_runtime import (
    MODE_ACTIVE,
    M135TrialRuntime,
    M135RuntimeError,
    build_default_active_candidates,
)
from integration.m8_selection_orchestration import M8SelectionOrchestrator, QuestSelectionTcpServer
from integration.m13_7_golden_session import (
    AudioCueSystem,
    AudioTimingError,
    HumanSessionRecorder,
    NullLoggingAudioBackend,
    PcToneAudioBackend,
    RAW_EEG_FILENAME,
    SLOT_TO_FREQUENCY_HZ,
    _GoldenQuestTransport,
    _RecorderM135Logger,
    _append_jsonl,
    _context_weights,
    _jsonl_read,
    _atomic_json,
    _git_commit,
    _utc_now,
    validate_golden_protocol,
    verify_session,
    wait_for_timeline_duration,
)
from integration.m13_dynamic_stopping import DynamicStoppingSnapshot
from integration.m13_7_golden_session import _FakeClock
from integration.m13_7_golden_session import RecordedND8Replay
from integration.m13_7_golden_session import GOLDEN_PROTOCOL_VERSION


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL_PATH = REPO_ROOT / "docs" / "protocols" / "M13_7_Golden_Protocol_v1.json"
CHANNEL_QUALITY_FILENAME = "channel-quality.jsonl"
FROZEN_DECODER_CONFIGURATION = {
    "backend": "numpy_fbcca",
    "candidateChannels": [2, 3, 4, 5, 7],
    "selectedChannels": [2, 3, 4, 5, 7],
    "onsetGuardSeconds": 0.5,
    "analysisWindowSeconds": 1.5,
    "stepSeconds": 0.2,
    "m13PolicySource": "existing M13.5 active runtime",
}
LEGACY_PARTIAL_PREPARATION_SECONDS = 3.0
DEFAULT_CONTINUATION_REASON = "reuse_valid_partial_block0_block1_after_timing_hotfix"


class GoldenLauncherError(RuntimeError):
    """A Golden launcher configuration or lifecycle transition is unsafe."""


def validate_windows_com_port(com_port):
    """Validate an operator-selected Windows COM port without opening it."""
    value = str(com_port or "").strip().upper()
    if not re.fullmatch(r"COM[1-9][0-9]*", value):
        raise GoldenLauncherError("COM port must be an explicit Windows name such as COM3")
    return value


class _RealClock:
    monotonic_ns = staticmethod(time.perf_counter_ns)
    sleep = staticmethod(time.sleep)
    utc_now = staticmethod(_utc_now)


class _CompressedPreflightClock(_RealClock):
    """Use real time for audio while keeping synthetic preflight short.

    The one-second cue-to-stimulus silence and the 700 ms target-group
    separation are never compressed. Other protocol waits are shortened
    because this is a representative software preflight, not a timing or EEG
    acceptance run.
    """

    @staticmethod
    def sleep(seconds):
        seconds = float(seconds)
        if seconds >= 0.7 - 1e-9:
            time.sleep(seconds)
        else:
            time.sleep(max(0.0, seconds * 0.1))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_provenance():
    def run(*args):
        try:
            return subprocess.check_output(["git", *args], cwd=str(REPO_ROOT), text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return "unavailable"

    status = run("status", "--porcelain=v1")
    return {
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "workingTreeDirty": bool(status),
        "workingTreeStatusPorcelain": status.splitlines() if status else [],
    }


def load_golden_protocol(path) -> dict:
    """Load and validate the protocol; trial order is never rebuilt here."""
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GoldenLauncherError("cannot load Golden protocol {}: {}".format(path, error)) from error
    try:
        validate_golden_protocol(value)
    except (TypeError, ValueError, KeyError) as error:
        raise GoldenLauncherError("Golden protocol validation failed: {}".format(error)) from error
    if value.get("protocolVersion") != GOLDEN_PROTOCOL_VERSION:
        raise GoldenLauncherError("unsupported protocol version: {}".format(value.get("protocolVersion")))
    if len(value.get("trials", [])) != len(value.get("generatedOrder", [])):
        raise GoldenLauncherError("generatedOrder and trials have different lengths")
    if [item.get("trialId") for item in value["trials"]] != list(value["generatedOrder"]):
        raise GoldenLauncherError("protocol generatedOrder does not match trial records")
    return value


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _new_session_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "{}-{}".format(prefix, stamp)


def build_execution_slice(protocol: dict, start_block_index: int = 0) -> dict:
    """Return the protocol-defined block slice and its exact projected duration.

    Duration is summed from the versioned protocol's per-block breakdown.  It is
    deliberately not estimated from an average trial, and it does not mutate
    the canonical 147-trial order.
    """
    blocks = list(protocol.get("blocks", []))
    try:
        start = int(start_block_index)
    except (TypeError, ValueError) as error:
        raise GoldenLauncherError("start_block_index must be an integer") from error
    if start < 0 or start >= len(blocks):
        raise GoldenLauncherError(
            "start_block_index must be between 0 and {}".format(max(0, len(blocks) - 1))
        )

    duration_rows = {
        row["block"]: row for row in protocol.get("durationBreakdown", {}).get("blocks", [])
    }
    specs_by_block = {}
    for spec in protocol.get("trials", []):
        specs_by_block.setdefault(spec["block"], []).append(spec)
    selected_blocks = blocks[start:]
    selected_ids = [block["block"] for block in selected_blocks]
    selected_rows = [duration_rows[block_id] for block_id in selected_ids]
    execution_seconds = round(sum(float(row["totalSeconds"]) for row in selected_rows), 3)
    selected_specs = [spec for block_id in selected_ids for spec in specs_by_block.get(block_id, [])]
    return {
        "startBlockIndex": start,
        "executedBlockIndices": list(range(start, len(blocks))),
        "skippedBlockIndices": list(range(0, start)),
        "executedBlockIds": selected_ids,
        "plannedSegments": len(selected_specs),
        "protocolTotalSegments": len(protocol.get("trials", [])),
        "protocolProjectedDurationSeconds": float(protocol["projectedDurationSeconds"]),
        "protocolProjectedDurationMinutes": float(protocol["projectedDurationMinutes"]),
        "executionProjectedDurationSeconds": execution_seconds,
        "executionProjectedDurationMinutes": round(execution_seconds / 60.0, 2),
        "durationBreakdown": {
            "blocks": selected_rows,
            "projectedDurationSeconds": execution_seconds,
            "projectedDurationMinutes": round(execution_seconds / 60.0, 2),
        },
        "trialIds": [spec["trialId"] for spec in selected_specs],
    }


def _resume_offsets(root: Path):
    raw_path = root / RAW_EEG_FILENAME
    if not raw_path.is_file():
        return 0, 0
    records = _jsonl_read(raw_path)
    if not records:
        return 0, 0
    last = records[-1]
    sequence = int(last.get("packetSequence", last.get("packet_sequence", -1)))
    first_sample = int(last.get("sourceSampleIndex", 0))
    sample_count = int(last.get("sampleCountPerChannel", len(last.get("samples", [[]])[0])))
    return sequence + 1, first_sample + sample_count


class GoldenPacketPipeline:
    """Common packet-to-buffer/decoder seam for live and replay-like sources."""

    def __init__(self, backend_name="numpy_fbcca", selected_channels=None):
        self.backend = DecoderBackend(backend_name)
        self.selected_channels = tuple(FROZEN_DECODER_CONFIGURATION["selectedChannels"] if selected_channels is None else selected_channels)
        self.buffer = RollingEegBuffer()
        self.packet_count = 0
        self.observed_sample_count = 0
        self.last_packet = None
        self.continuity_statuses = []
        self._next_segment_anchor = None
        self._next_segment_anchor_metadata = {}
        self._anchor_capture_armed = False
        self._condition = threading.Condition()

    @property
    def stop_sample(self):
        return self.buffer.stop_sample

    def mark_next_segment(self, stimulus_onset_monotonic_ns=None, stimulus_onset_event_sequence=None):
        """Arm the first post-onset packet as the EEG sample anchor.

        The packet timeline is continuous, so a stimulus event can fall between
        packet boundaries.  We retain the raw stream and conservatively anchor
        t=0 at the first packet observed at or after the actual
        ``STIMULUS_ONSET`` clock event.  No target-cue or preparation offset is
        used for this association.
        """
        with self._condition:
            self._next_segment_anchor = None
            self._next_segment_anchor_metadata = {
                "stimulusOnsetMonotonicNs": None if stimulus_onset_monotonic_ns is None else int(stimulus_onset_monotonic_ns),
                "stimulusOnsetEventSequence": None if stimulus_onset_event_sequence is None else int(stimulus_onset_event_sequence),
                "anchorBasis": "first_packet_received_at_or_after_stimulus_onset",
            }
            self._anchor_capture_armed = True

    @property
    def next_segment_anchor(self):
        with self._condition:
            return self._next_segment_anchor

    @property
    def next_segment_anchor_metadata(self):
        with self._condition:
            return dict(self._next_segment_anchor_metadata)

    def __call__(self, packet: Nd8Packet, continuity: PacketContinuityRecord):
        replay_packet = ReplayPacket(
            np.asarray(packet.samples, dtype=float),
            continuity.cumulative_first_sample_index,
            packet.packet_sequence,
            packet.pc_receive_monotonic_ns,
            continuity.status,
        )
        with self._condition:
            self.buffer.append(replay_packet)
            self.packet_count += 1
            self.observed_sample_count += packet.sample_count * packet.channel_count
            self.last_packet = (packet, continuity)
            self.continuity_statuses.append(continuity.status)
            onset_ns = self._next_segment_anchor_metadata.get("stimulusOnsetMonotonicNs")
            eligible = onset_ns is None or packet.pc_receive_monotonic_ns >= int(onset_ns)
            if self._anchor_capture_armed and eligible:
                self._next_segment_anchor = (packet, continuity)
                self._next_segment_anchor_metadata.update(
                    {
                        "anchorPacketSequence": int(packet.packet_sequence),
                        "sampleAnchorSampleIndex": int(continuity.cumulative_first_sample_index),
                        "anchorPacketReceiveMonotonicNs": int(packet.pc_receive_monotonic_ns),
                        "anchorPacketSampleCount": int(packet.sample_count),
                    }
                )
                self._anchor_capture_armed = False
            self._condition.notify_all()

    def wait_for_next_segment_anchor(self, timeout_seconds: float):
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while self._next_segment_anchor is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GoldenLauncherError("live ND8 stream did not provide a packet after STIMULUS_ONSET")
                self._condition.wait(min(0.05, remaining))
            return self._next_segment_anchor

    def wait_for_stop_sample(self, stop_sample: int, timeout_seconds: float):
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while self.buffer.stop_sample is None or self.buffer.stop_sample < int(stop_sample):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GoldenLauncherError("live ND8 stream did not provide the required sample window")
                self._condition.wait(min(0.05, remaining))

    def predict(self, anchor):
        if isinstance(anchor, tuple):
            start_sample = int(anchor[1].cumulative_first_sample_index)
        else:
            start_sample = int(anchor)
        first = int(start_sample) + int(round(FROZEN_DECODER_CONFIGURATION["onsetGuardSeconds"] * 1000.0))
        stop = first + int(round(FROZEN_DECODER_CONFIGURATION["analysisWindowSeconds"] * 1000.0))
        with self._condition:
            if self.buffer.stop_sample is None or self.buffer.stop_sample < stop:
                return None
            data = self.buffer.window(first, stop)[list(self.selected_channels)]
        started = time.perf_counter_ns()
        index, scores = self.backend.predict(data)
        return {
            "predictedClass": int(index),
            "candidateScores": [float(value) for value in scores],
            "firstEligibleSample": stop,
            "analysisWindowStartSample": first,
            "sampleAnchorSampleIndex": int(start_sample),
            "analysisWindowSampleCount": int(stop - first),
            "stimulusOnsetEventSequence": self.next_segment_anchor_metadata.get("stimulusOnsetEventSequence"),
            "samplesAvailableAtDecision": int(self.buffer.stop_sample),
            "computeDurationNs": time.perf_counter_ns() - started,
            "decoder": self.backend.name,
            "downstreamSampleCount": self.observed_sample_count,
        }

    def predict_windows(self, anchor):
        """Return the frozen M13 0.5 s guard + 1.5 s windows on one anchor."""
        if isinstance(anchor, tuple):
            start_sample = int(anchor[1].cumulative_first_sample_index)
        else:
            start_sample = int(anchor)
        windows = []
        for window_index in range(11):
            first = start_sample + 500 + 200 * window_index
            stop = first + 1500
            with self._condition:
                if self.buffer.stop_sample is None or self.buffer.stop_sample < stop:
                    raise GoldenLauncherError("live ND8 stream did not provide the frozen M13 window")
                data = self.buffer.window(first, stop)[list(self.selected_channels)]
            started = time.perf_counter_ns()
            index, scores = self.backend.predict(data)
            windows.append({
                "windowIndex": window_index,
                "effectiveAcquisitionSeconds": (2000 + 200 * window_index) / 1000.0,
                "analysisSampleRangeRelativeToStimulusOnset": [500 + 200 * window_index, 2000 + 200 * window_index],
                "rawScoresBySlot": [float(value) for value in scores],
                "predictedSlotIndex": int(index),
                "sampleAnchorSampleIndex": int(start_sample),
                "analysisWindowStartSample": int(first),
                "analysisWindowSampleCount": int(stop - first),
                "stimulusOnsetEventSequence": self.next_segment_anchor_metadata.get("stimulusOnsetEventSequence"),
                "samplesAvailableAtDecision": int(self.buffer.stop_sample),
                "computeDurationNs": time.perf_counter_ns() - started,
                "decoder": self.backend.name,
            })
        return windows


class _RecorderThenDecoderSink:
    """Persist raw evidence first, then expose it to the decoder pipeline."""

    def __init__(self, recorder: HumanSessionRecorder, downstream: GoldenPacketPipeline):
        self.recorder = recorder
        self.downstream = downstream

    def __call__(self, packet: Nd8Packet, continuity: PacketContinuityRecord):
        self.recorder.record_packet(packet, continuity, experiment_monotonic_ns=packet.pc_receive_monotonic_ns)
        self.downstream(packet, continuity)


class LiveND8Source:
    """Live ND8 source with raw-first recorder wiring and explicit lifecycle."""

    source_type = "live_nd8"

    def __init__(
        self,
        com_port: str,
        recorder: HumanSessionRecorder,
        downstream: GoldenPacketPipeline,
        nominal_sampling_rate_hz: float = 1000.0,
        packet_sequence_offset: int = 0,
        sample_index_offset: int = 0,
        adapter_factory=Nd8SerialAdapter,
        adapter_kwargs=None,
    ):
        self.com_port = validate_windows_com_port(com_port)
        self.recorder = recorder
        self.downstream = downstream
        self.packet_sequence_offset = int(packet_sequence_offset)
        self.sample_index_offset = int(sample_index_offset)
        self.nominal_sampling_rate_hz = float(nominal_sampling_rate_hz)
        self._first_callback = True
        self._sink = _RecorderThenDecoderSink(recorder, downstream)
        kwargs = dict(adapter_kwargs or {})
        kwargs["live_packet_observer"] = self._on_adapter_packet
        self.adapter = adapter_factory(
            com_port,
            nominal_sampling_rate_hz=float(nominal_sampling_rate_hz),
            **kwargs,
        )

    def _on_adapter_packet(self, packet: Nd8Packet, continuity: PacketContinuityRecord):
        if self.packet_sequence_offset or self.sample_index_offset:
            adjusted_packet_sequence = packet.packet_sequence + self.packet_sequence_offset
            adjusted_first_sample = continuity.cumulative_first_sample_index + self.sample_index_offset
            adjusted_status = "resume_boundary" if self._first_callback else continuity.status
            adjusted_issues = tuple(continuity.issues) + (("resume_boundary",) if self._first_callback else ())
            packet = replace(packet, packet_sequence=adjusted_packet_sequence)
            continuity = PacketContinuityRecord(
                adjusted_packet_sequence,
                adjusted_first_sample,
                adjusted_status,
                adjusted_issues,
            )
        self._first_callback = False
        self._sink(packet, continuity)

    def open_port(self):
        self.adapter.open_port()
        boundary = dict(self.recorder.manifest.get("hardwareBoundary", {}))
        boundary["comPortOpened"] = True
        boundary["openedComPort"] = self.com_port
        boundary["com11Opened"] = self.com_port == "COM11"
        self.recorder._touch_manifest(
            hardwareBoundary=boundary,
            liveSourceState={"port": self.com_port, "portState": self.adapter.state.value, "hostMacReady": self.adapter.host_mac_ready},
        )

    def start_streaming(self):
        self.adapter.start_streaming()
        boundary = dict(self.recorder.manifest.get("hardwareBoundary", {}))
        boundary["nd8Operated"] = True
        self.recorder._touch_manifest(
            hardwareBoundary=boundary,
            liveSourceState={"port": self.com_port, "portState": self.adapter.state.value, "hostMacReady": self.adapter.host_mac_ready},
        )

    def acquire_baseline(self, duration_seconds: float):
        deadline = time.monotonic() + float(duration_seconds)
        while time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def acquire_trial(self, spec: dict, duration_seconds: float):
        anchor = self.downstream.wait_for_next_segment_anchor(float(duration_seconds) + 5.0)
        start = int(anchor[1].cumulative_first_sample_index)
        expected = start + int(round(float(duration_seconds) * self.nominal_sampling_rate_hz))
        self.downstream.wait_for_stop_sample(expected, float(duration_seconds) + 5.0)

    def stop(self):
        if self.adapter.state.value == "streaming":
            self.adapter.stop()

    def close(self):
        self.adapter.close()


class SyntheticND8Source:
    """Deterministic source used by full simulation and preflight tests."""

    source_type = "synthetic_nd8_replay"

    def __init__(
        self,
        sink: Callable[[Nd8Packet, PacketContinuityRecord], None],
        clock,
        sampling_rate_hz: float = 1000.0,
        packet_samples: int = 2000,
        packet_sequence_offset: int = 0,
        sample_index_offset: int = 0,
    ):
        self.sink = sink
        self.clock = clock
        self.sampling_rate_hz = float(sampling_rate_hz)
        self.packet_samples = int(packet_samples)
        self.packet_sequence = int(packet_sequence_offset)
        self.next_sample = int(sample_index_offset)
        self._resume_sequence_start = self.packet_sequence
        self._resumed = bool(self.packet_sequence or self.next_sample)
        self._first = self.packet_sequence == 0 and self.next_sample == 0

    def _emit(self, slot=None):
        sample_count = self.packet_samples
        t = np.arange(sample_count, dtype=float) / self.sampling_rate_hz
        if slot is None:
            signal = np.zeros(sample_count, dtype=float)
        else:
            signal = np.sin(2.0 * np.pi * SLOT_TO_FREQUENCY_HZ[int(slot)] * t)
        values = np.vstack([signal * (1.0 - 0.03 * channel) for channel in range(8)])
        packet = Nd8Packet.from_sdk_payload(
            {
                "timestamp": self.next_sample / self.sampling_rate_hz * 1000.0,
                "data": values.tolist(),
            },
            packet_sequence=self.packet_sequence,
            nominal_sampling_rate_hz=self.sampling_rate_hz,
            receive_monotonic_ns=self.clock.monotonic_ns(),
            receive_utc=self.clock.utc_now(),
        )
        status = "initial" if self._first else ("resume_boundary" if self._resumed and self.packet_sequence == self._resume_sequence_start else "continuous")
        issues = ("resume_boundary",) if status == "resume_boundary" else ()
        continuity = PacketContinuityRecord(self.packet_sequence, self.next_sample, status, issues)
        self.sink(packet, continuity)
        self._first = False
        self.packet_sequence += 1
        self.next_sample += sample_count
        return packet, continuity

    def acquire_baseline(self, duration_seconds: float):
        self._emit(None)
        self.clock.sleep(float(duration_seconds))

    def acquire_trial(self, spec: dict, duration_seconds: float):
        result = self._emit(spec.get("slot"))
        self.clock.sleep(float(duration_seconds))
        return result

    def stop(self):
        return None

    def close(self):
        return None


class GoldenLiveSessionLauncher:
    """Run, pause, resume, abort, or preflight one protocol-defined session."""

    def __init__(
        self,
        protocol_path,
        data_root,
        mode="simulation",
        source_mode=None,
        com_port="COM11",
        session_id=None,
        resume=False,
        confirm_live_human=False,
        clock=None,
        packet_samples=2000,
        adapter_factory=Nd8SerialAdapter,
        source_factory=None,
        audio_mode=None,
        quest_mode="none",
        quest_host="0.0.0.0",
        quest_port=11001,
        quest_accept_timeout_seconds=30.0,
        quest_ack_timeout_seconds=5.0,
        recorder_source_type=None,
        source_metadata=None,
        start_block_index=0,
        continuation_from_session_id=None,
        continuation_reason=None,
    ):
        if mode not in ("simulation", "preflight", "live"):
            raise ValueError("mode must be simulation, preflight, or live")
        self.protocol_path = Path(protocol_path).resolve()
        self.protocol = load_golden_protocol(self.protocol_path)
        self.protocol_hash = _sha256(self.protocol_path)
        self.data_root = Path(data_root)
        self.mode = mode
        self.source_mode = source_mode or ("live-nd8" if mode == "live" else "synthetic")
        self.com_port = com_port
        self.session_id = session_id or _new_session_id("m13.7-{}".format("preflight" if mode == "preflight" else "golden-live"))
        self.execution_slice = build_execution_slice(self.protocol, start_block_index)
        self.start_block_index = self.execution_slice["startBlockIndex"]
        self.continuation_from_session_id = None if continuation_from_session_id is None else str(continuation_from_session_id)
        self.continuation_reason = continuation_reason or DEFAULT_CONTINUATION_REASON
        if self.start_block_index > 0:
            if self.mode == "preflight":
                raise GoldenLauncherError("start_block_index is only valid for full protocol execution")
            if not self.continuation_from_session_id:
                raise GoldenLauncherError("a continuation source session ID is required when start_block_index > 0")
            if self.continuation_from_session_id == self.session_id:
                raise GoldenLauncherError("continuation must write to a new session ID")
        self.resume = bool(resume)
        self.confirm_live_human = bool(confirm_live_human)
        self.clock = clock or _RealClock()
        self.packet_samples = int(packet_samples)
        self.adapter_factory = adapter_factory
        self.source_factory = source_factory
        normalized_quest_mode = str(quest_mode).replace("-", "_")
        if normalized_quest_mode not in ("none", "m8_tcp"):
            raise ValueError("quest_mode must be none or m8_tcp")
        if normalized_quest_mode != "none" and mode not in ("preflight", "live"):
            raise GoldenLauncherError("Quest M8 TCP integration is available only in PREFLIGHT or LIVE mode")
        if not str(quest_host).strip():
            raise ValueError("quest_host must not be empty")
        if not 0 <= int(quest_port) <= 65535:
            raise ValueError("quest_port must be between 0 and 65535")
        self.quest_mode = normalized_quest_mode
        self.quest_host = str(quest_host)
        self.quest_port = int(quest_port)
        self.quest_accept_timeout_seconds = float(quest_accept_timeout_seconds)
        self.quest_ack_timeout_seconds = float(quest_ack_timeout_seconds)
        if self.quest_accept_timeout_seconds <= 0 or self.quest_ack_timeout_seconds <= 0:
            raise ValueError("Quest transport timeouts must be positive")
        self.recorder_source_type = recorder_source_type
        self.source_metadata = dict(source_metadata or {})
        requested_audio_mode = audio_mode
        if requested_audio_mode is None:
            requested_audio_mode = "pc" if self.source_mode == "live-nd8" else "silent"
        if requested_audio_mode not in ("silent", "pc"):
            raise ValueError("audio_mode must be silent or pc")
        if self.mode == "simulation" and requested_audio_mode != "silent":
            raise GoldenLauncherError("simulation mode is speaker-independent and must use silent audio")
        if self.source_mode == "live-nd8" and requested_audio_mode != "pc":
            raise GoldenLauncherError("live-human and live-nd8 preflight require PC audio; silent fallback is forbidden")
        self.audio_mode = requested_audio_mode
        self.audio_backend_name = "pc_winsound" if self.audio_mode == "pc" else "null_logging"
        self.audio_timing_mode = (
            "compressed_preflight"
            if self.mode == "preflight" and self.source_mode == "synthetic" and self.audio_mode == "pc"
            else "protocol"
        )
        self.execution_timing_mode = (
            "compressed_preflight"
            if self.mode == "preflight" and self.source_mode == "synthetic"
            else "protocol"
        )
        self.recorder = None
        self.pipeline = None
        self.source = None
        self.cue = None
        self._quest = None
        self._quest_transport = None
        self.current_trial = None
        self.completed_blocks = []
        self._candidates = build_default_active_candidates()
        self._block_indices = {block["block"]: index for index, block in enumerate(self.protocol["blocks"])}

        if self.mode == "live" and self.source_mode != "live-nd8":
            raise ValueError("live mode requires source_mode=live-nd8")
        if self.source_mode == "live-nd8" and _is_within(self.data_root, REPO_ROOT):
            raise GoldenLauncherError("live/preflight ND8 output must be outside the repository")

    @property
    def session_root(self):
        return self.data_root / self.session_id

    def _next_block_index(self):
        completed_indices = [
            self._block_indices[block_id]
            for block_id in self.completed_blocks
            if block_id in self._block_indices
        ]
        return max(completed_indices, default=self.start_block_index - 1) + 1

    def _create_recorder(self, session_category=None):
        source_type = self.recorder_source_type or (
            "live_nd8" if self.source_mode == "live-nd8" else "synthetic_nd8_replay"
        )
        provenance = _git_provenance()
        timing = dict(self.protocol["timing"])
        quest_configuration = {
            "required": self.quest_mode == "m8_tcp",
            "mode": "m8_selection_tcp" if self.quest_mode == "m8_tcp" else "disabled",
            "host": self.quest_host if self.quest_mode == "m8_tcp" else None,
            "port": self.quest_port if self.quest_mode == "m8_tcp" else None,
            "expectedSelectionTrialIds": [],
        }
        recorder = HumanSessionRecorder(
            self.session_root,
            self.session_id,
            source_type,
            protocol_version=self.protocol["protocolVersion"],
            software_commit=provenance["commit"],
            configuration={
                "launcher": "integration.m13_7_live_launcher",
                "decoder": dict(FROZEN_DECODER_CONFIGURATION),
                "cueTiming": timing,
                "audio": {
                    "mode": self.audio_mode,
                    "backend": self.audio_backend_name,
                    "timingMode": self.audio_timing_mode,
                    "failurePolicy": "fail_fast" if self.audio_mode == "pc" else "record_warning",
                },
                "executionTimingMode": self.execution_timing_mode,
                "formalProtocolTiming": timing,
                "durationBreakdown": self.protocol.get("durationBreakdown"),
                "quest": quest_configuration,
                "sourceMetadata": dict(self.source_metadata),
                "protocolAuthority": str(self.protocol_path),
                "protocolSha256": self.protocol_hash,
                "startBlockIndex": self.start_block_index,
                "executedBlockIndices": list(self.execution_slice["executedBlockIndices"]),
                "skippedBlockIndices": list(self.execution_slice["skippedBlockIndices"]),
                "executionProjectedDurationSeconds": self.execution_slice["executionProjectedDurationSeconds"],
                "executionProjectedDurationMinutes": self.execution_slice["executionProjectedDurationMinutes"],
            },
            sampling_rate_hz=float(self.protocol["channelQuality"]["samplingRateHz"]),
            channel_count=8,
            resume=self.resume,
            monotonic_ns=self.clock.monotonic_ns,
            utc_now=self.clock.utc_now,
        )
        if self.resume and hasattr(self.clock, "ns"):
            existing_events = _jsonl_read(self.session_root / "events.jsonl")
            last_event_ns = max((int(item.get("monotonicNs", 0)) for item in existing_events), default=0)
            self.clock.ns = max(int(self.clock.ns), last_event_ns + 1)
        existing_hash = recorder.manifest.get("protocolProvenance", {}).get("sha256")
        if self.resume and existing_hash and existing_hash != self.protocol_hash:
            raise GoldenLauncherError("resume protocol hash does not match the frozen session manifest")
        category = session_category or (
            "GOLDEN_CONTINUATION" if self.start_block_index > 0
            else ("PREFLIGHT" if self.mode == "preflight" else "GOLDEN")
        )
        mode_label = "LIVE HUMAN" if self.mode == "live" else ("PREFLIGHT" if self.mode == "preflight" else "SIMULATION")
        continuation_values = {
            "startBlockIndex": self.start_block_index,
            "executedBlockIndices": list(self.execution_slice["executedBlockIndices"]),
            "skippedBlockIndices": list(self.execution_slice["skippedBlockIndices"]),
            "executionBlockIds": list(self.execution_slice["executedBlockIds"]),
            "protocolTotalSegments": self.execution_slice["protocolTotalSegments"],
            "plannedSegments": self.execution_slice["plannedSegments"],
            "executionProjectedDurationSeconds": self.execution_slice["executionProjectedDurationSeconds"],
            "executionProjectedDurationMinutes": self.execution_slice["executionProjectedDurationMinutes"],
            "executionDurationBreakdown": self.execution_slice["durationBreakdown"],
            "neverMergeRawSessions": True,
        }
        if self.start_block_index > 0:
            continuation_values.update(
                {
                    "continuationFromSessionId": self.continuation_from_session_id,
                    "continuationReason": self.continuation_reason,
                    "continuationFromTiming": {
                        "preparationSeconds": LEGACY_PARTIAL_PREPARATION_SECONDS,
                        "description": "legacy pre-hotfix partial session timing",
                    },
                }
            )
        recorder._touch_manifest(
            sessionCategory=category,
            mode=mode_label,
            launcherVersion="m13.7-golden-live-launcher-v1",
            protocolProvenance={
                "path": str(self.protocol_path),
                "sha256": self.protocol_hash,
                "protocolVersion": self.protocol["protocolVersion"],
                "generatedTrialOrder": list(self.protocol["generatedOrder"]),
            },
            generatedTrialOrder=list(self.protocol["generatedOrder"]),
            decoderConfiguration=dict(FROZEN_DECODER_CONFIGURATION),
            audioConfiguration={
                "mode": self.audio_mode,
                "backend": self.audio_backend_name,
                "timingMode": self.audio_timing_mode,
                "failurePolicy": "fail_fast" if self.audio_mode == "pc" else "record_warning",
            },
            executionTimingMode=self.execution_timing_mode,
            formalProtocolTiming=timing,
            projectedDurationSeconds=self.protocol["projectedDurationSeconds"],
            projectedDurationMinutes=self.protocol["projectedDurationMinutes"],
            durationBreakdown=self.protocol.get("durationBreakdown"),
            questIntegration=quest_configuration,
            sourceMetadata=dict(self.source_metadata),
            cueConfiguration={"timing": timing, "slotToFrequencyHz": {str(k): v for k, v in SLOT_TO_FREQUENCY_HZ.items()}},
            nd8SourceConfiguration={
                "sourceType": source_type,
                "comPort": self.com_port if source_type == "live_nd8" else None,
                "samplingRateHz": float(self.protocol["channelQuality"]["samplingRateHz"]),
                "channelCount": 8,
                "rawPreservationBoundary": "before_decoder_downstream_callback",
            },
            gitProvenance=provenance,
            resumePolicy=self.protocol["restAndResume"],
            **continuation_values,
        )
        self.recorder = recorder
        quality_path = self.session_root / CHANNEL_QUALITY_FILENAME
        quality_path.touch(exist_ok=True)
        manifest_files = dict(recorder.manifest.get("files", {}))
        manifest_files["channelQualityFile"] = CHANNEL_QUALITY_FILENAME
        recorder._touch_manifest(
            channelQualityFile=CHANNEL_QUALITY_FILENAME,
            files=manifest_files,
        )
        self.completed_blocks = list(recorder.manifest.get("completedBlocks", []))
        return recorder

    def startup_summary(self):
        if self.recorder is None:
            self._create_recorder()
        provenance = self.recorder.manifest.get("gitProvenance", {})
        return {
            "status": "CONFIRMATION_REQUIRED" if self.source_mode == "live-nd8" and not self.confirm_live_human else "READY",
            "mode": "LIVE HUMAN" if self.mode == "live" else ("PREFLIGHT" if self.mode == "preflight" else "SIMULATION"),
            "protocolVersion": self.protocol["protocolVersion"],
            "segments": self.execution_slice["plannedSegments"],
            "protocolTotalSegments": self.execution_slice["protocolTotalSegments"],
            "projectedDurationSeconds": self.execution_slice["executionProjectedDurationSeconds"],
            "projectedDurationMinutes": self.execution_slice["executionProjectedDurationMinutes"],
            "startBlockIndex": self.start_block_index,
            "executedBlockIndices": list(self.execution_slice["executedBlockIndices"]),
            "skippedBlockIndices": list(self.execution_slice["skippedBlockIndices"]),
            "comPort": self.com_port if self.mode == "live" or self.source_mode == "live-nd8" else None,
            "sessionOutputDirectory": str(self.session_root),
            "sessionId": self.session_id,
            "protocolSha256": self.protocol_hash,
            "audioMode": self.audio_mode,
            "audioBackend": self.audio_backend_name,
            "audioTimingMode": self.audio_timing_mode,
            "executionTimingMode": self.execution_timing_mode,
            "questMode": self.quest_mode,
            "questHost": self.quest_host if self.quest_mode == "m8_tcp" else None,
            "questPort": self.quest_port if self.quest_mode == "m8_tcp" else None,
            "gitCommit": provenance.get("commit"),
            "workingTreeDirty": provenance.get("workingTreeDirty"),
            "generatedOrderCount": len(self.protocol["generatedOrder"]),
            "warning": "LIVE HUMAN confirmation is required before opening COM11" if self.source_mode == "live-nd8" and not self.confirm_live_human else None,
        }

    def _build_components(self):
        self.pipeline = GoldenPacketPipeline()
        sequence_offset, sample_offset = _resume_offsets(self.session_root) if self.resume else (0, 0)
        sink = _RecorderThenDecoderSink(self.recorder, self.pipeline)
        if self.source_mode == "live-nd8":
            if self.source_factory is not None:
                self.source = self.source_factory(sink, self)
            else:
                self.source = LiveND8Source(
                    self.com_port,
                    self.recorder,
                    self.pipeline,
                    nominal_sampling_rate_hz=float(self.protocol["channelQuality"]["samplingRateHz"]),
                    packet_sequence_offset=sequence_offset,
                    sample_index_offset=sample_offset,
                    adapter_factory=self.adapter_factory,
                )
        else:
            if self.source_factory is not None:
                self.source = self.source_factory(sink, self)
            else:
                self.source = SyntheticND8Source(
                    sink,
                    self.clock,
                    sampling_rate_hz=float(self.protocol["channelQuality"]["samplingRateHz"]),
                    packet_samples=self.packet_samples,
                    packet_sequence_offset=sequence_offset,
                    sample_index_offset=sample_offset,
                )
        if self.audio_mode == "pc":
            try:
                audio_backend = PcToneAudioBackend()
            except Exception as error:
                raise GoldenLauncherError("PC audio backend is unavailable; live audio cannot fall back to Null: {}".format(error)) from error
        else:
            audio_backend = NullLoggingAudioBackend(self.clock.monotonic_ns)
        self.cue = AudioCueSystem(
            audio_backend,
            self.recorder.record_event,
            monotonic_ns=self.clock.monotonic_ns,
            utc_now=self.clock.utc_now,
            sleep=self.clock.sleep,
            required_silence_seconds=float(self.protocol["timing"]["guaranteedSilenceBeforeStimulusSeconds"]),
            timing_configuration=self.protocol["timing"],
            fail_on_backend_error=self.audio_mode == "pc",
        )
        if self.quest_mode == "m8_tcp":
            self._quest_transport = QuestSelectionTcpServer(
                self.quest_host,
                self.quest_port,
                accept_timeout_seconds=self.quest_accept_timeout_seconds,
                ack_timeout_seconds=self.quest_ack_timeout_seconds,
            )
            self._quest_transport.start()
            self._quest = M8SelectionOrchestrator(
                self._quest_transport,
                event_sink=self.recorder.record_quest_event,
            )
        else:
            self._quest_transport = None
            self._quest = M8SelectionOrchestrator(
                _GoldenQuestTransport(self._candidates),
                event_sink=self.recorder.record_quest_event,
            )
        self._m13_logger = _RecorderM135Logger(self.recorder)

    def _expected_quest_trial_ids(self, specs):
        return [
            item["trialId"]
            for item in specs
            if item.get("block") in ("m13_active", "context_conditions", "closed_loop_tasks")
            and item.get("slot") is not None
        ]

    def _refresh_quest_evidence(self, expected_trial_ids=None):
        if self.recorder is None or self.quest_mode != "m8_tcp" or self._quest_transport is None:
            return
        existing = dict(self.recorder.manifest.get("questIntegration", {}))
        if expected_trial_ids is None:
            expected_trial_ids = existing.get("expectedSelectionTrialIds", [])
        evidence = self._quest_transport.evidence
        integration = {
            **existing,
            **evidence,
            "required": True,
            "expectedSelectionTrialIds": list(expected_trial_ids),
        }
        boundary = dict(self.recorder.manifest.get("hardwareBoundary", {}))
        boundary["questOperated"] = bool(evidence["connectionAccepted"] and evidence["ackCount"] > 0)
        self.recorder._touch_manifest(
            questIntegration=integration,
            hardwareBoundary=boundary,
        )

    def _rest(self, trial_id, rest_type, seconds):
        seconds = float(seconds)
        if seconds <= 0:
            return
        self.recorder.record_event(
            "REST_STARTED",
            trialId=None,
            eventSource="orchestrator",
            restType=rest_type,
            durationSeconds=seconds,
            afterTrialId=trial_id,
        )
        wait_for_timeline_duration(
            self.clock,
            seconds,
            compressed=self.execution_timing_mode == "compressed_preflight",
        )
        self.recorder.record_event(
            "REST_COMPLETED",
            trialId=None,
            eventSource="orchestrator",
            restType=rest_type,
            durationSeconds=seconds,
            afterTrialId=trial_id,
        )

    def _record_quality(self, record):
        value = {
            "recordType": "m13_7_channel_quality",
            "schemaVersion": 1,
            "sessionId": self.recorder.session_id,
            "monotonicNs": int(self.clock.monotonic_ns()),
            "utcTimestamp": self.clock.utc_now(),
            **dict(record),
        }
        _append_jsonl(self.session_root / CHANNEL_QUALITY_FILENAME, value)
        return value

    def _run_quality_gate(self, block_config):
        if self.source_mode != "live-nd8":
            quality = {
                "verdict": "NOT_MEASURED_SIMULATION",
                "qualityGate": "3/5 candidate occipital channels",
                "candidateChannels": self.protocol["channelQuality"]["candidateChannels"],
                "minimumPassingChannels": self.protocol["channelQuality"]["minimumPassingChannels"],
                "selectedChannels": list(FROZEN_DECODER_CONFIGURATION["selectedChannels"]),
                "source": "synthetic source; no human channel admission claim",
            }
            self._record_quality(quality)
            self.recorder._touch_manifest(channelAdmission=quality, selectedChannels=quality["selectedChannels"])
            return quality
        from eeg.decoder.formal_online import channel_admission

        stop = self.pipeline.stop_sample or 0
        start = self.pipeline.buffer.start_sample or 0
        sample_count = min(60000, max(0, int(stop - start)))
        if sample_count <= 0:
            raise GoldenLauncherError("live channel-quality baseline produced no samples")
        values = self.pipeline.buffer.window(start, start + sample_count)
        quality = channel_admission(
            values,
            self.pipeline.continuity_statuses,
            self.recorder.manifest.get("softwareCommit", _git_commit()),
            self.clock.utc_now(),
        )
        quality["baselineSamplesPerChannel"] = sample_count
        quality["qualityGate"] = "3/5 candidate occipital channels"
        self._record_quality(quality)
        if quality["verdict"] != "READY":
            self.recorder._touch_manifest(channelAdmission=quality, selectedChannels=[])
            raise GoldenLauncherError("live ND8 channel admission failed: {}".format(quality["verdict"]))
        self.pipeline.selected_channels = tuple(quality["selectedChannels"])
        self.recorder._touch_manifest(channelAdmission=quality, selectedChannels=list(self.pipeline.selected_channels))
        return quality
        self.clock.sleep(seconds)
        self.recorder.record_event(
            "REST_COMPLETED",
            trialId=None,
            eventSource="orchestrator",
            restType=rest_type,
            durationSeconds=seconds,
            afterTrialId=trial_id,
        )

    def _decoder_and_runtime(self, spec, anchor):
        slot = spec.get("slot")
        decoded = self.pipeline.predict(anchor) if anchor is not None else None
        if decoded is None:
            self.recorder.record_decoder_evidence(
                spec["trialId"],
                0,
                sourceType=self.source.source_type,
                decoder="numpy_fbcca",
                decisionMade=False,
                reason="insufficient_contiguous_samples",
                downstreamSampleCount=self.pipeline.observed_sample_count,
            )
            return {"decisionMade": False, "reason": "insufficient_contiguous_samples"}

        raw_scores = decoded["candidateScores"]
        self.recorder.record_decoder_evidence(
            spec["trialId"],
            0,
            sourceType=self.source.source_type,
            decoder=decoded["decoder"],
            rawScores=raw_scores,
            predictedClass=decoded["predictedClass"],
            firstEligibleSample=decoded["firstEligibleSample"],
            analysisWindowStartSample=decoded["analysisWindowStartSample"],
            analysisWindowSampleCount=decoded["analysisWindowSampleCount"],
            sampleAnchorSampleIndex=decoded["sampleAnchorSampleIndex"],
            stimulusOnsetEventSequence=decoded["stimulusOnsetEventSequence"],
            samplesAvailableAtDecision=decoded["samplesAvailableAtDecision"],
            downstreamSampleCount=decoded["downstreamSampleCount"],
            computeDurationNs=decoded["computeDurationNs"],
        )
        block = spec["block"]
        if block == "no_intent_rejection" or slot is None:
            return {"decisionMade": False, "reason": "no_intent"}

        condition = spec.get("condition", "neutral")
        weights = _context_weights(condition, int(slot)) if condition in ("aligned", "neutral", "conflict") else (0.25, 0.25, 0.25)
        context_prior = __import__("integration.m13_7_golden_session", fromlist=["fixture_context_prior"]).fixture_context_prior(weights)
        eeg_by_block = {
            candidate.logical_block_id: score
            for candidate, score in zip(self._candidates, raw_scores)
        }
        fused = fuse_context_and_eeg(context_prior, self._candidates, eeg_by_block)
        self.recorder.record_context_evidence(
            spec["trialId"],
            0,
            condition=condition,
            contextPrior=context_prior.to_public_dict(),
            fusedEvidence=fused.to_public_dict(),
        )
        if block not in ("m13_active", "context_conditions", "closed_loop_tasks"):
            return {
                "decisionMade": False,
                "reason": "calibration_or_artifact_only",
                "predictedClass": decoded["predictedClass"],
            }
        snapshot = DynamicStoppingSnapshot.from_m12(
            0,
            fused,
            1.5,
            2.0,
            {"source": "M13.7 full live launcher", "packetSource": self.source.source_type},
        )
        runtime = M135TrialRuntime(
            MODE_ACTIVE,
            self._quest,
            self._m13_logger,
            candidates=self._candidates,
        )
        selection_id = spec["trialId"] + "-selection"
        try:
            if not runtime.start_trial(selection_id, spec["trialId"]):
                return {"decisionMade": False, "reason": "selection_open_rejected"}
            runtime.observe(snapshot, spec["trialId"], selection_id)
            return runtime.finalize_trial().to_public_dict()
        except M135RuntimeError as error:
            return {"decisionMade": False, "reason": "m13_runtime_error", "detail": str(error)}

    def _execute_trial(self, spec):
        trial_id = spec["trialId"]
        self.current_trial = trial_id
        slot = spec.get("slot")
        frequency = None if slot is None else float(SLOT_TO_FREQUENCY_HZ[int(slot)])
        self.recorder.record_event(
            "TRIAL_STARTED",
            trialId=trial_id,
            eventSource="orchestrator",
            protocolOrdinal=spec["ordinal"],
            protocolBlock=spec["block"],
            condition=spec.get("condition"),
            slot=slot,
            frequencyHz=frequency,
            targetCueBeepCount=spec.get("targetCueBeepCount"),
            protocolRecord=dict(spec),
            mode=self.recorder.manifest.get("mode"),
        )
        self.recorder.record_event(
            "TRIAL_PREPARATION_STARTED",
            trialId=trial_id,
            eventSource="orchestrator",
            preparationSeconds=float(self.protocol["timing"]["preparationSeconds"]),
        )
        self.cue.target_cue(trial_id, self.recorder.session_id, slot, frequency)
        wait_for_timeline_duration(
            self.clock,
            float(self.protocol["timing"]["preparationSeconds"]),
            compressed=self.execution_timing_mode == "compressed_preflight",
        )
        self.recorder.record_event(
            "TRIAL_PREPARATION_FINISHED",
            trialId=trial_id,
            eventSource="orchestrator",
            preparationSeconds=float(self.protocol["timing"]["preparationSeconds"]),
        )
        self.cue.ready_cue(trial_id, self.recorder.session_id)
        self.cue.wait_for_stimulus_silence()
        onset_event = self.cue.stimulus_onset(
            trial_id,
            self.recorder.session_id,
            slot,
            frequency,
            spec.get("condition"),
        )
        self.pipeline.mark_next_segment(
            None if onset_event is None else onset_event.get("monotonicNs"),
            None if onset_event is None else onset_event.get("sequence"),
        )
        acquired = self.source.acquire_trial(spec, float(self.protocol["timing"]["stimulusAndAnalysisSeconds"]))
        anchor = self.pipeline.next_segment_anchor
        if anchor is not None:
            packet, continuity = anchor
            anchor_metadata = self.pipeline.next_segment_anchor_metadata
            self.recorder.record_event(
                "TRIAL_SAMPLE_ANCHOR",
                trialId=trial_id,
                eventSource="acquisition",
                packetSequence=packet.packet_sequence,
                sourceSampleIndex=continuity.cumulative_first_sample_index,
                continuityStatus=continuity.status,
                stimulusOnsetMonotonicNs=anchor_metadata.get("stimulusOnsetMonotonicNs"),
                stimulusOnsetEventSequence=anchor_metadata.get("stimulusOnsetEventSequence"),
                anchorBasis=anchor_metadata.get("anchorBasis"),
                anchorPacketReceiveMonotonicNs=anchor_metadata.get("anchorPacketReceiveMonotonicNs"),
                sampleAnchorSampleIndex=anchor_metadata.get("sampleAnchorSampleIndex"),
            )
        outcome = self._decoder_and_runtime(spec, anchor)
        self.cue.stimulus_offset(trial_id, self.recorder.session_id)
        self.cue.end_cue(trial_id, self.recorder.session_id, "decision" if outcome.get("decisionMade") else outcome.get("reason", "no_decision"))
        if spec["block"] == "closed_loop_tasks":
            self.recorder.record_action(
                trial_id,
                actionType="closed_loop_selection_and_action",
                taskId=spec.get("taskId"),
                taskName=spec.get("taskName"),
                repetition=spec.get("repetition"),
                sequenceStep=spec.get("sequenceStep"),
                logicalBlockId=spec.get("targetLogicalBlockId"),
                executionStatus="not_invoked_by_golden_launcher",
                telemetryAssociation={
                    "source": "M13.7 live launcher boundary",
                    "physicalRobotOperated": False,
                    "mujocoInvoked": False,
                    "preserveIndividualAction": bool(spec.get("preserveIndividualAction")),
                },
            )
        if spec["block"] == "controlled_artifact":
            self.recorder.record_action(
                trial_id,
                actionType="controlled_artifact_label",
                artifactLabel=spec.get("artifactLabel"),
                performedBySoftware=False,
                instruction=spec.get("instruction"),
            )
        self.recorder.record_event(
            "TRIAL_DECISION",
            trialId=trial_id,
            eventSource="orchestrator",
            outcome=outcome,
            packetCount=self.pipeline.packet_count,
            observedSampleCount=self.pipeline.observed_sample_count,
        )
        self.recorder.record_event(
            "TRIAL_FINISHED",
            trialId=trial_id,
            eventSource="orchestrator",
            outcome=outcome,
            sampleAnchor=None if anchor is None else anchor[1].cumulative_first_sample_index,
        )
        self.current_trial = None
        return outcome

    def _execute_block(self, block_config, specs, stop_after_block=None, block_index=None):
        block_id = block_config["block"]
        if block_id in self.completed_blocks:
            return {"block": block_id, "status": "SKIPPED_RESUME"}
        self.recorder.record_event(
            "BLOCK_STARTED",
            trialId=None,
            eventSource="orchestrator",
            blockId=block_id,
            blockIndex=block_index,
            trialCount=len(specs),
            resumeBoundary=bool(block_config.get("resumeBoundary", True)),
        )
        if block_id == "channel_quality_baseline":
            self.recorder.record_event(
                "CHANNEL_QUALITY_BASELINE_STARTED",
                trialId=None,
                eventSource="acquisition",
                durationSeconds=float(block_config["durationSeconds"]),
                candidateChannels=self.protocol["channelQuality"]["candidateChannels"],
                minimumPassingChannels=self.protocol["channelQuality"]["minimumPassingChannels"],
            )
            self.source.acquire_baseline(float(block_config["durationSeconds"]))
            self._run_quality_gate(block_config)
            self.recorder.record_event(
                "CHANNEL_QUALITY_BASELINE_FINISHED",
                trialId=None,
                eventSource="acquisition",
                durationSeconds=float(block_config["durationSeconds"]),
                qualityGate="3/5 candidate occipital channels",
            )
        else:
            for index, spec in enumerate(specs):
                self._execute_trial(spec)
                if index + 1 < len(specs):
                    self._rest(spec["trialId"], "inter_trial", self.protocol["timing"]["interTrialRestSeconds"])
        self.recorder.record_event(
            "BLOCK_COMPLETED",
            trialId=None,
            eventSource="orchestrator",
            blockId=block_id,
            blockIndex=block_index,
            trialCount=len(specs),
        )
        self.completed_blocks.append(block_id)
        self.recorder._touch_manifest(
            completedBlocks=list(self.completed_blocks),
            lastCompletedBlock=block_id,
            nextBlockIndex=self._next_block_index() if block_index is not None else len(self.completed_blocks),
            periodicFlush="append-only JSONL records fsync on every record; manifest atomically refreshed at block boundary",
        )
        rest_seconds = block_config.get("restAfterSeconds")
        if rest_seconds is not None:
            self._rest(None, "block", rest_seconds)
        if stop_after_block == block_id:
            self.recorder._touch_manifest(
                status="paused_at_block_boundary",
                pausedAtBlock=block_id,
                partialSession=True,
            )
            return {"block": block_id, "status": "PAUSED"}
        return {"block": block_id, "status": "PASS"}

    def _run_specs(self, specs, preflight=False, stop_after_block=None):
        session_category = "PREFLIGHT" if preflight else (
            "GOLDEN_CONTINUATION" if self.start_block_index > 0 else "GOLDEN"
        )
        execution_slice = self.execution_slice if not preflight else {
            "plannedSegments": len(specs),
            "executedBlockIndices": [],
            "skippedBlockIndices": [],
        }
        self.recorder.record_event(
            "SESSION_STARTED",
            trialId=None,
            eventSource="orchestrator",
            sessionCategory=session_category,
            plannedSegments=execution_slice["plannedSegments"],
            executedSegments=len(specs),
            protocolTotalSegments=len(self.protocol["trials"]),
            startBlockIndex=self.start_block_index if not preflight else None,
            executedBlockIndices=list(execution_slice["executedBlockIndices"]),
            skippedBlockIndices=list(execution_slice["skippedBlockIndices"]),
        )
        if preflight:
            self.recorder.record_event(
                "PREFLIGHT_STARTED",
                trialId=None,
                eventSource="orchestrator",
                selectedTrialIds=[item["trialId"] for item in specs],
            )
            block = {"block": "preflight_representative", "resumeBoundary": True, "trialCount": len(specs), "restAfterSeconds": 0.0}
            result = self._execute_block(block, specs, stop_after_block=stop_after_block)
            self.recorder.record_event("PREFLIGHT_FINISHED", trialId=None, eventSource="orchestrator", result=result)
            return [result]

        results = []
        specs_by_block = {}
        for spec in self.protocol["trials"]:
            specs_by_block.setdefault(spec["block"], []).append(spec)
        for block_index, block in enumerate(self.protocol["blocks"]):
            if block_index < self.start_block_index:
                continue
            result = self._execute_block(
                block,
                specs_by_block.get(block["block"], []),
                stop_after_block=stop_after_block,
                block_index=block_index,
            )
            results.append(result)
            if result.get("status") == "PAUSED":
                break
        return results

    def _abort_partial(self, reason, status="aborted_partial"):
        if self.recorder is None:
            return
        if self.current_trial and self.current_trial in getattr(self.recorder, "_analysis_open", set()):
            try:
                self.cue.stimulus_offset(self.current_trial, self.recorder.session_id, reason="interrupted_partial")
            except (AudioTimingError, RuntimeError):
                pass
        if self.current_trial:
            try:
                self.recorder.record_event(
                    "TRIAL_INTERRUPTED",
                    trialId=self.current_trial,
                    eventSource="orchestrator",
                    reason=str(reason),
                )
            except (ValueError, RuntimeError):
                pass
        self.recorder._touch_manifest(
            status=status,
            partialSession=True,
            abortReason=str(reason),
            abortedUtc=self.clock.utc_now(),
            completedBlocks=list(self.completed_blocks),
            nextBlockIndex=self._next_block_index(),
        )
        details = getattr(reason, "details", None)
        if details:
            self.recorder._touch_manifest(
                timingFailure={
                    "error": str(reason),
                    **dict(details),
                }
            )

    def _close_source(self):
        if self.source is None:
            return
        try:
            self.source.stop()
        finally:
            self.source.close()

    def _close_quest_transport(self):
        if self._quest_transport is None:
            return
        try:
            self._quest_transport.close()
        finally:
            self._quest_transport = None

    def run(self, stop_after_block=None, specs=None):
        self._create_recorder()
        summary = self.startup_summary()
        if self.source_mode == "live-nd8" and not self.confirm_live_human:
            self.recorder._touch_manifest(status="awaiting_live_confirmation", startupSummary=summary)
            return summary
        try:
            self._build_components()
            expected_quest_trial_ids = self._expected_quest_trial_ids(specs or self.protocol["trials"])
            self._refresh_quest_evidence(expected_quest_trial_ids)
            if self.source_mode == "live-nd8":
                self.source.open_port()
                self.source.start_streaming()
            if specs is None:
                block_results = self._run_specs(
                    self.protocol["trials"],
                    preflight=False,
                    stop_after_block=stop_after_block,
                )
            else:
                block_results = self._run_specs(specs, preflight=True, stop_after_block=stop_after_block)
            paused = any(item.get("status") == "PAUSED" for item in block_results)
            if paused:
                self._refresh_quest_evidence(expected_quest_trial_ids)
                summary.update({"status": "PAUSED", "blockResults": block_results, "sessionRoot": str(self.session_root)})
                return summary
            self._close_source()
            self._refresh_quest_evidence(expected_quest_trial_ids)
            manifest = self.recorder.finalize()
            verification = verify_session(self.session_root)
            replay = RecordedND8Replay(self.session_root, speed="max").replay(lambda packet, continuity: None)
            expected_segments = len(specs) if specs is not None else self.execution_slice["plannedSegments"]
            summary.update({
                "status": "PASS" if verification["status"] == "PASS" and replay["packetCount"] > 0 else "FAIL",
                "sessionRoot": str(self.session_root),
                "blockResults": block_results,
                "plannedSegments": expected_segments,
                "executedSegments": expected_segments,
                "protocolTotalSegments": len(self.protocol["trials"]),
                "startBlockIndex": self.start_block_index if specs is None else None,
                "executedBlockIndices": list(self.execution_slice["executedBlockIndices"]) if specs is None else [],
                "skippedBlockIndices": list(self.execution_slice["skippedBlockIndices"]) if specs is None else [],
                "executionProjectedDurationSeconds": (
                    self.execution_slice["executionProjectedDurationSeconds"] if specs is None
                    else self.protocol["projectedDurationSeconds"]
                ),
                "executionProjectedDurationMinutes": (
                    self.execution_slice["executionProjectedDurationMinutes"] if specs is None
                    else self.protocol["projectedDurationMinutes"]
                ),
                "manifestStatus": manifest.get("status"),
                "verification": verification,
                "replay": replay,
                "packetCount": self.pipeline.packet_count,
                "observedSampleCount": self.pipeline.observed_sample_count,
                "hardwareBoundary": manifest.get("hardwareBoundary"),
                "questIntegration": manifest.get("questIntegration"),
            })
            _atomic_json(self.session_root / ("preflight-summary.json" if specs is not None else "simulation-summary.json"), summary)
            return summary
        except KeyboardInterrupt:
            self._refresh_quest_evidence()
            self._abort_partial("KeyboardInterrupt")
            return {**summary, "status": "ABORTED_PARTIAL", "sessionRoot": str(self.session_root), "completedBlocks": list(self.completed_blocks)}
        except Exception as error:
            self._refresh_quest_evidence()
            self._abort_partial(error, status="failed_partial")
            raise
        finally:
            try:
                self._close_source()
            except Exception:
                pass
            try:
                self._close_quest_transport()
            except Exception:
                pass


def run_full_simulation(
    protocol_path,
    data_root,
    session_id=None,
    stop_after_block=None,
    resume=False,
    packet_samples=2000,
    start_block_index=0,
    continuation_from_session_id=None,
    continuation_reason=None,
):
    launcher = GoldenLiveSessionLauncher(
        protocol_path,
        data_root,
        mode="simulation",
        session_id=session_id,
        resume=resume,
        packet_samples=packet_samples,
        start_block_index=start_block_index,
        continuation_from_session_id=continuation_from_session_id,
        continuation_reason=continuation_reason,
        audio_mode="silent",
        clock=_FakeClock(),
    )
    return launcher.run(stop_after_block=stop_after_block)


def run_preflight(
    protocol_path,
    data_root,
    source_mode="synthetic",
    com_port="COM11",
    session_id=None,
    confirm_live_human=False,
    audio_mode="silent",
    quest_mode="none",
    quest_host="0.0.0.0",
    quest_port=11001,
):
    normalized_source = "live-nd8" if source_mode == "live-nd8" else "synthetic"
    normalized_quest_mode = str(quest_mode).replace("-", "_")
    if normalized_source == "live-nd8":
        clock = _RealClock()
    elif audio_mode == "pc":
        clock = _CompressedPreflightClock()
    else:
        clock = _FakeClock()
    launcher = GoldenLiveSessionLauncher(
        protocol_path,
        data_root,
        mode="preflight",
        source_mode=normalized_source,
        com_port=com_port,
        session_id=session_id,
        confirm_live_human=confirm_live_human,
        clock=clock,
        audio_mode=audio_mode,
        quest_mode=normalized_quest_mode,
        quest_host=quest_host,
        quest_port=quest_port,
    )
    by_slot = {}
    no_intent = None
    for item in launcher.protocol["trials"]:
        if item.get("slot") is None and no_intent is None:
            no_intent = item
        elif normalized_quest_mode == "m8_tcp" and item.get("block") == "m13_active" and item.get("slot") not in by_slot:
            by_slot[item["slot"]] = item
        elif normalized_quest_mode != "m8_tcp" and item.get("slot") not in by_slot:
            by_slot[item["slot"]] = item
    if normalized_quest_mode == "m8_tcp" and set(by_slot) != {0, 1, 2}:
        raise GoldenLauncherError("M8 Quest preflight requires one m13_active trial for each slot 0, 1, and 2")
    selected = [by_slot[index] for index in (0, 1, 2) if index in by_slot]
    if no_intent is not None:
        selected.append(no_intent)
    return launcher.run(specs=selected)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)

    def common(command, data_required=True):
        command.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL_PATH)
        command.add_argument("--data-root", type=Path, required=data_required)
        command.add_argument("--session-id")

    simulate = subs.add_parser("simulate", help="run the entire protocol with a no-hardware source")
    common(simulate)
    simulate.add_argument("--stop-after-block")
    simulate.add_argument("--resume", action="store_true")
    simulate.add_argument("--packet-samples", type=int, default=2000)
    simulate.add_argument("--start-block-index", type=int, default=0)
    simulate.add_argument("--continuation-from-session-id")
    simulate.add_argument("--continuation-reason", default=DEFAULT_CONTINUATION_REASON)

    preflight = subs.add_parser("preflight", help="run the short representative preflight")
    common(preflight)
    preflight.add_argument("--source", choices=("synthetic", "live-nd8"), default="synthetic")
    preflight.add_argument(
        "--audio",
        choices=("silent", "pc"),
        default="silent",
        help="silent deterministic backend by default; pc enables real Windows speaker cues",
    )
    preflight.add_argument("--com", default="COM11")
    preflight.add_argument("--confirm-live-human", action="store_true")
    preflight.add_argument(
        "--quest",
        choices=("none", "m8-tcp"),
        default="none",
        help="none keeps the software-only preflight; m8-tcp requires a real M9 Quest M8 selection client",
    )
    preflight.add_argument("--quest-host", default="0.0.0.0")
    preflight.add_argument("--quest-port", type=int, default=11001)

    live = subs.add_parser("live", help="run the complete live human protocol after confirmation")
    common(live)
    live.add_argument("--com", required=True)
    live.add_argument("--resume", action="store_true")
    live.add_argument("--confirm-live-human", action="store_true")
    live.add_argument("--start-block-index", type=int, default=0)
    live.add_argument("--continuation-from-session-id")
    live.add_argument("--continuation-reason", default=DEFAULT_CONTINUATION_REASON)

    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.command == "simulate":
        result = run_full_simulation(
            args.protocol,
            args.data_root,
            session_id=args.session_id,
            stop_after_block=args.stop_after_block,
            resume=args.resume,
            packet_samples=args.packet_samples,
            start_block_index=args.start_block_index,
            continuation_from_session_id=args.continuation_from_session_id,
            continuation_reason=args.continuation_reason,
        )
    elif args.command == "preflight":
        result = run_preflight(
            args.protocol,
            args.data_root,
            source_mode=args.source,
            com_port=args.com,
            session_id=args.session_id,
            confirm_live_human=args.confirm_live_human,
            audio_mode=args.audio,
            quest_mode=args.quest,
            quest_host=args.quest_host,
            quest_port=args.quest_port,
        )
    else:
        launcher = GoldenLiveSessionLauncher(
            args.protocol,
            args.data_root,
            mode="live",
            source_mode="live-nd8",
            com_port=args.com,
            session_id=args.session_id,
            resume=args.resume,
            confirm_live_human=args.confirm_live_human,
            audio_mode="pc",
            start_block_index=args.start_block_index,
            continuation_from_session_id=args.continuation_from_session_id,
            continuation_reason=args.continuation_reason,
        )
        result = launcher.run()
    print(json.dumps({
        "status": result.get("status"),
        "mode": result.get("mode"),
        "sessionRoot": result.get("sessionRoot"),
        "segments": result.get("segments"),
        "plannedSegments": result.get("plannedSegments"),
        "executedSegments": result.get("executedSegments"),
        "startBlockIndex": result.get("startBlockIndex"),
        "executedBlockIndices": result.get("executedBlockIndices"),
        "skippedBlockIndices": result.get("skippedBlockIndices"),
        "executionProjectedDurationSeconds": result.get("executionProjectedDurationSeconds"),
        "verification": None if result.get("verification") is None else result["verification"].get("status"),
        "replay": None if result.get("replay") is None else result["replay"].get("status"),
        "hardwareBoundary": result.get("hardwareBoundary"),
        "questIntegration": result.get("questIntegration"),
        "warning": result.get("warning"),
    }, ensure_ascii=False, sort_keys=True))
    if result.get("status") in ("PASS", "READY", "PAUSED", "CONFIRMATION_REQUIRED"):
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GoldenLauncherError",
    "GoldenLiveSessionLauncher",
    "GoldenPacketPipeline",
    "LiveND8Source",
    "SyntheticND8Source",
    "build_execution_slice",
    "load_golden_protocol",
    "run_full_simulation",
    "run_preflight",
]
