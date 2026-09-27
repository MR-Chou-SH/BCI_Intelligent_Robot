"""M13.7 Golden Human Session infrastructure.

This module keeps the existing M8/M13 lifecycle as the authority and adds
durable evidence, replaceable audio cues, a live-compatible raw ND8 replay
source, and a versioned protocol schedule. It never opens COM11 and never
claims a live-human result.
"""

from datetime import datetime, timezone
import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Callable, Iterable, Mapping, Optional

import numpy as np

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.acquisition.recorded_nd8_replay import RecordedND8Replay
from eeg.decoder.pseudo_online import DecoderBackend, ReplayPacket, RollingEegBuffer
from eeg.sample_association.models import PacketContinuityRecord
from integration.m10_task_benchmark import load_task_definitions
from integration.m12_context_eeg_fusion import (
    ActiveSsvepCandidate,
    fuse_context_and_eeg,
)
from integration.m13_5_runtime import (
    MODE_ACTIVE,
    M135TrialRuntime,
    build_default_active_candidates,
)
from integration.m13_dynamic_stopping import DynamicStoppingSnapshot
from integration.m8_selection_orchestration import M8SelectionOrchestrator
from integration.m13_trajectory_fixture import fixture_context_prior


GOLDEN_SCHEMA_VERSION = 1
GOLDEN_PROTOCOL_VERSION = "m13.7-golden-v1"
RAW_EEG_FILENAME = "raw-eeg.jsonl"
PACKET_METADATA_FILENAME = "packet-metadata.jsonl"
EVENTS_FILENAME = "events.jsonl"
DECODER_FILENAME = "decoder-evidence.jsonl"
CONTEXT_FILENAME = "context-evidence.jsonl"
ACTIONS_FILENAME = "actions-telemetry.jsonl"
QUEST_FILENAME = "quest-events.jsonl"
SLOT_TO_FREQUENCY_HZ = {0: 7.2, 1: 9.0, 2: 12.0}
FORMAL_GOLDEN_PREPARATION_SECONDS = 6.0
TRIAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
REQUIRED_SESSION_FILES = {
    "rawEegFile": RAW_EEG_FILENAME,
    "packetMetadataFile": PACKET_METADATA_FILENAME,
    "eventFile": EVENTS_FILENAME,
    "decoderEvidenceFile": DECODER_FILENAME,
    "contextEvidenceFile": CONTEXT_FILENAME,
    "actionsTelemetryFile": ACTIONS_FILENAME,
    "questEventsFile": QUEST_FILENAME,
}


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _jsonl_read(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("missing JSONL file: {}".format(path))
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("invalid JSONL at {}:{}: {}".format(path, line_number, error)) from error
            if not isinstance(value, dict):
                raise ValueError("JSONL at {}:{} is not an object".format(path, line_number))
            value["_lineNumber"] = line_number
            records.append(value)
    return records


def _append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("{}.{}.tmp".format(path.name, os.getpid()))
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def _hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_timeline_duration(clock, seconds, compressed=False):
    """Wait for a protocol duration without undersleeping in live timing mode."""
    seconds = float(seconds)
    if seconds <= 0.0:
        return
    if compressed:
        clock.sleep(seconds)
        return
    deadline_ns = int(clock.monotonic_ns()) + int(math.ceil(seconds * 1_000_000_000.0))
    while True:
        remaining_ns = deadline_ns - int(clock.monotonic_ns())
        if remaining_ns <= 0:
            return
        clock.sleep(remaining_ns / 1_000_000_000.0)


class AudioTimingError(RuntimeError):
    """Audio was requested in a timing state where it is forbidden."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = dict(details or {})


class NullLoggingAudioBackend:
    """Silent backend that records every requested tone for tests and rehearsal."""

    name = "null_logging"

    def __init__(self, monotonic_ns: Callable[[], int] = time.monotonic_ns):
        self.monotonic_ns = monotonic_ns
        self.calls = []

    def beep(self, frequency_hz, duration_seconds):
        self.calls.append(
            {
                "frequencyHz": float(frequency_hz),
                "durationSeconds": float(duration_seconds),
                "requestedMonotonicNs": int(self.monotonic_ns()),
            }
        )

    def summary(self):
        return {
            "backend": self.name,
            "available": True,
            "beepCount": len(self.calls),
            "calls": list(self.calls),
        }


class PcToneAudioBackend:
    """Small Windows PC tone backend; acoustic arrival is not measured here."""

    name = "pc_winsound"

    def __init__(self):
        try:
            import winsound
        except ImportError as error:  # pragma: no cover - platform-specific
            raise RuntimeError("winsound is unavailable; use the null backend for software tests") from error
        self._winsound = winsound
        self.calls = []

    def beep(self, frequency_hz, duration_seconds):  # pragma: no cover - platform-specific
        frequency = max(37, min(32767, int(round(float(frequency_hz)))))
        duration_ms = max(1, int(round(float(duration_seconds) * 1000.0)))
        started = time.perf_counter_ns()
        self._winsound.Beep(frequency, duration_ms)
        self.calls.append(
            {
                "frequencyHz": frequency,
                "durationSeconds": duration_ms / 1000.0,
                "requestedMonotonicNs": started,
            }
        )

    def summary(self):  # pragma: no cover - platform-specific
        return {
            "backend": self.name,
            "available": True,
            "beepCount": len(self.calls),
            "calls": list(self.calls),
        }


class AudioCueSystem:
    """Cue contract attached to an existing trial/presentation lifecycle."""

    TARGET_BEEP_FREQUENCY_HZ = 880.0
    BEEP_FREQUENCY_HZ = TARGET_BEEP_FREQUENCY_HZ
    READY_BEEP_FREQUENCY_HZ = 1760.0
    END_BEEP_FREQUENCY_HZ = 440.0
    TARGET_BEEP_SECONDS = 0.15
    INTER_BEEP_SILENCE_SECONDS = 0.28
    TARGET_GROUP_SEPARATION_SECONDS = 0.70
    READY_BEEP_SECONDS = 0.50
    END_BEEP_SECONDS = 0.60
    SILENCE_SAFETY_MARGIN_SECONDS = 0.010

    def __init__(
        self,
        backend,
        event_sink: Callable[..., object],
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_now: Callable[[], str] = _utc_now,
        sleep: Callable[[float], None] = time.sleep,
        required_silence_seconds: float = 1.0,
        timing_configuration: Optional[Mapping] = None,
        fail_on_backend_error: bool = False,
    ):
        if required_silence_seconds < 1.0:
            raise ValueError("required silence must be at least one second")
        self.backend = backend
        self.event_sink = event_sink
        self.monotonic_ns = monotonic_ns
        self.utc_now = utc_now
        self.sleep = sleep
        self.required_silence_seconds = float(required_silence_seconds)
        self.fail_on_backend_error = bool(fail_on_backend_error)
        timing = dict(timing_configuration or {})
        self.target_beep_seconds = float(timing.get("targetBeepSeconds", self.TARGET_BEEP_SECONDS))
        self.inter_beep_silence_seconds = float(
            timing.get("interTargetBeepSilenceSeconds", self.INTER_BEEP_SILENCE_SECONDS)
        )
        self.target_group_separation_seconds = float(
            timing.get("targetGroupSeparationSeconds", self.TARGET_GROUP_SEPARATION_SECONDS)
        )
        self.target_beep_frequency_hz = float(
            timing.get("targetFrequencyHz", self.TARGET_BEEP_FREQUENCY_HZ)
        )
        self.target_beep_frequencies_hz_by_slot = {
            int(slot): float(frequency)
            for slot, frequency in dict(timing.get("targetFrequenciesHzBySlot", {})).items()
        }
        self.ready_beep_frequency_hz = float(
            timing.get("readyFrequencyHz", self.READY_BEEP_FREQUENCY_HZ)
        )
        self.end_beep_frequency_hz = float(
            timing.get("endFrequencyHz", self.END_BEEP_FREQUENCY_HZ)
        )
        self.ready_beep_seconds = float(timing.get("readyBeepSeconds", self.READY_BEEP_SECONDS))
        self.end_beep_seconds = float(timing.get("endBeepSeconds", self.END_BEEP_SECONDS))
        if min(
            self.target_beep_seconds,
            self.inter_beep_silence_seconds,
            self.target_group_separation_seconds,
            self.ready_beep_seconds,
            self.end_beep_seconds,
        ) < 0 or min(
            self.target_beep_frequency_hz,
            self.ready_beep_frequency_hz,
            self.end_beep_frequency_hz,
        ) <= 0:
            raise ValueError("audio timing values must be nonnegative")
        if any(
            slot not in SLOT_TO_FREQUENCY_HZ or frequency <= 0
            for slot, frequency in self.target_beep_frequencies_hz_by_slot.items()
        ):
            raise ValueError("per-slot target cue frequencies require slots 0, 1, 2 and positive Hz")
        self._analysis_open = set()
        self._ready_end_ns = {}
        self._silence_deadline_ns = {}
        self._last_ready_trial_id = None
        self._warnings = []

    @property
    def warnings(self):
        return tuple(self._warnings)

    def _event(self, event_type, trial_id, session_id, **values):
        return self.event_sink(
            event_type,
            trialId=trial_id,
            sessionId=session_id,
            eventSource="audio" if "CUE" in event_type else "trial_lifecycle",
            **values,
        )

    def _play(self, trial_id, session_id, frequency_hz, duration_seconds):
        if trial_id in self._analysis_open:
            raise AudioTimingError("audio is forbidden while analysis window is open for {}".format(trial_id))
        try:
            self.backend.beep(frequency_hz, duration_seconds)
        except Exception as error:  # Timeline remains authoritative; failure is durable.
            self._warnings.append("audio_backend_failure:{}".format(error))
            self._event(
                "AUDIO_BACKEND_WARNING",
                trial_id,
                session_id,
                detail=str(error),
                audioAllowed=True,
            )
            if self.fail_on_backend_error:
                raise AudioTimingError("audio backend failed: {}".format(error)) from error

    def target_cue(self, trial_id, session_id, slot=None, frequency_hz=None):
        if slot is not None and slot not in SLOT_TO_FREQUENCY_HZ:
            raise ValueError("target cue slot must be 0, 1, 2, or None")
        expected_frequency = None if slot is None else SLOT_TO_FREQUENCY_HZ[slot]
        if (
            slot is not None
            and frequency_hz is not None
            and not math.isclose(float(frequency_hz), expected_frequency)
        ):
            raise ValueError("target cue frequency does not match the frozen slot mapping")
        beep_count = 0 if slot is None else int(slot) + 1
        cue_frequency_hz = (
            self.target_beep_frequencies_hz_by_slot.get(int(slot), self.target_beep_frequency_hz)
            if slot is not None else self.target_beep_frequency_hz
        )
        self._event(
            "TARGET_CUE_START",
            trial_id,
            session_id,
            slot=slot,
            frequencyHz=expected_frequency if frequency_hz is None else float(frequency_hz),
            cueToneFrequencyHz=cue_frequency_hz,
            beepCount=beep_count,
        )
        for index in range(beep_count):
            self._play(trial_id, session_id, cue_frequency_hz, self.target_beep_seconds)
            if index + 1 < beep_count:
                self.sleep(self.inter_beep_silence_seconds)
        self._event(
            "TARGET_CUE_END",
            trial_id,
            session_id,
            slot=slot,
            frequencyHz=expected_frequency if frequency_hz is None else float(frequency_hz),
            cueToneFrequencyHz=cue_frequency_hz,
            beepCount=beep_count,
        )
        if beep_count:
            self.sleep(self.target_group_separation_seconds)

    def ready_cue(self, trial_id, session_id):
        self._event("READY_CUE_START", trial_id, session_id, beepCount=1)
        self._play(trial_id, session_id, self.ready_beep_frequency_hz, self.ready_beep_seconds)
        self._event("READY_CUE_END", trial_id, session_id, beepCount=1)
        ready_end_ns = int(self.monotonic_ns())
        self._ready_end_ns[trial_id] = ready_end_ns
        required_ns = int(math.ceil(self.required_silence_seconds * 1_000_000_000.0))
        safety_ns = int(math.ceil(self.SILENCE_SAFETY_MARGIN_SECONDS * 1_000_000_000.0))
        self._silence_deadline_ns[trial_id] = ready_end_ns + required_ns + safety_ns
        self._last_ready_trial_id = trial_id

    def wait_for_stimulus_silence(self, trial_id=None):
        trial_id = trial_id or self._last_ready_trial_id
        if trial_id is None or trial_id not in self._ready_end_ns:
            raise AudioTimingError("stimulus silence wait occurred before READY_CUE_END")
        deadline_ns = self._silence_deadline_ns[trial_id]
        while True:
            remaining_ns = deadline_ns - int(self.monotonic_ns())
            if remaining_ns <= 0:
                return
            self.sleep(remaining_ns / 1_000_000_000.0)

    def stimulus_onset(self, trial_id, session_id, slot=None, frequency_hz=None, condition=None):
        ready_end = self._ready_end_ns.get(trial_id)
        if ready_end is None:
            raise AudioTimingError("stimulus onset occurred before READY_CUE_END")
        onset_attempt_ns = int(self.monotonic_ns())
        silence_ns = onset_attempt_ns - ready_end
        silence = silence_ns / 1_000_000_000.0
        if silence + 1e-9 < self.required_silence_seconds:
            minimum_deadline_ns = ready_end + int(math.ceil(self.required_silence_seconds * 1_000_000_000.0))
            raise AudioTimingError(
                "configured silence before stimulus was not satisfied",
                details={
                    "readyEndMonotonicNs": ready_end,
                    "onsetAttemptMonotonicNs": onset_attempt_ns,
                    "requestedOnsetDeadlineMonotonicNs": minimum_deadline_ns,
                    "measuredSilenceSeconds": silence,
                    "shortfallSeconds": max(0.0, self.required_silence_seconds - silence),
                },
            )
        onset_event = self._event(
            "STIMULUS_ONSET",
            trial_id,
            session_id,
            slot=slot,
            frequencyHz=frequency_hz,
            condition=condition,
            silenceBeforeStimulusSeconds=silence,
        )
        self._event(
            "ANALYSIS_WINDOW_OPEN",
            trial_id,
            session_id,
            slot=slot,
            frequencyHz=frequency_hz,
            condition=condition,
        )
        self._analysis_open.add(trial_id)
        return onset_event

    def stimulus_offset(self, trial_id, session_id, reason="configured_window_complete"):
        if trial_id not in self._analysis_open:
            raise AudioTimingError("stimulus offset occurred without an open analysis window")
        self._event("ANALYSIS_WINDOW_CLOSE", trial_id, session_id, reason=reason)
        self._analysis_open.remove(trial_id)
        self._event("STIMULUS_OFFSET", trial_id, session_id, reason=reason)

    def end_cue(self, trial_id, session_id, outcome=None):
        if trial_id in self._analysis_open:
            raise AudioTimingError("END_CUE is forbidden before analysis closes")
        self._event("END_CUE_START", trial_id, session_id, beepCount=1, outcome=outcome)
        self._play(trial_id, session_id, self.end_beep_frequency_hz, self.end_beep_seconds)
        self._event("END_CUE_END", trial_id, session_id, beepCount=1, outcome=outcome)

    def summary(self):
        summary = self.backend.summary() if hasattr(self.backend, "summary") else {"backend": type(self.backend).__name__}
        summary.update(
            {
                "targetCueMapping": {str(key): value for key, value in SLOT_TO_FREQUENCY_HZ.items()},
                "requiredSilenceSeconds": self.required_silence_seconds,
                "silenceSafetyMarginSeconds": self.SILENCE_SAFETY_MARGIN_SECONDS,
                "timingConfiguration": {
                    "targetBeepSeconds": self.target_beep_seconds,
                    "interTargetBeepSilenceSeconds": self.inter_beep_silence_seconds,
                    "targetGroupSeparationSeconds": self.target_group_separation_seconds,
                    "readyBeepSeconds": self.ready_beep_seconds,
                    "endBeepSeconds": self.end_beep_seconds,
                },
                "frequencyConfiguration": {
                    "targetFrequencyHz": self.target_beep_frequency_hz,
                    "readyFrequencyHz": self.ready_beep_frequency_hz,
                    "endFrequencyHz": self.end_beep_frequency_hz,
                },
                "analysisGuard": "no audio while ANALYSIS_WINDOW_OPEN",
                "failOnBackendError": self.fail_on_backend_error,
                "warnings": list(self._warnings),
                "softwarePlaybackTimestampBoundary": "invocation time only; not acoustic arrival at the ear",
            }
        )
        return summary


class HumanSessionRecorder:
    """Crash-resilient, append-only evidence recorder for one session."""

    def __init__(
        self,
        root,
        session_id,
        source_type,
        protocol_version=GOLDEN_PROTOCOL_VERSION,
        software_commit=None,
        configuration=None,
        sampling_rate_hz=1000.0,
        channel_count=8,
        resume=False,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        utc_now: Callable[[], str] = _utc_now,
    ):
        self.root = Path(root)
        self.session_id = str(session_id)
        if not TRIAL_ID_RE.match(self.session_id):
            raise ValueError("session_id contains unsupported characters")
        self.monotonic_ns = monotonic_ns
        self.utc_now = utc_now
        self.source_type = str(source_type)
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        if self.manifest_path.exists() and not resume:
            raise FileExistsError("refusing to overwrite an existing session manifest: {}".format(self.manifest_path))
        if self.manifest_path.exists():
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            self.source_type = str(self.manifest.get("sourceType", self.source_type))
            if self.manifest.get("sessionId") != self.session_id:
                raise ValueError("resume session ID does not match manifest")
            if self.manifest.get("status") == "finalized":
                raise ValueError("finalized session is immutable; resume a new session ID")
            self._event_sequence = self._next_sequence(self.root / EVENTS_FILENAME)
        else:
            existing = [item for item in self.root.iterdir() if item.is_file()]
            if existing:
                raise FileExistsError("session directory contains files but no manifest: {}".format(self.root))
            now = self.utc_now()
            self.manifest = {
                "recordType": "m13_7_golden_session_manifest",
                "schemaVersion": GOLDEN_SCHEMA_VERSION,
                "sessionId": self.session_id,
                "protocolVersion": str(protocol_version),
                "sourceType": str(source_type),
                "softwareCommit": software_commit or _git_commit(),
                "createdUtc": now,
                "updatedUtc": now,
                "status": "recording",
                "samplingRateHz": float(sampling_rate_hz),
                "channelCount": int(channel_count),
                "samplingAssumptions": {
                    "rawValues": "as received before algorithm preprocessing",
                    "sampleAnchor": "first packet received at or after the recorded STIMULUS_ONSET event; hardware timing is not independently verified",
                    "hardwareTimingVerified": False,
                    "physicalOpticalTimingVerified": False,
                },
                "configuration": dict(configuration or {}),
                "files": dict(REQUIRED_SESSION_FILES),
                **REQUIRED_SESSION_FILES,
                "hardwareBoundary": {
                    "questOperated": False,
                    "nd8Operated": False,
                    "com11Opened": False,
                    "newRealEegCollected": False,
                    "physicalRobotOperated": False,
                },
            }
            self._event_sequence = 0
            _atomic_json(self.manifest_path, self.manifest)
        self._analysis_open = set()
        self._started_trials = set()
        self._real_eeg_marked = bool(
            self.manifest.get("hardwareBoundary", {}).get("newRealEegCollected")
        )
        self._restore_event_state()
        for filename in REQUIRED_SESSION_FILES.values():
            (self.root / filename).touch(exist_ok=True)

    @staticmethod
    def _next_sequence(path):
        if not Path(path).is_file():
            return 0
        records = _jsonl_read(path)
        sequences = [int(item["sequence"]) for item in records if isinstance(item.get("sequence"), int)]
        return max(sequences, default=-1) + 1

    def _restore_event_state(self):
        path = self.root / EVENTS_FILENAME
        if not path.is_file():
            return
        for record in _jsonl_read(path):
            trial_id = record.get("trialId")
            if record.get("eventType") == "TRIAL_STARTED" and trial_id:
                self._started_trials.add(trial_id)
            if record.get("eventType") == "ANALYSIS_WINDOW_OPEN" and trial_id:
                self._analysis_open.add(trial_id)
            elif record.get("eventType") == "ANALYSIS_WINDOW_CLOSE" and trial_id:
                self._analysis_open.discard(trial_id)

    def _touch_manifest(self, **values):
        self.manifest.update(values)
        self.manifest["updatedUtc"] = self.utc_now()
        _atomic_json(self.manifest_path, self.manifest)

    def record_event(
        self,
        event_type,
        trialId=None,
        sessionId=None,
        eventSource="orchestrator",
        monotonicNs=None,
        utcTimestamp=None,
        **values
    ):
        event_type = str(event_type)
        trial_id = None if trialId is None else str(trialId)
        if trial_id is not None and not TRIAL_ID_RE.match(trial_id):
            raise ValueError("invalid trial ID: {}".format(trial_id))
        if event_type == "TRIAL_STARTED":
            if trial_id is None or trial_id in self._started_trials:
                raise ValueError("duplicate or missing TRIAL_STARTED trial ID")
            self._started_trials.add(trial_id)
        if trial_id is not None and event_type != "TRIAL_STARTED" and trial_id not in self._started_trials:
            raise ValueError("event references a trial that was not started: {}".format(trial_id))
        if eventSource == "audio" and trial_id in self._analysis_open:
            raise AudioTimingError("audio event {} occurred during analysis for {}".format(event_type, trial_id))
        record = {
            "recordType": "m13_7_event",
            "schemaVersion": GOLDEN_SCHEMA_VERSION,
            "eventType": event_type,
            "sessionId": self.session_id if sessionId is None else str(sessionId),
            "trialId": trial_id,
            "sequence": self._event_sequence,
            "monotonicNs": int(self.monotonic_ns() if monotonicNs is None else monotonicNs),
            "utcTimestamp": self.utc_now() if utcTimestamp is None else str(utcTimestamp),
            "eventSource": str(eventSource),
            **values,
        }
        _append_jsonl(self.root / EVENTS_FILENAME, record)
        self._event_sequence += 1
        if event_type == "ANALYSIS_WINDOW_OPEN" and trial_id:
            self._analysis_open.add(trial_id)
        elif event_type == "ANALYSIS_WINDOW_CLOSE" and trial_id:
            self._analysis_open.discard(trial_id)
        return record

    def record_packet(self, packet: Nd8Packet, continuity: PacketContinuityRecord, experiment_monotonic_ns=None):
        if not isinstance(packet, Nd8Packet) or not isinstance(continuity, PacketContinuityRecord):
            raise TypeError("record_packet requires Nd8Packet and PacketContinuityRecord")
        raw = packet.raw_log_record()
        raw["recordType"] = "m13_7_raw_packet"
        raw["experimentMonotonicNs"] = int(
            packet.pc_receive_monotonic_ns if experiment_monotonic_ns is None else experiment_monotonic_ns
        )
        raw["sourceSampleIndex"] = int(continuity.cumulative_first_sample_index)
        raw["rawPreservation"] = "values_as_received_before_preprocessing"
        _append_jsonl(self.root / RAW_EEG_FILENAME, raw)
        metadata = {
            "recordType": "m13_7_packet_metadata",
            "schemaVersion": GOLDEN_SCHEMA_VERSION,
            "packet": packet.to_metadata().to_dict(),
            "continuity": continuity.to_dict(),
            "experimentMonotonicNs": raw["experimentMonotonicNs"],
        }
        _append_jsonl(self.root / PACKET_METADATA_FILENAME, metadata)
        if self.source_type == "live_nd8" and not self._real_eeg_marked:
            boundary = dict(self.manifest.get("hardwareBoundary", {}))
            boundary["newRealEegCollected"] = True
            self._touch_manifest(hardwareBoundary=boundary)
            self._real_eeg_marked = True
        return raw

    def record_decoder_evidence(self, trial_id, window_index, **values):
        if trial_id not in self._started_trials:
            raise ValueError("decoder evidence references an unknown trial")
        record = {
            "recordType": "m13_7_decoder_evidence",
            "schemaVersion": GOLDEN_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "trialId": trial_id,
            "windowIndex": int(window_index),
            "monotonicNs": int(self.monotonic_ns()),
            "utcTimestamp": self.utc_now(),
            **values,
        }
        _append_jsonl(self.root / DECODER_FILENAME, record)
        return record

    def record_context_evidence(self, trial_id, window_index, **values):
        if trial_id not in self._started_trials:
            raise ValueError("context evidence references an unknown trial")
        record = {
            "recordType": "m13_7_context_evidence",
            "schemaVersion": GOLDEN_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "trialId": trial_id,
            "windowIndex": int(window_index),
            "monotonicNs": int(self.monotonic_ns()),
            "utcTimestamp": self.utc_now(),
            **values,
        }
        _append_jsonl(self.root / CONTEXT_FILENAME, record)
        return record

    def record_action(self, trial_id=None, **values):
        if trial_id is not None and trial_id not in self._started_trials:
            raise ValueError("action references an unknown trial")
        record = {
            "recordType": "m13_7_action_telemetry",
            "schemaVersion": GOLDEN_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "trialId": trial_id,
            "monotonicNs": int(self.monotonic_ns()),
            "utcTimestamp": self.utc_now(),
            **values,
        }
        _append_jsonl(self.root / ACTIONS_FILENAME, record)
        return record

    def record_quest_event(self, value):
        if not isinstance(value, Mapping):
            raise TypeError("Quest event must be an object")
        record = {
            "recordType": "m13_7_quest_event",
            "schemaVersion": GOLDEN_SCHEMA_VERSION,
            "sessionId": self.session_id,
            "monotonicNs": int(self.monotonic_ns()),
            "utcTimestamp": self.utc_now(),
            **dict(value),
        }
        _append_jsonl(self.root / QUEST_FILENAME, record)
        return record

    def finalize(self):
        if self._analysis_open:
            raise AudioTimingError("cannot finalize while an analysis window is open")
        if self.manifest.get("status") == "finalized":
            return dict(self.manifest)
        self.record_event("SESSION_FINALIZED", eventSource="recorder", trialId=None)
        counts = {}
        for key, filename in REQUIRED_SESSION_FILES.items():
            path = self.root / filename
            counts[key] = len(_jsonl_read(path)) if path.is_file() else 0
        raw_path = self.root / RAW_EEG_FILENAME
        self._touch_manifest(
            status="finalized",
            finalizedUtc=self.utc_now(),
            recordCounts=counts,
            rawEegSha256=_hash_file(raw_path) if raw_path.is_file() else None,
        )
        return dict(self.manifest)

    # Explicit aliases make the live-source integration seam discoverable
    # without creating a second recording API.
    record_nd8_packet = record_packet
    record_trial_event = record_event
    close = finalize


class _RecorderM135Logger:
    """Adapter that preserves M13.5 event semantics inside the Golden session."""

    def __init__(self, recorder):
        self.recorder = recorder
        self.session_id = recorder.session_id

    def append(self, event_type, **values):
        trial_id = values.get("trialId")
        if event_type in ("window_evaluated", "final_submission", "shadow_hypothetical_decision", "trial_closed"):
            self.recorder.record_decoder_evidence(
                trial_id,
                values.get("windowIndex", -1),
                eventType=event_type,
                evidence=values,
            )
        return self.recorder.record_event(
            "M135_{}".format(str(event_type).upper()),
            trialId=trial_id,
            eventSource="m13_5_runtime",
            evidence=values,
        )


class _GoldenQuestTransport:
    """In-memory M8 transport double for the no-ND8 software run."""

    def __init__(self, candidates):
        self.candidates = tuple(candidates)
        self.submissions = []
        self.aborts = []

    def open_selection(self, selection_id):
        return {
            "protocolVersion": 1,
            "messageType": "selection_ack",
            "selectionId": selection_id,
            "accepted": True,
        }

    def submit_eeg_selection(self, selection_id, class_index):
        self.submissions.append((selection_id, int(class_index)))
        candidate = self.candidates[int(class_index)]
        return {
            "protocolVersion": 1,
            "messageType": "selection_ack",
            "selectionId": selection_id,
            "accepted": True,
            "resolvedTargetId": candidate.target_id,
            "resolvedLogicalBlockId": candidate.logical_block_id,
            "provenance": "no_nd8_dry_run_frozen_snapshot",
        }

    def abort_selection(self, selection_id):
        self.aborts.append(selection_id)
        return {
            "protocolVersion": 1,
            "messageType": "selection_ack",
            "selectionId": selection_id,
            "accepted": True,
        }


class _FakeClock:
    def __init__(self):
        self.ns = 1_000_000_000

    def monotonic_ns(self):
        return self.ns

    def sleep(self, seconds):
        self.ns += int(round(float(seconds) * 1_000_000_000.0))

    def utc_now(self):
        return "2026-01-01T00:00:{:02d}.{:06d}Z".format(
            (self.ns // 1_000_000_000) % 60,
            (self.ns // 1000) % 1_000_000,
        )


AUDIO_REHEARSAL_INTER_SLOT_SILENCE_SECONDS = 1.2


class _PerfCounterClock:
    """Wall-clock adapter used only by explicitly audible rehearsals."""

    monotonic_ns = staticmethod(time.perf_counter_ns)
    sleep = staticmethod(time.sleep)
    utc_now = staticmethod(_utc_now)


def _balanced_slots(count_per_class, seed, max_run=2):
    if count_per_class < 0:
        raise ValueError("count_per_class must be nonnegative")
    rng = random.Random(int(seed))
    remaining = {slot: int(count_per_class) for slot in range(3)}
    result = []
    while sum(remaining.values()):
        choices = [
            slot
            for slot, count in remaining.items()
            if count
            and (
                not result
                or result[-1] != slot
                or (len(result) > 1 and result[-2] != slot)
            )
        ]
        if not choices:
            raise ValueError("could not construct a balanced schedule under max_run")
        choices.sort(key=lambda slot: (-remaining[slot], rng.random()))
        selected = choices[0]
        result.append(selected)
        remaining[selected] -= 1
    return result


def _shuffle_with_max_run(items, key, rng, max_run=2):
    items = list(items)
    for _attempt in range(1000):
        candidate = list(items)
        rng.shuffle(candidate)
        run = 0
        previous = object()
        valid = True
        for item in candidate:
            value = key(item)
            run = run + 1 if value == previous else 1
            if run > max_run:
                valid = False
                break
            previous = value
        if valid:
            return candidate
    raise ValueError("could not randomize schedule under the maximum target run")


def _golden_trial_timing_breakdown(item, timing):
    beep_count = int(item["targetCueBeepCount"])
    target_cue = (
        0.0
        if beep_count == 0
        else beep_count * timing["targetBeepSeconds"]
        + (beep_count - 1) * timing["interTargetBeepSilenceSeconds"]
        + timing["targetGroupSeparationSeconds"]
    )
    components = {
        "targetCueSeconds": target_cue,
        "preparationSeconds": timing["preparationSeconds"],
        "readyBeepSeconds": timing["readyBeepSeconds"],
        "guaranteedSilenceBeforeStimulusSeconds": timing["guaranteedSilenceBeforeStimulusSeconds"],
        "stimulusAndAnalysisSeconds": timing["stimulusAndAnalysisSeconds"],
        "endBeepSeconds": timing["endBeepSeconds"],
        "interTrialRestSeconds": timing["interTrialRestSeconds"],
    }
    components["totalSeconds"] = sum(components.values())
    return {key: round(value, 3) for key, value in components.items()}


def build_golden_protocol(seed=20260917, session_id="m13.7-golden-v1"):
    """Build the exact, reproducible Golden v1 schedule and duration."""
    rng = random.Random(int(seed))
    timing = {
        "preparationSeconds": FORMAL_GOLDEN_PREPARATION_SECONDS,
        "targetFrequencyHz": AudioCueSystem.TARGET_BEEP_FREQUENCY_HZ,
        "targetBeepSeconds": AudioCueSystem.TARGET_BEEP_SECONDS,
        "interTargetBeepSilenceSeconds": AudioCueSystem.INTER_BEEP_SILENCE_SECONDS,
        "targetGroupSeparationSeconds": AudioCueSystem.TARGET_GROUP_SEPARATION_SECONDS,
        "readyFrequencyHz": AudioCueSystem.READY_BEEP_FREQUENCY_HZ,
        "readyBeepSeconds": AudioCueSystem.READY_BEEP_SECONDS,
        "guaranteedSilenceBeforeStimulusSeconds": 1.0,
        "stimulusAndAnalysisSeconds": 4.0,
        "endFrequencyHz": AudioCueSystem.END_BEEP_FREQUENCY_HZ,
        "endBeepSeconds": AudioCueSystem.END_BEEP_SECONDS,
        "interTrialRestSeconds": 2.0,
        "baselineSeconds": 120.0,
        "blockRestSeconds": 60.0,
        "taskRestSeconds": 45.0,
    }
    trials = []
    ordinal = 0

    def add(block, condition, slot, **values):
        nonlocal ordinal
        trial_id = "{}-trial-{:03d}".format(session_id, ordinal + 1)
        record = {
            "ordinal": ordinal + 1,
            "trialId": trial_id,
            "block": block,
            "condition": condition,
            "slot": slot,
            "frequencyHz": None if slot is None else SLOT_TO_FREQUENCY_HZ[slot],
            "targetCueBeepCount": 0 if slot is None else int(slot) + 1,
            **values,
        }
        trials.append(record)
        ordinal += 1

    for slot in _balanced_slots(20, rng.randrange(1_000_000)):
        add("static_ssvep_calibration", "static", slot)
    for slot in _balanced_slots(6, rng.randrange(1_000_000)):
        add("m13_active", "independent_active", slot)
    context_slots = [
        (relation, slot, repetition)
        for relation in ("aligned", "neutral", "conflict")
        for slot in range(3)
        for repetition in range(1, 4)
    ]
    context_slots = _shuffle_with_max_run(context_slots, lambda item: item[1], rng)
    for relation, slot, repetition in context_slots:
        add("context_conditions", relation, slot, repetition=repetition)
    for index in range(9):
        add(
            "no_intent_rejection",
            "no_intent",
            None,
            rejectionLabel="no_intent_{}".format(index + 1),
        )

    definitions = load_task_definitions()
    for task_id in ("house", "tower", "bridge"):
        definition = definitions[task_id]
        for repetition in range(1, 3):
            for step_index, logical_id in enumerate(definition.ordered_logical_block_ids):
                add(
                    "closed_loop_tasks",
                    task_id,
                    step_index % 3,
                    taskId=task_id,
                    taskName=definition.task_name,
                    repetition=repetition,
                    sequenceStep=step_index,
                    targetLogicalBlockId=logical_id,
                    preserveIndividualAction=True,
                )
    artifact_labels = ("blink_like", "jaw_like", "head_motion_like")
    for index in range(9):
        add(
            "controlled_artifact",
            "artifact_label_only",
            index % 3,
            artifactLabel=artifact_labels[index % len(artifact_labels)],
            instruction="label_only_prepare_now_do_not_perform_during_software_run",
        )

    block_order = [
        "static_ssvep_calibration",
        "m13_active",
        "context_conditions",
        "no_intent_rejection",
        "closed_loop_tasks",
        "controlled_artifact",
    ]
    rest_after_block = {
        "static_ssvep_calibration": timing["blockRestSeconds"],
        "m13_active": timing["blockRestSeconds"],
        "context_conditions": timing["blockRestSeconds"],
        "no_intent_rejection": timing["blockRestSeconds"],
        "closed_loop_tasks": timing["taskRestSeconds"],
        "controlled_artifact": 0.0,
    }
    projected = timing["baselineSeconds"]
    duration_blocks = [{
        "block": "channel_quality_baseline",
        "trialCount": 0,
        "trialDurationSeconds": 0.0,
        "restAfterSeconds": 0.0,
        "totalSeconds": timing["baselineSeconds"],
    }]
    normal_trial_seconds = 0.0
    rest_after_block_seconds = 0.0
    for block in block_order:
        block_trials = [item for item in trials if item["block"] == block]
        trial_breakdowns = [_golden_trial_timing_breakdown(item, timing) for item in block_trials]
        trial_duration = sum(item["totalSeconds"] for item in trial_breakdowns)
        block_rest = rest_after_block[block]
        block_total = trial_duration + block_rest
        projected += block_total
        normal_trial_seconds += trial_duration
        rest_after_block_seconds += block_rest
        duration_blocks.append({
            "block": block,
            "trialCount": len(block_trials),
            "trialDurationSeconds": round(trial_duration, 3),
            "restAfterSeconds": block_rest,
            "totalSeconds": round(block_total, 3),
        })
    return {
        "recordType": "m13_7_golden_protocol_v1",
        "schemaVersion": GOLDEN_SCHEMA_VERSION,
        "protocolVersion": GOLDEN_PROTOCOL_VERSION,
        "sessionId": session_id,
        "seed": int(seed),
        "frequenciesHz": [7.2, 9.0, 12.0],
        "slotMapping": {str(key): value for key, value in SLOT_TO_FREQUENCY_HZ.items()},
        "channelQuality": {
            "candidateChannels": 5,
            "minimumPassingChannels": 3,
            "baselineIsNormalTrial": False,
            "samplingRateHz": 1000,
            "preserveAllAvailableChannels": True,
        },
        "blocks": [
            {
                "block": "channel_quality_baseline",
                "trialCount": 0,
                "durationSeconds": timing["baselineSeconds"],
                "resumeBoundary": True,
            }
        ]
        + [
            {
                "block": block,
                "trialCount": sum(1 for item in trials if item["block"] == block),
                "restAfterSeconds": rest_after_block[block],
                "resumeBoundary": True,
            }
            for block in block_order
        ],
        "counts": {
            "staticCalibrationPerClass": 20,
            "staticCalibrationTotal": 60,
            "m13ActivePerClass": 6,
            "m13ActiveTotal": 18,
            "contextRelations": ["aligned", "neutral", "conflict"],
            "contextTotal": 27,
            "noIntentTotal": 9,
            "closedLoopSequencesPerTask": 2,
            "closedLoopTaskNames": ["House", "Tower", "Bridge"],
            "closedLoopIndividualSelectionSegments": 24,
            "controlledArtifactTotal": 9,
            "normalTrialSegmentTotal": len(trials),
        },
        "timing": timing,
        "projectedDurationSeconds": round(projected, 3),
        "projectedDurationMinutes": round(projected / 60.0, 2),
        "durationBreakdown": {
            "baselineSeconds": timing["baselineSeconds"],
            "normalTrialSeconds": round(normal_trial_seconds, 3),
            "restAfterBlockSeconds": round(rest_after_block_seconds, 3),
            "projectedDurationSeconds": round(projected, 3),
            "blocks": duration_blocks,
        },
        "restAndResume": {
            "resumeAtBlockBoundaries": True,
            "neverMergeRawSessions": True,
            "abortBehavior": "finalize partial session as inspectable; resume into a new sessionId unless explicit recorder resume is used",
            "artifactBlockPreparedButNotPerformed": True,
        },
        "audioContract": {
            "targetCue": "slot 0=1 beep, slot 1=2 beeps, slot 2=3 beeps; target beeps are separated by 280 ms and followed by 700 ms group separation",
            "readyCue": "one higher, longer readiness beep after target-group separation and silent preparation",
            "endCue": "one lower, longer end beep after the analysis window closes",
            "analysisWindowAudio": "forbidden",
            "arrivalBoundary": "software playback invocation is not acoustic arrival evidence",
        },
        "humanInstructionsSummary": "hear target cue count, remain silent during preparation/EEG analysis, attend the Quest stimulus, and hear one end cue after the window closes",
        "trials": trials,
        "generatedOrder": [item["trialId"] for item in trials],
    }


def validate_golden_protocol(protocol):
    if protocol.get("protocolVersion") != GOLDEN_PROTOCOL_VERSION:
        raise ValueError("unexpected Golden Protocol version")
    if tuple(protocol.get("frequenciesHz", ())) != (7.2, 9.0, 12.0):
        raise ValueError("Golden Protocol frequency mapping changed")
    trials = protocol.get("trials")
    if not isinstance(trials, list) or len(trials) != 147:
        raise ValueError("Golden Protocol v1 must contain 147 trial segments")
    trial_ids = [item.get("trialId") for item in trials]
    if len(set(trial_ids)) != len(trial_ids) or any(
        not isinstance(item, str) or not TRIAL_ID_RE.match(item) for item in trial_ids
    ):
        raise ValueError("Golden Protocol has duplicate or invalid trial IDs")
    for item in trials:
        slot = item.get("slot")
        if slot is not None and (
            slot not in SLOT_TO_FREQUENCY_HZ
            or item.get("frequencyHz") != SLOT_TO_FREQUENCY_HZ[slot]
            or item.get("targetCueBeepCount") != slot + 1
        ):
            raise ValueError("slot/frequency/cue mapping mismatch in {}".format(item.get("trialId")))
    static = [item for item in trials if item["block"] == "static_ssvep_calibration"]
    active = [item for item in trials if item["block"] == "m13_active"]
    context = [item for item in trials if item["block"] == "context_conditions"]
    no_intent = [item for item in trials if item["block"] == "no_intent_rejection"]
    closed = [item for item in trials if item["block"] == "closed_loop_tasks"]
    artifacts = [item for item in trials if item["block"] == "controlled_artifact"]
    if any(sum(item["slot"] == slot for item in static) != 20 for slot in range(3)):
        raise ValueError("static calibration is not 20 per class")
    if any(sum(item["slot"] == slot for item in active) != 6 for slot in range(3)):
        raise ValueError("M13 active block is not 6 per class")
    if len(context) != 27 or len(no_intent) != 9 or len(closed) != 24 or len(artifacts) != 9:
        raise ValueError("Golden Protocol block counts are inconsistent")
    if any(item["block"] != "no_intent_rejection" and item["slot"] is None for item in trials):
        raise ValueError("only no-intent trials may omit an SSVEP slot")
    timing = protocol.get("timing")
    if not isinstance(timing, dict):
        raise ValueError("Golden Protocol timing is missing")
    preparation_seconds = float(timing.get("preparationSeconds", -1.0))
    if not math.isclose(preparation_seconds, FORMAL_GOLDEN_PREPARATION_SECONDS):
        raise ValueError("formal Golden preparationSeconds must be 6.0")
    required_timing = (
        "targetBeepSeconds",
        "interTargetBeepSilenceSeconds",
        "targetGroupSeparationSeconds",
        "readyBeepSeconds",
        "guaranteedSilenceBeforeStimulusSeconds",
        "stimulusAndAnalysisSeconds",
        "endBeepSeconds",
        "interTrialRestSeconds",
    )
    if any(key not in timing for key in required_timing):
        raise ValueError("Golden Protocol timing is incomplete")
    duration_breakdown = protocol.get("durationBreakdown")
    if not isinstance(duration_breakdown, dict):
        raise ValueError("Golden Protocol durationBreakdown is missing")
    expected_normal = round(
        sum(_golden_trial_timing_breakdown(item, timing)["totalSeconds"] for item in trials),
        3,
    )
    expected_block_rests = {
        "static_ssvep_calibration": float(timing["blockRestSeconds"]),
        "m13_active": float(timing["blockRestSeconds"]),
        "context_conditions": float(timing["blockRestSeconds"]),
        "no_intent_rejection": float(timing["blockRestSeconds"]),
        "closed_loop_tasks": float(timing["taskRestSeconds"]),
        "controlled_artifact": 0.0,
    }
    block_rows = {row.get("block"): row for row in duration_breakdown.get("blocks", [])}
    expected_rest_total = 0.0
    for block, expected_rest in expected_block_rests.items():
        row = block_rows.get(block)
        if row is None or not math.isclose(float(row.get("restAfterSeconds", -1.0)), expected_rest):
            raise ValueError("durationBreakdown rest is not derived from timing for {}".format(block))
        expected_rest_total += expected_rest
    expected_total = round(float(timing["baselineSeconds"]) + expected_normal + expected_rest_total, 3)
    if not math.isclose(float(duration_breakdown.get("normalTrialSeconds", -1.0)), expected_normal):
        raise ValueError("durationBreakdown normalTrialSeconds is not derived from timing")
    if not math.isclose(float(duration_breakdown.get("restAfterBlockSeconds", -1.0)), expected_rest_total):
        raise ValueError("durationBreakdown restAfterBlockSeconds is not derived from timing")
    if not math.isclose(float(protocol.get("projectedDurationSeconds", -1.0)), expected_total):
        raise ValueError("projected duration is not derived from timing")
    if not math.isclose(float(duration_breakdown.get("projectedDurationSeconds", -1.0)), expected_total):
        raise ValueError("durationBreakdown projected duration is not derived from timing")
    for block in ("static_ssvep_calibration", "m13_active", "context_conditions", "closed_loop_tasks", "controlled_artifact"):
        block_slots = [item["slot"] for item in trials if item["block"] == block]
        longest = 0
        previous_slot = None
        run_length = 0
        for slot in block_slots:
            run_length = run_length + 1 if slot == previous_slot else 1
            previous_slot = slot
            longest = max(longest, run_length)
        if longest > 2:
            raise ValueError("{} contains a pathological target run".format(block))
    return True


def verify_session(root, expected_block_ids=None, allow_partial_session=False):
    """Strictly verify a Golden Session or an explicitly declared slice."""
    root = Path(root)
    checks = []

    def check(name, passed, detail):
        checks.append({"name": name, "status": "PASS" if passed else "FAIL", "detail": detail})

    manifest = None
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        check("manifest_present_and_parseable", isinstance(manifest, dict), "manifest.json is an object")
    except (OSError, json.JSONDecodeError) as error:
        check("manifest_present_and_parseable", False, str(error))
        return {"status": "FAIL", "overallStatus": "FAIL", "checks": checks, "sessionRoot": str(root)}

    if expected_block_ids is None and manifest.get("sessionCategory") == "GOLDEN_CONTINUATION":
        expected_block_ids = manifest.get("executionBlockIds", [])
        allow_partial_session = True
    scope_block_ids = None if expected_block_ids is None else {str(item) for item in expected_block_ids}

    parsed = {}
    for key, filename in REQUIRED_SESSION_FILES.items():
        path = root / manifest.get(key, filename)
        try:
            parsed[key] = _jsonl_read(path)
            check("file_{}".format(key), True, "{} records".format(len(parsed[key])))
        except (OSError, ValueError) as error:
            parsed[key] = []
            check("file_{}".format(key), False, str(error))

    raw = parsed["rawEegFile"]
    metadata = parsed["packetMetadataFile"]
    events = parsed["eventFile"]
    check("raw_stream_present", bool(raw), "raw packet stream is non-empty")
    check(
        "raw_packets_valid",
        all(
            item.get("recordType") in ("m13_7_raw_packet", "nd8_raw_packet")
            and len(item.get("samples", [])) == 8
            and all(isinstance(channel, list) and channel for channel in item.get("samples", []))
            and all(
                math.isfinite(float(value))
                for channel in item.get("samples", [])
                for value in channel
            )
            for item in raw
        ),
        "raw packets retain 8 finite channels",
    )
    sequences = [item.get("packetSequence") for item in raw]
    check("raw_packet_sequence", sequences == list(range(len(sequences))), "packet sequences are contiguous from zero")
    check("raw_metadata_reference", len(metadata) == len(raw), "one metadata record per raw packet")

    event_times = [
        item.get("monotonicNs")
        for item in events
        if item.get("eventType") != "TRIAL_SAMPLE_ANCHOR"
    ]
    check(
        "event_timing_monotonic",
        all(isinstance(value, int) and value >= 0 for value in event_times)
        and event_times == sorted(event_times),
        "event emission timestamps are nondecreasing; packet receive time is retained separately on sample anchors",
    )
    starts = [item for item in events if item.get("eventType") == "TRIAL_STARTED"]
    started_ids = [item.get("trialId") for item in starts]
    scoped_starts = [
        item for item in starts
        if scope_block_ids is None or item.get("protocolBlock") in scope_block_ids
    ]
    scoped_started_ids = [item.get("trialId") for item in scoped_starts]
    check(
        "trial_ids_unique_valid",
        len(started_ids) == len(set(started_ids))
        and all(isinstance(item, str) and TRIAL_ID_RE.match(item) for item in started_ids),
        "trial IDs are unique and valid",
    )
    known_ids = set(started_ids)
    references_valid = all(item.get("trialId") is None or item.get("trialId") in known_ids for item in events)
    check("event_trial_references", references_valid, "events reference known trial IDs")

    mapping_valid = True
    audio_guard = True
    ordering_valid = True
    complete_chains = True
    anchor_binding_valid = True
    anchor_records_present = False
    for trial_id in scoped_started_ids:
        trial_events = [item for item in events if item.get("trialId") == trial_id]
        type_names = [item.get("eventType") for item in trial_events]
        positions = {name: type_names.index(name) for name in set(type_names)}
        required = (
            "TARGET_CUE_START",
            "TARGET_CUE_END",
            "READY_CUE_START",
            "READY_CUE_END",
            "STIMULUS_ONSET",
            "ANALYSIS_WINDOW_OPEN",
            "ANALYSIS_WINDOW_CLOSE",
            "STIMULUS_OFFSET",
            "END_CUE_START",
            "END_CUE_END",
        )
        complete_chains = complete_chains and all(name in positions for name in required)
        ordering_valid = ordering_valid and all(
            positions[a] < positions[b]
            for a, b in (
                ("TARGET_CUE_START", "TARGET_CUE_END"),
                ("TARGET_CUE_END", "READY_CUE_START"),
                ("READY_CUE_START", "READY_CUE_END"),
                ("READY_CUE_END", "STIMULUS_ONSET"),
                ("STIMULUS_ONSET", "ANALYSIS_WINDOW_OPEN"),
                ("ANALYSIS_WINDOW_OPEN", "ANALYSIS_WINDOW_CLOSE"),
                ("ANALYSIS_WINDOW_CLOSE", "STIMULUS_OFFSET"),
                ("STIMULUS_OFFSET", "END_CUE_START"),
                ("END_CUE_START", "END_CUE_END"),
            )
            if a in positions and b in positions
        )
        if "TRIAL_PREPARATION_STARTED" in positions and "TRIAL_PREPARATION_FINISHED" in positions:
            ordering_valid = ordering_valid and all(
                positions[a] < positions[b]
                for a, b in (
                    ("TRIAL_PREPARATION_STARTED", "TARGET_CUE_START"),
                    ("TARGET_CUE_END", "TRIAL_PREPARATION_FINISHED"),
                    ("TRIAL_PREPARATION_FINISHED", "READY_CUE_START"),
                )
            )
        target = next((item for item in trial_events if item.get("eventType") == "TARGET_CUE_START"), {})
        slot = target.get("slot")
        beep_count = target.get("beepCount")
        if slot is None:
            mapping_valid = mapping_valid and beep_count == 0
        else:
            mapping_valid = (
                mapping_valid
                and slot in SLOT_TO_FREQUENCY_HZ
                and target.get("frequencyHz") == SLOT_TO_FREQUENCY_HZ[slot]
                and beep_count == slot + 1
            )
        ready_end = next((item for item in trial_events if item.get("eventType") == "READY_CUE_END"), None)
        onset = next((item for item in trial_events if item.get("eventType") == "STIMULUS_ONSET"), None)
        if ready_end and onset:
            silence = (onset["monotonicNs"] - ready_end["monotonicNs"]) / 1_000_000_000.0
            ordering_valid = ordering_valid and silence + 1e-9 >= 1.0
        open_event = next((item for item in trial_events if item.get("eventType") == "ANALYSIS_WINDOW_OPEN"), None)
        close_event = next((item for item in trial_events if item.get("eventType") == "ANALYSIS_WINDOW_CLOSE"), None)
        if open_event and close_event:
            audio_guard = audio_guard and not any(
                item.get("eventSource") == "audio"
                and open_event["monotonicNs"] <= item.get("monotonicNs", -1) < close_event["monotonicNs"]
                for item in trial_events
            )
        anchor = next((item for item in trial_events if item.get("eventType") == "TRIAL_SAMPLE_ANCHOR"), None)
        if anchor is not None:
            anchor_records_present = True
            anchor_binding_valid = anchor_binding_valid and (
                onset is not None
                and anchor.get("stimulusOnsetEventSequence") == onset.get("sequence")
                and anchor.get("stimulusOnsetMonotonicNs") == onset.get("monotonicNs")
                and anchor.get("sampleAnchorSampleIndex") == anchor.get("sourceSampleIndex")
                and anchor.get("anchorBasis") == "first_packet_received_at_or_after_stimulus_onset"
            )
    check("trial_event_chains_complete", complete_chains, "every started trial has the required lifecycle chain")
    check("stimulus_analysis_ordering", ordering_valid, "ready/silence/stimulus/analysis/offset order is valid")
    check("audio_not_inside_analysis", audio_guard, "no audio event occurs in an open analysis interval")
    check("slot_frequency_mapping", mapping_valid, "recorded cue metadata preserves 7.2/9/12 mapping")
    check(
        "sample_anchor_onset_binding",
        not anchor_records_present or anchor_binding_valid,
        "when present, each sample anchor names the actual STIMULUS_ONSET event and packet sample",
    )
    scope_complete = True
    if scope_block_ids is not None:
        completed_blocks = set(manifest.get("completedBlocks", []))
        scope_complete = scope_block_ids.issubset(completed_blocks)
        check(
            "declared_scope_complete",
            scope_complete,
            "declared block slice is complete: expected={} completed={}".format(
                sorted(scope_block_ids), sorted(completed_blocks)
            ),
        )
    quest_integration = manifest.get("questIntegration", {})
    quest_events = parsed["questEventsFile"]
    expected_quest_trial_ids = set(quest_integration.get("expectedSelectionTrialIds", []))
    accepted_quest_events = {
        (item.get("trialId"), item.get("eventType"))
        for item in quest_events
        if item.get("status") == "quest_accepted"
    }
    quest_evidence_valid = (
        not quest_integration.get("required")
        or (
            bool(expected_quest_trial_ids)
            and
            manifest.get("hardwareBoundary", {}).get("questOperated") is True
            and quest_integration.get("mode") == "m8_selection_tcp"
            and quest_integration.get("connectionAccepted") is True
            and int(quest_integration.get("ackCount", 0)) >= 2 * len(expected_quest_trial_ids)
            and all(
                (trial_id, "selection_open_ack") in accepted_quest_events and
                (trial_id, "eeg_selection_ack") in accepted_quest_events
                for trial_id in expected_quest_trial_ids
            )
        )
    )
    check(
        "quest_integration_evidence",
        quest_evidence_valid,
        "required Quest preflight has a real M8 TCP connection and accepted open/terminal ACKs",
    )
    strict_finalized = (
        manifest.get("status") == "finalized"
        and any(item.get("eventType") == "SESSION_FINALIZED" for item in events)
    )
    finalized_ok = strict_finalized or (allow_partial_session and scope_complete)
    check(
        "finalized",
        finalized_ok,
        "session has a durable finalization marker"
        if strict_finalized
        else "declared slice is complete; session contains an explicitly excluded partial remainder",
    )
    check(
        "hardware_boundary",
        (
            not any(
                bool(manifest.get("hardwareBoundary", {}).get(key))
                for key in (
                    "questOperated",
                    "nd8Operated",
                    "com11Opened",
                    "newRealEegCollected",
                    "physicalRobotOperated",
                )
            )
            or manifest.get("sourceType") not in ("synthetic_nd8_replay", "recorded_replay")
            or (
                manifest.get("sourceType") in ("synthetic_nd8_replay", "recorded_replay")
                and manifest.get("hardwareBoundary", {}).get("questOperated") is True
                and quest_integration.get("required") is True
                and quest_evidence_valid
                and not any(
                    bool(manifest.get("hardwareBoundary", {}).get(key))
                    for key in (
                        "nd8Operated",
                        "com11Opened",
                        "newRealEegCollected",
                        "physicalRobotOperated",
                    )
                )
            )
        ),
        "hardware boundary is explicit",
    )
    passed = all(item["status"] == "PASS" for item in checks)
    return {
        "status": "PASS" if passed else "FAIL",
        "overallStatus": "PASS" if passed else "FAIL",
        "sessionRoot": str(root),
        "sessionId": manifest.get("sessionId"),
        "manifestStatus": manifest.get("status"),
        "checks": checks,
        "rawPacketCount": len(raw),
        "trialCount": len(starts),
        "scopedTrialCount": len(scoped_started_ids),
        "scopeBlockIds": None if scope_block_ids is None else sorted(scope_block_ids),
        "sourceType": manifest.get("sourceType"),
    }


def _make_synthetic_source(
    root,
    trial_specs,
    sampling_rate_hz=1000.0,
    packet_size=200,
    stimulus_seconds=4.0,
):
    """Create a small source fixture used only by the no-ND8 dry-run."""
    clock = _FakeClock()
    recorder = HumanSessionRecorder(
        root,
        "m13.7-dry-source",
        "synthetic_fixture",
        protocol_version="m13.7-dry-source-v1",
        configuration={"purpose": "no_nd8_fixture_source", "packetSize": packet_size},
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
    )
    timeline_next_sample = 0
    packet_sequence = 0
    ranges = []
    for spec in trial_specs:
        start_sequence = packet_sequence
        start_sample = timeline_next_sample
        frequency = None if spec.get("slot") is None else SLOT_TO_FREQUENCY_HZ[spec["slot"]]
        sample_count = int(round(float(stimulus_seconds) * sampling_rate_hz))
        t = np.arange(sample_count, dtype=float) / sampling_rate_hz
        signal = np.zeros(sample_count, dtype=float) if frequency is None else np.sin(2.0 * np.pi * frequency * t)
        values = np.vstack([signal * (1.0 - 0.03 * channel) for channel in range(8)])
        for offset in range(0, sample_count, packet_size):
            chunk = values[:, offset : offset + packet_size]
            packet = Nd8Packet.from_sdk_payload(
                {
                    "timestamp": (timeline_next_sample / sampling_rate_hz) * 1000.0,
                    "data": chunk.tolist(),
                },
                packet_sequence=packet_sequence,
                nominal_sampling_rate_hz=sampling_rate_hz,
                receive_monotonic_ns=1_000_000_000 + int(timeline_next_sample / sampling_rate_hz * 1_000_000_000),
                receive_utc=clock.utc_now(),
            )
            continuity = PacketContinuityRecord(
                packet_sequence,
                timeline_next_sample,
                "initial" if packet_sequence == 0 else "continuous",
                (),
            )
            recorder.record_packet(packet, continuity)
            packet_sequence += 1
            timeline_next_sample += chunk.shape[1]
        ranges.append(
            {
                "trialId": spec["trialId"],
                "startPacketSequence": start_sequence,
                "endPacketSequence": packet_sequence - 1,
                "startSample": start_sample,
            }
        )
    recorder._touch_manifest(replayRanges=ranges)
    recorder.finalize()
    return ranges


def _context_weights(condition, expected_slot):
    if condition == "aligned":
        values = [0.02, 0.02, 0.02]
        values[expected_slot] = 0.94
        return tuple(values)
    if condition == "conflict":
        conflicting = (expected_slot + 1) % 3
        values = [0.02, 0.02, 0.02]
        values[conflicting] = 0.94
        return tuple(values)
    return (0.25, 0.25, 0.25)


def run_no_nd8_dry_run(output_dir, include_mujoco=False):
    """Run replay samples through DecoderBackend -> M12 -> M13.5 -> M8."""
    output_dir = Path(output_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError("dry-run output directory must be new: {}".format(output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    protocol_timing = build_golden_protocol()["timing"]
    trial_specs = [
        {"trialId": "m13.7-dry-trial-001", "slot": 0, "condition": "aligned", "expected": "decision"},
        {"trialId": "m13.7-dry-trial-002", "slot": 1, "condition": "neutral", "expected": "decision"},
        {"trialId": "m13.7-dry-trial-003", "slot": 2, "condition": "conflict", "expected": "conflict_decision"},
        {"trialId": "m13.7-dry-trial-004", "slot": None, "condition": "no_intent", "expected": "no_decision"},
    ]
    source_root = output_dir / "source-fixture"
    ranges = _make_synthetic_source(
        source_root,
        trial_specs,
        stimulus_seconds=float(protocol_timing["stimulusAndAnalysisSeconds"]),
    )
    range_by_id = {item["trialId"]: item for item in ranges}
    clock = _FakeClock()
    session_root = output_dir / "golden-session"
    recorder = HumanSessionRecorder(
        session_root,
        "m13.7-dry-run-session",
        "synthetic_nd8_replay",
        configuration={
            "decoder": "numpy_fbcca",
            "selectedChannels": [0, 1, 2, 3, 4],
            "onsetGuardSeconds": 0.5,
            "analysisWindowSeconds": 1.5,
            "cueTiming": dict(protocol_timing),
            "formalProtocolTiming": dict(protocol_timing),
            "executionTimingMode": "protocol",
            "stopping": "existing M13.5 active runtime",
            "sourceManifest": str(source_root / "manifest.json"),
        },
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
    )
    cue_backend = NullLoggingAudioBackend(clock.monotonic_ns)
    cue = AudioCueSystem(
        cue_backend,
        recorder.record_event,
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
        sleep=clock.sleep,
        required_silence_seconds=float(protocol_timing["guaranteedSilenceBeforeStimulusSeconds"]),
        timing_configuration=protocol_timing,
    )
    candidates = build_default_active_candidates()
    transport = _GoldenQuestTransport(candidates)
    quest = M8SelectionOrchestrator(transport, event_sink=recorder.record_quest_event)
    logger = _RecorderM135Logger(recorder)
    backend = DecoderBackend("numpy_fbcca")
    buffer = RollingEegBuffer()
    replay = RecordedND8Replay(source_root, speed="max")
    current = None
    runtime = None
    selection_id = None
    next_window = 0
    onset_events = {}
    current_anchor_sample = None
    results = []
    range_index = 0

    def start_trial(spec):
        nonlocal current, runtime, selection_id, next_window, current_anchor_sample
        current = spec
        selection_id = spec["trialId"] + "-selection"
        slot = spec["slot"]
        recorder.record_event(
            "TRIAL_STARTED",
            trialId=spec["trialId"],
            eventSource="orchestrator",
            block="dry_run",
            condition=spec["condition"],
            slot=slot,
            frequencyHz=None if slot is None else SLOT_TO_FREQUENCY_HZ[slot],
            expectedOutcome=spec["expected"],
        )
        recorder.record_event(
            "TRIAL_PREPARATION_STARTED",
            trialId=spec["trialId"],
            eventSource="orchestrator",
            preparationSeconds=float(protocol_timing["preparationSeconds"]),
        )
        cue.target_cue(
            spec["trialId"],
            recorder.session_id,
            slot,
            None if slot is None else SLOT_TO_FREQUENCY_HZ[slot],
        )
        wait_for_timeline_duration(clock, float(protocol_timing["preparationSeconds"]))
        recorder.record_event(
            "TRIAL_PREPARATION_FINISHED",
            trialId=spec["trialId"],
            eventSource="orchestrator",
            preparationSeconds=float(protocol_timing["preparationSeconds"]),
        )
        cue.ready_cue(spec["trialId"], recorder.session_id)
        cue.wait_for_stimulus_silence()
        onset_events[spec["trialId"]] = cue.stimulus_onset(
            spec["trialId"],
            recorder.session_id,
            slot,
            None if slot is None else SLOT_TO_FREQUENCY_HZ[slot],
            spec["condition"],
        )
        runtime = M135TrialRuntime(MODE_ACTIVE, quest, logger)
        if not runtime.start_trial(selection_id, spec["trialId"]):
            raise RuntimeError("dry-run Quest selection open rejected")
        next_window = 0
        current_anchor_sample = None

    def finish_trial():
        nonlocal current, runtime, current_anchor_sample
        cue.stimulus_offset(current["trialId"], recorder.session_id)
        result = runtime.finalize_trial()
        outcome = result.to_public_dict()
        cue.end_cue(
            current["trialId"],
            recorder.session_id,
            outcome="decision"
            if outcome["m13Decision"] and outcome["m13Decision"]["decisionMade"]
            else "no_decision",
        )
        recorder.record_action(
            current["trialId"],
            actionType="selection_to_task_boundary",
            decision=outcome,
            robotBackend="not_invoked_for_selection_smoke",
        )
        recorder.record_event(
            "TRIAL_FINISHED",
            trialId=current["trialId"],
            eventSource="orchestrator",
            outcome=outcome,
        )
        results.append({"trialId": current["trialId"], "expected": current["expected"], "result": outcome})
        current = None
        runtime = None

    for packet, continuity in replay.iter_packets():
        spec = trial_specs[range_index]
        packet_range = range_by_id[spec["trialId"]]
        if packet.packet_sequence == packet_range["startPacketSequence"]:
            start_trial(spec)
        recorder.record_packet(packet, continuity)
        if packet.packet_sequence == packet_range["startPacketSequence"]:
            onset_event = onset_events[current["trialId"]]
            anchor_event = recorder.record_event(
                "TRIAL_SAMPLE_ANCHOR",
                trialId=current["trialId"],
                eventSource="acquisition",
                packetSequence=packet.packet_sequence,
                sourceSampleIndex=continuity.cumulative_first_sample_index,
                continuityStatus=continuity.status,
                stimulusOnsetMonotonicNs=onset_event["monotonicNs"],
                stimulusOnsetEventSequence=onset_event["sequence"],
                anchorBasis="first_packet_received_at_or_after_stimulus_onset",
                anchorPacketReceiveMonotonicNs=packet.pc_receive_monotonic_ns,
                sampleAnchorSampleIndex=continuity.cumulative_first_sample_index,
            )
            current_anchor_sample = int(anchor_event["sampleAnchorSampleIndex"])
        buffer.append(
            ReplayPacket(
                np.asarray(packet.samples, dtype=float),
                continuity.cumulative_first_sample_index,
                packet.packet_sequence,
                packet.pc_receive_monotonic_ns,
                continuity.status,
            )
        )
        if current["slot"] is not None:
            start_sample = current_anchor_sample
            while next_window < 11:
                stop = start_sample + 500 + 1500 + next_window * 200
                if buffer.stop_sample < stop:
                    break
                first = start_sample + 500 + next_window * 200
                epoch = buffer.window(first, stop)
                index, scores = backend.predict(epoch[[0, 1, 2, 3, 4]])
                context_prior = fixture_context_prior(_context_weights(current["condition"], current["slot"]))
                evidence = fuse_context_and_eeg(
                    context_prior,
                    candidates,
                    {item.logical_block_id: score for item, score in zip(candidates, scores)},
                )
                snapshot = DynamicStoppingSnapshot.from_m12(
                    next_window,
                    evidence,
                    1.5,
                    2.0 + next_window * 0.2,
                    {
                        "source": "RecordedND8Replay",
                        "decoder": "numpy_fbcca",
                        "packetSequence": packet.packet_sequence,
                    },
                )
                recorder.record_decoder_evidence(
                    current["trialId"],
                    next_window,
                    sourceType="recorded_nd8_replay",
                    decoder="numpy_fbcca",
                    rawScores=list(scores),
                    predictedClass=index,
                    snapshot=snapshot.to_public_dict(),
                )
                recorder.record_context_evidence(
                    current["trialId"],
                    next_window,
                    condition=current["condition"],
                    contextPrior=context_prior.to_public_dict(),
                    fusedEvidence=evidence.to_public_dict(),
                )
                runtime.observe(snapshot, current["trialId"], selection_id)
                next_window += 1
        if packet.packet_sequence == packet_range["endPacketSequence"]:
            wait_for_timeline_duration(clock, float(protocol_timing["stimulusAndAnalysisSeconds"]))
            finish_trial()
            range_index += 1
            if range_index == len(trial_specs):
                break

    task_records = []
    from integration.m14_sequential_closed_loop import M14SequentialEpisodeRunner, SyntheticRobotAdapter

    definitions = load_task_definitions()
    for task_id in ("house", "tower", "bridge"):
        for repetition in (1, 2):
            episode = M14SequentialEpisodeRunner(
                SyntheticRobotAdapter(),
                source_type="M13.7_DRY_RUN_SYNTHETIC",
            ).run_episode(
                definitions[task_id],
                evidence_prefix="m13.7-{}-{}".format(task_id, repetition),
            )
            task_records.append(
                {
                    "taskId": task_id,
                    "repetition": repetition,
                    "episodeOutcome": episode["episodeOutcome"],
                    "steps": len(episode["steps"]),
                    "successfulRobotExecutionCount": episode["successfulRobotExecutionCount"],
                    "backend": "synthetic_robot_adapter",
                }
            )
            for step in episode["steps"]:
                recorder.record_action(
                    None,
                    actionType="m14_closed_loop_selection_and_action",
                    taskId=task_id,
                    repetition=repetition,
                    stepIndex=step["stepIndex"],
                    logicalBlockId=step["selection"]["m13FinalDecision"].get("selectedLogicalBlockId"),
                    robot=step["robot"],
                    telemetryAssociation={
                        "source": "existing M14 runner",
                        "mujocoInvoked": False,
                    },
                )

    if include_mujoco:
        from integration.m14_sequential_closed_loop import run_mujoco_acceptance

        mujoco_summary, _ = run_mujoco_acceptance()
    else:
        mujoco_summary = {
            "overallStatus": "NOT_ATTEMPTED",
            "reason": "optional MuJoCo pass omitted from default dry-run",
        }
    recorder._touch_manifest(
        audioCue=cue.summary(),
        dryRun={
            "sourceSession": str(source_root),
            "trialResults": results,
            "closedLoopTasks": task_records,
            "mujoco": mujoco_summary,
        },
    )
    manifest = recorder.finalize()
    verification = verify_session(session_root)
    replay_again = RecordedND8Replay(session_root).replay(lambda packet, continuity: None)
    summary = {
        "recordType": "m13_7_no_nd8_end_to_end_dry_run",
        "status": "PASS"
        if verification["status"] == "PASS"
        and replay_again["packetCount"] == len(_jsonl_read(session_root / RAW_EEG_FILENAME))
        else "FAIL",
        "hardwareBoundary": manifest["hardwareBoundary"],
        "sourceReplay": replay.replay(lambda packet, continuity: None),
        "trialResults": results,
        "closedLoopTasks": task_records,
        "mujoco": mujoco_summary,
        "sessionRoot": str(session_root),
        "verification": verification,
        "replayResult": replay_again,
        "audio": cue.summary(),
        "protocol": {
            "version": GOLDEN_PROTOCOL_VERSION,
            "threeClassesExercised": sorted({item["slot"] for item in trial_specs if item["slot"] is not None}),
            "rejectionPathExercised": any(item["expected"] == "no_decision" and not item["result"]["m13Decision"]["decisionMade"] for item in results),
            "noIntentPathExercised": any(item["condition"] == "no_intent" for item in trial_specs),
            "contextConditionsExercised": sorted({item["condition"] for item in trial_specs}),
        },
    }
    _atomic_json(output_dir / "dry-run-summary.json", summary)
    return summary


def run_audio_rehearsal(output_path=None, audible=False, clock=None):
    """Rehearse the complete cue lifecycle, silently by default or via PC audio."""
    audible = bool(audible)
    clock = clock or (_PerfCounterClock() if audible else _FakeClock())
    events = []
    backend = PcToneAudioBackend() if audible else NullLoggingAudioBackend(clock.monotonic_ns)

    def sink(event_type, **values):
        events.append(
            {
                "eventType": event_type,
                "monotonicNs": int(clock.monotonic_ns()),
                "utcTimestamp": clock.utc_now(),
                **values,
            }
        )

    cue = AudioCueSystem(
        backend,
        sink,
        monotonic_ns=clock.monotonic_ns,
        utc_now=clock.utc_now,
        sleep=clock.sleep,
        fail_on_backend_error=audible,
    )
    for slot in range(3):
        trial_id = "rehearsal-{}".format(slot)
        frequency_hz = SLOT_TO_FREQUENCY_HZ[slot]
        sink(
            "TRIAL_PREPARATION_STARTED",
            trialId=trial_id,
            sessionId="rehearsal",
            eventSource="orchestrator",
            slot=slot,
            frequencyHz=frequency_hz,
            preparationSeconds=1.0,
        )
        cue.target_cue(trial_id, "rehearsal", slot, frequency_hz)
        clock.sleep(1.0)
        sink(
            "TRIAL_PREPARATION_FINISHED",
            trialId=trial_id,
            sessionId="rehearsal",
            eventSource="orchestrator",
            slot=slot,
            frequencyHz=frequency_hz,
        )
        cue.ready_cue(trial_id, "rehearsal")
        cue.wait_for_stimulus_silence()
        cue.stimulus_onset(trial_id, "rehearsal", slot, frequency_hz, "audio_rehearsal")
        clock.sleep(1.0)
        cue.stimulus_offset(trial_id, "rehearsal", reason="rehearsal_analysis_interval_complete")
        cue.end_cue(trial_id, "rehearsal", "rehearsal_complete")
        if slot < 2:
            clock.sleep(AUDIO_REHEARSAL_INTER_SLOT_SILENCE_SECONDS)
    result = {
        "status": "PASS",
        "mode": "audible_pc" if audible else "silent",
        "interSlotSilenceSeconds": AUDIO_REHEARSAL_INTER_SLOT_SILENCE_SECONDS,
        "mapping": {str(key): value for key, value in SLOT_TO_FREQUENCY_HZ.items()},
        "backend": cue.summary(),
        "events": events,
        "hardwareBoundary": {
            "questOperated": False,
            "nd8Operated": False,
            "com11Opened": False,
        },
    }
    if output_path is not None:
        _atomic_json(output_path, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    rehearsal = subparsers.add_parser("rehearsal")
    rehearsal.add_argument("--output", type=Path, required=True)
    rehearsal.add_argument(
        "--audible",
        action="store_true",
        help="play the rehearsal through the Windows PC speaker; default is silent and deterministic",
    )
    protocol = subparsers.add_parser("protocol")
    protocol.add_argument("--output-dir", type=Path, required=True)
    protocol.add_argument("--seed", type=int, default=20260917)
    protocol.add_argument("--session-id", default="m13.7-golden-v1")
    dry = subparsers.add_parser("dry-run")
    dry.add_argument("--output-dir", type=Path, required=True)
    dry.add_argument("--with-mujoco", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--session", type=Path, required=True)
    replay = subparsers.add_parser("replay")
    replay.add_argument("--session", type=Path, required=True)
    replay.add_argument("--speed", default="max")
    live = subparsers.add_parser("live-command")
    live.add_argument("--data-root", type=Path, required=True)
    live.add_argument("--schedule", type=Path, required=True)
    live.add_argument("--com", default="COM11")
    args = parser.parse_args(argv)
    if args.command == "rehearsal":
        result = run_audio_rehearsal(args.output, audible=args.audible)
        print(
            json.dumps(
                {"status": result["status"], "mode": result["mode"], "output": str(args.output)},
                sort_keys=True,
            )
        )
        return 0
    if args.command == "protocol":
        protocol_value = build_golden_protocol(args.seed, args.session_id)
        validate_golden_protocol(protocol_value)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(args.output_dir / "M13_7_Golden_Protocol_v1.json", protocol_value)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "trials": len(protocol_value["trials"]),
                    "projectedDurationMinutes": protocol_value["projectedDurationMinutes"],
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "dry-run":
        result = run_no_nd8_dry_run(args.output_dir, args.with_mujoco)
        print(json.dumps({"status": result["status"], "sessionRoot": result["sessionRoot"]}, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "verify":
        result = verify_session(args.session)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "replay":
        result = RecordedND8Replay(args.session, speed=args.speed).replay(lambda packet, continuity: None)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "live-command":
        if not re.fullmatch(r"COM[1-9][0-9]*", str(args.com).upper()):
            parser.error("live launch requires an explicit Windows COM port such as COM3")
        command = [
            sys.executable,
            "-B",
            "-m",
            "integration.m8_selection_cli",
            "--mode",
            "live-nd8",
            "--m13-mode",
            "active",
            "--m13-schedule",
            str(args.schedule),
            "--com",
            str(args.com).upper(),
            "--data-root",
            str(args.data_root),
            "--max-trials",
            "9",
        ]
        print(
            json.dumps(
                {
                    "status": "NOT_LAUNCHED",
                    "command": command,
                    "hardwareOpened": False,
                    "warning": "future operator command only; this invocation does not open the selected COM port",
                },
                sort_keys=True,
            )
        )
        return 0
    return 2


__all__ = [
    "AudioCueSystem",
    "AudioTimingError",
    "HumanSessionRecorder",
    "NullAudioBackend",
    "NullLoggingAudioBackend",
    "PcAudioBackend",
    "PcToneAudioBackend",
    "RecordedND8Replay",
    "build_golden_protocol",
    "validate_golden_protocol",
    "verify_session",
    "run_audio_rehearsal",
    "run_no_nd8_dry_run",
]

NullAudioBackend = NullLoggingAudioBackend
PcAudioBackend = PcToneAudioBackend


if __name__ == "__main__":
    raise SystemExit(main())
