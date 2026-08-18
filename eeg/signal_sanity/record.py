"""Explicit M6.1a live recorder reusing the M5 ND8 adapter and post-sync gate."""

import argparse
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.sample_association.gate import PostSyncAssociationGate
from eeg.sample_association.jsonl import AppendOnlyJsonl


MODES = {"rest": 30.0, "artifact_sanity": 30.0, "single_ssvep_sanity": 15.0}


def make_session(root):
    session_id = "m6_1a-signal-sanity-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    session = Path(root) / session_id
    session.mkdir(parents=True, exist_ok=False)
    return session, session_id


def run_recording(com_port, data_root, mode, duration_seconds=None, electrode_note=""):
    if mode not in MODES:
        raise ValueError("unsupported validation mode: " + mode)
    duration_seconds = MODES[mode] if duration_seconds is None else float(duration_seconds)
    if duration_seconds <= 0 or duration_seconds > 600:
        raise ValueError("duration must be greater than zero and at most 600 seconds")
    session, session_id = make_session(data_root)
    gate = PostSyncAssociationGate()
    gate_log = AppendOnlyJsonl(session / "nd8-association-gate.jsonl")

    def observe(packet, continuity):
        decision = gate.observe(packet, continuity)
        gate_log.append({"recordType": "nd8_association_gate", "packetSequence": packet.packet_sequence,
                         "packetSdkTimestampMs": packet.device_timestamp,
                         "packetPcReceiveMonotonicNs": packet.pc_receive_monotonic_ns,
                         "continuity": continuity.to_dict(), "gate": decision.to_dict()})

    adapter = Nd8SerialAdapter(com_port, metadata_log=AppendOnlyJsonl(session / "packet-metadata.jsonl"),
                               raw_packet_log=AppendOnlyJsonl(session / "raw-eeg-packets.jsonl"), packet_observer=observe)
    try:
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        git_commit = "unavailable"
    manifest = {"recordType": "m6_1a_signal_sanity_session", "sessionId": session_id,
                "createdUtc": datetime.now(timezone.utc).isoformat(), "validationMode": mode,
                "requestedDurationSeconds": duration_seconds, "expectedSampleCountPerChannel": int(round(duration_seconds * 1000.0)),
                "samplingRateHz": 1000.0, "channelSchema": "ND8 all 8 SDK channels retained",
                "userElectrodeNote": electrode_note, "rawEegFile": "raw-eeg-packets.jsonl", "packetMetadataFile": "packet-metadata.jsonl",
                "gateEvidenceFile": "nd8-association-gate.jsonl", "hardwareTimingVerified": False,
                "physicalOpticalTimingVerified": False, "sampleAnchor": "unverified", "gitCommit": git_commit,
                "runtime": {"pythonVersion": sys.version, "implementation": sys.implementation.name}}
    if mode == "single_ssvep_sanity":
        manifest["stimulus"] = {"intendedTarget": "center", "nominalSoftwareFrequencyHz": 9.0,
                                "source": "Quest 3 M4/M5 configuration", "physicalOpticalTimingVerified": False}
    (session / "session-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        adapter.open_port()
        adapter.start_streaming()
        started = time.monotonic()
        while time.monotonic() - started < duration_seconds:
            time.sleep(0.05)
    finally:
        if adapter.state.value == "streaming":
            adapter.stop()
        adapter.close()
    manifest["actualDurationSeconds"] = time.monotonic() - started
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
    args = parser.parse_args()
    if args.com.upper() != "COM11":
        parser.error("M6.1a recorder is restricted to the verified COM11 configuration")
    print(run_recording(args.com, args.data_root, args.mode, args.duration_seconds, args.electrode_note), flush=True)


if __name__ == "__main__":
    main()
