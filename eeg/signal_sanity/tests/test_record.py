import json
from tempfile import TemporaryDirectory
import unittest

from eeg.signal_sanity.record import PhaseJsonl, _countdown_messages


class SignalSanityRecordingTests(unittest.TestCase):
    def test_phase_jsonl_keeps_preparation_out_of_formal_evidence(self):
        with TemporaryDirectory() as root:
            log = PhaseJsonl(root + "/preparation.jsonl", root + "/formal.jsonl")
            log.append({"phase": "preparation"})
            log.begin_formal()
            log.append({"phase": "formal"})
            with open(root + "/preparation.jsonl", encoding="utf-8") as stream:
                self.assertEqual("preparation", json.loads(stream.readline())["phase"])
            with open(root + "/formal.jsonl", encoding="utf-8") as stream:
                self.assertEqual("formal", json.loads(stream.readline())["phase"])
            self.assertEqual((1, 1), (log.preparation_count, log.formal_count))

    def test_countdown_exposes_required_visible_checkpoints(self):
        messages = _countdown_messages(13)
        for expected in ("13", "10", "5", "3", "2", "1"):
            self.assertTrue(any(expected in message for message in messages))


if __name__ == "__main__":
    unittest.main()
