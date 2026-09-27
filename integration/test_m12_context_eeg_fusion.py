import json
import math
import unittest

from integration.m11_context_prediction import ContextPrior, make_observation, predict_context_prior
from integration.m12_context_eeg_fusion import (
    ACTIVE_SLOT_FREQUENCIES_HZ,
    EEG_SCORE_EPSILON,
    ActiveSsvepCandidate,
    FusionInputError,
    fuse_context_and_eeg,
    fuse_fbcca_score_vector,
)


TARGETS = (
    "m9-vblock-red-01",
    "m9-vblock-green-01",
    "m9-vblock-blue-01",
    "m9-vblock-yellow-01",
)


def candidates(logical_ids=("block_sim_01", "block_sim_02", "block_sim_03")):
    target_by_logical = dict(zip(
        ("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"), TARGETS
    ))
    return tuple(
        ActiveSsvepCandidate.from_frozen_mapping(slot, target_by_logical[logical_id])
        for slot, logical_id in enumerate(logical_ids)
    )


def simple_context(probabilities, history=("block_sim_01",)):
    return ContextPrior(
        observable_history=tuple(history),
        available_logical_block_ids=("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"),
        step_index=len(history),
        candidate_task_count=1,
        task_hypotheses=(),
        next_target_probabilities=tuple(probabilities.items()),
        top_targets=(),
        tie=False,
        entropy=None,
        terminal=False,
        valid=True,
    )


