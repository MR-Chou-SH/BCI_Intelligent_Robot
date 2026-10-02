"""Offline checks for M28's M25 quality-transfer mapping and frozen gates."""

from __future__ import annotations

import importlib.util
import random
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSFER_PATH = (
    ROOT / "research_analysis" / "m28_real_semantic_context_20261002"
    / "attempt-01" / "run_m28_eeg_transfer.py"
)
SPEC = importlib.util.spec_from_file_location("m28_transfer_for_test", TRANSFER_PATH)
TRANSFER = importlib.util.module_from_spec(SPEC)
assert SPEC is not None and SPEC.loader is not None
SPEC.loader.exec_module(TRANSFER)


class M28EegTransferTests(unittest.TestCase):
    def test_empirical_prior_mass_is_preserved_when_mapped_to_eeg_classes(self):
        row = {
            "case_id": "fixture_case",
            "candidate_ids": ["surface_a", "container_b", "zone_c"],
            "result": {
                "candidate_ranking": ["container_b", "surface_a", "zone_c"],
                "context_prior": [0.12, 0.78, 0.10],
            },
            "evaluation_only": {"expected_target_ids": ["container_b"]},
            "api_latency_ms": 920.0,
        }
        mapped = TRANSFER._mapped_empirical_context(row, 2, random.Random(84), ("left", "center", "right"))

        self.assertTrue(mapped["semantic_top_correct"])
        self.assertEqual(mapped["context_top"], 2)
        self.assertEqual(mapped["prior"][2], 0.78)
        self.assertAlmostEqual(sum(mapped["prior"]), 1.0)
        self.assertEqual(mapped["api_latency_seconds"], 0.92)

    def test_full_prior_simulator_matches_frozen_m25_symmetric_prior_behavior(self):
        m25 = TRANSFER._load_m25()
        trials, manifest, folds, baseline_by, _, _, _ = m25.load_inputs()
        selected, baselines, matches = TRANSFER._baseline_and_shared(m25, trials, folds, baseline_by)
        self.assertEqual(matches, 264)
        trial = trials[0]
        op = "CONSERVATIVE"
        baseline = baselines[(op, trial["session"], trial["trialId"])]
        true_index = int(trial["trueClassIndex"])
        prior = m25.prior_vector(true_index, 0.80)
        params = selected[(trial["session"], op)]

        full = TRANSFER._simulate_full_prior(
            m25, trial, "0.10", baseline, prior, 0.0,
            float(params["topThreshold"]), float(params["marginThreshold"]),
            float(params["minimumEvidenceSeconds"]), float(params["stabilitySeconds"]),
        )
        legacy = m25.simulate_context(
            trial, "0.10", baseline, 0.80, true_index, True, m25.FROZEN_BRANCH,
            float(params["topThreshold"]), float(params["marginThreshold"]),
            float(params["minimumEvidenceSeconds"]), float(params["stabilitySeconds"]), 0.0,
        )

        for key in ("stop", "selectedIndex", "contextApplied", "authorizedPointCount", "wrongEarlyStop", "contextCausedError"):
            self.assertEqual(full[key], legacy[key], key)


if __name__ == "__main__":
    unittest.main()
