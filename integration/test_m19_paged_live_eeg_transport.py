"""M19 request/ACK/result transport tests over the existing M8 TCP stream."""

import json
import socket
import threading
import unittest

from integration.m8_selection_orchestration import QuestSelectionTcpServer, QuestSelectionTransportError


def _line(stream, value):
    stream.write((json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8"))
    stream.flush()


class M19PagedLiveTransportTests(unittest.TestCase):
    def test_decode_ack_requires_trimmed_selection_id_and_boolean_decision(self):
        transport = QuestSelectionTcpServer("127.0.0.1", 0)
        base = {
            "protocolVersion": 1,
            "messageType": "eeg_decode_ack",
            "selectionId": "selection-01",
            "accepted": True,
        }
        for field, value in (("selectionId", " "), ("selectionId", 42), ("accepted", 1)):
            invalid = dict(base)
            invalid[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(QuestSelectionTransportError):
                    transport.send_m19_decode_ack(invalid)

        abort_ack = dict(base, messageType="eeg_decode_abort_ack", accepted="true")
        with self.assertRaises(QuestSelectionTransportError):
            transport.send_m19_decode_abort_ack(abort_ack)

    def test_quest_request_receives_explicit_ack_and_class_only_result(self):
        transport = QuestSelectionTcpServer(
            "127.0.0.1", 0, accept_timeout_seconds=3.0, ack_timeout_seconds=3.0
        ).start()
        peer_records = []
        peer_errors = []
        request = {
            "protocolVersion": 1,
            "messageType": "eeg_decode_request",
            "contractId": "m19_decode_request_v1",
            "trialId": "transport-trial-01",
            "selectionId": "transport-selection-01",
            "pageId": "m16-page-1",
            "pageIndex": 0,
            "pageEpoch": 7,
            "createdUtc": "2026-09-22T12:00:00+00:00",
            "activeSlotCount": 2,
            "slots": [
                {"slotIndex": 0, "frequencyHz": 7.2, "targetId": "m9-vblock-yellow-01"},
                {"slotIndex": 2, "frequencyHz": 12.0, "targetId": "m9-vblock-green-01"},
            ],
        }

        def quest_peer():
            try:
                with socket.create_connection(("127.0.0.1", transport.port), timeout=3.0) as client:
                    stream = client.makefile("rwb")
                    _line(stream, request)
                    peer_records.append(json.loads(stream.readline().decode("utf-8")))
                    selection = json.loads(stream.readline().decode("utf-8"))
                    peer_records.append(selection)
                    _line(stream, {
                        "protocolVersion": 1,
                        "messageType": "selection_ack",
                        "selectionId": selection["selectionId"],
                        "accepted": True,
                        "rejectionReason": "None",
                    })
            except Exception as error:  # surfaced in the owning test thread
                peer_errors.append(error)

        worker = threading.Thread(target=quest_peer)
        worker.start()
        try:
            transport._ensure_connection()
            events = transport.poll_controller_events(0.1)
            self.assertEqual([request], events)
            ack = {
                "protocolVersion": 1,
                "messageType": "eeg_decode_ack",
                "trialId": request["trialId"],
                "selectionId": request["selectionId"],
                "pageId": request["pageId"],
                "pageEpoch": request["pageEpoch"],
                "accepted": True,
                "rejectionReason": "None",
            }
            transport.send_m19_decode_ack(ack)
            result = {
                "messageType": "eeg_decode_result",
                "selectionId": request["selectionId"],
                "trialId": request["trialId"],
                "pageId": request["pageId"],
                "pageEpoch": request["pageEpoch"],
                "decisionMade": True,
                "classIndex": 2,
                "slotIndex": 2,
                "accepted": True,
            }
            selection_ack = transport.submit_m19_eeg_selection(result)
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], peer_errors)
        self.assertTrue(selection_ack["accepted"])
        self.assertEqual("eeg_decode_ack", peer_records[0]["messageType"])
        self.assertEqual("eeg_selection", peer_records[1]["messageType"])
        self.assertEqual(2, peer_records[1]["predictedClassIndex"])
        self.assertNotIn("targetId", peer_records[1])
        self.assertNotIn("resolvedTargetId", peer_records[1])
        evidence = transport.evidence
        self.assertEqual(1, evidence["m19DecodeRequestCount"])
        self.assertEqual(1, evidence["m19DecodeAckCount"])

    def test_no_decision_closes_trial_without_sending_a_class(self):
        transport = QuestSelectionTcpServer(
            "127.0.0.1", 0, accept_timeout_seconds=3.0, ack_timeout_seconds=3.0
        ).start()
        peer_records = []

        def quest_peer():
            with socket.create_connection(("127.0.0.1", transport.port), timeout=3.0) as client:
                stream = client.makefile("rwb")
                selection = json.loads(stream.readline().decode("utf-8"))
                peer_records.append(selection)
                _line(stream, {
                    "protocolVersion": 1,
                    "messageType": "selection_ack",
                    "selectionId": selection["selectionId"],
                    "accepted": True,
                    "rejectionReason": "None",
                })

        worker = threading.Thread(target=quest_peer)
        worker.start()
        try:
            transport._ensure_connection()
            ack = transport.submit_m19_no_decision({
                "messageType": "eeg_decode_result",
                "selectionId": "no-decision-selection",
                "trialId": "no-decision-trial",
                "pageId": "m16-page-1",
                "pageEpoch": 4,
                "decisionMade": False,
                "classIndex": None,
                "accepted": False,
            })
        finally:
            transport.close()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertTrue(ack["accepted"])
        self.assertEqual("selection_abort", peer_records[0]["messageType"])
        self.assertFalse(peer_records[0]["decisionMade"])
        self.assertNotIn("predictedClassIndex", peer_records[0])
        self.assertNotIn("targetId", peer_records[0])


if __name__ == "__main__":
    unittest.main()
