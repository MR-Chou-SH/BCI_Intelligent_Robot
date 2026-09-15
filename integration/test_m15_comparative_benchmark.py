import json
import tempfile
import unittest
from pathlib import Path

from integration.m15_comparative_benchmark import (
    M15_CONDITIONS,
    acceptance_checks,
    run_benchmark,
    write_outputs,
)


class M15ComparativeBenchmarkTests(unittest.TestCase):
    def test_paired_conditions_and_metrics_pass(self):
        summary, trials = run_benchmark()
        checks = acceptance_checks(summary, trials)
        self.assertTrue(all(checks.values()), checks)
        self.assertEqual(summary["trialCount"], 12)
        self.assertEqual(tuple(summary["conditions"]), M15_CONDITIONS)
        self.assertIn("earlyStopCount", summary["conditionMetrics"][M15_CONDITIONS[2]])
        self.assertIn("agencyMetrics", summary)

    def test_outputs_are_machine_readable_jsonl_and_csv(self):
        with tempfile.TemporaryDirectory() as root:
            summary, _trials = write_outputs(Path(root))
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(len((Path(root) / "m15-trials.jsonl").read_text(encoding="utf-8").splitlines()), 12)
            self.assertGreater(len((Path(root) / "m15-condition-metrics.csv").read_text(encoding="utf-8").splitlines()), 1)
            json.loads((Path(root) / "m15-summary.json").read_text(encoding="utf-8"))

    def test_no_context_only_early_stop(self):
        summary, _trials = run_benchmark()
        self.assertEqual(summary["agencyMetrics"]["contextOnlyEarlyStopCount"], 0)
        self.assertTrue(summary["agencyMetrics"]["noContextOnlyVeto"])


if __name__ == "__main__":
    unittest.main()
