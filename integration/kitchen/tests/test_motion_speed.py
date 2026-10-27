"""Timing profiles retain endpoints/limits and preserve contact operations."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.planning.speed import speed_factor


class MotionSpeedTests(unittest.TestCase):
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
