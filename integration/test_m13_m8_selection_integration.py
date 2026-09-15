import unittest

from integration.m13_m8_selection_integration import (
    dynamic_stopping_decision_to_m8_final_decision,
    submit_dynamic_stopping_decision,
)
from integration.m13_trajectory_fixture import make_snapshot
from integration.m13_dynamic_stopping import run_dynamic_stopping
from integration.m8_selection_orchestration import M8SelectionOrchestrator


class FakeQuestTransport:
    def __init__(self):
        self.opened = []
        self.submitted = []
        self.aborted = []

    def _ack(self, selection_id, accepted=True):
        return {"protocolVersion": 1, "messageType": "selection_ack", "selectionId": selection_id, "accepted": accepted}

    def open_selection(self, selection_id):
        self.opened.append(selection_id)
        return self._ack(selection_id)

    def submit_eeg_selection(self, selection_id, class_index):
        self.submitted.append((selection_id, class_index))
        return self._ack(selection_id)

    def abort_selection(self, selection_id):
        self.aborted.append(selection_id)
        return self._ack(selection_id)


def _early_decision():
    return run_dynamic_stopping([
        make_snapshot(0, (2.0, .01, .01), (.98, .005, .005)),
        make_snapshot(1, (2.0, .01, .01), (.98, .005, .005)),
    ])[1]


def _no_decision():
    return run_dynamic_stopping([make_snapshot(0, (1.0, 1.0, .1))])[1]


class M13M8IntegrationTests(unittest.TestCase):
    def test_early_decision_enters_existing_seam_once(self):
        transport = FakeQuestTransport()
        orchestrator = M8SelectionOrchestrator(transport)
        self.assertTrue(orchestrator.open_selection("m13-selection-01", "m13-trial-01"))
        decision = _early_decision()
        result = submit_dynamic_stopping_decision(orchestrator, "m13-trial-01", decision, "m13-session")
        self.assertEqual(result["eventType"], "eeg_selection_ack")
        self.assertEqual(transport.submitted, [("m13-selection-01", 0)])
        duplicate = submit_dynamic_stopping_decision(orchestrator, "m13-trial-01", decision, "m13-session")
        self.assertEqual(duplicate["status"], "duplicate_final_decision")
        self.assertEqual(len(transport.submitted), 1)

    def test_no_decision_aborts_without_fabricated_selection(self):
        transport = FakeQuestTransport()
        orchestrator = M8SelectionOrchestrator(transport)
        self.assertTrue(orchestrator.open_selection("m13-selection-02", "m13-trial-02"))
        decision = _no_decision()
        converted = dynamic_stopping_decision_to_m8_final_decision(decision, "m13-trial-02")
        self.assertFalse(converted["decisionMade"])
        self.assertIsNone(converted["finalDecisionLabel"])
        result = submit_dynamic_stopping_decision(orchestrator, "m13-trial-02", decision)
        self.assertEqual(result["status"], "no_decision")
        self.assertEqual(transport.submitted, [])
        self.assertEqual(transport.aborted, ["m13-selection-02"])

    def test_identity_is_frozen_slot_label_only(self):
        converted = dynamic_stopping_decision_to_m8_final_decision(_early_decision(), "m13-trial-03")
        self.assertEqual(converted["finalDecisionLabel"], "target_left")
        self.assertNotIn("obj_", repr(converted))
        self.assertEqual(converted["m13SelectedTargetId"], "m9-vblock-red-01")


if __name__ == "__main__":
    unittest.main()
