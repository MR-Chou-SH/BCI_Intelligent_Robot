"""Selection/fallback/resume must never count trial/backup files as samples."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from integration.kitchen.data import optimized_collection as worker
from integration.kitchen.data import collect_dataset as dataset
from integration.kitchen.tasks.catalog import cases


class OptimizedCollectionTests(unittest.TestCase):
    def run_worker(self,root,pass_trial,mode='continuous'):
        case=cases('atomic')[0];identity={'test':True};backup=root/'backup';backup.mkdir(parents=True)
        h5=backup/'trajectory.h5';h5.write_bytes(b'immutable expert')
        ap=backup/'annotations.json';ap.write_text('{}')
        base={'case':case,'seed':0,'status':'QUALIFIED_EXPERT','task_success':True,
            'trajectory':str(h5),'annotations':str(ap),'annotations_sha256':worker.digest(ap),
            'verification':{'sha256':worker.digest(h5)},'simulated_seconds':10.,'wall_seconds':1.,
            'source_identity_sha256':worker.identity_sha256(identity)}
        worker.save(backup/'report.json',base)
        job=(case,0,str(root),'research',60,identity,False,'faster')
        settings={'mode':mode,'factors':[4.] if mode=='adaptive' else [3.,5.]}
        def candidate(root,index,*a):return {'commands':np.zeros((100-index*10,16)),'factor':settings['factors'][index]},None
        def run(data,context,report,plan,directory,record=False,**kw):
            directory.mkdir();result={**base,'factor':plan['factor'],'simulated_seconds':len(plan['commands'])/25,
                'status':'CANDIDATE_TASK_PASS' if pass_trial else 'FAILED_ATTEMPT','task_success':pass_trial}
            if record:
                path=directory/'trajectory.h5';path.write_bytes(b'optimized expert')
                annotation=directory/'annotations.json';annotation.write_text('{}')
                result.update(status='QUALIFIED_EXPERT',trajectory=str(path),annotations=str(annotation),
                    annotations_sha256=worker.digest(annotation),verification={'sha256':worker.digest(path)})
            worker.save(directory/'report.json',result);return result
        def adaptive(backup,directory,**kw):
            if not pass_trial:return None,None,[],[]
            directory.mkdir()
            result=run({}, {},base,{'factor':4.,'commands':np.zeros((50,16))},directory/'recorded',record=True)
            return result,directory/'recorded/report.json',[],[]
        with patch.object(worker,'validate_previous'),patch.object(worker,'source_identity',return_value=identity), \
             patch.object(worker,'load_backup',return_value=({}, {},base)), \
             patch.object(worker,'candidate_plan',side_effect=candidate) as generate, \
             patch.object(worker,'run_candidate',side_effect=run) as execute, \
             patch('integration.kitchen.data.adaptive_optimization.optimize',side_effect=adaptive) as adapt:
            result=worker.optimized_case(job,settings)
            again=worker.optimized_case(job,settings)
            self.assertEqual(result,again);self.assertEqual(generate.call_count,0 if mode=='adaptive' else 2)
            self.assertEqual(execute.call_count,0 if mode=='adaptive' else 3 if pass_trial else 2)
            self.assertEqual(adapt.call_count,1 if mode=='adaptive' else 0)
            self.assertEqual(h5.read_bytes(),b'immutable expert')
            report=worker.read(root/'report.json')
            self.assertEqual(report['postprocessing']['status'],'OPTIMIZED' if pass_trial else 'ORIGINAL_FALLBACK')
            self.assertEqual(Path(report['trajectory']),root/'trajectory.h5')
            self.assertEqual((root/'trajectory.h5').read_bytes(),b'optimized expert' if pass_trial else b'immutable expert')
            worker.validate_backup_receipt(report,root)
            h5.write_bytes(b'tampered backup')
            with self.assertRaisesRegex(ValueError,'Backup data hash'):worker.optimized_case(job,settings)

    def test_fastest_qualified_result_selected_and_resume_is_read_only(self):
        with tempfile.TemporaryDirectory() as d:self.run_worker(Path(d)/'attempt',True)

    def test_all_failed_trials_fall_back_to_qualified_expert(self):
        with tempfile.TemporaryDirectory() as d:self.run_worker(Path(d)/'attempt',False)

    def test_adaptive_qualification_resume_and_backup_fallback(self):
        for succeeds in (True,False):
            with tempfile.TemporaryDirectory() as d:self.run_worker(Path(d)/'attempt',succeeds,mode='adaptive')

    def test_source_collision_backup_is_retained_but_not_admitted_as_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)/'attempt';backup=root/'backup';backup.mkdir(parents=True)
            case=cases('atomic')[0];identity={'test':True}
            source=backup/'trajectory.h5';source.write_bytes(b'historical task-success trajectory')
            base={'case':case,'seed':0,'status':'QUALIFIED_EXPERT','task_success':True,
                'verification':{'sha256':worker.digest(source)},'source_identity_sha256':worker.identity_sha256(identity)}
            worker.save(backup/'report.json',base)
            with patch.object(worker,'validate_previous'),patch.object(worker,'source_identity',return_value=identity), \
                 patch.object(worker,'load_backup',return_value=({}, {},base)), \
                 patch('integration.kitchen.data.adaptive_optimization.optimize',return_value=(None,None,[],[
                     {'reason':'SOURCE_PATH_COLLISION_AT_1X'}])):
                r=worker.optimized_case((case,0,str(root),'research',60,identity,False),{'mode':'adaptive','factors':[4.]})
            self.assertEqual(r['status'],'FAILED_ATTEMPT');self.assertFalse(r['task_success'])
            self.assertEqual(source.read_bytes(),b'historical task-success trajectory')
            self.assertFalse((root/'trajectory.h5').exists())

    def test_interrupted_optimization_reuses_same_seed_when_backup_exists(self):
        case=cases('atomic')[0]
        plan={'cases':[case],'samples_per_case':1,'base_seed':0,'max_attempts':2,'identity':{},
              'postprocessing':{'mode':'continuous','factors':[3.,5.]}}
        with tempfile.TemporaryDirectory() as d:
            output=Path(d);directory=dataset.slot_path(output,case,0)/'attempt_01_seed0'
            (directory/'backup').mkdir(parents=True)
            worker.save(directory/'backup/report.json',{'status':'QUALIFIED_EXPERT'})
            worker.save(directory.with_suffix('.job.json'),{'case_id':case['case_id'],'sample':0,'seed':0})
            qualified,pending,missing=dataset.scan(output,plan)
            self.assertFalse(qualified or missing);self.assertEqual(pending[0][2],0)
            self.assertEqual(pending[0][3],directory)

    def deferred_worker(self,root,optimized,replay_status='CONTROL_RESIMULATION_PASS',defer=True):
        case=cases('atomic')[0];identity={'test':True};backup=root/'backup'
        settings={'mode':'adaptive','factors':[4.],**({'defer_backup_replay':True} if defer else {})}
        def native(job,capture_context=False,replay=True):
            self.assertTrue(capture_context);self.assertEqual(replay,not defer)
            backup.mkdir(parents=True);h5=backup/'trajectory.h5';h5.write_bytes(b'native expert')
            ap=backup/'annotations.json';ap.write_text('{}')
            worker.save(backup/'report.json',{'case':case,'seed':0,'status':'EXECUTED_REPLAY_PENDING','task_success':True,
                'trajectory':str(h5),'annotations':str(ap),'annotations_sha256':worker.digest(ap),
                'verification':{'status':'DEFERRED','sha256':worker.digest(h5),'task_success':None},
                'simulated_seconds':10.,'source_identity_sha256':worker.identity_sha256(identity)})
        def adaptive(backup_dir,directory,**kw):
            if not optimized:return None,None,[],[]
            directory.mkdir();path=directory/'trajectory.h5';path.write_bytes(b'optimized expert')
            annotation=directory/'annotations.json';annotation.write_text('{}')
            result={'case':case,'seed':0,'status':'QUALIFIED_EXPERT','task_success':True,'simulated_seconds':3.,
                'trajectory':str(path),'annotations':str(annotation),'annotations_sha256':worker.digest(annotation),
                'verification':{'sha256':worker.digest(path)},'source_identity_sha256':worker.identity_sha256(identity)}
            worker.save(directory/'qualified.json',result);return result,directory/'qualified.json',[],[]
        def replay(path):
            return {'status':replay_status,'sha256':worker.digest(path),'task_success':True,
                    'maximum_qpos_error':0.,'maximum_qvel_error':0.,'duration_s':10.,'task_errors':[]}
        job=(case,0,str(root),'research',60,identity,False,'faster')
        patches=[patch.object(worker,'validate_previous'),patch.object(worker,'source_identity',return_value=identity),
                 patch.object(worker,'one_case',side_effect=native),
                 patch.object(worker,'load_backup',return_value=({}, {},{})),
                 patch('integration.kitchen.data.adaptive_optimization.optimize',side_effect=adaptive),
                 patch('integration.kitchen.data.replay.verify',side_effect=replay)]
        for item in patches:item.start()
        self.addCleanup(lambda:[item.stop() for item in patches])
        from integration.kitchen.data import replay as replay_module
        return job,settings,replay_module.verify

    def test_deferred_backup_replay_is_skipped_when_optimized(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'attempt';job,settings,verify=self.deferred_worker(root,True)
            result=worker.optimized_case(job,settings);self.assertEqual(result,worker.optimized_case(job,settings))
            report=worker.read(root/'report.json');verify.assert_not_called()
            self.assertEqual(report['postprocessing']['backup_replay'],'NOT_REQUIRED')
            self.assertEqual((root/'trajectory.h5').read_bytes(),b'optimized expert')
            self.assertEqual(worker.read(root/'backup/report.json')['status'],'EXECUTED_REPLAY_PENDING')
            worker.validate_backup_receipt(report,root)
            del report['postprocessing']['settings']['defer_backup_replay']
            with self.assertRaisesRegex(ValueError,'Unqualified backup'):worker.validate_backup_receipt(report,root)

    def test_deferred_backup_is_replayed_before_fallback_admission(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'attempt';job,settings,verify=self.deferred_worker(root,False)
            result=worker.optimized_case(job,settings);self.assertEqual(result,worker.optimized_case(job,settings))
            report=worker.read(root/'report.json');self.assertEqual(verify.call_count,1)
            self.assertEqual(report['status'],'QUALIFIED_EXPERT')
            self.assertEqual(report['verification']['status'],'CONTROL_RESIMULATION_PASS')
            self.assertEqual(report['postprocessing']['backup_replay'],'PASS')
            self.assertEqual((root/'trajectory.h5').read_bytes(),b'native expert')
            (root/'backup_replay.json').unlink()
            with self.assertRaisesRegex(ValueError,'Deferred backup replay'):worker.validate_backup_receipt(report,root)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'attempt';job,settings,_=self.deferred_worker(root,False,replay_status='CONTROL_RESIMULATION_DIVERGED')
            r=worker.optimized_case(job,settings)
            self.assertEqual(r['status'],'FAILED_ATTEMPT');self.assertEqual(r['error'],'BACKUP_REPLAY_FAILED')
            self.assertFalse((root/'trajectory.h5').exists())

    def test_pending_backup_requires_explicit_deferral(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'attempt';job,_,verify=self.deferred_worker(root,True,defer=True)
            worker.one_case(job,capture_context=True,replay=False)  # Seal a pending backup, then run without the batch setting.
            r=worker.optimized_case(job,{'mode':'adaptive','factors':[4.]})
            self.assertEqual(worker.read(root/'report.json')['postprocessing']['status'],'BACKUP_FAILED')
            self.assertNotEqual(r['status'],'QUALIFIED_EXPERT');verify.assert_not_called()

    def test_interrupted_deferred_backup_reuses_seed_only_when_deferral_enabled(self):
        case=cases('atomic')[0]
        for defer,resumed in ((True,True),(False,False)):
            plan={'cases':[case],'samples_per_case':1,'base_seed':0,'max_attempts':2,'identity':{},
                  'postprocessing':{'mode':'adaptive','factors':[4.],**({'defer_backup_replay':True} if defer else {})}}
            with tempfile.TemporaryDirectory() as d:
                output=Path(d);directory=dataset.slot_path(output,case,0)/'attempt_01_seed0'
                (directory/'backup').mkdir(parents=True)
                worker.save(directory/'backup/report.json',{'status':'EXECUTED_REPLAY_PENDING'})
                worker.save(directory.with_suffix('.job.json'),{'case_id':case['case_id'],'sample':0,'seed':0})
                qualified,pending,missing=dataset.scan(output,plan)
                self.assertEqual(pending[0][3]==directory,resumed)

    def direct_worker(self,root,direct_ok):
        case=cases('atomic')[0];identity={'test':True}
        settings={'mode':'adaptive','factors':[4.],'direct_fast':True}
        calls=[]
        def native(job,capture_context=False,replay=True,direct_fast=False):
            directory=Path(job[2]);directory.mkdir(parents=True);calls.append((directory.name,direct_fast))
            h5=directory/'trajectory.h5';h5.write_bytes(b'fast expert' if direct_fast else b'native expert')
            ap=directory/'annotations.json';ap.write_text('{}')
            ok=direct_ok or not direct_fast
            worker.save(directory/'report.json',{'case':case,'seed':0,'status':'QUALIFIED_EXPERT' if ok else 'FAILED_ATTEMPT',
                'task_success':ok,'trajectory':str(h5),'annotations':str(ap),'annotations_sha256':worker.digest(ap),
                'verification':{'status':'CONTROL_RESIMULATION_PASS','sha256':worker.digest(h5),'task_success':ok},
                'simulated_seconds':5. if direct_fast else 20.,'source_identity_sha256':worker.identity_sha256(identity),
                **({} if ok else {'error':'ExpertFailure(test)'})})
            if not ok:h5.unlink();ap.unlink()
        def adaptive(backup_dir,directory,**kw):return None,None,[],[]
        job=(case,0,str(root),'research',60,identity,False,'faster')
        patches=[patch.object(worker,'validate_previous'),patch.object(worker,'source_identity',return_value=identity),
                 patch.object(worker,'one_case',side_effect=native),patch.object(worker,'load_backup',return_value=({}, {},{})),
                 patch('integration.kitchen.data.adaptive_optimization.optimize',side_effect=adaptive)]
        for item in patches:item.start()
        self.addCleanup(lambda:[item.stop() for item in patches])
        return job,settings,calls

    def test_direct_fast_success_skips_backup_and_resumes_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'attempt';job,settings,calls=self.direct_worker(root,True)
            result=worker.optimized_case(job,settings);self.assertEqual(result,worker.optimized_case(job,settings))
            self.assertEqual(calls,[('direct',True)]);self.assertFalse((root/'backup').exists())
            report=worker.read(root/'report.json')
            self.assertEqual(report['postprocessing']['status'],'DIRECT_FAST')
            self.assertEqual((root/'trajectory.h5').read_bytes(),b'fast expert')
            worker.validate_backup_receipt(report,root)
            (root/'direct/trajectory.h5').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'Direct-fast source'):worker.validate_backup_receipt(report,root)

    def test_failed_direct_fast_keeps_its_report_and_uses_the_ordinary_path(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'attempt';job,settings,calls=self.direct_worker(root,False)
            worker.optimized_case(job,settings)
            self.assertEqual(calls,[('direct',True),('backup',False)])
            report=worker.read(root/'report.json')
            self.assertEqual(report['postprocessing']['status'],'ORIGINAL_FALLBACK')
            self.assertEqual(worker.read(root/'direct/report.json')['status'],'FAILED_ATTEMPT')
            self.assertEqual((root/'trajectory.h5').read_bytes(),b'native expert')

    def test_interrupted_direct_fast_resumes_same_seed(self):
        case=cases('atomic')[0]
        plan={'cases':[case],'samples_per_case':1,'base_seed':0,'max_attempts':2,'identity':{},
              'postprocessing':{'mode':'adaptive','factors':[4.],'direct_fast':True}}
        with tempfile.TemporaryDirectory() as d:
            output=Path(d);directory=dataset.slot_path(output,case,0)/'attempt_01_seed0'
            (directory/'direct').mkdir(parents=True)
            worker.save(directory.with_suffix('.job.json'),{'case_id':case['case_id'],'sample':0,'seed':0})
            qualified,pending,missing=dataset.scan(output,plan)
            self.assertEqual(pending[0][3],directory)


if __name__=='__main__':unittest.main()
