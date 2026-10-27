"""Timing profiles retain endpoints/limits and preserve contact operations."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.planning.speed import speed_factor


class MotionSpeedTests(unittest.TestCase):
    def test_route_clock_is_independent_of_ik_knot_density(self):
        from integration.kitchen.planning.speed import polyline_clock
        # The same route must not acquire extra pauses from more IK samples.
        coarse=polyline_clock([[0.,0.],[1.,0.]],[100.])
        dense=polyline_clock([[i/100,0.] for i in range(101)],[1.]*100)
        np.testing.assert_allclose(dense,coarse,atol=1e-14)
        middle=np.diff(dense,axis=0)[20:-20,0]
        self.assertLess(np.ptp(middle),1e-12)
        self.assertGreater(middle.min(),0.)

    def test_route_clock_stays_on_edges_and_preserves_other_dof(self):
        from integration.kitchen.planning.speed import polyline_clock
        path=polyline_clock([[0,0,3],[0,0,3],[1,0,3],[1,1,3]],[0,100,100])
        self.assertTrue(np.all((np.abs(path[:,1])<1e-12)|(np.abs(path[:,0]-1)<1e-12)))
        np.testing.assert_allclose(path[:,2],3.)
        np.testing.assert_allclose(path[-1],[1,1,3])
        self.assertLessEqual(np.max(np.abs(np.diff(path,axis=0))),.0101)

    def test_release_wait_checks_food_as_well_as_container_and_has_no_minimum_dwell(self):
        expert=KitchenExpert.__new__(KitchenExpert);waits=[]
        contract={'payload_ids':['burger_container','burger']}
        expert.feedback=lambda:{'contact_execution':{'measured_pad_separation_s':.08,'measured':{
            'grasp':{'pads_separated':True,'pad_normal_forces_n':[0.,0.]},
            'support':{n:{'supported':True} for n in contract['payload_ids']}}}}
        expert.obj=lambda n:{'linear_velocity_world_m_s':[0.,0.,0.],
            'angular_velocity_world_rad_s':[.1 if n=='burger' and not waits else 0.,0.,0.]}
        expert.wait=lambda s:waits.append(s)
        expert.wait_released_payload(contract)
        self.assertEqual(waits,[.08])
        expert.wait_released_payload(contract)
        self.assertEqual(waits,[.08])

    def test_banana_keeps_standard_timing_and_restores_profile_even_on_failure(self):
        expert=KitchenExpert.__new__(KitchenExpert);expert.motion_profile='faster'
        seen=[]
        def run(fruit,**kwargs):
            seen.append((fruit,expert.motion_profile))
            if kwargs['wash']:raise RuntimeError('placement failed')
        expert._fruit=run
        expert.fruit('banana')
        self.assertEqual(expert.motion_profile,'faster')
        with self.assertRaisesRegex(RuntimeError,'placement failed'):expert.fruit('banana',wash=True)
        self.assertEqual(expert.motion_profile,'faster')
        expert.fruit('apple')
        self.assertEqual(seen,[('banana','standard'),('banana','standard'),('apple','faster')])

    def test_contact_operations_keep_original_rates(self):
        for phase in ('acquire','release','device'):
            self.assertEqual(speed_factor('faster',{'phase':phase}),1.)
        self.assertGreater(speed_factor('faster'),speed_factor('faster',{'phase':'carry'}))
        self.assertEqual(speed_factor('standard',{'phase':'carry'}),1.)
        with self.assertRaises(ValueError):speed_factor('invalid')

    def test_faster_spline_shortens_clock_without_breaking_limits_or_endpoint(self):
        def targets(profile):
            expert=KitchenExpert.__new__(KitchenExpert);expert.motion_profile=profile;expert.contract=None
            initial=np.zeros(16);initial[8:]=.2
            limit=.002
            controller=SimpleNamespace(reference=initial.copy(),prepare_joint_targets=lambda q,reference:
                reference+np.clip(q-reference,-limit,limit))
            expert.platform=SimpleNamespace(controller=controller)
            path=[np.ones(7)*value for value in (.05,.1,.15)]
            values,_,_=expert.smooth_targets('left',path,speed=.008)
            values=np.asarray(values)
            np.testing.assert_allclose(values[-1,:7],path[-1])
            np.testing.assert_allclose(values[:,8:],np.tile(initial[8:],(len(values),1)))
            self.assertLessEqual(np.abs(np.diff(np.vstack([initial,values]),axis=0)).max(),limit+1e-9)
            return len(values)
        self.assertLess(targets('faster'),targets('standard'))

    def test_only_faster_free_or_carry_right_routes_skip_intermediate_stops(self):
        for profile,phase,continuous in [('standard',None,False),('faster',None,True),
                ('faster','carry',True),('faster','acquire',False),('faster','release',False),('faster','device',False)]:
            with self.subTest(profile=profile,phase=phase):
                expert=KitchenExpert.__new__(KitchenExpert);expert.motion_profile=profile
                expert.contract={'phase':phase} if phase else None
                expert.scene=SimpleNamespace(arms={'right':Mock()});expert.view=Mock()
                expert.continuous_poses=Mock(return_value=True);expert.point=Mock()
                with patch('integration.kitchen.planning.motion.tcp_pose',return_value=(np.zeros(3),np.eye(3))):
                    expert.line('right',np.array([.1,0,0]),np.eye(3),expert.contract)
                self.assertEqual(expert.continuous_poses.called,continuous)
                self.assertEqual(expert.point.called,not continuous)
