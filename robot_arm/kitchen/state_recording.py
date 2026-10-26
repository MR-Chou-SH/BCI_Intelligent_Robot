"""Compact state/action recording. No images, RPC dumps or contact diagnostics."""
import hashlib
import json
from pathlib import Path
import uuid
import platform as system_platform

import h5py
import mujoco
import numpy as np

from .object_state import object_states, key_object_states
from .scene_identity import model_identity

SCHEMA = "kitchen_state_actions_v2"


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
    """One compressed numeric file; buffers at most one second of samples."""
    def __init__(self, path, metadata, initial):
        self.path=Path(path)
        self.file=h5py.File(self.path,"x")
        self.file.attrs["schema"]=SCHEMA
        self.file.attrs["metadata_json"]=json.dumps(metadata,ensure_ascii=False,allow_nan=False,separators=(",",":"))
        self.file.attrs["sealed"]=False
        self.file.attrs["start_tick"]=int(initial["sim_tick"])
        self.file.attrs["state_spec"]=int(initial["state_spec"])
        self.file.create_dataset("initial/integration_state",data=np.asarray(initial["integration_state"],dtype="f8"))
        self.file.create_dataset("initial/ctrl",data=np.asarray(initial["ctrl"],dtype="f8"))
        count=len(metadata["object_ids"])
        self.datasets={}
        for name,width,dtype,chunk in (("actions/ctrl",(16,),"f8",500),("states/tick",(),"i8",25),
                ("states/qpos",(len(initial["qpos"]),),"f8",25),("states/qvel",(len(initial["qvel"]),),"f8",25),
                ("states/object_pose",(count,7),"f8",25),("states/task_flags",(count,2),"u1",25)):
            self.datasets[name]=self.file.create_dataset(name,shape=(0,*width),maxshape=(None,*width),
                dtype=dtype,chunks=(chunk,*width),compression="gzip",compression_opts=1,shuffle=True,fletcher32=True)
        self.actions=[];self.states=[]

    def _append(self,name,values):
        ds=self.datasets[name];start=ds.shape[0]
        ds.resize(start+len(values),axis=0);ds[start:]=values

    def action(self,ctrl):
        self.actions.append(np.asarray(ctrl,dtype="f8").copy())
        if len(self.actions)>=500:self.flush_actions()

    def flush_actions(self):
        if self.actions:
            self._append("actions/ctrl",self.actions);self.actions.clear()

    def state(self,tick,qpos,qvel,objects):
        poses=[[*o["position_world_m"],*o["quaternion_world_wxyz"]] for o in objects]
        flags=[[o.get("cleaned",False),o.get("heated",False)] for o in objects]
        row=(int(tick),np.asarray(qpos).copy(),np.asarray(qvel).copy(),poses,flags)
        if self.states and self.states[-1][0]==tick:self.states[-1]=row
        elif not self.states and self.datasets["states/tick"].shape[0] and self.datasets["states/tick"][-1]==tick:
            for name,value in zip(("states/tick","states/qpos","states/qvel","states/object_pose","states/task_flags"),row):
                self.datasets[name][-1]=value
        else:self.states.append(row)
        if len(self.states)>=25:self.flush_states()

    def flush_states(self):
        if self.states:
            for index,name in enumerate(("states/tick","states/qpos","states/qvel","states/object_pose","states/task_flags")):
                self._append(name,[row[index] for row in self.states])
            self.states.clear()
        self.file.flush()

    def seal(self,end_tick,reason,failed=False):
        self.flush_actions();self.flush_states()
        self.file.attrs["end_tick"]=int(end_tick)
        self.file.attrs["end_reason"]=str(reason)
        self.file.attrs["sampling_failed"]=bool(failed)
        self.file.attrs["sealed"]=not failed
        self.file.close()


class StateRecorder:
    def __init__(self, root, platform):
        for name,expected in platform.control["configuration"]["sources"].items():
            if Path(name).name!=name or hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()!=expected:
                raise ValueError("Control source changed since initialization; restart before recording")
        self.directory=Path(root)/("state_trajectory_"+uuid.uuid4().hex)
        self.directory.mkdir(parents=True,exist_ok=False)
        self.path=self.directory/"trajectory.h5"
        self.start_tick=self.last_tick=int(platform.controller.tick)
        self.steps=0;self.failed=False;self.sealed=False;self.file_sha256=None
        scene=platform.scene
        initial=snapshot(scene,platform.controller)
        metadata={"layout":scene.layout_id,"base_seed":scene.seed,"episode":platform.episode,
            "identity":platform.token(),"model_identity":model_identity(scene,builder_sha256=scene.builder_sha256),
            "control_identity":platform.control,"physics_hz":500,"state_hz":25,"images_included":False,
            "object_ids":list(scene.objects),"task_flag_columns":["cleaned","heated"],
            "software":{"python":system_platform.python_version(),"mujoco":mujoco.__version__,"numpy":np.__version__},
            "action_semantics":"actual substep ctrl16: left7+tendon,right7+tendon",
            "state_semantics":"qpos/qvel plus queried object xyz+quaternion wxyz; initial integration state stored once"}
        self.writer=CompactWriter(self.path,metadata,initial)
        self.file=self.writer.file
        self._state(platform.controller)

    @property
    def closed(self):
        return not bool(self.file.id.valid)

    def _state(self,controller):
        query=key_object_states(controller.scene,controller)
        self.writer.state(controller.tick,controller.scene.data.qpos,controller.scene.data.qvel,query["objects"])

    def sample(self, controller):
        try:
            if controller.tick!=self.last_tick+1:raise ValueError("State recording physics tick gap")
            self.writer.action(controller.last_u[controller.frame_step])
            self.steps+=1;self.last_tick=int(controller.tick)
            if (self.last_tick-self.start_tick)%20==0:self._state(controller)
        except Exception:
            self.failed=True
            raise

    def close(self,platform,reason):
        if not self.closed:
            try:
                self._state(platform.controller)
                self.writer.seal(self.last_tick,reason,self.failed)
                self.sealed=not self.failed
            except BaseException:
                self.failed=True
                if not self.closed:self.file.close()
                raise
        if self.sealed and self.file_sha256 is None:
            with self.path.open("rb") as stream:self.file_sha256=hashlib.file_digest(stream,"sha256").hexdigest()
        return self.info()

    def info(self):
        return {"schema":SCHEMA,"path":str(self.path),"active":not self.closed,
                "start_tick":self.start_tick,"end_tick":self.last_tick,"steps":self.steps,
                "sampling_failed":self.failed,"images_included":False,
                "sealed":self.sealed,"sha256":self.file_sha256}
