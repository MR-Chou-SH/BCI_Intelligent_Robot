"""CPU reference state-RL environment on the existing kitchen controller."""
import numpy as np
from robot_arm.kitchen.platform import KitchenPlatform
from robot_arm.kitchen.state_tensor import StateTensor
from robot_arm.kitchen.action_codec import encode
from integration.kitchen.data.lerobot_export import pack16
from integration.kitchen.tasks.catalog import cases
from integration.kitchen.runtime.client import LocalPlannerClient
from .tasks import TaskMonitor
from .observations import ObservationLayout
from . import snapshots


class PrivilegedEnvironment:
    def __init__(self, *, case_id='kitchen_expert_v3:L1:apple', seed=0, horizon=750):
        self.case=next(c for c in cases() if c['case_id']==case_id)
        if self.case['task']!='L1':
            raise NotImplementedError('RL environment preparation currently implementsL1; TaskMonitor supports all task definitions')
        self.horizon=int(horizon);self.rng=np.random.default_rng(seed)
        if self.horizon<1:raise ValueError('horizon must be positive')
        self.platform=KitchenPlatform(layout='kitchen_v3_norice_candidate_013',seed=seed,initialization_profile='research')
        self.client=LocalPlannerClient(self.platform)
        self.monitor=None
        self.reset(seed=seed, first=True)

    def reset(self, *, seed=None, snapshot=None, curriculum=False, first=False):
        if self.monitor:self.monitor.close()
        if snapshot is None:
            if not first:
                self.client.token=self.platform.token()
                reply=self.client.call('reset',{'seed':int(seed) if seed is not None else int(self.rng.integers(2**32))},mutating=True)
                if not reply['ok']:raise RuntimeError(reply)
            self.platform.advance(500,detail='execution')
        self.monitor=TaskMonitor(self.platform,self.case,validate_start=snapshot is None)
        if snapshot is not None:snapshots.apply(self.platform,self.monitor,self.rng,snapshot,new_task_episode=curriculum)
        self.reader=StateTensor(self.platform.scene);self.layout=ObservationLayout(self.reader,self.platform.scene,self.case)
        saved=snapshot.get('environment',{}) if snapshot is not None and not curriculum else {}
        if saved and saved['horizon']!=self.horizon:raise ValueError('Reset horizon mismatch')
        self.steps=saved.get('steps',0);self.done=saved.get('done',False)
        return self.observation(),{'task':self.monitor.evidence(),'initialization':self.platform.initialization}

    def state(self):return self.reader.query(self.platform.scene,self.platform.controller)

    def observation(self):return self.layout.normalize(self.layout.raw(self.state(),self.monitor))

    def accepted_action(self):
        q,g=encode(self.platform.controller.accepted)
        return pack16(q,g)

    def step(self, action):
        if self.done:raise RuntimeError('Episode ended; reset before stepping')
        action=np.asarray(action,dtype=float)
        if action.shape!=(8,) or not np.isfinite(action).all():raise ValueError('Expected finite8D action')
        action=np.clip(action,-1,1)
        c=self.platform.controller;target=c.reference.copy()
        target[:7]+=action[:7]*c.velocity_limit/c.control_hz
        target[7]=.05*(1-(action[7]+1)/2)
        c.accept_joint_targets(target);self.platform.advance(20,detail='execution');self.steps+=1
        task=self.monitor.update();terminated=task['success'] or task['failure_reason'] is not None
        truncated=self.steps>=self.horizon and not terminated
        self.done=bool(terminated or truncated)
        return self.observation(),float(task['success']),bool(terminated),bool(truncated),{
            'task':task,'accepted_action':self.accepted_action(),'phi_state':self.layout.diversity(self.state())}

    def capture(self):
        return {**snapshots.capture(self.platform,self.monitor,self.rng),
                'environment':{'steps':self.steps,'horizon':self.horizon,'done':self.done}}

    def close(self):
        if self.monitor:self.monitor.close()
        self.platform.close()
