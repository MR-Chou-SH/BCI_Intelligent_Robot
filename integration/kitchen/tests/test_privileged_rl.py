import copy
import json
from types import SimpleNamespace
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import mujoco
import numpy as np
from robot_arm.kitchen.platform import KitchenPlatform
from robot_arm.kitchen.state_tensor import StateTensor
from robot_arm.kitchen.object_state import object_states
from integration.kitchen.runtime.client import LocalPlannerClient
from integration.kitchen.tasks.catalog import cases, chain
from integration.kitchen.rl.environment import PrivilegedEnvironment
from integration.kitchen.rl.tasks import TaskMonitor


class PrivilegedRLTests(unittest.TestCase):
    def test_tcp_native_quaternion_branch_and_legacy_demo_alignment(self):
        from robot_arm.kitchen.limits import joint_control_limits
        from integration.kitchen.rl.anchors import align_legacy_tcp_quaternions
        env=PrivilegedEnvironment(seed=0)
        try:
            scene=env.platform.scene;m,d=scene.model,scene.data;rng=np.random.default_rng(45)
            native=[];legacy=[]
            for _ in range(12):
                for side in ('left','right'):
                    arm=scene.arms[side];low,high=joint_control_limits(m,arm)
                    d.qpos[list(arm.qpos)]=rng.uniform(low,high)
                mujoco.mj_kinematics(m,d)
                state=env.state();old=env.layout.normalize(env.layout.raw(state,env.monitor))
                native.append(old.copy())
                for index,(offset,side) in enumerate(((0,'left'),(22,'right'))):
                    body=scene.arms[side].tcp_body
                    np.testing.assert_allclose(state['robot'][index,18:22],d.xquat[body],atol=1e-12)
                    quat=np.empty(4);mujoco.mju_mat2Quat(quat,d.xmat[body])
                    old[offset+18:offset+22]=quat
                legacy.append(old)
            original=np.array(legacy);expected=np.array(native)
            transitions={'observation':original.copy(),'next_observation':original.copy()}
            changed=align_legacy_tcp_quaternions(transitions,env.layout.schema(),scene)
            self.assertGreater(changed,0) # Exercise a branch missed by home-pose tests.
            for values in transitions.values():
                np.testing.assert_allclose(values,expected,atol=2e-7)
                for offset in (0,22):
                    np.testing.assert_array_equal(values[:,offset:offset+18],original[:,offset:offset+18])
                np.testing.assert_array_equal(values[:,44:],original[:,44:])
        finally:env.close()

    def test_resume_budget_extension_preserves_schedule_and_rejects_other_changes(self):
        from thirdparty.discodemo.adapter import resume_decay_steps
        old={'learning_rate_decay_step':100000,'gamma':.99,'buffer_max_length':50000}
        previous={'step':100000,'agent_config':old}
        extended={**old,'learning_rate_decay_step':1000000}
        self.assertEqual(resume_decay_steps(extended,previous,1000000),100000)
        self.assertEqual(previous['agent_config'],old)
        for changes in ({'gamma':.9},{'buffer_max_length':100000}):
            with self.assertRaisesRegex(ValueError,'Resume mismatch'):
                resume_decay_steps({**extended,**changes},previous,1000000)
        for limit in (99999,100000):
            with self.assertRaisesRegex(ValueError,'must exceed'):
                resume_decay_steps(extended,previous,limit)

    def test_generation_timeout_is_sealed_but_never_qualified(self):
        from integration.kitchen.rl.collect import record_episode
        from integration.kitchen.data.replay import read_compact
        env=PrivilegedEnvironment(seed=5,horizon=2)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                out=Path(temporary)/'attempt'
                row=record_episode(env,lambda _:np.r_[np.zeros(7),1.],out,{'method':'unit-test'})
                self.assertFalse(row['task_success']);self.assertEqual(row['status'],'FAILED_ATTEMPT')
                self.assertNotIn('trajectory',row)
                self.assertIn('POLICY_TASK_FAILED',json.loads((out/'report.json').read_text())['error'])
                files=list(out.rglob('*.h5'));self.assertEqual(len(files),1)
                data=read_compact(files[0]);self.assertEqual(len(data['actions/tick']),2)
                self.assertFalse(data['metadata']['images_included'])
        finally:env.close()

    def test_numeric_state_is_same_tick_read_only_and_covers_all_movable_ids(self):
        p=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=3,initialization_profile='research')
        try:
            p.advance(20,detail='execution');m,d=p.scene.model,p.scene.data
            spec=mujoco.mjtState.mjSTATE_INTEGRATION;before=np.empty(mujoco.mj_stateSize(m,spec))
            mujoco.mj_getState(m,d,before,spec)
            reader=StateTensor(p.scene);state=reader.query(p.scene,p.controller)
            result=LocalPlannerClient(p).call('get_privileged_state')
            after=np.empty_like(before);mujoco.mj_getState(m,d,after,spec)
            np.testing.assert_array_equal(before,after)
            self.assertTrue(result['ok']);self.assertEqual(result['result']['state']['tick'],20)
            self.assertEqual(len(reader.ids),12)
            for item in object_states(p.scene,p.controller)['objects']:
                if item['id'] not in reader.ids:continue
                index=reader.ids.index(item['id'])
                if item['id'] in p.scene.objects:
                    np.testing.assert_allclose(state['objects'][index,:3],item['position_world_m'],atol=1e-12)
                    np.testing.assert_allclose(state['objects'][index,7:10],item['linear_velocity_world_m_s'],atol=1e-12)
                if item.get('joint_name'):
                    self.assertAlmostEqual(state['object_joint'][index,0],item['joint_position'])
            LocalPlannerClient(p).call('reset',{'seed':4},mutating=True)
            with self.assertRaisesRegex(ValueError,'another model'):reader.query(p.scene,p.controller)
        finally:p.close()

    def test_snapshot_round_trip_preserves_future_dynamics_and_task_memory(self):
        env=PrivilegedEnvironment(seed=0)
        try:
            action=np.zeros(8);action[0]=.2;action[7]=1.
            env.step(action);saved=env.capture();json.dumps(saved)
            first=env.step(action);first_state=env.platform.scene.data.qpos.copy()
            env.reset(seed=1);env.reset(seed=2);env.reset(snapshot=saved)
            second=env.step(action)
            np.testing.assert_array_equal(first_state,env.platform.scene.data.qpos)
            np.testing.assert_array_equal(first[0],second[0])
            self.assertEqual(first[1:4],second[1:4])
            self.assertEqual(first[4]['task'],second[4]['task'])
            env.reset(snapshot=saved,curriculum=True)
            self.assertEqual(env.monitor.quality.current,{})
            self.assertEqual(env.platform.controller.tick,saved['controller']['tick'])
        finally:env.close()

    def test_reference_step_uses_one_control_frame_and_times_out_without_success(self):
        env=PrivilegedEnvironment(seed=8,horizon=1)
        try:
            before=env.platform.controller.tick
            _,reward,terminated,truncated,info=env.step(np.r_[np.zeros(7),1.])
            self.assertEqual(env.platform.controller.tick-before,20)
            self.assertEqual(reward,0.)
            self.assertFalse(terminated);self.assertTrue(truncated)
            self.assertEqual(info['accepted_action'].shape,(16,))
            with self.assertRaisesRegex(RuntimeError,'Episode ended'):env.step(np.zeros(8))
        finally:env.close()

    def test_all100_catalogue_cases_have_physical_skill_chains(self):
        # These tests verify policy-driven phase tracking, not successful physics.
        # Existing success predicate tests separately cover support/footprint/etc.
        class Quality:
            def __init__(self,p):self.current={};self.failures=[]
            def start(self,phase):self.phase=phase
            def tray_object(self,name):pass
            def require(self,key,seconds):pass
        with patch('integration.kitchen.rl.tasks.ExpertQuality',Quality), \
             patch('integration.kitchen.rl.tasks.validate_initial'), \
             patch('integration.kitchen.rl.tasks.validate_skill'), \
             patch('integration.kitchen.rl.tasks.validate_final'):
            for case in cases():
                dev=SimpleNamespace(heated={'burger_container','hotdog_container'},heating=None,
                                    supports=lambda *args:False)
                p=SimpleNamespace(controller=SimpleNamespace(devices=dev,tick=0,stopped=False),
                                  scene=SimpleNamespace(objects={}),post_step_observers=[])
                monitor=TaskMonitor(p,case)
                for _ in chain(case):monitor.update()
                self.assertTrue(monitor.success,case['case_id'])
                self.assertEqual([s['skill'] for s in monitor.completed],list(chain(case)))


if __name__=='__main__':unittest.main()
