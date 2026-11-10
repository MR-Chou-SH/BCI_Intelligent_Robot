"""Batch evaluator talks to an external service and keeps errors out of task counts."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from integration.kitchen.data.files import digest, replace_json
from integration.kitchen.vla import evaluate as batch
from integration.kitchen.vla.images import camera_rig


class BatchEvaluationTests(unittest.TestCase):
    def test_summary_distinguishes_task_failure_error_and_pending(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            result = batch.summarize(output, [10, 11, 12, 13], [
                {'seed': 10, 'status': 'SUCCESS'}, {'seed': 11, 'status': 'FAILED'},
                {'seed': 12, 'status': 'ERROR'}])
            self.assertEqual((result['success'], result['failed'], result['errors'], result['pending']), (1,1,1,1))
            self.assertEqual(result['success_rate'], .25)
            self.assertEqual(result['success_rate_among_valid'], .5)

    def test_trial_checks_model_and_scene_identity_and_process_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            for index, (status, code, sha, expected) in enumerate([
                ('POLICY_SUCCESS', 0, 'wanted', 'SUCCESS'),
                ('POLICY_FAILED', 1, 'wanted', 'FAILED'),
                ('POLICY_ERROR', 1, 'wanted', 'ERROR'),
                ('POLICY_SUCCESS', 0, 'other-model', 'ERROR'),
                ('POLICY_SUCCESS', 1, 'wanted', 'ERROR')]):
                directory = Path(temp)/f'trial{index}'
                directory.mkdir()
                replace_json(directory/'report.json', {'initial_seed': 10000,
                    'initial_state_mode': 'fresh_research_seed', 'status': status,
                    'task_success': status == 'POLICY_SUCCESS', 'policy': {'model_sha256': sha}})
                with patch.object(batch.subprocess, 'Popen') as process:
                    process.return_value.wait.return_value = code
                    process.return_value.poll.return_value = code
                    row = batch.trial(['rollout-only'], directory, 10000, 'wanted', 5.)
                self.assertEqual(row['status'], expected)
                self.assertEqual(row['report_sha256'], digest(directory/'report.json'))

    def test_timeout_and_missing_report_are_errors(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(batch.subprocess, 'Popen') as process:
            process.return_value.wait.side_effect = subprocess.TimeoutExpired('rollout', 1.)
            with patch.object(batch, 'stop_process') as stop:
                row = batch.trial(['rollout-only'], Path(temp)/'timeout', 10000, 'model', 1.)
                self.assertEqual(row['status'], 'ERROR')
                self.assertIn('wall-clock', row['error'])
                stop.assert_called_once_with(process.return_value)

    def test_training_seed_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            checkpoint, dataset = root/'checkpoint', root/'dataset'
            checkpoint.mkdir(); dataset.mkdir()
            source = root/'trajectory.h5'
            replace_json(root/'report.json', {'seed': 7, 'initialization_profile': 'research',
                                              'verification': {'sha256': 'source'}})
            replace_json(dataset/'kitchen_export.json', {'source_episodes': [
                {'source': str(source), 'source_sha256': 'source'}]})
            replace_json(checkpoint/'kitchen_policy.json', {'dataset': str(dataset),
                'camera': camera_rig()['views'][0]['camera'], 'camera_rig': camera_rig()})
            (checkpoint/'model.safetensors').write_bytes(b'fake-model-for-boundary-tests')
            with self.assertRaisesRegex(ValueError, 'occur in training'):
                batch.training_identity(checkpoint, [7, 8])
            identity = batch.training_identity(checkpoint, [10000])
            self.assertEqual(identity['training_seeds'], [7])

    def test_multiple_seeds_share_one_folder_and_resume_does_not_execute_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root/'trajectory.h5'; source.write_bytes(b'raw')
            replace_json(root/'report.json', {'initialization_profile': 'research'})
            args = SimpleNamespace(run=root, checkpoint=None, manifest=None, output=root/'batch',
                seed_start=10000, episodes=3, seconds=60., clock='realtime', replan_remaining=40,
                host='127.0.0.1', port=8001, video=True, audit=True, episode_timeout=600., resume=False)
            identity = {'model_sha256': 'model', 'camera_rig': camera_rig()}
            calls = []
            def trial(command, directory, seed, sha, timeout):
                calls.append(command)
                self.assertEqual(command[2], 'rollout')  # Never launches serve or train.
                self.assertEqual(command[command.index('--port')+1], '8001')
                self.assertEqual(command[command.index('--clock')+1], 'realtime')
                self.assertEqual(command[command.index('--replan-remaining')+1], '40')
                self.assertIn('--video', command)
                directory.mkdir()
                report = directory/'report.json'; replace_json(report, {'seed': seed})
                return {'seed': seed, 'status': {10000:'SUCCESS', 10001:'FAILED', 10002:'ERROR'}[seed],
                        'report': str(report), 'report_sha256': digest(report)}
            with patch.object(batch, 'trajectory_paths', return_value=[source]), \
                 patch('integration.kitchen.data.replay.read_compact', return_value={'task': {'task': 'L1'}}), \
                 patch('integration.kitchen.data.collect_experts.source_identity', return_value={'test': True}), \
                 patch.object(batch, 'training_identity', return_value=identity), \
                 patch.object(batch, 'trial', side_effect=trial):
                self.assertEqual(batch.evaluate(args), 2)
                summary = json.loads((args.output/'summary.json').read_text())
                self.assertEqual((summary['success'], summary['failed'], summary['errors']), (1,1,1))
                self.assertEqual(len(calls), 3)
                args.resume = True
                self.assertEqual(batch.evaluate(args), 2)
                self.assertEqual(len(calls), 3)
                args.seconds = 30.
                with self.assertRaisesRegex(ValueError, 'settings changed'):
                    batch.evaluate(args)
                args.seconds = 60.
                report = Path(summary['results'][0]['report']); report.write_text('{}')
                with self.assertRaisesRegex(ValueError, 'report changed'):
                    batch.evaluate(args)


if __name__ == '__main__':
    unittest.main()
