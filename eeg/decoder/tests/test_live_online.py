import unittest
import numpy as np
from eeg.decoder.config import DecoderConfig
from eeg.decoder.pseudo_online import DecoderBackend
from eeg.decoder.live_online import LiveOnlineController
from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord

class LiveControllerTests(unittest.TestCase):
 def test_duplicate_start_and_one_final_decision(self):
  class ConstantBackend:
   name='constant'
   def predict(self,data): return 1,[.1,.9,.2]
  c=LiveOnlineController(ConstantBackend(),[0,1],config=DecoderConfig(analysis_duration_seconds=.1,onset_guard_seconds=.1)); a={'sessionId':'s','trialId':'t','estimatedGlobalSampleIndex':0}; self.assertTrue(c.start_trial(a)); self.assertFalse(c.start_trial(a))
  for i in range(40):
   m=EegPacketMetadata(0,i, '',10,8,1000,i); q=PacketContinuityRecord(i,i*10,'continuous',()); c.ingest_packet(m,q,np.random.default_rng(i).normal(size=(8,10)))
  result=c.stop_trial(); self.assertTrue(result['decisionMade']); self.assertEqual('target_center',result['finalDecisionLabel']); self.assertIsNone(c.stop_trial())
