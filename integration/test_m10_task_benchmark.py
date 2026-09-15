import json
from pathlib import Path
import tempfile
import unittest

from integration.m10_task_benchmark import (
    BENCHMARK_LOGICAL_BLOCK_IDS,
    FIXTURE_PATH,
    TASK_DEFINITIONS,
    TaskDefinition,
    TaskStatus,
    apply_selection,
    initial_state,
    load_benchmark_fixture,
    replay_sequence,
    reset_task,
    task_context,
)
from integration.m10_task_benchmark_runner import run_episode
from integration.m10_benchmark_acceptance import run_formal_acceptance


class M10TaskBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_benchmark_fixture()

    def test_task_definition_copies_sequence_into_immutable_tuple(self):
        source_sequence = list(TASK_DEFINITIONS["house"].ordered_logical_block_ids)
        definition = TaskDefinition("house-copy", "House Copy", source_sequence)
        source_sequence[0] = "block_sim_04"

        self.assertEqual("block_sim_01", definition.ordered_logical_block_ids[0])
        self.assertIsInstance(definition.ordered_logical_block_ids, tuple)

    def test_frozen_house_tower_and_bridge_full_episodes_complete(self):
        expected = {
            "house": ("block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"),
            "tower": ("block_sim_01", "block_sim_03", "block_sim_02", "block_sim_04"),
            "bridge": ("block_sim_01", "block_sim_02", "block_sim_04", "block_sim_03"),
        }
        fixture_episodes = self.fixture["episodes"]["validFull"]
        self.assertEqual(set(expected), {episode["taskId"] for episode in fixture_episodes})

        for task_id, sequence in expected.items():
            with self.subTest(task_id=task_id):
                definition = TASK_DEFINITIONS[task_id]
                self.assertEqual(sequence, definition.ordered_logical_block_ids)
                transitions, final_state = replay_sequence(definition, sequence)
                self.assertEqual((0, 1, 2, 3), tuple(item.step_index for item in transitions))
                self.assertTrue(all(item.accepted for item in transitions))
                self.assertEqual(TaskStatus.COMPLETED, final_state.status)
                self.assertEqual(sequence, final_state.completed_sequence)
                self.assertEqual((), final_state.remaining_sequence)

    def test_partial_episode_exposes_next_deterministic_context(self):
        episode = self.fixture["episodes"]["validPartial"][0]
        definition = TASK_DEFINITIONS[episode["taskId"]]
        transitions, state = replay_sequence(definition, episode["logicalBlockIds"])
        context = task_context(definition, state)

        self.assertTrue(all(item.accepted for item in transitions))
        self.assertEqual(episode["expectedStatus"], state.status.value)
        self.assertEqual(episode["expectedCurrentStep"], state.current_step)
        self.assertEqual(("block_sim_01", "block_sim_02"), state.completed_sequence)
        self.assertEqual(("block_sim_03", "block_sim_04"), state.remaining_sequence)
        self.assertEqual(2, context.step_index)
        self.assertEqual(("block_sim_03",), context.valid_next_logical_block_ids)

    def test_wrong_order_marks_episode_invalid_without_advancing(self):
        episode = self.fixture["episodes"]["invalidTransitions"][0]
        definition = TASK_DEFINITIONS[episode["taskId"]]
        _prefix, state = replay_sequence(definition, episode["acceptedPrefix"])

        transition = apply_selection(definition, state, episode["logicalBlockId"])

        self.assertFalse(transition.accepted)
        self.assertEqual(episode["expectedReasonCode"], transition.reason_code)
        self.assertEqual(episode["expectedStatus"], transition.state.status.value)
        self.assertEqual(1, transition.state.current_step)
        self.assertEqual(("block_sim_01",), transition.state.completed_sequence)
        self.assertEqual(("block_sim_02", "block_sim_03", "block_sim_04"), transition.state.remaining_sequence)
        self.assertEqual((), task_context(definition, transition.state).valid_next_logical_block_ids)

    def test_unknown_logical_id_invalidates_without_entering_task_state(self):
        episode = self.fixture["episodes"]["invalidTransitions"][1]
        definition = TASK_DEFINITIONS[episode["taskId"]]

        transition = apply_selection(
            definition, initial_state(definition), episode["logicalBlockId"]
        )

        self.assertFalse(transition.accepted)
        self.assertEqual(episode["expectedReasonCode"], transition.reason_code)
        self.assertEqual(episode["expectedStatus"], transition.state.status.value)
        self.assertEqual((), transition.state.completed_sequence)
        self.assertEqual(definition.ordered_logical_block_ids, transition.state.remaining_sequence)
        self.assertEqual("unknown_logical_block_id", transition.state.invalid_reason_code)

    def test_transition_after_completion_is_rejected_without_corrupting_completed_state(self):
        episode = self.fixture["episodes"]["invalidTransitions"][2]
        definition = TASK_DEFINITIONS[episode["taskId"]]
        _prefix, complete_state = replay_sequence(definition, episode["acceptedPrefix"])

        transition = apply_selection(definition, complete_state, episode["logicalBlockId"])

        self.assertFalse(transition.accepted)
        self.assertEqual(episode["expectedReasonCode"], transition.reason_code)
        self.assertEqual(TaskStatus.COMPLETED, transition.state.status)
        self.assertEqual(complete_state, transition.state)

    def test_invalid_state_is_terminal_until_reset(self):
        definition = TASK_DEFINITIONS["house"]
        invalid_transition = apply_selection(definition, initial_state(definition), "block_sim_99")

        next_transition = apply_selection(definition, invalid_transition.state, "block_sim_01")

        self.assertFalse(next_transition.accepted)
        self.assertEqual("task_already_invalid", next_transition.reason_code)
        self.assertEqual(invalid_transition.state, next_transition.state)

    def test_reset_from_completed_and_invalid_restores_same_initial_state(self):
        definition = TASK_DEFINITIONS["house"]
        _full, completed_state = replay_sequence(
            definition, definition.ordered_logical_block_ids
        )
        invalid_state = apply_selection(
            definition, initial_state(definition), "block_sim_99"
        ).state
        expected = initial_state(definition)

        self.assertEqual(expected, reset_task(definition))
        self.assertEqual(expected, reset_task(definition))
        self.assertNotEqual(completed_state, reset_task(definition))
        self.assertNotEqual(invalid_state, reset_task(definition))
        self.assertEqual(
            task_context(definition, expected),
            task_context(definition, reset_task(definition)),
        )

    def test_replay_output_is_deterministic_and_uses_only_logical_ids(self):
        first = run_episode(
            "house", iter(TASK_DEFINITIONS["house"].ordered_logical_block_ids)
        )
        second = run_episode("house", TASK_DEFINITIONS["house"].ordered_logical_block_ids)
        serialized_first = json.dumps(first, sort_keys=True, separators=(",", ":"))
        serialized_second = json.dumps(second, sort_keys=True, separators=(",", ":"))
        fixture_text = FIXTURE_PATH.read_text(encoding="utf-8")

        self.assertEqual(serialized_first, serialized_second)
        self.assertNotIn("obj_", serialized_first)
        self.assertNotIn("obj_", fixture_text)
        all_ids = {
            block_id
            for definition in TASK_DEFINITIONS.values()
            for block_id in definition.ordered_logical_block_ids
        }
        self.assertEqual(set(BENCHMARK_LOGICAL_BLOCK_IDS), all_ids)

    def test_runner_replays_wrong_order_as_invalid(self):
        result = run_episode("house", ("block_sim_01", "block_sim_03"))

        self.assertEqual("invalid", result["episodeOutcome"])
        self.assertEqual("invalid", result["finalState"]["status"])
        self.assertEqual([True, False], [step["accepted"] for step in result["transitions"]])
        self.assertEqual("wrong_order", result["transitions"][-1]["reasonCode"])

    def test_runner_distinguishes_valid_incomplete_from_completed(self):
        result = run_episode("house", ("block_sim_01", "block_sim_02"))

        self.assertEqual("valid_incomplete", result["episodeOutcome"])
        self.assertEqual("in_progress", result["finalState"]["status"])
        self.assertEqual(["block_sim_03"], result["taskContext"]["validNextLogicalBlockIds"])

    def test_formal_acceptance_summary_and_jsonl_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.jsonl"
            second_path = Path(directory) / "second.jsonl"
            first = run_formal_acceptance(evidence_path=first_path)
            second = run_formal_acceptance(evidence_path=second_path)

            self.assertEqual(first, second)
            self.assertEqual("PASS", first["overallStatus"])
            self.assertEqual({"total": 7, "passed": 7, "failed": 0}, first["caseCounts"])
            self.assertEqual("PASS", first["branchingStatus"])
            self.assertEqual(2, len(first["branchingEvidence"]))

            cases = {
                case["caseId"]: case
                for group in first["fixtureGroups"]
                for case in group["cases"]
            }
            self.assertEqual("PASS", cases["house-full"]["benchmarkCaseStatus"])
            self.assertEqual("completed", cases["house-full"]["episodeOutcome"])
            self.assertEqual("PASS", cases["house-partial-first-two"]["benchmarkCaseStatus"])
            self.assertEqual("valid_incomplete", cases["house-partial-first-two"]["episodeOutcome"])
            self.assertEqual("PASS", cases["house-wrong-order-after-red"]["benchmarkCaseStatus"])
            self.assertEqual("invalid", cases["house-wrong-order-after-red"]["episodeOutcome"])
            self.assertEqual("PASS", cases["house-unknown-logical-id"]["benchmarkCaseStatus"])
            self.assertEqual("invalid", cases["house-unknown-logical-id"]["episodeOutcome"])
            self.assertEqual("PASS", cases["house-transition-after-completion"]["benchmarkCaseStatus"])
            self.assertEqual("completed", cases["house-transition-after-completion"]["episodeOutcome"])
            self.assertEqual("completed", cases["house-transition-after-completion"]["finalTaskStatus"])

            branch_by_prefix = {
                tuple(record["prefixLogicalBlockIds"]): record
                for record in first["branchingEvidence"]
            }
            self.assertEqual(
                {
                    "house": ["block_sim_02"],
                    "tower": ["block_sim_03"],
                    "bridge": ["block_sim_02"],
                },
                branch_by_prefix[("block_sim_01",)]["taskValidNextLogicalBlockIds"],
            )
            self.assertEqual(
                {
                    "house": ["block_sim_03"],
                    "bridge": ["block_sim_04"],
                },
                branch_by_prefix[("block_sim_01", "block_sim_02")][
                    "taskValidNextLogicalBlockIds"
                ],
            )

            first_records = first_path.read_text(encoding="utf-8").splitlines()
            second_records = second_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(first_records, second_records)
            self.assertEqual(9, len(first_records))
            self.assertEqual(7, sum('"recordType": "episodeEvidence"' in line for line in first_records))
            self.assertNotIn("obj_", json.dumps(first, sort_keys=True))
            self.assertNotIn("obj_", first_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
