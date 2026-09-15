import json
import unittest

from integration.m13_dynamic_stopping import M13_FUSED_EVIDENCE_THRESHOLD, M13_MARGIN_THRESHOLD, M13_REQUIRED_CONSECUTIVE
from integration.m12_context_eeg_fusion import LAMBDA_CONTEXT
from integration.m15_comparative_benchmark import run_benchmark
from integration.m18_research_sandboxes import (
    M18_LAMBDA_CANDIDATES,
    M18_PROVENANCE,
    ParticipantSessionAdaptationConfig,
    UserCorrectionEvidence,
    run_all_sandboxes,
    synthetic_acceptance,
)


class M18ResearchSandboxesTests(unittest.TestCase):
    def test_synthetic_acceptance_and_defaults(self):
        result = synthetic_acceptance()
        self.assertEqual(result["status"], "PASS", result)
        report = result["report"]
        self.assertEqual(report["provenance"], M18_PROVENANCE)
        self.assertEqual(report["productionDefaults"], {"m12Lambda": LAMBDA_CONTEXT, "m13FusedThreshold": M13_FUSED_EVIDENCE_THRESHOLD, "m13MarginThreshold": M13_MARGIN_THRESHOLD, "m13RequiredConsecutive": M13_REQUIRED_CONSECUTIVE})
        self.assertEqual(report["contextWeight"]["candidateLambdas"], list(M18_LAMBDA_CANDIDATES))

    def test_sandboxes_are_deterministic_and_privacy_safe(self):
        _summary, trials = run_benchmark()
        left = run_all_sandboxes(trials)
        right = run_all_sandboxes(trials)
        self.assertEqual(left, right)
        self.assertNotIn("obj_", json.dumps(left, sort_keys=True))
        self.assertFalse(left["acceptance"]["selectionPerformed"])

    def test_future_contracts_validate_without_production_opt_in(self):
        config = ParticipantSessionAdaptationConfig("p", "s")
        self.assertTrue(config.validate())
        correction = UserCorrectionEvidence("selection", "block_sim_01", None, "not-recorded")
        self.assertEqual(correction.provenance, M18_PROVENANCE)


if __name__ == "__main__":
    unittest.main()
