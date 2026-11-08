"""Pear footing preserves height/mass and uses one real rigid-body collider."""
import unittest
import numpy as np
import mujoco
from robot_arm.kitchen.model import build_scene


class PearSupportTests(unittest.TestCase):
    def test_foot_matches_apple_and_keeps_pear_total_mass_and_bottom(self):
        scene=build_scene(layout_id='kitchen_v3_norice_candidate_013',seed=0)
        m=scene.model;pear=m.geom('pear_foot').id;apple=m.geom('apple_foot').id
        self.assertEqual(m.geom_type[pear],mujoco.mjtGeom.mjGEOM_CYLINDER)
        np.testing.assert_array_equal(m.geom_size[pear],m.geom_size[apple])
        np.testing.assert_array_equal(m.geom_friction[pear],m.geom_friction[apple])
        self.assertAlmostEqual(m.geom_pos[pear,2]-m.geom_size[pear,1],-.045)
        self.assertEqual(m.geom_bodyid[pear],m.body('pear').id)
        self.assertAlmostEqual(m.body_mass[m.body('pear').id],.18)
        self.assertEqual(m.body_jntnum[m.body('pear').id],1)

    def test_historical_candidate_does_not_gain_a_foot(self):
        scene=build_scene(layout_id='kitchen_v3_norice_candidate_012',seed=0)
        self.assertEqual(mujoco.mj_name2id(scene.model,mujoco.mjtObj.mjOBJ_GEOM,'pear_foot'),-1)


if __name__=='__main__':unittest.main()
