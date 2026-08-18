"""Explicit, short ND8 serial validation entry point for M5.3.

Run this module only with the project-external CPython 3.9 vendor SDK environment.
It never scans, pairs, unpairs, or changes to a rate other than the explicit
``--sampling-rate`` value. It writes a new append-only session and exits after
the requested duration.
"""

import argparse
import json
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eeg.sample_association.jsonl import AppendOnlyJsonl

from .nd8_serial_adapter import Nd8SerialAdapter


def create_session(root: Path) -> Path:
    session_id = "nd8-m5_3-validation-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8]
    )
    session = root / session_id
    session.mkdir(parents=True, exist_ok=False)
    return session


def summarize(adapter: Nd8SerialAdapter, requested_duration_seconds: float) -> dict:
    packets = adapter.timeline.packets
    timestamp_deltas = [current.device_timestamp - previous.device_timestamp
                        for previous, current in zip(packets, packets[1:])]
    receive_deltas_ms = [(current.pc_receive_monotonic_ns - previous.pc_receive_monotonic_ns) / 1e6
                         for previous, current in zip(packets, packets[1:])]
    issue_counts = Counter(issue for record in adapter.timeline.continuity for issue in record.issues)
    sample_counts = sorted(set(packet.sample_count for packet in packets))
    channel_counts = sorted(set(packet.channel_count for packet in packets))
    nominal_durations_ms = sorted(set(packet.sample_count / packet.sampling_rate_hz * 1000.0 for packet in packets))
    return {
        "recordType": "nd8_m5_3_validation_summary",
        "requestedDurationSeconds": requested_duration_seconds,
        "callbackPacketCount": len(packets),
        "channelCounts": channel_counts,
        "sampleCountsPerChannel": sample_counts,
        "nominalPacketDurationsMs": nominal_durations_ms,
        "sdkTimestampDeltasMs": timestamp_deltas,
        "pcReceiveDeltasMs": receive_deltas_ms,
        "continuityIssueCounts": dict(issue_counts),
        "callbackErrors": list(adapter.callback_errors),
        "adapterPacketSequenceIsSynthetic": True,
        "hardwareSampleCounterObserved": False,
        "dongleHostMacReady": adapter.host_mac_ready,
        "dongleHostMacSuffix": adapter.host_mac_suffix,
        "timestampSemantics": "SDK-reported milliseconds; first-sample interpretation remains unverified",
    }


def run_validation(com_port: str, data_root: Path, duration_seconds: float, sampling_rate_hz: float) -> Path:
    session = create_session(data_root)
    metadata_log = AppendOnlyJsonl(session / "packet-metadata.jsonl")
    raw_log = AppendOnlyJsonl(session / "raw-eeg-packets.jsonl")
    adapter = Nd8SerialAdapter(
        com_port,
        nominal_sampling_rate_hz=sampling_rate_hz,
        metadata_log=metadata_log,
        raw_packet_log=raw_log,
    )
    started_monotonic_ns = time.monotonic_ns()
    manifest = {
        "recordType": "nd8_m5_3_validation_session",
        "createdUtc": datetime.now(timezone.utc).isoformat(),
        "comPort": com_port,
        "requestedDurationSeconds": duration_seconds,
        "requestedSamplingRateHz": sampling_rate_hz,
        "rawDataFile": "raw-eeg-packets.jsonl",
        "metadataFile": "packet-metadata.jsonl",
        "adapterPacketSequenceIsSynthetic": True,
        "dongleHostMacQueryRequiredBeforeStreaming": True,
    }
    with (session / "session-manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        adapter.open_port()
        adapter.start_streaming()
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        if adapter.state.value == "streaming":
            adapter.stop()
        adapter.close()
    summary = summarize(adapter, duration_seconds)
    summary["elapsedMonotonicSeconds"] = (time.monotonic_ns() - started_monotonic_ns) / 1e9
    with (session / "summary.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return session


def main():
    parser = argparse.ArgumentParser(description="Explicit short ND8 M5.3 serial validation")
    parser.add_argument("--com", required=True)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=float, default=15.0)
    parser.add_argument("--sampling-rate", type=float, default=1000.0)
    args = parser.parse_args()
    if not 10.0 <= args.duration_seconds <= 120.0:
        parser.error("duration must remain between 10 and 120 seconds for this validation")
    if args.sampling_rate != 1000.0:
        parser.error("this validation permits only the confirmed 1000 Hz rate")
    print(run_validation(args.com, args.data_root, args.duration_seconds, args.sampling_rate))


if __name__ == "__main__":
    main()
