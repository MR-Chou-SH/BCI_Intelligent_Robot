"""Faster data transport must preserve physics and every control-frame guard."""
import unittest
import numpy as np
from robot_arm.kitchen.platform import KitchenPlatform
from robot_arm.kitchen.contact import object_geom_ids
from integration.kitchen.runtime.client import LocalPlannerClient
from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.planning.speed import polyline_clock


class CollectionEfficiencyTests(unittest.TestCase):
    def test_compact_step_keeps_identical_physics_and_observer_clock(self):
        platforms=[KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0) for _ in range(2)]
        try:
            clocks=[[],[]];results=[]
            for i,p in enumerate(platforms):
                p.post_step_observers.append(lambda c,i=i:clocks[i].append(c.tick))
                client=LocalPlannerClient(p)
                args={'n_substeps':100}
                if i:args['detail']='execution'
                result=client.call('step',args,mutating=True)
                self.assertTrue(result['ok']);results.append(result['result'])
            np.testing.assert_array_equal(platforms[0].scene.data.qpos,platforms[1].scene.data.qpos)
            np.testing.assert_array_equal(platforms[0].scene.data.qvel,platforms[1].scene.data.qvel)
            self.assertEqual(clocks,[[20,40,60,80,100]]*2)
            self.assertIn('robots',results[0]);self.assertNotIn('robots',results[1])
            self.assertEqual(results[0]['stopped'],results[1]['stopped'])
            client=LocalPlannerClient(platforms[1]);tick=platforms[1].controller.tick
            reply=client.call('step',{'n_substeps':100,'detail':'invalid'},mutating=True)
            self.assertFalse(reply['ok']);self.assertEqual(platforms[1].controller.tick,tick)
        finally:
            for p in platforms:p.close()

    def test_static_geometry_cache_returns_independent_sets(self):
        p=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0)
        try:
            first=object_geom_ids(p.scene,'banana');expected=first.copy();first.clear()
            self.assertEqual(object_geom_ids(p.scene,'banana'),expected)
            self.assertTrue(expected)
        finally:p.close()

    def test_rpc_batch_size_does_not_change_native_trajectory(self):
        platforms=[KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0) for _ in range(2)]
        try:
            for p,batch in zip(platforms,(8,25)):
                client=LocalPlannerClient(p);initial=p.controller.reference.copy()
                targets=[]
                for i in range(60):
                    q=initial.copy();q[0]+=(i+1)*.0002;targets.append(q.tolist())
                for first in range(0,len(targets),batch):
                    part=targets[first:first+batch]
                    self.assertTrue(client.call('execute_trajectory',{'waypoints':part},mutating=True)['ok'])
                    self.assertTrue(client.call('step',{'n_substeps':20*len(part),'detail':'execution'},mutating=True)['ok'])
                self.assertFalse(p.controller.stopped)
            np.testing.assert_array_equal(platforms[0].scene.data.qpos,platforms[1].scene.data.qpos)
            np.testing.assert_array_equal(platforms[0].scene.data.qvel,platforms[1].scene.data.qvel)
        finally:
            for p in platforms:p.close()

    def test_clock_exposes_exact_contract_boundary_without_an_internal_stop(self):
        points=np.array([[0.],[.4],[1.]])
        values,clock,knots=polyline_clock(points,[40.,60.],with_clock=True)
        split=np.searchsorted(clock,knots[1],side='left')
        self.assertGreater(split,0);self.assertLess(split,len(values)-1)
        self.assertLess(clock[split-1],knots[1]);self.assertGreaterEqual(clock[split],knots[1])
        steps=np.diff(values[:,0]);self.assertGreater(steps[split-1],.009)

    def test_stream_switches_contact_contract_without_endpoint_settle(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        expert=KitchenExpert.__new__(KitchenExpert);calls=[];expert.events=[]
        expert.platform=SimpleNamespace(controller=SimpleNamespace(tick=0));expert.joints=Mock()
        expert.execute=lambda targets,contract:calls.append((len(targets),contract))
        acquire={'phase':'acquire'}
        expert.execute_smooth('right',[np.zeros(16)]*18,None,acquire=acquire,split=11)
        self.assertEqual(calls,[(8,None),(3,None),(5,acquire),(2,acquire)])
        expert.joints.assert_called_once()

    def test_larger_batches_preserve_the_exact_contact_switch(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        expert=KitchenExpert.__new__(KitchenExpert);expert.motion_profile='faster'
        expert.events=[];calls=[];expert.joints=Mock()
        expert.platform=SimpleNamespace(controller=SimpleNamespace(tick=0))
        expert.execute=lambda targets,contract:calls.append((len(targets),contract))
        acquire={'phase':'acquire'}
        expert.execute_smooth('right',[np.zeros(16)]*60,None,acquire=acquire,split=31)
        self.assertEqual(calls,[(25,None),(6,None),(19,acquire),(10,acquire)])
        expert.joints.assert_called_once()

    def test_empty_gripper_ramp_uses_existing_limit_without_fixed_115_frame_hold(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        expert=KitchenExpert.__new__(KitchenExpert);expert.motion_profile='faster'
        expert.held=expert.contract=None;expert.wait=Mock()
        expert.platform=SimpleNamespace(controller=SimpleNamespace(reference=np.zeros(16)))
        expert.call=Mock(side_effect=[{}, {'stopped':False}])
        expert.set_empty_jaws(.05)
        args=expert.call.call_args_list[0].args[1];targets=np.array(args['waypoints'])
        self.assertTrue(args['guarded_gripper']);self.assertLess(len(targets),115)
        self.assertLessEqual(np.max(np.diff(np.r_[0.,targets[:,15]])),.002+1e-12)
        self.assertAlmostEqual(targets[-1,15],.05)
        expert.call.reset_mock();expert.set_empty_jaws(0.)
        expert.call.assert_not_called()
