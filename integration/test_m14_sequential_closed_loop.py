import json
import tempfile
import unittest
from pathlib import Path

from integration.m10_task_benchmark import load_task_definitions
from integration.m14_sequential_closed_loop import (
    M14SequentialEpisodeRunner,
    SyntheticRobotAdapter,
    run_synthetic_acceptance,
)


class M14SequentialClosedLoopTests(unittest.TestCase):
    def test_synthetic_acceptance_covers_positive_and_fail_closed_cases(self):
        with tempfile.TemporaryDirectory() as root:
            summary, records = run_synthetic_acceptance(Path(root) / "m14.jsonl")
        self.assertEqual(summary["overallStatus"], "PASS")
        self.assertEqual(len(records), 7)
        self.assertEqual([item["taskId"] for item in records[:3]], ["house", "tower", "bridge"])
        self.assertTrue(summary["checks"]["m13NoDecisionSuppressesRobot"])
        self.assertTrue(summary["checks"]["duplicateFinalDecisionSuppressed"])

    def test_successful_robot_execution_is_the_only_m10_commit(self):
        definitions = load_task_definitions()
        adapter = SyntheticRobotAdapter(fail_on_call=1)
        record = M14SequentialEpisodeRunner(adapter).run_episode(
            definitions["house"], evidence_prefix="test-m14-failure"
        )
        self.assertEqual(record["finalState"]["completedSequence"], [])
        self.assertEqual(record["finalState"]["status"], "not_started")
        self.assertEqual(record["robotDispatchAttemptCount"], 1)
        self.assertFalse(record["steps"][0]["m10Commit"]["committed"])

    def test_m11_input_contains_observable_history_only_and_m13_stops_at_second_window(self):
        definitions = load_task_definitions()
        record = M14SequentialEpisodeRunner(SyntheticRobotAdapter()).run_episode(
            definitions["tower"], evidence_prefix="test-m14-contract"
        )
        first = record["steps"][0]
        self.assertEqual(first["m11Input"]["completedLogicalBlockHistory"], [])
        self.assertNotIn("remainingLogicalBlockIds", first["m11Input"])
        self.assertTrue(all(step["selection"]["m13FinalDecision"]["earlyStop"] for step in record["steps"]))
        self.assertTrue(all(step["selection"]["m13FinalDecision"]["stopWindow"] == 1 for step in record["steps"]))
        self.assertNotIn("obj_", json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
