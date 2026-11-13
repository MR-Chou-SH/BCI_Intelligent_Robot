"""One fine instruction must ask for one behaviour in every task context."""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.tasks.catalog import cases
from integration.kitchen.tasks.execution import FRUIT_TRAY_SLOT
from integration.kitchen.tasks.language import coarse_instruction, instruction
from integration.kitchen.validation.success import validate_final, validate_initial


def bare_expert(calls):
    expert = KitchenExpert.__new__(KitchenExpert)
    expert.mark = lambda stage, **kw: calls.append(('stage', stage, kw.get('skill')))
    expert.wait = lambda seconds: None
    expert.open_door = lambda **kw: calls.append(('door', 'open'))
    expert.close_door = lambda **kw: calls.append(('door', 'close'))
    expert.fruit = lambda fruit, wash=False, slot=0.: calls.append(('fruit', fruit, wash, slot))
    expert.meal = lambda meal, destination='tray', **kw: calls.append(('meal', meal, destination))
    expert.clear_left_corridor = lambda: calls.append(('clear', 'left'))
    expert.return_home = lambda side, **kw: calls.append(('home', side))
    expert.settle_quality = lambda quality, key: calls.append(('settle', key))
    return expert


class FruitPlacementTests(unittest.TestCase):
    def test_fruit_slot_is_the_left_half_with_or_without_a_meal(self):
        self.assertLess(FRUIT_TRAY_SLOT, 0.)
        for case in (c for c in cases() if c['fruit'] and c['task'] != 'B1'):
            skill = next(s for s in ('L1', 'L2') if s in (case['task'], *case['skills']))
            calls = []
            expert = bare_expert(calls)
            with patch('integration.kitchen.validation.success.validate_skill'):
                expert.skill(skill, case, Mock())
            self.assertEqual([c for c in calls if c[0] == 'fruit'],
                             [('fruit', case['fruit'], skill == 'L2', FRUIT_TRAY_SLOT)], case['case_id'])

    def test_left_clearance_opens_the_following_language_stage(self):
        for case in (c for c in cases() if c['task'] in ('C05', 'C06', 'C07', 'C08', 'D06', 'D07', 'D08', 'D09')):
            calls = []
            expert = bare_expert(calls)
            expert.skill = lambda name, case, quality: calls.append(('skill', name))
            expert.heat_meal = lambda case, quality: calls.append(('skill', 'heat'))
            quality = Mock(failures=[], tray_tilt_peak=0.)
            quality.platform = SimpleNamespace(controller=SimpleNamespace(devices=SimpleNamespace(
                heated={'burger_container', 'hotdog_container'})))
            expert.run_case(case, quality)
            stages = [c for c in calls if c[:2] == ('stage', 'language_stage_started')]
            second = calls.index(stages[1])
            clear = calls.index(('clear', 'left'))
            # Fruit stage = placement + settle only; the retreat starts stage 2.
            self.assertLess(second, clear, case['case_id'])
            self.assertEqual(calls[second + 1], ('stage', 'composite_left_clearance', None))
            self.assertLess(calls.index(('skill', case['skills'][0])), second)


class LoadedDeliveryTests(unittest.TestCase):
    def deliveries(self):
        return [c for c in cases('atomic') if c['task'] == 'B1']

    def test_every_tray_load_once_with_one_command(self):
        rows = self.deliveries()
        loads = [(c['fruit'], c['meal']) for c in rows]
        self.assertEqual(len(rows), 15)
        self.assertEqual(len(set(loads)), 15)
        self.assertIn((None, None), loads)
        self.assertEqual({instruction('B1', c) for c in rows}, {'Deliver the serving tray to the user with both arms.'})
        self.assertEqual({coarse_instruction(c) for c in rows}, {'Deliver the serving tray to the user with both arms.'})

    def test_empty_tray_case_keeps_its_historical_definition(self):
        empty = next(c for c in self.deliveries() if not c['fruit'] and not c['meal'])
        self.assertEqual(empty, {'namespace': 'kitchen_expert_v3', 'task': 'B1', 'fruit': None, 'meal': None,
            'initial_occupancy': None, 'instruction': '双臂协作递送空托盘', 'skills': ['deliver_tray'],
            'execution_order': 'sequential', 'task_success': 'NOT_VERIFIED', 'case_id': 'kitchen_expert_v3:B1'})

    def test_preparation_places_the_load_like_composites_then_homes(self):
        for case in self.deliveries():
            calls = []
            bare_expert(calls).prepare(case)
            motion = [c for c in calls if c[0] != 'stage']
            fruit, meal = case['fruit'], case['meal']
            expected = []
            if fruit:
                expected.append(('fruit', fruit, False, FRUIT_TRAY_SLOT))
                if meal:expected.append(('clear', 'left'))
            if meal:expected += [('meal', meal, 'tray'), ('home', 'right')]
            if fruit or meal:expected.append(('home', 'left'))
            self.assertEqual(motion, expected, case['case_id'])

    def test_initial_tray_load_must_match_the_case(self):
        everything = ('apple', 'pear', 'banana', 'tomato', 'burger_container', 'hotdog_container')
        def controller(load):
            return SimpleNamespace(devices=SimpleNamespace(angle=lambda name: math.radians(100),
                meal_ready=lambda name: False, supports=lambda name, supports: name in load))
        for case in self.deliveries():
            load = {n for n in (case['fruit'], case['meal'] and case['meal'] + '_container') if n}
            validate_initial(controller(load), case)
            for extra in set(everything) - load:
                with self.assertRaisesRegex(ValueError, 'INITIAL_TRAY_LOAD_MISMATCH'):
                    validate_initial(controller(load | {extra}), case)
            for missing in load:
                with self.assertRaisesRegex(ValueError, 'INITIAL_TRAY_LOAD_MISMATCH'):
                    validate_initial(controller(load - {missing}), case)

    def test_delivery_success_requires_the_whole_load_to_stay_on_the_tray(self):
        for case in self.deliveries():
            checked = []
            quality = Mock(failures=[], tray_tilt_peak=0.)
            quality.platform = SimpleNamespace(controller=SimpleNamespace(devices=SimpleNamespace(heated=set())))
            quality.tray_object.side_effect = checked.append
            validate_final(quality, case)
            self.assertEqual(checked, [n for n in (case['fruit'], case['meal'] and case['meal'] + '_container') if n])


if __name__ == '__main__':
    unittest.main()
