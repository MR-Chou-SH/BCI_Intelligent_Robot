"""Measured geometry, simple approach feasibility and query regression tests."""
import math
import unittest

import mujoco
import numpy as np

from integration.kitchen.planning.meal_geometry import grasp_candidates, plan_grasp, MealGrasp, approach_path
from robot_arm.kitchen.platform import KitchenPlatform
from robot_arm.kitchen.object_state import object_states
from robot_arm.kitchen.research_initialization import audit_object_separation


class MealGeometryTests(unittest.TestCase):
    def test_closed_empty_jaw_self_contact_does_not_block_arm_only_route(self):
        from integration.kitchen.planning.expert import KitchenExpert
        from integration.kitchen.runtime.client import LocalPlannerClient
        from robot_arm.kitchen.control import tcp_pose
        p = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0)
        try:
            expert = KitchenExpert(p, LocalPlannerClient(p))
            expert.set_empty_jaws(.05)
            view = expert.view()
            position, rotation = tcp_pose(view.data, p.scene.arms["right"])
            before = p.scene.data.qpos.copy()
            # Closed opposing finger holders touch each other by construction;
            # the arm-only route keeps those finger coordinates fixed.
            route = approach_path(view, MealGrasp("empty_closed",position,rotation,
                                                 position,np.empty(7),0.))
            self.assertTrue(route)
            np.testing.assert_array_equal(p.scene.data.qpos, before)
        finally: p.close()

    def test_randomized_pickups_with_closed_and_100_degree_door(self):
        for seed in range(4):
            p = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=seed,
                                initialization_profile="research")
            try:
                m, d = p.scene.model, p.scene.data
                separation = audit_object_separation(p.scene)
                self.assertTrue(separation["pass"])
                rows = {frozenset(r["objects"]):r for r in separation["pairs"]}
                for pair in (("burger_container","hotdog_container"),
                             ("burger_container","wood_tray"),
                             ("hotdog_container","wood_tray")):
                    row = rows[frozenset(pair)]
                    self.assertEqual(row["minimum_distance_m"], .060)
                    self.assertGreaterEqual(row["signed_distance_m"], .060)
                adr = m.jnt_qposadr[m.joint("microwave_hinge").id]
                for angle in (0., math.radians(100)):
                    d.qpos[adr] = angle; mujoco.mj_forward(m, d)
                    qpos, qvel, time = d.qpos.copy(), d.qvel.copy(), d.time
                    for name in ("burger_container", "hotdog_container"):
                        grasp = plan_grasp(p.scene, name, plan_approach=True, destination="tray")
                        self.assertGreaterEqual(grasp.clearance_m, .001)
                        self.assertTrue(grasp.approach_joints)
                        np.testing.assert_allclose(grasp.rotation.T @ grasp.rotation, np.eye(3), atol=1e-12)
                    np.testing.assert_array_equal(d.qpos, qpos)
                    np.testing.assert_array_equal(d.qvel, qvel)
                    self.assertEqual(d.time, time)
            finally: p.close()

    def test_query_exposes_actual_compiled_bar_tilt_and_same_tick_pose(self):
        p = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=2,
                            initialization_profile="research")
        try:
            m, d = p.scene.model, p.scene.data
            before = d.qpos.copy()
            result = object_states(p.scene, p.controller, ids=["burger_container"])
            frames = result["objects"][0]["handle_frames"]
            self.assertEqual(len(frames), 2)
            for frame in frames:
                g = m.geom(frame["geom_name"]).id
                np.testing.assert_allclose(frame["position_world_m"], d.geom_xpos[g], atol=1e-10)
                np.testing.assert_allclose(frame["rotation_world"], d.geom_xmat[g].reshape(3, 3), atol=1e-10)
            # The burger's compiled bar has a 60deg local tilt, independent of
            # the randomized carrier yaw. Candidates must retain that frame.
            body = p.scene.objects["burger_container"]
            local = d.xmat[body].reshape(3, 3).T @ np.asarray(frames[0]["rotation_world"])
            self.assertAlmostEqual(local[0, 2], math.sin(math.radians(60)), places=6)
            for meal in ("burger","hotdog"):
                for side in ("left","right"):
                    geom=m.geom(meal+"_grasp_"+side)
                    self.assertAlmostEqual(m.geom_quat[geom.id][0], math.cos(math.radians(30)))
                    self.assertAlmostEqual(m.geom_friction[geom.id][0], 1.6)
                self.assertAlmostEqual(m.geom_friction[m.geom(meal+"_plate").id][0], 1.2)
            first = next(grasp_candidates(p.scene, "burger_container"))
            np.testing.assert_allclose(first[2][:, 0], -np.asarray(frames[0]["rotation_world"])[:, 2])
            np.testing.assert_array_equal(d.qpos, before)
            self.assertFalse(result["images_included"])
        finally: p.close()

    def test_buried_carrier_fails_before_motion(self):
        p = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0)
        try:
            m, d = p.scene.model, p.scene.data
            adr = m.jnt_qposadr[m.joint("burger_container_free").id]
            d.qpos[adr+2] -= .10; mujoco.mj_forward(m, d)
            before = d.qpos.copy()
            with self.assertRaisesRegex(ValueError, "NO_GEOMETRIC_MEAL_GRASP"):
                plan_grasp(p.scene, "burger_container", plan_approach=True)
            np.testing.assert_array_equal(d.qpos, before)
            self.assertEqual(p.controller.tick, 0)
        finally: p.close()


if __name__ == "__main__": unittest.main()
