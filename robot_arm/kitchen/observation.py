"""Same-tick camera snapshots and replay state, independent of live physics."""
from __future__ import annotations
import copy
import queue
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace, MappingProxyType
import numpy as np
import mujoco
from .control import state16,tcp_pose
from .model import build_scene
from .recording import (CAMERAS, PHYSICS_TRACE_SCHEMA, PHYSICS_TRACE_SUBSTEPS,
    PHYSICS_TRACE_HZ, PHYSICS_TRACE_MAX_CONTACTS_PER_SUBSTEP)
from .scene_identity import (LEGACY_CANONICAL_BUILDER_SHA256,
    legacy_snapshot_matches, model_identity)

def controller_snapshot(controller):
    scene=controller.scene
    spec=mujoco.mjtState.mjSTATE_INTEGRATION
    vector=np.empty(mujoco.mj_stateSize(scene.model,spec))
    mujoco.mj_getState(scene.model,scene.data,vector,spec)
    return {"model_identity":model_identity(scene,builder_sha256=scene.builder_sha256),
        "mujoco_state_spec":int(spec),"mujoco_state":vector.tolist(),
        "controller":{"tick":controller.tick,"frame_step":controller.frame_step,"reference":controller.reference.tolist(),
        "previous":controller.previous.tolist(),"accepted":controller.accepted.tolist(),
        "stopped":controller.stopped,"stop_reason":controller.stop_reason,"stop_tick":controller.stop_tick,
        "stop_evidence":copy.deepcopy(controller.stop_evidence_snapshot),
        "current_stop_evidence":copy.deepcopy(controller.stop_evidence),
        "allowed_device_contact_geoms":[scene.model.geom(g).name for g in sorted(controller.contact_guard.device_contacts)]},
        "device_state":controller.devices.snapshot(),"device_events":copy.deepcopy(controller.devices.events)}

def restore_snapshot(controller,snapshot):
    saved_identity=snapshot.get("model_identity")
    expected=model_identity(controller.scene,builder_sha256=controller.scene.builder_sha256)
    if saved_identity!=expected:
        legacy_reference=None
        if (controller.scene.layout_id=="canonical" and controller.scene.seed!=0 and
                isinstance(saved_identity,dict) and
                saved_identity.get("builder_sha256")==LEGACY_CANONICAL_BUILDER_SHA256):
            legacy_reference=build_scene(seed=0,layout_id="canonical").model
        if not legacy_snapshot_matches(controller.scene,saved_identity or {},
                current_builder_sha256=controller.scene.builder_sha256,
                canonical_seed0_model=legacy_reference):
            raise ValueError("Snapshot/model identity mismatch")
    if "device_state" not in snapshot:raise ValueError("Snapshot missing authoritative device timers")
    spec=snapshot["mujoco_state_spec"]
    vector=np.asarray(snapshot["mujoco_state"],dtype=float)
    if len(vector)!=mujoco.mj_stateSize(controller.scene.model,spec):
        raise ValueError("Snapshot/model state shape mismatch")
    mujoco.mj_setState(controller.scene.model,controller.scene.data,vector,spec)
    mujoco.mj_forward(controller.scene.model,controller.scene.data)
    saved=snapshot["controller"]
    for key in ("reference","previous","accepted"):setattr(controller,key,np.array(saved[key]))
    controller.tick=int(saved["tick"])
    controller.frame_step=int(saved.get("frame_step",0))
    controller.stopped=bool(saved["stopped"]);controller.stop_reason=saved["stop_reason"]
    controller.stop_tick=saved.get("stop_tick")
    controller.stop_evidence_snapshot=copy.deepcopy(saved.get("stop_evidence",[]))
    controller.stop_evidence=copy.deepcopy(saved.get("current_stop_evidence",[]))
    controller.contact_guard.device_contacts.clear()
    for geom_name in saved.get("allowed_device_contact_geoms",[]):
        controller.contact_guard.allow_device_contact(geom_name)
    controller.devices.restore(snapshot["device_state"])
    controller.devices.events=copy.deepcopy(snapshot.get("device_events",[]))

