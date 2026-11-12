"""Do not disguise source-path collisions as speed problems or successes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from integration.kitchen.data import adaptive_optimization as worker
from integration.kitchen.data.files import replace_json,digest,read


class AdaptiveTests(unittest.TestCase):
    def test_source_collision_audit_stops_timing_retries_and_resumes_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);backup=root/'backup';backup.mkdir();source=backup/'trajectory.h5';source.write_bytes(b'original')
            base={'verification':{'sha256':digest(source)},'case':{'case_id':'test'},'seed':0}
            error='RELAXED_AUDIT: {"geom1":"left_link","geom2":"right_link","normal_force_n":80}'
            def trial(backup,path,factor,*args,**kwargs):
                path.mkdir();result={'status':'FAILED_ATTEMPT','task_success':False,'actual_seconds':1.,'error':error}
                replace_json(path/'report.json',result);return result
            with patch.object(worker,'load_backup',return_value=({}, {'changes':[]},base)), \
                 patch.object(worker,'source_identity',return_value={'test':True}), \
                 patch.object(worker,'source_washes',return_value=False), \
                 patch.object(worker,'trial',side_effect=trial) as run:
                selected,path,rows,errors=worker.optimize(backup,root/'opt')
                self.assertIsNone(selected);self.assertIsNone(path);self.assertEqual(len(rows),2)
                self.assertEqual([x.args[2] for x in run.call_args_list],[4.,1.])
                self.assertEqual(errors[0]['reason'],'SOURCE_PATH_COLLISION_AT_1X')
                worker.optimize(backup,root/'opt');self.assertEqual(run.call_count,2)
                self.assertEqual(source.read_bytes(),b'original')
                self.assertEqual(read(root/'opt/decision.json')['unresolved_reason'],'SOURCE_PATH_COLLISION_AT_1X')

    def test_compose_keeps_earlier_repairs_and_takes_slower_caps(self):
        parent={'name':'wash','factor':4.,'caps':{'microwave_start_geom':2.},
                'windows':[{'start_tick':0,'end_tick':20,'factor':3}],'wash_gate':True}
        child={'name':'button','factor':4.,'caps':{'microwave_start_geom':1.,'faucet_handle_geom':2.},
               'windows':[{'start_tick':0,'end_tick':20,'factor':3},{'start_tick':40,'end_tick':60,'factor':2}]}
        recipe=worker.compose(parent,child)
        self.assertEqual(recipe['name'],'wash+button')
        self.assertEqual(recipe['caps'],{'microwave_start_geom':1.,'faucet_handle_geom':2.})
        self.assertEqual(len(recipe['windows']),2);self.assertTrue(recipe['wash_gate'])
        self.assertTrue(worker.same_timing(worker.compose(parent,{**parent,'name':'again'}),parent))

    def run_chain(self,root,failures):
        """failures maps the set of applied repairs to (error, failure tick) or None for success."""
        backup=root/'backup';backup.mkdir();source=backup/'trajectory.h5';source.write_bytes(b'original')
        base={'verification':{'sha256':digest(source)},'case':{'case_id':'test'},'seed':0,
              'initialization_profile':'research'}
        def repairs(data,context,failed):
            name=failed['error'].split(':')[0]
            return [{'name':'fix_'+name,'factor':4.,'caps':{name:1.},'windows':[]}]
        def trial(backup,path,factor,*args,record=False,verify_recording=True,segment_factors=None,**kwargs):
            path.mkdir();outcome=failures.get(frozenset(segment_factors or {}))
            result={'status':'FAILED_ATTEMPT','task_success':False,'actual_seconds':1.}
            if outcome is None:
                (path/'trajectory.h5').write_bytes(b'recorded')
                result.update(status='CANDIDATE_TASK_PASS',task_success=True,actual_seconds=2.,trajectory=str(path/'trajectory.h5'))
            else:result.update(error=outcome[0],failure_source_tick=outcome[1])
            replace_json(path/'report.json',result);return result
        patches=[patch.object(worker,'load_backup',return_value=({}, {'changes':[]},base)),
                 patch.object(worker,'source_identity',return_value={'test':True}),
                 patch.object(worker,'trial',side_effect=trial),
                 patch.object(worker,'base_caps',return_value={}),
                 patch.object(worker,'source_washes',return_value=False),
                 patch.object(worker,'repair_recipes',side_effect=repairs),
                 patch.object(worker,'schedule',side_effect=lambda d,c,f,segment_factors=None,windows=None:([0]*(10+len(segment_factors)),[],{})),
                 patch('integration.kitchen.data.replay.verify',return_value={'status':'CONTROL_RESIMULATION_PASS','task_success':True,'sha256':'x'}),
                 patch('integration.kitchen.data.annotations.build_annotations',return_value={'schema':'test'})]
        for item in patches:item.start()
        self.addCleanup(lambda:[item.stop() for item in patches])
        return backup,source

    def test_later_failure_composes_repairs_and_replays_recorded_trial_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            backup,source=self.run_chain(root,{frozenset():('WASH: x',10),frozenset({'WASH'}):('HEAT: y',20)})
            from integration.kitchen.data import replay
            selected,path,rows,errors=worker.optimize(backup,root/'opt');run=worker.trial
            self.assertEqual(selected['adaptive_recipe']['name'],'fix_WASH+fix_HEAT')
            self.assertEqual(selected['adaptive_recipe']['caps'],{'WASH':1.,'HEAT':1.})
            self.assertEqual(path,root/'opt/fix_WASH+fix_HEAT/qualified.json')
            self.assertEqual(selected['verification']['status'],'CONTROL_RESIMULATION_PASS')
            self.assertEqual(len(rows),3);self.assertEqual(replay.verify.call_count,1)
            self.assertTrue(all(c.kwargs['record'] and not c.kwargs['verify_recording'] for c in run.call_args_list))
            with patch('integration.kitchen.data.optimized_collection.check_report',return_value=selected) as check:
                worker.optimize(backup,root/'opt')
            check.assert_called_once();self.assertEqual(replay.verify.call_count,1)
            self.assertEqual(source.read_bytes(),b'original')

    def test_repairs_compose_only_after_progress_and_within_depth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            # The first repair fails earlier than the original failure: no refinement.
            backup,_=self.run_chain(root,{frozenset():('WASH: x',10),frozenset({'WASH'}):('HEAT: y',5),
                                          frozenset({'WASH','HEAT'}):None})
            selected,_,rows,_=worker.optimize(backup,root/'no_progress')
            self.assertEqual([r['recipe'] for r in rows][:2],['global4_contact_caps','fix_WASH'])
            self.assertNotIn('fix_WASH+fix_HEAT',[r['recipe'] for r in rows])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);chain={frozenset():('A0: x',1)};applied=set()
            for index in range(6):
                applied.add(f'A{index}');chain[frozenset(applied)]=(f'A{index+1}: x',index+2)
            backup,_=self.run_chain(root,chain)
            selected,_,rows,_=worker.optimize(backup,root/'deep')
            names=[r['recipe'] for r in rows if r['recipe'].startswith('fix_')]
            self.assertEqual(max(n.count('+') for n in names)+1,worker.MAX_REPAIR_DEPTH)

    def test_wash_gate_starts_with_first_candidate_and_duplicate_repairs_are_skipped(self):
        import numpy as np
        from integration.kitchen.planning.local_timing import source_washes
        flags=np.zeros((5,2,2),dtype=np.uint8);self.assertFalse(source_washes({'states/task_flags':flags}))
        flags[3:,1,0]=1;self.assertTrue(source_washes({'states/task_flags':flags}))
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            backup,_=self.run_chain(root,{frozenset():('HEAT: x',10),frozenset({'DOOR'}):('HEAT: y',5)})
            def repairs(data,context,failed):
                return [{'name':'same_as_start','factor':4.,'caps':{},'windows':[]},
                        {'name':'fix_DOOR','factor':4.,'caps':{'DOOR':1.},'windows':[]}]
            with patch.object(worker,'source_washes',return_value=True), \
                 patch.object(worker,'repair_recipes',side_effect=repairs):
                selected,_,rows,_=worker.optimize(backup,root/'opt')
            self.assertIsNone(selected)
            names=[r['recipe'] for r in rows]
            self.assertEqual(names,['global4_contact_caps','fix_DOOR','global3_fallback','global2_fallback'])
            self.assertTrue(all(c.kwargs['wash_gate'] for c in worker.trial.call_args_list))

    def test_first_level_repairs_run_before_composed_refinements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            # fix_A moves the failure later (would be refined); fix_B is a sibling first-level repair.
            backup,_=self.run_chain(root,{frozenset():('START: x',10),frozenset({'A'}):('LATER: y',20),
                                          frozenset({'B'}):('START: z',10),frozenset({'A','LATER'}):('LATER: w',20)})
            def repairs(data,context,failed):
                name=failed['error'].split(':')[0]
                if name=='START':
                    return [{'name':'fix_A','factor':4.,'caps':{'A':1.},'windows':[]},
                            {'name':'fix_B','factor':4.,'caps':{'B':1.},'windows':[]}]
                return [{'name':'fix_'+name,'factor':4.,'caps':{name:1.},'windows':[]}]
            with patch.object(worker,'repair_recipes',side_effect=repairs):
                _,_,rows,_=worker.optimize(backup,root/'opt')
            names=[r['recipe'] for r in rows]
            self.assertLess(names.index('fix_B'),names.index('fix_A+fix_LATER'))
