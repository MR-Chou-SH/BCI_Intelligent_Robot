import tempfile
import unittest
from pathlib import Path

from integration.m15_historical_replay import run_historical_comparison


HISTORICAL = Path(r"D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json")


class M15HistoricalReplayTests(unittest.TestCase):
    @unittest.skipUnless(HISTORICAL.is_file(), "registered historical M6.5b fixture is unavailable")
    def test_registered_fixture_runs_descriptive_paired_comparison(self):
        summary, trials = run_historical_comparison(HISTORICAL)
        self.assertEqual(summary["status"], "PASS")
        self.assertEqual(summary["trialCount"], 89)
        self.assertEqual(len(trials), 89)
        self.assertEqual(summary["conditionMetrics"]["C_CONTEXT_EEG_DYNAMIC_STOP"]["trialCount"], 89)


if __name__ == "__main__":
    unittest.main()
