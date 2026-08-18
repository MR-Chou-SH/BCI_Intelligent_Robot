import unittest

from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord
from eeg.sample_association.runtime import AssociationCoordinator


class MemoryLog:
    def __init__(self): self.records = []
    def append(self, value): self.records.append(value)


def packet(sequence, timestamp, receive_ns):
    return EegPacketMetadata(timestamp, receive_ns, "2026-01-01T00:00:00+00:00", 200, 8, 1000.0,
                             sequence, None, "milliseconds", False, False)


class RuntimeAssociationTests(unittest.TestCase):
    def setUp(self):
        self.output = MemoryLog(); self.gates = MemoryLog()
        self.coordinator = AssociationCoordinator(self.output, self.gates)

    def feed_stable(self):
        for i in range(12):
            p = packet(i, 1_700_000_000_000 + i * 200, 10_000_000_000 + i * 200_000_000)
            self.coordinator.ingest_packet(p, PacketContinuityRecord(i, i * 200, "continuous"))

    def event(self, event_time=11_850_000_000, event_type="stimulus_started_software"):
        return {"connectionId": "c", "pcReceiveMonotonicNs": event_time,
                "estimatedPcEventMonotonicNs": event_time,
                "clockSync": {"status": "ready", "acceptedSampleCount": 3,
                              "affineResidualRmsSeconds": 0.001, "latestAcceptedPcMonotonicNs": event_time},
                "originalQuestEvent": {"sessionId": "s", "trialId": "t", "eventType": event_type, "sequence": 1}}

    def test_pre_sync_event_is_invalid(self):
        p = packet(0, 12345, 9_000_000_000)
        self.coordinator.ingest_packet(p, PacketContinuityRecord(0, 0, "continuous"))
        self.assertEqual("pre_sync", self.coordinator.gate.state.value)
        self.coordinator.ingest_event(self.event())
        self.assertEqual("event_not_after_post_sync_association_ready", self.output.records[0]["invalidReason"])

    def test_transition_then_stable_post_sync_becomes_ready(self):
        self.coordinator.ingest_packet(packet(0, 12345, 9_000_000_000), PacketContinuityRecord(0, 0, "continuous"))
        self.feed_stable()
        self.assertEqual("association_ready", self.coordinator.gate.state.value)

    def test_stable_post_sync_association_is_software_estimate(self):
        self.feed_stable()
        self.coordinator.ingest_event(self.event())
        self.assertEqual(1, len(self.output.records))
        record = self.output.records[0]
        self.assertTrue(record["associationValid"])
        self.assertEqual("software_derived_estimate", record["sampleIndexKind"])
        self.assertFalse(record["hardwareTimingVerified"])

    def test_continuity_break_invalidates_later_event(self):
        self.feed_stable()
        p = packet(20, 1_700_000_005_000, 13_000_000_000)
        self.coordinator.ingest_packet(p, PacketContinuityRecord(20, 2400, "anomaly", ("packet_sequence_gap",)))
        self.coordinator.ingest_event(self.event(13_100_000_000))
        self.assertEqual("event_not_after_post_sync_association_ready", self.output.records[-1]["invalidReason"])

    def test_missing_clock_sync_is_invalid(self):
        self.feed_stable()
        value = self.event(); value["clockSync"] = {"status": "unavailable"}
        self.coordinator.ingest_event(value)
        self.assertEqual("quest_pc_clock_mapping_quality_unavailable_or_stale", self.output.records[-1]["invalidReason"])

    def test_packet_boundary_is_not_clamped(self):
        self.feed_stable()
        self.coordinator.ingest_event(self.event(12_000_000_000))
        self.assertEqual(1, len(self.output.records))
        self.assertIn(self.output.records[0]["estimatedSampleOffset"], (0, 199))


if __name__ == "__main__":
    unittest.main()
