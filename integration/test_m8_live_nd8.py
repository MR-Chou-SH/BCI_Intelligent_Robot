import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from integration.m8_live_nd8 import (
    M8LiveNd8Session,
    M8LiveTrialCoordinator,
    M8WindowsAudibleCue,
    build_m8_live_trial_plan,
    create_m8_live_dry_run,
    limit_m8_live_trial_plan,
    run_m8_preparation_countdown,
    validate_vendor_cpython39_runtime,
)
import integration.m8_selection_cli as selection_cli
from integration.m8_selection_cli import main as cli_main
from integration.m8_selection_orchestration import M8LiveTrialBridge, M8SelectionOrchestrator


def accepted_ack(selection_id):
    return {
        "protocolVersion": 1,
        "messageType": "selection_ack",
        "selectionId": selection_id,
        "accepted": True,
        "rejectionReason": "None",
        "resolvedSlot": 0,
        "resolvedTargetId": "target-42",
        "resolvedClassName": "bottle",
    }


class FakeQuestTransport:
    def __init__(self):
        self.opens = []
        self.decisions = []
        self.aborts = []

    def open_selection(self, selection_id):
        self.opens.append(selection_id)
        return accepted_ack(selection_id)

    def submit_eeg_selection(self, selection_id, predicted_class_index):
        self.decisions.append((selection_id, predicted_class_index))
        return accepted_ack(selection_id)

    def abort_selection(self, selection_id):
        self.aborts.append(selection_id)
        return accepted_ack(selection_id)


class FakeLiveController:
    def __init__(self, result):
        self.result = result
        self.started = []
        self.stops = []

    def start_trial(self, association):
        self.started.append(dict(association))
        return True

    def stop_trial(self, reason):
        self.stops.append(reason)
        return dict(self.result)


