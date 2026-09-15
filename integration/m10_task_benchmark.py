"""Deterministic, pure-software task progression for the M10 benchmark prep."""

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Iterable, Optional, Tuple


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "m10" / "task_definitions.json"
BENCHMARK_LOGICAL_BLOCK_IDS = (
    "block_sim_01",
    "block_sim_02",
    "block_sim_03",
    "block_sim_04",
)


class TaskStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INVALID = "invalid"


class EpisodeOutcome(str, Enum):
    """Task outcome, separate from whether a benchmark case met its expectation."""

    COMPLETED = "completed"
    VALID_INCOMPLETE = "valid_incomplete"
    INVALID = "invalid"


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    task_name: str
    ordered_logical_block_ids: Tuple[str, ...]

    def __post_init__(self):
        object.__setattr__(
            self, "ordered_logical_block_ids", tuple(self.ordered_logical_block_ids)
        )
        if not self.task_id or not self.task_name:
            raise ValueError("task_id and task_name are required")
        if not self.ordered_logical_block_ids:
            raise ValueError("a task must contain at least one logical block")
        if any(block_id not in BENCHMARK_LOGICAL_BLOCK_IDS for block_id in self.ordered_logical_block_ids):
            raise ValueError("task sequences must use the four frozen logical block IDs")
        if len(set(self.ordered_logical_block_ids)) != len(self.ordered_logical_block_ids):
            raise ValueError("a task sequence cannot repeat a logical block ID")

    def to_public_dict(self):
        return {
            "taskId": self.task_id,
            "taskName": self.task_name,
            "orderedLogicalBlockIds": list(self.ordered_logical_block_ids),
        }


@dataclass(frozen=True)
class TaskState:
    task_id: str
    current_step: int
    completed_sequence: Tuple[str, ...]
    remaining_sequence: Tuple[str, ...]
    status: TaskStatus
    invalid_reason_code: Optional[str] = None

    def to_public_dict(self):
        return {
            "taskId": self.task_id,
            "currentStep": self.current_step,
            "completedSequence": list(self.completed_sequence),
            "remainingSequence": list(self.remaining_sequence),
            "status": self.status.value,
            "invalidReasonCode": self.invalid_reason_code,
        }


@dataclass(frozen=True)
class TaskTransitionResult:
    accepted: bool
    step_index: int
    logical_block_id: Optional[str]
    reason_code: Optional[str]
    state: TaskState

    def to_public_dict(self):
        return {
            "accepted": self.accepted,
            "stepIndex": self.step_index,
            "logicalBlockId": self.logical_block_id,
            "reasonCode": self.reason_code,
            "state": self.state.to_public_dict(),
        }


@dataclass(frozen=True)
class TaskContext:
    task_id: str
    step_index: int
    completed_logical_block_ids: Tuple[str, ...]
    remaining_logical_block_ids: Tuple[str, ...]
    valid_next_logical_block_ids: Tuple[str, ...]

    def to_public_dict(self):
        return {
            "taskId": self.task_id,
            "stepIndex": self.step_index,
            "completedLogicalBlockIds": list(self.completed_logical_block_ids),
            "remainingLogicalBlockIds": list(self.remaining_logical_block_ids),
            "validNextLogicalBlockIds": list(self.valid_next_logical_block_ids),
        }


def load_benchmark_fixture(path=FIXTURE_PATH):
    """Load the checked-in deterministic JSON task and episode definitions."""
    with Path(path).open("r", encoding="utf-8") as source:
        fixture = json.load(source)
    if not isinstance(fixture, dict) or fixture.get("schemaVersion") != 1:
        raise ValueError("M10 task fixture schemaVersion must be 1")
    if tuple(fixture.get("logicalBlockIds", ())) != BENCHMARK_LOGICAL_BLOCK_IDS:
        raise ValueError("M10 logical block ID fixture does not match the frozen contract")
    return fixture


def load_task_definitions(path=FIXTURE_PATH):
    fixture = load_benchmark_fixture(path)
    task_records = fixture.get("tasks")
    if not isinstance(task_records, list) or not task_records:
        raise ValueError("M10 fixture must contain task definitions")
    definitions = {}
    for record in task_records:
        definition = TaskDefinition(
            task_id=record["taskId"],
            task_name=record["taskName"],
            ordered_logical_block_ids=tuple(record["orderedLogicalBlockIds"]),
        )
        if definition.task_id in definitions:
            raise ValueError("duplicate M10 taskId: {!r}".format(definition.task_id))
        definitions[definition.task_id] = definition
    return definitions


