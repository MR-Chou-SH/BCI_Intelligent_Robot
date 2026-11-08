import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.planning.common import ExpertFailure


class ButtonContinuityTests(unittest.TestCase):
    def test_contact_starts_without_point_settling_and_stops_on_device_event(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        dev=SimpleNamespace(heating=None,heated=set(),angle=lambda name:.0032)
        controller=SimpleNamespace(devices=dev,tick=0)
        expert.platform=SimpleNamespace(controller=controller)
        expert.mark=Mock();expert.wait=Mock();expert.point=Mock();calls=[]
        device={'phase':'device','target':'microwave_start_geom'}
        def execute(targets,contract=None):
            calls.append((len(targets),contract));controller.tick+=len(targets)*20
            if contract is device and sum(c is device for _,c in calls)==2:dev.heating='hotdog_container'
        expert.execute=execute
        expert.execute_button_targets([np.zeros(16)]*60,31,device,'hotdog_container')
        self.assertEqual(calls,[(25,None),(6,None),(1,device),(1,device)])
        expert.wait.assert_not_called();expert.point.assert_not_called()
        self.assertEqual(expert.mark.call_args.kwargs['press_sim_seconds'],.08)

    def test_no_activation_fails_after_bounded_endpoint_budget(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        expert.platform=SimpleNamespace(controller=SimpleNamespace(tick=0,
            devices=SimpleNamespace(heating=None,heated=set())))
        expert.mark=Mock();expert.execute=Mock()
        with self.assertRaisesRegex(ExpertFailure,'BUTTON_PRESS_DID_NOT_START_HEATING'):
            expert.execute_button_targets([np.zeros(16)]*4,2,{},'hotdog_container')
        self.assertEqual(expert.execute.call_count,1+2+8)
