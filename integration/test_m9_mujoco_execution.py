"""Pure-software tests for M9 scene binding and execution feedback."""

import unittest
import copy
from dataclasses import replace

import numpy as np

from integration import m9_robot_adapter as contract
from integration.m9_mujoco_execution import (
    BackendExecutionOutcome,
    DEFAULT_M9_SCENE_BINDINGS,
    ExistingFr3UmiPickPlaceBackend,
    FR3_UMI_BASELINE_SNAPSHOT,
    InvalidLogicalBlockIdError,
    InvalidSceneBindingError,
    MujocoRobotExecutionAdapter,
    ResolvedSimulatorObject,
    RobotExecutionRequest,
    RobotOperation,
    SceneBindingRegistry,
    UnknownLogicalBlockError,
    create_fr3_umi_mujoco_adapter,
    create_execution_requests,
    make_mujoco_name_lookup,
)


def _selection_message(target_id="target-green"):
    batch_id = "m9-batch-0001"
    return {
        "protocolVersion": 1,
        "messageType": contract.CONFIRMED_BATCH_MESSAGE,
        "batchId": batch_id,
        "confirmedBatch": {
            "batchId": batch_id,
            "groupId": "m9-group-0001",
            "groupIndex": 0,
            "submittedUtc": "2026-09-14T03:00:01.0000000Z",
            "provenance": contract.CONFIRMED_BATCH_PROVENANCE,
            "selections": [
                {
                    "selectionId": "selection-green-01",
                    "predictedClassIndex": 1,
                    "slotIndex": 1,
                    "targetId": target_id,
                    "semanticLabel": "virtual block",
                    "resolvedUtc": "2026-09-14T03:00:00.0000000Z",
                    "provenance": contract.FROZEN_SELECTION_PROVENANCE,
                }
            ],
        },
    }


def _request(logical_block_id="block_green_01", operation=RobotOperation.PICK_AND_PLACE):
    target_id = "target-green"
    object_request = contract.confirmed_batch_to_robot_requests(
        _selection_message(target_id), {target_id: logical_block_id}
    )[0]
    return RobotExecutionRequest("m9-request-0001", object_request, operation)


class FakeMujocoApi:
    class mjtObj:
        mjOBJ_BODY = 1

    calls = []

    @classmethod
    def mj_name2id(cls, model, object_type, name):
        cls.calls.append((model, object_type, name))
        return model.get(name, -1)


class FakeBackend:
    def __init__(self, outcome=None, error=None):
        self.outcome = outcome or BackendExecutionOutcome(
            success=True,
            execution_provenance="fake-baseline-wrapper",
            backend_execution_id="sim-run-0001",
        )
        self.error = error
        self.calls = []

    def execute(self, request, simulator_object):
        self.calls.append((request, simulator_object))
        if self.error is not None:
            raise self.error
        return self.outcome


class FakeMujocoRuntime(FakeMujocoApi):
    resets = []
    forwards = []

    class mjtObj:
        mjOBJ_BODY = 1

    class mjtJoint:
        mjJNT_FREE = 0

    @classmethod
    def mj_resetData(cls, model, data):
        cls.resets.append((model, data))

    @classmethod
    def mj_forward(cls, model, data):
        cls.forwards.append((model, data))


class FakeBaselinePlanner:
    calls = []
    result_override = None

    def __init__(self, model, data, obj, place=False):
        self.model = model
        self.data = data
        self.obj = obj
        self.place = place
        self.run_options = None
        self.__class__.calls.append(self)

    def run_headless(self, max_steps, quiet):
        self.run_options = (max_steps, quiet)
        return self

    def summary(self):
        result = self.__class__.result_override
        if result is None:
            result = "place_ok" if self.place else "lift_ok"
        return {
            "result": result,
            "up": 0.28,
            "moved": 0.0,
            "contacts": 2,
            "grabbed": True,
            "in_box": self.place,
        }


