import json
import tempfile
import unittest
from pathlib import Path

from integration.m13_5_logging import M135SessionLogger, ensure_no_forbidden_identity, read_jsonl


class M135LoggingTests(unittest.TestCase):
    def test_session_log_is_append_only_and_structured(self):
        with tempfile.TemporaryDirectory() as root:
            logger = M135SessionLogger(Path(root), "session-1", "shadow", "synthetic", "commit", {"fusedThreshold": .70, "marginThreshold": .20, "requiredConsecutive": 2}, [{"slotIndex": 0, "targetId": "target-1", "logicalBlockId": "block_sim_01"}])
            logger.append("trial_started", trialId="trial-1", selectionId="selection-1")
            records = read_jsonl(logger.path)
            self.assertEqual([item["sequence"] for item in records], [0, 1])
            self.assertEqual(records[1]["eventType"], "trial_started")
            self.assertTrue(all(item["recordType"] == "m13_5_session_event" for item in records))

    def test_forbidden_obj_identity_is_rejected(self):
        with self.assertRaises(ValueError):
            ensure_no_forbidden_identity({"logicalBlockId": "obj_0"})


if __name__ == "__main__":
    unittest.main()
