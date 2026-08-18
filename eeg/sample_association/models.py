from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass(frozen=True)
class EegPacketMetadata:
    """Packet metadata only; raw sample arrays deliberately remain outside M5.3."""

    device_timestamp: Optional[float]
    pc_receive_monotonic_ns: int
    pc_receive_utc: str
    sample_count: int
    channel_count: int
    sampling_rate_hz: float
    packet_sequence: Optional[int] = None
    first_sample_index: Optional[int] = None
    device_timestamp_unit: str = "unknown"
    device_timestamp_first_sample_assumed: bool = False
    device_timestamp_hardware_verified: bool = False
    source: str = "observed_packet"

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class PacketContinuityRecord:
    packet_sequence: Optional[int]
    cumulative_first_sample_index: int
    status: str
    issues: tuple[str, ...] = ()

    def to_dict(self):
        value = asdict(self)
        value["issues"] = list(self.issues)
        return value


@dataclass(frozen=True)
class SampleAssociationResult:
    estimated_sample_index: Optional[int]
    mapping_method: str
    quality: str
    uncertainty_seconds: Optional[float]
    reference_packet_sequence: Optional[int]
    reference_sample_index: Optional[int]
    continuity_status: str
    hardware_timing_verified: bool
    detail: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class EpochAssociationRecord:
    session_id: str
    trial_id: str
    stimulus_event_type: str
    stimulus_sequence: int
    estimated_pc_event_monotonic_ns: int
    intended_epoch_start_seconds: float
    intended_epoch_end_seconds: float
    association: SampleAssociationResult
    source_event: dict = field(default_factory=dict)

    def to_dict(self):
        value = asdict(self)
        value["association"] = self.association.to_dict()
        return value
