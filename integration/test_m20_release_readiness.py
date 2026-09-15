import json
import tempfile
import unittest
from pathlib import Path

from integration.m20_release_readiness import M20_PRODUCTION_DEFAULTS, audit_environment, canonical_commands, release_readiness_report


class M20ReleaseReadinessTests(unittest.TestCase):
    def test_environment_and_commands_are_ready_without_install(self):
        environment = audit_environment()
        self.assertEqual(environment["status"], "PASS", environment)
        self.assertTrue(environment["noInstallPerformed"])
        self.assertFalse(environment["m13DefaultPathUsesScipy"])
        self.assertIn("m19Report", canonical_commands())

    def test_full_dry_run_and_recovery_pass(self):
        with tempfile.TemporaryDirectory() as root:
            report = release_readiness_report(Path(root) / "run")
        self.assertEqual(report["status"], "PASS", report)
        self.assertTrue(report["productionDefaultsUnchanged"])
        self.assertTrue(all(report["dryRun"]["recovery"].values()))
        self.assertFalse(report["dryRun"]["hardware"]["questOperated"])
        self.assertNotIn("obj_", json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