def joint_velocity16(scene):
    result=[]
    for side in ("left","right"):
        arm=scene.arms[side]
        result.extend(scene.data.qvel[list(arm.dofs)])
        # Named finger qpos addresses are not necessarily DOF addresses.
        names=(side+"_umi_left_finger_joint",side+"_umi_right_finger_joint")
        ids=[scene.model.joint(n).id for n in names]
        result.append(np.mean(scene.data.qvel[[scene.model.jnt_dofadr[i] for i in ids]]))
    return np.asarray(result)

def raw_tcp_targets(scene,targets=None):
    values=[]
    for side in ("left","right"):
        p,r=tcp_pose(scene.data,scene.arms[side])
        if targets and side in targets:
            p=np.asarray(targets[side]["position"]);r=np.asarray(targets[side]["rotation"])
        q=np.empty(4);mujoco.mju_mat2Quat(q,r.ravel())
        values.append(np.concatenate([p,q]))
    return np.array(values)


PHYSICS_TRACE_DEVICE_JOINTS=("faucet_switch","microwave_hinge","microwave_start_slide")


class PhysicsTraceSampler:
    """Read-only capture of exact post-mj_step MuJoCo evidence for one control frame."""
    def __init__(self,scene):
        self.scene=scene
        from .mechanics import coupling_manifest
        self.mechanical_constraints=coupling_manifest(scene)
        self.object_names=tuple(sorted(scene.objects))
        self.wash_timer_object_names=tuple(sorted(name for name in scene.objects
            if name in ("apple","pear","banana","tomato")))
        self.device_joint_ids={}
        for name in PHYSICS_TRACE_DEVICE_JOINTS:
            joint_id=int(mujoco.mj_name2id(scene.model,mujoco.mjtObj.mjOBJ_JOINT,name))
            if joint_id<0:raise ValueError("Missing physical-trace device joint: "+name)
            self.device_joint_ids[name]=joint_id
        expected_dt=1.0/PHYSICS_TRACE_HZ
        if not np.isclose(float(scene.model.opt.timestep),expected_dt,rtol=0.,atol=1e-12):
            raise ValueError("MuJoCo timestep does not match the physical-trace 500 Hz contract")
        self.equality_specs=[]
        for equality_id in range(int(scene.model.neq)):
            name=scene.model.eq(equality_id).name or f"equality_{equality_id}"
            self.equality_specs.append({
                "id":equality_id,"name":name,"type":int(scene.model.eq_type[equality_id]),
                "object1_id":int(scene.model.eq_obj1id[equality_id]),
                "object2_id":int(scene.model.eq_obj2id[equality_id]),
                "parameters":np.asarray(scene.model.eq_data[equality_id],dtype=float).tolist(),
            })

    def contract_metadata(self):
        model=self.scene.model
        return {
            "schema_version":PHYSICS_TRACE_SCHEMA,
            "event":"MUJOCO_PHYSICS_TRACE_FRAME",
            "sampling_hz":PHYSICS_TRACE_HZ,
            "substeps_per_observation":PHYSICS_TRACE_SUBSTEPS,
            "sample_semantics":"post_mj_step",
            "time_step_s":float(model.opt.timestep),
            "observation_semantics":"HDF5 observation/action row is pre-action at tick t; trace samples are t+1 through t+20",
            "object_names":list(self.object_names),
            "device_joint_names":list(PHYSICS_TRACE_DEVICE_JOINTS),
            "wash_timer_object_names":list(self.wash_timer_object_names),
            "geom_names_by_id":[model.geom(i).name or f"geom_{i}" for i in range(int(model.ngeom))],
            "body_names_by_id":[model.body(i).name or f"body_{i}" for i in range(int(model.nbody))],
            "equality_constraints":copy.deepcopy(self.equality_specs),
            "assist_constraints_qc_supported":False,
            "internal_mechanical_constraints":copy.deepcopy(self.mechanical_constraints),
            "contact_semantics":{
                "coverage":"all MuJoCo contacts; no contact filtering or truncation",
                "row_columns":["geom1_id","body1_id","geom2_id","body2_id","distance_m",
                    "position_world_xyz_m","frame_world_axes_row_major_9","wrench_contact_frame_6_n_nm"],
                "row_length":23,
                "maximum_contacts_per_substep":PHYSICS_TRACE_MAX_CONTACTS_PER_SUBSTEP,
                "distance":"signed MuJoCo contact distance in meters",
                "position":"world coordinates in meters",
                "frame":"raw MuJoCo contact frame, row-major 3x3",
                "wrench":"mj_contactForce six-vector in contact frame; first three N, last three N*m",
            },
            "device_state_semantics":"post DeviceState.update simulated timers and latches; wash/heating are explicit status, not fluid or thermal physics",
            "velocity_semantics":"mj_objectVelocity with flg_local=0; angular xyz then linear xyz in world coordinates at the named body origin",
            "quaternion_semantics":"MuJoCo wxyz",
            "constraint_semantics":"active equality ids per substep; static ids/type/object ids/eq_data in this contract metadata; active assist episodes remain unqualified pending runtime residual/load evidence",
        }

    def sample(self,controller,substep_index):
        if controller.scene is not self.scene:raise ValueError("Physical-trace sampler/controller scene mismatch")
        if type(substep_index) is not int or not 0<=substep_index<PHYSICS_TRACE_SUBSTEPS:
            raise ValueError("Physical-trace substep index outside the frozen 20-step frame")
        model,data=self.scene.model,self.scene.data
        if int(data.ncon)>PHYSICS_TRACE_MAX_CONTACTS_PER_SUBSTEP:
            raise RuntimeError("MuJoCo contact count exceeds physical-trace capacity; evidence will not be truncated")
        objects=[]
        for name in self.object_names:
            body_id=self.scene.objects[name]
            velocity=np.empty(6,dtype=float)
            mujoco.mj_objectVelocity(model,data,mujoco.mjtObj.mjOBJ_BODY,body_id,velocity,0)
            objects.append(np.concatenate((data.xpos[body_id],data.xquat[body_id],velocity)).astype(float).tolist())
        tcp=[]
        fingers=[]
        for side in ("left","right"):
            arm=self.scene.arms[side]
            position,rotation=tcp_pose(data,arm)
            quaternion=np.empty(4,dtype=float);mujoco.mju_mat2Quat(quaternion,rotation.ravel())
            tcp.append(np.concatenate((position,quaternion)).astype(float).tolist())
            fingers.append(np.asarray(data.qpos[list(arm.fingers)],dtype=float).tolist())
        devices=[]
        for name in PHYSICS_TRACE_DEVICE_JOINTS:
            joint_id=self.device_joint_ids[name]
            qpos_address=int(model.jnt_qposadr[joint_id])
            dof_address=int(model.jnt_dofadr[joint_id])
            devices.append([float(data.qpos[qpos_address]),float(data.qvel[dof_address])])
        contacts=[]
        for contact_index in range(int(data.ncon)):
            contact=data.contact[contact_index]
            geom1=int(contact.geom1);geom2=int(contact.geom2)
            body1=int(model.geom_bodyid[geom1]);body2=int(model.geom_bodyid[geom2])
            wrench=np.zeros(6,dtype=float)
            mujoco.mj_contactForce(model,data,contact_index,wrench)
            contacts.append([geom1,body1,geom2,body2,float(contact.dist),
                *np.asarray(contact.pos,dtype=float).tolist(),
                *np.asarray(contact.frame,dtype=float).tolist(),*wrench.tolist()])
        active_equalities=[i for i in range(int(model.neq)) if bool(data.eq_active[i])]
        from .mechanics import coupling_sample
        mechanical_sample=coupling_sample(self.scene,self.mechanical_constraints)
        allowed_device_contacts=sorted(int(geom_id) for geom_id in controller.contact_guard.device_contacts)
        device=controller.devices
        device_state={
            "enabled":bool(device.enabled),
            "wash_timer_s":[float(device.clean_seconds[name]) for name in self.wash_timer_object_names],
            "cleaned_object_names":sorted(device.cleaned),
            "heating_object_name":device.heating,
            "heat_timer_s":float(device.heat_seconds),
            "heated_object_names":sorted(device.heated),
            "button_hold_s":float(device.button_seconds),
            "button_latched":bool(device.button_latched),
            "simulated_device_state":True,
        }
        return {
            "sim_tick":int(controller.tick),"sim_time_s":float(data.time),
            "object_pose_velocity":objects,"tcp_pose":tcp,"finger_qpos":fingers,"device_joint_state":devices,
            "contacts":contacts,"contact_count":int(data.ncon),
            "active_equality_ids":active_equalities,
            "internal_mechanical_constraints":mechanical_sample,
            "gripper_actuator_force_n":{side:float(data.actuator_force[arm.gripper_actuator]) for side,arm in self.scene.arms.items()},
            "allowed_device_contact_geom_ids":allowed_device_contacts,
            "device_state":device_state,
            "controller_stopped":bool(controller.stopped),
            "controller_stop_reason":controller.stop_reason,
            "controller_stop_tick":controller.stop_tick,
            "actual_ctrl16":np.asarray(controller.last_u[substep_index],dtype=float).tolist(),
        }


