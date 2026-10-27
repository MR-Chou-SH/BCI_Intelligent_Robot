"""Minimal image-free policy actions and replayable key states (V3)."""
import hashlib
import json
from pathlib import Path
import uuid
import platform as system_platform

import h5py
import mujoco
import numpy as np

from .object_state import object_states,key_object_states
from .scene_identity import model_identity
from .action_codec import encode
from .gripper import fingertip_gap

SCHEMA="kitchen_state_actions_v3"


def snapshot(scene, controller):
    """Explicit full-state query; used once at recording start, not each frame."""
    spec = mujoco.mjtState.mjSTATE_INTEGRATION
    state = np.empty(mujoco.mj_stateSize(scene.model, spec))
    mujoco.mj_getState(scene.model, scene.data, state, spec)
    return {"sim_tick": int(controller.tick), "sim_time_s": float(scene.data.time),
            "state_spec": int(spec), "integration_state": state.tolist(),
            "qpos": scene.data.qpos.tolist(), "qvel": scene.data.qvel.tolist(),
            "ctrl": scene.data.ctrl.tolist(), "object_states": object_states(scene, controller)}


class CompactWriter:
    """Append compressed 25Hz actions/states, with bounded one-second buffers."""
    def __init__(self,path,metadata,initial,controller):
        self.path=Path(path);self.file=h5py.File(self.path,"x")
        self.file.attrs.update(schema=SCHEMA,metadata_json=json.dumps(metadata,ensure_ascii=False,separators=(",",":")),
            sealed=False,start_tick=int(initial["sim_tick"]),state_spec=int(initial["state_spec"]))
        for name,value in (("integration_state",initial["integration_state"]),("qfrc_bias",controller.scene.data.qfrc_bias)):
            self.file.create_dataset("initial/"+name,data=np.asarray(value,dtype='f8'))
        self.file.attrs['controller_json']=json.dumps({
            'previous':controller.previous.tolist(),'accepted':controller.accepted.tolist(),
            'reference':controller.reference.tolist(),'frame_step':controller.frame_step,
            'tick':controller.tick,'stopped':controller.stopped},separators=(',',':'))
        self.file.attrs['devices_json']=json.dumps(controller.devices.snapshot(),separators=(',',':'))
        self.datasets={};count=len(metadata['object_ids'])
        specs=(("actions/tick",(),"i8"),("actions/joint_pos",(14,),"f8"),
            ("actions/gripper_opening",(2,),"f8"),("actions/interpolation_reset",(),"u1"),
            ("replay/gripper_target",(2,),"f8"),
            ("states/tick",(),"i8"),("states/qpos",(len(initial['qpos']),),"f8"),
            ("states/qvel",(len(initial['qvel']),),"f8"),("states/object_pose",(count,7),"f8"),
            ("states/task_flags",(count,2),"u1"),("states/gripper_width_m",(2,),"f8"))
        for name,width,dtype in specs:
            self.datasets[name]=self.file.create_dataset(name,shape=(0,*width),maxshape=(None,*width),
                dtype=dtype,chunks=(25,*width),compression='gzip',compression_opts=1,shuffle=True,fletcher32=True)
        self.actions=[];self.states=[]

    def _append(self,name,values):
        dataset=self.datasets[name];start=dataset.shape[0]
        dataset.resize(start+len(values),axis=0);dataset[start:]=values

    def flush(self,kind):
        rows=getattr(self,kind)
        if rows:
            names=[n for n in self.datasets if n.startswith(kind+'/')]
            if kind=='actions':names.append('replay/gripper_target')
            for index,name in enumerate(names):self._append(name,[row[index] for row in rows])
            rows.clear()

    def action(self,tick,command,reset):
        joints,opening=encode(command)
        self.actions.append((int(tick),joints,opening,bool(reset),np.asarray(command)[[7,15]].copy()))
        if len(self.actions)>=25:self.flush('actions')

    def state(self,tick,qpos,qvel,objects,width):
        row=(int(tick),np.asarray(qpos).copy(),np.asarray(qvel).copy(),
            [[*o['position_world_m'],*o['quaternion_world_wxyz']] for o in objects],
            [[o.get('cleaned',False),o.get('heated',False)] for o in objects],width)
        if self.states and self.states[-1][0]==tick:self.states[-1]=row
        elif not self.states and len(self.datasets['states/tick']) and self.datasets['states/tick'][-1]==tick:
            for name,value in zip([n for n in self.datasets if n.startswith('states/')],row):self.datasets[name][-1]=value
        else:self.states.append(row)
        if len(self.states)>=25:self.flush('states')

    def seal(self,end_tick,reason,failed=False):
        self.flush('actions');self.flush('states')
        self.file.attrs.update(end_tick=int(end_tick),end_reason=str(reason),sampling_failed=bool(failed),sealed=not failed)
        self.file.close()


