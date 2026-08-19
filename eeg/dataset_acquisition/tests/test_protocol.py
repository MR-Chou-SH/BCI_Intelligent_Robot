import json
from collections import Counter
from tempfile import TemporaryDirectory
import unittest

from eeg.dataset_acquisition.plan import TARGETS, generate_trial_plan, write_ground_truth
from eeg.dataset_acquisition.state import TrialPhase, TrialStateMachine
from eeg.dataset_acquisition.verifier import verify_session


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record) + "\n")


class DatasetProtocolTests(unittest.TestCase):
    def test_seed_is_reproducible_balanced_and_constrained(self):
        first = generate_trial_plan("s", 42)
        second = generate_trial_plan("s", 42)
        other = generate_trial_plan("s", 43)
        order = [item.targetId for item in first]
        self.assertEqual(order, [item.targetId for item in second])
        self.assertNotEqual(order, [item.targetId for item in other])
        self.assertEqual(Counter(order), Counter({target: 10 for target in TARGETS}))
        self.assertLessEqual(max(_run_lengths(order)), 2)
        self.assertEqual(list(first[0].plannedOrder), order)

    def test_state_machine_enforces_trial_lifecycle_breaks_and_abort(self):
        machine = TrialStateMachine(["t1", "t2"], break_after=(1,))
        self.assertEqual(TrialPhase.SESSION_PREPARATION, machine.phase)
        machine.start_trial(); machine.begin_stimulation(); machine.end_stimulation()
        self.assertEqual(TrialPhase.BREAK, machine.finish_trial().toPhase and machine.phase)
        machine.start_trial(); machine.begin_stimulation(); machine.abort("user_requested")
        self.assertEqual(TrialPhase.ABORTED, machine.phase)

    def test_verifier_accepts_perfect_synthetic_structure(self):
        with TemporaryDirectory() as root:
            session_id = "s"
            plan = generate_trial_plan(session_id, 7)
            protocol = {"stimulusSeconds": 4.0}
            write_ground_truth(root + "/trial-ground-truth.jsonl", plan, protocol)
            with open(root + "/session-manifest.json", "w", encoding="utf-8") as stream:
                json.dump({"randomSeed": 7}, stream)
            events, associations = [], []
            for item in plan:
                events.extend([{"originalQuestEvent": {"trialId": item.trialId, "eventType": "stimulus_started_software"}},
                               {"originalQuestEvent": {"trialId": item.trialId, "eventType": "stimulus_stopped_software"}}])
                for kind in ("stimulus_started_software", "stimulus_stopped_software"):
                    associations.append({"trialId": item.trialId, "stimulusEventType": kind, "associationValid": True,
                                         "hardwareTimingVerified": False, "sampleIndexKind": "software_derived_estimate"})
            write_jsonl(root + "/pc-stimulus-events.jsonl", events)
            write_jsonl(root + "/derived-association.jsonl", associations)
            write_jsonl(root + "/packet-metadata.jsonl", [{"recordType": "nd8_packet_metadata"}])
            write_jsonl(root + "/raw-eeg-packets.jsonl", [{"recordType": "nd8_raw_packet", "packetSequence": 0}])
            open(root + "/nd8-association-gate.jsonl", "w", encoding="utf-8").close()
            result = verify_session(root)
            self.assertEqual("complete", result["status"])
            self.assertFalse(result["classificationPerformed"])

    def test_verifier_rejects_missing_trial_and_duplicate_event(self):
        with TemporaryDirectory() as root:
            plan = generate_trial_plan("s", 7)[:-1]
            write_ground_truth(root + "/trial-ground-truth.jsonl", plan, {})
            with open(root + "/session-manifest.json", "w", encoding="utf-8") as stream:
                json.dump({"randomSeed": 7}, stream)
            write_jsonl(root + "/pc-stimulus-events.jsonl", [])
            write_jsonl(root + "/derived-association.jsonl", [])
            write_jsonl(root + "/packet-metadata.jsonl", [])
            result = verify_session(root)
            self.assertEqual("incomplete", result["status"])
            self.assertIn("expected_30_ground_truth_trials", result["errors"])


def _run_lengths(values):
    result, current, length = [], None, 0
    for value in values:
        if value == current:
            length += 1
        else:
            if length:
                result.append(length)
            current, length = value, 1
    if length:
        result.append(length)
    return result


if __name__ == "__main__":
    unittest.main()
