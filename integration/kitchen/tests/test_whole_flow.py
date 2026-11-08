import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.planning.common import ExpertFailure
from integration.kitchen.planning.route_flow import rounded_positions
from integration.kitchen.tools.motion_audit import quiet_intervals


class WholeFlowTests(unittest.TestCase):
    def test_heated_door_gate_has_no_unconditional_wait(self):
        e=KitchenExpert.__new__(KitchenExpert);e.mark=Mock();e.wait=Mock()
        e.platform=SimpleNamespace(controller=SimpleNamespace(tick=100,
            devices=SimpleNamespace(heated={'hotdog_container'},heating=None)))
        e.wait_heat_before_open('hotdog_container');e.wait.assert_not_called()
        self.assertEqual(e.mark.call_args.kwargs['residual_wait_seconds'],0.)

    def test_door_heat_gate_waits_for_real_completion_and_rejects_abort(self):
        e=KitchenExpert.__new__(KitchenExpert);e.mark=Mock()
        dev=SimpleNamespace(heated=set(),heating='hotdog_container')
        c=SimpleNamespace(tick=100,devices=dev);e.platform=SimpleNamespace(controller=c)
        def wait(seconds):
            c.tick+=20
            if c.tick==160:dev.heated.add('hotdog_container')
        e.wait=Mock(side_effect=wait);e.wait_heat_before_open('hotdog_container')
        self.assertEqual(e.wait.call_count,3)
        dev.heated.clear();dev.heating=None
        with self.assertRaisesRegex(ExpertFailure,'HEAT_ABORTED'):e.wait_heat_before_open('hotdog_container')

    def test_open_jaws_and_door_approach_are_started_while_heating(self):
        e=KitchenExpert.__new__(KitchenExpert);e.held=e.contract=None;e.mark=Mock();e.wait=Mock()
        dev=SimpleNamespace(meal_ready=lambda n:True,angle=lambda n:0.,heating=None,heated=set())
        e.platform=SimpleNamespace(controller=SimpleNamespace(devices=dev))
        model=SimpleNamespace(geom=lambda n:SimpleNamespace(id=0),body=lambda n:SimpleNamespace(id=0),geom_size=np.ones((1,3))*.01)
        view=SimpleNamespace(model=model,data=SimpleNamespace(geom_xpos=np.zeros((1,3)),xmat=np.eye(3).reshape(1,9)))
        e.view=lambda:view;e.scene=SimpleNamespace(arms={'right':SimpleNamespace(pad_geoms=[0])})
        e.set_empty_jaws=Mock();e.continuous_poses=Mock(return_value=True);e.call=Mock()
        def press(*args):dev.heating='hotdog_container'
        e.press_button_continuously=Mock(side_effect=press)
        def open_door(**kwargs):
            self.assertEqual(e.set_empty_jaws.call_args.args,(0.,))
            self.assertEqual(dev.heating,'hotdog_container');self.assertFalse(dev.heated)
            self.assertEqual(kwargs['wait_for_heat'],'hotdog_container')
        e.open_door=Mock(side_effect=open_door)
        with patch('integration.kitchen.planning.expert.tcp_pose',return_value=(np.zeros(3),np.eye(3))),\
             patch('integration.kitchen.planning.expert.geometry_bounds',return_value=(np.zeros(3),np.ones(3)*.01)):
            e.heat_open('hotdog')
        e.wait.assert_not_called();e.open_door.assert_called_once()

    def test_release_gripper_has_no_fixed_empty_tail(self):
        e=KitchenExpert.__new__(KitchenExpert);q=np.zeros(16);q[15]=.005
        e.platform=SimpleNamespace(controller=SimpleNamespace(reference=q))
        e.execute=Mock();e.grip('right',0.,{'target':'hotdog_container','phase':'release'})
        targets=[v for call in e.execute.call_args_list for v in call.args[0]]
        self.assertEqual(len(targets),5);self.assertEqual(targets[-1][15],0.)

    def test_corner_round_keeps_cavity_tail_and_endpoints(self):
        points=np.array([[0,0,0],[1,0,0],[1,1,0],[2,1,0],[2,1,-1]])
        rounded=rounded_positions(points,radius=.015,protected_tail=2)
        np.testing.assert_array_equal(rounded[0],points[0]);np.testing.assert_array_equal(rounded[-1],points[-1])
        self.assertFalse(any(np.array_equal(p,points[1]) for p in rounded))
        self.assertTrue(any(np.array_equal(p,points[2]) for p in rounded))
        self.assertTrue(any(np.array_equal(p,points[3]) for p in rounded))

    def test_measured_quiet_audit_uses_recorded_ticks(self):
        intervals=quiet_intervals(np.arange(500,660,20),[True]*5+[False,True])
        self.assertEqual(intervals,[{'start_tick':500,'end_tick':600,'duration_s':.2}])
