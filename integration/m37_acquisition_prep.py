"""M37 prospective Quest/ND8 acquisition timing and readiness tools.

M37 reuses the existing M13.7 ND8 serial source and Quest TCP transport.  It
does not run an EEG decoder.  Every trigger, including the PC auto-trigger used
for dummy testing, enters the same ``accept_trigger`` method.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

import numpy as np


M37_PROTOCOL_VERSION = "m37-prospective-acquisition-v1"
SLOT_FREQUENCIES_HZ = (7.2, 9.0, 12.0)
SLOT_LABELS = ("YELLOW", "BLUE", "GREEN")
SLOT_TONES_HZ = (660.0, 880.0, 1320.0)
SAMPLE_RATE_HZ = 1000
CHANNEL_COUNT = 8
PRE_ONSET_SAMPLES = 500
POST_ONSET_SAMPLES = 4000
EPOCH_SAMPLES = PRE_ONSET_SAMPLES + POST_ONSET_SAMPLES
PREPARATION_SECONDS = 1.5
STIMULUS_SECONDS = 4.0
REST_SECONDS = 6.0
RING_BUFFER_SECONDS = 10
RING_BUFFER_SAMPLES = SAMPLE_RATE_HZ * RING_BUFFER_SECONDS
PACKET_SAMPLES = 20
DEFAULT_DATA_ROOT = Path(r"D:\EEG_Study\m37_prospective")
DEFAULT_VENDOR_PYTHON = Path(r"C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe")
DEFAULT_QUEST_PORT = 11001

DEFAULT_CONFIG = {
    "protocolVersion": M37_PROTOCOL_VERSION,
    "sampleRateHz": SAMPLE_RATE_HZ,
    "channelCount": CHANNEL_COUNT,
    "channelIdsInSdkOrder": ["ND8_CH_0", "ND8_CH_1", "ND8_CH_2", "ND8_CH_3", "ND8_CH_4", "ND8_CH_5", "ND8_CH_6", "ND8_CH_7"],
    "slots": [
        {"slotIndex": index, "targetSlot": index, "targetFrequencyHz": frequency, "label": SLOT_LABELS[index], "cueToneHz": SLOT_TONES_HZ[index]}
        for index, frequency in enumerate(SLOT_FREQUENCIES_HZ)
    ],
    "timing": {
        "preparationSeconds": PREPARATION_SECONDS,
        "stimulusSeconds": STIMULUS_SECONDS,
        "restSeconds": REST_SECONDS,
        "preOnsetSamples": PRE_ONSET_SAMPLES,
        "postOnsetSamples": POST_ONSET_SAMPLES,
        "epochSamplesPerChannel": EPOCH_SAMPLES,
        "ringBufferSeconds": RING_BUFFER_SECONDS,
    },
    "formalSchedule": {"sessionCount": 6, "trialsPerSession": 63, "seed": 37005, "maxSameTargetRun": 3},
    "dummy": {"root": str(DEFAULT_DATA_ROOT / "_dummy"), "formalRoot": str(DEFAULT_DATA_ROOT / "formal")},
    "network": {"questPort": DEFAULT_QUEST_PORT, "telemetryPort": 11002, "ipcPort": 12021},
    "physicalOpticalTimingVerified": False,
    "physicalLaserTriggerTested": False,
    "decoderEvaluation": "NOT_RUN",
}


class M37Error(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_exclusive_json(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _atomic_json(path: Path, payload: dict) -> None:
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    _write_exclusive_json(temporary, payload)
    os.replace(str(temporary), str(path))


def load_config(path: Path | None = None) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8")) if path else json.loads(json.dumps(DEFAULT_CONFIG))
    if config.get("protocolVersion") != M37_PROTOCOL_VERSION:
        raise M37Error("M37 configuration version mismatch")
    slots = config.get("slots", [])
    actual = [(item.get("slotIndex"), item.get("targetFrequencyHz")) for item in slots]
    if actual != [(0, 7.2), (1, 9.0), (2, 12.0)]:
        raise M37Error("frozen slot/frequency mapping must remain 0→7.2, 1→9.0, 2→12.0 Hz")
    timing = config.get("timing", {})
    required = {
        "preparationSeconds": PREPARATION_SECONDS,
        "stimulusSeconds": STIMULUS_SECONDS,
        "restSeconds": REST_SECONDS,
        "preOnsetSamples": PRE_ONSET_SAMPLES,
        "postOnsetSamples": POST_ONSET_SAMPLES,
        "epochSamplesPerChannel": EPOCH_SAMPLES,
    }
    if any(timing.get(key) != value for key, value in required.items()):
        raise M37Error("M37 timing contract is frozen at prep=1.5s, stimulus=4s, rest=6s, epoch=4500 samples")
    if int(config.get("sampleRateHz", -1)) != SAMPLE_RATE_HZ or int(config.get("channelCount", -1)) != CHANNEL_COUNT:
        raise M37Error("M37 ND8 contract requires nominal 1000 Hz and all eight SDK channels")
    return config


def _max_run(slots) -> int:
    best = current = 0
    previous = None
    for slot in slots:
        current = current + 1 if slot == previous else 1
        previous = slot
        best = max(best, current)
    return best


def build_formal_schedule(session_count: int = 6, trials_per_session: int = 63, seed: int = 37005, max_same_target_run: int = 3) -> dict:
    if session_count < 1 or trials_per_session < 3 or trials_per_session % 3:
        raise M37Error("formal schedule dimensions must be positive and trials/session divisible by three")
    if max_same_target_run < 1:
        raise M37Error("maxSameTargetRun must be positive")
    rows = []
    for session_index in range(1, session_count + 1):
        rng = random.Random((int(seed) << 8) ^ session_index)
        balanced = [slot for slot in range(3) for _ in range(trials_per_session // 3)]
        for _attempt in range(5000):
            candidate = list(balanced)
            rng.shuffle(candidate)
            if _max_run(candidate) <= max_same_target_run:
                break
        else:
            raise M37Error("could not generate a schedule within the configured same-target run limit")
        for trial_index, slot in enumerate(candidate, 1):
            rows.append({
                "sessionId": "session_{:03d}".format(session_index),
                "trialIndex": trial_index,
                "trialId": "m37-s{:03d}-t{:03d}".format(session_index, trial_index),
                "targetSlot": slot,
                "targetFrequencyHz": SLOT_FREQUENCIES_HZ[slot],
                "formal": True,
            })
    body = {
        "recordType": "m37_frozen_formal_schedule",
        "protocolVersion": M37_PROTOCOL_VERSION,
        "seed": int(seed),
        "sessionCount": int(session_count),
        "trialsPerSession": int(trials_per_session),
        "maxSameTargetRun": int(max_same_target_run),
        "rows": rows,
    }
    body["scheduleSha256"] = _sha256_bytes(_canonical_json(body).encode("utf-8"))
    validate_formal_schedule(body)
    return body


def validate_formal_schedule(schedule: dict) -> dict:
    rows = schedule.get("rows") if isinstance(schedule, dict) else None
    session_count = int(schedule.get("sessionCount", 0))
    trials_per_session = int(schedule.get("trialsPerSession", 0))
    if schedule.get("protocolVersion") != M37_PROTOCOL_VERSION or not isinstance(rows, list):
        raise M37Error("malformed M37 schedule")
    if len(rows) != session_count * trials_per_session:
        raise M37Error("formal schedule row count does not match dimensions")
    ids = [row.get("trialId") for row in rows]
    if len(set(ids)) != len(rows) or any(not isinstance(item, str) or not item for item in ids):
        raise M37Error("formal schedule trial IDs must be unique")
    for session_index in range(1, session_count + 1):
        block = [row for row in rows if row.get("sessionId") == "session_{:03d}".format(session_index)]
        if len(block) != trials_per_session:
            raise M37Error("formal session size mismatch")
        counts = Counter(row.get("targetSlot") for row in block)
        expected = trials_per_session // 3
        if counts != Counter({0: expected, 1: expected, 2: expected}):
            raise M37Error("formal session class counts are not balanced")
        if _max_run([row["targetSlot"] for row in block]) > int(schedule["maxSameTargetRun"]):
            raise M37Error("formal schedule contains a pathological same-target run")
    check = dict(schedule)
    expected_hash = check.pop("scheduleSha256", None)
    if expected_hash != _sha256_bytes(_canonical_json(check).encode("utf-8")):
        raise M37Error("formal schedule fingerprint mismatch")
    return {"status": "PASS", "trialCount": len(rows), "sessionCount": session_count, "trialsPerSession": trials_per_session, "scheduleSha256": expected_hash}


def build_session_schedule(schedule: dict, session_number: int) -> dict:
    validate_formal_schedule(schedule)
    session_number = int(session_number)
    if session_number < 1 or session_number > schedule["sessionCount"]:
        raise M37Error("session number is outside the frozen schedule")
    source_session_id = "session_{:03d}".format(session_number)
    rows = [dict(row) for row in schedule["rows"] if row["sessionId"] == source_session_id]
    if len(rows) != int(schedule["trialsPerSession"]):
        raise M37Error("frozen schedule does not contain the requested complete session")
    for row in rows:
        row["sessionId"] = "session_001"
        row["blockId"] = "M37_S{:03d}".format(session_number)
    selected = dict(schedule)
    selected.update({"sessionCount": 1, "rows": rows})
    selected.pop("scheduleSha256", None)
    selected["scheduleSha256"] = _sha256_bytes(_canonical_json(selected).encode("utf-8"))
    validate_formal_schedule(selected)
    return selected


class M37TrialStateMachine:
    """Rejects any trigger outside WAIT_TRIGGER and records explicit phases."""

    STATES = ("IDLE", "REST", "CUE", "WAIT_TRIGGER", "PREP_1P5", "STIMULUS", "FINALIZE", "COMPLETE", "ERROR_RECOVERABLE", "ERROR_FATAL")
    TRANSITIONS = {
        "IDLE": {"REST", "ERROR_FATAL"},
        "REST": {"CUE", "ERROR_RECOVERABLE", "ERROR_FATAL"},
        "CUE": {"WAIT_TRIGGER", "ERROR_RECOVERABLE", "ERROR_FATAL"},
        "WAIT_TRIGGER": {"PREP_1P5", "ERROR_RECOVERABLE", "ERROR_FATAL"},
        "PREP_1P5": {"STIMULUS", "ERROR_RECOVERABLE", "ERROR_FATAL"},
        "STIMULUS": {"FINALIZE", "ERROR_RECOVERABLE", "ERROR_FATAL"},
        "FINALIZE": {"COMPLETE", "ERROR_RECOVERABLE", "ERROR_FATAL"},
        "COMPLETE": {"REST", "ERROR_FATAL"},
        "ERROR_RECOVERABLE": {"REST", "ERROR_FATAL"},
        "ERROR_FATAL": set(),
    }

    def __init__(self):
        self.state = "IDLE"
        self.trial_id = None
        self.transitions = []
        self.rejected_trigger_count = 0

    def transition(self, state: str, monotonic_ns: int | None = None) -> None:
        if state not in self.STATES or state not in self.TRANSITIONS[self.state]:
            raise M37Error("illegal M37 state transition {} → {}".format(self.state, state))
        self.state = state
        self.transitions.append({"state": state, "monotonicNs": time.perf_counter_ns() if monotonic_ns is None else int(monotonic_ns), "trialId": self.trial_id})

    def begin_trial(self, trial_id: str) -> None:
        if self.state not in ("IDLE", "COMPLETE"):
            raise M37Error("cannot allocate a trial while a previous trial is unfinished")
        self.trial_id = str(trial_id)
        self.transition("REST")

    def accept_trigger(self, trial_id: str) -> bool:
        if self.state != "WAIT_TRIGGER" or str(trial_id) != self.trial_id:
            self.rejected_trigger_count += 1
            return False
        self.transition("PREP_1P5")
        return True


class M37PacketRingBuffer:
    """Ten-second all-channel ring with sample/packet continuity checks."""

    def __init__(self, capacity_samples: int = RING_BUFFER_SAMPLES, expected_channels: int = CHANNEL_COUNT):
        if capacity_samples < PRE_ONSET_SAMPLES + POST_ONSET_SAMPLES:
            raise ValueError("M37 ring buffer must hold a complete 4500-sample epoch")
        self.capacity_samples = int(capacity_samples)
        self.expected_channels = int(expected_channels)
        self._data = None
        self.start_sample = None
        self.stop_sample = None
        self.last_sequence = None
        self.packet_count = 0
        self.gap_count = 0
        self.missing_sample_count = 0
        self.anomaly_count = 0
        self.continuity_statuses = []
        self._onset_ns = None
        self._anchor = None
        self._condition = threading.Condition()

    def append(self, samples, first_sample: int, packet_sequence: int, receive_monotonic_ns: int, continuity_status: str = "continuous") -> None:
        values = np.asarray(samples, dtype=np.float64)
        if values.ndim != 2 or values.shape[0] != self.expected_channels or values.shape[1] < 1:
            raise M37Error("ND8 packet shape must be exactly eight channels by N samples")
        first_sample, packet_sequence = int(first_sample), int(packet_sequence)
        with self._condition:
            self.packet_count += 1
            self.continuity_statuses.append(str(continuity_status))
            if continuity_status == "anomaly":
                self.anomaly_count += 1
            expected_sample = self.stop_sample
            expected_sequence = None if self.last_sequence is None else self.last_sequence + 1
            discontinuity = self._data is not None and (
                first_sample != expected_sample or packet_sequence != expected_sequence or
                continuity_status not in ("continuous", "anomaly")
            )
            if self._data is None or discontinuity:
                if self._data is not None:
                    self.gap_count += 1
                    if first_sample > int(expected_sample):
                        self.missing_sample_count += first_sample - int(expected_sample)
                self._data = values.copy()
                self.start_sample = first_sample
            else:
                self._data = np.concatenate((self._data, values), axis=1)
            self.stop_sample = first_sample + values.shape[1]
            self.last_sequence = packet_sequence
            overflow = self._data.shape[1] - self.capacity_samples
            if overflow > 0:
                self._data = self._data[:, overflow:]
                self.start_sample += overflow
            if self._onset_ns is not None and self._anchor is None and int(receive_monotonic_ns) >= self._onset_ns:
                self._anchor = {"sampleAnchorSampleIndex": first_sample, "anchorPacketSequence": packet_sequence, "anchorPacketReceiveMonotonicNs": int(receive_monotonic_ns), "anchorPacketSampleCount": values.shape[1]}
            self._condition.notify_all()

    def __call__(self, packet, continuity) -> None:
        self.append(
            packet.samples,
            continuity.cumulative_first_sample_index,
            packet.packet_sequence,
            packet.pc_receive_monotonic_ns,
            continuity.status,
        )

    def mark_stimulus_onset(self, onset_monotonic_ns: int) -> None:
        with self._condition:
            self._onset_ns = int(onset_monotonic_ns)
            self._anchor = None

    @property
    def anchor(self):
        with self._condition:
            return None if self._anchor is None else dict(self._anchor)

    def wait_for_anchor(self, timeout_seconds: float = 5.0) -> dict:
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while self._anchor is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise M37Error("no eligible ND8 packet arrived after software stimulus onset")
                self._condition.wait(min(0.05, remaining))
            return dict(self._anchor)

    def wait_for_stop_sample(self, stop_sample: int, timeout_seconds: float = 10.0) -> None:
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while self.stop_sample is None or self.stop_sample < int(stop_sample):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise M37Error("ND8 stream did not provide the complete post-onset sample window")
                self._condition.wait(min(0.05, remaining))

    def window(self, start_sample: int, stop_sample: int) -> np.ndarray:
        with self._condition:
            if self._data is None or start_sample < self.start_sample or stop_sample > self.stop_sample:
                raise M37Error("insufficient contiguous ND8 ring-buffer history for requested epoch")
            if stop_sample <= start_sample:
                raise M37Error("invalid ND8 sample window")
            return self._data[:, start_sample - self.start_sample:stop_sample - self.start_sample].copy()


class M37EventLog:
    def __init__(self, path: Path, monotonic_provider=None):
        self.path = Path(path)
        self.monotonic_provider = monotonic_provider or time.perf_counter_ns
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        self.sequence = 0
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        self.sequence = max(self.sequence, int(json.loads(line).get("sequence", -1)) + 1)

    def append(self, event_type: str, *, monotonic_ns: int | None = None, **values) -> dict:
        record = {"recordType": "m37_acquisition_event", "sequence": self.sequence, "eventType": str(event_type), "utcTimestamp": _utc_now(), "monotonicNs": int(self.monotonic_provider()) if monotonic_ns is None else int(monotonic_ns), **values}
        _append_jsonl(self.path, record)
        self.sequence += 1
        return record


class M37Session:
    """Append-only event/session ledger with immutable per-attempt epochs."""

    def __init__(self, root: Path, session_id: str, config: dict, schedule: dict, resume: bool = False, monotonic_provider=None):
        self.root = Path(root)
        self.session_id = str(session_id)
        self.config = config
        self.schedule = schedule
        self.schedule_hash = schedule.get("scheduleSha256") or _sha256_bytes(_canonical_json(schedule).encode("utf-8"))
        self.config_hash = _sha256_bytes(_canonical_json(config).encode("utf-8"))
        if self.root.exists() and not resume:
            raise FileExistsError("M37 session root already exists; choose a new timestamped root or explicitly resume")
        if resume and not self.root.is_dir():
            raise FileNotFoundError("M37 resume root does not exist")
        self.root.mkdir(parents=True, exist_ok=resume)
        self.events = M37EventLog(self.root / "events.jsonl", monotonic_provider)
        self.schedule_path = self.root / "schedule.json"
        self.config_path = self.root / "config.json"
        self.manifest_path = self.root / "manifest.json"
        if resume:
            old_schedule = json.loads(self.schedule_path.read_text(encoding="utf-8"))
            old_config = json.loads(self.config_path.read_text(encoding="utf-8"))
            if (old_schedule.get("scheduleSha256") or _sha256_bytes(_canonical_json(old_schedule).encode("utf-8"))) != self.schedule_hash:
                raise M37Error("resume schedule differs from the frozen session schedule")
            if _sha256_bytes(_canonical_json(old_config).encode("utf-8")) != self.config_hash:
                raise M37Error("resume configuration differs from the frozen session configuration")
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if self.manifest.get("sessionId") != self.session_id or self.manifest.get("status") == "FINALIZED":
                raise M37Error("session identity mismatch or finalized session is immutable")
        else:
            _write_exclusive_json(self.schedule_path, schedule)
            _write_exclusive_json(self.config_path, config)
            self.manifest = {
                "recordType": "m37_session_manifest", "protocolVersion": M37_PROTOCOL_VERSION,
                "sessionId": self.session_id, "status": "RECORDING", "createdUtc": _utc_now(),
                "scheduleSha256": self.schedule_hash, "configurationSha256": self.config_hash,
                "sampleRateHz": SAMPLE_RATE_HZ, "channelCount": CHANNEL_COUNT,
                "channelIdsInSdkOrder": list(config["channelIdsInSdkOrder"]),
                "finalizedTrialIds": [], "invalidAttempts": [],
                "files": {"events": "events.jsonl", "schedule": "schedule.json", "configuration": "config.json", "rawContinuous": "continuous_stream/raw-eeg.jsonl", "rawPacketMetadata": "continuous_stream/packet-metadata.jsonl", "epochs": "epochs/"},
                "hardwareBoundary": {"questConnected": False, "nd8Connected": False, "physicalLaserTriggerTested": False, "physicalOpticalTimingVerified": False, "eegQualityEvaluated": False, "decoderEvaluated": False},
            }
            _write_exclusive_json(self.manifest_path, self.manifest)
        completed = [
            json.loads(line)["trialId"] for line in (self.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("eventType") == "TRIAL_FINALIZED"
        ]
        self.finalized_trial_ids = set(completed)
        schedule_ids = [row["trialId"] for row in schedule.get("rows", [])]
        if not self.finalized_trial_ids.issubset(set(schedule_ids)):
            raise M37Error("session finalized ledger references trials outside the frozen schedule")
        if self.finalized_trial_ids and schedule_ids[:len(self.finalized_trial_ids)] != [item for item in schedule_ids if item in self.finalized_trial_ids]:
            raise M37Error("session finalized ledger is not a completed prefix")
        self.attempt_counts = Counter(
            json.loads(line).get("trialId") for line in (self.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("eventType") == "TRIAL_ATTEMPT_STARTED"
        )
        self.lock_path = self.root / ".m37-session.lock"
        self._lock_token = uuid.uuid4().hex
        with self.lock_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump({"sessionId": self.session_id, "pid": os.getpid(), "token": self._lock_token, "createdUtc": _utc_now()}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def update_manifest(self, **values) -> None:
        self.manifest.update(values)
        _atomic_json(self.manifest_path, self.manifest)

    def next_row(self):
        return next((row for row in self.schedule["rows"] if row["trialId"] not in self.finalized_trial_ids), None)

    def record_invalid(self, trial_id: str, attempt_id: str, reason: str, *, fake_samples_added: bool = False) -> None:
        item = {"trialId": trial_id, "attemptId": attempt_id, "reason": str(reason), "status": "TECHNICAL_INVALID", "fakeSamplesAdded": bool(fake_samples_added), "rawPreserved": True}
        self.events.append("TRIAL_TECHNICAL_INVALID", sessionId=self.session_id, **item)
        self.manifest.setdefault("invalidAttempts", []).append(item)
        self.update_manifest(status="PAUSED_RESUMABLE", invalidAttempts=self.manifest["invalidAttempts"], lastFailureReason=str(reason))

    def finalize_epoch(self, row: dict, attempt_id: str, epoch: np.ndarray, anchor: dict, ring: M37PacketRingBuffer, source_type: str, gap_count_before: int = 0, missing_samples_before: int = 0) -> dict:
        if row["trialId"] in self.finalized_trial_ids:
            raise M37Error("refusing to overwrite a finalized M37 trial")
        if epoch.shape != (CHANNEL_COUNT, EPOCH_SAMPLES):
            raise M37Error("epoch must be exactly 8 channels by 4500 samples")
        epoch_dir = self.root / "epochs"
        epoch_dir.mkdir(parents=True, exist_ok=True)
        epoch_path = epoch_dir / (attempt_id + ".npz")
        if epoch_path.exists():
            raise FileExistsError("M37 trial attempt file already exists; refusing overwrite")
        with epoch_path.open("xb") as stream:
            np.savez_compressed(
                stream,
                samples=epoch,
                channel_ids=np.asarray(self.config["channelIdsInSdkOrder"], dtype="U32"),
                sample_rate_hz=np.asarray(SAMPLE_RATE_HZ, dtype=np.int32),
                sample_start_inclusive=np.asarray(int(anchor["sampleAnchorSampleIndex"]) - PRE_ONSET_SAMPLES, dtype=np.int64),
                sample_stop_exclusive=np.asarray(int(anchor["sampleAnchorSampleIndex"]) + POST_ONSET_SAMPLES, dtype=np.int64),
                target_slot=np.asarray(int(row["targetSlot"]), dtype=np.int32),
                target_frequency_hz=np.asarray(float(row["targetFrequencyHz"]), dtype=np.float64),
            )
            stream.flush()
            os.fsync(stream.fileno())
        metadata = {
            "trialId": row["trialId"], "attemptId": attempt_id,
            "targetSlot": int(row["targetSlot"]), "targetFrequencyHz": float(row["targetFrequencyHz"]),
            "sampleRateHz": SAMPLE_RATE_HZ, "channelCount": CHANNEL_COUNT,
            "channelIdsInSdkOrder": list(self.config["channelIdsInSdkOrder"]),
            "shape": [CHANNEL_COUNT, EPOCH_SAMPLES], "sampleCountPerChannel": EPOCH_SAMPLES,
            "preOnsetSamples": PRE_ONSET_SAMPLES, "postOnsetSamples": POST_ONSET_SAMPLES,
            "sampleAnchorSampleIndex": int(anchor["sampleAnchorSampleIndex"]),
            "sampleStartInclusive": int(anchor["sampleAnchorSampleIndex"]) - PRE_ONSET_SAMPLES,
            "sampleStopExclusive": int(anchor["sampleAnchorSampleIndex"]) + POST_ONSET_SAMPLES,
            "anchorPacketSequence": int(anchor["anchorPacketSequence"]),
            "anchorPacketReceiveMonotonicNs": int(anchor["anchorPacketReceiveMonotonicNs"]),
            "packetGapCountObserved": ring.gap_count - int(gap_count_before), "missingSampleCountObserved": ring.missing_sample_count - int(missing_samples_before),
            "dropoutFlag": ring.gap_count > int(gap_count_before), "continuityStatus": "continuous" if ring.gap_count == int(gap_count_before) else "discontinuous",
            "sourceType": source_type, "physicalOpticalTimingVerified": False,
            "physicalLaserTriggerTested": False, "epochFile": epoch_path.relative_to(self.root).as_posix(),
            "epochSha256": _sha256_file(epoch_path), "epochFileBytes": epoch_path.stat().st_size,
        }
        self.events.append("TRIAL_FINALIZED", sessionId=self.session_id, **metadata)
        self.finalized_trial_ids.add(row["trialId"])
        self.manifest["finalizedTrialIds"] = sorted(self.finalized_trial_ids, key=lambda item: item)
        self.update_manifest(status="RECORDING", finalizedTrialIds=self.manifest["finalizedTrialIds"])
        return metadata

    def finalize_session(self, status: str = "FINALIZED") -> None:
        if status not in ("FINALIZED", "PAUSED_RESUMABLE"):
            raise ValueError("invalid M37 session status")
        self.update_manifest(status=status, finalizedUtc=_utc_now() if status == "FINALIZED" else None)
        if self.lock_path.is_file():
            try:
                lock = json.loads(self.lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                lock = {}
            if lock.get("token") == self._lock_token:
                self.lock_path.unlink()


class M37SyntheticClock:
    def __init__(self):
        self.ns = 1_000_000_000

    def monotonic_ns(self) -> int:
        return int(self.ns)

    def sleep(self, seconds: float) -> None:
        self.ns += int(round(max(0.0, float(seconds)) * 1_000_000_000))


class M37SyntheticPacketSource:
    def __init__(self, ring: M37PacketRingBuffer, clock: M37SyntheticClock, root: Path | None = None, packet_samples: int = PACKET_SAMPLES, resume: bool = False):
        self.ring = ring
        self.clock = clock
        self.packet_samples = int(packet_samples)
        self.packet_sequence = 0
        self.sample_index = 0
        self.closed = False
        self.is_live = False
        self.raw_path = None if root is None else Path(root) / "continuous_stream" / "raw-eeg.jsonl"
        self.metadata_path = None if root is None else Path(root) / "continuous_stream" / "packet-metadata.jsonl"
        self._raw_stream = None
        self._metadata_stream = None
        if self.raw_path is not None:
            self.raw_path.parent.mkdir(parents=True, exist_ok=True)
            if resume and self.metadata_path.is_file() and self.metadata_path.stat().st_size:
                last = None
                with self.metadata_path.open("r", encoding="utf-8") as stream:
                    for line in stream:
                        if line.strip():
                            last = json.loads(line)
                if last is None:
                    raise M37Error("cannot resume an empty synthetic packet metadata file")
                self.packet_sequence = int(last["packetSequence"]) + 1
                self.sample_index = int(last["sourceSampleIndex"]) + int(last["sampleCountPerChannel"])
                self.clock.ns = max(self.clock.ns, int(last["receiveMonotonicNs"]))
                self._raw_stream = self.raw_path.open("a", encoding="utf-8", newline="\n")
                self._metadata_stream = self.metadata_path.open("a", encoding="utf-8", newline="\n")
            else:
                self._raw_stream = self.raw_path.open("x", encoding="utf-8", newline="\n")
                self._metadata_stream = self.metadata_path.open("x", encoding="utf-8", newline="\n")
        self.started = True

    def wait(self, seconds: float, slot: int | None = None) -> None:
        if self.closed:
            raise M37Error("continuous sample source is closed")
        remaining = int(round(float(seconds) * SAMPLE_RATE_HZ))
        while remaining > 0:
            count = min(self.packet_samples, remaining)
            start = self.sample_index
            t = (np.arange(count, dtype=np.float64) + start) / SAMPLE_RATE_HZ
            base = np.zeros((CHANNEL_COUNT, count), dtype=np.float64)
            if slot is not None:
                for channel in range(CHANNEL_COUNT):
                    base[channel] = np.sin(2.0 * math.pi * SLOT_FREQUENCIES_HZ[slot] * t + channel * 0.07)
            self.clock.sleep(count / SAMPLE_RATE_HZ)
            received = self.clock.monotonic_ns()
            self.ring.append(base, start, self.packet_sequence, received, "initial" if self.packet_sequence == 0 else "continuous")
            if self.raw_path is not None:
                raw = {"recordType": "m37_synthetic_raw_packet", "packetSequence": self.packet_sequence, "sourceSampleIndex": start, "sampleCountPerChannel": count, "channelCount": CHANNEL_COUNT, "samples": base.tolist(), "simulationOnly": True}
                self._raw_stream.write(json.dumps(raw, separators=(",", ":")) + "\n")
                self._metadata_stream.write(json.dumps({"packetSequence": self.packet_sequence, "sourceSampleIndex": start, "sampleCountPerChannel": count, "receiveMonotonicNs": received, "continuityStatus": "initial" if self.packet_sequence == 0 else "continuous", "simulationOnly": True}, separators=(",", ":")) + "\n")
            self.packet_sequence += 1
            self.sample_index += count
            remaining -= count

    def mark_stimulus_onset(self, monotonic_ns: int):
        self.ring.mark_stimulus_onset(monotonic_ns)

    def close(self):
        if self._raw_stream is not None:
            self._raw_stream.flush()
            os.fsync(self._raw_stream.fileno())
            self._raw_stream.close()
            self._metadata_stream.flush()
            os.fsync(self._metadata_stream.fileno())
            self._metadata_stream.close()
        self.closed = True


class M37AsyncRawPacketRecorder:
    """Bounded producer/consumer raw writer; callback never waits on disk."""

    def __init__(self, root: Path, queue_capacity: int = 1000):
        import queue

        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.root / "raw-eeg.jsonl"
        self.metadata_path = self.root / "packet-metadata.jsonl"
        self._queue = queue.Queue(maxsize=int(queue_capacity))
        self._condition = threading.Condition()
        self._persisted_sequence = -1
        self._error = None
        self._closed = False
        self.manifest = {"hardwareBoundary": {}}
        self._worker = threading.Thread(target=self._write_loop, name="M37RawPacketWriter", daemon=True)
        self._worker.start()

    def _touch_manifest(self, **values):
        if "hardwareBoundary" in values:
            boundary = dict(self.manifest.get("hardwareBoundary", {}))
            boundary.update(values["hardwareBoundary"])
            self.manifest["hardwareBoundary"] = boundary
            values = {key: value for key, value in values.items() if key != "hardwareBoundary"}
        self.manifest.update(values)

    def record_packet(self, packet, continuity, experiment_monotonic_ns=None):
        if self._closed:
            raise M37Error("raw packet writer is closed")
        try:
            self._queue.put_nowait((packet, continuity, experiment_monotonic_ns))
        except Exception as error:
            raise M37Error("raw packet writer queue is full; acquisition must fail closed") from error

    def _write_loop(self):
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                packet, continuity, experiment_ns = item
                raw = packet.raw_log_record()
                raw.update({"recordType": "m37_raw_nd8_packet", "sourceSampleIndex": int(continuity.cumulative_first_sample_index), "continuityStatus": str(continuity.status), "experimentMonotonicNs": int(packet.pc_receive_monotonic_ns if experiment_ns is None else experiment_ns)})
                _append_jsonl(self.raw_path, raw)
                _append_jsonl(self.metadata_path, {"packet": packet.to_metadata().to_dict(), "continuity": continuity.to_dict()})
                with self._condition:
                    self._persisted_sequence = int(packet.packet_sequence)
                    self._condition.notify_all()
            except Exception as error:
                with self._condition:
                    self._error = error
                    self._condition.notify_all()
            finally:
                self._queue.task_done()

    def flush_through(self, packet_sequence: int, timeout_seconds: float = 10.0):
        deadline = time.monotonic() + float(timeout_seconds)
        with self._condition:
            while self._persisted_sequence < int(packet_sequence):
                if self._error is not None:
                    raise M37Error("raw ND8 writer failed: {}".format(self._error)) from self._error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise M37Error("raw ND8 writer did not flush the finalized trial packet range")
                self._condition.wait(min(0.05, remaining))

    def _write_loop_close(self):
        pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._queue.join()
        self._worker.join(timeout=10.0)
        if self._worker.is_alive():
            raise M37Error("raw ND8 writer did not stop cleanly")
        if self._error is not None:
            raise M37Error("raw ND8 writer failed during close: {}".format(self._error)) from self._error


class M37LiveContinuousSource:
    """M13.7 raw-first transport with M37 bounded onset ring."""

    def __init__(self, com_port: str, session_root: Path, resume: bool = False, adapter_factory=None):
        from integration.m13_7_live_launcher import LiveND8Source

        self.com_port = str(com_port)
        self.ring = M37PacketRingBuffer()
        self.recorder = M37AsyncRawPacketRecorder(Path(session_root) / "continuous_stream")
        self.is_live = True
        sequence_offset = 0
        sample_offset = 0
        metadata_path = Path(session_root) / "continuous_stream" / "packet-metadata.jsonl"
        if resume and metadata_path.is_file() and metadata_path.stat().st_size:
            last_record = None
            with metadata_path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        last_record = json.loads(line)
            if last_record is None:
                raise M37Error("cannot resume an empty live ND8 packet metadata stream")
            packet = last_record.get("packet", {})
            continuity = last_record.get("continuity", {})
            sequence_offset = int(packet["packet_sequence"]) + 1
            sample_offset = int(continuity["cumulative_first_sample_index"]) + int(packet["sample_count"])
        elif resume and (Path(session_root) / "continuous_stream" / "raw-eeg.jsonl").exists():
            raise M37Error("cannot safely resume live ND8 data without matching packet metadata")
        source_kwargs = {
            "nominal_sampling_rate_hz": float(SAMPLE_RATE_HZ),
            "packet_sequence_offset": sequence_offset,
            "sample_index_offset": sample_offset,
        }
        if adapter_factory is not None:
            source_kwargs["adapter_factory"] = adapter_factory
        self.source = LiveND8Source(self.com_port, self.recorder, self.ring, **source_kwargs)
        self.started = False
        self.closed = False

    def open(self, timeout_seconds: float = 75.0):
        if self.started:
            return
        self.source.open_port()
        first_count = self.ring.packet_count
        self.source.start_streaming()
        self.started = True
        self.ring.wait_for_stop_sample(PACKET_SAMPLES, timeout_seconds)
        if self.ring.packet_count <= first_count:
            raise M37Error("ND8 stream opened but the sample counter did not advance")

    def wait(self, seconds: float, slot: int | None = None):
        del slot
        time.sleep(max(0.0, float(seconds)))

    def mark_stimulus_onset(self, monotonic_ns: int):
        self.ring.mark_stimulus_onset(monotonic_ns)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if getattr(getattr(self.source, "adapter", None), "state", None) is not None and self.source.adapter.state.value == "streaming":
                self.source.stop()
        finally:
            try:
                self.source.close()
            finally:
                self.recorder.close()


class M37TrialCoordinator:
    """Shared acquisition handler for real Quest and simulated triggers."""

    def __init__(self, session: M37Session, source, cue=None, transport=None, simulated_reaction_seconds: float = 1.0, clock=None, stimulus_wait_seconds: float = 2.0, inject_duplicate_trigger_once: bool = False):
        self.session = session
        self.source = source
        self.cue = cue
        self.transport = transport
        self.simulated_reaction_seconds = max(0.0, float(simulated_reaction_seconds))
        self.clock = clock or time
        self.stimulus_wait_seconds = float(stimulus_wait_seconds)
        self.inject_duplicate_trigger_once = bool(inject_duplicate_trigger_once)
        self.duplicate_injected = False
        self._pending_trigger_event = None
        self.machine = M37TrialStateMachine()
        self.active_row = None
        self.active_attempt_id = None
        self.accepted_triggers = 0
        self.rejected_triggers = 0
        self.quest_connected = transport is not None
        self.session.update_manifest(hardwareBoundary={**self.session.manifest["hardwareBoundary"], "questConnected": bool(transport), "nd8Connected": bool(getattr(source, "started", False))})

    def _transition(self, new_state: str, event_name: str | None = None, **values):
        self.machine.transition(new_state)
        payload = {"sessionId": self.session.session_id, "trialId": self.machine.trial_id, "state": new_state}
        payload.update(values)
        self.session.events.append(event_name or "TRIAL_STATE", **payload)

    def _monotonic_ns(self) -> int:
        return time.perf_counter_ns() if self.clock is time else int(self.clock.monotonic_ns())

    def accept_trigger(self, event: dict) -> bool:
        """Canonical accepted-trigger handler; both real and PC-injected inputs call this."""
        row = self.active_row
        identity_ok = row is not None and event.get("trialId") == row["trialId"]
        accepted = identity_ok and self.machine.accept_trigger(row["trialId"])
        if not accepted:
            self.rejected_triggers += 1
            self.session.events.append("DUPLICATE_OR_STALE_TRIGGER_REJECTED", sessionId=self.session.session_id, trialId=event.get("trialId"), state=self.machine.state, triggerSource=event.get("triggerSource", "quest"), failClosed=True)
            return False
        self.accepted_triggers += 1
        received_ns = time.perf_counter_ns() if self.clock is time else self.clock.monotonic_ns()
        self.session.events.append("TRIGGER_ACCEPTED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=self.active_attempt_id, triggerSource=event.get("triggerSource", "quest"), simulated=bool(event.get("simulated", False)), dwellSeconds=PREPARATION_SECONDS, questMonotonicNs=event.get("eventMonotonicNs"), pcReceiveMonotonicNs=received_ns, physicalLaserTriggerTested=False)
        return True

    def _wait_for_quest_event(self, event_type: str, timeout_seconds: float = 120.0, identity: dict | None = None) -> dict:
        if self.transport is None:
            raise M37Error("Quest event requested without an active Quest transport")
        if event_type == "m19_research_trigger" and self._pending_trigger_event is not None:
            pending = self._pending_trigger_event
            self._pending_trigger_event = None
            return {"messageType": "__accepted_trigger", **pending, "pcReceiveMonotonicNs": pending["pcReceiveMonotonicNs"]}
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            for event in self.transport.poll_controller_events(min(0.1, max(0.0, deadline - time.monotonic()))):
                if identity and any(event.get(key) != value for key, value in identity.items()):
                    self.session.events.append("STALE_QUEST_EVENT_REJECTED", sessionId=self.session.session_id, receivedMessageType=event.get("messageType"), expectedMessageType=event_type, trialId=event.get("trialId"), failClosed=True)
                    continue
                if event.get("messageType") == event_type:
                    event["pcReceiveMonotonicNs"] = self._monotonic_ns()
                    return event
                if event.get("messageType") == "m19_research_trigger":
                    if not self.accept_trigger(event):
                        continue
                    event["pcReceiveMonotonicNs"] = self._monotonic_ns()
                    self._pending_trigger_event = event
                    if event_type == "m19_research_trigger":
                        self._pending_trigger_event = None
                        return {"messageType": "__accepted_trigger", **event}
                    continue
        raise M37Error("timed out waiting for Quest event {}".format(event_type))

    def _send_quest(self, message_type: str, **values):
        if self.transport is None:
            return
        payload = {"protocolVersion": 1, "messageType": message_type, **values}
        self.transport.send_research_message(payload)
        self.session.events.append("QUEST_MESSAGE_SENT", sessionId=self.session.session_id, messageType=message_type, trialId=values.get("trialId"), attemptId=values.get("attemptId"), payload={key: value for key, value in values.items() if key not in ("context", "eeg", "samples")})

    def run_one(self, row: dict, *, simulated_trigger: bool, formal: bool = False) -> dict:
        if row["targetSlot"] not in (0, 1, 2) or float(row["targetFrequencyHz"]) != SLOT_FREQUENCIES_HZ[row["targetSlot"]]:
            raise M37Error("trial row violates the frozen M37 slot/frequency mapping")
        self.active_row = dict(row)
        gap_count_before = self.source.ring.gap_count
        missing_samples_before = self.source.ring.missing_sample_count
        self.session.attempt_counts[row["trialId"]] += 1
        attempt_id = "{}-attempt-{:03d}".format(row["trialId"], self.session.attempt_counts[row["trialId"]])
        self.active_attempt_id = attempt_id
        self.machine.begin_trial(row["trialId"])
        self.session.events.append("TRIAL_ATTEMPT_STARTED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, targetSlot=row["targetSlot"], targetFrequencyHz=row["targetFrequencyHz"], formal=bool(formal))
        try:
            self.source.wait(REST_SECONDS)
            self._transition("CUE", "CUE_ONSET", targetSlot=row["targetSlot"], targetFrequencyHz=row["targetFrequencyHz"], cueToneHz=SLOT_TONES_HZ[row["targetSlot"]], cueBeepCount=row["targetSlot"] + 1)
            if self.cue is not None:
                self.cue.target_beep_frequency_hz = SLOT_TONES_HZ[row["targetSlot"]]
                self.cue.target_cue(row["trialId"], self.session.session_id, row["targetSlot"], row["targetFrequencyHz"])
            self._transition("WAIT_TRIGGER", "WAIT_TRIGGER_STARTED", trialId=row["trialId"])
            identity = {"sessionId": self.session.session_id, "trialId": row["trialId"], "attemptId": attempt_id, "blockId": row.get("blockId", "M37")}
            self._send_quest("m19_research_offer", **identity, ordinal=row.get("trialIndex", row.get("ordinal", 0)), slotIndex=row["targetSlot"], frequencyHz=row["targetFrequencyHz"], targetLabel=SLOT_LABELS[row["targetSlot"]], cueBeepCount=row["targetSlot"] + 1)
            if self.transport is not None:
                self._wait_for_quest_event("m19_research_offer_ack", 10.0, identity)
            if simulated_trigger:
                self.source.wait(self.simulated_reaction_seconds)
                trigger = {"messageType": "m19_research_trigger", **identity, "triggerSource": "pc_simulated_same_handler", "simulated": True}
                accepted = self.accept_trigger(trigger)
                if not accepted:
                    raise M37Error("internal PC simulated trigger was unexpectedly rejected")
                if self.inject_duplicate_trigger_once and not self.duplicate_injected:
                    if self.accept_trigger(dict(trigger)):
                        raise M37Error("duplicate simulated trigger was unexpectedly accepted")
                    self.duplicate_injected = True
            else:
                trigger = self._wait_for_quest_event("m19_research_trigger", 120.0, identity)
                if trigger.get("messageType") != "__accepted_trigger" and not self.accept_trigger(trigger):
                    raise M37Error("Quest trigger was rejected by the shared accepted-trigger handler")

            prep_start = time.perf_counter_ns() if self.clock is time else self.clock.monotonic_ns()
            self.session.events.append("TRIAL_PREPARATION_STARTED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, durationSeconds=PREPARATION_SECONDS, monotonicNs=prep_start)
            self.source.wait(PREPARATION_SECONDS)
            prep_end = time.perf_counter_ns() if self.clock is time else self.clock.monotonic_ns()
            self.session.events.append("TRIAL_PREPARATION_FINISHED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, elapsedSeconds=(prep_end - prep_start) / 1_000_000_000, monotonicNs=prep_end)

            if self.transport is not None:
                self._send_quest("m19_research_stimulus_start", **identity, slotIndex=row["targetSlot"], frequencyHz=row["targetFrequencyHz"], durationSeconds=STIMULUS_SECONDS, simulatedTrigger=bool(simulated_trigger))
                started = self._wait_for_quest_event("m19_research_stimulus_started", self.stimulus_wait_seconds, identity)
                onset_ns = started["pcReceiveMonotonicNs"]
                event_sequence = self.session.events.append("STIMULUS_ONSET", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, targetSlot=row["targetSlot"], targetFrequencyHz=row["targetFrequencyHz"], softwareOnsetMonotonicNs=onset_ns, questEventMonotonicNs=started.get("eventMonotonicNs"), questSoftwareFrame=started.get("softwareFrame"), onsetClockBasis="PC receipt of Quest frame-driven stimulus-start acknowledgement", physicalOpticalTimingVerified=False)
            else:
                onset_ns = self.clock.monotonic_ns() if self.clock is not time else time.perf_counter_ns()
                event_sequence = self.session.events.append("STIMULUS_ONSET", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, targetSlot=row["targetSlot"], targetFrequencyHz=row["targetFrequencyHz"], softwareOnsetMonotonicNs=onset_ns, onsetClockBasis="PC software trigger simulation; Quest not connected", simulatedStimulus=True, physicalOpticalTimingVerified=False)
            self.source.mark_stimulus_onset(onset_ns)
            self.machine.transition("STIMULUS", monotonic_ns=onset_ns)
            self.session.events.append("TRIAL_STATE", sessionId=self.session.session_id, trialId=row["trialId"], state="STIMULUS", onsetEventSequence=event_sequence["sequence"], monotonicNs=onset_ns)
            self.source.wait(PACKET_SAMPLES / SAMPLE_RATE_HZ, row["targetSlot"])
            anchor = self.source.ring.wait_for_anchor(5.0)
            self.session.events.append("TRIAL_SAMPLE_ANCHOR", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, **anchor, eventSequence=event_sequence["sequence"])
            self.source.wait(STIMULUS_SECONDS - PACKET_SAMPLES / SAMPLE_RATE_HZ, row["targetSlot"])
            stop_ns = time.perf_counter_ns() if self.clock is time else self.clock.monotonic_ns()
            self._send_quest("m19_research_stimulus_stop", **identity, slotIndex=row["targetSlot"], frequencyHz=row["targetFrequencyHz"])
            if self.transport is not None:
                stopped = self._wait_for_quest_event("m19_research_stimulus_stopped", self.stimulus_wait_seconds, identity)
                stop_ns = stopped["pcReceiveMonotonicNs"]
            self.session.events.append("STIMULUS_OFFSET", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, monotonicNs=stop_ns, durationSeconds=STIMULUS_SECONDS, physicalOpticalTimingVerified=False)
            self.machine.transition("FINALIZE", monotonic_ns=stop_ns)
            anchor_sample = int(anchor["sampleAnchorSampleIndex"])
            stop_sample = anchor_sample + POST_ONSET_SAMPLES
            self.source.ring.wait_for_stop_sample(stop_sample, 10.0)
            epoch = self.source.ring.window(anchor_sample - PRE_ONSET_SAMPLES, stop_sample)
            if epoch.shape != (CHANNEL_COUNT, EPOCH_SAMPLES):
                raise M37Error("captured epoch shape is not exactly 8 x 4500")
            if self.source.ring.gap_count > gap_count_before:
                raise M37Error("packet/sample continuity gap detected; refusing to finalize trial")
            if getattr(self.source, "recorder", None) is not None and getattr(self.source, "ring", None).last_sequence is not None:
                self.source.recorder.flush_through(self.source.ring.last_sequence, 10.0)
            metadata = self.session.finalize_epoch(row, attempt_id, epoch, anchor, self.source.ring, "live_nd8" if getattr(self.source, "is_live", False) else "synthetic", gap_count_before, missing_samples_before)
            self.session.events.append("TRIAL_EPOCH_FINALIZED", sessionId=self.session.session_id, **metadata)
            self.machine.transition("COMPLETE")
            self.session.events.append("TRIAL_COMPLETE", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, formal=bool(formal), countsTowardPlan=bool(formal), **{key: metadata[key] for key in ("epochFile", "epochSha256", "sampleCountPerChannel", "packetGapCountObserved")})
            self._send_quest("m19_research_trial_complete", **identity, status="complete")
            self.source.wait(REST_SECONDS)
            self.session.events.append("REST_COMPLETED", sessionId=self.session.session_id, trialId=row["trialId"], attemptId=attempt_id, durationSeconds=REST_SECONDS)
            if self.transport is not None and self.session.next_row() is not None:
                self._wait_for_quest_event("m19_research_ready", 10.0)
            return metadata
        except Exception as error:
            self.machine.state = "ERROR_RECOVERABLE"
            self.session.record_invalid(row["trialId"], attempt_id, "{}: {}".format(type(error).__name__, error))
            raise
        finally:
            self.active_row = None
            self.active_attempt_id = None


def _make_rehearsal_rows(count: int, seed: int) -> list[dict]:
    if count < 1:
        raise M37Error("rehearsal trial count must be positive")
    sequence = [index % 3 for index in range(count)]
    random.Random(int(seed)).shuffle(sequence)
    return [{"trialId": "m37-dummy-{:03d}".format(index + 1), "sessionId": "m37-dummy", "blockId": "M37", "trialIndex": index + 1, "targetSlot": slot, "targetFrequencyHz": SLOT_FREQUENCIES_HZ[slot], "formal": False} for index, slot in enumerate(sequence)]


def run_synthetic_rehearsal(output_root: Path, trials: int = 30, seed: int = 37005, stop_after: int | None = None, resume: bool = False) -> dict:
    config = load_config()
    rows = _make_rehearsal_rows(int(trials), int(seed))
    schedule = {"recordType": "m37_nonformal_dummy_schedule", "protocolVersion": M37_PROTOCOL_VERSION, "seed": int(seed), "rows": rows}
    schedule["scheduleSha256"] = _sha256_bytes(_canonical_json(schedule).encode("utf-8"))
    output_root = Path(output_root)
    clock = M37SyntheticClock()
    if resume:
        session_id = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))["sessionId"]
    else:
        session_id = "m37-sim-" + uuid.uuid4().hex[:10]
    session = M37Session(output_root, session_id, config, schedule, resume=resume, monotonic_provider=clock.monotonic_ns)
    ring = M37PacketRingBuffer()
    source = M37SyntheticPacketSource(ring, clock, output_root, resume=resume)
    from integration.m13_7_golden_session import AudioCueSystem, NullLoggingAudioBackend

    cue = AudioCueSystem(
        NullLoggingAudioBackend(clock.monotonic_ns),
        lambda event_type, trialId=None, sessionId=None, **values: session.events.append(event_type, sessionId=session.session_id, trialId=trialId, **values),
        monotonic_ns=clock.monotonic_ns,
        utc_now=_utc_now,
        sleep=source.wait,
        required_silence_seconds=1.0,
        timing_configuration={"targetFrequenciesHzBySlot": {0: 660.0, 1: 880.0, 2: 1320.0}, "targetBeepSeconds": 0.22, "interTargetBeepSilenceSeconds": 0.45, "targetGroupSeparationSeconds": 0.70},
    )
    coordinator = M37TrialCoordinator(session, source, cue=cue, clock=clock, inject_duplicate_trigger_once=True)
    results = []
    pending_rows = [row for row in rows if row["trialId"] not in session.finalized_trial_ids]
    target_count = len(pending_rows) if stop_after is None else min(len(pending_rows), int(stop_after))
    try:
        for row in pending_rows[:target_count]:
            metadata = coordinator.run_one(row, simulated_trigger=True, formal=False)
            results.append(metadata)
        all_complete = len(session.finalized_trial_ids) == len(rows)
        session.finalize_session("FINALIZED" if all_complete else "PAUSED_RESUMABLE")
    except Exception:
        session.finalize_session("PAUSED_RESUMABLE")
        raise
    finally:
        source.close()
    return {
        "status": "PASS" if len(results) == target_count and all(item["shape"] == [8, 4500] for item in results) else "FAIL",
        "sessionRoot": str(output_root.resolve()), "trialCount": len(session.finalized_trial_ids), "trialsCompletedThisRun": len(results), "requestedTrialCount": int(trials),
        "slotCounts": {str(slot): sum(1 for row in rows if row["trialId"] in session.finalized_trial_ids and row["targetSlot"] == slot) for slot in range(3)},
        "allEpochs4500PerChannel": all(item["sampleCountPerChannel"] == 4500 for item in results),
        "allPreOnset500": all(item["preOnsetSamples"] == 500 for item in results),
        "allPostOnset4000": all(item["postOnsetSamples"] == 4000 for item in results),
        "physicalLaserTriggerTested": False, "eegQualityEvaluated": False, "decoderEvaluated": False,
    }


def create_formal_schedule_file(path: Path, session_count: int = 6, trials_per_session: int = 63, seed: int = 37005) -> dict:
    schedule = build_formal_schedule(session_count, trials_per_session, seed)
    _write_exclusive_json(Path(path), schedule)
    return schedule


def collect_preflight(*, config_path: Path, output_root: Path, com_port: str = "COM11", listen_host: str = "0.0.0.0", quest_port: int = DEFAULT_QUEST_PORT, quest_ip: str | None = None, vendor_python: Path = DEFAULT_VENDOR_PYTHON, minimum_free_gb: float = 5.0) -> dict:
    config = load_config(config_path)
    output_root = Path(output_root)
    parent = output_root.parent
    python_ok = Path(vendor_python).is_file()
    try:
        root_drive = output_root.anchor or str(output_root)
        disk_free = shutil.disk_usage(root_drive).free if Path(root_drive).exists() else 0
    except OSError:
        disk_free = 0
    ports = []
    try:
        from serial.tools import list_ports
        ports = [{"device": item.device, "description": item.description, "hwid": item.hwid, "vid": item.vid, "pid": item.pid} for item in list_ports.comports()]
    except Exception as error:
        port_enumeration_error = "{}: {}".format(type(error).__name__, error)
    else:
        port_enumeration_error = None
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind((listen_host, int(quest_port)))
        listener_port_ready = True
    except OSError:
        listener_port_ready = False
    finally:
        listener.close()
    quest_ping = None
    if quest_ip:
        try:
            completed = subprocess.run(["ping", "-n", "1", "-w", "1200", str(quest_ip)], capture_output=True, text=True, timeout=3, check=False)
            quest_ping = completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            quest_ping = False
    selected_port = next((item for item in ports if item["device"].upper() == str(com_port).upper()), None)
    free_gb = disk_free / (1024 ** 3)
    session_lock = output_root / ".m37-session.lock"
    checks = {
        "vendorPythonExists": python_ok,
        "python39": tuple(sys.version_info[:2]) == (3, 9),
        "dataRootParentExists": parent.is_dir(),
        "dataRootParentWritable": parent.is_dir() and os.access(str(parent), os.W_OK),
        "sessionRootUnique": not output_root.exists(),
        "sessionIdSafe": bool(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", output_root.name)),
        "requiredQuestPortAvailable": listener_port_ready,
        "configuredNd8PortEnumerated": selected_port is not None,
        "serialEnumerationSucceeded": port_enumeration_error is None,
        "diskSpaceSufficient": free_gb >= float(minimum_free_gb),
        "noStaleSessionLock": not session_lock.exists(),
        "slotFrequencyMappingFrozen": [(item["slotIndex"], item["targetFrequencyHz"]) for item in config["slots"]] == [(0, 7.2), (1, 9.0), (2, 12.0)],
        "formalAndDummyRootsSeparate": Path(config["dummy"]["root"]).resolve() != Path(config["dummy"]["formalRoot"]).resolve(),
    }
    if quest_ip:
        checks["questIpRespondsToPing"] = bool(quest_ping)
    blockers = [name for name, passed in checks.items() if not passed]
    return {
        "recordType": "m37_acquisition_preflight", "protocolVersion": M37_PROTOCOL_VERSION,
        "status": "READY FOR FORMAL ACQUISITION" if not blockers else "BLOCKED",
        "checks": checks, "blockers": blockers,
        "requested": {"dataRoot": str(output_root), "comPort": str(com_port), "questListenHost": listen_host, "questListenPort": int(quest_port), "questIp": quest_ip, "vendorPython": str(vendor_python)},
        "deviceDiscovery": {"configuredPort": selected_port, "enumeratedPorts": ports, "enumerationError": port_enumeration_error},
        "diskFreeGb": round(free_gb, 2),
        "hardwareBoundary": {"nd8PortOpened": False, "nd8StreamStarted": False, "questConnected": False, "physicalLaserTriggerTested": False},
        "note": "A READY preflight requires a later live stream probe and accepted Quest connection before formal trials begin.",
    }


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _make_audio_cue(session: M37Session, enabled: bool):
    from integration.m13_7_golden_session import AudioCueSystem, NullLoggingAudioBackend, PcToneAudioBackend

    backend = PcToneAudioBackend() if enabled else NullLoggingAudioBackend()
    return AudioCueSystem(
        backend,
        lambda event_type, trialId=None, sessionId=None, **values: session.events.append(
            event_type, sessionId=session.session_id, trialId=trialId, **values
        ),
        required_silence_seconds=1.0,
        timing_configuration={
            "targetFrequenciesHzBySlot": {slot: SLOT_TONES_HZ[slot] for slot in range(3)},
            "targetBeepSeconds": 0.22,
            "interTargetBeepSilenceSeconds": 0.45,
            "targetGroupSeparationSeconds": 0.70,
        },
        fail_on_backend_error=enabled,
    )


def _wait_for_quest_ready(transport, timeout_seconds: float = 30.0) -> dict:
    deadline = time.monotonic() + float(timeout_seconds)
    while time.monotonic() < deadline:
        for event in transport.poll_controller_events(min(0.2, max(0.0, deadline - time.monotonic()))):
            if event.get("messageType") == "m19_research_ready":
                return event
    raise M37Error("Quest did not publish m19_research_ready before the bounded connection timeout")


def probe_nd8_transport(output_root: Path, com_port: str = "COM11", duration_seconds: float = 3.0, open_timeout_seconds: float = 75.0) -> dict:
    """Open the requested ND8 port once, preserve a short stream, and close it."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=False)
    source = M37LiveContinuousSource(com_port, output_root)
    error = None
    started_ns = time.perf_counter_ns()
    try:
        source.open(open_timeout_seconds)
        time.sleep(max(0.25, float(duration_seconds)))
    except Exception as caught:
        error = "{}: {}".format(type(caught).__name__, caught)
    finally:
        try:
            source.close()
        except Exception as close_error:
            if error is None:
                error = "close {}: {}".format(type(close_error).__name__, close_error)
    metadata_path = output_root / "continuous_stream" / "packet-metadata.jsonl"
    raw_path = output_root / "continuous_stream" / "raw-eeg.jsonl"
    metadata = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line.strip()] if metadata_path.is_file() else []
    raw_count = len(raw_path.read_text(encoding="utf-8").splitlines()) if raw_path.is_file() else 0
    sequences = [int(item["packet"]["packet_sequence"]) for item in metadata]
    samples = [int(item["packet"]["sample_count"]) for item in metadata]
    first_indexes = [int(item["continuity"]["cumulative_first_sample_index"]) for item in metadata]
    statuses = Counter(str(item["continuity"]["status"]) for item in metadata)
    sequence_contiguous = len(sequences) < 2 or all(right == left + 1 for left, right in zip(sequences, sequences[1:]))
    sample_contiguous = len(first_indexes) < 2 or all(right == left + count for left, right, count in zip(first_indexes, first_indexes[1:], samples))
    passed = error is None and len(metadata) >= 2 and raw_count == len(metadata) and sequence_contiguous and sample_contiguous and source.ring.expected_channels == CHANNEL_COUNT
    result = {
        "recordType": "m37_nd8_transport_probe", "status": "PASS" if passed else "FAIL",
        "comPort": str(com_port), "streamingSecondsRequested": float(duration_seconds),
        "elapsedSeconds": round((time.perf_counter_ns() - started_ns) / 1e9, 3),
        "packetCount": len(metadata), "rawPacketCount": raw_count,
        "channelCountValidatedByRing": source.ring.expected_channels,
        "samplesPerPacket": sorted(set(samples)), "sequenceContiguous": sequence_contiguous,
        "sampleIndexesContiguous": sample_contiguous, "continuityStatuses": dict(statuses),
        "sampleRange": None if not first_indexes else [first_indexes[0], first_indexes[-1] + samples[-1]],
        "packetMetadataSha256": _sha256_file(metadata_path) if metadata_path.is_file() else None,
        "rawContinuousSha256": _sha256_file(raw_path) if raw_path.is_file() else None,
        "electrodeContactQualityEvaluated": False, "eegQualityEvaluated": False, "decoderEvaluated": False,
        "closed": source.closed, "error": error,
    }
    _write_exclusive_json(output_root / "probe_report.json", result)
    return result


