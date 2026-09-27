"""M9 scene binding and execution-result seam for a reused MuJoCo baseline.

This module owns the boundary between stable logical block IDs and simulator
objects. It does not implement robot planning or control. A thin backend must
adapt the existing FR3/UMI Pick/Place entry point to PickPlaceBackend after its
actual API has been inspected.
"""

from collections.abc import Mapping
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import importlib
import inspect
import math
from operator import index as integer_index
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Optional, Protocol
from uuid import uuid4

import numpy as np

from integration.m9_robot_adapter import (
    InvalidTargetMappingError,
    RobotObjectRequest,
    confirmed_batch_to_robot_requests,
    validate_logical_block_id,
)


class SceneBindingError(ValueError):
    """A logical block could not be resolved to a simulator object."""


class InvalidLogicalBlockIdError(SceneBindingError):
    """The supplied logical block ID is not canonical."""


class UnknownLogicalBlockError(SceneBindingError):
    """The canonical logical block ID has no explicit scene binding."""


class MissingSimulatorObjectError(SceneBindingError):
    """The configured simulator object name is absent from the loaded scene."""


class SceneLookupError(SceneBindingError):
    """The simulator name lookup failed or returned an invalid object ID."""


class InvalidSceneBindingError(ValueError):
    """The explicit logical-ID-to-simulator-name mapping is invalid."""


class RobotOperation(str, Enum):
    PICK = "pick"
    PICK_AND_PLACE = "pick_and_place"


FR3_UMI_BASELINE_SNAPSHOT = "a42a0876350f6c94747ce93fa640807b14b18bfd"

# The baseline assigns physical geometry by ordinal name and does not provide
# object colors/semantic labels. These simulation-only IDs deliberately avoid
# inventing such semantics; a caller maps confirmed TargetIds to them explicitly.
DEFAULT_M9_SCENE_BINDINGS = MappingProxyType(
    {
        "block_sim_01": "obj_0",
        "block_sim_02": "obj_1",
        "block_sim_03": "obj_2",
        "block_sim_04": "obj_3",
    }
)


def _required_text(value, field):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError("{} must be a non-empty exact string".format(field))
    return value


def _single_line_error(error):
    return " ".join(str(error).split()) or "unspecified error"


def _canonical_logical_block_id(value):
    try:
        return validate_logical_block_id(value)
    except InvalidTargetMappingError as error:
        raise InvalidLogicalBlockIdError(str(error)) from error


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class SceneObjectBinding:
    """One explicit logical identity to simulator-internal name binding."""

    logical_block_id: str
    simulator_object_name: str

    def __post_init__(self):
        _canonical_logical_block_id(self.logical_block_id)
        _required_text(self.simulator_object_name, "simulatorObjectName")


@dataclass(frozen=True)
class ResolvedSimulatorObject:
    """Simulator-only object reference; never part of the BCI selection request."""

    logical_block_id: str
    simulator_object_name: str
    simulator_object_id: int


class SceneBindingRegistry:
    """Immutable, exact mapping from logical block IDs to scene object names."""

    def __init__(self, logical_id_to_simulator_name):
        if not isinstance(logical_id_to_simulator_name, Mapping) or not logical_id_to_simulator_name:
            raise InvalidSceneBindingError("an explicit non-empty scene binding is required")

        by_logical_id = {}
        seen_simulator_names = set()
        for logical_block_id, simulator_object_name in logical_id_to_simulator_name.items():
            try:
                binding = SceneObjectBinding(logical_block_id, simulator_object_name)
            except ValueError as error:
                raise InvalidSceneBindingError(str(error)) from error
            if binding.simulator_object_name in seen_simulator_names:
                raise InvalidSceneBindingError(
                    "simulator object is bound more than once: {!r}".format(
                        binding.simulator_object_name
                    )
                )
            seen_simulator_names.add(binding.simulator_object_name)
            by_logical_id[binding.logical_block_id] = binding
        self._by_logical_id = MappingProxyType(by_logical_id)

    def resolve_binding(self, logical_block_id):
        logical_block_id = _canonical_logical_block_id(logical_block_id)
        try:
            return self._by_logical_id[logical_block_id]
        except KeyError as error:
            raise UnknownLogicalBlockError(
                "logical block ID is not registered: {!r}".format(logical_block_id)
            ) from error

    def resolve_scene_object(self, logical_block_id, name_lookup):
        """Resolve an exact registered name using the caller's MuJoCo lookup."""
        if not callable(name_lookup):
            raise SceneLookupError("scene name lookup must be callable")
        binding = self.resolve_binding(logical_block_id)
        try:
            raw_object_id = name_lookup(binding.simulator_object_name)
        except Exception as error:
            raise SceneLookupError(
                "scene lookup failed for {!r}: {}".format(
                    binding.simulator_object_name, _single_line_error(error)
                )
            ) from error

        if raw_object_id is None or isinstance(raw_object_id, bool):
            raise SceneLookupError(
                "scene lookup returned an invalid ID for {!r}".format(
                    binding.simulator_object_name
                )
            )
        try:
            object_id = integer_index(raw_object_id)
        except TypeError as error:
            raise SceneLookupError(
                "scene lookup returned a non-integer ID for {!r}".format(
                    binding.simulator_object_name
                )
            ) from error
        if object_id < 0:
            raise MissingSimulatorObjectError(
                "simulator object is missing from the loaded scene: {!r}".format(
                    binding.simulator_object_name
                )
            )
        return ResolvedSimulatorObject(
            logical_block_id=binding.logical_block_id,
            simulator_object_name=binding.simulator_object_name,
            simulator_object_id=object_id,
        )


