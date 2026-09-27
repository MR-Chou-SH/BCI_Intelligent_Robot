"""Cross-runtime M19 confirmed-batch RPC contract tests."""

from __future__ import annotations

import unittest

from integration.m8_selection_transport.simulated_batch_consumer import BatchIdempotentConsumer
from integration.m13_8_final_runtime_integration import LocalJsonlIpcServer
from integration.m9_batch_dispatch import M9BatchDispatcher
from integration.m9_mujoco_execution import RobotExecutionResult, RobotOperation
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping
from integration.m9_virtual_e2e import make_confirmed_batch
from integration.m19_mujoco_rpc import M19MujocoRpcEndpoint, M19RemoteMujocoBatchDispatcher


class RecordingRobotAdapter:
    def __init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(request.selection.selection_id)
        selection = request.selection
        return RobotExecutionResult(
            request_id=request.request_id,
            logical_block_id=request.logical_block_id,
            operation=request.operation,
            success=True,
            failure_code=None,
            failure_reason=None,
            source_batch_id=selection.batch_id,
            source_selection_id=selection.selection_id,
            source_target_id=selection.source_target_id,
            batch_provenance=selection.batch_provenance,
            selection_provenance=selection.selection_provenance,
            backend_execution_id="rpc-test-{}".format(len(self.calls)),
            execution_provenance="m19-mujoco-rpc-test-adapter",
            started_utc=selection.resolved_utc,
            completed_utc=selection.resolved_utc,
        )


class M19MuJoCoRpcTests(unittest.TestCase):
    def test_confirmed_selection_order_and_batch_dedup_cross_the_loopback_boundary(self):
        mapping = load_virtual_block_target_mapping()
        adapter = RecordingRobotAdapter()
        dispatcher = M9BatchDispatcher(mapping, adapter, RobotOperation.PICK_AND_PLACE)
        server = LocalJsonlIpcServer(M19MujocoRpcEndpoint(dispatcher).handle)
        remote = M19RemoteMujocoBatchDispatcher(*server.address)
        consumer = BatchIdempotentConsumer()
        payload = make_confirmed_batch(
            "m9-vblock-blue-01", batch_id="m19-rpc-batch", selection_id="m19-rpc-blue", slot_index=0
        )
        selections = [payload["confirmedBatch"]["selections"][0]]
        for index, (target_id, selection_id) in enumerate((
            ("m9-vblock-red-01", "m19-rpc-red"),
            ("m9-vblock-green-01", "m19-rpc-green"),
        ), 1):
            extra = make_confirmed_batch(
                target_id,
                batch_id="m19-rpc-batch",
                selection_id=selection_id,
                slot_index=index,
            )
            selections.extend(extra["confirmedBatch"]["selections"])
        payload["confirmedBatch"]["selections"] = selections
        try:
            self.assertTrue(remote.health())
            first_receipt = consumer.accept(payload)
            result = remote.dispatch(first_receipt)
            self.assertEqual(["m19-rpc-blue", "m19-rpc-red", "m19-rpc-green"], adapter.calls)
            self.assertEqual(
                adapter.calls,
                [attempt.to_public_dict()["selectionId"] for attempt in result.executions],
            )
            self.assertTrue(all(attempt.success for attempt in result.executions))

            duplicate_receipt = consumer.accept(payload)
            duplicate = remote.dispatch(duplicate_receipt)
            self.assertTrue(duplicate.duplicate_batch)
            self.assertEqual(3, len(adapter.calls), "replayed batch must not execute again")
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
