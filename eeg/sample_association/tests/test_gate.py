import unittest

from eeg.sample_association.gate import PostSyncAssociationGate
from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord
from eeg.sample_association.timeline import EegPacketTimeline


def packet(sequence, timestamp):
    return EegPacketMetadata(
        timestamp,
        1_000_000_000 + sequence * 200_000_000,
        "2026-01-01T00:00:00+00:00",
        200,
        8,
        1000.0,
        sequence,
        None,
        "milliseconds",
        False,
        False,
    )


class PostSyncAssociationGateTests(unittest.TestCase):
    def test_startup_relative_to_unix_timestamp_domain_transition_is_transition(self):
        gate = PostSyncAssociationGate()
        timeline = EegPacketTimeline()
        first = packet(0, 1_000.0)
        second = packet(1, 1_700_000_000_000.0)
        first_continuity = timeline.append(first)
        second_continuity = timeline.append(second)
        gate.observe(first, first_continuity)
        decision = gate.observe(second, second_continuity)

        self.assertEqual("transition", decision.state)
        self.assertFalse(decision.association_ready)
        self.assertEqual("startup_relative_to_unix_ms_timestamp_domain_transition", decision.reason)
        self.assertEqual(("timestamp_delta_mismatch", "timestamp_jump"), second_continuity.issues)

    def test_packet_gap_remains_continuity_lost_even_at_timestamp_domain_transition(self):
        gate = PostSyncAssociationGate()
        gate.observe(packet(0, 1_000.0), PacketContinuityRecord(0, 0, "continuous"))
        decision = gate.observe(
            packet(2, 1_700_000_000_000.0),
            PacketContinuityRecord(
                2,
                400,
                "anomaly",
                ("packet_sequence_gap", "timestamp_delta_mismatch", "timestamp_jump"),
            ),
        )

        self.assertEqual("continuity_lost", decision.state)
        self.assertIn("packet_sequence_gap", decision.reason)

    def test_unix_timestamp_jump_without_domain_transition_remains_continuity_lost(self):
        gate = PostSyncAssociationGate()
        gate.observe(packet(0, 1_700_000_000_000.0), PacketContinuityRecord(0, 0, "continuous"))
        decision = gate.observe(
            packet(1, 1_700_000_010_000.0),
            PacketContinuityRecord(
                1,
                200,
                "anomaly",
                ("timestamp_delta_mismatch", "timestamp_jump"),
            ),
        )

        self.assertEqual("continuity_lost", decision.state)


if __name__ == "__main__":
    unittest.main()
