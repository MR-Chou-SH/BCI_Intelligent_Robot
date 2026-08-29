import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

import numpy as np
from eeg.decoder.config import DecoderConfig
from eeg.decoder.live_online import LiveOnlineController
from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord
from integration.m8_selection_cli import main as cli_main
from integration.m8_selection_orchestration import (
    M8LiveTrialBridge,
    M8SelectionOrchestrator,
    QuestSelectionTcpServer,
)


def accepted_ack(selection_id, **extra):
    return {
        "protocolVersion": 1,
        "messageType": "selection_ack",
        "selectionId": selection_id,
        "accepted": True,
        "rejectionReason": "None",
        "resolvedSlot": 1,
        "resolvedTargetId": "target-42",
        "resolvedClassName": "bottle\r",
        **extra,
    }


class FakeQuestTransport:
    def __init__(self, open_ack=None, decision_ack=None):
        self.open_ack = open_ack
        self.decision_ack = decision_ack
        self.opens = []
        self.decisions = []
        self.aborts = []

    def open_selection(self, selection_id):
        self.opens.append(selection_id)
        return self.open_ack or accepted_ack(selection_id)

    def submit_eeg_selection(self, selection_id, predicted_class_index):
        self.decisions.append((selection_id, predicted_class_index))
        return self.decision_ack or accepted_ack(selection_id)

    def abort_selection(self, selection_id):
        self.aborts.append(selection_id)
        return accepted_ack(selection_id)


class FakeLiveController:
    def __init__(self, result):
        self.result = result
        self.started = []
        self.stops = []

    def start_trial(self, association):
        self.started.append(association)
        return True

    def stop_trial(self, reason):
        self.stops.append(reason)
        return self.result


