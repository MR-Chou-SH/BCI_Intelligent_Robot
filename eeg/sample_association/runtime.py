"""Live, append-only Quest event to ND8 packet/sample association coordinator."""

import threading

from integration.synchronization.clock_sync import AffineClockMapper

from .gate import PostSyncAssociationGate
from .jsonl import AppendOnlyJsonl


class AssociationCoordinator:
    """Keeps raw evidence external and writes only derived association records."""

    def __init__(self, association_log, gate_log=None, maximum_sync_residual_seconds=0.050,
                 maximum_sync_age_seconds=5.0):
        self.association_log = association_log if hasattr(association_log, "append") else AppendOnlyJsonl(association_log)
        self.gate_log = gate_log if gate_log is None or hasattr(gate_log, "append") else AppendOnlyJsonl(gate_log)
        self.gate = PostSyncAssociationGate()
        self.packets = []
        self.pending_events = []
        self.maximum_sync_residual_seconds = float(maximum_sync_residual_seconds)
        self.maximum_sync_age_seconds = float(maximum_sync_age_seconds)
        self._trial_event_types = {}
        self._lock = threading.RLock()
        # Only for a rounded offset landing at a continuous packet boundary.
        self.packet_boundary_tolerance_samples = 0.5

    def ingest_packet(self, packet, continuity):
        with self._lock:
            decision = self.gate.observe(packet, continuity)
            self.packets.append((packet, continuity, decision))
            if self.gate_log is not None:
                self.gate_log.append({"recordType": "nd8_association_gate", "packetSequence": packet.packet_sequence,
                                      "packetSdkTimestampMs": packet.device_timestamp,
                                      "packetPcReceiveMonotonicNs": packet.pc_receive_monotonic_ns,
                                      "continuity": continuity.to_dict(), "gate": decision.to_dict()})
            self._flush_pending()
            return decision

    def ingest_event(self, pc_event):
        with self._lock:
            event = pc_event.get("originalQuestEvent") if isinstance(pc_event, dict) else None
            event_time = pc_event.get("estimatedPcEventMonotonicNs") if isinstance(pc_event, dict) else None
            base = self._base_record(pc_event, event)
            reason = self._event_order_reason(event)
            if reason:
                self._write_invalid(base, reason)
            elif not isinstance(event_time, int):
                self._write_invalid(base, "quest_pc_clock_mapping_unavailable")
            elif not self._clock_is_acceptable(pc_event, event_time):
                self._write_invalid(base, "quest_pc_clock_mapping_quality_unavailable_or_stale")
            elif self.gate.ready_pc_monotonic_ns is None or event_time < self.gate.ready_pc_monotonic_ns:
                self._write_invalid(base, "event_not_after_post_sync_association_ready")
            else:
                self.pending_events.append((pc_event, base))
                self._flush_pending()

    def finalize(self):
        with self._lock:
            for _, base in self.pending_events:
                self._write_invalid(base, "no_eligible_nd8_packet_before_session_end")
            self.pending_events = []

    def _flush_pending(self):
        remaining = []
        for pc_event, base in self.pending_events:
            result = self._associate(pc_event, base)
            if result is None:
                remaining.append((pc_event, base))
            else:
                self.association_log.append(result)
        self.pending_events = remaining

    def _associate(self, pc_event, base):
        event_ns = pc_event["estimatedPcEventMonotonicNs"]
        eligible = [(packet, continuity, decision) for packet, continuity, decision in self.packets
                    if decision.association_ready and packet.pc_receive_monotonic_ns >= self.gate.ready_pc_monotonic_ns]
        if len(eligible) < 2:
            return None
        mapper = AffineClockMapper(maximum_samples=len(eligible))
        for packet, _, _ in eligible:
            mapper.add(packet.device_timestamp / 1000.0, packet.pc_receive_monotonic_ns / 1e9)
        coefficients = mapper.coefficients()
        if coefficients is None:
            return None
        a, b = coefficients
        estimated_sdk_ms = ((event_ns / 1e9 - b) / a) * 1000.0
        last_end = eligible[-1][0].device_timestamp + eligible[-1][0].sample_count / eligible[-1][0].sampling_rate_hz * 1000.0
        if estimated_sdk_ms >= last_end:
            return None
        candidate_index = next((index for index, (p, _, _) in enumerate(eligible)
                                if p.device_timestamp <= estimated_sdk_ms <
                                p.device_timestamp + p.sample_count / p.sampling_rate_hz * 1000.0), None)
        candidate = eligible[candidate_index] if candidate_index is not None else None
        if candidate is None:
            return self._invalid_record(base, "event_not_covered_by_eligible_nd8_packet")
        packet, continuity, decision = candidate
        raw_offset = (estimated_sdk_ms - packet.device_timestamp) * packet.sampling_rate_hz / 1000.0
        offset = int(round(raw_offset))
        if offset == packet.sample_count and candidate_index + 1 < len(eligible):
            next_packet, next_continuity, next_decision = eligible[candidate_index + 1]
            next_raw_offset = ((estimated_sdk_ms - next_packet.device_timestamp) *
                               next_packet.sampling_rate_hz / 1000.0)
            packets_are_adjacent = (
                packet.packet_sequence is not None and next_packet.packet_sequence == packet.packet_sequence + 1 and
                next_continuity.status == "continuous" and
                abs(next_packet.device_timestamp - (
                    packet.device_timestamp + packet.sample_count / packet.sampling_rate_hz * 1000.0
                )) <= self.packet_boundary_tolerance_samples / packet.sampling_rate_hz * 1000.0
            )
            if packets_are_adjacent and abs(next_raw_offset) <= self.packet_boundary_tolerance_samples:
                packet, continuity, decision = next_packet, next_continuity, next_decision
                offset = 0
        if offset < 0 or offset >= packet.sample_count:
            return self._invalid_record(base, "estimated_sample_offset_outside_packet")
        residual = mapper.residual_rms_seconds()
        first_index = continuity.cumulative_first_sample_index
        base.update({
            "associationValid": True, "invalidReason": None,
            "nd8Gate": decision.to_dict(), "timestampSegmentId": decision.segment_id,
            "associatedPacketSequence": packet.packet_sequence, "associatedPacketSdkTimestampMs": packet.device_timestamp,
            "associatedPacketPcReceiveMonotonicNs": packet.pc_receive_monotonic_ns,
            "packetContinuityStatus": continuity.status, "packetContinuityIssues": list(continuity.issues),
            "estimatedSdkEventTimestampMs": estimated_sdk_ms,
            "estimatedSampleOffset": offset, "estimatedGlobalSampleIndex": first_index + offset,
            "sampleIndexKind": "software_derived_estimate",
            "sampleAnchorState": "vendor_demo_declares_first_point_time_not_independently_verified",
            "mappingMethod": "quest_pc_affine_then_nd8_post_sync_affine",
            "nd8MappingResidualSeconds": residual,
            "nd8MappingUncertaintySeconds": (residual or 0.0) + 0.5 / packet.sampling_rate_hz,
            "hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
        })
        return base

    def _clock_is_acceptable(self, pc_event, event_ns):
        clock = pc_event.get("clockSync") or {}
        residual = clock.get("affineResidualRmsSeconds")
        latest = clock.get("latestAcceptedPcMonotonicNs")
        return (clock.get("status") == "ready" and isinstance(residual, (int, float)) and
                residual <= self.maximum_sync_residual_seconds and isinstance(latest, int) and
                event_ns - latest <= int(self.maximum_sync_age_seconds * 1e9))

    def _base_record(self, pc_event, event):
        event = event if isinstance(event, dict) else {}
        return {"recordType": "quest_nd8_derived_association", "sessionId": event.get("sessionId"),
                "trialId": event.get("trialId"), "stimulusEventType": event.get("eventType"),
                "stimulusSequence": event.get("sequence"), "originalQuestEvent": event,
                "pcEventRecordConnectionId": pc_event.get("connectionId") if isinstance(pc_event, dict) else None,
                "estimatedPcEventMonotonicNs": pc_event.get("estimatedPcEventMonotonicNs") if isinstance(pc_event, dict) else None,
                "questPcClockSync": pc_event.get("clockSync") if isinstance(pc_event, dict) else None,
                "hardwareTimingVerified": False, "physicalOpticalTimingVerified": False}

    def _event_order_reason(self, event):
        if not isinstance(event, dict) or not event.get("sessionId") or event.get("eventType") not in ("stimulus_started_software", "stimulus_stopped_software"):
            return "missing_or_unsupported_stimulus_event"
        key = (event["sessionId"], event.get("trialId", ""))
        seen = self._trial_event_types.setdefault(key, [])
        kind = event["eventType"]
        if kind in seen:
            return "duplicate_stimulus_event_type"
        if kind == "stimulus_stopped_software" and "stimulus_started_software" not in seen:
            return "stimulus_stop_without_start"
        seen.append(kind)
        return None

    def _write_invalid(self, base, reason):
        self.association_log.append(self._invalid_record(base, reason))

    @staticmethod
    def _invalid_record(base, reason):
        base.update({"associationValid": False, "invalidReason": reason,
                     "mappingMethod": "unavailable", "sampleIndexKind": "unavailable",
                     "sampleAnchorState": "unverified", "hardwareTimingVerified": False,
                     "physicalOpticalTimingVerified": False})
        return base
