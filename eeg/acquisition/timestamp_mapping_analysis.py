"""Offline software-level mapping analysis for an append-only ND8 session."""

import argparse
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(math.floor(index))
    upper = int(math.ceil(index))
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def distribution(values):
    if not values:
        return {"count": 0}
    mean = sum(values) / len(values)
    return {
        "count": len(values),
        "mean": mean,
        "median": percentile(values, 0.5),
        "min": min(values),
        "max": max(values),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "rmsAboutMean": math.sqrt(sum((value - mean) ** 2 for value in values) / len(values)),
    }


def analyze_packet_segment(packets):
    """Fit one uninterrupted SDK timestamp segment to PC receive evidence."""
    if len(packets) < 2:
        raise ValueError("at least two packet records are required")
    sdk_ms = [float(packet["device_timestamp"]) for packet in packets]
    mono_ns = [int(packet["pc_receive_monotonic_ns"]) for packet in packets]
    utc_ms = [datetime.fromisoformat(packet["pc_receive_utc"]).timestamp() * 1000.0 for packet in packets]
    sample_counts = [int(packet["sample_count"]) for packet in packets]
    rates = [float(packet["sampling_rate_hz"]) for packet in packets]
    x_mean = sum(sdk_ms) / len(sdk_ms)
    y_mean = sum(mono_ns) / len(mono_ns)
    x_variance = sum((x - x_mean) ** 2 for x in sdk_ms)
    slope_ns_per_ms = sum((x - x_mean) * (y - y_mean) for x, y in zip(sdk_ms, mono_ns)) / x_variance
    intercept_ns = y_mean - slope_ns_per_ms * x_mean
    residual_ms = [(y - (slope_ns_per_ms * x + intercept_ns)) / 1e6 for x, y in zip(sdk_ms, mono_ns)]
    offsets_ms = [pc - sdk for pc, sdk in zip(utc_ms, sdk_ms)]
    elapsed_sdk_seconds = [(x - sdk_ms[0]) / 1000.0 for x in sdk_ms]
    elapsed_mean = sum(elapsed_sdk_seconds) / len(elapsed_sdk_seconds)
    offset_mean = sum(offsets_ms) / len(offsets_ms)
    elapsed_variance = sum((x - elapsed_mean) ** 2 for x in elapsed_sdk_seconds)
    offset_drift_ms_per_second = (sum((x - elapsed_mean) * (y - offset_mean)
                                      for x, y in zip(elapsed_sdk_seconds, offsets_ms)) / elapsed_variance)
    return {
        "packetCount": len(packets),
        "firstPacketSequence": packets[0].get("packet_sequence"),
        "lastPacketSequence": packets[-1].get("packet_sequence"),
        "pcReceiveUnixMinusSdkTimestampMs": distribution(offsets_ms),
        "pcMonotonicFromSdkTimestamp": {
            "slopeNanosecondsPerMillisecond": slope_ns_per_ms,
            "driftPpmRelativeToOneMillisecond": (slope_ns_per_ms / 1_000_000.0 - 1.0) * 1_000_000.0,
            "residualMilliseconds": distribution(residual_ms),
            "offsetDriftMillisecondsPerSecond": offset_drift_ms_per_second,
        },
    }


def analyze_metadata_records(records):
    """Analyze packet metadata without upgrading the result to hardware timing."""
    packets = [record["packet"] for record in records]
    if len(packets) < 2:
        raise ValueError("at least two packet records are required")
    timestamp_deltas = [current["device_timestamp"] - previous["device_timestamp"]
                        for previous, current in zip(packets, packets[1:])]
    receive_deltas_ms = [(current["pc_receive_monotonic_ns"] - previous["pc_receive_monotonic_ns"]) / 1e6
                         for previous, current in zip(packets, packets[1:])]
    expected_deltas = [packet["sample_count"] / packet["sampling_rate_hz"] * 1000.0 for packet in packets[:-1]]
    anomalies = []
    segment_starts = [0]
    for index, (delta, expected) in enumerate(zip(timestamp_deltas, expected_deltas), start=1):
        if delta != expected:
            anomaly = {
                "previousPacketSequence": packets[index - 1].get("packet_sequence"),
                "packetSequence": packets[index].get("packet_sequence"),
                "previousSdkTimestampMs": packets[index - 1]["device_timestamp"],
                "sdkTimestampMs": packets[index]["device_timestamp"],
                "sdkTimestampDeltaMs": delta,
                "expectedDurationMs": expected,
                "deltaErrorMs": delta - expected,
                "pcReceiveDeltaMs": receive_deltas_ms[index - 1],
                "mismatchBeyondOneMs": abs(delta - expected) > 1.0,
                "segmentBoundary": abs(delta - expected) > expected or delta <= 0,
            }
            anomalies.append(anomaly)
            if anomaly["segmentBoundary"]:
                segment_starts.append(index)
    segment_starts.append(len(packets))
    segments = [analyze_packet_segment(packets[start:end]) for start, end in zip(segment_starts, segment_starts[1:])]
    primary_segment = max(segments, key=lambda segment: segment["packetCount"])
    issue_counts = Counter(issue for record in records for issue in record["continuity"]["issues"])
    return {
        "recordType": "nd8_timestamp_mapping_analysis",
        "mappingScope": "software-level SDK timestamp to PC receive-time evidence only",
        "packetCount": len(packets),
        "adapterPacketSequenceIsSynthetic": True,
        "sdkTimestampCadenceMs": distribution(timestamp_deltas),
        "pcReceiveCadenceMs": distribution(receive_deltas_ms),
        "sdkTimestampAnomalies": anomalies,
        "continuityIssueCounts": dict(issue_counts),
        "segments": segments,
        "primaryStableSegment": primary_segment,
        "hardwareTimingVerified": False,
    }


def analyze_session(session: Path, output_name="timestamp-mapping-analysis.json") -> Path:
    metadata_path = session / "packet-metadata.jsonl"
    records = [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line]
    analysis_path = session / output_name
    result = analyze_metadata_records(records)
    with analysis_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return analysis_path


def main():
    parser = argparse.ArgumentParser(description="Offline ND8 SDK-to-PC timestamp mapping analysis")
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output-name", default="timestamp-mapping-analysis.json")
    args = parser.parse_args()
    print(analyze_session(args.session, args.output_name))


if __name__ == "__main__":
    main()
