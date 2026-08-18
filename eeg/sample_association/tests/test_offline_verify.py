import json
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path

from eeg.sample_association.offline_verify import verify_session


def write_jsonl(path, values):
    path.write_text("".join(json.dumps(v) + "\n" for v in values), encoding="utf-8")


class OfflineVerificationTests(unittest.TestCase):
    def test_recomputes_raw_evidence_and_matches_live_association(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = []
            for i in range(12):
                receive_ns = 10_000_000_000 + 200_000_000 * i
                # This late packet must not influence an event that was
                # already received and associated before it arrived.
                if i == 11:
                    receive_ns = 30_000_000_000
                metadata.append({"packet": {"device_timestamp": 1_700_000_000_000 + 200 * i,
                    "pc_receive_monotonic_ns": receive_ns,
                    "pc_receive_utc": "2026-01-01T00:00:00+00:00", "sample_count": 200, "channel_count": 8,
                    "sampling_rate_hz": 1000.0, "packet_sequence": i, "first_sample_index": None,
                    "device_timestamp_unit": "milliseconds", "device_timestamp_first_sample_assumed": False,
                    "device_timestamp_hardware_verified": False, "source": "test"},
                    "continuity": {"packet_sequence": i, "cumulative_first_sample_index": i * 200,
                    "status": "continuous", "issues": []}})
            write_jsonl(root / "packet-metadata.jsonl", metadata)
            sync = []
            for i, q in enumerate((1.0, 2.0, 3.0)):
                sync.append({"recordType": "clock_sync_sample", "connectionId": "c", "pcResultReceiveMonotonicNs": 10_000_000_000 + i,
                    "sampleAcceptedForAffineFit": True, "rawSample": {"q1QuestMonotonicSeconds": q - .01,
                    "q4QuestMonotonicSeconds": q + .01, "p2PcReceiveMonotonicNs": int((q + 9.99) * 1e9),
                    "p3PcSendMonotonicNs": int((q + 10.01) * 1e9)}})
            write_jsonl(root / "pc-synchronization.jsonl", sync)
            event = {"recordType": "stimulus_event_received", "connectionId": "c", "pcReceiveMonotonicNs": 11_850_000_000,
                "originalQuestEvent": {"sessionId": "s", "trialId": "t", "eventType": "stimulus_started_software",
                "sequence": 1, "questMonotonicSeconds": 1.85}}
            write_jsonl(root / "pc-stimulus-events.jsonl", [event])
            write_jsonl(root / "derived-association.jsonl", [{"sessionId": "s", "trialId": "t", "stimulusSequence": 1,
                "associationValid": True, "associatedPacketSequence": 9, "estimatedSampleOffset": 50}])
            summary, records = verify_session(root)
            self.assertTrue(summary["passed"])
            self.assertTrue(records[0]["associationValid"])
            self.assertEqual(9, records[0]["associatedPacketSequence"])

    def test_reports_malformed_log(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "packet-metadata.jsonl").write_text("{bad}\n", encoding="utf-8")
            summary, _ = verify_session(root)
            self.assertFalse(summary["passed"])
            self.assertTrue(summary["rawEvidenceErrors"])

    def test_does_not_report_pass_when_stimulus_association_is_invalid(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            write_jsonl(root / "packet-metadata.jsonl", [])
            write_jsonl(root / "pc-synchronization.jsonl", [])
            event = {"recordType": "stimulus_event_received", "connectionId": "c", "pcReceiveMonotonicNs": 1,
                     "originalQuestEvent": {"sessionId": "s", "trialId": "t", "eventType": "stimulus_started_software", "sequence": 1}}
            write_jsonl(root / "pc-stimulus-events.jsonl", [event])
            write_jsonl(root / "derived-association.jsonl", [{"sessionId": "s", "trialId": "t", "stimulusSequence": 1,
                        "associationValid": False, "associatedPacketSequence": None, "estimatedSampleOffset": None}])
            summary, _ = verify_session(root)
            self.assertFalse(summary["passed"])
            self.assertEqual(0, summary["validStimulusAssociationCount"])


if __name__ == "__main__":
    unittest.main()