def make_mujoco_name_lookup(mujoco_module, model, object_type):
    """Build an exact mj_name2id lookup with an explicitly selected object type.

    The caller must choose the type supported by the inspected scene, for
    example mjtObj.mjOBJ_BODY if the bound objects are bodies. This avoids
    guessing whether the source baseline represents its blocks as bodies,
    geoms, or another MuJoCo object kind.
    """
    if mujoco_module is None or model is None or object_type is None:
        raise SceneLookupError("MuJoCo module, model, and object type are required")
    lookup = getattr(mujoco_module, "mj_name2id", None)
    if not callable(lookup):
        raise SceneLookupError("MuJoCo API does not provide mj_name2id")

    def name_lookup(name):
        return lookup(model, object_type, name)

    return name_lookup


@dataclass(frozen=True)
class RobotExecutionRequest:
    """A requested operation plus the immutable, confirmed M8 selection."""

    request_id: str
    selection: RobotObjectRequest
    operation: RobotOperation

    def __post_init__(self):
        _required_text(self.request_id, "requestId")
        if not isinstance(self.selection, RobotObjectRequest):
            raise TypeError("selection must be a RobotObjectRequest")
        if not isinstance(self.operation, RobotOperation):
            raise ValueError("operation must be an explicit RobotOperation")

    @property
    def logical_block_id(self):
        return self.selection.logical_block_id


def create_execution_requests(
    confirmed_batch,
    target_id_to_logical_block_id,
    requested_operation,
    request_id_factory=None,
):
    """Convert a confirmed Quest batch to execution requests in selection order.

    Operation choice is supplied by the caller; the EEG/BCI contract only
    identifies the selected logical block. Request IDs are generated once per
    selection and may use an injected factory for deterministic tests.
    """
    if not isinstance(requested_operation, RobotOperation):
        raise ValueError("requested_operation must be an explicit RobotOperation")
    if request_id_factory is None:
        request_id_factory = lambda: uuid4().hex
    if not callable(request_id_factory):
        raise ValueError("request_id_factory must be callable")

    selections = confirmed_batch_to_robot_requests(
        confirmed_batch, target_id_to_logical_block_id
    )
    seen_request_ids = set()
    requests = []
    for selection in selections:
        request_id = _required_text(request_id_factory(), "requestId")
        if request_id in seen_request_ids:
            raise ValueError("request_id_factory returned a duplicate request ID")
        seen_request_ids.add(request_id)
        requests.append(
            RobotExecutionRequest(
                request_id=request_id,
                selection=selection,
                operation=requested_operation,
            )
        )
    return tuple(requests)


@dataclass(frozen=True)
class BackendExecutionOutcome:
    """Normalized outcome returned by a thin wrapper around the existing baseline."""

    success: bool
    execution_provenance: str
    failure_reason: Optional[str] = None
    backend_execution_id: Optional[str] = None

    def __post_init__(self):
        if type(self.success) is not bool:
            raise ValueError("success must be a boolean")
        _required_text(self.execution_provenance, "executionProvenance")
        if self.success:
            if self.failure_reason is not None:
                raise ValueError("successful outcome cannot have a failure reason")
        else:
            _required_text(self.failure_reason, "failureReason")
        if self.backend_execution_id is not None:
            _required_text(self.backend_execution_id, "backendExecutionId")


@dataclass(frozen=True)
class RobotExecutionResult:
    """M9 execution feedback with simulator-internal identity kept private."""

    request_id: str
    logical_block_id: str
    operation: RobotOperation
    success: bool
    failure_code: Optional[str]
    failure_reason: Optional[str]
    source_batch_id: str
    source_selection_id: str
    source_target_id: str
    batch_provenance: str
    selection_provenance: str
    backend_execution_id: Optional[str]
    execution_provenance: str
    started_utc: str
    completed_utc: str

    def __post_init__(self):
        _required_text(self.request_id, "requestId")
        _canonical_logical_block_id(self.logical_block_id)
        if not isinstance(self.operation, RobotOperation):
            raise ValueError("operation must be a RobotOperation")
        if type(self.success) is not bool:
            raise ValueError("success must be a boolean")
        if self.success:
            if self.failure_code is not None or self.failure_reason is not None:
                raise ValueError("successful result cannot contain a failure")
        else:
            _required_text(self.failure_code, "failureCode")
            _required_text(self.failure_reason, "failureReason")
        _required_text(self.source_batch_id, "sourceBatchId")
        _required_text(self.source_selection_id, "sourceSelectionId")
        _required_text(self.source_target_id, "sourceTargetId")
        _required_text(self.batch_provenance, "batchProvenance")
        _required_text(self.selection_provenance, "selectionProvenance")
        _required_text(self.execution_provenance, "executionProvenance")
        _required_text(self.started_utc, "startedUtc")
        _required_text(self.completed_utc, "completedUtc")


class PickPlaceBackend(Protocol):
    """Narrow port for a wrapper that calls the existing Pick/Place baseline."""

    def execute(
        self,
        request: RobotExecutionRequest,
        simulator_object: ResolvedSimulatorObject,
    ) -> BackendExecutionOutcome:
        """Execute the requested operation and return BackendExecutionOutcome."""
        ...