TASK_DEFINITIONS = load_task_definitions()


def initial_state(definition):
    if not isinstance(definition, TaskDefinition):
        raise TypeError("definition must be a TaskDefinition")
    return TaskState(
        task_id=definition.task_id,
        current_step=0,
        completed_sequence=(),
        remaining_sequence=definition.ordered_logical_block_ids,
        status=TaskStatus.NOT_STARTED,
    )


def reset_task(definition):
    """Return the deterministic initial state, regardless of prior episode state."""
    return initial_state(definition)


def episode_outcome(state):
    """Return the task's final outcome without treating it as an acceptance result."""
    if not isinstance(state, TaskState):
        raise TypeError("state must be a TaskState")
    if state.status is TaskStatus.COMPLETED:
        return EpisodeOutcome.COMPLETED
    if state.status is TaskStatus.INVALID:
        return EpisodeOutcome.INVALID
    return EpisodeOutcome.VALID_INCOMPLETE


def apply_selection(definition, state, logical_block_id):
    """Apply one exact logical block selection without inference or side effects."""
    _validate_state_for_definition(definition, state)
    step_index = state.current_step
    if state.status is TaskStatus.INVALID:
        return TaskTransitionResult(
            False, step_index, logical_block_id, "task_already_invalid", state
        )
    if state.status is TaskStatus.COMPLETED:
        return TaskTransitionResult(
            False, step_index, logical_block_id, "task_already_completed", state
        )

    if logical_block_id not in BENCHMARK_LOGICAL_BLOCK_IDS:
        invalid_state = TaskState(
            state.task_id,
            state.current_step,
            state.completed_sequence,
            state.remaining_sequence,
            TaskStatus.INVALID,
            "unknown_logical_block_id",
        )
        return TaskTransitionResult(
            False, step_index, logical_block_id, "unknown_logical_block_id", invalid_state
        )

    expected_block_id = state.remaining_sequence[0]
    if logical_block_id != expected_block_id:
        invalid_state = TaskState(
            state.task_id,
            state.current_step,
            state.completed_sequence,
            state.remaining_sequence,
            TaskStatus.INVALID,
            "wrong_order",
        )
        return TaskTransitionResult(
            False, step_index, logical_block_id, "wrong_order", invalid_state
        )

    completed = state.completed_sequence + (logical_block_id,)
    remaining = state.remaining_sequence[1:]
    next_status = TaskStatus.COMPLETED if not remaining else TaskStatus.IN_PROGRESS
    next_state = TaskState(
        task_id=state.task_id,
        current_step=state.current_step + 1,
        completed_sequence=completed,
        remaining_sequence=remaining,
        status=next_status,
    )
    return TaskTransitionResult(True, step_index, logical_block_id, None, next_state)


def task_context(definition, state):
    """Build deterministic M11-facing context without any scores or predictions."""
    _validate_state_for_definition(definition, state)
    valid_next = (
        (state.remaining_sequence[0],)
        if state.status in (TaskStatus.NOT_STARTED, TaskStatus.IN_PROGRESS)
        and state.remaining_sequence
        else ()
    )
    return TaskContext(
        task_id=state.task_id,
        step_index=state.current_step,
        completed_logical_block_ids=state.completed_sequence,
        remaining_logical_block_ids=state.remaining_sequence,
        valid_next_logical_block_ids=valid_next,
    )


def replay_sequence(definition, logical_block_ids: Iterable[str], state=None):
    """Replay a deterministic episode, stopping after its first rejected step."""
    current_state = initial_state(definition) if state is None else state
    _validate_state_for_definition(definition, current_state)
    transitions = []
    for logical_block_id in logical_block_ids:
        result = apply_selection(definition, current_state, logical_block_id)
        transitions.append(result)
        current_state = result.state
        if not result.accepted:
            break
    return tuple(transitions), current_state


def _validate_state_for_definition(definition, state):
    if not isinstance(definition, TaskDefinition):
        raise TypeError("definition must be a TaskDefinition")
    if not isinstance(state, TaskState):
        raise TypeError("state must be a TaskState")
    if state.task_id != definition.task_id:
        raise ValueError("task state belongs to a different task definition")
    if state.current_step != len(state.completed_sequence):
        raise ValueError("task state current_step must equal completed sequence length")
    if state.completed_sequence + state.remaining_sequence != definition.ordered_logical_block_ids:
        raise ValueError("task state sequences do not match the task definition")
