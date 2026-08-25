import importlib.util
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


if __name__ == "__main__":
    unittest.main()