class ExistingFr3UmiPickPlaceBackend:
    """Headless adapter that calls the inspected FR3/UMI planner unchanged."""

    def __init__(
        self,
        mujoco_module,
        model,
        data,
        planner_class,
        max_steps=9000,
        persistent_world=False,
        placement_targets=None,
        simulation_step_callback=None,
        placement_target_resolver=None,
        before_execute_callback=None,
        post_release_callback=None,
        post_release_settle_steps=0,
        capture_trajectory=False,
    ):
        if not callable(getattr(mujoco_module, "mj_name2id", None)):
            raise TypeError("MuJoCo module must provide mj_name2id")
        if not callable(getattr(mujoco_module, "mj_resetData", None)):
            raise TypeError("MuJoCo module must provide mj_resetData")
        if not callable(getattr(mujoco_module, "mj_forward", None)):
            raise TypeError("MuJoCo module must provide mj_forward")
        if model is None or data is None:
            raise ValueError("a loaded MuJoCo model and data are required")
        if not callable(planner_class):
            raise TypeError("planner_class must be the existing GripperGraspPlanner")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        try:
            body_type = mujoco_module.mjtObj.mjOBJ_BODY
        except AttributeError as error:
            raise TypeError("MuJoCo module must expose mjtObj.mjOBJ_BODY") from error

        self._mujoco = mujoco_module
        self._model = model
        self._data = data
        self._planner_class = planner_class
        self._max_steps = max_steps
        self._persistent_world = bool(persistent_world)
        self._placement_targets = tuple(placement_targets or ())
        self._simulation_step_callback = simulation_step_callback
        if placement_target_resolver is not None and not callable(placement_target_resolver):
            raise TypeError("placement_target_resolver must be callable or None")
        if before_execute_callback is not None and not callable(before_execute_callback):
            raise TypeError("before_execute_callback must be callable or None")
        if post_release_callback is not None and not callable(post_release_callback):
            raise TypeError("post_release_callback must be callable or None")
        if isinstance(post_release_settle_steps, bool) or not isinstance(post_release_settle_steps, int):
            raise TypeError("post_release_settle_steps must be a non-negative integer")
        if post_release_settle_steps < 0:
            raise ValueError("post_release_settle_steps must be non-negative")
        self._placement_target_resolver = placement_target_resolver
        self._before_execute_callback = before_execute_callback
        self._post_release_callback = post_release_callback
        self._post_release_settle_steps = post_release_settle_steps
        self._capture_trajectory = bool(capture_trajectory)
        self._slot_to_simulator_object = {}
        self._trajectory_records = []
        if self._persistent_world and len(self._placement_targets) != 4:
            raise ValueError("persistent context world requires four placement targets")
        self._baseline_model_parameters = {}
        for parameter_name in ("actuator_forcerange", "geom_friction"):
            parameter_values = getattr(model, parameter_name, None)
            if parameter_values is not None:
                self._baseline_model_parameters[parameter_name] = copy.deepcopy(
                    parameter_values
                )
        self.name_lookup = make_mujoco_name_lookup(
            mujoco_module, model, body_type
        )
        self._initial_qpos = copy.deepcopy(getattr(data, "qpos", None))
        self._initial_qvel = copy.deepcopy(getattr(data, "qvel", None))
        self._initial_ctrl = copy.deepcopy(getattr(data, "ctrl", None))
        self._initial_act = copy.deepcopy(getattr(data, "act", None))
        self._free_joint_slices = self._find_free_joint_slices() if self._persistent_world else ()
        self._persistent_reset_count = 0
        self._persistent_execution_started = False
        self._placement_records = []
        self._planner_start_arm_qs = []
        self._inter_action_transitions = []
        self._last_planner = None

    def set_simulation_step_callback(self, callback):
        """Set an optional presentation-only callback after each MuJoCo step.

        The default remains the existing planner.run_headless path.  The
        callback is intentionally a narrow integration seam for consumers
        that need continuous state sampling while preserving the planner's
        public step semantics.
        """
        if callback is not None and not callable(callback):
            raise TypeError("simulation_step_callback must be callable or None")
        self._simulation_step_callback = callback

    def _table_bounds(self):
        """Return table limits from the compiled MuJoCo tabletop geometry."""
        for geom_id in range(int(self._model.ngeom)):
            name = self._mujoco.mj_id2name(
                self._model, self._mujoco.mjtObj.mjOBJ_GEOM, geom_id
            )
            if name != "tabletop":
                continue
            center = self._data.geom_xpos[geom_id]
            half = self._model.geom_size[geom_id]
            return {
                "x": [float(center[0] - half[0]), float(center[0] + half[0])],
                "y": [float(center[1] - half[1]), float(center[1] + half[1])],
                "topZ": float(center[2] + half[2]),
            }
        return None

    def _record_trajectory_phase(self, phase, planner, simulator_object, target_position):
        if not self._capture_trajectory:
            return
        try:
            gripper_id = int(planner.gripper_bid)
            object_id = int(planner.obj_bid)
            gripper_position = [float(value) for value in self._data.xpos[gripper_id]]
            block_position = [float(value) for value in self._data.xpos[object_id]]
            pad_position = None
            xmat = getattr(self._data, "xmat", None)
            if xmat is not None:
                rotation = np.asarray(xmat[gripper_id], dtype=float).reshape(3, 3)
                from utils.gripper_scene import (
                    MAX_OPEN,
                    PAD_OFFSET_IN_FLANGE,
                    object_half_extents,
                )
                pad_position = [
                    float(value)
                    for value in np.asarray(self._data.xpos[gripper_id])
                    + rotation @ np.asarray(PAD_OFFSET_IN_FLANGE, dtype=float)
                ]
                half_extents = object_half_extents(
                    self._model, simulator_object.simulator_object_name
                )
                grasp_horizontal_error = float(
                    np.linalg.norm(
                        np.asarray(pad_position[:2]) - np.asarray(block_position[:2])
                    )
                )
                grasp_horizontal_tolerance = float(
                    max(half_extents[0], half_extents[1]) + (float(MAX_OPEN) / 2.0)
                )
            else:
                half_extents = None
                grasp_horizontal_error = None
                grasp_horizontal_tolerance = None
            reference = pad_position or gripper_position
            distance = float(np.linalg.norm(np.asarray(reference) - np.asarray(block_position)))
            self._trajectory_records.append({
                "phase": str(phase),
                "simulatorObject": simulator_object.simulator_object_name,
                "blockPositionMujoco": block_position,
                "gripperPositionMujoco": gripper_position,
                "gripperPadPositionMujoco": pad_position,
                "gripperToBlockDistanceMeters": distance,
                "blockHalfExtentsMujoco": (
                    [float(value) for value in half_extents]
                    if half_extents is not None else None
                ),
                "graspHorizontalErrorMeters": grasp_horizontal_error,
                "graspHorizontalToleranceMeters": grasp_horizontal_tolerance,
                "graspAlignmentWithinSizeTolerance": (
                    grasp_horizontal_error <= grasp_horizontal_tolerance
                    if grasp_horizontal_error is not None
                    else None
                ),
                "targetPositionMujoco": (
                    [float(value) for value in target_position]
                    if target_position is not None else None
                ),
                "tableBoundsMujoco": self._table_bounds(),
                "plannerPhase": str(planner.phase),
            })
        except (AttributeError, IndexError, TypeError, ValueError):
            # The optional evidence must not change the baseline execution path.
            return

    def _run_planner_headless_with_callback(self, planner, simulator_object, target_position):
        """Run the existing planner step loop while reporting real MuJoCo steps."""
        planner.quiet = True
        if not planner.ik_ok:
            planner.result = "ik_fail"
            return planner
        steps = 0
        previous_phase = planner.phase
        recorded_phases = set()
        transition_phases = {
            ("APPROACH", "CLOSE"): "pre_grasp",
            ("SETTLE", "LIFT"): "grasp",
            ("LIFT", "HOLD"): "carry",
            ("PLACE", "PLACE_HOLD"): "place",
            ("RELEASE", "DONE"): "release",
        }
        while planner.step() and steps < self._max_steps:
            current_phase = planner.phase
            evidence_phase = transition_phases.get((previous_phase, current_phase))
            if evidence_phase is not None and evidence_phase not in recorded_phases:
                self._record_trajectory_phase(
                    evidence_phase, planner, simulator_object, target_position
                )
                recorded_phases.add(evidence_phase)
            if current_phase == "RELEASE" and previous_phase != "RELEASE":
                if self._post_release_callback is not None:
                    self._post_release_callback(
                        self._mujoco,
                        self._model,
                        self._data,
                        planner,
                        simulator_object,
                        target_position,
                    )
                    self._mujoco.mj_forward(self._model, self._data)
            self._mujoco.mj_step(self._model, self._data)
            steps += 1
            if self._simulation_step_callback is not None:
                self._simulation_step_callback()
            previous_phase = current_phase
        if planner.phase == "DONE" and "release" not in recorded_phases:
            self._record_trajectory_phase(
                "release", planner, simulator_object, target_position
            )
        for _ in range(self._post_release_settle_steps):
            self._mujoco.mj_step(self._model, self._data)
            if self._simulation_step_callback is not None:
                self._simulation_step_callback()
        self._mujoco.mj_forward(self._model, self._data)
        return planner

    def _find_free_joint_slices(self):
        try:
            free_type = self._mujoco.mjtJoint.mjJNT_FREE
            joint_types = self._model.jnt_type
            qpos_addresses = self._model.jnt_qposadr
            dof_addresses = self._model.jnt_dofadr
            count = int(self._model.njnt)
        except AttributeError as error:
            raise TypeError("persistent MuJoCo world requires joint metadata") from error
        slices = []
        for joint_id in range(count):
            if int(joint_types[joint_id]) == int(free_type):
                qpos_start = int(qpos_addresses[joint_id])
                dof_start = int(dof_addresses[joint_id])
                slices.append((qpos_start, qpos_start + 7, dof_start, dof_start + 6))
        return tuple(slices)

    def _restore_baseline_model_parameters(self):
        """Undo per-planner model edits before and after each isolated request."""
        for parameter_name, baseline_values in self._baseline_model_parameters.items():
            current_values = getattr(self._model, parameter_name)
            current_values[...] = baseline_values

    def _reset_for_request(self):
        if not self._persistent_world:
            self._mujoco.mj_resetData(self._model, self._data)
            self._mujoco.mj_forward(self._model, self._data)
            return

        if self._initial_qpos is None or self._initial_qvel is None:
            raise RuntimeError("persistent MuJoCo world has no initial state snapshot")
        if self._persistent_execution_started:
            # The context-demo world is deliberately continuous.  Do not copy
            # _initial_qpos here: that was the old per-request arm teleport.
            # The completed object's free-body state and the robot's current
            # joint state remain authoritative.  Reset only controller memory
            # before the next smooth planner transition.
            if self._initial_ctrl is not None and getattr(self._data, "ctrl", None) is not None:
                self._data.ctrl[...] = self._initial_ctrl
            if self._initial_act is not None and getattr(self._data, "act", None) is not None:
                self._data.act[...] = self._initial_act
            self._mujoco.mj_forward(self._model, self._data)
            return
        preserved_objects = [
            (
                self._data.qpos[qpos_start:qpos_end].copy(),
                self._data.qvel[dof_start:dof_end].copy(),
                qpos_start,
                qpos_end,
                dof_start,
                dof_end,
            )
            for qpos_start, qpos_end, dof_start, dof_end in self._free_joint_slices
        ]
        self._data.qpos[...] = self._initial_qpos
        self._data.qvel[...] = self._initial_qvel
        for qpos, qvel, qpos_start, qpos_end, dof_start, dof_end in preserved_objects:
            self._data.qpos[qpos_start:qpos_end] = qpos
            self._data.qvel[dof_start:dof_end] = qvel
        if self._initial_ctrl is not None and getattr(self._data, "ctrl", None) is not None:
            self._data.ctrl[...] = self._initial_ctrl
        if self._initial_act is not None and getattr(self._data, "act", None) is not None:
            self._data.act[...] = self._initial_act
        self._persistent_reset_count += 1
        self._mujoco.mj_forward(self._model, self._data)

    def _planner_supports_start_arm_q(self):
        try:
            parameters = inspect.signature(self._planner_class).parameters
        except (TypeError, ValueError):
            return False
        return "start_arm_q" in parameters

    def _retreat_previous_action_to_safe_wait(self):
        if self._last_planner is None:
            return False
        retreat = getattr(self._last_planner, "retreat_to_safe_wait", None)
        if not callable(retreat):
            return False
        return bool(retreat(self._simulation_step_callback))

    def execute(self, request, simulator_object):
        expected_id = self.name_lookup(simulator_object.simulator_object_name)
        if expected_id < 0 or expected_id != simulator_object.simulator_object_id:
            raise RuntimeError(
                "resolved simulator object no longer matches the loaded scene"
            )

        # The reused planner edits model-level actuator/friction arrays;
        # restore those values before every planner construction.  The formal
        # four-cube Context Demo preserves free-body object state between
        # requests; legacy M9 callers retain the isolated reset behavior.
        self._restore_baseline_model_parameters()
        continuing = self._persistent_world and self._persistent_execution_started
        self._reset_for_request()
        retreat_performed = self._retreat_previous_action_to_safe_wait() if continuing else False
        continuation_start_q = None
        if continuing:
            qpos = getattr(self._data, "qpos", None)
            if qpos is not None and len(qpos) >= 7:
                continuation_start_q = np.asarray(qpos[:7], dtype=float).copy()
        place = request.operation is RobotOperation.PICK_AND_PLACE
        planner_kwargs = {"place": place}
        if continuation_start_q is not None and self._planner_supports_start_arm_q():
            planner_kwargs["start_arm_q"] = continuation_start_q
            self._planner_start_arm_qs.append(continuation_start_q.tolist())
            self._inter_action_transitions.append({
                "transition": "previous_release_to_next_home",
                "teleport": False,
                "safeWaitPose": "vertical_retreat_after_previous_action",
                "retreatPerformed": retreat_performed,
                "startArmQ": continuation_start_q.tolist(),
            })
        elif self._persistent_world and self._planner_supports_start_arm_q():
            qpos = getattr(self._data, "qpos", None)
            if qpos is not None and len(qpos) >= 7:
                self._planner_start_arm_qs.append(
                    np.asarray(qpos[:7], dtype=float).tolist()
                )
        target_position = None
        simulator_object_name = simulator_object.simulator_object_name
        if self._before_execute_callback is not None:
            self._before_execute_callback(
                self._mujoco, self._model, self._data, request, simulator_object
            )
        if place and self._persistent_world:
            build_slot_index = request.selection.build_slot_index
            if build_slot_index is None and self._placement_target_resolver is None:
                return BackendExecutionOutcome(
                    success=False,
                    failure_reason="persistent context execution requires buildSlotIndex",
                    execution_provenance="fr3_umi_mujoco:persistent_world_contract",
                )
            if build_slot_index is not None:
                if build_slot_index in self._slot_to_simulator_object:
                    if self._slot_to_simulator_object[build_slot_index] != simulator_object_name:
                        raise RuntimeError("build slot is already bound to another simulator object")
                self._slot_to_simulator_object[build_slot_index] = simulator_object_name
            if self._placement_target_resolver is None:
                target_position = self._placement_targets[build_slot_index]
            else:
                # A caller can resolve a fixed world target from the immutable
                # logical object identity. M19 has no build-slot placement
                # field; it must not synthesize one from EEG/visual slot order.
                target_position = self._placement_target_resolver(
                    build_slot_index,
                    self._model,
                    self._data,
                    simulator_object,
                    dict(self._slot_to_simulator_object),
                )
                if target_position is None or len(target_position) != 3:
                    raise RuntimeError("placement target resolver returned an invalid target")
                target_position = tuple(float(value) for value in target_position)
            planner_kwargs.update(
                place_target_position=target_position,
                place_center=target_position[:2],
                place_region_center=target_position[:2],
                place_region_half_size=0.045,
                place_region_z_tolerance=0.06,
                # The four-cube fixture is an explicit tabletop placement,
                # not the legacy deep-drop box.  Releasing close to the
                # requested cube center avoids a long free fall before the
                # planner's terminal verification.
                drop_clear=0.03,
            )
        try:
            planner = self._planner_class(
                self._model,
                self._data,
                simulator_object.simulator_object_name,
                **planner_kwargs,
            )
            if self._persistent_world:
                self._persistent_execution_started = True
            self._last_planner = planner
            if (
                self._simulation_step_callback is None
                and self._post_release_callback is None
                and self._post_release_settle_steps == 0
                and not self._capture_trajectory
            ):
                completed = planner.run_headless(max_steps=self._max_steps, quiet=True)
            else:
                completed = self._run_planner_headless_with_callback(
                    planner, simulator_object, target_position
                )
            if completed is not None:
                planner = completed
            summary = planner.summary()
            if not isinstance(summary, Mapping):
                raise RuntimeError("existing FR3/UMI planner returned no result mapping")
            baseline_result = summary.get("result")
            if not isinstance(baseline_result, str) or not baseline_result:
                raise RuntimeError("existing FR3/UMI planner omitted its result enum")

            expected_result = "place_ok" if place else "lift_ok"
            success = baseline_result == expected_result
            detail_keys = ("up", "moved", "contacts", "grabbed", "in_box")
            details = ", ".join(
                "{}={!r}".format(key, summary[key])
                for key in detail_keys
                if key in summary
            )
            failure_reason = None
            if not success:
                failure_reason = "existing baseline reported {!r}; expected {!r}".format(
                    baseline_result, expected_result
                )
                if details:
                    failure_reason += " ({})".format(details)
            if success and target_position is not None:
                body_id = self._mujoco.mj_name2id(
                    self._model,
                    self._mujoco.mjtObj.mjOBJ_BODY,
                    simulator_object.simulator_object_name,
                )
                position = [float(item) for item in self._data.xpos[body_id]]
                self._placement_records.append({
                    "buildSlotIndex": request.selection.build_slot_index,
                    "simulatorObject": simulator_object.simulator_object_name,
                    "targetPosition": [float(item) for item in target_position],
                    "finalPosition": position,
                    "result": baseline_result,
                })

            provenance = "fr3_umi_mujoco@{}:GripperGraspPlanner:{}".format(
                FR3_UMI_BASELINE_SNAPSHOT, baseline_result
            )
            return BackendExecutionOutcome(
                success=success,
                failure_reason=failure_reason,
                execution_provenance=provenance,
                backend_execution_id=uuid4().hex,
            )
        finally:
            self._restore_baseline_model_parameters()

    def world_state_evidence(self):
        objects = []
        final_linear_speeds = {}
        for object_name in ("obj_0", "obj_1", "obj_2", "obj_3"):
            try:
                body_id = self._mujoco.mj_name2id(
                    self._model, self._mujoco.mjtObj.mjOBJ_BODY, object_name
                )
            except Exception:
                continue
            if body_id < 0:
                continue
            objects.append({
                "simulatorObject": object_name,
                "position": [float(item) for item in self._data.xpos[body_id]],
            })
            cvel = getattr(self._data, "cvel", None)
            if cvel is not None:
                final_linear_speeds[object_name] = float(
                    np.linalg.norm(np.asarray(cvel[body_id][3:], dtype=float))
                )
        return {
            "persistentWorld": self._persistent_world,
            "wholeSceneResetPerRequest": not self._persistent_world,
            "persistentStateResetCount": self._persistent_reset_count,
            "continuousRobotMotion": self._persistent_world,
            "plannerStartArmQ": list(self._planner_start_arm_qs),
            "interActionTransitions": list(self._inter_action_transitions),
            "placements": list(self._placement_records),
            "objects": objects,
            "buildSlotTargets": [list(target) for target in self._placement_targets],
            "trajectoryEvidence": list(self._trajectory_records),
            "slotToSimulatorObject": dict(self._slot_to_simulator_object),
            "finalLinearSpeedsMetersPerSecond": final_linear_speeds,
        }


