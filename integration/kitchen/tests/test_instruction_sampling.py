"""Per-instruction sample counts and hard-linked final files."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from integration.kitchen.data import collect_dataset as dataset
from integration.kitchen.data.optimized_collection import copy_immutable
from integration.kitchen.tasks.catalog import cases


class InstructionSamplingTests(unittest.TestCase):
    def plan(self, per_instruction=40, selected=None):
        selected = selected or cases()
        return {'cases': selected, 'base_seed': 0, 'seed_scheme': 'per_sample',
                'samples_per_instruction': per_instruction,
                'samples': dataset.instruction_samples(selected, per_instruction)}

    def test_forty_per_instruction_matches_the_collection_plan(self):
        plan = self.plan()
        count = {c['case_id']: dataset.case_samples(plan, c) for c in plan['cases']}
        by_task = {}
        for c in plan['cases']:
            by_task.setdefault(c['task'], set()).add(count[c['case_id']])
        self.assertEqual(by_task['B1'], {3})      # one prompt, 15 tray loads
        self.assertEqual(by_task['R3'], {14})     # one prompt, 3 oven occupancies
        for task in ('L1', 'L2', 'R1', 'R2', 'R4', 'R5', 'C04', 'C05', 'C08', 'D01', 'D05', 'D09'):
            self.assertEqual(by_task[task], {40}, task)
        self.assertEqual(sum(count.values()), 3927)
        self.assertEqual(sum(count[c['case_id']] for c in plan['cases'] if c['task'] == 'C05'), 320)

    def test_every_slot_and_retry_gets_a_unique_contiguous_seed(self):
        plan = self.plan()
        slots = list(dataset.slots(plan))
        self.assertEqual(len(slots), 3927)
        first = [dataset.attempt_seed(plan, c, s, 0) for c, s in slots]
        self.assertEqual(first, list(range(3927)))
        retries = [dataset.attempt_seed(plan, c, s, a) for c, s in slots for a in (1, 2)]
        self.assertEqual(sorted(first + retries), list(range(3 * 3927)))

    def test_cli_writes_explicit_counts_and_rejects_both_options(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(dataset, 'run_path', side_effect=Path):
            output = Path(temporary) / 'plan'
            ids = ['kitchen_expert_v3:B1', 'kitchen_expert_v3:B1:apple', 'kitchen_expert_v3:C05:apple:burger']
            self.assertEqual(dataset.main(['--output', str(output), '--case-ids', *ids,
                                           '--samples-per-instruction', '6', '--plan-only']), 0)
            plan = json.loads((output / 'plan.json').read_text())
            self.assertNotIn('samples_per_case', plan)
            self.assertEqual(plan['samples'], {ids[0]: 3, ids[1]: 3, ids[2]: 6})
            self.assertEqual(len(list(dataset.slots(plan))), 12)
            with self.assertRaises(SystemExit):
                dataset.main(['--output', str(Path(temporary) / 'both'), '--samples-per-case', '2',
                              '--samples-per-instruction', '40', '--plan-only'])

    def test_uniform_plans_still_use_samples_per_case(self):
        plan = {'cases': cases()[:3], 'samples_per_case': 2, 'base_seed': 5}
        self.assertEqual(len(list(dataset.slots(plan))), 6)
        self.assertEqual(dataset.attempt_seed(plan, plan['cases'][2], 1, 1), 5 + 1 + 2)


class FinalFileTests(unittest.TestCase):
    def test_final_file_is_a_hard_link_of_the_sealed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, final = Path(temporary) / 'direct.h5', Path(temporary) / 'trajectory.h5'
            source.write_bytes(b'sealed')
            copy_immutable(source, final)
            self.assertEqual(os.stat(source).st_ino, os.stat(final).st_ino)
            copy_immutable(source, final)  # resume: identical content accepted
            other = Path(temporary) / 'other.h5'; other.write_bytes(b'different')
            with self.assertRaisesRegex(ValueError, 'differs'):
                copy_immutable(other, final)

    def test_falls_back_to_copy_when_links_are_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary, patch('os.link', side_effect=OSError('EXDEV')):
            source, final = Path(temporary) / 'a.json', Path(temporary) / 'b.json'
            source.write_text('{}')
            copy_immutable(source, final)
            self.assertNotEqual(os.stat(source).st_ino, os.stat(final).st_ino)
            self.assertEqual(final.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
