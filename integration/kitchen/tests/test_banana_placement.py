"""Grip strengthening for turning must not change straight fruit acquisition."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from integration.kitchen.planning.expert import KitchenExpert
from robot_arm.kitchen.platform import KitchenPlatform


class BananaPlacementTests(unittest.TestCase):
    def test_long_axis_alignment_does_not_flip_an_inverted_washed_fruit(self):
        from integration.kitchen.planning.common import banana_tray_rotations,rz
        for tray in [np.eye(3),rz(.3)]:
            for sign in (1.,-1.):
                body=tray@np.diag([1.,sign,sign])
                for desired in banana_tray_rotations(body,tray):
                    np.testing.assert_allclose(np.abs(tray.T@desired[:,0]),[0.,1.,0.],atol=1e-12)
                    self.assertGreater(float(body[:,2]@desired[:,2]),.99)
                    self.assertAlmostEqual(float(np.linalg.det(desired)),1.)

    def test_turn_force_is_opt_in_and_waits_for_both_pads(self):
        for minimum,expected in [(None,1),(8.,2)]:
            expert=KitchenExpert.__new__(KitchenExpert);calls=[]
            expert.platform=SimpleNamespace(controller=SimpleNamespace(reference=np.zeros(16)))
            expert.execute=lambda *args:calls.append(args);expert.wait=Mock()
            expert.feedback=lambda:{'contact_execution':{'measured':{'grasp':{
                'pad_normal_forces_n':[9.,5. if len(calls)==1 else 9.]}}}}
            expert.grip('left',.05,{'target':'banana'},minimum_force=minimum)
            self.assertEqual(len(calls),expected)
            expert.wait.assert_called_once_with(.16)

    def test_faucet_thicker_surface_keeps_device_identity_and_mass(self):
        platform=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0)
        try:
            model=platform.scene.model;g=model.geom('faucet_handle_geom').id
            self.assertAlmostEqual(float(model.geom_size[g,0]),.016)
            self.assertGreater(float(model.geom_friction[g,0]),float(model.geom_friction[model.geom('apple_collision').id,0]))
            self.assertAlmostEqual(float(model.body_mass[model.body('faucet_handle').id]),.06)
            self.assertGreaterEqual(model.joint('faucet_switch').id,0)
        finally:platform.close()