class CameraRig:
    def __init__(self,scene,height=256,width=256):
        self.live=scene
        self.model=copy.copy(scene.model)
        self.data=mujoco.MjData(self.model)
        self.height=height;self.width=width
        self.ids=[int(mujoco.mj_name2id(self.model,mujoco.mjtObj.mjOBJ_CAMERA,n)) for n in CAMERAS]
        if min(self.ids)<0:raise ValueError("All three named cameras required")
        self.renderer=mujoco.Renderer(self.model,height=height,width=width)
    def intrinsics(self):
        matrices=[]
        for camera in self.ids:
            fy=.5*self.height/np.tan(np.deg2rad(self.model.cam_fovy[camera])*.5)
            matrices.append([[fy,0,(self.width-1)/2],[0,fy,(self.height-1)/2],[0,0,1]])
        return matrices
    def capture(self,tick):
        return self.capture_frozen(freeze_camera_state(self.live,tick))
    def capture_frozen(self,snapshot):
        """Render only a detached pre-action state; never query the live scene."""
        self.data.qpos[:]=snapshot["qpos"]
        self.data.qvel[:]=snapshot["qvel"]
        self.data.time=snapshot["time"]
        mujoco.mj_forward(self.model,self.data)
        images=[];poses=[]
        for name,camera in zip(CAMERAS,self.ids):
            self.renderer.update_scene(self.data,camera=name)
            images.append(self.renderer.render().copy())
            q=np.empty(4);mujoco.mju_mat2Quat(q,self.data.cam_xmat[camera])
            poses.append(np.concatenate([self.data.cam_xpos[camera],q]))
        return {"images":np.stack(images),"camera_poses":np.array(poses),"sim_tick":snapshot["sim_tick"],
            "capture_monotonic_ns":snapshot["capture_monotonic_ns"],
            "capture_end_monotonic_ns":time.monotonic_ns()}
    def close(self):self.renderer.close()