class M8LiveNd8Tests(unittest.TestCase):
    def test_explicit_single_trial_limit_keeps_only_the_first_frozen_trial(self):
        trials = build_m8_live_trial_plan("m8-single")

        self.assertEqual(["m8-single-trial-001"], [
            item["trialId"] for item in limit_m8_live_trial_plan(trials, 1)
        ])
        self.assertEqual([0, 1, 2], [
            item["expectedClassIndex"] for item in limit_m8_live_trial_plan(trials, 3)
        ])

    def test_audible_preparation_cues_do_not_change_fixed_trial_mapping(self):
        cues = []

        class RecordingCue:
            def preparation_started(self):
                cues.append("preparation")

            def countdown_tick(self, remaining):
                cues.append("countdown-{}".format(remaining))

        run_m8_preparation_countdown(13, RecordingCue(), sleep=lambda _: None, output=lambda *args, **kwargs: None)

        self.assertEqual(["preparation", "countdown-3", "countdown-2", "countdown-1"], cues)
        self.assertEqual([0, 1, 2], [item["expectedClassIndex"] for item in build_m8_live_trial_plan("m8-session")])

    def test_audible_cue_failure_is_a_warning_not_a_business_failure(self):
        cue = M8WindowsAudibleCue(beep=lambda frequency, duration: (_ for _ in ()).throw(OSError("speaker")))

        cue.preparation_started()
        cue.countdown_tick(3)
        cue.observation_started()
        cue.trial_ended()

        self.assertEqual(4, cue.summary()["emittedCueCount"])
        self.assertEqual(4, cue.summary()["warningCount"])

    def test_fixed_three_trial_plan_keeps_expected_class_post_hoc(self):
        trials = build_m8_live_trial_plan("m8-session")

        self.assertEqual(
            [(0, 0, 7.2, "target_left"), (1, 1, 9.0, "target_center"), (2, 2, 12.0, "target_right")],
            [(item["slot"], item["expectedClassIndex"], item["frequencyHz"], item["expectedLabel"])
             for item in trials],
        )
        self.assertEqual(3, len({item["trialId"] for item in trials}))
        self.assertEqual(3, len({item["selectionId"] for item in trials}))

    def test_open_ack_precedes_live_start_and_only_final_decision_reaches_quest(self):
        trial = build_m8_live_trial_plan("m8-session")[1]
        transport = FakeQuestTransport()
        controller = FakeLiveController({
            "sessionId": "m8-session",
            "trialId": trial["trialId"],
            "decisionMade": True,
            "finalDecisionLabel": "target_center",
            "stabilizer": "2-Consecutive",
            "decisionPredictionIndex": 1,
            "decisionRelativeTimeSeconds": 2.2,
        })
        bridge = M8LiveTrialBridge(controller, M8SelectionOrchestrator(transport))
        coordinator = M8LiveTrialCoordinator(bridge)

        self.assertTrue(coordinator.start_trial(trial, 12345))
        result = coordinator.finish_trial()

        self.assertEqual([trial["selectionId"]], transport.opens)
        self.assertEqual([(trial["selectionId"], 1)], transport.decisions)
        self.assertEqual("quest_accepted", result["m8Selection"]["status"])
        self.assertNotIn("expectedClassIndex", controller.started[0])
        self.assertNotIn("expectedLabel", controller.started[0])

    def test_no_decision_and_runtime_abort_do_not_send_selection(self):
        trials = build_m8_live_trial_plan("m8-session")
        transport = FakeQuestTransport()
        controller = FakeLiveController({
            "sessionId": "m8-session",
            "trialId": trials[0]["trialId"],
            "decisionMade": False,
            "finalDecisionLabel": None,
            "reason": "no_sufficient_consecutive_run",
            "stabilizer": "2-Consecutive",
        })
        bridge = M8LiveTrialBridge(controller, M8SelectionOrchestrator(transport))
        coordinator = M8LiveTrialCoordinator(bridge)

        self.assertTrue(coordinator.start_trial(trials[0], 10))
        self.assertEqual("no_decision", coordinator.finish_trial()["m8Selection"]["status"])
        self.assertTrue(coordinator.start_trial(trials[1], 20))
        self.assertEqual("aborted", coordinator.abort_trial("nd8_packet_stall")["m8Selection"]["status"])
        self.assertEqual([], transport.decisions)
        self.assertEqual([trials[0]["selectionId"], trials[1]["selectionId"]], transport.aborts)

    def test_dry_run_writes_one_unique_prepared_session_without_vendor_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = create_m8_live_dry_run(root, "m8_2b-live")
            manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
            plan = json.loads((session / "m8-trial-plan.json").read_text(encoding="utf-8"))

            self.assertEqual("dry_run_passed", manifest["status"])
            self.assertFalse(manifest["nd8Started"])
            self.assertEqual([0, 1, 2], [item["expectedClassIndex"] for item in plan["trials"]])
            second = create_m8_live_dry_run(root, "m8_2b-live")
            self.assertNotEqual(session, second)

    def test_single_trial_dry_run_records_one_planned_selection_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = create_m8_live_dry_run(Path(directory), "m8-final", max_trials=1)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            plan = json.loads((root / "m8-trial-plan.json").read_text(encoding="utf-8"))

            self.assertEqual(1, manifest["plannedTrialCount"])
            self.assertEqual([0], manifest["trialOrder"])
            self.assertEqual(1, len(plan["trials"]))
            self.assertTrue(plan["trials"][0]["selectionId"].endswith("-selection-001"))

    def test_wrong_python_or_missing_vendor_import_fails_before_hardware(self):
        with self.assertRaisesRegex(RuntimeError, "CPython 3.9"):
            validate_vendor_cpython39_runtime(
                version_info=(3, 12, 0),
                implementation="cpython",
                architecture="64bit",
                importer=lambda name: object(),
            )
        with self.assertRaisesRegex(RuntimeError, "Neurodance SDK"):
            validate_vendor_cpython39_runtime(
                version_info=(3, 9, 13),
                implementation="cpython",
                architecture="64bit",
                importer=lambda name: (_ for _ in ()).throw(ImportError(name)),
            )

    def test_live_nd8_cli_dry_run_never_requires_vendor_runtime_or_nd8(self):
        with tempfile.TemporaryDirectory() as directory:
            exit_code = cli_main([
                "--mode", "live-nd8", "--dry-run", "--com", "COM11", "--data-root", directory,
            ])
            manifests = list(Path(directory).glob("*/manifest.json"))

            self.assertEqual(0, exit_code)
            self.assertEqual(1, len(manifests))
            self.assertEqual("dry_run_passed", json.loads(manifests[0].read_text(encoding="utf-8"))["status"])

    def test_live_nd8_cli_single_trial_dry_run_is_one_trial_without_batch_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            exit_code = cli_main([
                "--mode", "live-nd8", "--dry-run", "--max-trials", "1", "--com", "COM11", "--data-root", directory,
            ])
            manifests = list(Path(directory).glob("*/manifest.json"))

            self.assertEqual(0, exit_code)
            self.assertEqual(1, len(manifests))
            manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
            self.assertEqual(1, manifest["plannedTrialCount"])
            self.assertEqual([0], manifest["trialOrder"])

    def test_successful_single_trial_hands_released_port_to_batch_consumer_and_records_ack(self):
        receipt = SimpleNamespace(
            payload={"messageType": "target_batch_confirmed"},
            batch={"batchId": "m8-batch-final", "selections": [{"targetId": "target-42", "slotIndex": 0}]},
            downstream_accepted=True,
            ack={"messageType": "batch_ack", "batchId": "m8-batch-final"},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "session"
            root.mkdir()
            (root / "manifest.json").write_text("{}\n", encoding="utf-8")
            with patch.object(selection_cli, "run_live_nd8", return_value=(0, root)), \
                    patch.object(selection_cli, "consume_one_batch", return_value=receipt) as consume:
                exit_code = selection_cli.main([
                    "--mode", "live-nd8", "--max-trials", "1", "--com", "COM11", "--data-root", directory,
                ])

            self.assertEqual(0, exit_code)
            consume.assert_called_once_with("0.0.0.0", 11001, 45.0)
            evidence = json.loads((root / "m8-final-batch-delivery.json").read_text(encoding="utf-8"))
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(evidence["downstreamAccepted"])
            self.assertEqual("m8-batch-final", manifest["finalBatchDelivery"]["batchId"])

    def test_live_nd8_runtime_failure_records_session_without_constructing_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter_constructed = []
            args = SimpleNamespace(
                data_root=Path(directory), session_prefix="m8_2b-live", dry_run=False, com="COM11",
                preflight_timeout_seconds=1.0, packet_stall_seconds=2.0, host="127.0.0.1", port=11001,
                accept_timeout_seconds=1.0, ack_timeout_seconds=1.0, preflight_only=False,
                preparation_seconds=13.0, trial_window_seconds=4.0,
            )
            runner = M8LiveNd8Session(
                args,
                adapter_factory=lambda *unused: adapter_constructed.append(True),
                runtime_validator=lambda: (_ for _ in ()).throw(RuntimeError("wrong external runtime")),
            )
            exit_code, root = runner.run()
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

            self.assertEqual(2, exit_code)
            self.assertEqual([], adapter_constructed)
            self.assertEqual("incomplete", manifest["status"])
            self.assertIn("wrong external runtime", manifest["failureReason"])


if __name__ == "__main__":
    unittest.main()