class ContinuousFakePlanner(FakeBaselinePlanner):
    calls = []

    def __init__(self, model, data, obj, place=False, start_arm_q=None, **_kwargs):
        self.start_arm_q = None if start_arm_q is None else np.asarray(start_arm_q).copy()
        self.model = model
        self.data = data
        self.obj = obj
        self.place = place
        self.run_options = None
        self.__class__.calls.append(self)
        current = np.asarray(data.qpos[:7], dtype=float)
        if self.start_arm_q is None:
            data.qpos[:7] = current + 1.0
        else:
            data.qpos[:7] = self.start_arm_q + 1.0


class FakeMutableSceneModel(dict):
    def __init__(self):
        super().__init__({"obj_0": 4, "obj_1": 7, "obj_2": 8, "obj_3": 9})
        # Match the writable NumPy views exposed by real MuJoCo model arrays.
        self.actuator_forcerange = np.array([[-100.0, 100.0] for _ in range(8)])
        self.geom_friction = np.array(
            [[0.7 + index / 10.0, 0.1, 0.01] for index in range(5)]
        )


class ModelMutatingPlanner(FakeBaselinePlanner):
    constructor_snapshots = []
    fail_next = False

    def __init__(self, model, data, obj, place=False):
        self.__class__.constructor_snapshots.append(
            (
                obj,
                copy.deepcopy(model.actuator_forcerange),
                copy.deepcopy(model.geom_friction),
            )
        )
        for actuator_index in range(7):
            model.actuator_forcerange[actuator_index] *= 3.0
        model.actuator_forcerange[7] = [-200.0, 200.0]
        for friction in model.geom_friction:
            friction[0] = 2.0
        if self.__class__.fail_next:
            self.__class__.fail_next = False
            raise RuntimeError("injected planner construction failure")
        super().__init__(model, data, obj, place=place)


