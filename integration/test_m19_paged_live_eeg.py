import json
from pathlib import Path
import unittest

from integration.m19_paged_live_eeg import (
    M19ProtocolError,
    M19TrialRegistry,
    validate_decode_request,
)


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "m7_unity6000"
    / "Assets"
    / "Resources"
    / "BCI"
    / "M19"
    / "m19_decode_request_v1.json"
)


def _payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class M19DecodeRequestContractTests(unittest.TestCase):
    def test_shared_fixture_round_trips_without_target_inference(self):
        payload = _payload()
        request = validate_decode_request(payload)
        self.assertEqual("m19-contract-selection-001", request.selection_id)
        self.assertEqual(("m9-vblock-yellow-01", "m9-vblock-blue-01", "m9-vblock-green-01"),
                         tuple(slot.target_id for slot in request.slots))
        self.assertEqual(payload, request.to_payload())
        self.assertFalse(any("expected" in key.lower() for key in request.to_payload()))

    def test_one_two_and_three_active_slots_allow_noncontiguous_class_indices(self):
        base = _payload()
        for indices in ((0,), (0, 2), (0, 1, 2)):
            payload = dict(base)
            payload["activeSlotCount"] = len(indices)
            payload["slots"] = [base["slots"][index] for index in indices]
            request = validate_decode_request(payload)
            self.assertEqual(indices, tuple(slot.slot_index for slot in request.slots))

    def test_slot_frequency_and_active_count_must_match_frozen_slots(self):
        payload = _payload()
        payload["slots"][1]["frequencyHz"] = 12.0
        with self.assertRaises(M19ProtocolError):
            validate_decode_request(payload)

        payload = _payload()
        payload["activeSlotCount"] = 2
        with self.assertRaises(M19ProtocolError):
            validate_decode_request(payload)

    def test_duplicate_or_empty_target_and_task_truth_fields_fail_closed(self):
        payload = _payload()
        payload["slots"][1]["targetId"] = payload["slots"][0]["targetId"]
        with self.assertRaises(M19ProtocolError):
            validate_decode_request(payload)

        payload = _payload()
        payload["expectedTargetId"] = "m9-vblock-blue-01"
        with self.assertRaises(M19ProtocolError):
            validate_decode_request(payload)


class M19TrialRegistryTests(unittest.TestCase):
    def test_one_active_trial_and_unique_selection_id(self):
        registry = M19TrialRegistry()
        request = _payload()
        accepted = registry.accept_request(request)
        self.assertTrue(accepted["accepted"])
        busy = dict(request, selectionId="m19-other-selection", trialId="m19-other-trial")
        self.assertFalse(registry.accept_request(busy)["accepted"])
        result = registry.resolve(request["selectionId"], 1, page_id=request["pageId"], page_epoch=7)
        self.assertTrue(result["accepted"])
        self.assertEqual(1, result["classIndex"])
        self.assertNotIn("targetId", result)
        self.assertFalse(registry.accept_request(request)["accepted"])

    def test_abort_invalidates_late_result_and_allows_next_trial(self):
        registry = M19TrialRegistry()
        first = _payload()
        registry.accept_request(first)
        aborted = registry.abort(first["selectionId"], "page_changed")
        self.assertTrue(aborted["accepted"])
        late = registry.resolve(first["selectionId"], 0, page_id=first["pageId"], page_epoch=7)
        self.assertFalse(late["accepted"])
        self.assertEqual("STALE_SELECTION_REJECTED", late["rejectionReason"])
        second = dict(first, selectionId="m19-next-selection", trialId="m19-next-trial", pageEpoch=8)
        self.assertTrue(registry.accept_request(second)["accepted"])

    def test_page_epoch_mismatch_and_inactive_class_are_rejected(self):
        registry = M19TrialRegistry()
        request = _payload()
        registry.accept_request(request)
        stale = registry.resolve(request["selectionId"], 1, page_id=request["pageId"], page_epoch=6)
        self.assertFalse(stale["accepted"])
        self.assertEqual("STALE_SELECTION_REJECTED", stale["rejectionReason"])

        reduced = dict(request, selectionId="m19-reduced-selection", trialId="m19-reduced-trial", activeSlotCount=1,
                       slots=[request["slots"][0]])
        self.assertTrue(registry.accept_request(reduced)["accepted"])
        inactive = registry.resolve(reduced["selectionId"], 1, page_id=reduced["pageId"], page_epoch=7)
        self.assertFalse(inactive["accepted"])
        self.assertEqual("inactive_slot", inactive["rejectionReason"])

    def test_no_decision_is_terminal_without_a_class(self):
        registry = M19TrialRegistry()
        request = _payload()
        registry.accept_request(request)
        result = registry.resolve(request["selectionId"], None, decision_made=False,
                                  page_id=request["pageId"], page_epoch=7)
        self.assertFalse(result["accepted"])
        self.assertFalse(result["decisionMade"])
        self.assertEqual("no_decision", result["rejectionReason"])
        self.assertIsNone(registry.active_request)


if __name__ == "__main__":
    unittest.main()
