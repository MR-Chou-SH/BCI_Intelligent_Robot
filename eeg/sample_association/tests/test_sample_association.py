import unittest

from eeg.sample_association.association import SampleTimeMapper
from eeg.sample_association.events import associate_m5_event
from eeg.sample_association.models import EegPacketMetadata
from eeg.sample_association.timeline import EegPacketTimeline


def packet(timestamp, receive_ns, sequence, first_index=None, samples=10, channels=8, verified=False):
    return EegPacketMetadata(timestamp, receive_ns, "2026-01-01T00:00:00Z", samples, channels, 1000.0,
                             sequence, first_index, "milliseconds", verified, verified)


class SampleAssociationTests(unittest.TestCase):
    def test_verified_timestamp_interpolation_uses_hardware_counter(self):
        timeline = EegPacketTimeline()
        timeline.append(packet(1_000.0, 5_000_000_000, 7, 100, verified=True))
        result = SampleTimeMapper(timeline, pc_minus_device_seconds=4.0, device_clock_mapping_verified=True).map_pc_event(5_005_000_000)
        self.assertEqual(105, result.estimated_sample_index)
        self.assertEqual("hardware_counter_verified_device_timestamp", result.mapping_method)

    def test_pc_receive_fallback_is_explicitly_low_quality(self):
        timeline = EegPacketTimeline()
        timeline.append(packet(None, 1_000_000_000, 1))
        result = SampleTimeMapper(timeline).map_pc_event(1_004_000_000)
        self.assertEqual("pc_receive_time_fallback", result.mapping_method)
        self.assertEqual("low", result.quality)
        self.assertFalse(result.hardware_timing_verified)

    def test_detects_sequence_gap_duplicate_and_timestamp_regression(self):
        timeline = EegPacketTimeline()
        timeline.append(packet(1000.0, 1, 1))
        gap = timeline.append(packet(1030.0, 2, 3))
        duplicate = timeline.append(packet(1040.0, 3, 3))
        regression = timeline.append(packet(900.0, 4, 4))
        self.assertIn("packet_sequence_gap", gap.issues)
        self.assertIn("duplicate_packet_sequence", duplicate.issues)
        self.assertIn("timestamp_regression", regression.issues)

    def test_detects_shape_inconsistency(self):
        timeline = EegPacketTimeline()
        timeline.append(packet(1000.0, 1, 1))
        record = timeline.append(packet(1010.0, 2, 2, samples=11, channels=7))
        self.assertIn("inconsistent_sample_count", record.issues)
        self.assertIn("inconsistent_channel_count", record.issues)

    def test_detects_timestamp_delta_mismatch_against_packet_duration(self):
        timeline = EegPacketTimeline()
        timeline.append(packet(1000.0, 1, 1, samples=200))
        record = timeline.append(packet(1136.0, 2, 2, samples=200))
        self.assertIn("timestamp_delta_mismatch", record.issues)

    def test_m5_event_record_produces_epoch_association(self):
        timeline = EegPacketTimeline()
        timeline.append(packet(None, 2_000_000_000, 1, first_index=20))
        record = {"estimatedPcEventMonotonicNs": 2_004_000_000,
                  "originalQuestEvent": {"sessionId": "s", "trialId": "t", "eventType": "stimulus_started_software", "sequence": 1}}
        association = associate_m5_event(record, SampleTimeMapper(timeline), -0.14, 3.0)
        self.assertEqual("s", association.session_id)
        self.assertEqual(24, association.association.estimated_sample_index)
        self.assertEqual(-0.14, association.intended_epoch_start_seconds)

    def test_rejects_incomplete_metadata(self):
        with self.assertRaises(ValueError):
            EegPacketTimeline().append(EegPacketMetadata(None, 0, "", 0, 8, 1000.0))
        with self.assertRaises(ValueError):
            associate_m5_event({}, SampleTimeMapper(EegPacketTimeline()))


if __name__ == "__main__":
    unittest.main()
