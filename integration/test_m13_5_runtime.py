import tempfile
import unittest
from pathlib import Path

from integration.m13_5_analyzer import analyze_paths
from integration.m13_5_acceptance import acceptance_report
from integration.m13_5_runtime import MODE_ACTIVE, MODE_BASELINE, MODE_SHADOW
from integration.m13_5_streaming import run_fault_injection_acceptance, run_streaming_acceptance
from integration.m13_5_readiness import run_readiness


class M135RuntimeTests(unittest.TestCase):
    def test_streaming_cases_pass(self):
        with tempfile.TemporaryDirectory() as root:
            summary = run_streaming_acceptance(Path(root) / "streaming")
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(len(summary["cases"]), 7)
            self.assertTrue(summary["checks"]["shadowDoesNotAffectBaseline"])

    def test_fault_injection_cases_pass(self):
        with tempfile.TemporaryDirectory() as root:
            summary = run_fault_injection_acceptance(Path(root) / "faults")
            self.assertEqual(summary["status"], "PASS")
            self.assertTrue(all(item["pass"] for item in summary["results"]))
            self.assertEqual(len(summary["faultCatalog"]), 18)

    def test_analyzer_and_acceptance_reporter_consume_session_logs(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            run_streaming_acceptance(root / "streaming")
            paths = sorted((root / "streaming").glob("*/m13.5-session.jsonl"))
            summary, trials = analyze_paths(paths)
            self.assertEqual(summary["totalTrials"], 7)
            self.assertEqual(len(trials), 7)
            report = acceptance_report(paths)
            self.assertEqual(report["status"], "PASS")
            self.assertTrue(all(item["status"] == "PASS" for item in report["checks"]))

    def test_readiness_is_ready_with_explicit_hardware_warnings(self):
        summary = run_readiness()
        self.assertEqual(summary["status"], "READY")
        self.assertFalse(summary["hardwareBoundary"]["questOperated"])
        self.assertTrue(summary["dependencyAudit"]["legacyFbccaUsesScipy"])
        self.assertFalse(summary["dependencyAudit"]["pureNumpyFbccaUsesScipy"])


if __name__ == "__main__":
    unittest.main()
