import json
import unittest

from integration.m10_task_benchmark import BENCHMARK_LOGICAL_BLOCK_IDS
from integration.m11_context_prediction import (
    ContextObservation,
    make_observation,
    predict_context_prior,
)


class M11ContextPredictionTests(unittest.TestCase):
    def test_empty_history_has_uniform_task_hypotheses_and_one_next_target(self):
        result = predict_context_prior(make_observation(()))
        self.assertTrue(result.valid)
        self.assertEqual(3, result.candidate_task_count)
        self.assertEqual({"block_sim_01": 1.0}, result.probability_map())
        self.assertEqual(("block_sim_01",), result.top_targets)
        self.assertFalse(result.tie)
        self.assertAlmostEqual(1.0, sum(result.probability_map().values()))

    def test_single_prefix_has_two_to_one_branching_distribution(self):
        result = predict_context_prior(make_observation(("block_sim_01",)))
        self.assertEqual(
            {"block_sim_02": 2.0 / 3.0, "block_sim_03": 1.0 / 3.0},
            result.probability_map(),
        )

    def test_two_prefix_has_exact_half_half_tie(self):
        result = predict_context_prior(make_observation(("block_sim_01", "block_sim_02")))
        self.assertEqual(
            {"block_sim_03": 0.5, "block_sim_04": 0.5}, result.probability_map()
        )
        self.assertEqual(("block_sim_03", "block_sim_04"), result.top_targets)
        self.assertTrue(result.tie)

    def test_other_frozen_prefixes_are_deterministic(self):
        cases = {
            ("block_sim_01", "block_sim_03"): {"block_sim_02": 1.0},
            ("block_sim_01", "block_sim_02", "block_sim_03"): {"block_sim_04": 1.0},
            ("block_sim_01", "block_sim_02", "block_sim_04"): {"block_sim_03": 1.0},
            ("block_sim_01", "block_sim_03", "block_sim_02"): {"block_sim_04": 1.0},
        }
        for history, expected in cases.items():
            with self.subTest(history=history):
                result = predict_context_prior(make_observation(history))
                self.assertEqual(expected, result.probability_map())
                self.assertFalse(result.tie)

    def test_terminal_history_has_no_fifth_step_or_next_distribution(self):
        result = predict_context_prior(
            make_observation(BENCHMARK_LOGICAL_BLOCK_IDS)
        )
        self.assertTrue(result.valid)
        self.assertTrue(result.terminal)
        self.assertEqual({}, result.probability_map())
        self.assertEqual((), result.top_targets)

    def test_invalid_history_is_explicit_and_has_no_fallback(self):
        result = predict_context_prior(
            make_observation(("block_sim_01", "block_sim_04"))
        )
        self.assertFalse(result.valid)
        self.assertEqual(0, result.candidate_task_count)
        self.assertEqual({}, result.probability_map())
        self.assertEqual("no_consistent_task_hypothesis", result.invalid_reason)

    def test_unknown_and_repeated_history_are_invalid(self):
        for history in (("block_sim_99",), ("block_sim_01", "block_sim_01")):
            with self.subTest(history=history):
                result = predict_context_prior(make_observation(history))
                self.assertFalse(result.valid)
                self.assertEqual(0, result.candidate_task_count)

    def test_observable_step_index_is_derived_and_cannot_be_falsified(self):
        observation = make_observation(("block_sim_01",), step_index=1)
        self.assertEqual(1, observation.step_index)
        with self.assertRaisesRegex(ValueError, "stepIndex"):
            make_observation(("block_sim_01",), step_index=0)

    def test_available_catalogue_is_validated_without_hidden_task_input(self):
        self.assertEqual(
            set(BENCHMARK_LOGICAL_BLOCK_IDS),
            set(make_observation(()).available_logical_block_ids),
        )
        with self.assertRaisesRegex(ValueError, "frozen four-block"):
            make_observation((), available=("block_sim_01",))

    def test_same_observation_has_identical_output_for_every_hidden_task(self):
        serialized = []
        for hidden_task_id in ("house", "tower", "bridge"):
            # The hidden ID exists only in evaluator-side test bookkeeping;
            # it is deliberately not passed to the predictor.
            del hidden_task_id
            serialized.append(
                json.dumps(
                    predict_context_prior(
                        make_observation(("block_sim_01",))
                    ).to_public_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        self.assertEqual(1, len(set(serialized)))

    def test_public_serialization_contains_no_oracle_or_object_ids(self):
        public = predict_context_prior(make_observation(("block_sim_01",))).to_public_dict()
        serialized = json.dumps(public, sort_keys=True)
        self.assertNotIn("remainingSequence", serialized)
        self.assertNotIn("validNextLogicalBlockIds", serialized)
        self.assertNotIn("taskContext", serialized)
        self.assertNotIn("obj_", serialized)

    def test_replay_is_deterministic_and_probabilities_sum_to_one(self):
        histories = [
            (),
            ("block_sim_01",),
            ("block_sim_01", "block_sim_02"),
            ("block_sim_01", "block_sim_03"),
        ]
        first = [predict_context_prior(make_observation(item)).to_public_dict() for item in histories]
        second = [predict_context_prior(make_observation(item)).to_public_dict() for item in histories]
        self.assertEqual(first, second)
        for result in first:
            if result["valid"] and not result["terminal"]:
                self.assertAlmostEqual(1.0, sum(result["nextTargetProbabilities"].values()))


if __name__ == "__main__":
    unittest.main()
