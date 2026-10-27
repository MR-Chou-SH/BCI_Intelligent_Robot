"""Regression checks for the handoff between left and right expert skills."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.planning.common import ExpertFailure
from integration.kitchen.tasks.catalog import cases


class CompositeHandoffTests(unittest.TestCase):
    def expert(self):
        expert = KitchenExpert.__new__(KitchenExpert)
        calls = []
        expert.mark = lambda stage, **kw: calls.append(('stage', stage))
        expert.skill = lambda name, case, quality: calls.append(('skill', name))
        expert.clear_left_corridor = lambda: calls.append(('clear', 'left'))
        expert.settle_quality = lambda quality, key: calls.append(('settle', key))
        expert.heat_meal = lambda case, quality: calls.append(('heat', case['meal']))
        expert.platform = SimpleNamespace(controller=SimpleNamespace(devices=SimpleNamespace(
            heated={'burger_container', 'hotdog_container'})))
        quality = Mock()
        quality.failures = []
        quality.platform = expert.platform
        quality.tray_object.side_effect = lambda name: calls.append(('quality', name))
        return expert, quality, calls

    def test_left_is_parked_and_fruit_rechecked_before_right_skill(self):
        for task in ('C05', 'C06'):
            with self.subTest(task=task):
                expert, quality, calls = self.expert()
                case = next(c for c in cases() if c['task'] == task)
                expert.run_case(case, quality)
                self.assertEqual(calls[:6], [('skill', 'L1' if task == 'C05' else 'L2'),
                    ('stage', 'composite_left_clearance'), ('clear', 'left'),
                    ('settle', case['fruit'] + ':tray'), ('quality', case['fruit']), ('skill', 'R5')])

    def test_handoff_also_precedes_the_heating_chain(self):
        for task in ('C07', 'C08'):
            expert, quality, calls = self.expert()
            case = next(c for c in cases() if c['task'] == task)
            expert.run_case(case, quality)
            self.assertLess(calls.index(('clear', 'left')), calls.index(('heat', case['meal'])))

    def test_single_skill_cases_do_not_get_an_extra_homing_motion(self):
        for task in ('L1', 'L2', 'R5'):
            expert, quality, calls = self.expert()
            case = next(c for c in cases() if c['task'] == task)
            expert.run_case(case, quality)
            self.assertNotIn(('clear', 'left'), calls)

    def test_failed_retreat_prevents_starting_right_skill(self):
        expert, quality, calls = self.expert()
        expert.clear_left_corridor = Mock(side_effect=ExpertFailure('blocked retreat'))
        case = next(c for c in cases() if c['task'] == 'C05')
        with self.assertRaisesRegex(ExpertFailure, 'blocked retreat'):
            expert.run_case(case, quality)
        self.assertNotIn(('skill', 'R5'), calls)


if __name__ == '__main__':
    unittest.main()
