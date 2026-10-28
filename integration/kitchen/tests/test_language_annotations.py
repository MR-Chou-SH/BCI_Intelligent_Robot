"""Training labels must use action rows and preserve the real semantic chain."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from integration.kitchen.tasks.catalog import cases,chain,ATOMIC,HEAT
from integration.kitchen.tasks.language import coarse_instruction,instruction
from integration.kitchen.data.annotations import build_annotations,validate_sidecar,digest


class LanguageAnnotationsTests(unittest.TestCase):
    def fixture(self):
        case=next(c for c in cases() if c['case_id']=='kitchen_expert_v3:D09:banana:hotdog')
        return {'schema':'kitchen_state_actions_v3','task':case,'start_tick':100,'end_tick':1120,
            'segments':[{'skill':s,'start_tick':a,'end_tick':b} for s,a,b in [
                ('L2',100,300),('R1',340,500),('R4',500,620),('R2',620,800),('B1',820,1100)]],
            'language_stages':[{'skill':s,'start_tick':a} for s,a in [('L2',100),(HEAT,320),('B1',820)]],
            'actions/tick':np.arange(100,1120,20)}

    def build(self,data):
        with patch('integration.kitchen.data.annotations.read_compact',return_value=data),\
             patch('integration.kitchen.data.annotations.digest',return_value='trajectory-hash'):
            return build_annotations('trajectory.h5')

    def test_all_100_cases_have_resolved_english_and_shared_atomic_prompts(self):
        for case in cases():
            coarse=coarse_instruction(case)
            self.assertNotIn('{',coarse)
            self.assertTrue(coarse.endswith('.'))
            if case['task'] in ATOMIC:self.assertEqual(coarse,instruction(case['task'],case))
            for skill in chain(case):
                text=instruction(skill,case)
                if skill in ATOMIC:
                    atomic=next(a for a in cases('atomic') if a['task']==skill and
                        (not a['fruit'] or a['fruit']==case['fruit']) and
                        (not a['meal'] or a['meal']==case['meal']))
                    self.assertEqual(text,instruction(skill,atomic))

    def test_heating_is_one_fine_stage_with_full_coverage(self):
        result=self.build(self.fixture())
        self.assertEqual(result['targets'],{'fruit':'banana','food':'hotdog'})
        self.assertEqual(result['num_steps'],51)
        self.assertEqual([(s['skill'],s['start_step'],s['end_step']) for s in result['fine_segments']],
                         [('L2',0,11),(HEAT,11,36),('B1',36,51)])
        self.assertEqual(result['fine_segments'][1]['instruction'],'Heat the hot dog and place it on the serving tray.')

    def test_midframe_action_is_a_real_training_row_not_a_physics_tick(self):
        data=self.fixture();data['actions/tick']=np.insert(data['actions/tick'],1,101)
        result=self.build(data)
        self.assertEqual(result['num_steps'],52)
        self.assertEqual([(s['start_step'],s['end_step']) for s in result['fine_segments']],[(0,12),(12,37),(37,52)])

    def test_historical_export_reconstructs_groups_without_touching_data(self):
        data=self.fixture();data.pop('language_stages');before=copy.deepcopy(data)
        result=self.build(data)
        self.assertEqual([(s['start_step'],s['end_step']) for s in result['fine_segments']],[(0,12),(12,36),(36,51)])
        self.assertEqual(data['segments'],before['segments'])
        np.testing.assert_array_equal(data['actions/tick'],before['actions/tick'])

    def test_late_overlapping_or_foreign_stages_are_rejected(self):
        for changes in [lambda d:d['language_stages'][1].update(start_tick=500),
                        lambda d:d['language_stages'][1].update(start_tick=100),
                        lambda d:d['language_stages'][1].update(skill='R4'),
                        lambda d:d['language_stages'][1].update(start_tick=321)]:
            data=self.fixture();changes(data)
            with self.assertRaises(ValueError):self.build(data)

    def test_hash_and_label_tampering_are_both_rejected(self):
        annotation=self.build(self.fixture())
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'annotations.json';path.write_text(json.dumps(annotation));expected=digest(path)
            with patch('integration.kitchen.data.annotations.build_annotations',return_value=annotation):
                validate_sidecar('trajectory.h5',path,expected)
                with self.assertRaisesRegex(ValueError,'hash mismatch'):validate_sidecar('trajectory.h5',path,'bad')
                changed=copy.deepcopy(annotation);changed['fine_segments'][0]['end_step']=12
                path.write_text(json.dumps(changed))
                with self.assertRaisesRegex(ValueError,'does not match'):validate_sidecar('trajectory.h5',path,digest(path))
