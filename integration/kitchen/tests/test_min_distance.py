"""min_distance must equal the minimum of the full distance vector, bit for bit."""
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.payload_geometry import CarriedGeometry


class MinDistanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene = build_scene(layout_id='kitchen_v3_norice_candidate_013')

    def poses(self, count, seed):
        s, rng = self.scene, np.random.default_rng(seed)
        for _ in range(count):
            q = s.data.qpos.copy()
            for side in ('left', 'right'):
                arm = s.arms[side]
                q[list(arm.qpos)] = rng.uniform(s.model.jnt_range[list(arm.joints), 0], s.model.jnt_range[list(arm.joints), 1])
            yield q

    def test_matches_full_minimum_and_measures_fewer_pairs(self):
        s = self.scene
        for geometry, pairs in ((CollisionGeometry(s), None),
                                (CarriedGeometry(s, s.data.qpos.copy(), 'left', ['banana']), ('banana',))):
            pairs = geometry.pairs(pairs or ())
            calls = {'full': 0, 'min': 0}
            original = mujoco.mj_geomDistance

            def counted(key):
                def call(*args):
                    calls[key] += 1
                    return original(*args)
                return call
            for q in self.poses(120, 3):
                with patch('robot_arm.kitchen.collision.mujoco.mj_geomDistance', counted('full')):
                    full = np.min(geometry.distances(q, pairs))
                with patch('robot_arm.kitchen.collision.mujoco.mj_geomDistance', counted('min')):
                    fast = geometry.min_distance(q, pairs)
                self.assertEqual(full, fast)
            self.assertLess(calls['min'], calls['full'] / 2)

    def test_empty_pair_set_raises_like_numpy_minimum(self):
        geometry = CollisionGeometry(self.scene)
        with self.assertRaises(ValueError):
            geometry.min_distance(self.scene.data.qpos, np.empty((0, 2), dtype=int))


if __name__ == '__main__':
    unittest.main()
