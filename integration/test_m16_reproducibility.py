import json
import tempfile
import unittest
from pathlib import Path

from integration.m16_reproducibility import build_schedule, run_dry_run


class M16ReproducibilityTests(unittest.TestCase):
    def test_dry_run_composes_m14_m15_and_m13_5(self):
        with tempfile.TemporaryDirectory() as root:
            report, session_root = run_dry_run(Path(root))
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["m14"]["completedEpisodes"], 3)
            self.assertEqual(report["m14"]["m10Commits"], 12)
            self.assertEqual(report["m15"]["trialCount"], 12)
            self.assertEqual(report["m13_5"]["acceptanceStatus"], "PASS")
            self.assertTrue((session_root / "manifest.json").is_file())
            self.assertTrue((session_root / "m15" / "m15-condition-metrics.csv").is_file())
            self.assertNotIn("obj_", json.dumps(report, sort_keys=True))

    def test_schedule_is_deterministic_and_manifest_has_no_sensitive_identity(self):
        self.assertEqual(build_schedule(), build_schedule())
        with tempfile.TemporaryDirectory() as root:
            report, _session_root = run_dry_run(Path(root))
        self.assertEqual(report["manifest"]["pseudonymousParticipantId"], "development-synthetic-no-participant")
        self.assertFalse(report["manifest"]["hardwareMetadata"]["questOperated"])

    def test_resume_does_not_duplicate_or_overwrite_completed_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            first, session_root = run_dry_run(root)
            before = {
                "m14": (session_root / "m14" / "episodes.jsonl").read_text(encoding="utf-8"),
                "m15": (session_root / "m15" / "m15-trials.jsonl").read_text(encoding="utf-8"),
            }
            second, _ = run_dry_run(root)
            after = {
                "m14": (session_root / "m14" / "episodes.jsonl").read_text(encoding="utf-8"),
                "m15": (session_root / "m15" / "m15-trials.jsonl").read_text(encoding="utf-8"),
            }
        self.assertEqual(before, after)
        self.assertFalse(second["resume"]["manifestCreated"])
        self.assertFalse(second["resume"]["m14Written"])


if __name__ == "__main__":
    unittest.main()
