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
                 patch.object(worker,'trial',side_effect=trial) as run:
                selected,path,rows,errors=worker.optimize(backup,root/'opt')
                self.assertIsNone(selected);self.assertIsNone(path);self.assertEqual(len(rows),2)
                self.assertEqual([x.args[2] for x in run.call_args_list],[4.,1.])
                self.assertEqual(errors[0]['reason'],'SOURCE_PATH_COLLISION_AT_1X')
                worker.optimize(backup,root/'opt');self.assertEqual(run.call_count,2)
                self.assertEqual(source.read_bytes(),b'original')
                self.assertEqual(read(root/'opt/decision.json')['unresolved_reason'],'SOURCE_PATH_COLLISION_AT_1X')
