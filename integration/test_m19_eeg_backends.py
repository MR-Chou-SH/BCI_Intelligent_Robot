"""Backend parity, historical replay and fail-closed live-source tests."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from integration.m19_eeg_backends import (
    M19HistoricalEegBackend,
    M19LiveConfirmationRequired,
    M19LiveNd8Backend,
    M19SyntheticEegBackend,
)
from integration.m19_paged_live_eeg import M19TrialRegistry, validate_decode_request


class M19EegBackendTests(unittest.TestCase):
    def test_synthetic_backend_injects_all_classes_and_no_decision(self):
        backend = M19SyntheticEegBackend((0, 1, 2, None))
        request = SimpleNamespace(trial_id="trial", selection_id="selection", page_id="page", page_epoch=2)
        results = [backend.decode(request) for _ in range(4)]
        self.assertEqual([0, 1, 2, None], [item["classIndex"] for item in results])
        self.assertEqual([True, True, True, False], [item["decisionMade"] for item in results])
        self.assertEqual(4, len(backend.calls))
        self.assertNotIn("targetId", results[1])

    def test_historical_source_swap_uses_recorded_samples_and_current_request_slots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample_rate = 1000.0
            raw_path = root / "raw-eeg.jsonl"
            metadata_path = root / "packet-metadata.jsonl"
            events_path = root / "events.jsonl"
            with raw_path.open("w", encoding="utf-8") as raw, metadata_path.open("w", encoding="utf-8") as metadata:
                for sequence in range(5):
                    first = sequence * 500
                    time_axis = np.arange(first, first + 500, dtype=float) / sample_rate
                    signal = np.sin(2.0 * np.pi * 9.0 * time_axis)
                    samples = [
                        (signal * (1.0 - 0.03 * channel)).tolist()
                        for channel in range(8)
                    ]
                    raw.write(json.dumps({
                        "recordType": "m13_7_raw_packet",
                        "samples": samples,
                        "packetSequence": sequence,
                        "sdkTimestampMs": first,
                        "pcReceiveMonotonicNs": 1_000_000_000 + sequence * 500_000_000,
                        "pcReceiveUtc": "2026-09-22T12:00:00+00:00",
                        "nominalSamplingRateHz": sample_rate,
                    }) + "\n")
                    metadata.write(json.dumps({
                        "recordType": "m13_7_packet_metadata",
                        "packet": {"packet_sequence": sequence},
                        "continuity": {
                            "packet_sequence": sequence,
                            "cumulative_first_sample_index": first,
                            "status": "initial" if sequence == 0 else "continuous",
                            "issues": [],
                        },
                    }) + "\n")
            events_path.write_text("\n".join((
                json.dumps({"eventType": "STIMULUS_ONSET", "trialId": "historic-ssvep-trial", "frequencyHz": 9.0}),
                json.dumps({"eventType": "TRIAL_SAMPLE_ANCHOR", "trialId": "historic-ssvep-trial", "sampleAnchorSampleIndex": 0, "packetSequence": 0}),
            )) + "\n", encoding="utf-8")
            (root / "manifest.json").write_text(json.dumps({
                "sessionId": "historical-fixture",
                "rawEegFile": "raw-eeg.jsonl",
                "packetMetadataFile": "packet-metadata.jsonl",
                "eventFile": "events.jsonl",
                "decoderConfiguration": {"selectedChannels": [2, 3, 4, 5, 7]},
            }), encoding="utf-8")

            request_payload = {
                "contractId": "m19_decode_request_v1",
                "protocolVersion": 1,
                "messageType": "eeg_decode_request",
                "trialId": "new-current-trial",
                "selectionId": "new-current-selection",
                "pageId": "current-page",
                "pageIndex": 0,
                "pageEpoch": 9,
                "createdUtc": "2026-09-22T12:00:00+00:00",
                "activeSlotCount": 1,
                "slots": [{"slotIndex": 2, "frequencyHz": 12.0, "targetId": "current-page-target"}],
            }
            backend = M19HistoricalEegBackend(root)
            result = backend.decode(validate_decode_request(request_payload))

        self.assertTrue(result["decisionMade"])
        self.assertEqual(1, result["classIndex"])
        self.assertEqual("historic-ssvep-trial", result["evidence"]["historicalTrialId"])
        self.assertEqual("new-current-selection", result["evidence"]["currentRequestSelectionId"])
        self.assertFalse(result["evidence"]["historicalGroundTruthReadByDecoder"])
        # The old replay predicts class 1, which is inactive on the current
        # one-slot page. The registry, not this backend, must fail it closed.
        registry = M19TrialRegistry()
        request = validate_decode_request(request_payload)
        self.assertTrue(registry.accept_request(request_payload)["accepted"])
        resolved = registry.resolve(
            request.selection_id,
            result["classIndex"],
            decision_made=result["decisionMade"],
            page_id=request.page_id,
            page_epoch=request.page_epoch,
        )
        self.assertFalse(resolved["accepted"])
        self.assertEqual("inactive_slot", resolved["rejectionReason"])

    def test_live_backend_never_constructs_source_before_confirmation(self):
        calls = []

        def source_factory(port):
            calls.append(port)
            raise AssertionError("source factory must not run without confirmation")

        backend = M19LiveNd8Backend("COM11", source_factory=source_factory)
        with self.assertRaises(M19LiveConfirmationRequired):
            backend.open()
        self.assertEqual([], calls)

    def test_live_backend_passes_configured_port_only_after_confirmation_and_closes(self):
        calls = []

        class FakeContinuousSource:
            def __init__(self, port):
                self.port = port
                self.open_count = 0
                self.admission_count = 0
                self.close_count = 0

            def open(self):
                self.open_count += 1

            def admit_channels(self):
                self.admission_count += 1
                return {"verdict": "READY", "selectedChannels": [2, 3, 4]}

            def decode(self, request):
                return {"decisionMade": True, "classIndex": 2, "evidence": {"request": request.selection_id}}

            def close(self):
                self.close_count += 1

        def source_factory(port):
            source = FakeContinuousSource(port)
            calls.append(source)
            return source

        backend = M19LiveNd8Backend(
            "COM_FAKE",
            confirm_live_human=True,
            source_factory=source_factory,
        )
        backend.open()
        request = SimpleNamespace(selection_id="live-selection")
        result = backend.decode(request)
        backend.close()

        self.assertEqual(["COM_FAKE"], [item.port for item in calls])
        self.assertEqual(1, calls[0].open_count)
        self.assertEqual(1, calls[0].admission_count)
        self.assertEqual(1, calls[0].close_count)
        self.assertEqual(2, result["classIndex"])


if __name__ == "__main__":
    unittest.main()
