import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from eeg.sample_association.models import PacketContinuityRecord
from integration.m13_5_live_controller import M135LiveOnlineController, uniform_pre_eeg_context_prior
from integration.m13_5_logging import read_jsonl


class ConstantBackend:
    name = "numpy_fbcca"

    def predict(self, data):
        self.last_shape = tuple(data.shape)
        return 1, [0.1, 0.9, 0.1]


class M135LiveControllerTests(unittest.TestCase):
    def test_pre_eeg_context_is_explicitly_uniform(self):
        prior = uniform_pre_eeg_context_prior()
        self.assertTrue(prior.valid)
        self.assertTrue(prior.tie)
        self.assertEqual(0.25, prior.probability_map()["block_sim_04"])

    def test_real_callback_shape_flows_through_m12_m13_and_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = ConstantBackend()
            controller = M135LiveOnlineController(
                backend,
                [0, 1],
                session_root=Path(directory),
                session_id="pre-eeg-session",
                software_commit="test",
            )
            self.assertTrue(controller.start_trial({
                "sessionId": "pre-eeg-session",
                "trialId": "trial-1",
                "selectionId": "selection-1",
                "estimatedGlobalSampleIndex": 0,
            }))
            for sequence in range(11):
                metadata = SimpleNamespace(packet_sequence=sequence, pc_receive_monotonic_ns=sequence * 200_000_000)
                continuity = PacketContinuityRecord(sequence, sequence * 200, "continuous", ())
                controller.ingest_packet(metadata, continuity, np.ones((8, 200), dtype=float))
            self.assertTrue(controller.decision_ready)
            result = controller.stop_trial()
            self.assertTrue(result["decisionMade"])
            self.assertTrue(result["m13Decision"]["earlyStop"])
            self.assertEqual(1, result["m13Decision"]["stopWindow"])
            controller.record_final_submission({
                "trialId": "trial-1",
                "selectionId": "selection-1",
                "status": "quest_accepted",
                "decisionMade": True,
                "ack": {"accepted": True},
                "m13Decision": result["m13Decision"],
                "m13SelectedTargetId": result["m13SelectedTargetId"],
                "m13SelectedLogicalBlockId": result["m13SelectedLogicalBlockId"],
            })
            controller.close_session("completed")
            records = read_jsonl(Path(directory) / "m13.5-session.jsonl")
            self.assertEqual("real_nd8_floating_electrodes_no_human_eeg", records[0]["sourceType"])
            self.assertEqual(2, len([item for item in records if item["eventType"] == "window_evaluated"]))
            self.assertEqual(1, len([item for item in records if item["eventType"] == "final_submission"]))
            self.assertEqual(1, len([item for item in records if item["eventType"] == "trial_closed"]))


if __name__ == "__main__":
    unittest.main()
