import unittest
from threading import Event, Thread
import numpy as np
from eeg.decoder.config import DecoderConfig
from eeg.decoder.pseudo_online import DecoderBackend
from eeg.decoder.live_online import LiveOnlineController
from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord

class LiveControllerTests(unittest.TestCase):
 def packet(self, sequence, first, count=200, status='continuous'):
  return (EegPacketMetadata(0,sequence,'',count,8,1000,sequence),
          PacketContinuityRecord(sequence,first,status,()),np.ones((8,count)))
 def test_duplicate_start_and_one_final_decision(self):
  class ConstantBackend:
   name='constant'
   def predict(self,data): return 1,[.1,.9,.2]
  c=LiveOnlineController(ConstantBackend(),[0,1],config=DecoderConfig(analysis_duration_seconds=.1,onset_guard_seconds=.1)); a={'sessionId':'s','trialId':'t','estimatedGlobalSampleIndex':0}; self.assertTrue(c.start_trial(a)); self.assertFalse(c.start_trial(a))
  for i in range(40):
   m=EegPacketMetadata(0,i, '',10,8,1000,i); q=PacketContinuityRecord(i,i*10,'continuous',()); c.ingest_packet(m,q,np.random.default_rng(i).normal(size=(8,10)))
  result=c.stop_trial(); self.assertTrue(result['decisionMade']); self.assertEqual('target_center',result['finalDecisionLabel']); self.assertEqual(2,len(result['predictionTimeline'])); self.assertIsNone(c.stop_trial())

 def test_duplicate_packet_and_continuity_loss_are_safe(self):
  class ConstantBackend:
   name='constant'
   def predict(self,data): return 0,[.9,.1,.2]
  cfg=DecoderConfig(analysis_duration_seconds=.1,onset_guard_seconds=.1); c=LiveOnlineController(ConstantBackend(),[0,1],config=cfg); c.start_trial({'sessionId':'s','trialId':'x','estimatedGlobalSampleIndex':0})
  m=EegPacketMetadata(0,0,'',200,8,1000,0); q=PacketContinuityRecord(0,0,'continuous',()); c.ingest_packet(m,q,np.ones((8,200))); self.assertEqual([],c.ingest_packet(m,q,np.ones((8,200))))
  m2=EegPacketMetadata(0,1,'',200,8,1000,1); q2=PacketContinuityRecord(1,200,'continuity_lost',('packet_gap',)); c.ingest_packet(m2,q2,np.ones((8,200))); self.assertFalse(c.stop_trial()['decisionMade'])

 def test_stop_during_compute_discards_stale_result_without_deadlock(self):
  class BlockingBackend:
   name='blocking'
   def __init__(self): self.started,self.release=Event(),Event()
   def predict(self,data): self.started.set(); self.release.wait(2); return 1,[.1,.9,.2]
  backend=BlockingBackend(); predictions=[]; decisions=[]; cfg=DecoderConfig(analysis_duration_seconds=.1,onset_guard_seconds=.1)
  c=LiveOnlineController(backend,[0,1],predictions,decisions,cfg); c.start_trial({'sessionId':'s','trialId':'A','estimatedGlobalSampleIndex':0})
  args=self.packet(0,0); worker=Thread(target=lambda:c.ingest_packet(*args)); worker.start(); self.assertTrue(backend.started.wait(1))
  stopped=c.stop_trial('concurrent_stop'); self.assertFalse(stopped['decisionMade'])
  backend.release.set(); worker.join(1); self.assertFalse(worker.is_alive()); self.assertEqual([],predictions); self.assertEqual(1,len(decisions)); self.assertEqual([],stopped['predictionTimeline'])

 def test_new_trial_stabilizer_state_is_isolated(self):
  class SequenceBackend:
   name='sequence'
   def __init__(self): self.labels=[1,0,0]
   def predict(self,data): i=self.labels.pop(0); return i,[.9 if i==0 else .1,.9 if i==1 else .1,.1]
  cfg=DecoderConfig(analysis_duration_seconds=.1,onset_guard_seconds=.1); c=LiveOnlineController(SequenceBackend(),[0,1],config=cfg)
  c.start_trial({'sessionId':'s','trialId':'A','estimatedGlobalSampleIndex':0}); c.ingest_packet(*self.packet(0,0)); a=c.stop_trial(); self.assertFalse(a['decisionMade']); self.assertEqual(1,len(a['predictionTimeline']))
  self.assertTrue(c.start_trial({'sessionId':'s','trialId':'B','estimatedGlobalSampleIndex':200})); c.ingest_packet(*self.packet(1,200)); self.assertEqual(1,len(c.active['predictions'])); self.assertEqual(1,c.active['consecutiveCount']); self.assertIsNone(c.active['decision'])
  c.ingest_packet(*self.packet(2,400)); b=c.stop_trial(); self.assertTrue(b['decisionMade']); self.assertEqual('target_left',b['finalDecisionLabel']); self.assertEqual(1,b['decisionPredictionIndex']); self.assertTrue(all(x['trialId']=='B' for x in b['predictionTimeline']))
