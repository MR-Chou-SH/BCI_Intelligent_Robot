"""Replay-derived training data: clock alignment, immutable seals and resume."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import numpy as np

from integration.kitchen.data import collect_dataset
from integration.kitchen.vla import replay_dataset as replay
from integration.kitchen.vla.dataset import trajectory_paths
from integration.kitchen.vla.images import camera_rig


class ReplayDatasetTests(unittest.TestCase):
    def test_fresh_policy_initial_seeds_use_reset_and_settle_without_expert(self):
        from integration.kitchen.vla.environment import PolicyEnvironment
        from integration.kitchen.vla.observation import robot_state
        from robot_arm.kitchen.platform import KitchenPlatform
        import mujoco
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary)/'trajectory.h5'
            source.with_name('report.json').write_text(json.dumps({'seed': 0, 'initialization_profile': 'research'}))
            def platform(*args):
                return KitchenPlatform(layout='kitchen_v3_norice_candidate_013', seed=0,
                                       initialization_profile='research')
            def sample(seed):
                with patch('integration.kitchen.vla.environment.policy_episode', return_value={
                        'annotation': {'coarse_instruction': 'Place the apple directly on the serving tray.'}}), \
                     patch('integration.kitchen.vla.environment.read_compact', return_value={
                        'task': {'task': 'L1', 'fruit': 'apple', 'meal': None}}), \
                     patch('integration.kitchen.vla.environment.restored_platform', side_effect=platform):
                    env = PolicyEnvironment(source, render=False, initial_seed=seed)
                try:
                    self.assertEqual(env.start_tick, 500)
                    self.assertEqual(env.platform.episode['seed'], seed)
                    scene = env.platform.scene
                    spec = mujoco.mjtState.mjSTATE_INTEGRATION
                    before = np.empty(mujoco.mj_stateSize(scene.model, spec))
                    mujoco.mj_getState(scene.model, scene.data, before, spec)
                    state = robot_state(scene)
                    after = np.empty_like(before)
                    mujoco.mj_getState(scene.model, scene.data, after, spec)
                    np.testing.assert_array_equal(before, after)
                    np.testing.assert_array_equal(state, np.array(env.observation()['state'], dtype=np.float32))
                    return scene.data.qpos.copy()
                finally:
                    env.close()
            a, b, c = sample(10000), sample(10000), sample(10001)
            np.testing.assert_array_equal(a, b)
            self.assertGreater(float(np.max(np.abs(a-c))), .001)

    def test_fifty_training_samples_and_unique_bounded_retry_seeds(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(collect_dataset, 'run_path', side_effect=Path):
            output = Path(temporary)/'collection'
            self.assertEqual(collect_dataset.main(['--output', str(output), '--suite', 'atomic',
                '--case-ids', 'kitchen_expert_v3:L1:apple', '--samples-per-case', '50', '--plan-only']), 0)
            plan = json.loads((output/'plan.json').read_text())
            self.assertEqual(len(list(collect_dataset.slots(plan))), 50)
            seeds = [collect_dataset.attempt_seed(plan, s, a) for s in range(50) for a in range(3)]
            self.assertEqual(len(set(seeds)), 150)
            self.assertEqual(plan['profile'], 'research')
            self.assertEqual(plan['postprocessing']['mode'], 'continuous')

    def test_manifest_rejects_backup_duplicates_and_unqualified_rows(self):
        with tempfile.TemporaryDirectory() as temporary, patch('integration.kitchen.launch.run_path', side_effect=Path):
            path = Path(temporary)/'manifest.json'
            row = {'status': 'QUALIFIED_EXPERT', 'task_success': True,
                   'trajectory': '/tmp/example.h5', 'source_group': 'original'}
            path.write_text(json.dumps([row]))
            self.assertEqual(trajectory_paths(manifest=path), [Path('/tmp/example.h5')])
            path.write_text(json.dumps([row, {**row, 'trajectory': '/tmp/backup.h5'}]))
            with self.assertRaisesRegex(ValueError, 'source group'):
                trajectory_paths(manifest=path)
            path.write_text(json.dumps([{**row, 'task_success': False}]))
            with self.assertRaisesRegex(ValueError, 'unqualified'):
                trajectory_paths(manifest=path)

    def test_discodemo_latent_variants_share_split_group_but_reject_copies(self):
        from integration.kitchen.data.files import digest
        with tempfile.TemporaryDirectory() as temporary, patch('integration.kitchen.launch.run_path', side_effect=Path):
            base=Path(temporary);manifest=base/'manifest.json';rows=[]
            for z in range(2):
                report=base/f'report{z}.json';trajectory=str(base/f'{z}.h5')
                report.write_text(json.dumps({'generator':{'method':'discodemo'},'variant_id':str(z),
                    'trajectory':trajectory,'postprocessing':{'source_group':'same-initial'}}))
                rows.append({'status':'QUALIFIED_EXPERT','task_success':True,'report':str(report),
                    'report_sha256':digest(report),'trajectory':trajectory,
                    'variant_id':str(z),'source_group':'same-initial'})
            manifest.write_text(json.dumps(rows));self.assertEqual(len(trajectory_paths(manifest=manifest)),2)
            manifest.write_text(json.dumps(rows+[rows[0]]))
            with self.assertRaisesRegex(ValueError,'source group/variant'):trajectory_paths(manifest=manifest)
            # Changing the claimed variant without a matching hashed report fails.
            rows[1]['variant_id']='forged';manifest.write_text(json.dumps(rows))
            with self.assertRaisesRegex(ValueError,'source group/variant'):trajectory_paths(manifest=manifest)

    def test_actual_replay_callback_exports_preaction_frames_and_resumes(self):
        rig = camera_rig()
        for view in rig['views']:
            view['camera'].update(width=7, height=6)
        episode = {'source': '/tmp/source.h5', 'source_sha256': 'example-source',
                   'ticks': np.array([100, 120]), 'state': np.zeros((2, 16), dtype=np.float32),
                   'action': np.ones((2, 16), dtype=np.float32)}
        episode['state'][1] = .25
        scene, controller = object(), SimpleNamespace(tick=100)
        def verify(path, *, observer):
            for index, tick in enumerate((100, 120, 140)):
                controller.tick = tick
                observer(scene, controller, index)
            return {'status': 'CONTROL_RESIMULATION_PASS', 'task_success': True,
                    'maximum_qpos_error': 0., 'maximum_qvel_error': 0., 'compared_checkpoints': 2}
        rgb = np.zeros((6, 7, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(replay, 'policy_episode', return_value=episode), \
             patch('integration.kitchen.data.replay.verify', side_effect=verify) as runner, \
             patch.object(replay, 'CameraRig') as camera, \
             patch.object(replay, 'robot_state', side_effect=lambda _: episode['state'][(controller.tick-100)//20]):
            camera.return_value.render_all.return_value = {
                v['key']: rgb for v in rig['views'] if v['enabled']}
            output = Path(temporary)/'replay'
            result = replay.replay([episode['source']], output, rig)
            item = result['episodes'][0]
            state, action = replay.replay_arrays(output, item, episode)
            np.testing.assert_array_equal(state, episode['state'])
            np.testing.assert_array_equal(action, episode['action'])
            self.assertEqual(camera.return_value.render_all.call_count, 2)
            self.assertFalse((output/'example-source/camera3').exists())
            self.assertEqual(replay.replay([episode['source']], output, rig, resume=True), result)
            self.assertEqual(runner.call_count, 1)
            changed = copy.deepcopy(rig); changed['views'][0]['camera']['lookat'][0] += .01
            with self.assertRaisesRegex(ValueError, 'changed'):
                replay.replay([episode['source']], output, changed, resume=True)
            (output/item['policy_data']['file']).write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                replay.replay([episode['source']], output, rig, resume=True)

    def test_replay_failure_never_seals_observations(self):
        episode = {'source': '/tmp/source.h5', 'source_sha256': 'bad-source',
                   'ticks': np.array([100]), 'state': np.zeros((1,16)), 'action': np.zeros((1,16))}
        with tempfile.TemporaryDirectory() as temporary, \
             patch('integration.kitchen.data.replay.verify', return_value={
                 'status': 'CONTROL_RESIMULATION_DIVERGED', 'task_success': True}):
            output = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'did not qualify'):
                replay.replay_episode(episode, output, camera_rig())
            self.assertFalse(list(output.rglob('episode.json')))


if __name__ == '__main__':
    unittest.main()
