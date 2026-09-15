import unittest

from integration.m10_mujoco_sequential_e2e import run_sequential_episode
from integration.m10_task_benchmark import load_task_definitions
from integration.m9_mujoco_execution import (
    RobotExecutionResult,
    RobotOperation,
)
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


class CountingRobotAdapter:
    def __init__(self, failures=0):
        self.failures = failures
        self.calls = []

    def execute(self, request):
        self.calls.append(request.logical_block_id)
        success = self.failures == 0
        if not success:
            self.failures -= 1
        return RobotExecutionResult(
            request_id=request.request_id,
            logical_block_id=request.logical_block_id,
            operation=request.operation,
            success=success,
            failure_code=None if success else "pick_place_failed",
            failure_reason=None if success else "injected failure",
            source_batch_id=request.selection.batch_id,
            source_selection_id=request.selection.selection_id,
            source_target_id=request.selection.source_target_id,
            batch_provenance=request.selection.batch_provenance,
            selection_provenance=request.selection.selection_provenance,
            backend_execution_id="fake-run-{:02d}".format(len(self.calls)),
            execution_provenance="fake-fr3-umi:place_ok" if success else "fake-fr3-umi:failed",
            started_utc="2026-09-15T00:00:00Z",
            completed_utc="2026-09-15T00:00:01Z",
        )


class M10MujocoSequentialE2ETests(unittest.TestCase):
    def setUp(self):
        self.definitions = load_task_definitions()
        self.mapping = load_virtual_block_target_mapping()

    def test_successful_robot_execution_is_the_only_progress_commit(self):
        adapter = CountingRobotAdapter()
        result = run_sequential_episode(
            self.definitions["house"],
            self.definitions["house"].ordered_logical_block_ids,
            adapter,
            target_id_to_logical_block_id=self.mapping,
            evidence_prefix="test-success",
        )
        self.assertEqual(
            [
                "block_sim_01",
                "block_sim_02",
                "block_sim_03",
                "block_sim_04",
            ],
            adapter.calls,
        )
        self.assertEqual("completed", result["finalState"]["status"])
        self.assertEqual(4, result["successfulRobotExecutionCount"])
        self.assertTrue(all(event["progressedAfterRobotSuccess"] for event in result["events"]))

    def test_wrong_order_is_rejected_before_robot_dispatch(self):
        adapter = CountingRobotAdapter()
        result = run_sequential_episode(
            self.definitions["house"],
            ("block_sim_01", "block_sim_03"),
            adapter,
            target_id_to_logical_block_id=self.mapping,
            evidence_prefix="test-wrong-order",
        )
        self.assertEqual(["block_sim_01"], adapter.calls)
        self.assertEqual("invalid", result["finalState"]["status"])
        self.assertEqual(["block_sim_01"], result["finalState"]["completedSequence"])
        rejected = result["events"][-1]
        self.assertEqual("wrong_order", rejected["m10PreflightReasonCode"])
        self.assertFalse(rejected["robotExecutionDispatched"])
        self.assertEqual(0, rejected["robotExecutionAttemptCount"])
        self.assertIsNone(rejected["dispatchResult"])

    def test_failed_robot_execution_does_not_advance_m10_state(self):
        adapter = CountingRobotAdapter(failures=1)
        result = run_sequential_episode(
            self.definitions["house"],
            ("block_sim_01", "block_sim_02"),
            adapter,
            target_id_to_logical_block_id=self.mapping,
            operation=RobotOperation.PICK_AND_PLACE,
            evidence_prefix="test-failure",
        )
        self.assertEqual(["block_sim_01"], adapter.calls)
        self.assertEqual("not_started", result["finalState"]["status"])
        self.assertEqual([], result["finalState"]["completedSequence"])
        self.assertFalse(result["events"][0]["progressedAfterRobotSuccess"])
        self.assertEqual("pick_place_failed", result["events"][0]["dispatchResult"]["executions"][0]["failureCode"])


if __name__ == "__main__":
    unittest.main()
