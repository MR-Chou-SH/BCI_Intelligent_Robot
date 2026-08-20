import tempfile,unittest
from pathlib import Path
from eeg.decoder.live_diagnostic import prepare_manifest,FILES
class DiagnosticTests(unittest.TestCase):
 def test_prepares_complete_ground_truth_isolated_schema(self):
  with tempfile.TemporaryDirectory() as d:
   m=prepare_manifest(Path(d)/'s','s','abc',[2,3,4,5,7]); self.assertFalse(m['groundTruthLeakage']); self.assertTrue(all((Path(d)/'s'/x).exists() for x in FILES))
