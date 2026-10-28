"""Test policy layout, constant-clock alignment and real-writer protocol."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from integration.kitchen.data import lerobot_export as converter
from integration.kitchen.data.files import digest


class FakeWriter:
    instance=None
    @classmethod
    def create(cls,**kwargs):
        cls.instance=cls();cls.instance.config=kwargs;kwargs['root'].mkdir()
        cls.instance.frames=[];cls.instance.episodes=[];cls.instance.finalized=False
        return cls.instance
    def add_frame(self,frame):self.frames.append(copy.deepcopy(frame))
    def save_episode(self):self.episodes.append(self.frames);self.frames=[]
    def finalize(self):self.finalized=True


class LeRobotExportTests(unittest.TestCase):
    def episode(self):
        return {'state':np.arange(64,dtype=np.float32).reshape(4,16),
            'action':np.arange(64,dtype=np.float32).reshape(4,16)/100,
            'ticks':np.array([100,120,140,160]),'source':'raw.h5','source_sha256':'group',
            'annotation':{'coarse_instruction':'Wash fruit. Then heat food.','case_id':'case','targets':{'fruit':'apple'}},
            'segments':[{'instruction':'Wash fruit.','export_start':0,'export_end':2},
                        {'instruction':'Heat food.','export_start':2,'export_end':4}]}

    def test_joint_gripper_order_roundtrips(self):
        joints=np.arange(28).reshape(2,14);grippers=np.array([[.2,.8],[1.,0.]])
        action=converter.pack16(joints,grippers)
        np.testing.assert_array_equal(action[:,:7],joints[:,:7])
        np.testing.assert_array_equal(action[:,8:15],joints[:,7:])
        q,g=converter.unpack16(action)
        np.testing.assert_array_equal(q,joints);np.testing.assert_allclose(g,grippers)
        self.assertEqual(action.dtype,np.float32)

    def test_fine_export_separates_chunks_at_language_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(converter,'policy_episode',return_value=self.episode()):
            result=converter.export([Path('raw.h5')],Path(temporary)/'dataset','fine',writer_class=FakeWriter)
        writer=FakeWriter.instance
        self.assertEqual([len(e) for e in writer.episodes],[2,2])
        self.assertEqual([e[0]['task'] for e in writer.episodes],['Wash fruit.','Heat food.'])
        self.assertTrue(writer.finalized)
        self.assertEqual(writer.config['fps'],25)
        self.assertEqual(set(writer.config['features']),{'action','observation.state'})
        self.assertEqual([r['source_group'] for r in result['exported_episodes']],['group','group'])
        self.assertFalse(result['vision_ready'])
        self.assertEqual(result['source_episodes'][0]['coarse_instruction'],'Wash fruit. Then heat food.')

    def test_coarse_export_preserves_whole_episode(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(converter,'policy_episode',return_value=self.episode()):
            converter.export([Path('raw.h5')],Path(temporary)/'dataset','coarse',writer_class=FakeWriter)
        self.assertEqual(len(FakeWriter.instance.episodes),1)
        self.assertEqual(len(FakeWriter.instance.episodes[0]),4)
        self.assertTrue(all(f['task']=='Wash fruit. Then heat food.' for f in FakeWriter.instance.episodes[0]))

    def test_policy_state_uses_measured_preaction_robot_only_and_handles_midframe_actions(self):
        data={'metadata':{'action_hz':25,'physics_hz':500,'arm_qpos_indices':list(range(14))},
            'start_tick':100,'end_tick':160,'actions/tick':np.array([100,110,120,140]),
            'states/tick':np.array([100,120,140,160]),
            'states/qpos':np.arange(400).reshape(4,100),
            'states/gripper_width_m':np.array([[.01,.02],[.03,.04],[.05,.06],[.07,.08]]),
            'actions/joint_pos':np.arange(56).reshape(4,14),
            'actions/gripper_opening':np.array([[.1,.2],[.3,.4],[.5,.6],[.7,.8]])}
        annotation={'trajectory_sha256':'hash','fine_segments':[
            {'start_step':0,'end_step':2,'instruction':'first'},
            {'start_step':2,'end_step':4,'instruction':'second'}]}
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'trajectory.h5';path.write_bytes(b'raw')
            path.with_name('report.json').write_text(json.dumps({'status':'QUALIFIED_EXPERT','task_success':True,
                'verification':{'sha256':digest(path)},'annotations_sha256':'annotation-hash'}))
            path.with_name('annotations.json').write_text(json.dumps(annotation))
            with patch.object(converter,'read_compact',return_value=data),patch.object(converter,'validate_sidecar'):
                episode=converter.policy_episode(path)
            self.assertEqual(episode['state'].shape,(3,16))
            np.testing.assert_array_equal(episode['state'][0,:7],data['states/qpos'][0,:7])
            np.testing.assert_allclose(episode['state'][:,7],[.01,.03,.05])
            np.testing.assert_array_equal(episode['action'][1,:7],data['actions/joint_pos'][2,:7])
            self.assertEqual([(s['export_start'],s['export_end']) for s in episode['segments']],[(0,1),(1,3)])
            self.assertEqual(path.read_bytes(),b'raw')

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(FileExistsError):converter.export([],Path(temporary),'coarse',writer_class=FakeWriter)
