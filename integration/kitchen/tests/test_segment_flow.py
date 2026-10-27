"""Segment boundaries must not become stops inside a continuous route."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np

from integration.kitchen.planning.expert import KitchenExpert
from integration.kitchen.tasks.execution import TaskExecution


class SegmentFlowTests(unittest.TestCase):
    def expert(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        scratch=SimpleNamespace(arms={'right':SimpleNamespace(qpos=tuple(range(7)))},
            model=Mock(),data=SimpleNamespace(qpos=np.zeros(7)))
        expert.view=lambda:scratch
        expert.stream_joints=Mock();expert.mark=Mock();expert.line=Mock();expert.return_home=Mock()
        return expert,scratch

    def test_exit_and_door_approach_are_planned_before_one_execution(self):
        expert,scratch=self.expert();observed=[]
        def plan(view,goal,side):
            expert.stream_joints.assert_not_called()
            observed.append(view.data.qpos.copy())
            return [np.full(7,len(observed)*.1)]
        with patch('integration.kitchen.planning.meal_geometry.approach_path',side_effect=plan),\
             patch('integration.kitchen.planning.motion.mujoco.mj_forward'):
            expert.free_pose('right',np.ones(3),np.eye(3),via=[(np.zeros(3),np.eye(3))])
        np.testing.assert_allclose(observed[0],0.)
        np.testing.assert_allclose(observed[1],.1)
        expert.stream_joints.assert_called_once()
        np.testing.assert_allclose(expert.stream_joints.call_args.args[1],[np.full(7,.1),np.full(7,.2)])
        expert.line.assert_not_called();expert.return_home.assert_not_called()

    def test_failed_combined_plan_clears_oven_before_replanning_door(self):
        expert,scratch=self.expert();order=[]
        expert.line.side_effect=lambda *a:order.append('withdraw')
        expert.stream_joints.side_effect=lambda *a,**kw:order.append('door_route')
        with patch('integration.kitchen.planning.meal_geometry.approach_path',
                   side_effect=[[np.full(7,.1)],ValueError('blocked'),[np.full(7,.2)]]),\
             patch('integration.kitchen.planning.motion.mujoco.mj_forward'):
            expert.free_pose('right',np.ones(3),np.eye(3),via=[(np.zeros(3),np.eye(3))],home_fallback=True)
        self.assertEqual(order,['withdraw','door_route'])
        self.assertEqual(expert.stream_joints.call_count,1)
        expert.return_home.assert_not_called()
        self.assertEqual(expert.mark.call_args.args[0],'route_via_fallback')

    def test_r1_passes_exit_explicitly_without_hidden_pending_motion(self):
        exit_pose=(np.zeros(3),np.eye(3))
        expert=SimpleNamespace(meal=Mock(return_value=exit_pose),close_door=Mock(),mark=Mock())
        case={'meal':'burger','fruit':None};quality=Mock()
        with patch('integration.kitchen.validation.success.validate_skill'):
            TaskExecution.skill(expert,'R1',case,quality)
        expert.meal.assert_called_once_with('burger','oven',defer_oven_exit=True)
        self.assertIs(expert.close_door.call_args.kwargs['approach_via'],exit_pose)
