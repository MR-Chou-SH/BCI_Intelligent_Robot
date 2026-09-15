from dataclasses import replace
import unittest

from integration.m12_context_eeg_fusion import fuse_context_and_eeg
from integration.m13_dynamic_stopping import (
    DynamicStoppingInputError,
    DynamicStoppingPolicy,
    M13_FUSED_EVIDENCE_THRESHOLD,
    M13_MARGIN_THRESHOLD,
    M13_REQUIRED_CONSECUTIVE,
    WINDOW_GRID_SECONDS,
    run_dynamic_stopping,
)
from integration.m13_trajectory_fixture import fixture_candidates, fixture_context_prior, make_snapshot


class M13DynamicStoppingTests(unittest.TestCase):
    def test_defaults_are_frozen(self):
        self.assertEqual(M13_FUSED_EVIDENCE_THRESHOLD, 0.70)
        self.assertEqual(M13_MARGIN_THRESHOLD, 0.20)
        self.assertEqual(M13_REQUIRED_CONSECUTIVE, 2)
        with self.assertRaises(DynamicStoppingInputError):
            DynamicStoppingPolicy(margin_threshold=0.19)
        self.assertEqual(WINDOW_GRID_SECONDS, (0.5, 1.0, 1.5, 2.0, 2.5, 3.0))

    def test_first_eligible_window_waits_and_second_stops(self):
        policy, decision = run_dynamic_stopping([
            make_snapshot(0, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)),
            make_snapshot(1, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)),
        ])
        self.assertFalse(policy.evaluations[0].stop)
        self.assertTrue(decision.decision_made)
        self.assertTrue(decision.early_stop)
        self.assertEqual(decision.stop_window, 1)
        self.assertEqual(decision.selected_logical_block_id, "block_sim_01")
        self.assertEqual(decision.stop_reason, "early_stable_fused_evidence")

    def test_transient_spike_does_not_stop(self):
        policy, decision = run_dynamic_stopping([
            make_snapshot(0, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)),
            make_snapshot(1, (1.0, 1.0, 1.0)),
            make_snapshot(2, (1.0, 0.9, 0.1)),
        ])
        self.assertFalse(any(item.stop for item in policy.evaluations))
        self.assertFalse(decision.early_stop)
        self.assertEqual(decision.stop_reason, "full_window_fallback")

    def test_target_switch_resets_count(self):
        policy, decision = run_dynamic_stopping([
            make_snapshot(0, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)),
            make_snapshot(1, (0.01, 2.0, 0.01), (0.005, 0.98, 0.005)),
            make_snapshot(2, (0.01, 2.0, 0.01), (0.005, 0.98, 0.005)),
        ])
        self.assertEqual(policy.evaluations[0].consecutive_count, 1)
        self.assertEqual(policy.evaluations[1].consecutive_count, 1)
        self.assertTrue(decision.early_stop)
        self.assertEqual(decision.selected_logical_block_id, "block_sim_02")
        self.assertEqual(decision.stop_window, 2)

    def test_context_alone_cannot_override_eeg_top(self):
        policy, decision = run_dynamic_stopping([
            make_snapshot(0, (0.29, 0.30, 0.01), (0.98, 0.005, 0.005)),
            make_snapshot(1, (0.29, 0.30, 0.01), (0.98, 0.005, 0.005)),
        ])
        self.assertTrue(all(not item.stop for item in policy.evaluations))
        self.assertFalse(decision.early_stop)
        self.assertEqual(policy.evaluations[-1].reason, "eeg_confirmation_failed")

    def test_strong_eeg_override_can_stop_after_confirmation(self):
        _policy, decision = run_dynamic_stopping([
            make_snapshot(0, (0.01, 2.0, 0.01), (0.98, 0.005, 0.005)),
            make_snapshot(1, (0.01, 2.0, 0.01), (0.98, 0.005, 0.005)),
        ])
        self.assertTrue(decision.early_stop)
        self.assertEqual(decision.selected_logical_block_id, "block_sim_02")

    def test_margin_threshold_blocks_ambiguous_evidence(self):
        policy, decision = run_dynamic_stopping([
            make_snapshot(0, (1.0, 0.9, 0.1)),
            make_snapshot(1, (1.0, 0.9, 0.1)),
        ])
        self.assertTrue(all(not item.stop for item in policy.evaluations))
        self.assertEqual(policy.evaluations[-1].reason, "fused_threshold_not_met")
        self.assertEqual(decision.stop_reason, "full_window_fallback")

    def test_valid_max_window_fallback(self):
        _policy, decision = run_dynamic_stopping([
            make_snapshot(0, (1.0, 0.9, 0.1)),
            make_snapshot(1, (1.0, 0.9, 0.1)),
            make_snapshot(2, (1.0, 0.9, 0.1)),
        ])
        self.assertFalse(decision.early_stop)
        self.assertTrue(decision.decision_made)
        self.assertEqual(decision.stop_reason, "full_window_fallback")

    def test_final_tie_is_no_decision(self):
        _policy, decision = run_dynamic_stopping([
            make_snapshot(0, (1.0, 1.0, 0.1)),
        ])
        self.assertFalse(decision.decision_made)
        self.assertEqual(decision.stop_reason, "no_decision_tie")

    def test_invalid_evidence_is_explicit(self):
        policy = DynamicStoppingPolicy()
        policy.reject_invalid(0, 2.0)
        decision = policy.finalize()
        self.assertFalse(decision.decision_made)
        self.assertEqual(decision.stop_reason, "invalid_evidence")

    def test_nan_evidence_is_rejected_without_fabrication(self):
        snapshot = make_snapshot(0, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005))
        bad_entries = tuple(replace(entry, eeg_evidence_score=float("nan")) for entry in snapshot.fused_evidence.entries)
        bad_snapshot = replace(snapshot, fused_evidence=replace(snapshot.fused_evidence, entries=bad_entries))
        with self.assertRaises(DynamicStoppingInputError):
            DynamicStoppingPolicy().observe(bad_snapshot)

    def test_deterministic_replay(self):
        snapshots = [make_snapshot(0, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)), make_snapshot(1, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005))]
        self.assertEqual(run_dynamic_stopping(snapshots)[1].to_public_dict(), run_dynamic_stopping(snapshots)[1].to_public_dict())

    def test_uniform_context_preserves_eeg_ranking(self):
        candidates = fixture_candidates()
        evidence = fuse_context_and_eeg(fixture_context_prior((0.25, 0.25, 0.25)), candidates, {
            candidate.logical_block_id: score for candidate, score in zip(candidates, (0.1, 0.8, 0.2))
        })
        self.assertEqual(evidence.top_logical_block_ids, ("block_sim_02",))
        self.assertEqual(max(evidence.entries, key=lambda item: item.fused_evidence).candidate.logical_block_id, "block_sim_02")

    def test_identity_does_not_expose_obj_ids(self):
        _policy, decision = run_dynamic_stopping([
            make_snapshot(0, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)),
            make_snapshot(1, (2.0, 0.01, 0.01), (0.98, 0.005, 0.005)),
        ])
        self.assertNotIn("obj_", repr(decision.to_public_dict()))


if __name__ == "__main__":
    unittest.main()
