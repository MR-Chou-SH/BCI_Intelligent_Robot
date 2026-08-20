import unittest
import numpy as np
from eeg.decoder.formal_online import channel_admission,generate_online_plan,technical_validity,replacements,evaluate_formal
class FormalOnlineTests(unittest.TestCase):
 def data(self,usable=5):
  a=np.zeros((8,1000));
  for c in (2,3,4,5,7)[:usable]: a[c]=np.linspace(0,1,1000)+c
  return a
 def test_channel_gate_five_four_ready_three_fails(self):
  self.assertEqual('READY',channel_admission(self.data(5))['verdict']); self.assertEqual('READY',channel_admission(self.data(4))['verdict']); self.assertEqual('CHANNEL CHECK FAILED',channel_admission(self.data(3))['verdict'])
 def test_channel_gate_rejects_rail_nonfinite_and_continuity(self):
  a=self.data(5); a[2]=375000; a[3,0]=np.nan; r=channel_admission(a,['continuous']); self.assertFalse(r['channels'][0]['usable']); self.assertFalse(r['channels'][1]['usable']); self.assertEqual('CHANNEL CHECK FAILED',channel_admission(self.data(5),['lost'])['verdict'])
 def test_plan_replacement_and_no_decision(self):
  p=generate_online_plan('s',4,'pilot_online')['trials']; v=[{'trialId':p[0]['trialId'],'technicalStatus':'technical_invalid','reason':'stale_sync'}]; x=replacements(p,v); self.assertEqual(p[0]['targetId'],x['records'][0]['replacementTarget']); self.assertFalse(x['abort']); self.assertTrue(replacements(p,v*4)['abort'])
 def test_evaluator_denominators_and_no_decision(self):
  p=generate_online_plan('s',4,'pilot_online')['trials']; o=[]
  for i,t in enumerate(p): o.append({'trialId':t['trialId'],'startAssociationValid':True,'stopAssociationValid':True,'finalDecisionLabel':t['targetId'] if i<2 else None})
  o[2]['technicalReason']='stale_sync'; r=evaluate_formal(p,o); self.assertEqual(8,r['technicalValid']); self.assertEqual(2,r['decisions']); self.assertEqual(6,r['noDecision']); self.assertEqual(.25,r['accuracy']); self.assertEqual(1.0,r['accuracyAmongDecisions'])
if __name__=='__main__': unittest.main()