def freeze_camera_state(scene,tick):
    snapshot={"sim_tick":int(tick),"capture_monotonic_ns":time.monotonic_ns(),
        "qpos":scene.data.qpos.copy(),"qvel":scene.data.qvel.copy(),"time":float(scene.data.time)}
    snapshot["qpos"].setflags(write=False)
    snapshot["qvel"].setflags(write=False)
    return MappingProxyType(snapshot)


@dataclass(eq=False)
class _CameraRequest:
    snapshot: dict
    done: threading.Event=field(default_factory=threading.Event)
    result: dict | None=None
    error: BaseException | None=None
    render_seconds: float=0.


class ConcurrentCameraRig:
    """One owner thread and one outstanding immutable frame, without a backlog.

    submit/collect/close belong to the control thread. A native driver call cannot
    be cancelled; close reports a still-live daemon owner instead of falsely
    claiming cleanup or starting a replacement renderer.
    """
    def __init__(self,scene,height=256,width=256):
        self.live=scene
        self._render_scene=SimpleNamespace(model=copy.copy(scene.model))
        self._height=height;self._width=width
        self._queue=queue.Queue(maxsize=1);self._stop=threading.Event()
        self._ready=threading.Event();self._pending=None
        self._closed=False;self._failed=False;self._initial_error=None;self._close_error=None
        self._worker=threading.Thread(target=self._run,name="KitchenCamera",daemon=True)
        self._worker.start()
        if not self._ready.wait(10.):
            self._closed=True;self._stop.set();self._worker.join(2.)
            error=TimeoutError("Camera worker initialization timed out; cleanup requires owner exit")
            error.camera_worker_alive=self.worker_alive
            raise error
        if self._initial_error is not None:
            self.close()
            raise self._initial_error

    @property
    def worker_alive(self):return self._worker.is_alive()

    def _run(self):
        rig=None
        try:
            rig=CameraRig(self._render_scene,self._height,self._width)
            self._intrinsics=copy.deepcopy(rig.intrinsics())
            self._ready.set()
            while not self._stop.is_set():
                try:request=self._queue.get(timeout=.05)
                except queue.Empty:continue
                try:
                    if self._stop.is_set():raise RuntimeError("Camera closed before render")
                    started=time.perf_counter()
                    result=rig.capture_frozen(request.snapshot)
                    request.render_seconds=time.perf_counter()-started
                    request.result={key:np.array(value,copy=True) if isinstance(value,np.ndarray) else value
                        for key,value in result.items()}
                except BaseException as error:request.error=error
                finally:request.done.set()
                if request.error is not None:break
        except BaseException as error:
            self._initial_error=error
        finally:
            if rig is not None:
                try:rig.close()
                except BaseException as error:self._close_error=error
            self._ready.set()

    def intrinsics(self):return copy.deepcopy(self._intrinsics)

    def submit(self,tick):
        self.require_ready()
        request=_CameraRequest(freeze_camera_state(self.live,tick))
        self._pending=request
        self._queue.put_nowait(request)
        return request

    def require_ready(self):
        if self._closed:raise RuntimeError("Camera worker is closed")
        if self._failed:raise RuntimeError("Camera worker failed; close before reuse")
        if self._pending is not None:raise RuntimeError("Camera frame is already pending")
        if not self.worker_alive:raise RuntimeError("Camera owner exited; reset/restart required")

    def discard_pending(self,timeout=.2):
        """Consume an abandoned frame without writing it; retain poison on failure."""
        if self._pending is not None:self.collect(self._pending,timeout=timeout)

    def collect(self,request,timeout=.2):
        if self._closed or request is not self._pending:raise RuntimeError("Invalid or consumed camera request")
        if not request.done.wait(timeout):
            self._failed=True
            raise TimeoutError("Camera capture timed out; owner may still be running")
        self._pending=None
        if request.error is not None:
            self._failed=True
            raise request.error
        if request.result is None or request.result.get("sim_tick")!=request.snapshot["sim_tick"]:
            self._failed=True
            raise RuntimeError("Camera result/request tick mismatch")
        return request.result

    def capture(self,tick):return self.collect(self.submit(tick),timeout=5.)

    def close(self,timeout=2.):
        self._closed=True;self._stop.set()
        self._worker.join(timeout)
        if self.worker_alive:raise TimeoutError("Camera owner is still live; cleanup NOT_VERIFIED")
        if self._close_error is not None:raise RuntimeError("Camera owner cleanup failed") from self._close_error
