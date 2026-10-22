"""Named 6DoF DLS IK and rate-limited position control; no runtime pose teleport."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
import mujoco
from .model import HOME
from .limits import joint_control_limits

PAD_OFFSET=np.array([0.,.003,.1244])
NAMES=tuple([f"left_q{i}" for i in range(1,8)]+["left_tendon_m"]+
            [f"right_q{i}" for i in range(1,8)]+["right_tendon_m"])

def tcp_pose(data,arm):
    rotation=data.xmat[arm.tcp_body].reshape(3,3).copy()
    return data.xpos[arm.tcp_body]+rotation@np.asarray(arm.tcp_offset), rotation

def state16(scene):
    values=[]
    for side in ("left","right"):
        arm=scene.arms[side]
        values.extend(scene.data.qpos[list(arm.qpos)])
        values.append(np.mean(scene.data.qpos[list(arm.fingers)]))
    return np.asarray(values,dtype=np.float64)

def actuator16(scene):
    values=[]
    for side in ("left","right"):
        a=scene.arms[side]
        values.extend(scene.data.ctrl[list(a.actuators)])
        values.append(scene.data.ctrl[a.gripper_actuator])
    return np.asarray(values,dtype=np.float64)

@dataclass
class IKResult:
    q: np.ndarray
    position_error: float
    rotation_error: float
    success: bool

def solve_ik(scene,side,position,rotation=None,max_iterations=100,scratch_data=None,
             position_tolerance=.002,rotation_tolerance=.03):
    m,live=scene.model,scene.data
    a=scene.arms[side]
    position=np.asarray(position,dtype=float)
    if position.shape!=(3,) or not np.isfinite(position).all(): raise ValueError("Invalid TCP position")
    if not (0<position_tolerance<=.002 and 0<rotation_tolerance<=.03):
        raise ValueError("IK tolerances may only tighten the existing acceptance limits")
    scratch=mujoco.MjData(m) if scratch_data is None else scratch_data
    scratch.qpos[:]=live.qpos
    scratch.qvel[:]=0
    mujoco.mj_kinematics(m,scratch);mujoco.mj_comPos(m,scratch)
    _,current_rotation=tcp_pose(scratch,a)
    rotation=current_rotation if rotation is None else np.asarray(rotation,dtype=float)
    if rotation.shape!=(3,3) or not np.isfinite(rotation).all() or not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-5) or np.linalg.det(rotation)<.99:
        raise ValueError("Invalid TCP rotation")
    q=scratch.qpos[list(a.qpos)].copy()
    lower,upper=joint_control_limits(m,a)
    for _ in range(max_iterations):
        scratch.qpos[list(a.qpos)]=q
        mujoco.mj_kinematics(m,scratch);mujoco.mj_comPos(m,scratch)
        current,current_rotation=tcp_pose(scratch,a)
        p_error=position-current
        quat=np.empty(4)
        mujoco.mju_mat2Quat(quat,(rotation@current_rotation.T).ravel())
        if quat[0]<0: quat*=-1
        r_error=np.empty(3); mujoco.mju_quat2Vel(r_error,quat,1.)
        pe,re=float(np.linalg.norm(p_error)),float(np.linalg.norm(r_error))
        if pe<position_tolerance and re<rotation_tolerance: return IKResult(q,pe,re,True)
        jacp=np.zeros((3,m.nv)); jacr=np.zeros((3,m.nv))
        mujoco.mj_jac(m,scratch,jacp,jacr,current,a.tcp_body)
        jac=np.vstack([jacp[:,list(a.dofs)],.3*jacr[:,list(a.dofs)]])
        error=np.concatenate([p_error,.3*r_error])
        dq=jac.T@np.linalg.solve(jac@jac.T+.03**2*np.eye(6),error)
        dq=np.clip(dq,-.12,.12)
        q=np.clip(q+dq,lower,upper)
    return IKResult(q,pe,re,False)

class DualArmController:
    control_hz=25
    substeps=20
    velocity_limit=.7
    tcp_velocity_limit=.15
    # Margin for finite-rate actuator tracking; measured physical guard stays at 0.15 m/s.
    tcp_command_velocity=.12
    def __init__(self,scene):
        self.scene=scene
        self.ik_scratch=mujoco.MjData(scene.model)
        self.tcp_scratch=mujoco.MjData(scene.model)
        self.accepted=actuator16(scene)
        self.previous=self.accepted.copy()
        self.reference=self.accepted.copy()
        self.stopped=False
        self.stop_reason=None
        self.stop_tick=None
        self.stop_evidence_snapshot=[]
        self.tick=int(round(scene.data.time/scene.model.opt.timestep))
        self.last_u=np.zeros((self.substeps,16))
        self.frame_step=0
        from .safety import ContactGuard
        self.contact_guard=ContactGuard(scene)
        self.stop_evidence=[]
        from .devices import DeviceState
        self.devices=DeviceState(scene)

    def hold(self,reason):
        # Cancel pending interpolation, not a distant old target.
        target=self.reference.copy()
        state=state16(self.scene)
        for start in (0,8): target[start:start+7]=state[start:start+7]
        self.accepted=target.copy(); self.previous=target.copy(); self.reference=target.copy()
        if not self.stopped:
            self.stop_tick=self.tick
            self.stop_evidence_snapshot=list(self.stop_evidence)
            self.stop_reason=reason
        self.stopped=True
        self._write_ctrl(target)

    def enable(self):
        if self.contact_guard.inspect():raise RuntimeError("Unsafe contact persists; rearm rejected")
        self.stopped=False; self.stop_reason=None
        self.previous=self.reference.copy()
        self.accepted=self.previous.copy()

    def _write_ctrl(self,values):
        for offset,side in ((0,"left"),(8,"right")):
            a=self.scene.arms[side]
            ids=list(a.actuators)
            kp=self.scene.model.actuator_gainprm[ids,0]
            bias=self.scene.data.qfrc_bias[list(a.dofs)]
            # Feedforward enters the SAME torque-limited position actuators.
            # It never applies uncapped external robot forces.
            compensated=values[offset:offset+7]+bias/kp
            self.scene.data.ctrl[ids]=np.clip(compensated,
                self.scene.model.actuator_ctrlrange[ids,0],self.scene.model.actuator_ctrlrange[ids,1])
            self.scene.data.ctrl[a.gripper_actuator]=values[offset+7]

    def _cap_tcp_step(self,side,current_q,target_q):
        arm=self.scene.arms[side];m=self.scene.model
        scratch=self.tcp_scratch;scratch.qpos[:]=self.scene.data.qpos
        scratch.qpos[list(arm.qpos)]=current_q;mujoco.mj_kinematics(m,scratch)
        start=tcp_pose(scratch,arm)[0].copy()
        delta=target_q-current_q;limit=self.tcp_command_velocity/self.control_hz
        scale=1.
        for _ in range(6):
            scratch.qpos[list(arm.qpos)]=current_q+scale*delta;mujoco.mj_kinematics(m,scratch)
            distance=float(np.linalg.norm(tcp_pose(scratch,arm)[0]-start))
            if distance<=limit+1e-9:return current_q+scale*delta
            scale*=limit/distance
        return current_q.copy() # fail closed if the bounded search did not prove the cap

    def prepare_joint_targets(self,values,reference=None):
        values=np.asarray(values,dtype=float)
        if values.shape!=(16,) or not np.isfinite(values).all(): raise ValueError("Expected 16 finite joint/tendon targets")
        if self.stopped: raise RuntimeError("Controller stopped: "+str(self.stop_reason))
        result=values.copy()
        # Target and resulting 500Hz actuator signal are explicitly distinct.
        previous=self.reference.copy() if reference is None else np.asarray(reference,dtype=float).copy()
        for offset,side in ((0,"left"),(8,"right")):
            a=self.scene.arms[side]
            lo,hi=joint_control_limits(self.scene.model,a)
            result[offset:offset+7]=np.clip(result[offset:offset+7],lo,hi)
            # Preserve the certified joint-linear direction during rate limiting.
            delta=result[offset:offset+7]-previous[offset:offset+7]
            peak=float(np.max(np.abs(delta)))
            scale=min(1.,(self.velocity_limit/self.control_hz)/peak) if peak else 1.
            result[offset:offset+7]=previous[offset:offset+7]+scale*delta
            # Cap relative to the preceding command so tracking lag cannot bypass joint rate limits.
            result[offset:offset+7]=self._cap_tcp_step(side,previous[offset:offset+7],result[offset:offset+7])
            result[offset+7]=np.clip(result[offset+7],0,.05)
            result[offset+7]=previous[offset+7]+np.clip(result[offset+7]-previous[offset+7],-.002,.002)
        return result.copy()

    def accept_joint_targets(self,values):
        if self.frame_step:
            raise RuntimeError("Targets may change only at a 25Hz control boundary")
        result=self.prepare_joint_targets(values)
        self.previous=self.reference.copy()
        self.accepted=result.copy()
        return result

    def accept_cartesian(self,targets):
        result=self.reference.copy()
        diagnostics={}
        for offset,side in ((0,"left"),(8,"right")):
            if side not in targets: continue
            target=targets[side]
            ik=solve_ik(self.scene,side,target["position"],target.get("rotation"),scratch_data=self.ik_scratch)
            diagnostics[side]={"position_error":ik.position_error,"rotation_error":ik.rotation_error,"success":ik.success}
            if not ik.success:
                self.hold("IK_UNREACHABLE_"+side)
                return None,diagnostics
            result[offset:offset+7]=ik.q
            result[offset+7]=target.get("tendon_target",result[offset+7])
        return self.accept_joint_targets(result),diagnostics

    def advance(self,substep_callback=None):
        self.advance_substeps(self.substeps,substep_callback)
        return self.last_u.copy()

    def advance_substeps(self,count,substep_callback=None):
        if isinstance(count,bool) or not isinstance(count,int) or count<0:
            raise ValueError("Expected a nonnegative integer substep count")
        m,d=self.scene.model,self.scene.data
        for _ in range(count):
            step=self.frame_step
            alpha=(step+1)/self.substeps
            command=self.previous+(self.accepted-self.previous)*alpha
            self._write_ctrl(command)
            self.last_u[step]=actuator16(self.scene)
            mujoco.mj_step(m,d)
            self.tick+=1
            self.devices.update()
            violations=self.contact_guard.inspect()
            if violations and not self.stopped:
                self.stop_evidence=violations
                reason=("CONTACT_GUARD" if any("normal_force_n" in x for x in violations) else
                        "CONSTRAINT_GUARD" if any(x.get("kind") in ("jaw_transmission","finger_joint_limit") for x in violations) else
                        "TCP_SPEED_LIMIT")
                self.hold(reason)
            try:
                if substep_callback: substep_callback(self)
            finally:
                self.frame_step=(step+1)%self.substeps
                if not self.frame_step:
                    self.reference=self.accepted.copy()
                    self.previous=self.reference.copy()
