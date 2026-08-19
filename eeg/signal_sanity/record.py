"""Explicit M6.1a live recorder reusing the M5 ND8 adapter and post-sync gate."""

import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns

from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.sample_association.gate import PostSyncAssociationGate
from eeg.sample_association.jsonl import AppendOnlyJsonl


MODES = {"rest": 30.0, "artifact_sanity": 30.0, "single_ssvep_sanity": 15.0}


class PhaseJsonl:
    """Route preparation and formal records to separate append-only evidence files."""

    def __init__(self, preparation_path, formal_path):
        self.preparation_log = AppendOnlyJsonl(preparation_path)
        self.formal_log = AppendOnlyJsonl(formal_path)
        self.formal_started = False
        self.preparation_count = 0
        self.formal_count = 0

    def append(self, record):
        if self.formal_started:
            self.formal_log.append(record)
            self.formal_count += 1
        else:
            self.preparation_log.append(record)
            self.preparation_count += 1

    def begin_formal(self):
        self.formal_started = True


def _countdown_messages(seconds):
    if seconds <= 0:
        return []
    checkpoints = {seconds, 10, 5, 3, 2, 1}
    return ["Preparation countdown: {} s remaining".format(value)
            for value in range(int(seconds), 0, -1) if value in checkpoints]


def _run_countdown(seconds, sleep=time.sleep, output=print):
    for remaining in range(int(seconds), 0, -1):
        if remaining in {int(seconds), 10, 5, 3, 2, 1}:
            output("Preparation countdown: {} s remaining".format(remaining), flush=True)
        sleep(1.0)


def make_session(root):
    session_id = "m6_1a-signal-sanity-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    session = Path(root) / session_id
    session.mkdir(parents=True, exist_ok=False)
    return session, session_id


