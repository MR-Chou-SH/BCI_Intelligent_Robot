import json
import unittest

from integration.synchronization.clock_sync import AffineClockMapper, calculate_ntp_sample
from integration.synchronization.protocol import JsonLineDecoder, SequenceTracker


class ProtocolTests(unittest.TestCase):
    def test_partial_and_multiple_json_lines(self):
        decoder = JsonLineDecoder()
        self.assertEqual([], decoder.feed(b'{"a":'))
        self.assertEqual([{"a": 1}, {"b": 2}], decoder.feed(b'1}\n{"b":2}\n'))

    def test_malformed_complete_line_is_visible(self):
        with self.assertRaises(json.JSONDecodeError):
            JsonLineDecoder().feed(b'{bad}\n')

    def test_duplicate_gap_and_out_of_order_sequences(self):
        tracker = SequenceTracker()
        self.assertEqual("in_order", tracker.observe("s", 0).status)
        self.assertEqual("gap", tracker.observe("s", 2).status)
        self.assertEqual("duplicate", tracker.observe("s", 2).status)
        self.assertEqual("out_of_order", tracker.observe("s", 1).status)

    def test_ntp_sign_convention(self):
        sample = calculate_ntp_sample(10.0, 110.01, 110.02, 10.03)
        self.assertAlmostEqual(0.02, sample["roundTripSeconds"])
        self.assertAlmostEqual(100.0, sample["offsetPcMinusQuestSeconds"])

    def test_affine_fit(self):
        mapper = AffineClockMapper()
        for quest in range(5):
            mapper.add(quest, 1.0001 * quest + 50.0)
        a, b = mapper.coefficients()
        self.assertAlmostEqual(1.0001, a)
        self.assertAlmostEqual(50.0, b)
        self.assertAlmostEqual(0.0, mapper.residual_rms_seconds())


if __name__ == "__main__":
    unittest.main()
