import json
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from eeg.signal_sanity.analysis import analyze_session


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, allow_nan=True) + "\n")


def packet(sequence, channels):
    return {"recordType": "nd8_raw_packet", "packetSequence": sequence, "samples": [list(map(float, value)) for value in channels]}


class SignalSanityAnalysisTests(unittest.TestCase):
    def make_session(self, root, mode="single_ssvep_sanity"):
        with open(root + "/session-manifest.json", "w", encoding="utf-8") as stream:
            json.dump({"sessionId": "synthetic", "validationMode": mode, "samplingRateHz": 1000.0}, stream)

    def test_synthetic_sinusoid_constant_and_all_channels_are_reported(self):
        with TemporaryDirectory() as root:
            self.make_session(root)
            time = np.arange(2000) / 1000.0
            channels = [np.zeros(2000), np.sin(2 * np.pi * 9 * time)] + [0.1 * np.sin(2 * np.pi * (11 + index) * time) for index in range(6)]
            write_jsonl(root + "/raw-eeg-packets.jsonl", [packet(0, channels)])
            write_jsonl(root + "/packet-metadata.jsonl", [{"continuity": {"issues": []}}])
            summary = analyze_session(root)
            self.assertEqual(8, len(summary["channels"]))
            self.assertTrue(summary["channels"][0]["constantCandidate"])
            self.assertEqual("invalid", summary["channels"][0]["quality"])
            self.assertEqual("usable_with_warnings", summary["overallRecommendation"])
            ratio = summary["channels"][1]["spectralSummary"]["neighborhoodPowers"]["9Hz"]["stimulusToNearbyBackgroundRatio"]
            self.assertGreater(ratio, 10.0)
            self.assertTrue((__import__("pathlib").Path(root) / "analysis" / "signal-quality-summary.json").exists())

    def test_nonfinite_input_is_invalid_not_silent_pass(self):
        with TemporaryDirectory() as root:
            self.make_session(root, "rest")
            write_jsonl(root + "/raw-eeg-packets.jsonl", [packet(0, [[float("nan"), 1.0] for _ in range(8)])])
            write_jsonl(root + "/packet-metadata.jsonl", [])
            summary = analyze_session(root)
            self.assertEqual("invalid", summary["overallRecommendation"])
            self.assertEqual(1, summary["channels"][0]["statistics"]["nonFiniteCount"])

    def test_malformed_packet_and_sequence_gap_are_reported(self):
        with TemporaryDirectory() as root:
            self.make_session(root, "rest")
            with open(root + "/raw-eeg-packets.jsonl", "w", encoding="utf-8") as stream:
                stream.write("{bad}\n")
                stream.write(json.dumps(packet(2, [[1.0, 2.0] for _ in range(8)])) + "\n")
            write_jsonl(root + "/packet-metadata.jsonl", [{"continuity": {"issues": ["packet_sequence_gap"]}}])
            summary = analyze_session(root)
            self.assertEqual("invalid", summary["overallRecommendation"])
            self.assertTrue(any("malformed_json" in item for item in summary["inputErrors"]))

    def test_repeated_analysis_is_deterministic_except_generation_time(self):
        with TemporaryDirectory() as root:
            self.make_session(root, "rest")
            write_jsonl(root + "/raw-eeg-packets.jsonl", [packet(0, [[float(index) for index in range(1024)] for _ in range(8)])])
            write_jsonl(root + "/packet-metadata.jsonl", [])
            first = analyze_session(root); second = analyze_session(root)
            first.pop("generatedUtc"); second.pop("generatedUtc")
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
