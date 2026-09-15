"""M11 context-only next-target prediction baseline.

The predictor receives only the observable completed history, the observable
candidate catalogue, and its derived step index.  Hidden task identity and
M10 evaluator state deliberately do not cross this module boundary.
"""

from dataclasses import dataclass
import math
from typing import Iterable, Optional, Tuple

from integration.m10_task_benchmark import (
    BENCHMARK_LOGICAL_BLOCK_IDS,
    load_task_definitions,
)


_TASK_DEFINITIONS = load_task_definitions()
_TASK_ORDER = tuple(_TASK_DEFINITIONS)
_TOLERANCE = 1e-12


@dataclass(frozen=True)
class ContextObservation:
    """The legal M11 observable input contract."""

    completed_logical_block_history: Tuple[str, ...]
    available_logical_block_ids: Tuple[str, ...]
    step_index: int

    @classmethod
    def from_observable(
        cls,
        completed_logical_block_history: Iterable[str],
        available_logical_block_ids: Iterable[str],
        step_index: Optional[int] = None,
    ):
        history = tuple(completed_logical_block_history)
        available = tuple(available_logical_block_ids)
        if any(not isinstance(item, str) for item in history + available):
            raise ValueError("observable logical block IDs must be strings")
        if len(set(available)) != len(available):
            raise ValueError("availableLogicalBlockIds must not contain duplicates")
        if set(available) != set(BENCHMARK_LOGICAL_BLOCK_IDS):
            raise ValueError(
                "availableLogicalBlockIds must describe the frozen four-block catalogue"
            )
        derived_step = len(history)
        if step_index is not None and step_index != derived_step:
            raise ValueError("stepIndex must equal the derived completed-history length")
        return cls(history, available, derived_step)


@dataclass(frozen=True)
class TaskHypothesis:
    task_id: str
    probability: float

    def to_public_dict(self):
        return {"taskId": self.task_id, "probability": self.probability}


@dataclass(frozen=True)
class ContextPrior:
    """M11 output; no hidden ground-truth task or evaluator oracle is included."""

    observable_history: Tuple[str, ...]
    available_logical_block_ids: Tuple[str, ...]
    step_index: int
    candidate_task_count: int
    task_hypotheses: Tuple[TaskHypothesis, ...]
    next_target_probabilities: Tuple[Tuple[str, float], ...]
    top_targets: Tuple[str, ...]
    tie: bool
    entropy: Optional[float]
    terminal: bool
    valid: bool
    invalid_reason: Optional[str] = None

    def probability_map(self):
        return dict(self.next_target_probabilities)

    def to_public_dict(self):
        result = {
            "observableHistory": list(self.observable_history),
            "availableLogicalBlockIds": list(self.available_logical_block_ids),
            "stepIndex": self.step_index,
            "candidateTaskCount": self.candidate_task_count,
            "taskHypotheses": [item.to_public_dict() for item in self.task_hypotheses],
            "nextTargetProbabilities": dict(self.next_target_probabilities),
            "topTargets": list(self.top_targets),
            "tie": self.tie,
            "entropy": self.entropy,
            "terminal": self.terminal,
            "valid": self.valid,
        }
        if self.invalid_reason is not None:
            result["invalidReason"] = self.invalid_reason
        return result


def _invalid(observation, reason):
    return ContextPrior(
        observable_history=observation.completed_logical_block_history,
        available_logical_block_ids=observation.available_logical_block_ids,
        step_index=observation.step_index,
        candidate_task_count=0,
        task_hypotheses=(),
        next_target_probabilities=(),
        top_targets=(),
        tie=False,
        entropy=None,
        terminal=False,
        valid=False,
        invalid_reason=reason,
    )


def _hypotheses_for_history(history):
    if len(history) > len(BENCHMARK_LOGICAL_BLOCK_IDS):
        return ()
    if len(set(history)) != len(history):
        return ()
    if any(item not in BENCHMARK_LOGICAL_BLOCK_IDS for item in history):
        return ()
    return tuple(
        definition
        for task_id in _TASK_ORDER
        for definition in (_TASK_DEFINITIONS[task_id],)
        if history == definition.ordered_logical_block_ids[: len(history)]
    )


def predict_context_prior(observation: ContextObservation) -> ContextPrior:
    """Apply the frozen uniform task-hypothesis aggregation rule."""
    if not isinstance(observation, ContextObservation):
        raise TypeError("observation must be a ContextObservation")
    hypotheses = _hypotheses_for_history(observation.completed_logical_block_history)
    if not hypotheses:
        return _invalid(observation, "no_consistent_task_hypothesis")

    if observation.step_index == len(BENCHMARK_LOGICAL_BLOCK_IDS):
        return ContextPrior(
            observable_history=observation.completed_logical_block_history,
            available_logical_block_ids=observation.available_logical_block_ids,
            step_index=observation.step_index,
            candidate_task_count=len(hypotheses),
            task_hypotheses=tuple(
                TaskHypothesis(item.task_id, 1.0 / len(hypotheses)) for item in hypotheses
            ),
            next_target_probabilities=(),
            top_targets=(),
            tie=False,
            entropy=0.0,
            terminal=True,
            valid=True,
        )

    task_probability = 1.0 / len(hypotheses)
    next_mass = {}
    for definition in hypotheses:
        next_id = definition.ordered_logical_block_ids[observation.step_index]
        next_mass[next_id] = next_mass.get(next_id, 0.0) + task_probability
    ordered_probabilities = tuple(
        (block_id, next_mass[block_id])
        for block_id in BENCHMARK_LOGICAL_BLOCK_IDS
        if block_id in next_mass
    )
    maximum = max(probability for _block_id, probability in ordered_probabilities)
    top_targets = tuple(
        block_id
        for block_id, probability in ordered_probabilities
        if math.isclose(probability, maximum, rel_tol=0.0, abs_tol=_TOLERANCE)
    )
    entropy = -sum(
        probability * math.log(probability)
        for _block_id, probability in ordered_probabilities
        if probability > 0.0
    )
    return ContextPrior(
        observable_history=observation.completed_logical_block_history,
        available_logical_block_ids=observation.available_logical_block_ids,
        step_index=observation.step_index,
        candidate_task_count=len(hypotheses),
        task_hypotheses=tuple(
            TaskHypothesis(item.task_id, task_probability) for item in hypotheses
        ),
        next_target_probabilities=ordered_probabilities,
        top_targets=top_targets,
        tie=len(top_targets) > 1,
        entropy=entropy,
        terminal=False,
        valid=True,
    )


def make_observation(history, available=None, step_index=None):
    """Convenience constructor used by replay and tests."""
    if available is None:
        available = BENCHMARK_LOGICAL_BLOCK_IDS
    return ContextObservation.from_observable(history, available, step_index)


__all__ = [
    "ContextObservation",
    "ContextPrior",
    "TaskHypothesis",
    "make_observation",
    "predict_context_prior",
]
