import unittest

from eeg.acquisition.timestamp_mapping_analysis import analyze_metadata_records


def record(sequence, timestamp_ms, monotonic_ns, utc):
    return {
        "packet": {
            "device_timestamp": timestamp_ms,
            "pc_receive_monotonic_ns": monotonic_ns,
            "pc_receive_utc": utc,
            "sample_count": 200,
            "sampling_rate_hz": 1000.0,
            "packet_sequence": sequence,
        },
        "continuity": {"issues": []},
    }


class TimestampMappingAnalysisTests(unittest.TestCase):
    def test_reports_stable_mapping_and_anomaly_context(self):
        records = [
            record(0, 1000.0, 10_000_000_000, "1970-01-01T00:00:02.000000+00:00"),
            record(1, 1200.0, 10_200_000_000, "1970-01-01T00:00:02.200000+00:00"),
            record(2, 1401.0, 10_401_000_000, "1970-01-01T00:00:02.401000+00:00"),
        ]
        analysis = analyze_metadata_records(records)
        self.assertEqual(3, analysis["packetCount"])
        self.assertEqual(1, len(analysis["sdkTimestampAnomalies"]))
        anomaly = analysis["sdkTimestampAnomalies"][0]
        self.assertEqual(201.0, anomaly["sdkTimestampDeltaMs"])
        self.assertFalse(anomaly["mismatchBeyondOneMs"])
        self.assertAlmostEqual(1_000_000.0, analysis["primaryStableSegment"]["pcMonotonicFromSdkTimestamp"]["slopeNanosecondsPerMillisecond"])

    def test_splits_a_severe_sdk_timestamp_jump_into_segments(self):
        records = [
            record(0, 1000.0, 10_000_000_000, "1970-01-01T00:00:02.000000+00:00"),
            record(1, 1200.0, 10_200_000_000, "1970-01-01T00:00:02.200000+00:00"),
            record(2, 1_700_000_000_000.0, 10_400_000_000, "1970-01-01T00:00:02.400000+00:00"),
            record(3, 1_700_000_000_200.0, 10_600_000_000, "1970-01-01T00:00:02.600000+00:00"),
        ]
        analysis = analyze_metadata_records(records)
        self.assertEqual(2, len(analysis["segments"]))
        self.assertTrue(analysis["sdkTimestampAnomalies"][0]["segmentBoundary"])
        self.assertEqual(2, analysis["primaryStableSegment"]["packetCount"])


if __name__ == "__main__":
    unittest.main()
