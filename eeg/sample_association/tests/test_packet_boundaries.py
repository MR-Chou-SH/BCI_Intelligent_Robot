import unittest

from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord
from eeg.sample_association.runtime import AssociationCoordinator


class MemoryLog:
    def __init__(self):
        self.records = []

    def append(self, value):
        self.records.append(value)


def packet(sequence, timestamp_ms, receive_ns):
    return EegPacketMetadata(timestamp_ms, receive_ns, "2026-01-01T00:00:00+00:00", 200, 8, 1000.0,
                             sequence, sequence * 200, "milliseconds", False, False)


def event(event_time_ns):
    return {"connectionId": "boundary", "pcReceiveMonotonicNs": event_time_ns,
            "estimatedPcEventMonotonicNs": event_time_ns,
            "clockSync": {"status": "ready", "acceptedSampleCount": 20,
                          "affineResidualRmsSeconds": 0.001,
                          "latestAcceptedPcMonotonicNs": event_time_ns},
            "originalQuestEvent": {"sessionId": "s", "trialId": "t",
                                    "eventType": "stimulus_started_software", "sequence": 1}}


class PacketBoundaryAssociationTests(unittest.TestCase):
    def setUp(self):
        self.output = MemoryLog()
        self.gates = MemoryLog()
        self.coordinator = AssociationCoordinator(self.output, self.gates)
        for sequence in range(13):
            self.coordinator.ingest_packet(packet(sequence, 1_700_000_000_000 + sequence * 200,
                                                  10_000_000_000 + sequence * 200_000_000),
                                           PacketContinuityRecord(sequence, sequence * 200, "continuous"))

    def test_exact_packet_start_maps_to_next_packet_zero(self):
        self.coordinator.ingest_event(event(12_400_000_000))
        record = self.output.records[0]
        self.assertTrue(record["associationValid"])
        self.assertEqual(12, record["associatedPacketSequence"])
        self.assertEqual(0, record["estimatedSampleOffset"])

    def test_just_after_boundary_maps_to_next_packet_zero(self):
        self.coordinator.ingest_event(event(12_400_100_000))
        record = self.output.records[0]
        self.assertTrue(record["associationValid"])
        self.assertEqual(12, record["associatedPacketSequence"])
        self.assertEqual(0, record["estimatedSampleOffset"])

    def test_just_before_boundary_stays_in_previous_packet(self):
        self.coordinator.ingest_event(event(12_399_400_000))
        record = self.output.records[0]
        self.assertTrue(record["associationValid"])
        self.assertEqual(11, record["associatedPacketSequence"])
        self.assertEqual(199, record["estimatedSampleOffset"])

    def test_outside_available_packet_neighborhood_is_rejected(self):
        self.coordinator.ingest_event(event(12_600_600_000))
        self.coordinator.finalize()
        self.assertFalse(self.output.records[0]["associationValid"])

    def test_packet_gap_is_not_reassociated_across_boundary(self):
        output = MemoryLog()
        coordinator = AssociationCoordinator(output, MemoryLog())
        for sequence in range(12):
            continuity = PacketContinuityRecord(sequence, sequence * 200, "continuous")
            coordinator.ingest_packet(packet(sequence, 1_700_000_000_000 + sequence * 200,
                                             10_000_000_000 + sequence * 200_000_000), continuity)
        coordinator.ingest_packet(packet(13, 1_700_000_002_400, 12_600_000_000),
                                   PacketContinuityRecord(13, 2400, "anomaly", ("packet_sequence_gap",)))
        coordinator.ingest_event(event(12_400_100_000))
        self.assertFalse(output.records[-1]["associationValid"])


if __name__ == "__main__":
    unittest.main()
