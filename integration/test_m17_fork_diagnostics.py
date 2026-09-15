import json
import unittest

from integration.m17_fork_diagnostics import (
    DIAGNOSTIC_LABELS,
    diagnose_trial,
    diagnose_trials,
    run_synthetic_acceptance,
    synthetic_validation_sessions,
)


class M17ForkDiagnosticsTests(unittest.TestCase):
    def test_m17_synthetic_patterns_pass(self):
        result = run_synthetic_acceptance()
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(all(result["cases"].values()), result)


    def test_m17_report_is_deterministic_and_privacy_safe(self):
        trials = synthetic_validation_sessions()
        left = diagnose_trials(trials, "SYNTHETIC")
        right = diagnose_trials(trials, "SYNTHETIC")
        self.assertEqual(left, right)
        self.assertNotIn("obj_", json.dumps(left, sort_keys=True))
        self.assertEqual(set(left["labelCounts"]), set(DIAGNOSTIC_LABELS))


    def test_m17_preserves_trajectory_and_task_fields(self):
        diagnostic = diagnose_trial(synthetic_validation_sessions()[0])
        self.assertEqual(diagnostic["metrics"]["windowCount"], 4)
        self.assertEqual(diagnostic["normalizedTrial"]["expectedTarget"], "A")
        self.assertTrue(diagnostic["metrics"]["shortVsFullDisagreement"])


if __name__ == "__main__":
    unittest.main()
