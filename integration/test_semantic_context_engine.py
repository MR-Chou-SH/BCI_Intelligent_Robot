"""Focused offline tests for the M28 semantic Context boundary."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from integration.semantic_context_engine import SemanticContextEngine


BENCHMARK_PATH = (
    Path(__file__).resolve().parents[1]
    / "research_analysis"
    / "m28_real_semantic_context_20261002"
    / "attempt-01"
    / "semantic_context_benchmark_v1.json"
)


class FakeContextClient:
    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.messages: list[list[dict[str, str]]] = []

    def complete(self, messages, *, model_id, temperature, max_output_tokens):
        self.messages.append(messages)
        if not self.responses:
            raise AssertionError("unexpected model call")
        return {"content": json.dumps(self.responses.pop(0), ensure_ascii=False), "usage": {}}


def _case(case_id: str) -> dict:
    document = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    return deepcopy(next(case["model_input"] for case in document["cases"] if case["case_id"] == case_id))


def _response(context: dict, top_id: str, relation: str, *, status: str = "informative") -> dict:
    return {
        "status": status,
        "reason_code": "fixture_response",
        "candidate_scores": [
            {
                "candidate_id": candidate_id,
                "semantic_score": 0.92 if candidate_id == top_id else 0.10,
                "relation_type": relation if candidate_id == top_id else "none",
            }
            for candidate_id in context["candidate_next_object_ids"]
        ],
    }


class SemanticContextEngineTests(unittest.TestCase):
    def test_phone_charger_accepts_shared_core_phone_alias(self):
        context = _case("m20_phone_charge")
        client = FakeContextClient(_response(context, "assist_wireless_charger", "charge"))
        result = SemanticContextEngine(client, "fake-model").predict(context)

        self.assertEqual(result["status"], "informative")
        self.assertEqual(result["top_candidate"], "assist_wireless_charger")
        self.assertEqual(result["coverage_decision"], "active")
        self.assertEqual(len(client.messages), 1)

    def test_full_container_is_rejected_before_model_call(self):
        context = _case("toy_bin_full")
        client = FakeContextClient()
        result = SemanticContextEngine(client, "fake-model").predict(context)

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "required_relation_incompatible_with_scene")
        self.assertEqual(client.messages, [])

    def test_closed_but_openable_container_remains_a_valid_place_in_target(self):
        context = _case("m20_medicine_store_closed")
        client = FakeContextClient(_response(context, "assist_storage_box", "place_in"))
        result = SemanticContextEngine(client, "fake-model").predict(context)

        self.assertEqual(result["status"], "informative")
        self.assertEqual(result["top_candidate"], "assist_storage_box")
        self.assertEqual(result["coverage_decision"], "active")

    def test_explicit_destination_must_be_in_candidate_set(self):
        context = _case("adversarial_book_wrong_surface")
        context["candidate_next_object_ids"].remove("bookshelf_01")
        client = FakeContextClient()
        result = SemanticContextEngine(client, "fake-model").predict(context)

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reason_code"], "requested_destination_not_candidate")
        self.assertEqual(client.messages, [])

    def test_active_response_must_rank_the_explicit_relation_first(self):
        context = _case("m20_phone_charge")
        client = FakeContextClient(_response(context, "assist_user_zone", "handover"))
        result = SemanticContextEngine(client, "fake-model", max_correction_retries=0).predict(context)

        self.assertEqual(result["status"], "context_off")
        self.assertEqual(result["reason_code"], "model_output_invalid_after_bounded_retry")
        self.assertEqual(result["provenance"]["attempt_count"], 1)

    def test_request_contains_only_engine_input_not_evaluation_labels(self):
        context = _case("m20_phone_charge")
        client = FakeContextClient(_response(context, "assist_wireless_charger", "charge"))
        SemanticContextEngine(client, "fake-model").predict(context)
        sent = json.loads(client.messages[0][1]["content"])

        self.assertEqual(sent["context_input"], context)
        self.assertNotIn("evaluation_only", sent)
        self.assertNotIn("expected_target_ids", sent)
        self.assertNotIn("expected_top_candidate", sent)

    def test_invalid_output_retries_once_then_fails_closed(self):
        context = _case("m20_phone_charge")
        malformed = {
            "status": "informative",
            "reason_code": "bad_ids",
            "candidate_scores": [{"candidate_id": "invented", "semantic_score": 1.0, "relation_type": "charge"}],
        }
        client = FakeContextClient(malformed, malformed)
        result = SemanticContextEngine(client, "fake-model").predict(context)

        self.assertEqual(result["status"], "context_off")
        self.assertEqual(result["reason_code"], "model_output_invalid_after_bounded_retry")
        self.assertEqual(result["provenance"]["attempt_count"], 2)
        self.assertEqual(len(client.messages), 2)


if __name__ == "__main__":
    unittest.main()
