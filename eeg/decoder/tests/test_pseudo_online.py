import unittest

import numpy as np

from eeg.decoder.config import DecoderConfig
from eeg.decoder.pseudo_online import DecoderBackend, ReplayPacket, RollingEegBuffer, replay_event_locked


class BufferTests(unittest.TestCase):
    def packet(self, first, sequence, status="continuous"):
        return ReplayPacket(np.vstack((np.arange(first, first + 200), np.arange(first, first + 200))),
                            first, sequence, sequence * 200_000_000, status)

    def test_append_order_and_exact_window(self):
        buffer = RollingEegBuffer(); buffer.append(self.packet(0, 0)); buffer.append(self.packet(200, 1))
        np.testing.assert_array_equal(buffer.window(190, 210)[0], np.arange(190, 210))

    def test_insufficient_history_and_continuity_reset(self):
        buffer = RollingEegBuffer(); buffer.append(self.packet(0, 0))
        with self.assertRaises(ValueError): buffer.window(0, 201)
        buffer.append(self.packet(200, 2))
        with self.assertRaises(ValueError): buffer.window(0, 200)

    def test_anomaly_does_not_create_a_false_sample_gap(self):
        buffer = RollingEegBuffer(); buffer.append(self.packet(0, 0)); buffer.append(self.packet(200, 1, "anomaly"))
        self.assertEqual(400, buffer.stop_sample)


class ReplayTests(unittest.TestCase):
    def test_event_waits_for_first_eligible_packet_once(self):
        config = DecoderConfig(analysis_duration_seconds=0.01, onset_guard_seconds=0.01)
        packets = [ReplayPacket(np.random.default_rng(1).normal(size=(8, 10)), i * 10, i,
                                i * 1_000_000, "continuous") for i in range(4)]
        event = {"sessionId": "s", "trialId": "t", "groundTruthLabel": "target_left",
                 "startSample": 0, "eventKnownLogicalNs": 0}
        decisions, pending = replay_event_locked(packets, [event], DecoderBackend("standard_cca", config), [0, 1], config)
        self.assertEqual(1, len(decisions)); self.assertFalse(pending)
        self.assertEqual(20, decisions[0]["firstEligibleSample"])
        self.assertGreaterEqual(decisions[0]["samplesAvailableAtDecision"], 20)

    def test_event_with_no_future_samples_has_no_prediction(self):
        config = DecoderConfig(analysis_duration_seconds=0.01, onset_guard_seconds=0.01)
        packets = [ReplayPacket(np.random.default_rng(2).normal(size=(8, 10)), 0, 0, 0, "continuous")]
        event = {"sessionId": "s", "trialId": "t", "groundTruthLabel": "target_left",
                 "startSample": 0, "eventKnownLogicalNs": 0}
        decisions, pending = replay_event_locked(packets, [event], DecoderBackend("standard_cca", config), [0, 1], config)
        self.assertFalse(decisions); self.assertEqual(1, len(pending))

    def test_all_backends_have_prediction_interface(self):
        config = DecoderConfig(analysis_duration_seconds=0.1)
        data = np.random.default_rng(3).normal(size=(5, 100))
        for name in ("standard_cca", "numpy_fbcca", "legacy_fbcca"):
            index, scores = DecoderBackend(name, config).predict(data)
            self.assertIn(index, range(3)); self.assertEqual(3, len(scores))


if __name__ == "__main__":
    unittest.main()
