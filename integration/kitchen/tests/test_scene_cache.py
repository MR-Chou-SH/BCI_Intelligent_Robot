"""A cached compiled scene must be indistinguishable from a fresh build and never shared."""
import unittest

import mujoco
import numpy as np

from robot_arm.kitchen import scene_cache
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.scene_identity import compiled_model_sha256, model_identity

LAYOUT = 'kitchen_v3_norice_candidate_013'


class SceneCacheTests(unittest.TestCase):
    def setUp(self):
        scene_cache.clear_scene_cache()
        self.addCleanup(scene_cache.clear_scene_cache)

    def test_copies_match_fresh_build_and_are_independent(self):
        fresh = build_scene(layout_id=LAYOUT)
        first = scene_cache.cached_build_scene(layout_id=LAYOUT)
        second = scene_cache.cached_build_scene(layout_id=LAYOUT)
        self.assertIsNot(first.model, second.model)
        self.assertIsNot(first.data, second.data)
        for scene in (first, second):
            self.assertEqual(compiled_model_sha256(scene.model), fresh.compiled_model_sha256)
            self.assertEqual(model_identity(scene, builder_sha256=scene.builder_sha256),
                             model_identity(fresh, builder_sha256=fresh.builder_sha256))
            for field in ('qpos', 'qvel', 'ctrl', 'qacc', 'xpos', 'qfrc_bias'):
                np.testing.assert_array_equal(getattr(scene.data, field), getattr(fresh.data, field))
            self.assertEqual(scene.arms, fresh.arms)
            self.assertEqual(scene.objects, fresh.objects)
        first.model.geom_friction[0, 0] = 9.
        first.data.qpos[0] = 1.
        third = scene_cache.cached_build_scene(layout_id=LAYOUT)
        self.assertEqual(third.model.geom_friction[0, 0], fresh.model.geom_friction[0, 0])
        self.assertEqual(third.data.qpos[0], fresh.data.qpos[0])
        mujoco.mj_step(third.model, third.data)  # copies are fully usable simulations

    def test_different_key_rebuilds(self):
        a = scene_cache.cached_build_scene(layout_id=LAYOUT, seed=0)
        b = scene_cache.cached_build_scene(layout_id=LAYOUT, seed=1)
        self.assertEqual((a.seed, b.seed), (0, 1))
        self.assertEqual(b.compiled_model_sha256, build_scene(layout_id=LAYOUT, seed=1).compiled_model_sha256)


if __name__ == '__main__':
    unittest.main()