class M9MujocoExecutionTests(unittest.TestCase):
    def setUp(self):
        FakeMujocoApi.calls = []
        self.model = {"obj_1": 7}
        self.name_lookup = make_mujoco_name_lookup(
            FakeMujocoApi, self.model, FakeMujocoApi.mjtObj.mjOBJ_BODY
        )
        self.registry = SceneBindingRegistry({"block_green_01": "obj_1"})

    def _real_baseline_adapter(self):
        FakeMujocoRuntime.calls = []
        FakeMujocoRuntime.resets = []
        FakeMujocoRuntime.forwards = []
        FakeBaselinePlanner.calls = []
        FakeBaselinePlanner.result_override = None
        model = {"obj_0": 4, "obj_1": 7, "obj_2": 8, "obj_3": 9}
        data = object()
        adapter = create_fr3_umi_mujoco_adapter(
            scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
            mujoco_module=FakeMujocoRuntime,
            scene_builder=lambda: (model, data),
            planner_class=FakeBaselinePlanner,
            max_steps=321,
        )
        return adapter, model, data

    def test_default_scene_bindings_are_explicit_simulation_only_ids(self):
        self.assertEqual(
            {
                "block_sim_01": "obj_0",
                "block_sim_02": "obj_1",
                "block_sim_03": "obj_2",
                "block_sim_04": "obj_3",
            },
            dict(DEFAULT_M9_SCENE_BINDINGS),
        )

    def test_concrete_backend_calls_existing_headless_planner_for_pick(self):
        adapter, model, data = self._real_baseline_adapter()
        request = _request("block_sim_01", RobotOperation.PICK)

        result = adapter.execute(request)

        self.assertTrue(result.success)
        self.assertEqual("m9-batch-0001", result.source_batch_id)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))
        self.assertNotIn("obj_0", repr(result))
        self.assertIn(FR3_UMI_BASELINE_SNAPSHOT, result.execution_provenance)
        self.assertIn("GripperGraspPlanner:lift_ok", result.execution_provenance)
        self.assertEqual(32, len(result.backend_execution_id))
        self.assertEqual(1, len(FakeBaselinePlanner.calls))
        planner = FakeBaselinePlanner.calls[0]
        self.assertEqual((model, data, "obj_0", False), (
            planner.model, planner.data, planner.obj, planner.place
        ))
        self.assertEqual((321, True), planner.run_options)
        self.assertEqual([(model, data)], FakeMujocoRuntime.resets)
        self.assertEqual([(model, data), (model, data)], FakeMujocoRuntime.forwards)
        self.assertEqual(
            [(model, FakeMujocoRuntime.mjtObj.mjOBJ_BODY, "obj_0")] * 2,
            FakeMujocoRuntime.calls,
        )

    def test_concrete_backend_runs_pick_place_mode_and_accepts_place_ok(self):
        adapter, _model, _data = self._real_baseline_adapter()

        result = adapter.execute(
            _request("block_sim_02", RobotOperation.PICK_AND_PLACE)
        )

        self.assertTrue(result.success)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))
        self.assertIn("GripperGraspPlanner:place_ok", result.execution_provenance)
        self.assertTrue(FakeBaselinePlanner.calls[0].place)

    def test_concrete_backend_preserves_baseline_failure_status(self):
        adapter, _model, _data = self._real_baseline_adapter()
        FakeBaselinePlanner.result_override = "grasp_fail"

        result = adapter.execute(
            _request("block_sim_01", RobotOperation.PICK_AND_PLACE)
        )

        self.assertFalse(result.success)
        self.assertEqual("pick_place_failed", result.failure_code)
        self.assertIn("grasp_fail", result.failure_reason)
        self.assertIn("expected 'place_ok'", result.failure_reason)
        self.assertIn("GripperGraspPlanner:grasp_fail", result.execution_provenance)

    def test_concrete_backend_rejects_a_stale_scene_object_id(self):
        FakeMujocoRuntime.calls = []
        FakeMujocoRuntime.resets = []
        FakeMujocoRuntime.forwards = []
        FakeBaselinePlanner.calls = []
        model = {"obj_0": 4}
        data = object()
        backend = ExistingFr3UmiPickPlaceBackend(
            FakeMujocoRuntime, model, data, FakeBaselinePlanner
        )
        simulator_object = ResolvedSimulatorObject("block_sim_01", "obj_0", 99)

        with self.assertRaisesRegex(RuntimeError, "no longer matches"):
            backend.execute(
                _request("block_sim_01", RobotOperation.PICK),
                simulator_object,
            )

        self.assertEqual([], FakeMujocoRuntime.resets)
        self.assertEqual([], FakeBaselinePlanner.calls)

    def test_repeated_requests_restore_model_parameters_and_recover_after_failure(self):
        FakeMujocoRuntime.calls = []
        FakeMujocoRuntime.resets = []
        FakeMujocoRuntime.forwards = []
        FakeBaselinePlanner.calls = []
        ModelMutatingPlanner.constructor_snapshots = []
        ModelMutatingPlanner.fail_next = True
        model = FakeMutableSceneModel()
        data = object()
        baseline_actuator_forcerange = copy.deepcopy(model.actuator_forcerange)
        baseline_geom_friction = copy.deepcopy(model.geom_friction)
        adapter = create_fr3_umi_mujoco_adapter(
            scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
            mujoco_module=FakeMujocoRuntime,
            scene_builder=lambda: (model, data),
            planner_class=ModelMutatingPlanner,
        )

        results = [
            adapter.execute(_request(logical_id, RobotOperation.PICK_AND_PLACE))
            for logical_id in (
                "block_sim_01",
                "block_sim_02",
                "block_sim_03",
                "block_sim_04",
            )
        ]

        self.assertEqual("backend_execution_error", results[0].failure_code)
        self.assertTrue(all(result.success for result in results[1:]))
        self.assertEqual(4, len(ModelMutatingPlanner.constructor_snapshots))
        self.assertEqual(3, len(ModelMutatingPlanner.calls))
        for _object_name, actuator_values, friction_values in ModelMutatingPlanner.constructor_snapshots:
            np.testing.assert_array_equal(baseline_actuator_forcerange, actuator_values)
            np.testing.assert_array_equal(baseline_geom_friction, friction_values)
        np.testing.assert_array_equal(baseline_actuator_forcerange, model.actuator_forcerange)
        np.testing.assert_array_equal(baseline_geom_friction, model.geom_friction)
        self.assertEqual(4, len(FakeMujocoRuntime.resets))
        # One initial scene forward plus one forward after each data reset.
        self.assertEqual(5, len(FakeMujocoRuntime.forwards))

    def test_persistent_context_chains_arm_state_without_per_request_teleport(self):
        ContinuousFakePlanner.calls = []
        ContinuousFakePlanner.result_override = None
        model = FakeMutableSceneModel()
        model.jnt_type = np.array([], dtype=int)
        model.jnt_qposadr = np.array([], dtype=int)
        model.jnt_dofadr = np.array([], dtype=int)
        model.njnt = 0

        class PersistentData:
            qpos = np.zeros(9, dtype=float)
            qvel = np.zeros(8, dtype=float)
            ctrl = np.zeros(8, dtype=float)
            act = np.zeros(8, dtype=float)
            xpos = np.zeros((10, 3), dtype=float)

        data = PersistentData()
        data.qpos[:7] = np.arange(7, dtype=float)
        FakeMujocoRuntime.resets = []
        FakeMujocoRuntime.forwards = []
        adapter = create_fr3_umi_mujoco_adapter(
            scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
            mujoco_module=FakeMujocoRuntime,
            scene_builder=lambda: (model, data),
            planner_class=ContinuousFakePlanner,
            persistent_world=True,
            placement_targets=((0.0, 0.0, 0.2),) * 4,
        )

        outcomes = []
        for build_slot in range(3):
            request = _request("block_sim_01", RobotOperation.PICK_AND_PLACE)
            request = RobotExecutionRequest(
                request.request_id,
                replace(request.selection, build_slot_index=build_slot),
                request.operation,
            )
            outcomes.append(adapter.execute(request))

        self.assertTrue(all(item.success for item in outcomes), outcomes)
        self.assertEqual(3, len(ContinuousFakePlanner.calls))
        self.assertIsNone(ContinuousFakePlanner.calls[0].start_arm_q)
        np.testing.assert_array_equal(
            np.arange(7, dtype=float) + 1.0,
            ContinuousFakePlanner.calls[1].start_arm_q,
        )
        np.testing.assert_array_equal(
            np.arange(7, dtype=float) + 2.0,
            ContinuousFakePlanner.calls[2].start_arm_q,
        )
        self.assertEqual([], FakeMujocoRuntime.resets)
        evidence = adapter.world_state_evidence()
        self.assertEqual(1, evidence["persistentStateResetCount"])
        self.assertTrue(evidence["continuousRobotMotion"])
        self.assertEqual(2, len(evidence["interActionTransitions"]))
        self.assertTrue(all(not item["teleport"] for item in evidence["interActionTransitions"]))

    def test_persistent_context_supports_one_two_three_action_boundaries(self):
        for action_count in (1, 2, 3):
            ContinuousFakePlanner.calls = []
            ContinuousFakePlanner.result_override = None
            model = FakeMutableSceneModel()
            model.jnt_type = np.array([], dtype=int)
            model.jnt_qposadr = np.array([], dtype=int)
            model.jnt_dofadr = np.array([], dtype=int)
            model.njnt = 0

            class PersistentData:
                qpos = np.zeros(9, dtype=float)
                qvel = np.zeros(8, dtype=float)
                ctrl = np.zeros(8, dtype=float)
                act = np.zeros(8, dtype=float)
                xpos = np.zeros((10, 3), dtype=float)

            data = PersistentData()
            adapter = create_fr3_umi_mujoco_adapter(
                scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
                mujoco_module=FakeMujocoRuntime,
                scene_builder=lambda: (model, data),
                planner_class=ContinuousFakePlanner,
                persistent_world=True,
                placement_targets=((0.0, 0.0, 0.2),) * 4,
            )
            outcomes = []
            for build_slot in range(action_count):
                request = _request("block_sim_01", RobotOperation.PICK_AND_PLACE)
                request = RobotExecutionRequest(
                    request.request_id,
                    replace(request.selection, build_slot_index=build_slot),
                    request.operation,
                )
                outcomes.append(adapter.execute(request))

            self.assertTrue(all(item.success for item in outcomes))
            self.assertEqual(action_count, len(ContinuousFakePlanner.calls))
            evidence = adapter.world_state_evidence()
            self.assertEqual(action_count - 1, len(evidence["interActionTransitions"]))
            self.assertEqual(1, evidence["persistentStateResetCount"])
            self.assertTrue(all(not item["teleport"] for item in evidence["interActionTransitions"]))

    def test_confirmed_selection_becomes_identified_operation_with_provenance(self):
        ids = iter(("request-1",))
        requests = create_execution_requests(
            _selection_message(),
            {"target-green": "block_green_01"},
            RobotOperation.PICK_AND_PLACE,
            request_id_factory=lambda: next(ids),
        )

        self.assertEqual(1, len(requests))
        request = requests[0]
        self.assertEqual("request-1", request.request_id)
        self.assertEqual("block_green_01", request.logical_block_id)
        self.assertEqual(RobotOperation.PICK_AND_PLACE, request.operation)
        self.assertEqual("m9-batch-0001", request.selection.batch_id)
        self.assertEqual("selection-green-01", request.selection.selection_id)
        self.assertEqual("target-green", request.selection.source_target_id)
        self.assertFalse(hasattr(request.selection, "simulator_object_name"))
        self.assertFalse(hasattr(request, "simulator_object_id"))

    def test_mujoco_name_lookup_uses_exact_name_and_explicit_object_type(self):
        resolved = self.registry.resolve_scene_object("block_green_01", self.name_lookup)

        self.assertEqual("obj_1", resolved.simulator_object_name)
        self.assertEqual(7, resolved.simulator_object_id)
        self.assertEqual(
            [(self.model, FakeMujocoApi.mjtObj.mjOBJ_BODY, "obj_1")],
            FakeMujocoApi.calls,
        )

    def test_unknown_logical_id_fails_before_scene_or_backend_lookup(self):
        backend = FakeBackend()
        adapter = MujocoRobotExecutionAdapter(self.registry, self.name_lookup, backend)

        result = adapter.execute(_request("block_green_02"))

        self.assertFalse(result.success)
        self.assertEqual("unknown_logical_block", result.failure_code)
        self.assertIn("block_green_02", result.failure_reason)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))
        self.assertEqual([], FakeMujocoApi.calls)
        self.assertEqual([], backend.calls)

    def test_internal_simulator_name_is_not_accepted_as_a_logical_id(self):
        with self.assertRaises(InvalidSceneBindingError):
            SceneBindingRegistry({"obj_1": "obj_1"})
        with self.assertRaises(InvalidLogicalBlockIdError):
            self.registry.resolve_binding("block_green_01 ")

    def test_missing_scene_object_returns_explicit_failure(self):
        backend = FakeBackend()
        missing_lookup = make_mujoco_name_lookup(
            FakeMujocoApi, {}, FakeMujocoApi.mjtObj.mjOBJ_BODY
        )
        adapter = MujocoRobotExecutionAdapter(self.registry, missing_lookup, backend)

        result = adapter.execute(_request())

        self.assertFalse(result.success)
        self.assertEqual("missing_simulator_object", result.failure_code)
        self.assertNotIn("obj_1", result.failure_reason)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))
        self.assertEqual([], backend.calls)

    def test_success_calls_backend_once_and_returns_structured_result(self):
        backend = FakeBackend()
        adapter = MujocoRobotExecutionAdapter(self.registry, self.name_lookup, backend)
        request = _request(operation=RobotOperation.PICK)

        result = adapter.execute(request)

        self.assertTrue(result.success)
        self.assertIsNone(result.failure_code)
        self.assertIsNone(result.failure_reason)
        self.assertEqual(request.request_id, result.request_id)
        self.assertEqual(request.logical_block_id, result.logical_block_id)
        self.assertEqual(RobotOperation.PICK, result.operation)
        self.assertEqual("m9-batch-0001", result.source_batch_id)
        self.assertEqual("selection-green-01", result.source_selection_id)
        self.assertEqual("target-green", result.source_target_id)
        self.assertEqual(contract.CONFIRMED_BATCH_PROVENANCE, result.batch_provenance)
        self.assertEqual(contract.FROZEN_SELECTION_PROVENANCE, result.selection_provenance)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))
        self.assertEqual("sim-run-0001", result.backend_execution_id)
        self.assertEqual("fake-baseline-wrapper", result.execution_provenance)
        self.assertTrue(result.started_utc.endswith("Z"))
        self.assertTrue(result.completed_utc.endswith("Z"))
        self.assertEqual(1, len(backend.calls))
        self.assertEqual(request, backend.calls[0][0])
        self.assertEqual("obj_1", backend.calls[0][1].simulator_object_name)

    def test_backend_failure_is_returned_with_reason_and_provenance(self):
        backend = FakeBackend(
            outcome=BackendExecutionOutcome(
                success=False,
                execution_provenance="fake-baseline-wrapper",
                failure_reason="grasp planning failed for obj_1",
                backend_execution_id="sim-run-failed-1",
            )
        )
        adapter = MujocoRobotExecutionAdapter(self.registry, self.name_lookup, backend)

        result = adapter.execute(_request())

        self.assertFalse(result.success)
        self.assertEqual("pick_place_failed", result.failure_code)
        self.assertEqual("grasp planning failed for simulator object", result.failure_reason)
        self.assertNotIn("obj_1", result.failure_reason)
        self.assertEqual("sim-run-failed-1", result.backend_execution_id)
        self.assertEqual("fake-baseline-wrapper", result.execution_provenance)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))

    def test_backend_exception_becomes_structured_failure(self):
        backend = FakeBackend(error=RuntimeError("trajectory obj_1\nexception"))
        adapter = MujocoRobotExecutionAdapter(self.registry, self.name_lookup, backend)

        result = adapter.execute(_request())

        self.assertFalse(result.success)
        self.assertEqual("backend_execution_error", result.failure_code)
        self.assertIn("RuntimeError", result.failure_reason)
        self.assertNotIn("obj_1", result.failure_reason)
        self.assertNotIn("\n", result.failure_reason)
        self.assertEqual("adapter:backend_exception", result.execution_provenance)
        self.assertFalse(hasattr(result, "simulator_object_name"))
        self.assertFalse(hasattr(result, "simulator_object_id"))

    def test_invalid_scene_lookup_result_fails_explicitly(self):
        adapter = MujocoRobotExecutionAdapter(
            self.registry, lambda _name: "not-an-id", FakeBackend()
        )

        result = adapter.execute(_request())

        self.assertFalse(result.success)
        self.assertEqual("scene_lookup_failed", result.failure_code)
        self.assertEqual("MuJoCo body lookup failed", result.failure_reason)

    def test_duplicate_simulator_binding_is_rejected(self):
        with self.assertRaises(InvalidSceneBindingError):
            SceneBindingRegistry(
                {"block_green_01": "obj_1", "block_blue_01": "obj_1"}
            )

    def test_lookup_exception_is_a_scene_failure_and_never_calls_backend(self):
        def broken_lookup(_name):
            raise RuntimeError("model lookup failed")

        backend = FakeBackend()
        adapter = MujocoRobotExecutionAdapter(self.registry, broken_lookup, backend)

        result = adapter.execute(_request())

        self.assertFalse(result.success)
        self.assertEqual("scene_lookup_failed", result.failure_code)
        self.assertEqual("MuJoCo body lookup failed", result.failure_reason)
        self.assertEqual([], backend.calls)


if __name__ == "__main__":
    unittest.main()