class StateRecorder:
    def __init__(self,root,platform):
        for name,expected in platform.control['configuration']['sources'].items():
            if Path(name).name!=name or hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=expected:
                raise ValueError('Control source changed since initialization; restart before recording')
        self.directory=Path(root)/('state_trajectory_'+uuid.uuid4().hex)
        self.directory.mkdir(parents=True,exist_ok=False);self.path=self.directory/'trajectory.h5'
        self.start_tick=self.last_tick=int(platform.controller.tick)
        self.steps=0;self.failed=False;self.sealed=False;self.file_sha256=None
        self.last_accepted=self.last_previous=None
        scene=platform.scene;initial=snapshot(scene,platform.controller)
        metadata={'layout':scene.layout_id,'base_seed':scene.seed,'episode':platform.episode,
            'identity':platform.token(),'model_identity':model_identity(scene,builder_sha256=scene.builder_sha256),
            'control_identity':platform.control,'physics_hz':500,'state_hz':25,'action_hz':25,'images_included':False,
            'object_ids':list(scene.objects),'task_flag_columns':['cleaned','heated'],
            'arm_order':['left','right'],'joint_order':[f'{s}_q{i}' for s in ('left','right') for i in range(1,8)],
            'arm_qpos_indices':[int(i) for s in ('left','right') for i in scene.arms[s].qpos],
            'action_semantics':{'joint_pos':'nominal position targets, radians, left7 then right7',
                'gripper_opening':'control opening ratio, left/right; 0 closed, 1 open; tendon=.05*(1-opening)',
                'replay/gripper_target':'two exact native tendon targets at action ticks; avoids lossy opening round-trip',
                'interpolation_reset':'mid-frame hold/cancel resets the interpolation start to this action'},
            'state_semantics':'full qpos/qvel, queried object poses/flags and measured inner pad gap in metres',
            'software':{'python':system_platform.python_version(),'mujoco':mujoco.__version__,'numpy':np.__version__}}
        self.writer=CompactWriter(self.path,metadata,initial,platform.controller);self.file=self.writer.file
        self.gripper_data=mujoco.MjData(scene.model)
        self._state(platform.controller)

    @property
    def closed(self):return not bool(self.file.id.valid)

    def annotate(self,task,events):
        segments=[];active=None
        for event in events:
            if event['stage']=='skill_started':active={'skill':event['skill'],'start_tick':int(event['tick'])}
            elif event['stage']=='skill_qualified' and active:
                segments.append({**active,'end_tick':int(event['tick'])});active=None
        self.file.attrs['task_json']=json.dumps(task,ensure_ascii=False,separators=(',',':'))
        self.file.attrs['segments_json']=json.dumps(segments,separators=(',',':'))

    def before_step(self,controller):
        changed=self.last_accepted is not None and (not np.array_equal(controller.accepted,self.last_accepted) or
            controller.frame_step!=0 and not np.array_equal(controller.previous,self.last_previous))
        if self.last_accepted is None or controller.frame_step==0 or changed:
            reset=bool(changed and controller.frame_step!=0)
            if reset and not np.array_equal(controller.previous,controller.accepted):
                self.failed=True;raise ValueError('Unsupported mid-frame interpolation change')
            self.writer.action(controller.tick,controller.accepted,reset)
            self.last_accepted=controller.accepted.copy();self.last_previous=controller.previous.copy()

    def _state(self,controller):
        query=key_object_states(controller.scene,controller)
        self.gripper_data.qpos[:]=controller.scene.data.qpos
        mujoco.mj_kinematics(controller.scene.model,self.gripper_data)
        widths=[max(0.,fingertip_gap(controller.scene.model,self.gripper_data,controller.scene.arms[s])['inner_gap_m'])
                for s in ('left','right')]
        self.writer.state(controller.tick,controller.scene.data.qpos,controller.scene.data.qvel,query['objects'],widths)

    def sample(self,controller):
        try:
            if controller.tick!=self.last_tick+1:raise ValueError('State recording physics tick gap')
            self.steps+=1;self.last_tick=int(controller.tick)
            if (self.last_tick-self.start_tick)%20==0:self._state(controller)
        except Exception:self.failed=True;raise

    def close(self,platform,reason):
        if not self.closed:
            try:self._state(platform.controller);self.writer.seal(self.last_tick,reason,self.failed);self.sealed=not self.failed
            except BaseException:
                self.failed=True
                if not self.closed:self.file.close()
                raise
        if self.sealed and self.file_sha256 is None:
            with self.path.open('rb') as stream:self.file_sha256=hashlib.file_digest(stream,'sha256').hexdigest()
        return self.info()

    def info(self):
        return {'schema':SCHEMA,'path':str(self.path),'active':not self.closed,'start_tick':self.start_tick,
            'end_tick':self.last_tick,'steps':self.steps,'sampling_failed':self.failed,'images_included':False,
            'sealed':self.sealed,'sha256':self.file_sha256}
