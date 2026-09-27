"""Read-only replay of a recorded ND8 raw packet stream.

The replay deliberately yields the same ``(Nd8Packet, continuity)`` pair that
``Nd8SerialAdapter`` sends to its live packet observer.  It is a source
substitution, not a second decoder or an offline-only analysis path.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Callable, Iterator, Optional

from eeg.sample_association.models import PacketContinuityRecord
from eeg.sample_association.timeline import EegPacketTimeline

from .nd8_packet import Nd8Packet


class RecordedND8ReplayError(ValueError):
    """The recorded session cannot safely be used as an ND8 source."""


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise RecordedND8ReplayError(
                    "invalid JSONL at {}:{}: {}".format(path, line_number, error)
                ) from error
            if not isinstance(value, dict):
                raise RecordedND8ReplayError(
                    "JSONL record at {}:{} is not an object".format(path, line_number)
                )
            yield value


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class RecordedND8Replay:
    """Replay immutable raw ND8 packets from a Golden Session directory.

    ``speed='max'`` does not sleep and is deterministic for automated tests.
    ``speed='realtime'`` follows recorded monotonic receive intervals.  A
    positive numeric ``speed`` is a rate multiplier (2.0 is twice as fast).
    """

    def __init__(self, session_root, speed="max", sleep: Callable[[float], None] = time.sleep):
        self.session_root = Path(session_root)
        self.manifest_path = self.session_root / "manifest.json"
        if not self.manifest_path.is_file():
            raise RecordedND8ReplayError("recorded session manifest is missing: {}".format(self.manifest_path))
        try:
            self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RecordedND8ReplayError("recorded session manifest is invalid JSON") from error
        if not isinstance(self.manifest, dict):
            raise RecordedND8ReplayError("recorded session manifest must be an object")
        self.raw_path = self.session_root / self.manifest.get("rawEegFile", "raw-eeg.jsonl")
        self.metadata_path = self.session_root / self.manifest.get("packetMetadataFile", "packet-metadata.jsonl")
        if not self.raw_path.is_file():
            raise RecordedND8ReplayError("recorded raw EEG stream is missing: {}".format(self.raw_path))
        if speed not in ("max", "realtime"):
            try:
                speed = float(speed)
            except (TypeError, ValueError) as error:
                raise ValueError("speed must be 'max', 'realtime', or a positive number") from error
            if speed <= 0:
                raise ValueError("numeric replay speed must be positive")
        self.speed = speed
        self.sleep = sleep

    def _continuity_by_sequence(self):
        result = {}
        if not self.metadata_path.is_file():
            return result
        for record in _read_jsonl(self.metadata_path):
            if record.get("recordType") not in ("nd8_packet_metadata", "m13_7_packet_metadata"):
                continue
            packet = record.get("packet", record)
            continuity = record.get("continuity")
            sequence = packet.get("packet_sequence", packet.get("packetSequence"))
            if sequence is None or not isinstance(continuity, dict):
                continue
            result[int(sequence)] = PacketContinuityRecord(
                packet_sequence=continuity.get("packet_sequence", continuity.get("packetSequence", int(sequence))),
                cumulative_first_sample_index=int(
                    continuity.get("cumulative_first_sample_index", continuity.get("cumulativeFirstSampleIndex", 0))
                ),
                status=str(continuity.get("status", "unknown")),
                issues=tuple(continuity.get("issues", ())),
            )
        return result

    @staticmethod
    def _packet(record):
        if record.get("recordType") not in ("nd8_raw_packet", "m13_7_raw_packet"):
            raise RecordedND8ReplayError("unexpected recordType in raw EEG stream: {}".format(record.get("recordType")))
        samples = record.get("samples")
        if not isinstance(samples, list) or len(samples) != 8:
            raise RecordedND8ReplayError("raw ND8 packet must contain exactly 8 channels")
        normalized = []
        sample_count = None
        for channel in samples:
            if not isinstance(channel, list) or not channel:
                raise RecordedND8ReplayError("raw ND8 packet channels must be non-empty lists")
            values = tuple(float(value) for value in channel)
            if sample_count is None:
                sample_count = len(values)
            elif len(values) != sample_count:
                raise RecordedND8ReplayError("raw ND8 packet channels have inconsistent lengths")
            normalized.append(values)
        sequence = record.get("packetSequence", record.get("packet_sequence"))
        timestamp = record.get("sdkTimestampMs", record.get("sdk_timestamp_ms"))
        receive_ns = record.get("pcReceiveMonotonicNs", record.get("pc_receive_monotonic_ns"))
        receive_utc = record.get("pcReceiveUtc", record.get("pc_receive_utc", _utc_now()))
        sampling_rate = record.get("nominalSamplingRateHz", record.get("nominal_sampling_rate_hz", 1000.0))
        if sequence is None or timestamp is None or receive_ns is None:
            raise RecordedND8ReplayError("raw ND8 packet is missing sequence/timestamp/receive timing")
        return Nd8Packet(
            sdk_timestamp_ms=float(timestamp),
            samples=tuple(normalized),
            pc_receive_monotonic_ns=int(receive_ns),
            pc_receive_utc=str(receive_utc),
            packet_sequence=int(sequence),
            nominal_sampling_rate_hz=float(sampling_rate),
        )

    def iter_packets(self) -> Iterator[tuple[Nd8Packet, PacketContinuityRecord]]:
        """Yield packets in recorded source order without changing the source."""
        known_continuity = self._continuity_by_sequence()
        timeline = EegPacketTimeline()
        previous_sequence = None
        for record in _read_jsonl(self.raw_path):
            packet = self._packet(record)
            if previous_sequence is not None and packet.packet_sequence <= previous_sequence:
                raise RecordedND8ReplayError("raw packet sequence is not strictly increasing")
            previous_sequence = packet.packet_sequence
            continuity = known_continuity.get(packet.packet_sequence)
            if continuity is None:
                continuity = timeline.append(packet.to_metadata())
            else:
                # Keep the timeline warm so a metadata-light recording still
                # has the same next-sample semantics if later records omit it.
                timeline.append(packet.to_metadata())
            yield packet, continuity

    def replay(self, on_packet: Callable[[Nd8Packet, PacketContinuityRecord], None]) -> dict:
        """Deliver all packets to a live-compatible callback."""
        count = 0
        first_ns: Optional[int] = None
        previous_ns: Optional[int] = None
        for packet, continuity in self.iter_packets():
            current_ns = packet.pc_receive_monotonic_ns
            if first_ns is None:
                first_ns = current_ns
            if previous_ns is not None:
                delta_seconds = max(0.0, (current_ns - previous_ns) / 1_000_000_000.0)
                if self.speed == "realtime":
                    self.sleep(delta_seconds)
                elif isinstance(self.speed, (int, float)):
                    self.sleep(delta_seconds / float(self.speed))
            previous_ns = current_ns
            on_packet(packet, continuity)
            count += 1
        return {
            "status": "PASS",
            "sourceType": "recorded_nd8_replay",
            "sessionId": self.manifest.get("sessionId"),
            "packetCount": count,
            "firstPacketMonotonicNs": first_ns,
            "lastPacketMonotonicNs": previous_ns,
            "speed": self.speed,
            "sourceManifest": str(self.manifest_path),
        }


__all__ = ["RecordedND8Replay", "RecordedND8ReplayError"]
