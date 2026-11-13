"""The 55-degree main review view keeps every task element inside a 16:9 frame."""
import math
import unittest

import mujoco
import numpy as np

from integration.kitchen.data.replay import MAIN_VIEW, MAIN_VIEW_SIZE, REVIEW_VIEWS, VLA_VIEW, VLA_VIEW_SIZE, _review_camera, letterbox
from robot_arm.kitchen.model import build_scene

ELEMENTS = ('sink', 'basket', 'faucet', 'faucet_handle', 'microwave', 'microwave_door', 'microwave_button',
            'wood_tray', 'apple', 'pear', 'banana', 'tomato', 'burger_container', 'burger', 'hotdog_container', 'hotdog')


class ReviewCameraTests(unittest.TestCase):
    def test_main_view_is_55_degree_frontal_and_frames_all_task_elements(self):
        scene = build_scene(layout_id='kitchen_v3_norice_candidate_013')
        m, d = scene.model, scene.data
        mujoco.mj_forward(m, d)
        identity_fovy = m.vis.global_.fovy
        camera, pivot = _review_camera(m, MAIN_VIEW)
        self.assertEqual((camera.elevation, camera.azimuth), (-55., 90.))
        self.assertEqual(MAIN_VIEW_SIZE, (1600, 900))
        self.assertEqual(m.vis.global_.fovy, REVIEW_VIEWS[MAIN_VIEW]['fovy'])
        self.assertNotEqual(identity_fovy, m.vis.global_.fovy)  # playback copy only
        el, az = math.radians(camera.elevation), math.radians(camera.azimuth)
        forward = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        right = np.cross(forward, [0., 0., 1.]); right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        position = np.asarray(pivot) - forward * camera.distance
        half = math.tan(math.radians(m.vis.global_.fovy) / 2)
        visible = mujoco.MjvOption().geomgroup
        seen = set()
        for g in range(m.ngeom):
            body = m.body(int(m.geom_bodyid[g])).name
            if not visible[int(m.geom_group[g])] or not (body in ELEMENTS or m.geom(g).name == 'user_table'
                                                          or body.startswith(('left_', 'right_'))):
                continue
            c, h = m.geom_aabb[g, :3], m.geom_aabb[g, 3:]
            local = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * h + c
            world = d.geom_xpos[g] + local @ d.geom_xmat[g].reshape(3, 3).T - position
            depth = world @ forward
            self.assertTrue(np.all(depth > 0), body)
            x = world @ right / (depth * half * MAIN_VIEW_SIZE[0] / MAIN_VIEW_SIZE[1])
            y = world @ up / (depth * half)
            self.assertLessEqual(np.abs(x).max(), 1., body)
            self.assertLessEqual(np.abs(y).max(), 1., body)
            seen.add(body if body in ELEMENTS else m.geom(g).name if body == 'world' else body.split('_')[0])
        self.assertTrue(set(ELEMENTS) | {'user_table', 'left', 'right'} <= seen)

    def test_preset_rejects_named_camera_adjustments(self):
        scene = build_scene(layout_id='kitchen_v3_norice_candidate_013')
        with self.assertRaisesRegex(ValueError, 'fixed'):
            _review_camera(scene.model, MAIN_VIEW, zoom=.9)
        with self.assertRaisesRegex(ValueError, 'fixed'):
            _review_camera(scene.model, MAIN_VIEW, lookat=[0., 0., 1.])
        camera, pivot = _review_camera(scene.model, 'v3_front')
        self.assertEqual((camera, pivot), ('v3_front', None))

    def test_dataset_renderer_defaults_to_main_view_and_keeps_front_view_framing(self):
        import argparse
        import contextlib
        import io
        from pathlib import Path
        from unittest.mock import patch
        from integration.kitchen.tools import render_dataset
        original = argparse.ArgumentParser.parse_args
        captured = {}

        def capture(parser, *args, **kwargs):
            captured['args'] = original(parser, *args, **kwargs)
            return captured['args']

        def settings(*extra):
            with patch.object(argparse.ArgumentParser, 'parse_args', capture), \
                 patch.object(render_dataset, 'run_path', side_effect=Path), \
                 patch.object(render_dataset, 'read', side_effect=RuntimeError('stop after argument defaults')):
                with self.assertRaisesRegex(RuntimeError, 'stop after'):
                    render_dataset.main(['--manifest', 'm.json', '--output', 'videos', *extra])
            a = captured['args']
            return a.camera, a.width, a.height, a.zoom, a.lookat

        self.assertEqual(settings(), ('v3_main55', 1600, 900, 1., None))
        self.assertEqual(settings('--camera', 'v3_front', '--width', '960', '--height', '540'),
                         ('v3_front', 960, 540, .9, [.7, .15, .95]))
        errors = io.StringIO()
        with patch.object(render_dataset, 'run_path', side_effect=Path), contextlib.redirect_stderr(errors), \
             self.assertRaises(SystemExit):
            render_dataset.main(['--manifest', 'm.json', '--output', 'videos', '--zoom', '.9'])
        self.assertIn('Preset review views take no --zoom/--lookat', errors.getvalue())

    def project(self, view_name, size):
        scene = build_scene(layout_id='kitchen_v3_norice_candidate_013')
        m, d = scene.model, scene.data
        mujoco.mj_forward(m, d)
        camera, pivot = _review_camera(m, view_name)
        el, az = math.radians(camera.elevation), math.radians(camera.azimuth)
        forward = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
        right = np.cross(forward, [0., 0., 1.]); right /= np.linalg.norm(right)
        up = np.cross(right, forward)
        position = np.asarray(pivot) - forward * camera.distance
        half = math.tan(math.radians(m.vis.global_.fovy) / 2)
        visible = mujoco.MjvOption().geomgroup
        xs, ys = [], []
        for g in range(m.ngeom):
            body = m.body(int(m.geom_bodyid[g])).name
            if not visible[int(m.geom_group[g])] or not (body in ELEMENTS or m.geom(g).name == 'user_table'
                                                          or body.startswith(('left_', 'right_'))):
                continue
            c, h = m.geom_aabb[g, :3], m.geom_aabb[g, 3:]
            local = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * h + c
            world = d.geom_xpos[g] + local @ d.geom_xmat[g].reshape(3, 3).T - position
            depth = world @ forward
            xs.append(world @ right / (depth * half * size[0] / size[1])); ys.append(world @ up / (depth * half))
        return np.concatenate(xs), np.concatenate(ys)

    def test_vla_view_keeps_all_elements_in_the_square_and_out_of_the_black_bands(self):
        self.assertEqual(VLA_VIEW_SIZE, (224, 224))
        x, y = self.project(VLA_VIEW, VLA_VIEW_SIZE)
        top, bottom = REVIEW_VIEWS[VLA_VIEW]['letterbox']
        rows = (1 - y) / 2  # 0 at the top edge, 1 at the bottom edge
        self.assertLessEqual(np.abs(x).max(), 1.)
        self.assertGreaterEqual(rows.min(), top)
        self.assertLessEqual(rows.max(), bottom)

    def test_letterbox_blackens_only_rows_outside_the_band(self):
        pixels = np.full((224, 224, 3), 200, np.uint8)
        out = letterbox(pixels.copy(), (.25, .75))
        self.assertFalse(out[:56].any()); self.assertFalse(out[168:].any())
        self.assertTrue((out[56:168] == 200).all())
        np.testing.assert_array_equal(letterbox(pixels.copy(), None), pixels)

    def test_vla_rig_renders_the_letterboxed_square_view(self):
        from pathlib import Path
        from integration.kitchen.vla import images
        rig_path = Path(images.__file__).with_name('cameras_main55_224.json')
        rig = images.camera_rig(rig_path)
        camera = rig['views'][0]['camera']
        self.assertEqual((camera['width'], camera['height']), VLA_VIEW_SIZE)
        self.assertEqual(tuple(camera['letterbox']), REVIEW_VIEWS[VLA_VIEW]['letterbox'])
        bad = images.camera_rig(rig_path); bad['views'][0]['camera']['letterbox'] = [.8, .2]
        with self.assertRaisesRegex(ValueError, 'letterbox'):
            images.camera_rig(bad)
        scene = build_scene(layout_id='kitchen_v3_norice_candidate_013')
        view = images.Camera(scene, camera)
        try:
            pixels = view.render()
        finally:
            view.close()
        top, bottom = (round(f * 224) for f in camera['letterbox'])
        self.assertFalse(pixels[:top].any()); self.assertFalse(pixels[bottom:].any())
        self.assertTrue(pixels[top:bottom].any())


if __name__ == '__main__':
    unittest.main()