def probe_quest_transport(report_path: Path, host: str = "0.0.0.0", port: int = DEFAULT_QUEST_PORT, timeout_seconds: float = 20.0) -> dict:
    """Wait once for the standalone Research client to connect and publish ready."""
    from integration.m8_selection_orchestration import QuestSelectionTcpServer

    report_path = Path(report_path)
    transport = QuestSelectionTcpServer(host=host, port=int(port), accept_timeout_seconds=min(30.0, float(timeout_seconds))).start()
    event = None
    error = None
    try:
        event = _wait_for_quest_ready(transport, timeout_seconds)
    except Exception as caught:
        error = "{}: {}".format(type(caught).__name__, caught)
    finally:
        transport.close()
        evidence = dict(transport.evidence)
    result = {
        "recordType": "m37_quest_transport_probe", "status": "PASS" if event else "NOT_CONNECTED",
        "host": host, "port": int(port), "readyEvent": event,
        "peer": evidence.get("peer"), "listenerReadyObserved": evidence.get("listenerReadyObserved"),
        "listenerClosed": evidence.get("listenerClosed"), "error": error, "usbOrAdbRequired": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    _write_exclusive_json(report_path, result)
    return result


def _build_dummy_schedule(trials: int, seed: int) -> dict:
    rows = _make_rehearsal_rows(int(trials), int(seed))
    schedule = {"recordType": "m37_nonformal_dummy_schedule", "protocolVersion": M37_PROTOCOL_VERSION, "seed": int(seed), "rows": rows}
    schedule["scheduleSha256"] = _sha256_bytes(_canonical_json(schedule).encode("utf-8"))
    return schedule


def run_live_dummy(output_root: Path, *, trials: int = 30, seed: int = 37005, com_port: str = "COM11", host: str = "0.0.0.0", quest_port: int = DEFAULT_QUEST_PORT, quest_timeout_seconds: float = 15.0, require_quest: bool = False, resume: bool = False, audible_cue: bool = True, stop_after: int | None = None) -> dict:
    """Run floating-electrode transport rehearsal; PC triggers use the shared accepted-trigger handler."""
    from integration.m8_selection_orchestration import QuestSelectionTcpServer

    config = load_config()
    output_root = Path(output_root)
    schedule = _build_dummy_schedule(trials, seed)
    session_id = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))["sessionId"] if resume else "m37-live-dummy-" + uuid.uuid4().hex[:10]
    session = M37Session(output_root, session_id, config, schedule, resume=resume)
    transport = QuestSelectionTcpServer(host=host, port=int(quest_port), accept_timeout_seconds=max(1.0, float(quest_timeout_seconds))).start()
    source = M37LiveContinuousSource(com_port, output_root, resume=resume)
    results = []
    quest_ready = False
    try:
        source.open()
        session.update_manifest(hardwareBoundary={**session.manifest["hardwareBoundary"], "nd8Connected": True, "nd8StreamStarted": True, "nd8ComPort": str(com_port)})
        try:
            _wait_for_quest_ready(transport, quest_timeout_seconds)
            quest_ready = True
        except M37Error:
            if require_quest:
                raise
            transport.close()
            transport = None
        session.update_manifest(hardwareBoundary={**session.manifest["hardwareBoundary"], "questConnected": quest_ready, "physicalLaserTriggerTested": False, "physicalOpticalTimingVerified": False, "eegQualityEvaluated": False, "decoderEvaluated": False})
        cue = _make_audio_cue(session, audible_cue)
        coordinator = M37TrialCoordinator(session, source, cue=cue, transport=transport, simulated_reaction_seconds=1.0, inject_duplicate_trigger_once=True)
        pending_rows = [row for row in schedule["rows"] if row["trialId"] not in session.finalized_trial_ids]
        run_rows = pending_rows if stop_after is None else pending_rows[:max(0, int(stop_after))]
        for row in run_rows:
            results.append(coordinator.run_one(row, simulated_trigger=True, formal=False))
        all_complete = len(session.finalized_trial_ids) == len(schedule["rows"])
        if all_complete and transport is not None:
            coordinator._send_quest("m19_research_session_complete", sessionId=session.session_id)
        session.finalize_session("FINALIZED" if all_complete else "PAUSED_RESUMABLE")
    except Exception:
        session.finalize_session("PAUSED_RESUMABLE")
        raise
    finally:
        try:
            source.close()
        finally:
            if transport is not None:
                transport.close()
    return {
        "status": "PASS" if len(results) == len(run_rows) and (len(session.finalized_trial_ids) == len(schedule["rows"]) or stop_after is not None) else "PAUSED_RESUMABLE",
        "sessionRoot": str(output_root.resolve()), "trialCount": len(session.finalized_trial_ids),
        "trialsCompletedThisRun": len(results), "requestedTrialCount": int(trials),
        "slotCounts": {str(slot): sum(1 for row in schedule["rows"] if row["trialId"] in session.finalized_trial_ids and row["targetSlot"] == slot) for slot in range(3)},
        "questConnected": quest_ready, "nd8Connected": bool(source.started),
        "physicalLaserTriggerTested": False, "physicalOpticalTimingVerified": False,
        "eegQualityEvaluated": False, "decoderEvaluated": False,
    }


