import unittest

from integration.m8_selection_transport.simulated_batch_consumer import (
    BatchIdempotentConsumer,
)
from integration.m9_batch_dispatch import M9BatchDispatcher
from integration.m9_mujoco_execution import (
    BackendExecutionOutcome,
    DEFAULT_M9_SCENE_BINDINGS,
    MujocoRobotExecutionAdapter,
    RobotOperation,
    SceneBindingRegistry,
)
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping
from integration.m9_virtual_e2e import loopback_replay, make_confirmed_batch


class CountingBackend:
    def __init__(self, fail_count=0):
        self.fail_count = fail_count
        self.calls = []

    def execute(self, request, simulator_object):
        self.calls.append((request, simulator_object))
        if self.fail_count:
            self.fail_count -= 1
            raise RuntimeError("injected backend failure")
        return BackendExecutionOutcome(
            success=True,
            execution_provenance="fake-fr3-umi-baseline",
            backend_execution_id="fake-run-{:03d}".format(len(self.calls)),
        )


def _adapter(backend):
    name_to_id = {"obj_0": 0, "obj_1": 1, "obj_2": 2, "obj_3": 3}
    return MujocoRobotExecutionAdapter(
        SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
        lambda name: name_to_id.get(name, -1),
        backend,
    )


def _two_selection_batch():
    first = make_confirmed_batch(
        "m9-vblock-red-01", batch_id="m9-batch-failure-recovery", selection_id="selection-fail", slot_index=0
    )
    second = make_confirmed_batch(
        "m9-vblock-green-01", batch_id="m9-batch-failure-recovery", selection_id="selection-next", slot_index=1
    )
    first["confirmedBatch"]["selections"].extend(second["confirmedBatch"]["selections"])
    return first


class M9BatchDispatchTests(unittest.TestCase):
    def test_all_four_frozen_target_ids_prepare_exact_logical_and_scene_bindings(self):
        mapping = load_virtual_block_target_mapping()
        self.assertEqual(
            {
                "m9-vblock-red-01": "block_sim_01",
                "m9-vblock-green-01": "block_sim_02",
                "m9-vblock-blue-01": "block_sim_03",
                "m9-vblock-yellow-01": "block_sim_04",
            },
            mapping,
        )
        backend = CountingBackend()
        dispatcher = M9BatchDispatcher(mapping, _adapter(backend))
        receiver = BatchIdempotentConsumer()
        targets = tuple(mapping)

        for index, target_id in enumerate(targets):
            receipt = receiver.accept(
                make_confirmed_batch(
                    target_id,
                    batch_id="mapping-batch-{:02d}".format(index),
                    selection_id="mapping-selection-{:02d}".format(index),
                )
            )
            result = dispatcher.dispatch(receipt)
            self.assertTrue(result.success)
            self.assertEqual("block_sim_{:02d}".format(index + 1), result.executions[0].logical_block_id)
            self.assertEqual(index, backend.calls[index][1].simulator_object_id)
            self.assertNotIn("obj_", repr(result.to_public_dict()))

        self.assertEqual(4, len(backend.calls))

    def test_loopback_acknowledges_reconnect_replay_but_dispatches_once(self):
        mapping = load_virtual_block_target_mapping()
        backend = CountingBackend()
        dispatcher = M9BatchDispatcher(mapping, _adapter(backend))
        payload = make_confirmed_batch(
            "m9-vblock-red-01", batch_id="loopback-duplicate-batch", selection_id="loopback-selection"
        )
        replay_same_batch = payload
        replay_same_selection = make_confirmed_batch(
            "m9-vblock-red-01",
            batch_id="loopback-new-batch",
            selection_id="loopback-selection",
        )

        acknowledgements, results = loopback_replay(
            (payload, replay_same_batch, replay_same_selection), dispatcher
        )

        self.assertEqual(3, len(acknowledgements))
        self.assertTrue(all(ack["messageType"] == "batch_ack" for ack in acknowledgements))
        self.assertTrue(results[0].success)
        self.assertTrue(results[1].duplicate_batch)
        self.assertEqual("loopback-selection", results[2].duplicate_selection_ids[0])
        self.assertEqual("duplicate", results[2].state)
        self.assertEqual(1, len(backend.calls))

    def test_unknown_target_and_invalid_mapping_return_structured_rejections(self):
        message = make_confirmed_batch(
            "target-not-in-catalog", batch_id="unknown-target-batch", selection_id="unknown-target-selection"
        )
        receipt = BatchIdempotentConsumer().accept(message)
        result = M9BatchDispatcher(load_virtual_block_target_mapping(), _adapter(CountingBackend())).dispatch(receipt)
        self.assertFalse(result.accepted)
        self.assertEqual("unknown_target", result.failure_code)
        self.assertEqual((), result.executions)

        valid_receipt = BatchIdempotentConsumer().accept(
            make_confirmed_batch("m9-vblock-red-01", batch_id="invalid-map-batch")
        )
        invalid_map = M9BatchDispatcher({}, _adapter(CountingBackend())).dispatch(valid_receipt)
        self.assertFalse(invalid_map.accepted)
        self.assertEqual("invalid_target_mapping", invalid_map.failure_code)

    def test_empty_batch_is_rejected_by_existing_m8_boundary(self):
        message = make_confirmed_batch("m9-vblock-red-01")
        message["confirmedBatch"]["selections"] = []
        with self.assertRaisesRegex(ValueError, "1-3 selections"):
            BatchIdempotentConsumer().accept(message)

    def test_duplicate_selection_id_inside_one_batch_is_rejected_before_execution(self):
        message = _two_selection_batch()
        message["confirmedBatch"]["selections"][1]["selectionId"] = "selection-fail"
        receipt = BatchIdempotentConsumer().accept(message)
        backend = CountingBackend()

        result = M9BatchDispatcher(load_virtual_block_target_mapping(), _adapter(backend)).dispatch(receipt)

        self.assertFalse(result.accepted)
        self.assertEqual("invalid_confirmed_batch", result.failure_code)
        self.assertEqual((), result.executions)
        self.assertEqual([], backend.calls)

    def test_failure_is_structured_and_next_selection_in_same_batch_executes(self):
        backend = CountingBackend(fail_count=1)
        dispatcher = M9BatchDispatcher(
            load_virtual_block_target_mapping(), _adapter(backend), RobotOperation.PICK_AND_PLACE
        )
        receipt = BatchIdempotentConsumer().accept(_two_selection_batch())

        result = dispatcher.dispatch(receipt)

        self.assertTrue(result.accepted)
        self.assertEqual(2, len(result.executions))
        self.assertFalse(result.executions[0].success)
        self.assertEqual("backend_execution_error", result.executions[0].result.failure_code)
        self.assertTrue(result.executions[1].success)
        self.assertEqual("block_sim_02", result.executions[1].logical_block_id)
        self.assertEqual(2, len(backend.calls))


if __name__ == "__main__":
    unittest.main()
