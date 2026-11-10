"""Batched version of the existing25Hz position controller, with CUDA graphs."""
import numpy as np
from .limits import joint_control_limits
from .control import DualArmController


class WarpControl:
    def __init__(self, backend, reference):
        from ._warp_kernels import write_targets
        self.b=backend;self.t=backend.torch;self.wp=backend.wp
        self.scene=backend.scene;self.kernel=write_targets
        self.reference=self.t.as_tensor(reference,dtype=self.t.float32,device=backend.device).expand(backend.nworld,16).clone()
        self.previous=self.reference.clone();self.accepted=self.reference.clone()
        self.previous_wp=self.wp.from_torch(self.previous);self.accepted_wp=self.wp.from_torch(self.accepted)
        actuators=[];dofs=[]
        for side in ('left','right'):
            arm=self.scene.arms[side];actuators.extend([*arm.actuators,arm.gripper_actuator]);dofs.extend([*arm.dofs,-1])
        m=backend.cpu_model
        self.ids=actuators
        gains=m.actuator_gainprm[actuators,0].copy();gains[[7,15]]=1
        self.kernel_args=[self.wp.array(actuators,dtype=int,device=backend.device),
            self.wp.array(dofs,dtype=int,device=backend.device),self.wp.array(gains.astype('f4'),device=backend.device),
            self.wp.array(m.actuator_ctrlrange[actuators,0].astype('f4'),device=backend.device),
            self.wp.array(m.actuator_ctrlrange[actuators,1].astype('f4'),device=backend.device)]
        with self.wp.ScopedDevice(backend.device):
            self.scratch=backend.mjw.make_data(m,nworld=backend.nworld,nconmax=1,njmax=1)
        self.scratch_qpos=self.wp.to_torch(self.scratch.qpos)
        self.bounds={side:tuple(self.t.as_tensor(v,dtype=self.t.float32,device=backend.device)
                               for v in joint_control_limits(m,arm)) for side,arm in self.scene.arms.items()}
        self.graph=None

    def _tcp(self, side, q):
        arm=self.scene.arms[side]
        self.scratch_qpos.copy_(self.b.qpos)
        self.scratch_qpos[:,list(arm.qpos)]=q
        with self.wp.ScopedDevice(self.b.device):self.b.mjw.kinematics(self.b.model,self.scratch)
        rotation=self.wp.to_torch(self.scratch.xmat)[:,arm.tcp_body]
        offset=self.t.as_tensor(arm.tcp_offset,dtype=q.dtype,device=q.device)
        return self.wp.to_torch(self.scratch.xpos)[:,arm.tcp_body]+rotation@offset

    def prepare(self, targets):
        result=targets.clone()
        for offset,side in ((0,'left'),(8,'right')):
            previous=self.reference[:,offset:offset+7];lo,hi=self.bounds[side]
            delta=result[:,offset:offset+7].clamp(lo,hi)-previous
            peak=delta.abs().amax(-1,keepdim=True)
            delta*=self.t.clamp((DualArmController.velocity_limit/25)/peak.clamp_min(1e-20),max=1)
            start=self._tcp(side,previous)
            scale=self.t.ones((self.b.nworld,1),device=self.b.device)
            chosen=previous.clone();passed=self.t.zeros((self.b.nworld,1),dtype=self.t.bool,device=self.b.device)
            cap=DualArmController.tcp_command_velocity/25
            for _ in range(6):
                candidate=previous+scale*delta
                distance=(self._tcp(side,candidate)-start).norm(dim=-1,keepdim=True)
                good=distance<=cap+1e-9
                chosen=self.t.where(good & ~passed,candidate,chosen);passed|=good
                scale=self.t.where(passed,scale,scale*cap/distance.clamp_min(1e-20))
            result[:,offset:offset+7]=chosen
            jaw=result[:,offset+7].clamp(0,.05)
            result[:,offset+7]=self.reference[:,offset+7]+(jaw-self.reference[:,offset+7]).clamp(-.002,.002)
        return result

    def _frame(self):
        for substep in range(20):
            self.wp.launch(self.kernel,dim=(self.b.nworld,16),inputs=[self.previous_wp,self.accepted_wp,
                self.b.data.qfrc_bias,*self.kernel_args,(substep+1)/20],outputs=[self.b.data.ctrl],device=self.b.device)
            self.b.mjw.step(self.b.model,self.b.data)

    def build_graph(self):
        # Warm kernels once at the caller's chosen reset state, then capture the
        # same operations. Restore is performed by the vector environment after
        # construction, before its first episode starts.
        with self.wp.ScopedDevice(self.b.device):
            self._frame();self.wp.synchronize()
            with self.wp.ScopedCapture(device=self.b.device) as capture:self._frame()
            self.graph=capture.graph

    def advance(self, targets):
        targets=self.t.as_tensor(targets,dtype=self.reference.dtype,device=self.b.device)
        if targets.shape!=self.reference.shape or not self.t.isfinite(targets).all():
            raise ValueError('Expected finite batched16D native position targets')
        self.previous.copy_(self.reference);self.accepted.copy_(self.prepare(targets))
        with self.wp.ScopedDevice(self.b.device):
            if self.graph is None:self._frame()
            else:self.wp.capture_launch(self.graph)
        self.reference.copy_(self.accepted);self.b.tick+=20
        return self.accepted
