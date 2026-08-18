from .models import SampleAssociationResult


class SampleTimeMapper:
    """Maps PC-domain events without upgrading unverified packet timestamps to facts."""

    def __init__(self, timeline, pc_minus_device_seconds=None, device_clock_mapping_verified=False):
        self.timeline = timeline
        self.pc_minus_device_seconds = pc_minus_device_seconds
        self.device_clock_mapping_verified = device_clock_mapping_verified

    def map_pc_event(self, estimated_pc_event_monotonic_ns):
        if not isinstance(estimated_pc_event_monotonic_ns, int) or estimated_pc_event_monotonic_ns < 0:
            return SampleAssociationResult(None, "unavailable", "unavailable", None, None, None, "unknown", False,
                                           "estimated PC event monotonic ns is required")
        if not self.timeline.packets:
            return SampleAssociationResult(None, "unavailable", "unavailable", None, None, None, "unknown", False,
                                           "no EEG packet metadata")
        event_seconds = estimated_pc_event_monotonic_ns / 1e9
        for packet, continuity in zip(self.timeline.packets, self.timeline.continuity):
            if self._can_use_verified_device_timestamp(packet):
                device_event_seconds = event_seconds - self.pc_minus_device_seconds
                packet_start = packet.device_timestamp / 1000.0
                packet_end = packet_start + packet.sample_count / packet.sampling_rate_hz
                if packet_start <= device_event_seconds < packet_end:
                    offset = int(round((device_event_seconds - packet_start) * packet.sampling_rate_hz))
                    return self._result(packet, continuity, offset, "verified_device_timestamp", "medium", 0.5 / packet.sampling_rate_hz, True)
        packet_index = min(range(len(self.timeline.packets)), key=lambda i: abs(
            self.timeline.packets[i].pc_receive_monotonic_ns - estimated_pc_event_monotonic_ns))
        packet = self.timeline.packets[packet_index]
        continuity = self.timeline.continuity[packet_index]
        offset = int(round((estimated_pc_event_monotonic_ns - packet.pc_receive_monotonic_ns) / 1e9 * packet.sampling_rate_hz))
        offset = max(0, min(packet.sample_count - 1, offset))
        return self._result(packet, continuity, offset, "pc_receive_time_fallback", "low",
                            packet.sample_count / packet.sampling_rate_hz, False,
                            "PC receive time is not a hardware sample timestamp")

    def _can_use_verified_device_timestamp(self, packet):
        return (packet.device_timestamp is not None and packet.device_timestamp_unit == "milliseconds" and
                packet.device_timestamp_first_sample_assumed and packet.device_timestamp_hardware_verified and
                self.device_clock_mapping_verified and self.pc_minus_device_seconds is not None)

    @staticmethod
    def _result(packet, continuity, offset, method, quality, uncertainty, hardware_verified, detail=""):
        first_index = packet.first_sample_index if packet.first_sample_index is not None else continuity.cumulative_first_sample_index
        if packet.first_sample_index is not None:
            method = "hardware_counter_" + method
        return SampleAssociationResult(first_index + offset, method, quality, uncertainty, packet.packet_sequence,
                                       first_index, continuity.status, hardware_verified, detail)