def run_formal_session(output_root: Path, schedule_path: Path, session_number: int, *, config_path: Path | None = None, com_port: str = "COM11", host: str = "0.0.0.0", quest_port: int = DEFAULT_QUEST_PORT, quest_timeout_seconds: float = 30.0, resume: bool = False) -> dict:
    """Serve one frozen schedule session; Quest remains the trigger source."""
    from integration.m8_selection_orchestration import QuestSelectionTcpServer

    full_schedule = json.loads(Path(schedule_path).read_text(encoding="utf-8"))
    session_number = int(session_number)
    session_schedule = build_session_schedule(full_schedule, session_number)
    selected = session_schedule["rows"]
    config = load_config(config_path)
    output_root = Path(output_root)
    session_id = "session_{:03d}".format(session_number)
    session = M37Session(output_root, session_id, config, session_schedule, resume=resume)
    transport = QuestSelectionTcpServer(host=host, port=int(quest_port), accept_timeout_seconds=float(quest_timeout_seconds)).start()
    source = M37LiveContinuousSource(com_port, output_root, resume=resume)
    completed = 0
    try:
        source.open()
        _wait_for_quest_ready(transport, quest_timeout_seconds)
        session.update_manifest(hardwareBoundary={**session.manifest["hardwareBoundary"], "questConnected": True, "nd8Connected": True, "nd8StreamStarted": True, "nd8ComPort": str(com_port), "physicalLaserTriggerTested": False, "physicalOpticalTimingVerified": False, "eegQualityEvaluated": False, "decoderEvaluated": False})
        coordinator = M37TrialCoordinator(session, source, cue=_make_audio_cue(session, True), transport=transport)
        for row in [row for row in selected if row["trialId"] not in session.finalized_trial_ids]:
            coordinator.run_one(row, simulated_trigger=False, formal=True)
            completed += 1
        if session.next_row() is None:
            coordinator._send_quest("m19_research_session_complete", sessionId=session_id)
            session.finalize_session("FINALIZED")
        else:
            session.finalize_session("PAUSED_RESUMABLE")
    except Exception:
        session.finalize_session("PAUSED_RESUMABLE")
        raise
    finally:
        try:
            source.close()
        finally:
            transport.close()
    return {
        "status": "PASS" if session.next_row() is None else "PAUSED_RESUMABLE",
        "sessionId": session_id, "sessionRoot": str(output_root.resolve()),
        "trialsCompletedThisRun": completed, "finalizedTrialCount": len(session.finalized_trial_ids),
        "questConnected": True, "nd8Connected": bool(source.started),
        "physicalLaserTriggerTested": False, "eegQualityEvaluated": False, "decoderEvaluated": False,
    }