class M12ContextEegFusionTests(unittest.TestCase):
    def test_frozen_three_slot_frequencies_and_explicit_identity_mapping(self):
        active = candidates()
        self.assertEqual((0, 1, 2), tuple(item.slot_index for item in active))
        self.assertEqual(ACTIVE_SLOT_FREQUENCIES_HZ, tuple(item.nominal_frequency_hz for item in active))
        self.assertEqual(("block_sim_01", "block_sim_02", "block_sim_03"), tuple(item.logical_block_id for item in active))

    def test_uniform_context_preserves_eeg_order(self):
        context = simple_context({
            "block_sim_01": 0.25,
            "block_sim_02": 0.25,
            "block_sim_03": 0.25,
            "block_sim_04": 0.25,
        })
        result = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 3.0,
            "block_sim_03": 2.0,
        })
        self.assertEqual(("block_sim_02",), result.top_logical_block_ids)
        self.assertAlmostEqual(0.5, result.entries[1].fused_evidence)

    def test_context_and_eeg_agreement(self):
        context = predict_context_prior(make_observation(("block_sim_01", "block_sim_02", "block_sim_03")))
        result = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 1.0,
            "block_sim_03": 20.0,
        })
        self.assertEqual(("block_sim_03",), result.top_logical_block_ids)

    def test_moderate_conflict_is_still_deterministic(self):
        context = simple_context({
            "block_sim_01": 0.05,
            "block_sim_02": 0.85,
            "block_sim_03": 0.05,
            "block_sim_04": 0.05,
        })
        first = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 1.5,
            "block_sim_03": 1.0,
        })
        second = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 1.5,
            "block_sim_03": 1.0,
        })
        self.assertEqual(first, second)
        self.assertEqual(("block_sim_02",), first.top_logical_block_ids)

    def test_strong_eeg_can_override_context_bias(self):
        context = simple_context({
            "block_sim_01": 0.05,
            "block_sim_02": 0.90,
            "block_sim_03": 0.05,
            "block_sim_04": 0.0,
        })
        result = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 1.0,
            "block_sim_03": 100.0,
        })
        self.assertEqual(("block_sim_03",), result.top_logical_block_ids)
        self.assertTrue(all(entry.context_prior_soft > 0.0 for entry in result.entries))

    def test_golden_conflict_strong_eeg_cannot_be_overridden_by_context(self):
        # Session B Block 3 trial-082: real replay evidence, not a ground-truth
        # label injected into the fusion call.
        context = simple_context({
            "block_sim_01": 0.02,
            "block_sim_02": 0.94,
            "block_sim_03": 0.02,
            "block_sim_04": 0.02,
        }, history=())
        result = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.2568097085822083,
            "block_sim_02": 0.676054705615901,
            "block_sim_03": 0.6087883686553649,
        })
        self.assertEqual(("block_sim_01",), result.top_logical_block_ids)
        self.assertTrue(result.is_strong_eeg)
        self.assertEqual("eeg_strong_override", result.fusion_mode)
        self.assertEqual(("block_sim_01",), result.raw_eeg_top_logical_block_ids)
        self.assertAlmostEqual(0.580755003, result.eeg_margin, places=6)
        self.assertEqual("block_sim_02", result.context_preferred_logical_block_ids[0])

    def test_missing_context_falls_back_to_eeg_only(self):
        result = fuse_context_and_eeg(None, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 1.1,
            "block_sim_03": 1.0,
        })
        self.assertEqual(("block_sim_02",), result.top_logical_block_ids)
        self.assertEqual("context_neutral", result.fusion_mode)
        self.assertFalse(result.is_strong_eeg)

    def test_weak_conflicting_eeg_can_still_be_assisted_by_context(self):
        context = simple_context({
            "block_sim_01": 0.02,
            "block_sim_02": 0.94,
            "block_sim_03": 0.02,
            "block_sim_04": 0.02,
        })
        result = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.1,
            "block_sim_02": 1.0,
            "block_sim_03": 0.01,
        })
        self.assertFalse(result.is_strong_eeg)
        self.assertEqual("context_conflict_weak_eeg", result.fusion_mode)
        self.assertEqual(("block_sim_02",), result.top_logical_block_ids)

    def test_half_half_context_ambiguity_is_resolved_by_eeg_without_id_winner(self):
        context = predict_context_prior(make_observation(("block_sim_01", "block_sim_02")))
        result = fuse_context_and_eeg(context, candidates(("block_sim_02", "block_sim_03", "block_sim_04")), {
            "block_sim_02": 1.0,
            "block_sim_03": 7.0,
            "block_sim_04": 2.0,
        })
        self.assertEqual(("block_sim_03",), result.top_logical_block_ids)
        self.assertFalse(result.tie)

    def test_active_projection_normalizes_global_context_mass(self):
        context = simple_context({
            "block_sim_01": 0.9,
            "block_sim_02": 0.1,
            "block_sim_03": 0.0,
            "block_sim_04": 0.0,
        })
        active = candidates(("block_sim_02", "block_sim_03", "block_sim_04"))
        result = fuse_context_and_eeg(context, active, {
            "block_sim_02": 1.0,
            "block_sim_03": 1.0,
            "block_sim_04": 1.0,
        })
        self.assertEqual((1.0, 0.0, 0.0), tuple(entry.context_prior_active for entry in result.entries))
        self.assertEqual((2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0), tuple(entry.context_prior_soft for entry in result.entries))

    def test_zero_active_context_mass_falls_back_to_uniform(self):
        context = predict_context_prior(make_observation(()))
        active = candidates(("block_sim_02", "block_sim_03", "block_sim_04"))
        result = fuse_context_and_eeg(context, active, {
            "block_sim_02": 1.0,
            "block_sim_03": 1.0,
            "block_sim_04": 1.0,
        })
        self.assertEqual((1.0 / 3.0,) * 3, tuple(entry.context_prior_active for entry in result.entries))
        self.assertEqual((1.0 / 3.0,) * 3, tuple(entry.context_prior_soft for entry in result.entries))

    def test_epsilon_preparation_keeps_zero_evidence_candidate_nonzero(self):
        result = fuse_context_and_eeg(simple_context({
            "block_sim_01": 1.0,
            "block_sim_02": 0.0,
            "block_sim_03": 0.0,
            "block_sim_04": 0.0,
        }), candidates(), {
            "block_sim_01": 0.0,
            "block_sim_02": 0.0,
            "block_sim_03": 0.0,
        })
        self.assertEqual((EEG_SCORE_EPSILON,) * 3, tuple(entry.prepared_eeg_evidence_score for entry in result.entries))
        self.assertTrue(all(entry.fused_evidence > 0.0 for entry in result.entries))

    def test_invalid_nan_inf_negative_missing_and_extra_evidence_are_rejected(self):
        context = simple_context({"block_sim_01": 1.0, "block_sim_02": 0.0, "block_sim_03": 0.0, "block_sim_04": 0.0})
        base = {"block_sim_01": 1.0, "block_sim_02": 1.0, "block_sim_03": 1.0}
        for value in (float("nan"), float("inf"), -1.0):
            with self.subTest(value=value):
                invalid = dict(base)
                invalid["block_sim_02"] = value
                with self.assertRaises(FusionInputError):
                    fuse_context_and_eeg(context, candidates(), invalid)
        with self.assertRaises(FusionInputError):
            fuse_context_and_eeg(context, candidates(), {"block_sim_01": 1.0})
        with self.assertRaises(FusionInputError):
            fuse_context_and_eeg(context, candidates(), {**base, "block_sim_04": 1.0})

    def test_fbcca_vector_adapter_is_read_only_and_maps_by_slot(self):
        context = simple_context({"block_sim_01": 0.25, "block_sim_02": 0.25, "block_sim_03": 0.25, "block_sim_04": 0.25})
        result = fuse_fbcca_score_vector(context, candidates(), (2.0, 9.0, 1.0))
        self.assertEqual(("block_sim_02",), result.top_logical_block_ids)

    def test_public_evidence_is_finite_private_identity_safe_and_robot_free(self):
        context = simple_context({"block_sim_01": 0.25, "block_sim_02": 0.25, "block_sim_03": 0.25, "block_sim_04": 0.25})
        public = fuse_context_and_eeg(context, candidates(), {
            "block_sim_01": 1.0,
            "block_sim_02": 2.0,
            "block_sim_03": 3.0,
        }).to_public_dict()
        serialized = json.dumps(public, sort_keys=True)
        self.assertNotIn("obj_", serialized)
        self.assertNotIn("RobotAdapter", serialized)
        self.assertTrue(all(math.isfinite(value) for entry in public["entries"] for value in (
            entry["eegEvidenceScore"], entry["preparedEegEvidenceScore"],
            entry["contextPriorSoft"], entry["normalizedFusedEvidence"])))
        self.assertAlmostEqual(1.0, sum(entry["normalizedFusedEvidence"] for entry in public["entries"]))

    def test_invalid_context_prior_is_rejected(self):
        terminal = predict_context_prior(make_observation(("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04")))
        with self.assertRaises(FusionInputError):
            fuse_context_and_eeg(terminal, candidates(), {"block_sim_01": 1.0, "block_sim_02": 1.0, "block_sim_03": 1.0})


if __name__ == "__main__":
    unittest.main()
