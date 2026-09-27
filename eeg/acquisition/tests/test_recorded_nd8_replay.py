from pathlib import Path
import tempfile
import unittest

import numpy as np

from eeg.acquisition.nd8_packet import Nd8Packet
from eeg.acquisition.recorded_nd8_replay import RecordedND8Replay
from eeg.sample_association.models import PacketContinuityRecord
from integration.m13_7_golden_session import HumanSessionRecorder, _FakeClock


class RecordedND8ReplayTests(unittest.TestCase):
    def _session(self, root):
        clock = _FakeClock()
        recorder = HumanSessionRecorder(
            root,
            "replay-session",
            "synthetic_fixture",
            monotonic_ns=clock.monotonic_ns,
            utc_now=clock.utc_now,
        )
        for sequence in range(3):
            packet = Nd8Packet.from_sdk_payload(
                {"timestamp": float(sequence * 10), "data": (np.ones((8, 10)) * sequence).tolist()},
                packet_sequence=sequence,
                receive_monotonic_ns=1_000_000_000 + sequence * 10_000_000,
                receive_utc="2026-01-01T00:00:00Z",
            )
            recorder.record_packet(packet, PacketContinuityRecord(sequence, sequence * 10, "initial" if sequence == 0 else "continuous", ()))
        recorder.finalize()
        return recorder.root

    def test_replay_yields_live_packet_contract_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._session(Path(directory) / "session")
            before = (root / "raw-eeg.jsonl").read_bytes()
            received = []
            result = RecordedND8Replay(root).replay(
                lambda packet, continuity: received.append(
                    (packet.packet_sequence, packet.channel_count, packet.sample_count, continuity.status)
                )
            )
            after = (root / "raw-eeg.jsonl").read_bytes()
        self.assertEqual(received, [(0, 8, 10, "initial"), (1, 8, 10, "continuous"), (2, 8, 10, "continuous")])
        self.assertEqual(result["packetCount"], 3)
        self.assertEqual(before, after)

    def test_rate_controlled_replay_is_deterministic_without_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._session(Path(directory) / "session")
            waits = []
            result = RecordedND8Replay(root, speed=2.0, sleep=waits.append).replay(lambda packet, continuity: None)
        self.assertEqual(result["packetCount"], 3)
        self.assertEqual(waits, [0.005, 0.005])


if __name__ == "__main__":
    unittest.main()
