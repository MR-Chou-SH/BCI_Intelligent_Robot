"""Small deterministic fixtures for M13 software/replay acceptance."""

import math

from integration.m11_context_prediction import ContextPrior
from integration.m12_context_eeg_fusion import ActiveSsvepCandidate, fuse_context_and_eeg
from integration.m13_dynamic_stopping import DynamicStoppingSnapshot
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


FIXTURE_TARGET_IDS = (
    "m9-vblock-red-01",
    "m9-vblock-green-01",
    "m9-vblock-blue-01",
)
FIXTURE_LOGICAL_IDS = ("block_sim_01", "block_sim_02", "block_sim_03")
FIXTURE_AVAILABLE_IDS = FIXTURE_LOGICAL_IDS + ("block_sim_04",)


def fixture_candidates():
    mapping = load_virtual_block_target_mapping()
    expected = {target_id: logical_id for target_id, logical_id in zip(FIXTURE_TARGET_IDS, FIXTURE_LOGICAL_IDS)}
    if any(mapping.get(target_id) != logical_id for target_id, logical_id in expected.items()):
        raise AssertionError("fixture must use the explicit frozen virtual-block mapping")
    return tuple(
        ActiveSsvepCandidate.from_frozen_mapping(slot, target_id, mapping)
        for slot, target_id in enumerate(FIXTURE_TARGET_IDS)
    )


def fixture_context_prior(active_weights=None):
    active_weights = tuple(active_weights or (0.25, 0.25, 0.25))
    if len(active_weights) != 3 or any(value < 0.0 or not math.isfinite(float(value)) for value in active_weights):
        raise ValueError("fixture context weights must contain three finite nonnegative values")
    total = sum(active_weights)
    if total > 1.0 + 1e-12:
        raise ValueError("fixture context weights cannot exceed one")
    fourth = 1.0 - total
    probabilities = tuple(zip(FIXTURE_AVAILABLE_IDS, active_weights + (fourth,)))
    maximum = max(value for _key, value in probabilities)
    return ContextPrior(
        observable_history=(),
        available_logical_block_ids=FIXTURE_AVAILABLE_IDS,
        step_index=0,
        candidate_task_count=1,
        task_hypotheses=(),
        next_target_probabilities=probabilities,
        top_targets=tuple(key for key, value in probabilities if math.isclose(value, maximum, abs_tol=1e-12, rel_tol=0.0)),
        tie=False,
        entropy=None,
        terminal=False,
        valid=True,
    )


def make_snapshot(window_index, eeg_scores, context_weights=(0.25, 0.25, 0.25), effective_acquisition_seconds=None, provenance=None):
    candidates = fixture_candidates()
    evidence = fuse_context_and_eeg(fixture_context_prior(context_weights), candidates, {
        candidate.logical_block_id: float(score)
        for candidate, score in zip(candidates, eeg_scores)
    })
    if effective_acquisition_seconds is None:
        effective_acquisition_seconds = 2.0 + 0.2 * window_index
    return DynamicStoppingSnapshot.from_m12(
        window_index,
        evidence,
        analysis_window_seconds=1.5,
        effective_acquisition_seconds=effective_acquisition_seconds,
        provenance=provenance or {"fixture": "M13 synthetic trajectory"},
    )


__all__ = ["FIXTURE_TARGET_IDS", "FIXTURE_LOGICAL_IDS", "FIXTURE_AVAILABLE_IDS", "fixture_candidates", "fixture_context_prior", "make_snapshot"]
