import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from integration.kitchen.data.collect_experts import validate_previous


class ExpertResumeTests(unittest.TestCase):
    def test_batch_identity_detects_model_geometry_changes(self):
        from unittest.mock import patch
        from integration.kitchen.data.collect_experts import source_identity,ROOT
        before=source_identity();model=ROOT/'robot_arm/kitchen/model.py'
        original=Path.read_bytes
        def changed(path):
            content=original(path)
            return content+b'\n# geometry changed\n' if path==model else content
        with patch.object(Path,'read_bytes',changed):after=source_identity()
        self.assertNotEqual(before['model_sources'],after['model_sources'])
        self.assertEqual(before['expert_sources'],after['expert_sources'])
        self.assertEqual(before['control'],after['control'])

    def test_failed_attempt_keeps_only_small_report_without_rendering(self):
        from unittest.mock import patch
        from integration.kitchen.data.collect_experts import one_case,source_identity
        from integration.kitchen.tasks.catalog import cases
        case=next(c for c in cases() if c["case_id"]=="kitchen_expert_v3:L1:apple")
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/"failed"
            # /tmp is a small tmpfs in this environment; the 10GiB data reserve
            # is a collection-run concern, not what this failure-path test checks.
            free=type("Usage",(),{"free":20*1024**3})()
            with patch("integration.kitchen.data.collect_experts.shutil.disk_usage",return_value=free), \
                 patch("integration.kitchen.planning.expert.KitchenExpert.run_case",side_effect=RuntimeError("test planner failure")):
                result=one_case((case,0,str(directory),"fixed",30,source_identity(),False))
            self.assertEqual(result["status"],"FAILED_ATTEMPT")
            self.assertFalse((directory/"data").exists())
            self.assertTrue((directory/"report.json").exists())
            report=json.loads((directory/"report.json").read_text())
            self.assertIn("test planner failure",report["error"])
            self.assertFalse(report["data_retained"])
            self.assertFalse(list(directory.rglob("*.png")))
            self.assertNotIn("events",report)
            self.assertNotIn("quality",report)
            self.assertFalse(report["images_included"])

    def test_resume_checks_report_and_initial_state_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            initial=root/"initial.json"
            initial.write_text('{"initial":true}')
            report=root/"report.json"
            case={"case_id":"kitchen_expert_v3:L1:apple"}
            content={"case":case,"seed":0,"status":"FAILED_ATTEMPT",
                     "initial_sha256":hashlib.sha256(initial.read_bytes()).hexdigest()}
            report.write_text(json.dumps(content))
            row={"case_id":case["case_id"],"seed":0,"status":"FAILED_ATTEMPT",
                 "report":str(report),"report_sha256":hashlib.sha256(report.read_bytes()).hexdigest()}
            validate_previous([row],root,[case],[0])
            with self.assertRaisesRegex(ValueError,"Duplicate or foreign"):
                validate_previous([row,row],root,[case],[0])
            with self.assertRaisesRegex(ValueError,"Duplicate or foreign"):
                validate_previous([row],root,[case],[1])
            initial.write_text('{"initial":false}')
            with self.assertRaisesRegex(ValueError,"initial state hash"):
                validate_previous([row],root,[case],[0])
            report.write_text('{}')
            with self.assertRaisesRegex(ValueError,"report hash"):
                validate_previous([row],root,[case],[0])


if __name__=="__main__":unittest.main()
