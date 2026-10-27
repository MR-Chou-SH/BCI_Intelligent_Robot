import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration.kitchen.data import collect_dataset as dataset
from integration.kitchen.tools.render_dataset import render
from integration.kitchen.tasks.catalog import cases


class DatasetTests(unittest.TestCase):
    def plan(self):
        return {'cases': cases('atomic')[:1], 'samples_per_case': 2, 'base_seed': 10,
                'max_attempts': 3, 'jobs': 2, 'profile': 'research', 'case_timeout': 60,
                'identity': {'test': True}, 'video': {}}

    def test_full_matrix_and_disjoint_retry_seeds(self):
        plan = self.plan()
        plan['cases'] = cases()
        self.assertEqual(len(list(dataset.slots(plan))), 200)
        seeds = [dataset.attempt_seed(plan, s, a) for s in range(2) for a in range(3)]
        self.assertEqual(len(set(seeds)), 6)

    def test_failed_attempt_does_not_fill_slot_and_retry_is_bounded(self):
        plan = self.plan()
        def worker(job):
            case, seed, path, *_ = job
            path = Path(path)
            path.mkdir()
            report = {'case': case, 'seed': seed, 'status': 'FAILED_ATTEMPT',
                      'task_success': False, 'source_identity': plan['identity']}
            dataset.save(path / 'report.json', report)
            return {'case_id': case['case_id'], 'seed': seed, 'status': 'FAILED_ATTEMPT',
                    'report': str(path / 'report.json'), 'report_sha256': dataset.digest(path / 'report.json')}
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(dataset, 'ProcessPoolExecutor', side_effect=lambda **kw: ThreadPoolExecutor(max_workers=2)), \
                patch.object(dataset, 'one_case', side_effect=worker) as work, \
                patch.object(dataset, 'validate_previous'):
            qualified, missing = dataset.collect(Path(temporary), plan)
            self.assertEqual(qualified, [])
            self.assertEqual(len(missing), 2)
            self.assertEqual(work.call_count, 6)
            dataset.collect(Path(temporary), plan)
            self.assertEqual(work.call_count, 6)

    def test_interrupted_attempt_preserved_next_seed_selected(self):
        plan = self.plan()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            case = plan['cases'][0]
            parent = dataset.slot_path(output, case, 0)
            parent.mkdir(parents=True)
            directory = parent / 'attempt_01_seed10'
            directory.mkdir()
            (directory / 'partial.txt').write_text('keep')
            dataset.save(directory.with_suffix('.job.json'), {'case_id': case['case_id'], 'sample': 0, 'seed': 10})
            qualified, pending, missing = dataset.scan(output, plan)
            self.assertEqual(pending[0][2], 12)
            self.assertEqual((directory / 'partial.txt').read_text(), 'keep')
            self.assertFalse(qualified or missing)

    def test_receipt_corruption_rejected(self):
        plan = self.plan()
        case = plan['cases'][0]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            directory = output / 'attempt'
            directory.mkdir()
            dataset.save(directory / 'report.json', {'case': case, 'seed': 10, 'status': 'FAILED_ATTEMPT'})
            dataset.save(directory.with_suffix('.result.json'), {
                'case_id': case['case_id'], 'seed': 10, 'status': 'FAILED_ATTEMPT',
                'report': str(directory / 'report.json'), 'report_sha256': 'corrupt'})
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                dataset.recover_attempt(directory, case, 10, output, plan['identity'])

    def test_two_videos_unique_and_resume_does_not_render_again(self):
        plan = self.plan()
        case = plan['cases'][0]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            rows = []
            for sample in (1, 2):
                report = output / f'report{sample}.json'
                dataset.save(report, {'trajectory': str(output / 'source.h5'), 'verification': {'sha256': 'source'}})
                rows.append({'case_id': case['case_id'], 'sample': sample, 'report': str(report)})
            def video(source, destination, **kw):
                destination.write_bytes(b'mp4')
                destination.with_suffix('.video.json').write_text('{}')
                return {'source_duration_s': 20, 'trajectory_sha256': 'source'}
            with patch('integration.kitchen.data.replay.video', side_effect=video) as renderer, \
                    patch('integration.kitchen.tools.render_dataset.run_path',side_effect=Path):
                first = render(output, plan, rows)
                second = render(output, plan, rows)
                self.assertEqual(renderer.call_count, 2)
                self.assertNotEqual(first[0]['video'], first[1]['video'])
                self.assertEqual(first, second)
                Path(first[0]['video']).write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'hash/path mismatch'):
                    render(output, plan, rows)


if __name__ == '__main__':
    unittest.main()
