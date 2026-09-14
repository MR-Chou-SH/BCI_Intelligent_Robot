"""M9 scene binding and execution-result seam for a reused MuJoCo baseline.

This module owns the boundary between stable logical block IDs and simulator
objects. It does not implement robot planning or control. A thin backend must
adapt the existing FR3/UMI Pick/Place entry point to PickPlaceBackend after its
actual API has been inspected.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import importlib
from operator import index as integer_index
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Optional, Protocol
from uuid import uuid4

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

    def __init__(self, mujoco_module, model, data, planner_class, max_steps=9000):
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
        self.name_lookup = make_mujoco_name_lookup(
            mujoco_module, model, body_type
        )

    def execute(self, request, simulator_object):
        expected_id = self.name_lookup(simulator_object.simulator_object_name)
        if expected_id < 0 or expected_id != simulator_object.simulator_object_id:
            raise RuntimeError(
                "resolved simulator object no longer matches the loaded scene"
            )

        # The existing scene is deterministic. Reset its state so each
        # confirmed selection starts from the same tabletop arrangement.
        self._mujoco.mj_resetData(self._model, self._data)
        self._mujoco.mj_forward(self._model, self._data)
        place = request.operation is RobotOperation.PICK_AND_PLACE
        planner = self._planner_class(
            self._model,
            self._data,
            simulator_object.simulator_object_name,
            place=place,
        )
        completed = planner.run_headless(max_steps=self._max_steps, quiet=True)
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

        provenance = "fr3_umi_mujoco@{}:GripperGraspPlanner:{}".format(
            FR3_UMI_BASELINE_SNAPSHOT, baseline_result
        )
        return BackendExecutionOutcome(
            success=success,
            failure_reason=failure_reason,
            execution_provenance=provenance,
            backend_execution_id=uuid4().hex,
        )


def create_fr3_umi_mujoco_adapter(
    scene_bindings=None,
    robot_arm_root=None,
    mujoco_module=None,
    scene_builder=None,
    planner_class=None,
    max_steps=9000,
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
            scene_builder = scene_module.build_gripper_scene
        if planner_class is None:
            planner_class = planner_module.GripperGraspPlanner

    model, data = scene_builder()
    mujoco_module.mj_forward(model, data)
    backend = ExistingFr3UmiPickPlaceBackend(
        mujoco_module, model, data, planner_class, max_steps=max_steps
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
