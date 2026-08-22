import importlib.util
import json
import socket
import unittest


spec = importlib.util.spec_from_file_location("sender", "integration/m8_selection_transport/simulated_selection_sender.py")
sender = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sender)


class SimulatedSelectionSenderTests(unittest.TestCase):
    def test_labels_normalize_to_canonical_slots(self):
        self.assertEqual(0, sender.parse_class("LEFT"))
        self.assertEqual(1, sender.parse_class("center"))
        self.assertEqual(2, sender.parse_class("target_right"))
        self.assertEqual(2, sender.parse_class("2"))

    def test_decision_message_contains_only_class_not_target(self):
        payload = sender.message("eeg_selection", "selection-1", 1)
        self.assertEqual(1, payload["protocolVersion"])
        self.assertEqual("eeg_selection", payload["messageType"])
        self.assertEqual("selection-1", payload["selectionId"])
        self.assertEqual(1, payload["predictedClassIndex"])
        self.assertNotIn("targetId", payload)
        self.assertIn("pcMonotonicNs", payload)
        self.assertIn("pcUtc", payload)

    def test_sender_uses_utf8_newline_delimited_json(self):
        sender_socket, quest_socket = socket.socketpair()
        with sender_socket, quest_socket:
            sender.send_line(sender_socket, sender.message("selection_open", "selection-2"))
            line = quest_socket.recv(4096)
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual("selection-2", json.loads(line.decode("utf-8"))["selectionId"])


if __name__ == "__main__":
    unittest.main()
