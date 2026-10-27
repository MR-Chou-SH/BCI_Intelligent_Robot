"""Delivery plans must retain both tray and food goals, including replay labels."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from integration.kitchen.tasks.catalog import cases,chain,known_cases
from integration.kitchen.tasks.execution import TaskExecution
from integration.kitchen.validation.success import validate_segments,validate_final,validate_timeline


class DeliveryCatalogTests(unittest.TestCase):
    def case(self,task):return next(c for c in cases() if c['task']==task)

    def test_no_duplicate_task_inputs_chains_or_repeated_hotdog_name(self):
        keys=[(tuple(chain(c)),c['fruit'],c['meal'],c['initial_occupancy']) for c in cases()]
        self.assertEqual(len(keys),len(set(keys)))
        self.assertFalse(any('hotdog:hotdog' in c['case_id'] for c in cases()))
        legacy=next(c for c in known_cases('kitchen_expert_v2') if c['case_id']=='kitchen_expert_v2:C01:apple')
        validate_timeline(legacy,[{'skill':'L1','start_tick':0,'end_tick':20}],0,20)

    def test_heating_chain_requires_delivery_suffix(self):
        for task,prefix in [('D05',[]),('D08',['L1']),('D09',['L2'])]:
            case=self.case(task)
            for heat in [['R1','R4','R2'],['R3','R1','R4','R2'],['R4','R2'],['R2']]:
                validate_segments(case,[{'skill':s} for s in prefix+heat+['B1']])
                with self.assertRaises(ValueError):validate_segments(case,[{'skill':s} for s in prefix+heat])

    def test_d03_prepares_loaded_open_oven_before_recording(self):
        calls=[]
        fake=SimpleNamespace(mark=lambda *a,**kw:None,wait=lambda *a:None,
            open_door=lambda:calls.append('open'),close_door=lambda:calls.append('close'),
            meal=lambda meal,destination:calls.append((meal,destination)))
        case=self.case('D03');TaskExecution.prepare(fake,case)
        self.assertEqual(calls,['open',(case['meal'],'oven')])

    def test_delivered_tray_does_not_mask_lost_food_or_heat_marker(self):
        case=self.case('D09');quality=Mock();quality.failures=[];quality.tray_tilt_peak=0.
        quality.platform.controller.devices.heated={case['meal']+'_container'}
        validate_final(quality,case)
        quality.require.assert_called_with('tray:delivered',2.)
        self.assertEqual([c.args[0] for c in quality.tray_object.call_args_list],
                         [case['fruit'],case['meal']+'_container'])
        quality.tray_object.side_effect=RuntimeError('FOOD_FELL')
        with self.assertRaisesRegex(RuntimeError,'FOOD_FELL'):validate_final(quality,case)
        quality.tray_object.side_effect=None;quality.platform.controller.devices.heated=set()
        with self.assertRaisesRegex(ValueError,'HEATED_MEAL_MARKER_LOST'):validate_final(quality,case)

    def test_loaded_delivery_requires_more_force_than_empty_tray(self):
        import numpy as np
        from integration.kitchen.planning.expert import KitchenExpert
        for loaded,expected_steps in [(False,1),(True,2)]:
            with self.subTest(loaded=loaded):
                expert=KitchenExpert.__new__(KitchenExpert);steps=[]
                expert.platform=SimpleNamespace(controller=SimpleNamespace(reference=np.zeros(16)))
                def feedback():
                    force=9. if len(steps)>=2 else 5.
                    arm={'pad_normal_forces_n':[force,force]}
                    return {'contact_execution':{'measured':{'grasp':{
                        'by_arm':{'left':arm,'right':arm},'pad_normal_forces_n':[force]*4}}}}
                expert.feedback=feedback;expert.execute=lambda *args:steps.append(args);expert.wait=Mock()
                expert.dual_grip(.05,{'payload_ids':['wood_tray','apple'] if loaded else ['wood_tray']})
                self.assertEqual(len(steps),expected_steps)
                expert.wait.assert_called_once_with(.16)
