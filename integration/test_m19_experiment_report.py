import json
import tempfile
import unittest
from pathlib import Path

from integration.m19_experiment_report import run_report_pipeline


class M19ExperimentReportTests(unittest.TestCase):
    def test_pipeline_creates_complete_empty_human_package(self):
        with tempfile.TemporaryDirectory() as root:
            summary, package = run_report_pipeline(Path(root))
            self.assertEqual(summary["status"], "PASS")
            self.assertEqual(summary["realHumanTrials"], 0)
            self.assertEqual(json.loads((package / "qc.json").read_text(encoding="utf-8"))["realHumanDataStatus"], "EMPTY_REAL_HUMAN_EEG")
            for name in ("summary.json", "qc.json", "fork-diagnostics.json", "benchmark.json", "benchmark.csv", "trial-table.csv", "episode-table.csv", "stopping-table.csv", "context-fusion-table.csv", "sandbox-summary.json", "summary.md"):
                self.assertTrue((package / name).is_file(), name)

    def test_pipeline_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first, package = run_report_pipeline(root)
            before = {path.name: path.read_bytes() for path in package.iterdir() if path.is_file()}
            second, _ = run_report_pipeline(root)
            after = {path.name: path.read_bytes() for path in package.iterdir() if path.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(first["m15TrialCount"], second["m15TrialCount"])
            self.assertNotIn("obj_", json.dumps(second, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
