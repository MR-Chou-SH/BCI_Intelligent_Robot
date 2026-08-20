import json,tempfile,unittest
from pathlib import Path
from eeg.decoder.live_diagnostic import prepare_manifest,FILES,generate_diagnostic_plan,verify_diagnostic_evidence
class DiagnosticTests(unittest.TestCase):
 def test_prepares_complete_ground_truth_isolated_schema(self):
  with tempfile.TemporaryDirectory() as d:
   m=prepare_manifest(Path(d)/'s','s','abc',[2,3,4,5,7]); self.assertFalse(m['groundTruthLeakage']); self.assertTrue(all((Path(d)/'s'/x).exists() for x in FILES))
 def test_explicit_diagnostic_plan_is_strict_three_target_mapping(self):
  plan=generate_diagnostic_plan('s'); self.assertEqual('diagnostic_live',plan['planMode']); self.assertEqual(['target_left','target_center','target_right'],[x['targetId'] for x in plan['trials']])
 def test_diagnostic_verifier_never_uses_formal_30_trial_gate(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)/'s'; prepare_manifest(root,'s','abc',[2,3,4,5,7]); plan=generate_diagnostic_plan('s')
   events=[]; associations=[]
   for trial in plan['trials']:
    for kind in ('stimulus_started_software','stimulus_stopped_software'):
     events.append({'originalQuestEvent':{'trialId':trial['trialId'],'eventType':kind}}); associations.append({'trialId':trial['trialId'],'stimulusEventType':kind,'associationValid':True})
   (root/'quest-events.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in events),encoding='utf-8')
   (root/'associations.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in associations),encoding='utf-8')
   (root/'decisions.jsonl').write_text(''.join(json.dumps({'trialId':x['trialId'],'decisionMade':False})+'\n' for x in plan['trials']),encoding='utf-8')
   self.assertEqual('complete',verify_diagnostic_evidence(root)['status'])
