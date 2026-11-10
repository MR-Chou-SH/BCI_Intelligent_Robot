"""Preplanned trajectory stress tests retain event clocks and private limits."""
import unittest
import numpy as np
from robot_arm.kitchen.platform import KitchenPlatform
from robot_arm.kitchen.control import DualArmController
from integration.kitchen.data.speed_stress import schedule,relax


class SpeedStressTests(unittest.TestCase):
    def source(self):
        n=200;commands=np.tile(np.linspace(0,.1,n)[:,None],(1,14))
        data={'start_tick':0,'actions/tick':np.arange(n)*20,'actions/joint_pos':commands,
            'replay/gripper_target':np.tile(np.linspace(.05,0,n)[:,None],(1,2)),
            'states/tick':np.arange(n+1)*20,'states/task_flags':np.zeros((n+1,1,2),dtype='u1'),
            'controller':{'reference':np.r_[np.zeros(7),.05,np.zeros(7),.05]}}
        context={'changes':[{'tick':0,'method':'mode','args':{}}],
            'events':[{'tick':0,'stage':'skill_started','skill':'L1'},
                      {'tick':4000,'stage':'skill_qualified','skill':'L1'}]}
        return data,context

    def test_global_compression_includes_jaws_and_static_phases(self):
        data,context=self.source()
        original,_,_=schedule(data,context,1)
        fast,events,stats=schedule(data,context,4)
        self.assertEqual(len(original),200);self.assertEqual(len(fast),88)
        self.assertEqual(stats['protected_physical_timer_frames'],50)
        np.testing.assert_array_equal(original[-1],fast[-1])
        self.assertEqual(events[-1]['tick'],len(fast)*20)
        self.assertEqual(events[-1]['source_tick'],4000)
        self.assertGreater(abs(fast[0,7]-.05),abs(original[0,7]-.05))
        with self.assertRaises(ValueError):schedule(data,context,4.1)

    def test_measured_device_timer_interval_is_not_time_compressed(self):
        data,context=self.source()
        data['states/task_flags'][50:,:,0]=1
        _,_,stats=schedule(data,context,4)
        # First50 frames provide the wash timer; last50 provide task stability.
        self.assertEqual(stats['protected_physical_timer_frames'],100)

    def test_local_speed_cap_changes_only_selected_contact_interval(self):
        data,context=self.source()
        context['changes']=[{'tick':0,'method':'mode','args':{}},
            {'tick':1000,'method':'mode','args':{'contact_contract':{'target':'button'}}},
            {'tick':2000,'method':'mode','args':{}}]
        fast,events,_=schedule(data,context,4)
        mixed,mixed_events,stats=schedule(data,context,4,segment_factors={'button':2})
        self.assertEqual(len(mixed)-len(fast),25-13)
        self.assertEqual(stats['local_output_frames']['button'],25)
        for source_tick in (0,1000):
            a=next(e['tick'] for e in events if e['source_tick']==source_tick)
            b=next(e['tick'] for e in mixed_events if e['source_tick']==source_tick)
            self.assertEqual(a,b)
        original,_,_=schedule(data,context,1)
        limited,_,_=schedule(data,context,1,segment_factors={'button':2})
        np.testing.assert_array_equal(original,limited)

    def test_relaxed_limits_are_scoped_to_one_controller(self):
        p=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0)
        other=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=0)
        try:
            before=other.controller.prepare_joint_targets(other.controller.reference.copy())
            limits=relax(p.controller,4)
            self.assertEqual(limits['joint_command_rad_s'],2.8)
            self.assertEqual(other.controller.velocity_limit,.7);self.assertEqual(DualArmController.velocity_limit,.7)
            goal=p.controller.reference.copy();goal[7]=.05
            self.assertLessEqual(abs(p.controller.prepare_joint_targets(goal)[7]-p.controller.reference[7]),.008+1e-12)
            np.testing.assert_array_equal(before,other.controller.prepare_joint_targets(other.controller.reference.copy()))
        finally:p.close();other.close()

    def test_semantic_repairs_find_washed_regrasp_without_case_specific_ticks(self):
        from integration.kitchen.planning.local_timing import repair_recipes,base_caps
        data,context=self.source();data['end_tick']=4000;data['metadata']={'object_ids':['pear']}
        data['states/task_flags'][90:,:,0]=1
        context['changes']=[{'tick':0,'method':'mode','args':{}},
            {'tick':2200,'method':'mode','args':{'contact_contract':{'target':'pear','phase':'acquire'}}},
            {'tick':2600,'method':'mode','args':{'contact_contract':{'target':'pear','phase':'carry'}}}]
        recipes=repair_recipes(data,context,{'error':'placement failed'})
        self.assertTrue(any(r['name']=='washed_prefix_cap3' for r in recipes))
        for recipe in recipes:
            _,_,stats=schedule(data,context,4,windows=recipe['windows'])
            self.assertTrue(stats['local_windows'])
        context['changes'].append({'tick':3000,'method':'mode','args':{'contact_contract':{'target':'microwave_start_geom'}}})
        self.assertEqual(base_caps(context),{'microwave_start_geom':2.})

    def test_collision_failure_after_wash_does_not_slow_unrelated_wash(self):
        from integration.kitchen.planning.local_timing import repair_recipes,collision_pair
        data,context=self.source();data['end_tick']=4000;data['metadata']={'object_ids':['pear']}
        data['states/task_flags'][40:,:,0]=1
        context['events']=[{'tick':0,'stage':'skill_started','skill':'L2'},
            {'tick':2000,'stage':'skill_qualified','skill':'L2'},
            {'tick':2000,'stage':'skill_started','skill':'R5'},
            {'tick':4000,'stage':'skill_qualified','skill':'R5'}]
        context['changes']=[{'tick':1000,'method':'mode','args':{'contact_contract':{'target':'pear','phase':'acquire'}}},
            {'tick':1400,'method':'mode','args':{'contact_contract':{'target':'pear','phase':'carry'}}}]
        failed={'failure_source_tick':3000,'error':'RELAXED_AUDIT: {"geom1":"left_link","geom2":"right_link","normal_force_n":80}'}
        self.assertEqual(collision_pair(failed),('left_link','right_link'))
        recipes=repair_recipes(data,context,failed)
        self.assertFalse(any(r['name'].startswith('washed') for r in recipes))
        self.assertTrue(any(r['name']=='failure_neighborhood_cap2' for r in recipes))

    def test_missing_wash_marker_gets_runtime_gate_before_global_slowdown(self):
        from integration.kitchen.planning.local_timing import repair_recipes
        data,context=self.source();data['end_tick']=4000;data['metadata']={'object_ids':['pear']}
        recipes=repair_recipes(data,context,{'error':'FRUIT_NOT_CLEANED'})
        self.assertEqual(recipes[0]['name'],'wash_completion_gate')
        self.assertEqual(recipes[0]['factor'],4.)
        self.assertTrue(recipes[0]['wash_gate']);self.assertEqual(recipes[0]['windows'],[])


if __name__=='__main__':unittest.main()