def create_fr3_umi_mujoco_adapter(
    scene_bindings=None,
    robot_arm_root=None,
    mujoco_module=None,
    scene_builder=None,
    planner_class=None,
    max_steps=9000,
    persistent_world=False,
    context_demo_scene=False,
    placement_targets=None,
    m13_6_visual_scene=False,
    placement_target_resolver=None,
    before_execute_callback=None,
    post_release_callback=None,
    post_release_settle_steps=0,
    capture_trajectory=False,
):
    """Load the copied baseline and wire its real BODY lookup and headless planner.

    Optional dependencies are injectable for software tests. Normal callers
    load MuJoCo and the baseline modules from this repository's robot_arm/.
    """
    if scene_bindings is None:
        scene_bindings = SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS)
    if not isinstance(scene_bindings, SceneBindingRegistry):
        raise TypeError("scene_bindings must be a SceneBindingRegistry")

    if mujoco_module is None:
        try:
            mujoco_module = importlib.import_module("mujoco")
        except ImportError as error:
            raise RuntimeError(
                "MuJoCo is not installed in the active Python environment"
            ) from error

    if scene_builder is None or planner_class is None:
        source_root = (
            Path(robot_arm_root)
            if robot_arm_root is not None
            else Path(__file__).resolve().parents[1] / "robot_arm"
        ).resolve()
        if not (source_root / "assets" / "fr3_umi_merged.xml").is_file():
            raise FileNotFoundError(
                "FR3/UMI baseline model is missing under {}".format(source_root)
            )
        for package_name in ("utils", "control"):
            loaded = sys.modules.get(package_name)
            if loaded is not None:
                module_path = getattr(loaded, "__file__", None)
                if module_path is None or source_root / package_name not in Path(
                    module_path
                ).resolve().parents:
                    raise RuntimeError(
                        "cannot safely load FR3/UMI baseline: top-level package "
                        "{!r} is already loaded from another location".format(
                            package_name
                        )
                    )
        source_root_string = str(source_root)
        if source_root_string not in sys.path:
            sys.path.insert(0, source_root_string)
        scene_module = importlib.import_module("utils.gripper_scene")
        planner_module = importlib.import_module("control.gripper_planner")
        if scene_builder is None:
            scene_builder = (
                scene_module.build_m13_6_visual_scene
                if m13_6_visual_scene
                else (
                    scene_module.build_context_aware_demo_scene
                    if context_demo_scene
                    else scene_module.build_gripper_scene
                )
            )
        if planner_class is None:
            planner_class = planner_module.GripperGraspPlanner
        if placement_targets is None and context_demo_scene:
            placement_targets = scene_module.CONTEXT_DEMO_BUILD_SLOT_TARGETS

    model, data = scene_builder()
    mujoco_module.mj_forward(model, data)
    backend = ExistingFr3UmiPickPlaceBackend(
        mujoco_module,
        model,
        data,
        planner_class,
        max_steps=max_steps,
        persistent_world=persistent_world,
        placement_targets=placement_targets,
        placement_target_resolver=placement_target_resolver,
        before_execute_callback=before_execute_callback,
        post_release_callback=post_release_callback,
        post_release_settle_steps=post_release_settle_steps,
        capture_trajectory=capture_trajectory,
    )
    return MujocoRobotExecutionAdapter(
        scene_bindings=scene_bindings,
        name_lookup=backend.name_lookup,
        backend=backend,
    )