def run_recording(com_port, data_root, mode, duration_seconds=None, electrode_note="", preparation_seconds=0.0):
    if mode not in MODES:
        raise ValueError("unsupported validation mode: " + mode)
    duration_seconds = MODES[mode] if duration_seconds is None else float(duration_seconds)
    if duration_seconds <= 0 or duration_seconds > 600:
        raise ValueError("duration must be greater than zero and at most 600 seconds")
    preparation_seconds = float(preparation_seconds)
    if preparation_seconds < 0 or preparation_seconds > 120:
        raise ValueError("preparation duration must be between zero and 120 seconds")
    if mode != "single_ssvep_sanity" and preparation_seconds:
        raise ValueError("preparation countdown is supported only for single_ssvep_sanity")
    session, session_id = make_session(data_root)
    gate = PostSyncAssociationGate()
    gate_log = AppendOnlyJsonl(session / "nd8-association-gate.jsonl")
    event_log = AppendOnlyJsonl(session / "experiment-events.jsonl")

    def observe(packet, continuity):
        decision = gate.observe(packet, continuity)
        gate_log.append({"recordType": "nd8_association_gate", "packetSequence": packet.packet_sequence,
                         "packetSdkTimestampMs": packet.device_timestamp,
                         "packetPcReceiveMonotonicNs": packet.pc_receive_monotonic_ns,
                         "continuity": continuity.to_dict(), "gate": decision.to_dict()})

    if mode == "single_ssvep_sanity" and preparation_seconds:
        raw_log = PhaseJsonl(session / "preparation-raw-eeg-packets.jsonl", session / "raw-eeg-packets.jsonl")
        metadata_log = PhaseJsonl(session / "preparation-packet-metadata.jsonl", session / "packet-metadata.jsonl")
    else:
        raw_log = metadata_log = None
    adapter = Nd8SerialAdapter(com_port,
                               metadata_log=(metadata_log or AppendOnlyJsonl(session / "packet-metadata.jsonl")),
                               raw_packet_log=(raw_log or AppendOnlyJsonl(session / "raw-eeg-packets.jsonl")),
                               packet_observer=observe)
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        git_commit = "unavailable"
    manifest = {"recordType": "m6_1a_signal_sanity_session", "sessionId": session_id,
                "createdUtc": datetime.now(timezone.utc).isoformat(), "validationMode": mode,
                "requestedDurationSeconds": duration_seconds, "expectedSampleCountPerChannel": int(round(duration_seconds * 1000.0)),
                "samplingRateHz": 1000.0, "channelSchema": "ND8 all 8 SDK channels retained",
                "userElectrodeNote": electrode_note, "rawEegFile": "raw-eeg-packets.jsonl", "packetMetadataFile": "packet-metadata.jsonl",
                "gateEvidenceFile": "nd8-association-gate.jsonl", "experimentEventsFile": "experiment-events.jsonl",
                "hardwareTimingVerified": False,
                "physicalOpticalTimingVerified": False, "sampleAnchor": "unverified", "gitCommit": git_commit,
                "runtime": {"pythonVersion": sys.version, "implementation": sys.implementation.name}}
    if mode == "single_ssvep_sanity":
        manifest["stimulus"] = {"intendedTarget": "center", "nominalSoftwareFrequencyHz": 9.0,
                                "source": "Quest 3 M4/M5 configuration", "physicalOpticalTimingVerified": False}
        manifest["preparation"] = {"requestedCountdownSeconds": preparation_seconds,
                                   "rawEvidenceFile": "preparation-raw-eeg-packets.jsonl" if preparation_seconds else None,
                                   "packetMetadataFile": "preparation-packet-metadata.jsonl" if preparation_seconds else None,
                                   "formalAnalysisExcludesPreparation": bool(preparation_seconds)}
    (session / "session-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        adapter.open_port()
        adapter.start_streaming()
        if preparation_seconds:
            deadline = time.monotonic() + 60.0
            while gate.ready_pc_monotonic_ns is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if gate.ready_pc_monotonic_ns is None:
                raise RuntimeError("ND8 post-sync association_ready was not reached before preparation countdown")
            event_log.append({"recordType": "m6_1a_preparation_started", "pcMonotonicNs": perf_counter_ns(),
                              "createdUtc": datetime.now(timezone.utc).isoformat(), "durationSeconds": preparation_seconds,
                              "nd8AssociationReadyBeforeCountdown": True})
            print("ND8 association_ready; preparation countdown begins.", flush=True)
            _run_countdown(preparation_seconds)
            raw_log.begin_formal()
            metadata_log.begin_formal()
            formal_started_utc = datetime.now(timezone.utc).isoformat()
            event_log.append({"recordType": "m6_1a_formal_recording_started", "pcMonotonicNs": perf_counter_ns(),
                              "createdUtc": formal_started_utc, "preparationSeconds": preparation_seconds,
                              "formalAnalysisExcludesPreparation": True})
            manifest["preparation"].update({"nd8AssociationReadyBeforeCountdown": True,
                                            "formalRecordingStartedUtc": formal_started_utc,
                                            "formalAnalysisStartRecordBoundary": True})
            print("FORMAL RECORDING STARTED", flush=True)
        started = time.monotonic()
        while time.monotonic() - started < duration_seconds:
            time.sleep(0.05)
    finally:
        if adapter.state.value == "streaming":
            adapter.stop()
        adapter.close()
    manifest["actualDurationSeconds"] = time.monotonic() - started
    if raw_log is not None:
        manifest["preparationObservedPacketCount"] = raw_log.preparation_count
        manifest["observedPacketCount"] = raw_log.formal_count
        formal_packets = adapter.timeline.packets[-raw_log.formal_count:] if raw_log.formal_count else []
        manifest["observedSampleCountPerChannel"] = sum(packet.sample_count for packet in formal_packets)
    else:
        manifest["observedPacketCount"] = len(adapter.timeline.packets)
        manifest["observedSampleCountPerChannel"] = sum(packet.sample_count for packet in adapter.timeline.packets)
    manifest["finalGate"] = gate._decision("recording_finished").to_dict()
    manifest["callbackErrors"] = list(adapter.callback_errors)
    (session / "session-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return session


def main():
    parser = argparse.ArgumentParser(description="M6.1a ND8 signal sanity recorder (no EEG classifier)")
    parser.add_argument("--com", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--duration-seconds", type=float)
    parser.add_argument("--electrode-note", default="")
    parser.add_argument("--preparation-seconds", type=float, default=0.0,
                        help="single_ssvep_sanity only: exclude a post-gate preparation interval from formal raw evidence")
    args = parser.parse_args()
    if args.com.upper() != "COM11":
        parser.error("M6.1a recorder is restricted to the verified COM11 configuration")
    print(run_recording(args.com, args.data_root, args.mode, args.duration_seconds, args.electrode_note,
                        args.preparation_seconds), flush=True)


if __name__ == "__main__":
    main()
