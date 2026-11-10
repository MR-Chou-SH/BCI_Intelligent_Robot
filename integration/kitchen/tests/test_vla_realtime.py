"""Realtime queue exhaustion advances physics and controller limits remain visible."""
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np


class RealtimePolicyTests(unittest.TestCase):
    def test_default_clock_advances_while_delayed_inference_has_no_actions(self):
        from integration.kitchen.vla.rollout import run
        release = threading.Event()
        class Env:
            def __init__(self, *args, **kwargs):
                self.frame = self.rate_limited_frames = 0
                self.guard_violations = []
                self.last_success_error = 'Not yet complete'
                self.platform = SimpleNamespace(controller=SimpleNamespace(stopped=False, reference=np.zeros(16)),
                                                episode={'initialization_hash':'fresh'})
                self.quality = SimpleNamespace(report=lambda: {})
                self.action_limits = SimpleNamespace(report=lambda: {})
            def observation(self): return {'frame':self.frame}
            def step(self, action):
                self.frame += 1
                if self.frame >= 5: release.set()
                return False
            def close(self): release.set()
        class Client:
            metadata = {}
            def infer(self, observation, *, reset=False, **kwargs):
                if reset: return np.zeros((2,16)), np.zeros((2,16))
                if not release.wait(timeout=1): raise TimeoutError('Physics did not advance during underrun')
                return np.zeros((50,16)), np.zeros((50,16))
        with tempfile.TemporaryDirectory() as temp, patch('integration.kitchen.vla.rollout.PolicyEnvironment', Env):
            report = run('unused.h5', Path(temp)/'rollout', Client(), seconds=.24, timeout=1.)
        self.assertEqual(report['clock'], 'realtime')
        self.assertEqual(report['replan_remaining'], 40)
        self.assertEqual(report['simulated_seconds'], .24)
        self.assertGreater(report['queue_underrun_frames'], 0)
        self.assertEqual(report['status'], 'POLICY_FAILED')

    def test_position_and_rate_limits_are_counted_separately(self):
        from robot_arm.kitchen.platform import KitchenPlatform
        from robot_arm.kitchen.action_codec import encode, decode
        from integration.kitchen.data.lerobot_export import pack16
        from integration.kitchen.vla.action_limits import ActionLimits
        platform = KitchenPlatform(layout='kitchen_v3_norice_candidate_013', seed=0)
        try:
            c = platform.controller
            audit = ActionLimits(platform.scene)
            joints, opening = encode(c.reference)
            action = pack16(joints, opening); action[0] = 100.; action[7] = 1.5
            target = decode(np.concatenate([action[:7], action[8:15]]), np.clip(action[[7,15]],0,1))
            accepted = c.accept_joint_targets(target)
            audit.command(c, action, target, accepted)
            audit.measured()
            report = audit.report()
            self.assertEqual(report['frames'], 1)
            self.assertEqual(report['arms']['left']['joint_position_clipped_frames'], 1)
            self.assertEqual(report['arms']['left']['joint_rate_limited_frames'], 1)
            self.assertEqual(report['arms']['left']['gripper_range_clipped_frames'], 1)
            self.assertEqual(report['arms']['right']['joint_position_clipped_frames'], 0)
        finally: platform.close()

    def test_request_frequency_is_distinct_from_service_latency(self):
        from integration.kitchen.vla.performance import PerformanceAudit
        client = SimpleNamespace(timings=[
            {'warmup':False,'frame':10,'started_at':1.,'client_ms':220.,'server_ms':210.},
            {'warmup':False,'frame':20,'started_at':1.4,'client_ms':220.,'server_ms':210.},
            {'warmup':False,'frame':30,'started_at':1.8,'client_ms':220.,'server_ms':210.}])
        report = PerformanceAudit().report(client)
        self.assertAlmostEqual(report['request_rate_wall_hz'],2.5)
        self.assertAlmostEqual(report['request_rate_sim_hz'],2.5)
        self.assertEqual(report['metrics']['steady_client_ms']['mean'],220.)


if __name__ == '__main__': unittest.main()