class MujocoRobotExecutionAdapter:
    """Resolve a logical block and translate one backend outcome to M9 feedback."""

    def __init__(self, scene_bindings, name_lookup, backend):
        if not isinstance(scene_bindings, SceneBindingRegistry):
            raise TypeError("scene_bindings must be a SceneBindingRegistry")
        if not callable(name_lookup):
            raise TypeError("name_lookup must be callable")
        if not callable(getattr(backend, "execute", None)):
            raise TypeError("backend must provide execute(request, simulator_object)")
        self._scene_bindings = scene_bindings
        self._name_lookup = name_lookup
        self._backend = backend

    def world_state_evidence(self):
        evidence = getattr(self._backend, "world_state_evidence", None)
        if not callable(evidence):
            return None
        raw = dict(evidence())
        logical_objects = []
        for logical_id, binding in self._scene_bindings._by_logical_id.items():
            for item in raw.get("objects", ()):
                if item.get("simulatorObject") == binding.simulator_object_name:
                    logical_objects.append(dict(item, logicalBlockId=logical_id))
                    break
        raw["objects"] = logical_objects
        by_object = {item["simulatorObject"]: item for item in logical_objects}
        slot_evidence = []
        for placement in raw.get("placements", ()):
            final_position = placement.get("finalPosition")
            target_position = placement.get("targetPosition")
            object_record = by_object.get(placement.get("simulatorObject"))
            if not isinstance(final_position, list) or not isinstance(target_position, list):
                continue
            horizontal_error = math.hypot(
                float(final_position[0]) - float(target_position[0]),
                float(final_position[1]) - float(target_position[1]),
            )
            vertical_error = abs(float(final_position[2]) - float(target_position[2]))
            slot_evidence.append({
                "buildSlotIndex": placement["buildSlotIndex"],
                "logicalBlockId": object_record.get("logicalBlockId") if object_record else None,
                "targetPosition": list(target_position),
                "finalPosition": list(final_position),
                "horizontalErrorMeters": horizontal_error,
                "verticalErrorMeters": vertical_error,
                "horizontalToleranceMeters": 0.12,
                "verticalToleranceMeters": 0.08,
                "withinPlacementTolerance": horizontal_error <= 0.12 and vertical_error <= 0.08,
            })
        slot_evidence.sort(key=lambda item: item["buildSlotIndex"])
        raw["slotEvidence"] = slot_evidence
        raw["allFourObjectsPresent"] = len(logical_objects) == 4
        raw["allSlotsExecutedOnce"] = [
            item["buildSlotIndex"] for item in slot_evidence
        ] == [0, 1, 2, 3]
        raw["allSlotsWithinPlacementTolerance"] = bool(slot_evidence) and all(
            item["withinPlacementTolerance"] for item in slot_evidence
        )
        raw["upperTopologyEvidence"] = {
            "upperSlots": [2, 3],
            "bottomSlots": [0, 1],
            "upperTargetsAboveBottomTargets": (
                len(raw.get("buildSlotTargets", ())) == 4 and
                raw["buildSlotTargets"][2][2] > raw["buildSlotTargets"][0][2] and
                raw["buildSlotTargets"][3][2] > raw["buildSlotTargets"][1][2]
            ),
        }

        # Optional strict mechanical evidence used by the M13.10 Showcase.
        # This is deliberately separate from the historical broad placement
        # tolerance above, so older M9/M13 acceptance callers keep their
        # existing contract while Showcase evidence cannot hide a dropped
        # upper block.
        trajectory = list(raw.get("trajectoryEvidence", ()))
        required_phases = ("pre_grasp", "grasp", "carry", "place", "release")
        trajectory_by_object = {}
        for record in trajectory:
            trajectory_by_object.setdefault(record.get("simulatorObject"), []).append(record)
        bound_objects = dict(raw.get("slotToSimulatorObject", {}))
        action_phase_evidence = []
        for simulator_object in bound_objects.values():
            records = trajectory_by_object.get(simulator_object, [])
            phase_by_name = {record.get("phase"): record for record in records}
            action_phase_evidence.append({
                "simulatorObject": simulator_object,
                "phases": [phase for phase in required_phases if phase in phase_by_name],
                "allRequiredPhases": all(phase in phase_by_name for phase in required_phases),
                "graspAlignment": (
                    phase_by_name.get("grasp", {}).get("graspAlignmentWithinSizeTolerance") is True
                ),
            })
        all_trajectory_phases = bool(action_phase_evidence) and all(
            item["allRequiredPhases"] for item in action_phase_evidence
        )
        all_grasps_aligned = bool(action_phase_evidence) and all(
            item["graspAlignment"] for item in action_phase_evidence
        )
        final_by_object = {
            item.get("simulatorObject"): item
            for item in logical_objects
            if isinstance(item.get("position"), list)
        }
        half_extents_by_object = {}
        for simulator_object, records in trajectory_by_object.items():
            for record in records:
                values = record.get("blockHalfExtentsMujoco")
                if isinstance(values, list) and len(values) == 3:
                    half_extents_by_object[simulator_object] = tuple(float(value) for value in values)
                    break
        table_bounds = next(
            (record.get("tableBoundsMujoco") for record in trajectory if record.get("tableBoundsMujoco")),
            None,
        )
        all_final_objects_inside_table = bool(table_bounds) and all(
            object_name in half_extents_by_object
            and object_name in final_by_object
            and table_bounds["x"][0] + half_extents_by_object[object_name][0]
            <= final_by_object[object_name]["position"][0]
            <= table_bounds["x"][1] - half_extents_by_object[object_name][0]
            and table_bounds["y"][0] + half_extents_by_object[object_name][1]
            <= final_by_object[object_name]["position"][1]
            <= table_bounds["y"][1] - half_extents_by_object[object_name][1]
            for object_name in bound_objects.values()
        )
        stack_evidence = []
        for upper_slot in (2, 3):
            target_object = bound_objects.get(upper_slot)
            support_object = bound_objects.get(upper_slot - 2)
            target_record = final_by_object.get(target_object)
            support_record = final_by_object.get(support_object)
            target_half = half_extents_by_object.get(target_object)
            support_half = half_extents_by_object.get(support_object)
            if not all((target_record, support_record, target_half, support_half)):
                continue
            target_position = target_record["position"]
            support_position = support_record["position"]
            horizontal_error = math.hypot(
                float(target_position[0]) - float(support_position[0]),
                float(target_position[1]) - float(support_position[1]),
            )
            expected_z = float(support_position[2] + support_half[2] + target_half[2])
            vertical_gap = float(target_position[2] - expected_z)
            horizontal_tolerance = min(
                support_half[0], support_half[1], target_half[0], target_half[1]
            )
            vertical_tolerance = 0.5 * min(support_half[2], target_half[2])
            stack_evidence.append({
                "upperSlot": upper_slot,
                "targetObject": target_object,
                "supportObject": support_object,
                "horizontalErrorMeters": horizontal_error,
                "horizontalToleranceMeters": horizontal_tolerance,
                "verticalGapMeters": vertical_gap,
                "verticalToleranceMeters": vertical_tolerance,
                "stableSupport": (
                    horizontal_error <= horizontal_tolerance
                    and abs(vertical_gap) <= vertical_tolerance
                ),
            })
        all_stack_supports_stable = len(stack_evidence) == 2 and all(
            item["stableSupport"] for item in stack_evidence
        )
        final_speeds = raw.get("finalLinearSpeedsMetersPerSecond", {})
        max_final_speed = max((float(value) for value in final_speeds.values()), default=0.0)
        raw["finalStackEvidence"] = stack_evidence
        raw["mechanicalSanity"] = {
            "requiredTrajectoryPhases": list(required_phases),
            "actions": action_phase_evidence,
            "allActionsHaveRequiredTrajectoryPhases": all_trajectory_phases,
            "allGraspsAlignedWithinGeometryTolerance": all_grasps_aligned,
            "allFinalObjectsInsideTableBounds": all_final_objects_inside_table,
            "allUpperSupportsStable": all_stack_supports_stable,
            "maxFinalObjectLinearSpeedMetersPerSecond": max_final_speed,
            "allFinalObjectsSettled": max_final_speed <= 0.05,
            "pass": (
                all_trajectory_phases
                and all_grasps_aligned
                and all_final_objects_inside_table
                and all_stack_supports_stable
                and max_final_speed <= 0.05
            ),
        }
        return raw

    def execute(self, request):
        if not isinstance(request, RobotExecutionRequest):
            raise TypeError("request must be a RobotExecutionRequest")
        started_utc = _utc_now()
        try:
            simulator_object = self._scene_bindings.resolve_scene_object(
                request.logical_block_id, self._name_lookup
            )
        except InvalidLogicalBlockIdError as error:
            return self._failure(
                request,
                "invalid_logical_block_id",
                "logical block ID is invalid",
                started_utc,
            )
        except UnknownLogicalBlockError as error:
            return self._failure(request, "unknown_logical_block", str(error), started_utc)
        except MissingSimulatorObjectError:
            return self._failure(
                request,
                "missing_simulator_object",
                "configured MuJoCo body is absent from the loaded scene",
                started_utc,
            )
        except SceneLookupError:
            return self._failure(
                request,
                "scene_lookup_failed",
                "MuJoCo body lookup failed",
                started_utc,
            )

        try:
            outcome = self._backend.execute(request, simulator_object)
        except Exception as error:
            return self._failure(
                request,
                "backend_execution_error",
                "existing FR3/UMI baseline raised {}".format(type(error).__name__),
                started_utc,
                execution_provenance="adapter:backend_exception",
            )

        if not isinstance(outcome, BackendExecutionOutcome):
            return self._failure(
                request,
                "invalid_backend_result",
                "backend must return BackendExecutionOutcome",
                started_utc,
                execution_provenance="adapter:invalid_backend_result",
            )
        if not outcome.success:
            return self._failure(
                request,
                "pick_place_failed",
                self._public_failure_reason(outcome.failure_reason),
                started_utc,
                execution_provenance=outcome.execution_provenance,
                backend_execution_id=outcome.backend_execution_id,
            )
        return self._result(
            request=request,
            success=True,
            failure_code=None,
            failure_reason=None,
            started_utc=started_utc,
            execution_provenance=outcome.execution_provenance,
            backend_execution_id=outcome.backend_execution_id,
        )

    def _public_failure_reason(self, failure_reason):
        reason = _single_line_error(failure_reason)
        for binding in self._scene_bindings._by_logical_id.values():
            reason = reason.replace(binding.simulator_object_name, "simulator object")
        return reason

    def _failure(
        self,
        request,
        failure_code,
        failure_reason,
        started_utc,
        execution_provenance="adapter:scene_binding",
        backend_execution_id=None,
    ):
        return self._result(
            request=request,
            success=False,
            failure_code=failure_code,
            failure_reason=failure_reason,
            started_utc=started_utc,
            execution_provenance=execution_provenance,
            backend_execution_id=backend_execution_id,
        )

    @staticmethod
    def _result(
        request,
        success,
        failure_code,
        failure_reason,
        started_utc,
        execution_provenance,
        backend_execution_id=None,
    ):
        selection = request.selection
        return RobotExecutionResult(
            request_id=request.request_id,
            logical_block_id=request.logical_block_id,
            operation=request.operation,
            success=success,
            failure_code=failure_code,
            failure_reason=failure_reason,
            source_batch_id=selection.batch_id,
            source_selection_id=selection.selection_id,
            source_target_id=selection.source_target_id,
            batch_provenance=selection.batch_provenance,
            selection_provenance=selection.selection_provenance,
            backend_execution_id=backend_execution_id,
            execution_provenance=execution_provenance,
            started_utc=started_utc,
            completed_utc=_utc_now(),
        )
