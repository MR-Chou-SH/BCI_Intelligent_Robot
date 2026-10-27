import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np

from integration.kitchen.planning.expert import KitchenExpert


class MotionContinuityTests(unittest.TestCase):
    def test_smoothing_sends_controller_validated_limit_values(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        controller=SimpleNamespace(reference=np.full(16,.1),
            prepare_joint_targets=lambda value,reference:np.maximum(value,0.))
        expert.platform=SimpleNamespace(controller=controller)
        commands,_,_=expert.smooth_targets('left',[np.full(7,-1e-16)],speed=.008)
        self.assertTrue(np.all(np.asarray(commands)>=0.))

    def test_stream_keeps_other_arm_and_contract_and_settles_only_once(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        reference=np.arange(16,dtype=float)/100
        controller=SimpleNamespace(reference=reference.copy(),tick=0,
            prepare_joint_targets=lambda value,reference: np.asarray(value).copy())
        expert.platform=SimpleNamespace(controller=controller)
        expert.contract=None;expert.events=[];expert.stage='test'
        contract={'arm':'left','phase':'carry','target':'apple'}
        commands=[];contracts=[]
        def execute(targets,active):
            commands.extend(np.asarray(t).copy() for t in targets);contracts.append(active)
            controller.reference=commands[-1].copy();controller.tick+=20*len(targets)
        expert.execute=execute;expert.joints=Mock()
        path=[reference[:7]+x for x in (.04,.08,.12)]
        expert.stream_joints('left',path,contract,speed=.008)
        expert.joints.assert_called_once()
        np.testing.assert_allclose(expert.joints.call_args.args[1],path[-1])
        self.assertTrue(all(c==contract for c in contracts))
        array=np.asarray(commands)
        np.testing.assert_allclose(array[:,8:],np.tile(reference[8:],(len(array),1)))
        np.testing.assert_allclose(array[:,7],reference[7])
        delta=np.diff(np.vstack([reference,array]),axis=0)[:,:7]
        self.assertLessEqual(np.max(np.abs(delta)),.008+1e-12)
        self.assertTrue(np.all(np.max(np.abs(delta),axis=1)>0))
        np.testing.assert_allclose(array[-1,:7],path[-1])

    def test_failed_cartesian_preplan_never_executes_partial_path(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        view=SimpleNamespace(arms={'left':SimpleNamespace(qpos=tuple(range(7)))},
                             data=SimpleNamespace(qpos=np.zeros(7)))
        expert.view=lambda:view;expert.stream_joints=Mock()
        good=SimpleNamespace(success=True,q=np.ones(7)*.1)
        bad=SimpleNamespace(success=False)
        with patch('integration.kitchen.planning.motion.solve_ik',side_effect=[good,bad,bad]):
            result=expert.continuous_poses('left',[(np.zeros(3),np.eye(3))]*2)
        self.assertFalse(result)
        expert.stream_joints.assert_not_called()

    def test_home_is_used_only_after_current_pose_route_fails(self):
        expert=KitchenExpert.__new__(KitchenExpert)
        expert.view=Mock();expert.mark=Mock();expert.return_home=Mock();expert.stream_joints=Mock()
        path=[np.zeros(7)]
        with patch('integration.kitchen.planning.meal_geometry.approach_path',return_value=path):
            expert.free_pose('right',np.zeros(3),np.eye(3),home_fallback=True)
        expert.return_home.assert_not_called()
        with patch('integration.kitchen.planning.meal_geometry.approach_path',side_effect=[ValueError('blocked'),path]):
            expert.free_pose('right',np.zeros(3),np.eye(3),home_fallback=True)
        expert.return_home.assert_called_once_with('right')
        self.assertIn('blocked',expert.mark.call_args.kwargs['reason'])


if __name__=='__main__':unittest.main()
