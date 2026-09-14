"""Pure-software tests for the M9 logical object / robot adapter contract."""

import ast
from dataclasses import FrozenInstanceError
import inspect
import unittest

from integration import m9_robot_adapter as contract


def selection(selection_id, target_id, predicted_class_index, slot_index):
    return {
        "selectionId": selection_id,
        "predictedClassIndex": predicted_class_index,
        "slotIndex": slot_index,
        "targetId": target_id,
        "semanticLabel": "virtual block",
        "hasWorldPosition": True,
        "worldPosition": {"x": 900.0, "y": 800.0, "z": 700.0},
        "resolvedUtc": "2026-09-14T01:00:00.0000000Z",
        "provenance": contract.FROZEN_SELECTION_PROVENANCE,
    }


def confirmed_message(selections=None):
    batch_id = "m8-batch-0001"
    return {
        "protocolVersion": 1,
        "messageType": contract.CONFIRMED_BATCH_MESSAGE,
        "batchId": batch_id,
        "confirmedBatch": {
            "batchId": batch_id,
            "groupId": "m8-group-0002",
            "groupIndex": 2,
            "submittedUtc": "2026-09-14T01:00:01.0000000Z",
            "provenance": contract.CONFIRMED_BATCH_PROVENANCE,
            "selections": selections if selections is not None else [
                selection("selection-red", "target-red", 0, 0),
                selection("selection-blue", "target-blue", 2, 2),
            ],
        },
    }


class RecordingAdapter:
    def __init__(self):
        self.requests = []

    def accept_object_request(self, request):
        self.requests.append(request)


class M9RobotAdapterContractTests(unittest.TestCase):
    def test_maps_confirmed_selections_and_preserves_identity_provenance(self):
        requests = contract.confirmed_batch_to_robot_requests(
            confirmed_message(),
            {"target-red": "block_red_01", "target-blue": "block_blue_01"},
        )

        self.assertEqual(("block_red_01", "block_blue_01"), tuple(r.logical_block_id for r in requests))
        first = requests[0]
        self.assertEqual("m8-batch-0001", first.batch_id)
        self.assertEqual("m8-group-0002", first.group_id)
        self.assertEqual(2, first.group_index)
        self.assertEqual("selection-red", first.selection_id)
        self.assertEqual("target-red", first.source_target_id)
        self.assertEqual(0, first.predicted_class_index)
        self.assertEqual(0, first.slot_index)
        self.assertEqual("virtual block", first.semantic_label)
        self.assertEqual("2026-09-14T01:00:01.0000000Z", first.submitted_utc)
        self.assertEqual("2026-09-14T01:00:00.0000000Z", first.resolved_utc)
        self.assertEqual(contract.CONFIRMED_BATCH_PROVENANCE, first.batch_provenance)
        self.assertEqual(contract.FROZEN_SELECTION_PROVENANCE, first.selection_provenance)
        self.assertFalse(hasattr(first, "world_position"))
        self.assertFalse(hasattr(first, "mujoco_body_name"))

    def test_fake_adapter_receives_only_an_immutable_logical_request(self):
        request = contract.confirmed_batch_to_robot_requests(
            confirmed_message([selection("selection-red", "target-red", 0, 0)]),
            {"target-red": "block_red_01"},
        )[0]
        adapter = RecordingAdapter()

        self.assertIsInstance(adapter, contract.RobotAdapter)
        adapter.accept_object_request(request)
        self.assertEqual([request], adapter.requests)
        with self.assertRaises(FrozenInstanceError):
            request.logical_block_id = "block_blue_01"

    def test_rejects_unknown_target_id(self):
        with self.assertRaises(contract.UnknownTargetIdError):
            contract.confirmed_batch_to_robot_requests(
                confirmed_message(), {"target-red": "block_red_01"}
            )

    def test_rejects_duplicate_target_and_selection_ids(self):
        duplicate_target = confirmed_message([
            selection("selection-one", "target-red", 0, 0),
            selection("selection-two", "target-red", 1, 1),
        ])
        with self.assertRaises(contract.DuplicateTargetIdError):
            contract.confirmed_batch_to_robot_requests(
                duplicate_target, {"target-red": "block_red_01"}
            )

        duplicate_selection = confirmed_message([
            selection("selection-one", "target-red", 0, 0),
            selection("selection-one", "target-blue", 1, 1),
        ])
        with self.assertRaises(contract.ConfirmedBatchContractError):
            contract.confirmed_batch_to_robot_requests(
                duplicate_selection,
                {"target-red": "block_red_01", "target-blue": "block_blue_01"},
            )

    def test_rejects_blank_and_noncanonical_target_ids(self):
        for invalid_target_id in (None, "", "   ", " target-red "):
            with self.subTest(target_id=invalid_target_id):
                payload = confirmed_message([selection("selection", invalid_target_id, 0, 0)])
                with self.assertRaises(contract.IllegalTargetIdError):
                    contract.confirmed_batch_to_robot_requests(
                        payload, {"target-red": "block_red_01"}
                    )

    def test_rejects_invalid_logical_id_registry(self):
        for logical_id in ("obj_0", "block_Red_01", "block_red", "block_red_01 "):
            with self.subTest(logical_id=logical_id):
                with self.assertRaises(contract.InvalidTargetMappingError):
                    contract.confirmed_batch_to_robot_requests(
                        confirmed_message([selection("selection", "target-red", 0, 0)]),
                        {"target-red": logical_id},
                    )

    def test_rejects_mismatched_batch_id_and_unconfirmed_provenance(self):
        mismatched = confirmed_message([selection("selection", "target-red", 0, 0)])
        mismatched["batchId"] = "different-batch"
        with self.assertRaises(contract.ConfirmedBatchContractError):
            contract.confirmed_batch_to_robot_requests(mismatched, {"target-red": "block_red_01"})

        unconfirmed = confirmed_message([selection("selection", "target-red", 0, 0)])
        unconfirmed["confirmedBatch"]["provenance"] = "unconfirmed"
        with self.assertRaises(contract.ConfirmedBatchContractError):
            contract.confirmed_batch_to_robot_requests(unconfirmed, {"target-red": "block_red_01"})

    def test_rejects_two_target_ids_resolving_to_same_logical_block(self):
        payload = confirmed_message([
            selection("selection-one", "target-red-a", 0, 0),
            selection("selection-two", "target-red-b", 1, 1),
        ])
        with self.assertRaises(contract.DuplicateTargetIdError):
            contract.confirmed_batch_to_robot_requests(
                payload,
                {"target-red-a": "block_red_01", "target-red-b": "block_red_01"},
            )

    def test_contract_has_no_hardware_or_simulator_imports(self):
        tree = ast.parse(inspect.getsource(contract))
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertTrue({"mujoco", "serial", "socket", "UnityEngine"}.isdisjoint(imported_roots))


if __name__ == "__main__":
    unittest.main()