class M8SelectionOrchestrationTests(unittest.TestCase):
    def test_free_selection_forwards_any_class_in_actual_selection_order(self):
        for number, labels in enumerate((
            ("target_center", "target_left"),
            ("target_right", "target_center"),
        )):
            transport = FakeQuestTransport()
            orchestrator = M8SelectionOrchestrator(transport)
            for index, label in enumerate(labels):
                selection_id = "free-{}-selection-{}".format(number, index)
                trial_id = "free-{}-trial-{}".format(number, index)
                self.assertTrue(orchestrator.open_selection(selection_id, trial_id))
                result = orchestrator.submit_final_decision({
                    "trialId": trial_id,
                    "decisionMade": True,
                    "finalDecisionLabel": label,
                    "stabilizer": "2-Consecutive",
                })
                self.assertEqual("quest_accepted", result["status"])

            expected_indices = [1, 0] if number == 0 else [2, 1]
            self.assertEqual(expected_indices, [item[1] for item in transport.decisions])

    def test_final_m6_labels_submit_the_canonical_slot_indices_once(self):
        transport = FakeQuestTransport()
        orchestrator = M8SelectionOrchestrator(transport)

        for number, (label, expected_index) in enumerate((
            ("target_left", 0), ("target_center", 1), ("target_right", 2),
        )):
            selection_id = "selection-{}".format(number)
            trial_id = "trial-{}".format(number)
            self.assertTrue(orchestrator.open_selection(selection_id, trial_id))
            result = orchestrator.submit_final_decision({
                "trialId": trial_id,
                "decisionMade": True,
                "finalDecisionLabel": label,
                "stabilizer": "2-Consecutive",
            })
            self.assertEqual("quest_accepted", result["status"])
            self.assertEqual(expected_index, result["predictedClassIndex"])

        self.assertEqual(
            [("selection-0", 0), ("selection-1", 1), ("selection-2", 2)],
            transport.decisions,
        )

    def test_open_rejection_never_starts_or_submits_a_trial(self):
        transport = FakeQuestTransport(open_ack=accepted_ack("selection-a", accepted=False,
                                                              rejectionReason="InvalidSelectionId"))
        orchestrator = M8SelectionOrchestrator(transport)

        self.assertFalse(orchestrator.open_selection("selection-a", "trial-a"))
        result = orchestrator.submit_final_decision({
            "trialId": "trial-a", "decisionMade": True, "finalDecisionLabel": "target_left",
        })

        self.assertEqual("stale_or_unknown_trial", result["status"])
        self.assertEqual([], transport.decisions)

    def test_duplicate_final_decision_is_not_sent_twice(self):
        transport = FakeQuestTransport()
        orchestrator = M8SelectionOrchestrator(transport)
        self.assertTrue(orchestrator.open_selection("selection-b", "trial-b"))
        decision = {"trialId": "trial-b", "decisionMade": True, "finalDecisionLabel": "target_center"}

        self.assertEqual("quest_accepted", orchestrator.submit_final_decision(decision)["status"])
        self.assertEqual("duplicate_final_decision", orchestrator.submit_final_decision(decision)["status"])
        self.assertEqual([("selection-b", 1)], transport.decisions)

    def test_no_decision_and_abort_do_not_fabricate_eeg_selection(self):
        transport = FakeQuestTransport()
        orchestrator = M8SelectionOrchestrator(transport)
        self.assertTrue(orchestrator.open_selection("selection-c", "trial-c"))
        self.assertEqual("no_decision", orchestrator.submit_final_decision({
            "trialId": "trial-c", "decisionMade": False, "finalDecisionLabel": None,
            "reason": "no_sufficient_consecutive_run",
        })["status"])
        self.assertTrue(orchestrator.open_selection("selection-d", "trial-d"))
        self.assertEqual("aborted", orchestrator.abort_trial("trial-d", "operator_abort")["status"])
        self.assertTrue(orchestrator.open_selection("selection-e", "trial-e"))
        self.assertEqual("stale_or_unknown_trial", orchestrator.submit_final_decision({
            "trialId": "trial-d", "decisionMade": True, "finalDecisionLabel": "target_right",
        })["status"])
        self.assertEqual("quest_accepted", orchestrator.submit_final_decision({
            "trialId": "trial-e", "decisionMade": True, "finalDecisionLabel": "target_right",
        })["status"])
        self.assertEqual(["selection-c", "selection-d"], transport.aborts)
        self.assertEqual([("selection-e", 2)], transport.decisions)

    def test_quest_rejection_is_reported_as_a_terminal_integration_failure(self):
        transport = FakeQuestTransport(decision_ack=accepted_ack("selection-e", accepted=False,
                                                                   rejectionReason="UnknownSelectionId"))
        orchestrator = M8SelectionOrchestrator(transport)
        self.assertTrue(orchestrator.open_selection("selection-e", "trial-e"))

        result = orchestrator.submit_final_decision({
            "trialId": "trial-e", "decisionMade": True, "finalDecisionLabel": "target_right",
        })

        self.assertEqual("quest_rejected", result["status"])
        self.assertEqual("UnknownSelectionId", result["rejectionReason"])

    def test_bridge_opens_before_starting_m6_and_only_forwards_the_final_record(self):
        transport = FakeQuestTransport()
        controller = FakeLiveController({
            "trialId": "trial-f", "decisionMade": True, "finalDecisionLabel": "target_left",
            "stabilizer": "2-Consecutive",
        })
        bridge = M8LiveTrialBridge(controller, M8SelectionOrchestrator(transport))

        self.assertTrue(bridge.start_trial("selection-f", {"trialId": "trial-f"}))
        result = bridge.stop_trial("stimulus_stopped")

        self.assertEqual("quest_accepted", result["m8Selection"]["status"])
        self.assertEqual(["selection-f"], transport.opens)
        self.assertEqual([{"trialId": "trial-f"}], controller.started)
        self.assertEqual([("selection-f", 0)], transport.decisions)

    def test_bridge_does_not_start_m6_when_quest_rejects_selection_open(self):
        transport = FakeQuestTransport(open_ack=accepted_ack("selection-g", accepted=False,
                                                              rejectionReason="InvalidSelectionId"))
        controller = FakeLiveController({"trialId": "trial-g", "decisionMade": True,
                                         "finalDecisionLabel": "target_left"})
        bridge = M8LiveTrialBridge(controller, M8SelectionOrchestrator(transport))

        self.assertFalse(bridge.start_trial("selection-g", {"trialId": "trial-g"}))
        self.assertEqual([], controller.started)
        self.assertEqual([], transport.decisions)

    def test_real_m6_controller_forwards_only_the_stabilized_final_decision(self):
        class ConstantBackend:
            name = "constant"

            def predict(self, data):
                return 1, [0.1, 0.9, 0.2]

        transport = FakeQuestTransport()
        controller = LiveOnlineController(
            ConstantBackend(), [0, 1], config=DecoderConfig(analysis_duration_seconds=0.1, onset_guard_seconds=0.1),
        )
        bridge = M8LiveTrialBridge(controller, M8SelectionOrchestrator(transport))
        association = {"sessionId": "m6", "trialId": "trial-h", "estimatedGlobalSampleIndex": 0}

        self.assertTrue(bridge.start_trial("selection-h", association))
        for sequence in (0, 1):
            metadata = EegPacketMetadata(0, sequence, "", 200, 8, 1000, sequence)
            continuity = PacketContinuityRecord(sequence, sequence * 200, "continuous", ())
            controller.ingest_packet(metadata, continuity, np.ones((8, 200)))
        result = bridge.stop_trial()

        self.assertTrue(result["decisionMade"])
        self.assertEqual("target_center", result["finalDecisionLabel"])
        self.assertEqual(2, len(result["predictionTimeline"]))
        self.assertEqual("quest_accepted", result["m8Selection"]["status"])
        self.assertEqual([("selection-h", 1)], transport.decisions)


