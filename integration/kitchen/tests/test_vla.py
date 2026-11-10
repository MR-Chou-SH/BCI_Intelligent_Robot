"""Policy boundary checks do not require Torch, LeRobot or a GPU."""
import json
import threading
import unittest
import numpy as np
from integration.kitchen.vla.protocol import rgb_encode, rgb_decode, action_chunk, ChunkBuffer, PolicyClient
from integration.kitchen.vla.server import serve


class PolicyBoundaryTests(unittest.TestCase):
    def test_selected_c_rig_only_allocates_enabled_views(self):
        from unittest.mock import patch
        from integration.kitchen.vla.images import CameraRig, camera_rig
        rig = camera_rig()
        self.assertEqual([v['enabled'] for v in rig['views']], [True, True, False])
        self.assertEqual(rig['views'][0]['camera']['position_world_m'], [0., -2.1, 2.2])
        with patch('integration.kitchen.vla.images.Camera') as camera:
            renderer = CameraRig(object())
            self.assertEqual(camera.call_count, 2)
            self.assertEqual([call.args[1]['camera'] for call in camera.call_args_list], ['v3_front', 'left_wrist'])
            self.assertEqual(set(renderer.render_all()), {'observation.images.camera1', 'observation.images.camera2'})
            renderer.close()

    def test_multiview_protocol_rejects_missing_disabled_and_changed_pose(self):
        import copy
        from integration.kitchen.vla.images import camera_rig
        from integration.kitchen.vla.protocol import observation_images
        rig = camera_rig()
        for view in rig['views']:
            view['camera'].update(width=7, height=6)
        rgb = np.arange(126, dtype=np.uint8).reshape(6, 7, 3)
        request = {'cameras': copy.deepcopy(rig), 'images': {
            v['key']: rgb_encode(rgb) for v in rig['views'] if v['enabled']}}
        request['cameras']['views'].reverse()  # Canonical order is not a wire restriction.
        decoded = observation_images(request, rig)
        np.testing.assert_array_equal(decoded['observation.images.camera2'], rgb)
        for mutation in ('missing', 'disabled', 'pose'):
            bad = copy.deepcopy(request)
            if mutation == 'missing':
                del bad['images']['observation.images.camera2']
            elif mutation == 'disabled':
                bad['images']['observation.images.camera3'] = rgb_encode(rgb)
            else:
                bad['cameras']['views'][-1]['camera']['position_world_m'][0] += .1
            with self.assertRaises(ValueError):
                observation_images(bad, rig)
        legacy = {'camera': {'camera': 'v3_oblique', 'width': 7, 'height': 6}, 'image': rgb_encode(rgb)}
        np.testing.assert_array_equal(observation_images(legacy, legacy['camera'])['observation.images.camera1'], rgb)

    def test_performance_distinguishes_steady_motion_from_chunk_velocity_reversal(self):
        from integration.kitchen.vla.performance import PerformanceAudit
        from types import SimpleNamespace
        audit = PerformanceAudit()
        q0 = np.zeros(16)
        q1 = q0.copy(); q1[0] = .028
        q2 = q1.copy(); q2[0] += .028
        audit.step(1., .004, q0, q1, np.zeros(7), False)
        audit.step(1.04, .004, q1, q2, np.zeros(7), False)
        audit.step(1.08, .004, q2, q1, np.zeros(7), True)
        client = SimpleNamespace(timings=[{'warmup':True,'client_ms':100.,'server_ms':90.},
                                          {'warmup':False,'client_ms':12.,'server_ms':10.}])
        report = audit.report(client)
        self.assertAlmostEqual(report['metrics']['apply_interval_ms']['mean'],40.)
        self.assertAlmostEqual(report['metrics']['boundary_command_accel_rad_s2']['max'],35.)
        self.assertAlmostEqual(report['metrics']['interior_command_accel_rad_s2']['max'],0.)
        self.assertEqual(report['joint_direction_reversals_above_002_rad_s'],1)
        self.assertEqual(report['metrics']['steady_client_ms']['mean'],12.)

    def test_constant_and_nearly_constant_joints_have_stable_normalization(self):
        from integration.kitchen.vla.normalization import stable_stats
        values = np.full((723, 16), np.float32(-.7853981852531433))
        values[:, 9] += np.linspace(0, .0001, len(values))
        stats = stable_stats(values)
        normalized = (values-stats['mean'])/stats['std']
        self.assertLess(float(np.abs(normalized).max()), .1)
        np.testing.assert_allclose(normalized*stats['std']+stats['mean'], values)
        self.assertEqual(stable_stats(values, width_state=True)['std'][7], .0001)

    def test_rgb_round_trip_and_corrupt_size(self):
        source = np.arange(6*7*3, dtype=np.uint8).reshape(6, 7, 3)
        value = rgb_encode(source)
        np.testing.assert_array_equal(rgb_decode(value), source)
        value['shape'][0] = 8
        with self.assertRaises(ValueError):
            rgb_decode(value)
        with self.assertRaises(ValueError):
            rgb_encode(source.astype(float))

    def test_delayed_chunks_discard_expired_actions_and_keep_model_prefix(self):
        buffer = ChunkBuffer()
        a = np.arange(10*16, dtype=np.float32).reshape(10, 16)
        m = a/10
        self.assertTrue(buffer.merge(4, a, m, 7))
        np.testing.assert_array_equal(buffer.get(7), a[3])
        np.testing.assert_array_equal(buffer.prefix(9), m[5:])
        self.assertEqual(buffer.remaining(9), 5)
        self.assertFalse(buffer.merge(0, a, m, 14))
        np.testing.assert_array_equal(buffer.get(9), a[5])
        self.assertIsNone(buffer.get(14))
        with self.assertRaises(ValueError):
            buffer.merge(20, a, m, 14)

    def test_reject_nonfinite_or_wrong_embodiment(self):
        for bad in [np.zeros((2, 44)), np.full((2, 16), np.nan), np.zeros((0, 16))]:
            with self.assertRaises(ValueError):
                action_chunk(bad)

    def test_http_boundary_keeps_policy_and_simulator_separate(self):
        class Backend:
            metadata = {'fps': 25, 'model_sha256': 'first'}
            def infer(self, request):
                self.request = request
                return np.zeros((3, 16)), np.ones((3, 16))
        backend = Backend()
        server = serve(backend, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = PolicyClient(port=server.server_port)
            a, m = client.infer({'frame': 6, 'state': [0]*16, 'prompt': 'Place the apple.'},
                                prefix=np.ones((2, 16)), delay=1, reset=True)
            self.assertEqual(a.shape, (3, 16))
            np.testing.assert_array_equal(m, 1)
            self.assertEqual(backend.request['inference_delay'], 1)
            self.assertTrue(backend.request['reset'])
            backend.metadata = {'fps': 25, 'model_sha256': 'changed'}
            with self.assertRaises(ValueError):
                client.infer({'frame': 7, 'state': [0]*16, 'prompt': 'Place the apple.'})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
