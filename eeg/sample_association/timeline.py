from .models import EegPacketMetadata, PacketContinuityRecord


class EegPacketTimeline:
    """Append-only packet metadata timeline with explicit, non-repairing diagnostics."""

    def __init__(self):
        self.packets = []
        self.continuity = []
        self._next_synthetic_sample_index = 0

    def append(self, packet: EegPacketMetadata):
        if not isinstance(packet, EegPacketMetadata):
            raise TypeError("packet must be EegPacketMetadata")
        if packet.sample_count <= 0 or packet.channel_count <= 0 or packet.sampling_rate_hz <= 0:
            raise ValueError("packet sample_count, channel_count, and sampling_rate_hz must be positive")
        issues = []
        previous = self.packets[-1] if self.packets else None
        first_index = packet.first_sample_index
        if first_index is None:
            first_index = self._next_synthetic_sample_index
        if previous is not None:
            if packet.packet_sequence is not None and previous.packet_sequence is not None:
                if packet.packet_sequence == previous.packet_sequence:
                    issues.append("duplicate_packet_sequence")
                elif packet.packet_sequence < previous.packet_sequence:
                    issues.append("out_of_order_packet_sequence")
                elif packet.packet_sequence > previous.packet_sequence + 1:
                    issues.append("packet_sequence_gap")
            if packet.channel_count != previous.channel_count:
                issues.append("inconsistent_channel_count")
            if packet.sample_count != previous.sample_count:
                issues.append("inconsistent_sample_count")
            if packet.device_timestamp is not None and previous.device_timestamp is not None:
                if packet.device_timestamp < previous.device_timestamp:
                    issues.append("timestamp_regression")
                elif packet.device_timestamp_unit == "milliseconds" and previous.device_timestamp_unit == "milliseconds":
                    expected = previous.device_timestamp + previous.sample_count / previous.sampling_rate_hz * 1000.0
                    timestamp_delta_error = abs(packet.device_timestamp - expected)
                    if timestamp_delta_error > 1.0:
                        issues.append("timestamp_delta_mismatch")
                    if timestamp_delta_error > (previous.sample_count / previous.sampling_rate_hz * 1000.0):
                        issues.append("timestamp_jump")
        status = "continuous" if not issues else "anomaly"
        record = PacketContinuityRecord(packet.packet_sequence, first_index, status, tuple(issues))
        self.packets.append(packet)
        self.continuity.append(record)
        self._next_synthetic_sample_index = max(self._next_synthetic_sample_index, first_index + packet.sample_count)
        return record
