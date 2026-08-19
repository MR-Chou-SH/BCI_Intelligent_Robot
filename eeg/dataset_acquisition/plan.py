"""Deterministic, balanced M6.1b ground-truth trial plans."""

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path


TARGETS = {
    "target_left": {"targetSide": "left", "nominalFrequencyHz": 7.2},
    "target_center": {"targetSide": "center", "nominalFrequencyHz": 9.0},
    "target_right": {"targetSide": "right", "nominalFrequencyHz": 12.0},
}
GENERATOR_VERSION = "m6_1b_balanced_shuffle_v1"


@dataclass(frozen=True)
class TrialPlanItem:
    sessionId: str
    trialId: str
    trialIndex: int
    targetId: str
    targetSide: str
    nominalFrequencyHz: float
    randomSeed: int
    plannedOrder: tuple[str, ...]
    trialStatus: str = "planned"
    expectedStimulusDurationSeconds: float = 4.0

    def to_dict(self):
        value = asdict(self)
        value["plannedOrder"] = list(value["plannedOrder"])
        return value


def _balanced_order(seed, per_class, maximum_consecutive):
    values = [target for target in TARGETS for _ in range(per_class)]
    rng = random.Random(seed)
    for _ in range(1000):
        rng.shuffle(values)
        if all(run <= maximum_consecutive for run in _runs(values)):
            return tuple(values)
    raise RuntimeError("unable to produce a constrained balanced order")


def _runs(values):
    if not values:
        return []
    lengths = []
    current, length = values[0], 1
    for value in values[1:]:
        if value == current:
            length += 1
        else:
            lengths.append(length)
            current, length = value, 1
    lengths.append(length)
    return lengths


def generate_trial_plan(session_id, seed, trials_per_class=10, maximum_consecutive=2):
    if not session_id:
        raise ValueError("session_id is required")
    if trials_per_class <= 0:
        raise ValueError("trials_per_class must be positive")
    if maximum_consecutive <= 0:
        raise ValueError("maximum_consecutive must be positive")
    order = _balanced_order(int(seed), int(trials_per_class), int(maximum_consecutive))
    return [TrialPlanItem(session_id, "m6_1b-trial-{:03d}".format(index), index,
                          target, TARGETS[target]["targetSide"], TARGETS[target]["nominalFrequencyHz"],
                          int(seed), order)
            for index, target in enumerate(order, 1)]


def write_ground_truth(path, plan, protocol):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for item in plan:
            value = item.to_dict()
            value["protocol"] = protocol
            stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
