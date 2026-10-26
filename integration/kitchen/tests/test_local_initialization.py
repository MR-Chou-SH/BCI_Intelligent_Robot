import unittest
from unittest.mock import patch

import numpy as np
import mujoco

from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen import research_initialization as init


class LocalInitializationTests(unittest.TestCase):
    def test_seed_repeat_bounds_group_rigidity_and_fixed_devices(self):
        scene = build_scene(layout_id="kitchen_v3_norice_candidate_013", seed=0)
        original_q, original_v = scene.data.qpos.copy(), scene.data.qvel.copy()
        original_ctrl = scene.data.ctrl.copy()
        state = np.random.get_state()
        hashes = []
        for seed in (0, 1, 0):
            scene.data.qpos[:] = original_q
            scene.data.qvel[:] = original_v
            mujoco.mj_forward(scene.model, scene.data)
            reference_relative = {group[0]: scene.data.xpos[scene.objects[group[1]]] - scene.data.xpos[scene.objects[group[0]]]
                                  for group in init.GROUPS if len(group) == 2}
            result = init.initialize_research_scene(scene, seed)
            hashes.append(result["episode_scene_hash"])
            changed = set()
            for group in init.GROUPS:
                root = group[0]
                adr, _ = init._free_address(scene, root)
                delta = scene.data.qpos[adr:adr+2] - scene.model.qpos0[adr:adr+2]
                self.assertTrue(np.all(np.abs(delta) <= .08 + 1e-12))
                if root in init.FRUITS:
                    self.assertLess(scene.data.qpos[adr], 0)
                else:
                    self.assertGreater(scene.data.qpos[adr], 0)
                if len(group) == 2:
                    rotation = scene.data.xmat[scene.objects[root]].reshape(3, 3)
                    local = rotation.T @ (scene.data.xpos[scene.objects[group[1]]] - scene.data.xpos[scene.objects[root]])
                    np.testing.assert_allclose(local, reference_relative[root], atol=1e-12)
                for name in group:
                    a, _ = init._free_address(scene, name)
                    changed.update(range(a, a+7))
            untouched = sorted(set(range(scene.model.nq)) - changed)
            np.testing.assert_array_equal(scene.data.qpos[untouched], original_q[untouched])
            np.testing.assert_array_equal(scene.data.ctrl, original_ctrl)
            self.assertTrue(result["geometry"]["pass"])
            self.assertFalse(result["dynamic_settling"]["occurred"])
        self.assertEqual(hashes[0], hashes[2])
        self.assertNotEqual(hashes[0], hashes[1])
        after = np.random.get_state()
        self.assertEqual(state[0], after[0])
        np.testing.assert_array_equal(state[1], after[1])
        self.assertEqual(state[2:], after[2:])

    def test_exhausted_sampling_preserves_live_state(self):
        scene = build_scene(layout_id="kitchen_v3_norice_candidate_013", seed=0)
        before = scene.data.qpos.copy()
        with patch.object(init, "_approach_check", return_value=None):
            with self.assertRaises(init.ResearchInitializationError) as failure:
                init.initialize_research_scene(scene, 0)
        self.assertEqual(failure.exception.metadata["code"], "BOUNDED_SAMPLING_EXHAUSTED")
        self.assertFalse(failure.exception.metadata["live_state_committed"])
        np.testing.assert_array_equal(scene.data.qpos, before)


if __name__ == "__main__":
    unittest.main()
