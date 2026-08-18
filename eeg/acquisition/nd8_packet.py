"""Immutable representation of one SDK EEG callback payload."""

from dataclasses import dataclass
from datetime import datetime, timezone
from numbers import Real
from time import monotonic_ns
from typing import Any, Optional

from eeg.sample_association.models import EegPacketMetadata


@dataclass(frozen=True)
class Nd8Packet:
    """Raw 8×N callback data plus the local receive-time evidence.

    ``sdk_timestamp_ms`` is retained as reported by the SDK. Its documented
    first-sample meaning is not marked hardware-verified until real-device
    evidence has been reviewed.
    """

    sdk_timestamp_ms: float
    samples: tuple[tuple[float, ...], ...]
    pc_receive_monotonic_ns: int
    pc_receive_utc: str
    packet_sequence: int
    nominal_sampling_rate_hz: float = 1000.0

    @classmethod
    def from_sdk_payload(
        cls,
        payload: dict[str, Any],
        packet_sequence: int,
        nominal_sampling_rate_hz: float = 1000.0,
        receive_monotonic_ns: Optional[int] = None,
        receive_utc: Optional[str] = None,
    ) -> "Nd8Packet":
        if not isinstance(payload, dict):
            raise ValueError("ND8 payload must be a mapping")
        timestamp = payload.get("timestamp")
        data = payload.get("data")
        if not isinstance(timestamp, Real):
            raise ValueError("ND8 payload timestamp must be numeric")
        if not _is_array_like(data) or len(data) != 8:
            raise ValueError("ND8 payload must contain exactly 8 channels")
        channels = []
        sample_count = None
        for channel in data:
            if not _is_array_like(channel):
                raise ValueError("each ND8 channel must be a sample sequence")
            values = tuple(float(value) for value in channel)
            if not values:
                raise ValueError("ND8 packet must contain at least one sample per channel")
            if sample_count is None:
                sample_count = len(values)
            elif len(values) != sample_count:
                raise ValueError("ND8 channels have inconsistent sample counts")
            channels.append(values)
        if nominal_sampling_rate_hz <= 0:
            raise ValueError("nominal sampling rate must be positive")
        return cls(
            sdk_timestamp_ms=float(timestamp),
            samples=tuple(channels),
            pc_receive_monotonic_ns=monotonic_ns() if receive_monotonic_ns is None else receive_monotonic_ns,
            pc_receive_utc=(datetime.now(timezone.utc).isoformat() if receive_utc is None else receive_utc),
            packet_sequence=packet_sequence,
            nominal_sampling_rate_hz=float(nominal_sampling_rate_hz),
        )

    @property
    def channel_count(self) -> int:
        return len(self.samples)

    @property
    def sample_count(self) -> int:
        return len(self.samples[0])

    @property
    def packet_duration_seconds(self) -> float:
        return self.sample_count / self.nominal_sampling_rate_hz

    def to_metadata(self) -> EegPacketMetadata:
        return EegPacketMetadata(
            device_timestamp=self.sdk_timestamp_ms,
            pc_receive_monotonic_ns=self.pc_receive_monotonic_ns,
            pc_receive_utc=self.pc_receive_utc,
            sample_count=self.sample_count,
            channel_count=self.channel_count,
            sampling_rate_hz=self.nominal_sampling_rate_hz,
            packet_sequence=self.packet_sequence,
            device_timestamp_unit="milliseconds",
            # Vendor demo wording calls this the "first point time", but no
            # independent protocol/hardware validation has established that
            # semantic for this project session.
            device_timestamp_first_sample_assumed=False,
            device_timestamp_hardware_verified=False,
            source="nd8_sdk_serial_callback",
        )

    def metadata_log_record(self, continuity) -> dict[str, Any]:
        """JSON-ready metadata only; raw EEG remains in the in-memory packet queue."""
        return {
            "recordType": "nd8_packet_metadata",
            "packet": self.to_metadata().to_dict(),
            "packetDurationSeconds": self.packet_duration_seconds,
            "continuity": continuity.to_dict(),
            "rawDataStoredInPacketQueue": True,
            "rawDataSerialized": False,
        }

    def raw_log_record(self) -> dict[str, Any]:
        """Append-only raw packet record for a separately named live session."""
        return {
            "recordType": "nd8_raw_packet",
            "packetSequence": self.packet_sequence,
            "sdkTimestampMs": self.sdk_timestamp_ms,
            "pcReceiveMonotonicNs": self.pc_receive_monotonic_ns,
            "pcReceiveUtc": self.pc_receive_utc,
            "channelCount": self.channel_count,
            "sampleCountPerChannel": self.sample_count,
            "nominalSamplingRateHz": self.nominal_sampling_rate_hz,
            "samples": self.samples,
        }


def _is_array_like(value: Any) -> bool:
    """Accept SDK lists and NumPy-like arrays without importing NumPy."""
    if isinstance(value, (str, bytes)):
        return False
    try:
        len(value)
        iter(value)
    except TypeError:
        return False
    return True
