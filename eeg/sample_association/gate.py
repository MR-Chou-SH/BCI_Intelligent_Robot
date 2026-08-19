"""Runtime post-sync gate for ND8 software-level association eligibility."""

from dataclasses import dataclass
from enum import Enum


UNIX_MS_MINIMUM = 946684800000  # 2000-01-01; excludes the observed startup domain.


class AssociationGateState(str, Enum):
    PRE_SYNC = "pre_sync"
    TRANSITION = "transition"
    STABILIZING = "stabilizing_unix_ms"
    READY = "association_ready"
    CONTINUITY_LOST = "continuity_lost"


@dataclass(frozen=True)
class GateDecision:
    state: str
    association_ready: bool
    segment_id: int
    reason: str
    stable_packet_count: int
    stable_span_ms: float

    def to_dict(self):
        return self.__dict__.copy()


class PostSyncAssociationGate:
    """Observes real packet evidence; it never uses packet count alone as a gate."""

    def __init__(self, minimum_packets=10, minimum_span_ms=1800.0, cadence_tolerance_ms=2.0):
        self.minimum_packets = int(minimum_packets)
        self.minimum_span_ms = float(minimum_span_ms)
        self.cadence_tolerance_ms = float(cadence_tolerance_ms)
        self.state = AssociationGateState.PRE_SYNC
        self.segment_id = 0
        self._stable = []
        self.ready_pc_monotonic_ns = None
        self._previous_timestamp_was_unix_ms = False

    def observe(self, packet, continuity):
        timestamp = packet.device_timestamp
        unix_ms = timestamp is not None and float(timestamp) >= UNIX_MS_MINIMUM
        issues = set(continuity.issues)
        severe = {"timestamp_jump", "timestamp_regression", "packet_sequence_gap",
                  "duplicate_packet_sequence", "out_of_order_packet_sequence",
                  "inconsistent_sample_count", "inconsistent_channel_count"}
        transition_issues = {"timestamp_delta_mismatch", "timestamp_jump"}
        recognized_domain_transition = (
            unix_ms
            and not self._previous_timestamp_was_unix_ms
            and "timestamp_jump" in issues
            and issues.issubset(transition_issues)
        )
        if recognized_domain_transition:
            # The ND8 SDK can move from startup-relative milliseconds to Unix
            # milliseconds after the first hardware timestamp anchor.  The
            # timeline retains the jump as raw diagnostic evidence; the gate
            # treats this narrowly defined, expected domain boundary as a new
            # post-sync segment rather than a loss of packet continuity.
            self.segment_id += 1
            self._stable = [packet]
            self.ready_pc_monotonic_ns = None
            self.state = AssociationGateState.TRANSITION
            self._previous_timestamp_was_unix_ms = True
            return self._decision("startup_relative_to_unix_ms_timestamp_domain_transition")
        if issues.intersection(severe):
            self.segment_id += 1
            self._stable = []
            self.ready_pc_monotonic_ns = None
            self.state = AssociationGateState.CONTINUITY_LOST
            self._previous_timestamp_was_unix_ms = unix_ms
            return self._decision("continuity_or_timestamp_discontinuity:" + ",".join(sorted(issues)))
        if not unix_ms:
            self._stable = []
            self.ready_pc_monotonic_ns = None
            self.state = AssociationGateState.PRE_SYNC
            self._previous_timestamp_was_unix_ms = False
            return self._decision("sdk_timestamp_not_unix_milliseconds")
        self._previous_timestamp_was_unix_ms = True
        if self.state in (AssociationGateState.PRE_SYNC, AssociationGateState.CONTINUITY_LOST):
            self.segment_id += 1
            self._stable = []
            self.state = AssociationGateState.TRANSITION
        self._stable.append(packet)
        if len(self._stable) >= 2:
            previous = self._stable[-2]
            expected = previous.sample_count / previous.sampling_rate_hz * 1000.0
            delta = packet.device_timestamp - previous.device_timestamp
            if abs(delta - expected) > self.cadence_tolerance_ms:
                self.segment_id += 1
                self._stable = [packet]
                self.ready_pc_monotonic_ns = None
                self.state = AssociationGateState.TRANSITION
                return self._decision("timestamp_cadence_outside_tolerance")
        span = self._span_ms()
        if len(self._stable) >= self.minimum_packets and span >= self.minimum_span_ms:
            self.state = AssociationGateState.READY
            if self.ready_pc_monotonic_ns is None:
                self.ready_pc_monotonic_ns = packet.pc_receive_monotonic_ns
            return self._decision("stable_unix_ms_timestamp_and_continuity_window_observed")
        self.state = AssociationGateState.STABILIZING
        return self._decision("observing_stable_unix_ms_timestamp_and_continuity_window")

    def _span_ms(self):
        if len(self._stable) < 2:
            return 0.0
        return float(self._stable[-1].device_timestamp - self._stable[0].device_timestamp)

    def _decision(self, reason):
        return GateDecision(self.state.value, self.state == AssociationGateState.READY, self.segment_id,
                            reason, len(self._stable), self._span_ms())