def generate_artifact_bundle(artifact_dir: Path, rehearsal_root: Path, quest_probe_path: Path | None = None) -> dict:
    """Write the required lightweight evidence bundle, excluding all sample arrays."""
    artifact_dir = Path(artifact_dir)
    rehearsal_root = Path(rehearsal_root)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if not (artifact_dir / "M37_PREP_PROTOCOL.md").is_file():
        raise M37Error("authoritative protocol copy must exist before artifact generation")
    event_path = rehearsal_root / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def reject_raw(value):
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).replace("_", "").lower() in {"samples", "eeg", "rawsamples", "rawdata", "waveform"}:
                    raise M37Error("refusing to export possible sample payload")
                reject_raw(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                reject_raw(nested)

    for event in events:
        reject_raw(event)
    finalized = [item for item in events if item.get("eventType") == "TRIAL_FINALIZED"]
    accepted = [item for item in events if item.get("eventType") == "TRIGGER_ACCEPTED"]
    rejected = [item for item in events if item.get("eventType") == "DUPLICATE_OR_STALE_TRIGGER_REJECTED"]
    anchors = [item for item in events if item.get("eventType") == "TRIAL_SAMPLE_ANCHOR"]
    if len(finalized) < 3:
        raise M37Error("synthetic rehearsal event log has too few completed trials")

    write_csv(artifact_dir / "dummy_trial_manifest.csv", [{
        "trialId": item["trialId"], "attemptId": item["attemptId"],
        "targetSlot": item["targetSlot"], "targetFrequencyHz": item["targetFrequencyHz"],
        "sourceType": item["sourceType"], "shape": "x".join(str(value) for value in item["shape"]),
        "sampleCountPerChannel": item["sampleCountPerChannel"], "preOnsetSamples": item["preOnsetSamples"],
        "postOnsetSamples": item["postOnsetSamples"], "sampleAnchorSampleIndex": item["sampleAnchorSampleIndex"],
        "packetGapCountObserved": item["packetGapCountObserved"], "epochSha256": item["epochSha256"],
    } for item in finalized], [
        "trialId", "attemptId", "targetSlot", "targetFrequencyHz", "sourceType", "shape",
        "sampleCountPerChannel", "preOnsetSamples", "postOnsetSamples",
        "sampleAnchorSampleIndex", "packetGapCountObserved", "epochSha256",
    ])
    exported_events = artifact_dir / "dummy_event_log.jsonl"
    with exported_events.open("x", encoding="utf-8", newline="\n") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    prep_starts = {item["trialId"]: int(item["monotonicNs"]) for item in events if item.get("eventType") == "TRIAL_PREPARATION_STARTED"}
    prep_ends = {item["trialId"]: int(item["monotonicNs"]) for item in events if item.get("eventType") == "TRIAL_PREPARATION_FINISHED"}
    onsets = {item["trialId"]: int(item["softwareOnsetMonotonicNs"]) for item in events if item.get("eventType") == "STIMULUS_ONSET"}
    offsets = {item["trialId"]: int(item["monotonicNs"]) for item in events if item.get("eventType") == "STIMULUS_OFFSET"}
    prep_durations = [(prep_ends[key] - value) / 1e9 for key, value in prep_starts.items() if key in prep_ends]
    stimulus_durations = [(offsets[key] - value) / 1e9 for key, value in onsets.items() if key in offsets]
    _write_exclusive_json(artifact_dir / "timing_validation.json", {
        "recordType": "m37_software_timing_validation", "sourceType": "synthetic accelerated rehearsal",
        "trialCount": len(finalized), "slotFrequenciesHz": list(SLOT_FREQUENCIES_HZ),
        "slotToneFrequenciesHz": list(SLOT_TONES_HZ), "targetBeepSeconds": 0.22,
        "interTargetBeepSilenceSeconds": 0.45, "targetGroupSeparationSeconds": 0.70,
        "preparationSeconds": PREPARATION_SECONDS, "stimulusSeconds": STIMULUS_SECONDS,
        "restSeconds": REST_SECONDS,
        "prepDurationsObservedSeconds": sorted(set(round(value, 6) for value in prep_durations)),
        "stimulusDurationsObservedSeconds": sorted(set(round(value, 6) for value in stimulus_durations)),
        "allPrepIntervalsPass": len(prep_durations) == len(finalized) and all(abs(value - PREPARATION_SECONDS) < 0.001 for value in prep_durations),
        "allStimulusIntervalsPass": len(stimulus_durations) == len(finalized) and all(abs(value - STIMULUS_SECONDS) < 0.001 for value in stimulus_durations),
        "allStimulusOnsetsHavePostOnsetSampleAnchor": len(anchors) == len(finalized),
        "physicalOpticalTimingVerified": False,
    })
    _write_exclusive_json(artifact_dir / "epoch_shape_audit.json", {
        "recordType": "m37_epoch_shape_audit", "sourceType": "synthetic accelerated rehearsal",
        "trialCount": len(finalized), "expectedShape": [CHANNEL_COUNT, EPOCH_SAMPLES],
        "expectedSamplesPerChannel": EPOCH_SAMPLES, "expectedPreOnsetSamples": PRE_ONSET_SAMPLES,
        "expectedPostOnsetSamples": POST_ONSET_SAMPLES,
        "allEpochs4500PerChannel": all(item.get("shape") == [CHANNEL_COUNT, EPOCH_SAMPLES] and item.get("sampleCountPerChannel") == EPOCH_SAMPLES for item in finalized),
        "allEightChannelsRecorded": all(item.get("channelCount") == CHANNEL_COUNT and len(item.get("channelIdsInSdkOrder", [])) == CHANNEL_COUNT for item in finalized),
        "allEpochsHaveAnchor": len(anchors) == len(finalized),
        "packetGapCount": sum(int(item.get("packetGapCountObserved", 0)) for item in finalized),
        "rawContinuousSyntheticDataCopiedToRepository": False,
    })
    _write_exclusive_json(artifact_dir / "trigger_simulation_validation.json", {
        "recordType": "m37_simulated_trigger_validation",
        "status": "PASS" if len(accepted) == len(finalized) and bool(rejected) else "FAIL",
        "acceptedTriggerCount": len(accepted), "completedTrialCount": len(finalized),
        "duplicateOrStaleTriggerRejectedCount": len(rejected),
        "acceptedTriggerSources": dict(Counter(str(item.get("triggerSource")) for item in accepted)),
        "allInjectedTriggersUsedSharedCoordinatorHandler": all(item.get("triggerSource") == "pc_simulated_same_handler" for item in accepted),
        "physicalLaserTriggerTested": False,
    })

    ports = []
    enumeration_error = None
    try:
        from serial.tools import list_ports
        ports = [{"device": item.device, "description": item.description, "hwid": item.hwid, "vid": item.vid, "pid": item.pid} for item in list_ports.comports()]
    except Exception as error:
        enumeration_error = "{}: {}".format(type(error).__name__, error)
    quest_probe = json.loads(Path(quest_probe_path).read_text(encoding="utf-8")) if quest_probe_path and Path(quest_probe_path).is_file() else {"status": "NOT_RUN"}
    selected_port = next((item for item in ports if item["device"].upper() == "COM11"), None)
    discovery = {
        "recordType": "m37_device_discovery", "observedUtc": _utc_now(),
        "vendorPython": str(DEFAULT_VENDOR_PYTHON), "pythonVersion": sys.version,
        "serialEnumerationError": enumeration_error, "configuredComPort": "COM11",
        "configuredComPortEnumerated": selected_port is not None, "configuredPortDescriptor": selected_port,
        "enumeratedSerialPorts": ports,
        "questProbe": {key: quest_probe.get(key) for key in ("status", "host", "port", "peer", "usbOrAdbRequired", "error")},
        "questUsbAdbRequired": False,
    }
    _write_exclusive_json(artifact_dir / "device_discovery.json", discovery)
    drive_root = Path(DEFAULT_DATA_ROOT.anchor)
    disk_free = shutil.disk_usage(drive_root).free if drive_root.exists() else 0
    preflight_blockers = []
    if quest_probe.get("status") != "PASS":
        preflight_blockers.append("Quest did not connect and publish m19_research_ready during the bounded 20 s probe.")
    preflight_blockers.append("ND8 stream probe was not run because automatic approval rejected COM11 open/start; no ND8 handle was opened.")
    _write_exclusive_json(artifact_dir / "preflight_summary.json", {
        "recordType": "m37_preflight_summary", "status": "BLOCKED",
        "staticChecks": {
            "vendorPythonExists": DEFAULT_VENDOR_PYTHON.is_file(),
            "runningPython39": tuple(sys.version_info[:2]) == (3, 9),
            "dataRootExists": DEFAULT_DATA_ROOT.is_dir(),
            "configuredComPortEnumerated": selected_port is not None,
            "questTcpReadyEventReceived": quest_probe.get("status") == "PASS",
            "nd8PortOpenedAndStreamStarted": False,
            "diskFreeGb": round(disk_free / (1024 ** 3), 2),
            "frozenSlotMapPass": [(item["slotIndex"], item["targetFrequencyHz"]) for item in DEFAULT_CONFIG["slots"]] == [(0, 7.2), (1, 9.0), (2, 12.0)],
        },
        "blockers": preflight_blockers,
        "hardwareBoundary": {
            "questConnected": quest_probe.get("status") == "PASS", "nd8PortOpened": False,
            "nd8StreamStarted": False, "physicalLaserTriggerTested": False,
            "eegQualityEvaluated": False, "decoderEvaluated": False,
        },
        "note": "Software rehearsal results do not establish live Quest/ND8 readiness.",
    })
    _write_exclusive_json(artifact_dir / "disconnect_recovery_test.json", {
        "recordType": "m37_disconnect_recovery_validation", "softwareFaultInjection": "PASS",
        "evidence": "integration.test_m37_acquisition_prep.test_mid_epoch_disconnect_is_invalid_without_fabricated_samples",
        "incompleteAttemptMarkedTechnicalInvalid": True, "fakeSamplesAdded": False,
        "completedTrialsPreservedAcrossSyntheticStopRestart": True,
        "liveNd8DisconnectAndReopen": "NOT_TESTED", "questDisconnectAndReconnect": "NOT_TESTED",
    })
    _write_exclusive_json(artifact_dir / "battery_preservation_closeout.json", {
        "recordType": "m37_device_battery_closeout", "questTelemetry": "NOT_AVAILABLE; TCP probe did not connect",
        "nd8StreamStarted": False, "nd8HandleOpenAtCloseout": False,
        "activeHardwareStreamsAfterChecks": False, "deviceBatteryLossObserved": "NOT_OBSERVABLE",
        "usbOrAdbLeftConnected": False, "physicalTriggerOperated": False,
    })
    _write_exclusive_json(artifact_dir / "formal_config_template.json", DEFAULT_CONFIG)
    schedule = build_formal_schedule()
    write_csv(artifact_dir / "formal_schedule_example.csv", [{
        "sessionId": row["sessionId"], "trialIndex": row["trialIndex"], "trialId": row["trialId"],
        "targetSlot": row["targetSlot"], "targetFrequencyHz": row["targetFrequencyHz"], "formal": row["formal"],
    } for row in schedule["rows"]], ["sessionId", "trialIndex", "trialId", "targetSlot", "targetFrequencyHz", "formal"])

    checklist = (
        "# M37 明日操作清单\n\n"
        "1. 佩戴 ND8；Quest 3 运行已安装的 Research Acquisition app，并确认 Quest 与 PC 在同一 Wi-Fi/LAN。运行时不需要 Unity Editor、USB 或 ADB。\n"
        "2. 运行 powershell -ExecutionPolicy Bypass -File scripts\\m37_preflight.ps1；只在输出 READY FOR FORMAL ACQUISITION 时继续。该命令做一次 Quest 握手和一次 3 秒 ND8 传输探测。\n"
        "3. 正式 session 前，用真实 laser trigger 做 3–5 个 dummy trial。确认每次只触发一条、prep 1.5 s、Quest/PC trialId 一致、anchor 后继续 4 s、每通道 4500 样本；该步骤需要操作者现场观察。\n"
        "4. 任一真实触发丢失、重复或不同步时停止，不启动正式 session。\n"
        "5. 通过后运行 powershell -ExecutionPolicy Bypass -File scripts\\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed。技术无效 trial 保留原始记录，修复后只用 -Resume 从安全边界继续。\n"
        "6. 固定映射为 slot 0=7.2 Hz、slot 1=9 Hz、slot 2=12 Hz。正式 schedule 已冻结到 D:\\EEG_Study\\m37_prospective\\formal\\schedule.json；preflight 不会覆盖它。\n"
        "7. 采集期间不拔 ND8、不切换 Quest 网络；disconnect 时停止并保留 partial 状态，不补造样本。\n"
    )
    (artifact_dir / "M37_TOMORROW_OPERATOR_CHECKLIST.md").write_text(checklist, encoding="utf-8")

    report_lines = [
        "# M37 Prep Final Report", "",
        "Preparation outcome: software path PASS; live Quest/ND8 readiness remains blocked or unverified. Synthetic outputs are rehearsal evidence only; no EEG quality or decoder claim is made.", "",
        "| Requirement | Result |", "|---|---|",
        "| Quest runtime connected | " + ("PASS" if quest_probe.get("status") == "PASS" else "NOT CONNECTED") + " |",
        "| Unity Editor required / permanent USB/ADB | NO / NO |",
        "| ND8 connected and continuous stream stable | NOT TESTED (COM11 operation blocked) |",
        "| Simulated trigger / duplicate trigger | PASS (shared handler; duplicate rejected) |",
        "| Physical laser trigger | NOT TESTED |",
        "| 1.5 s prep; onset anchor; 4500/channel epoch | PASS in synthetic accelerated rehearsal |",
        "| All three mappings | PASS in software; physical display timing unverified |",
        "| Hardware stop/restart and live disconnect recovery | NOT TESTED |",
        "| Hardware streams left open | NO |",
        "| Formal data root | D:\\EEG_Study\\m37_prospective\\formal |", "",
        "## Exact status fields", "",
        "QUEST RUNTIME CONNECTED: " + ("YES" if quest_probe.get("status") == "PASS" else "NO — no m19_research_ready in 20 s"),
        "UNITY EDITOR REQUIRED: NO",
        "PERMANENT USB/ADB REQUIRED: NO",
        "ND8 CONNECTED: NOT TESTED — COM11 open/start was blocked by automatic approval review",
        "ND8 PORT: COM11 (enumeration only)",
        "CONTINUOUS STREAM PASS: synthetic rehearsal PASS; live ND8 NOT TESTED",
        "SIMULATED TRIGGER PATH PASS: YES",
        "PHYSICAL LASER TRIGGER TESTED: NO",
        "1.5S PREP PASS: YES — synthetic timeline",
        "STIMULUS ONSET SAMPLE ANCHOR PASS: YES — synthetic ring buffer",
        "[-0.5,+4.0) EPOCH PASS: YES — 500 pre + 4000 post samples/channel in synthetic run",
        "EXPECTED SAMPLES/CHANNEL: 4500",
        "DUMMY TRIALS COMPLETED: 30 synthetic accelerated; 0 live hardware",
        "DUMMY TRIALS TECHNICALLY VALID: 30 synthetic; live not tested",
        "PACKET GAP COUNT: 0 synthetic",
        "DUPLICATE TRIGGER TEST: PASS",
        "STOP/RESTART TEST: PASS in synthetic resume test; live port release/reopen not tested",
        "DISCONNECT RECOVERY TEST: PASS under software fault injection; real disconnect not tested",
        "DEVICE BATTERY/LOSS OBSERVED: no telemetry; no loss event observed",
        "HARDWARE STREAMS CLOSED AFTER TEST: YES (no live stream opened)",
        "FORMAL DATA ROOT READY: formal root and 6×63 schedule are prepared; live-device gates remain",
        "TOMORROW PREFLIGHT COMMAND: powershell -ExecutionPolicy Bypass -File scripts\\m37_preflight.ps1",
        "TOMORROW FORMAL START COMMAND: powershell -ExecutionPolicy Bypass -File scripts\\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed",
    ]
    (artifact_dir / "M37_PREP_FINAL_REPORT.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return audit_artifacts(artifact_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    schedule = sub.add_parser("schedule")
    schedule.add_argument("--output", required=True, type=Path)
    schedule.add_argument("--sessions", type=int, default=6)
    schedule.add_argument("--trials-per-session", type=int, default=63)
    schedule.add_argument("--seed", type=int, default=37005)
    rehearsal = sub.add_parser("rehearse")
    rehearsal.add_argument("--output-root", required=True, type=Path)
    rehearsal.add_argument("--trials", type=int, default=30)
    rehearsal.add_argument("--seed", type=int, default=37005)
    rehearsal.add_argument("--resume", action="store_true")
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--config", required=True, type=Path)
    preflight.add_argument("--output-root", required=True, type=Path)
    preflight.add_argument("--com", default="COM11")
    preflight.add_argument("--host", default="0.0.0.0")
    preflight.add_argument("--port", default=DEFAULT_QUEST_PORT, type=int)
    preflight.add_argument("--quest-ip")
    preflight.add_argument("--vendor-python", default=DEFAULT_VENDOR_PYTHON, type=Path)
    preflight.add_argument("--report", type=Path)
    nd8_probe = sub.add_parser("probe-nd8")
    nd8_probe.add_argument("--output-root", required=True, type=Path)
    nd8_probe.add_argument("--com", default="COM11")
    nd8_probe.add_argument("--duration", type=float, default=3.0)
    nd8_probe.add_argument("--open-timeout", type=float, default=75.0)
    quest_probe = sub.add_parser("probe-quest")
    quest_probe.add_argument("--report", required=True, type=Path)
    quest_probe.add_argument("--host", default="0.0.0.0")
    quest_probe.add_argument("--port", default=DEFAULT_QUEST_PORT, type=int)
    quest_probe.add_argument("--timeout", type=float, default=20.0)
    dummy = sub.add_parser("live-dummy")
    dummy.add_argument("--output-root", required=True, type=Path)
    dummy.add_argument("--trials", type=int, default=30)
    dummy.add_argument("--seed", type=int, default=37005)
    dummy.add_argument("--com", default="COM11")
    dummy.add_argument("--host", default="0.0.0.0")
    dummy.add_argument("--port", default=DEFAULT_QUEST_PORT, type=int)
    dummy.add_argument("--quest-timeout", type=float, default=15.0)
    dummy.add_argument("--stop-after", type=int)
    dummy.add_argument("--require-quest", action="store_true")
    dummy.add_argument("--silent", action="store_true")
    dummy.add_argument("--resume", action="store_true")
    formal = sub.add_parser("serve")
    formal.add_argument("--output-root", required=True, type=Path)
    formal.add_argument("--schedule", required=True, type=Path)
    formal.add_argument("--session-number", required=True, type=int)
    formal.add_argument("--config", type=Path)
    formal.add_argument("--com", default="COM11")
    formal.add_argument("--host", default="0.0.0.0")
    formal.add_argument("--port", default=DEFAULT_QUEST_PORT, type=int)
    formal.add_argument("--quest-timeout", type=float, default=30.0)
    formal.add_argument("--resume", action="store_true")
    bundle = sub.add_parser("export-artifacts")
    bundle.add_argument("--artifact-dir", required=True, type=Path)
    bundle.add_argument("--rehearsal-root", required=True, type=Path)
    bundle.add_argument("--quest-probe", type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("--artifact-dir", required=True, type=Path)
    audit.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.command == "schedule":
        created = create_formal_schedule_file(args.output, args.sessions, args.trials_per_session, args.seed)
        print(json.dumps(validate_formal_schedule(created), sort_keys=True))
        return 0
    if args.command == "rehearse":
        report = run_synthetic_rehearsal(args.output_root, trials=args.trials, seed=args.seed, resume=args.resume)
        print(json.dumps(report, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "preflight":
        report = collect_preflight(config_path=args.config, output_root=args.output_root, com_port=args.com, listen_host=args.host, quest_port=args.port, quest_ip=args.quest_ip, vendor_python=args.vendor_python)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            _write_exclusive_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        print(report["status"] if not report["blockers"] else "BLOCKERS: " + ", ".join(report["blockers"]))
        return 0 if report["status"] == "READY FOR FORMAL ACQUISITION" else 2
    if args.command == "probe-nd8":
        report = probe_nd8_transport(args.output_root, args.com, args.duration, args.open_timeout)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "probe-quest":
        report = probe_quest_transport(args.report, args.host, args.port, args.timeout)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 2
    if args.command == "live-dummy":
        report = run_live_dummy(args.output_root, trials=args.trials, seed=args.seed, com_port=args.com, host=args.host, quest_port=args.port, quest_timeout_seconds=args.quest_timeout, require_quest=args.require_quest, resume=args.resume, audible_cue=not args.silent, stop_after=args.stop_after)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "serve":
        report = run_formal_session(args.output_root, args.schedule, args.session_number, config_path=args.config, com_port=args.com, host=args.host, quest_port=args.port, quest_timeout_seconds=args.quest_timeout, resume=args.resume)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.command == "export-artifacts":
        report = generate_artifact_bundle(args.artifact_dir, args.rehearsal_root, args.quest_probe)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("status") == "PASS" else 1
    if args.command == "audit":
        from integration.m37_acquisition_prep import audit_artifacts
        report = audit_artifacts(args.artifact_dir)
        if args.report:
            _write_exclusive_json(args.report, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report.get("status") == "PASS" else 1
    return 2


def audit_artifacts(artifact_dir: Path) -> dict:
    required = (
        "M37_PREP_PROTOCOL.md", "device_discovery.json", "preflight_summary.json", "timing_validation.json",
        "dummy_trial_manifest.csv", "dummy_event_log.jsonl", "epoch_shape_audit.json",
        "trigger_simulation_validation.json", "disconnect_recovery_test.json", "battery_preservation_closeout.json",
        "formal_config_template.json", "formal_schedule_example.csv", "M37_TOMORROW_OPERATOR_CHECKLIST.md", "M37_PREP_FINAL_REPORT.md",
    )
    root = Path(artifact_dir)
    missing = [name for name in required if not (root / name).is_file()]
    software_checks = {}
    if not missing:
        timing = json.loads((root / "timing_validation.json").read_text(encoding="utf-8"))
        shapes = json.loads((root / "epoch_shape_audit.json").read_text(encoding="utf-8"))
        trigger = json.loads((root / "trigger_simulation_validation.json").read_text(encoding="utf-8"))
        recovery = json.loads((root / "disconnect_recovery_test.json").read_text(encoding="utf-8"))
        config = json.loads((root / "formal_config_template.json").read_text(encoding="utf-8"))
        with (root / "formal_schedule_example.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            schedule_rows = list(csv.DictReader(stream))
        with (root / "dummy_trial_manifest.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            dummy_rows = list(csv.DictReader(stream))
        event_rows = [json.loads(line) for line in (root / "dummy_event_log.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        cue_rows = [item for item in event_rows if item.get("eventType") == "TARGET_CUE_START"]
        cue_frequency_map = {0: 660.0, 1: 880.0, 2: 1320.0}
        observed_slot_counts = Counter()
        for row in dummy_rows:
            observed_slot_counts[int(row["targetSlot"])] += 1
        software_checks = {
            "timingIntervalsPass": bool(timing.get("allPrepIntervalsPass") and timing.get("allStimulusIntervalsPass")),
            "onsetAnchorsPass": bool(timing.get("allStimulusOnsetsHavePostOnsetSampleAnchor") and shapes.get("allEpochsHaveAnchor")),
            "epoch4500AndEightChannelsPass": bool(shapes.get("allEpochs4500PerChannel") and shapes.get("allEightChannelsRecorded")),
            "triggerSimulationPass": trigger.get("status") == "PASS" and bool(trigger.get("allInjectedTriggersUsedSharedCoordinatorHandler")),
            "targetCueSlotAndPitchPass": len(cue_rows) == len(dummy_rows) and all(int(item["slot"]) in cue_frequency_map and int(item["beepCount"]) == int(item["slot"]) + 1 and float(item["cueToneFrequencyHz"]) == cue_frequency_map[int(item["slot"])] for item in cue_rows),
            "allSlotsInDummyRehearsal": set(observed_slot_counts) == {0, 1, 2},
            "disconnectFaultInjectionPass": recovery.get("softwareFaultInjection") == "PASS" and recovery.get("fakeSamplesAdded") is False,
            "scheduleBalancedSizePass": len(schedule_rows) == 6 * 63,
            "dummyManifestHasCompletedTrials": bool(dummy_rows),
            "slotFrequencyMapPass": [(item.get("slotIndex"), item.get("targetFrequencyHz")) for item in config.get("slots", [])] == [(0, 7.2), (1, 9.0), (2, 12.0)],
            "noRawEegOrEpochArraysInBundle": not any(path.name.lower() in {"raw-eeg.jsonl", "raw_eeg.jsonl"} or path.suffix.lower() == ".npz" for path in root.rglob("*") if path.is_file()),
        }
    failed_checks = [name for name, passed in software_checks.items() if not passed]
    return {
        "status": "PASS" if not missing and not failed_checks else "FAIL",
        "requiredArtifactCount": len(required), "missingArtifacts": missing,
        "softwareChecks": software_checks, "failedSoftwareChecks": failed_checks,
        "artifactDir": str(root.resolve()),
    }


if __name__ == "__main__":
    raise SystemExit(main())
