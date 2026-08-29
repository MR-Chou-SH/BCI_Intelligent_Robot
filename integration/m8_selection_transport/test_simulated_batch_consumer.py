import importlib.util
import json
import socket
import threading
import time
import unittest


spec = importlib.util.spec_from_file_location("consumer", "integration/m8_selection_transport/simulated_batch_consumer.py")
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


def valid_message():
    return {
        "protocolVersion": 1,
        "messageType": "target_batch_confirmed",
        "confirmedBatch": {
            "batchId": "m8-batch-0001",
            "groupId": "m8-group-0001",
            "groupIndex": 1,
            "selections": [
                {"targetId": "target-a", "slotIndex": 0},
                {"targetId": "target-c", "slotIndex": 2},
            ],
        },
    }


class SimulatedBatchConsumerTests(unittest.TestCase):
    def test_consume_one_batch_binds_the_existing_port_and_returns_matching_ack(self):
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        reservation.close()
        receipts = []
        worker = threading.Thread(
            target=lambda: receipts.append(consumer.consume_one_batch("127.0.0.1", port, 1.0))
        )
        worker.start()

        deadline = time.monotonic() + 1.0
        while True:
            try:
                connection = socket.create_connection(("127.0.0.1", port), timeout=0.1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    self.fail("batch consumer did not bind the released selection port")
                time.sleep(0.01)
        with connection:
            connection.sendall((json.dumps(valid_message()) + "\n").encode("utf-8"))
            ack = json.loads(connection.recv(4096).decode("utf-8"))

        worker.join(1.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual("m8-batch-0001", ack["batchId"])
        self.assertTrue(receipts[0].downstream_accepted)

    def test_validates_ordered_nonempty_batch(self):
        batch = consumer.validate_batch_message(valid_message())
        self.assertEqual(["target-a", "target-c"], [item["targetId"] for item in batch["selections"]])

    def test_rejects_empty_or_wrong_message(self):
        empty = valid_message()
        empty["confirmedBatch"]["selections"] = []
        with self.assertRaises(ValueError):
            consumer.validate_batch_message(empty)
        wrong = valid_message()
        wrong["messageType"] = "selection_ack"
        with self.assertRaises(ValueError):
            consumer.validate_batch_message(wrong)

    def test_duplicate_batch_is_accepted_downstream_once_but_acked_each_time(self):
        receiver = consumer.BatchIdempotentConsumer()

        first = receiver.accept(valid_message())
        duplicate = receiver.accept(valid_message())

        self.assertTrue(first.downstream_accepted)
        self.assertFalse(duplicate.downstream_accepted)
        self.assertEqual(["m8-batch-0001"], receiver.accepted_batch_ids)
        self.assertEqual(
            {"protocolVersion": 1, "messageType": "batch_ack", "batchId": "m8-batch-0001"},
            first.ack,
        )
        self.assertEqual(first.ack, duplicate.ack)


if __name__ == "__main__":
    unittest.main()
