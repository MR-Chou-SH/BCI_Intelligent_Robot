import tempfile
import unittest
from pathlib import Path

import numpy as np

from integration.kitchen.planning.action_retiming import retime
from integration.kitchen.data.retiming_experiment import ContextClient,build_candidate,restored_platform


class ActionRetimingTests(unittest.TestCase):
    def test_long_carry_pause_only_bridged_when_clear_and_internal(self):
        q=np.zeros((50,16));q[:10,0]=np.arange(1,11)*.001;q[10:40,0]=.01;q[40:,0]=np.arange(11,21)*.001
        mode={'contact_contract':{'phase':'carry'}}
        def run(clear,cuts):return retime(q,np.zeros(16),cuts,[(0,mode)],2.,lambda x,p:x,
            continuous=True,carry_pause_clear=lambda *args:clear)
        clear,_,stats=run(True,{0,50});blocked,_,_=run(False,{0,50});boundary,_,_=run(True,{0,10,40,50})
        self.assertEqual(stats['bridged_clear_carry_pause_frames'],30)
        self.assertLess(len(clear),len(blocked))
        self.assertEqual(len(boundary),len(blocked))
        np.testing.assert_array_equal(clear[-1],q[-1])

    def test_constant_joint_at_lower_limit_stays_exact_through_interpolation(self):
        commands=np.zeros((40,16));commands[:,0]=np.linspace(.001,.04,40)
        commands[:,13]=.8521;initial=np.zeros(16);initial[13]=.8521
        result,_,_=retime(commands,initial,{0,40},[(0,{})],3.,lambda goal,previous:goal,continuous=True)
        self.assertTrue(np.all(result[:,13]==.8521))

    def test_five_x_request_still_obeys_existing_per_frame_limiter(self):
        commands=np.zeros((40,16));commands[:,0]=np.linspace(.001,.04,40)
        def limit(goal,previous):return previous+np.clip(goal-previous,-.002,.002)
        result,_,stats=retime(commands,np.zeros(16),{0,40},[(0,{})],5.,limit,continuous=True)
        self.assertGreater(stats['limiter_constrained_frames'],0)
        self.assertLessEqual(np.max(np.abs(np.diff(np.vstack([np.zeros(16),result]),axis=0))),.002+1e-9)
        np.testing.assert_array_equal(result[-1],commands[-1])
        with self.assertRaises(ValueError):retime(commands,np.zeros(16),{0,40},[(0,{})],5.01,limit)

    def test_continuous_clock_bridges_only_short_interior_pauses(self):
        q=np.zeros((38,16));q[:10,0]=np.arange(1,11)*.001
        q[10:15,0]=.01;q[15:25,0]=np.arange(11,21)*.001;q[25:,0]=.02
        out,mapping,stats=retime(q,np.zeros(16),{0,38},[(0,{})],2.,lambda x,p:x,continuous=True)
        self.assertLess(len(out),30)
        # Long terminal stabilization is retained, short interior pause is gone.
        self.assertEqual(np.count_nonzero(np.abs(np.diff(out[:,0]))<1e-12),13)
        np.testing.assert_array_equal(out[-1],q[-1])

    def test_faucet_constant_arc_clock_removes_internal_speed_valleys(self):
        q=np.zeros((30,16));q[:,0]=np.r_[np.linspace(.001,.01,10),np.linspace(.0101,.011,10),np.linspace(.012,.021,10)]
        mode={'contact_contract':{'phase':'device','target':'faucet_handle_geom'}}
        out,_,_=retime(q,np.zeros(16),{0,30},[(0,mode)],1.5,lambda x,p:x,continuous=True)
        speeds=np.diff(np.r_[0.,out[:,0]])
        self.assertLess(np.std(speeds[:-1]),1e-10)
        self.assertLess(len(out),30)

    def test_short_pause_at_explicit_boundary_is_not_erased(self):
        q=np.zeros((15,16));q[:5,0]=np.arange(1,6)*.001
        q[5:10,0]=.005;q[10:,0]=np.arange(6,11)*.001
        out,mapping,_=retime(q,np.zeros(16),{0,5,10,15},[(0,{})],2.,lambda x,p:x,continuous=True)
        self.assertEqual(mapping[10]-mapping[5],5)

    def test_blends_preserve_grippers_and_obey_real_tcp_bound(self):
        from integration.kitchen.planning.action_smoothing import blend_source
        from robot_arm.kitchen.platform import KitchenPlatform
        from robot_arm.kitchen.control import tcp_pose
        import mujoco
        p=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0)
        try:
            n=35;reference=p.controller.reference.copy();commands=np.tile(reference,(n,1))
            commands[:,0]+=np.r_[np.linspace(0,.03,18),np.linspace(.03,.02,17)]
            ids=[int(i) for side in ('left','right') for i in p.scene.arms[side].qpos]
            data={'controller':{'reference':reference},'metadata':{'arm_qpos_indices':ids},
                  'states/qpos':np.tile(p.scene.data.qpos,(n+1,1))}
            output,stats=blend_source(commands,data,{0,n},[(0,{})],p)
            np.testing.assert_array_equal(output[:,[7,15]],commands[:,[7,15]])
            np.testing.assert_array_equal(output[-1],commands[-1])
            self.assertLessEqual(np.max(np.abs(output-commands)),.008+1e-12)
            self.assertLessEqual(stats['maximum_tcp_blend_m'],.002)
            self.assertGreater(stats['blended_samples'],0)
        finally:p.close()

    def test_holds_grips_and_event_endpoints_survive(self):
        q=np.zeros((20,16));q[:8,0]=np.arange(1,9)*.001;q[8:,0]=.008
        q[12:16,7]=np.arange(1,5)*.001;q[16:,7]=.004
        out,mapping,stats=retime(q,np.zeros(16),{0,8,12,16,20},[(0,{})],2.,lambda x,prev:x)
        self.assertEqual(len(out),16)
        self.assertEqual(mapping,{0:0,8:4,12:8,16:12,20:16})
        np.testing.assert_array_equal(out[4:],q[8:])
        self.assertEqual(stats['protected_source_frames'],12)
        np.testing.assert_array_equal(out[3],q[7])

    def test_existing_limiter_caps_progress_without_extra_remainder_frames(self):
        q=np.zeros((10,16));q[:,0]=np.arange(1,11)*.01
        def prepare(goal,previous):return previous+np.clip(goal-previous,-.01,.01)
        out,mapping,stats=retime(q,np.zeros(16),{0,10},[(0,{})],2.,prepare)
        self.assertGreater(stats['limiter_constrained_frames'],0)
        self.assertEqual(len(out),len(q))
        self.assertLessEqual(np.max(np.abs(np.diff(np.vstack([np.zeros(16),out]),axis=0))),.01+1e-12)
        np.testing.assert_allclose(out[-1],q[-1],atol=1e-12)

    def test_device_and_guarded_frames_keep_original_clock(self):
        q=np.zeros((8,16));q[:,0]=np.arange(1,9)*.001
        for mode in ({'contact_contract':{'phase':'device'}},{'guarded_gripper':True}):
            out,mapping,_=retime(q,np.zeros(16),{0,8},[(0,mode)],2.,lambda x,prev:x)
            np.testing.assert_array_equal(out,q);self.assertEqual(mapping[8],8)

    def test_one_x_reconstructs_exact_targets(self):
        q=np.zeros((12,16));q[:,8]=np.arange(1,13)*.003
        out,mapping,_=retime(q,np.zeros(16),{0,5,12},[(0,{})],1.,lambda x,prev:x)
        np.testing.assert_array_equal(out,q);self.assertEqual(mapping[5],5)

    def test_semantic_and_release_events_are_remapped_by_boundaries(self):
        from types import SimpleNamespace
        n=12;commands=np.zeros((n,14));commands[:,0]=np.arange(1,n+1)*.001
        data={'start_tick':500,'end_tick':740,'actions/tick':np.arange(500,740,20),
            'actions/joint_pos':commands,'replay/gripper_target':np.zeros((n,2)),
            'controller':{'reference':np.zeros(16)}}
        context={'changes':[{'tick':500,'method':'mode','args':{}},
                            {'tick':620,'method':'end_contact','args':{}}],
                 'events':[{'tick':500,'stage':'skill_started','skill':'R5'},
                           {'tick':740,'stage':'skill_qualified','skill':'R5'}]}
        controller=SimpleNamespace(prepare_joint_targets=lambda x,reference:x)
        result=build_candidate(data,context,2.,controller)
        self.assertEqual(result['changes'][1]['tick'],560)
        self.assertEqual(result['events'][-1]['tick'],620)

    def test_real_trace_is_sparse_and_does_not_store_waypoints_or_contacts(self):
        from robot_arm.kitchen.platform import KitchenPlatform
        p=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0,initialization_profile='fixed')
        try:
            client=ContextClient(p);client.active=True
            q=p.controller.reference.tolist()
            for _ in range(2):
                reply=client.call('execute_trajectory',{'waypoints':[q],'source':'programmatic_validation'},mutating=True)
                self.assertTrue(reply['ok'],reply)
                self.assertTrue(client.call('step',{'n_substeps':20,'detail':'execution'},mutating=True)['ok'])
            self.assertEqual(client.changes,[{'tick':0,'method':'mode','args':{}}])
        finally:p.close()


if __name__=='__main__':unittest.main()