class QuestSelectionTcpServerTests(unittest.TestCase):
    def test_open_and_final_decision_use_newline_json_and_return_the_quest_ack(self):
        transport = QuestSelectionTcpServer("127.0.0.1", 0, accept_timeout_seconds=1.0, ack_timeout_seconds=1.0)
        transport.start()
        received = []

        def quest_client():
            with socket.create_connection(("127.0.0.1", transport.port), timeout=1.0) as client:
                stream = client.makefile("rwb")
                for expected_type in ("selection_open", "eeg_selection"):
                    request = json.loads(stream.readline().decode("utf-8"))
                    received.append(request)
                    self.assertEqual(expected_type, request["messageType"])
                    ack = accepted_ack(request["selectionId"], resolvedClassName="bottle\r")
                    stream.write((json.dumps(ack) + "\n").encode("utf-8"))
                    stream.flush()

        worker = threading.Thread(target=quest_client)
        worker.start()
        try:
            open_ack = transport.open_selection("selection-g")
            decision_ack = transport.submit_eeg_selection("selection-g", 2)
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(["selection_open", "eeg_selection"], [item["messageType"] for item in received])
        self.assertNotIn("targetId", received[1])
        self.assertEqual(2, received[1]["predictedClassIndex"])
        self.assertEqual("bottle", decision_ack["resolvedClassName"])
        self.assertTrue(open_ack["accepted"])

    def test_abort_uses_its_own_terminal_message_without_an_eeg_selection(self):
        transport = QuestSelectionTcpServer("127.0.0.1", 0, accept_timeout_seconds=1.0, ack_timeout_seconds=1.0)
        transport.start()
        received = []

        def quest_client():
            with socket.create_connection(("127.0.0.1", transport.port), timeout=1.0) as client:
                stream = client.makefile("rwb")
                for expected_type in ("selection_open", "selection_abort"):
                    request = json.loads(stream.readline().decode("utf-8"))
                    received.append(request)
                    self.assertEqual(expected_type, request["messageType"])
                    stream.write((json.dumps(accepted_ack(request["selectionId"])) + "\n").encode("utf-8"))
                    stream.flush()

        worker = threading.Thread(target=quest_client)
        worker.start()
        try:
            self.assertTrue(transport.open_selection("selection-abort")["accepted"])
            self.assertTrue(transport.abort_selection("selection-abort")["accepted"])
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(["selection_open", "selection_abort"], [item["messageType"] for item in received])


class M8SelectionCliTests(unittest.TestCase):
    def test_mock_command_opens_then_submits_one_final_decision(self):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        reservation.close()
        with tempfile.TemporaryDirectory() as directory:
            event_log = Path(directory) / "events.jsonl"
            exit_codes = []
            worker = threading.Thread(target=lambda: exit_codes.append(cli_main([
                "--mode", "mock", "--host", "127.0.0.1", "--port", str(port),
                "--event-log", str(event_log), "--selection-id-prefix", "cli-selection",
                "--trial-id", "cli-trial", "--final-label", "target_center",
            ])))
            worker.start()

            deadline = time.monotonic() + 1.0
            while True:
                try:
                    client = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("CLI did not open the selection listener")
                    time.sleep(0.01)
            with client:
                stream = client.makefile("rwb")
                for expected_type in ("selection_open", "eeg_selection"):
                    request = json.loads(stream.readline().decode("utf-8"))
                    self.assertEqual(expected_type, request["messageType"])
                    ack = accepted_ack(request["selectionId"])
                    stream.write((json.dumps(ack) + "\n").encode("utf-8"))
                    stream.flush()
            worker.join(1.0)

            self.assertFalse(worker.is_alive())
            self.assertEqual([0], exit_codes)
            records = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["selection_open_ack", "eeg_selection_ack"], [record["eventType"] for record in records])
            self.assertEqual("quest_accepted", records[-1]["status"])

    def test_mock_no_decision_terminates_the_quest_snapshot_without_eeg_selection(self):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        reservation.close()
        with tempfile.TemporaryDirectory() as directory:
            event_log = Path(directory) / "events.jsonl"
            exit_codes = []
            worker = threading.Thread(target=lambda: exit_codes.append(cli_main([
                "--mode", "mock", "--host", "127.0.0.1", "--port", str(port),
                "--event-log", str(event_log), "--selection-id-prefix", "cli-no-decision",
                "--trial-id", "cli-no-decision-trial", "--no-decision",
            ])))
            worker.start()

            deadline = time.monotonic() + 1.0
            while True:
                try:
                    client = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        self.fail("CLI did not open the selection listener")
                    time.sleep(0.01)
            with client:
                stream = client.makefile("rwb")
                received_types = []
                for expected_type in ("selection_open", "selection_abort"):
                    request = json.loads(stream.readline().decode("utf-8"))
                    received_types.append(request["messageType"])
                    self.assertEqual(expected_type, request["messageType"])
                    stream.write((json.dumps(accepted_ack(request["selectionId"])) + "\n").encode("utf-8"))
                    stream.flush()
            worker.join(1.0)

            self.assertFalse(worker.is_alive())
            self.assertEqual([0], exit_codes)
            self.assertEqual(["selection_open", "selection_abort"], received_types)
            records = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["selection_open_ack", "selection_abort_ack"], [record["eventType"] for record in records])
            self.assertEqual("no_decision", records[-1]["status"])


if __name__ == "__main__":
    unittest.main()
