"""Single-owner kitchen simulator API; external planners provide every motion.

All hypothetical configurations use scratch data. Runtime actions write only
bounded robot actuator targets; reset is the sole live state initialization.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import json
from pathlib import Path
import secrets
import threading
import uuid

import mujoco
import numpy as np

from .collision import CollisionGeometry
from .control import DualArmController, actuator16, state16, tcp_pose
from .contact_execution import ENFORCE_CONTACT_POLICY, ContactExecution, DEVICE_TARGETS
# Task-only mode (user direction 2026-10-25): per-waypoint collision certificates
# are not enforced during motion; outcomes are checked at segment ends.
ENFORCE_COLLISION_CERTIFICATE = False
from .layouts import DEFAULT_PLATFORM_LAYOUT,layout_manifest
from .model import build_scene
from .registry import REMOVED_V3, SceneRegistry, control_identity, digest


class PlatformError(Exception):
    def __init__(self, code, detail=None):
        self.code,self.detail=code,detail
        super().__init__(code)


class PhysicsEvidence:
    """Append-only physical samples with same-tick state, source and hash chain."""
    def __init__(self, root, platform, source, *, execution_id=None, contact_contract=None, contact_preflight=None):
        from .observation import PhysicsTraceSampler
        self.sampler=PhysicsTraceSampler(platform.scene)
        directory=Path(root)/("episode_"+uuid.uuid4().hex)
        directory.mkdir(parents=True,exist_ok=False)
        compressed=getattr(platform,"evidence_compression",None)=="gzip"
        self.path=directory/("physics.jsonl.gz" if compressed else "physics.jsonl")
        if compressed:
            import gzip
            self.file=gzip.open(self.path,"xt",encoding="utf-8",compresslevel=1)
        else:
            self.file=self.path.open("x",encoding="utf-8",buffering=1)
        self.previous="0"*64
        self.samples=0
        self.failed=False
        self.start_tick=platform.controller.tick
        self.last_tick=self.start_tick
        try:
            self.write({"type":"metadata","source":source,"manifest":platform.manifest(),
                        "execution_id":execution_id,"contact_contract":copy.deepcopy(contact_contract),
                        "contact_preflight":copy.deepcopy(contact_preflight),
                        "physics_contract":self.sampler.contract_metadata(),
                        "record_semantics":"each physics record is one post_mj_step sample, not an HDF5 action row",
                        "task_success":"NOT_INFERRED_FROM_CALLER_LABEL"})
        except Exception:
            self.failed=True
            self.file.close()
            raise

    def write(self, value):
        record={"previous_sha256":self.previous,"record":value}
        self.previous=digest(record)
        record["sha256"]=self.previous
        self.file.write(json.dumps(record,separators=(",",":"),allow_nan=False)+"\n")

    def sample(self, controller):
        try:
            if controller.tick!=self.last_tick+1:raise RuntimeError("Physics evidence tick gap")
            sample=self.sampler.sample(controller,controller.frame_step)
            sample.update(type="physics",qpos=controller.scene.data.qpos.tolist(),qvel=controller.scene.data.qvel.tolist(),
                          requested_reference16=controller.reference.tolist(),accepted_target16=controller.accepted.tolist())
            self.write(sample)
            self.samples+=1
            self.last_tick=controller.tick
        except Exception:
            self.failed=True
            raise

    def close(self, reason, *, execution=None, contact_feedback=None):
        if self.file.closed:
            return
        self.write({"type":"end","reason":reason,"physics_samples":self.samples,"last_recorded_tick":self.last_tick,
                    "sampling_failed":self.failed,"execution":execution,"contact_feedback":contact_feedback,
                    "task_success":"NOT_INFERRED"})
        self.file.close()


class KitchenPlatform:
    read_methods={"get_scene_manifest","observe","query_objects","get_object_states","get_replay_snapshot","check_configuration","check_motion",
                  "get_execution_status","get_task_feedback","get_world_state","check_contact_motion"}
    mutation_methods={"reset","submit_joint_targets","execute_trajectory","step","cancel","hold","end_contact",
                      "start_state_recording","stop_state_recording"}
    arm_target_indices=np.array([0,1,2,3,4,5,6,8,9,10,11,12,13,14])

    def __init__(self, *,layout=DEFAULT_PLATFORM_LAYOUT,seed=None,mode="manual",data_root=None,
                 initialization_profile="fixed",evidence_compression=None,record_physics=False):
        if evidence_compression not in (None,"gzip"):
            raise ValueError("Unsupported evidence compression")
        self.evidence_compression=evidence_compression
        self.record_physics=bool(record_physics)
        self.post_step_observers=[]
        if mode not in ("manual","realtime"):
            raise ValueError("mode must be manual or realtime")
        if initialization_profile not in ("fixed", "research"):
            raise ValueError("initialization_profile must be fixed or research")
        self.lock=threading.RLock()
        self.session=uuid.uuid4().hex
        self.epoch=0
        self.mode=mode
        self.initialization_profile=initialization_profile
        self.episode=None
        self.initialization=None
        self.last_reset_failure=None
        self.data_root=None if data_root is None else Path(data_root).resolve()
        self.cache=OrderedDict()
        self.execution=None
        self.queue=[]
        self.evidence=None
        self.state_recorder=None
        self.sequence=0
        self.writer_owner=None
        self.history=OrderedDict()
        self._load(layout,seed)

    def _load(self,layout,seed):
        with self.lock:
            return self._load_locked(layout, seed)

    def _record_reset_failure(self,seed,cause,stage,initialization=None):
        detail={"attempted_seed":seed,"retained_episode_id":self.episode["episode_id"] if self.episode else None,
            "cause":cause,"stage":stage}
        if initialization is not None:detail["initialization"]=initialization
        self.last_reset_failure=detail
        if self.data_root is not None:
            try:
                directory=self.data_root/("session_"+self.session)
                directory.mkdir(parents=True,exist_ok=True)
                with (directory/"reset_failures.jsonl").open("a",encoding="utf-8") as file:
                    file.write(json.dumps(detail,allow_nan=False)+"\n")
            except Exception as record_error:
                detail["failure_record_error"]=str(record_error)
        return detail

    def _load_locked(self,layout,seed):
        if layout_manifest(layout).get("profile")!="kitchen_platform_v3":
            raise PlatformError("PLATFORM_V3_LAYOUT_REQUIRED")
        research=self.initialization_profile=="research"
        if research and layout!="kitchen_v3_norice_candidate_013":
            raise PlatformError("RESEARCH_REQUIRES_EXPLICIT_CANDIDATE_013")
        if seed is None:
            seed=secrets.randbits(32) if research else 0
            if research and self.episode and seed==self.episode["seed"]:
                seed=(seed+1) % 2**32
        if type(seed) is not int or not 0<=seed<2**32:
            raise PlatformError("INVALID_SEED")
        # Prepare the entire episode privately. Publication occurs only after
        # geometry, components, metadata storage and old-record sealing succeed.
        try:
            scene=build_scene(layout_id=layout,seed=0 if research else seed)
            door_angle=float(layout_manifest(layout).get("initial_door_angle_rad",0.))
            if door_angle:
                import mujoco
                adr=scene.model.jnt_qposadr[scene.model.joint("microwave_hinge").id]
                scene.data.qpos[adr]=door_angle
                mujoco.mj_forward(scene.model,scene.data)
            if research:
                from .research_initialization import initialize_research_scene
                initialization=initialize_research_scene(scene,seed)
            else:
                identity={"schema":"fixed-initialization-v1","seed":seed,
                    "base_scene_hash":scene.scene_sha256,"initial_state_hash":digest(scene.data.qpos.tolist())}
                initialization={**identity,"episode_scene_hash":digest(identity),
                    "status":"FIXED_SCENE_INITIALIZED","initial_qpos":scene.data.qpos.tolist(),
                    "initial_qvel":scene.data.qvel.tolist(),"dynamic_settling":{"occurred":False,"steps":0}}
            controller=DualArmController(scene)
            registry=SceneRegistry(scene)
            geometry=CollisionGeometry(scene)
            contact=ContactExecution(scene,controller)
            contact._stride=controller.substeps
            control=control_identity()
            episode={"episode_id":uuid.uuid4().hex,"episode_index":self.epoch+1,"seed":seed,
                "initialization_profile":self.initialization_profile,
                "initialization_hash":initialization["episode_scene_hash"],
                "base_scene_hash":scene.scene_sha256,"base_scene_seed":scene.seed,
                "status":initialization["status"]}
            if self.data_root is not None and self.record_physics:
                directory=self.data_root/("session_"+self.session)/"initializations"
                directory.mkdir(parents=True,exist_ok=True)
                with (directory/(episode["episode_id"]+".json")).open("x",encoding="utf-8") as file:
                    json.dump({"publication":"PREPARED_ONLY_ACTIVE_EPISODE_IS_FROM_OBSERVE",
                        "episode":episode,"initialization":initialization,
                        "control_identity":control},file,allow_nan=False)
        except Exception as error:
            detail=self._record_reset_failure(seed,str(error),"PREPARATION",getattr(error,"metadata",None))
            raise PlatformError("RESET_PREPARATION_FAILED",detail) from error
        if self.evidence:
            try:
                self._seal_evidence("RESET")
            except PlatformError as error:
                self._record_reset_failure(seed,error.code,"PREVIOUS_EVIDENCE_SEAL")
                raise
        self.scene,self.controller,self.registry=scene,controller,registry
        self.geometry,self.contact,self.control=geometry,contact,control
        self.initialization,self.episode=initialization,episode
        self.last_reset_failure=None
        self.epoch+=1
        self.execution=None
        self.queue=[]
        self.evidence=None
        self.sequence=0
        self.state_recorder=None

    def token(self):
        with self.lock:
            return self._token_locked()

    def _token_locked(self):
        return {"runtime_session":self.session,"epoch":self.epoch,"sim_tick":self.controller.tick,
                "scene_hash":self.scene.scene_sha256,"control_hash":self.control["control_hash"],
                "episode_id":self.episode["episode_id"],"initialization_hash":self.episode["initialization_hash"]}

    def manifest(self):
        with self.lock:
            return self._manifest_locked()

    def _manifest_locked(self):
        return {**self.registry.manifest(),"control_identity":copy.deepcopy(self.control),
                "runtime":{**self.token(),"mode":self.mode},"episode":copy.deepcopy(self.episode),
                "initialization":copy.deepcopy(self.initialization),
                "environment":{"initialization_profile":self.initialization_profile,
                    "base_scene_hash":self.scene.scene_sha256,"base_scene_seed":self.scene.seed,
                    "reset_seed_semantics":"uint32 explicit seed; omitted/null generates new seed in research, fixed preserves current seed",
                    "authority":"PC MuJoCo reset only; Unity never randomizes",
                    "randomized_task_success":"NOT_INFERRED_FROM_FIXED_DEMO_EVIDENCE"},
                "methods":sorted(self.read_methods|self.mutation_methods),
                "trajectory_contract":{"waypoint_period_ticks":20,"values":"left 7 rad, tendon m, right 7 rad, tendon m",
                    "collision_prediction":"joint-linear, current device/object state; rechecked before every frame",
                    "force_guard":"500Hz measured; certificate is not dynamics success",
                    "maximum_waypoints_per_request":4500},
                "contact_execution_contract":{"schema":"kitchen-contact-v1","phases":["acquire","carry","device","release"],
                    "target_support_transition":{"default":False,"opt_in_field":"target_support_transition",
                        "scope":"currently retained free target to declared bounded static support only",
                        "permission":"current >=50ms bilateral grasp and retained anchor; per-point support limits unchanged",
                        "load_evidence":"unchanged individual upward vertical force >0.25N; weak transition is not support success"},
                    "maximum_waypoints":50,"maximum_scratch_substeps":1000,"maximum_preview_wall_s":3.,
                    "prediction":"bounded discrete scratch dynamics, not continuous certificate or task success",
                    "monitoring":"all actual post-step samples, including after queue completion",
                    "completion":"release then end_contact after measured support and pad separation; cancel holds"},
                "capabilities":{"geometry_queries":"AVAILABLE","bounded_joint_control":"AVAILABLE",
                    "object_state_query":"get_object_states; scratch-forward same-tick state, no images",
                    "state_recording":"start_state_recording/stop_state_recording; manual, continuous controls and checkpoints",
                    "task_strategies":"EXTERNAL_PLANNER","physical_task_results":"NOT_VERIFIED",
                    "formal_layout":"AWAITING_USER_REVIEW","quest":"HARDWARE_REQUIRED"}}

    def _configuration(self,values):
        values=np.asarray(values,dtype=float)
        if values.shape!=(16,) or not np.isfinite(values).all():
            raise PlatformError("INVALID_TARGETS","Expected 16 finite values, tendon units m")
        q=self.scene.data.qpos.copy()
        for offset,side in ((0,"left"),(8,"right")):
            arm=self.scene.arms[side]
            from .limits import joint_control_limits
            lo,hi=joint_control_limits(self.scene.model,arm)
            if np.any(values[offset:offset+7]<lo) or np.any(values[offset:offset+7]>hi) or not 0<=values[offset+7]<=.05:
                raise PlatformError("JOINT_OR_TENDON_LIMIT",side)
            q[list(arm.qpos)]=values[offset:offset+7]
        return q

    def _intentional_pairs(self, names):
        # Only explicit pad-target contact is exempt from the 2mm *clearance*,
        # never from nonpenetration, physics collision or measured-force guards.
        from .contact import object_geom_ids
        allowed_devices={"faucet_handle_geom","microwave_door_handle","microwave_start_geom"}
        result=[]
        for name in names:
            if name in self.scene.objects:
                targets=object_geom_ids(self.scene,name)
            elif name in allowed_devices:
                targets={self.scene.model.geom(name).id}
            else:
                raise PlatformError("INVALID_CONTACT_TARGET",name)
            for arm in self.scene.arms.values():
                result.extend((p,g) for p in arm.pad_geoms for g in targets)
        return result

    def _check(self,a,b,contacts=()):
        return {**self.geometry.certificate(a,b,intentional_pairs=self._intentional_pairs(contacts)),
                "scope":"arm joints with current measured fingers/devices/objects frozen; no contact-dynamics certificate"}

    def observe(self):
        with self.lock:
            return self._observe_locked()

    def _observe_locked(self):
        m,d=self.scene.model,self.scene.data
        arms={}
        for side,arm in self.scene.arms.items():
            p,r=tcp_pose(d,arm)
            q=np.empty(4);mujoco.mju_mat2Quat(q,r.ravel())
            from .gripper import fingertip_gap
            gap=fingertip_gap(m,d,arm)["inner_gap_m"]
            arms[side]={"qpos_rad":d.qpos[list(arm.qpos)].tolist(),"qvel_rad_s":d.qvel[list(arm.dofs)].tolist(),
                "finger_qpos_m":d.qpos[list(arm.fingers)].tolist(),"physical_inner_gap_m":max(0.,gap),
                "tendon_target_m":float(d.ctrl[arm.gripper_actuator]),
                "tcp_position_world_m":p.tolist(),"tcp_quaternion_wxyz":q.tolist()}
        return {**self.token(),"sim_time_s":float(d.time),"control_frame_phase":self.controller.frame_step,
                "episode":copy.deepcopy(self.episode),"last_reset_failure":copy.deepcopy(self.last_reset_failure),
                "robots":arms,"state16_measured":state16(self.scene).tolist(),
                "reference16":self.controller.reference.tolist(),"accepted_target16":self.controller.accepted.tolist(),
                "actual_ctrl16":actuator16(self.scene).tolist(),"qpos":d.qpos.tolist(),"qvel":d.qvel.tolist(),
                "objects":self.registry.query(),"devices":self.controller.devices.snapshot(),
                "execution":copy.deepcopy(self.execution),"contact_execution":self.contact.feedback(),"stopped":self.controller.stopped,
                "stop_reason":self.controller.stop_reason,"stop_tick":self.controller.stop_tick}

    def world(self):
        with self.lock:
            return self._world_locked()

    def _world_locked(self):
        self.sequence+=1
        m,d=self.scene.model,self.scene.data
        return {"type":"world","schema":1,"runtimeSession":self.session,"sceneEpoch":str(self.epoch),
                "sceneHash":self.scene.scene_sha256,"sequence":self.sequence,"simTick":self.controller.tick,
                "episode":copy.deepcopy(self.episode),
                "stopped":self.controller.stopped,"stop_reason":self.controller.stop_reason,
                "bodies":[{"id":b,"name":m.body(b).name,"position":d.xpos[b].tolist(),
                            "quaternion_wxyz":d.xquat[b].tolist()} for b in range(m.nbody)],
                "state16":state16(self.scene).tolist(),"physics_authority":"MuJoCo"}

    def _validate_token(self,token,*,fresh=True):
        if not isinstance(token,dict):
            raise PlatformError("TOKEN_REQUIRED")
        now=self.token()
        for key in now:
            if key=="sim_tick" and not fresh:
                continue
            if token.get(key)!=now[key]:
                raise PlatformError("STALE_OR_MISMATCHED_TOKEN",{"field":key,"current":now[key]})

    def _start_execution(self,waypoints,source,contacts,guarded_gripper=False,contact_contract=None):
        if self.execution and self.execution["status"]=="RUNNING":
            raise PlatformError("EXECUTION_BUSY")
        if self.controller.stopped:
            raise PlatformError("CONTROLLER_STOPPED",self.controller.stop_reason)
        if self.controller.frame_step:
            raise PlatformError("CONTROL_BOUNDARY_REQUIRED",{"phase":self.controller.frame_step,"period":20})
        if source not in ("external_planner","programmatic_validation"):
            raise PlatformError("INVALID_SOURCE")
        if not isinstance(waypoints,list) or not 1<=len(waypoints)<=4500:
            raise PlatformError("INVALID_WAYPOINT_COUNT")
        contact_mode=contact_contract is not None
        active_contact=self.contact.feedback()["active"]
        if active_contact and not contact_mode:
            raise PlatformError("CONTACT_PHASE_ACTIVE_USE_RELEASE_OR_CANCEL")
        if contact_mode:
            if contacts or guarded_gripper:
                raise PlatformError("CONTACT_CONTRACT_CANNOT_MIX_LEGACY_PERMISSIONS")
            contact_contract=self.contact.validate_contract(contact_contract)
            if len(waypoints)>50:
                raise PlatformError("CONTACT_WAYPOINT_LIMIT",50)
        checked=[]
        gripper_changes=False
        previous=self.controller.reference.copy()
        qprevious=self.scene.data.qpos.copy()
        for index,values in enumerate(waypoints):
            qnext=self._configuration(values)
            accepted=self.controller.prepare_joint_targets(values,reference=previous)
            if guarded_gripper and not np.allclose(accepted[self.arm_target_indices],
                    self.controller.reference[self.arm_target_indices],atol=1e-12,rtol=0.):
                raise PlatformError("GUARDED_GRIPPER_REQUIRES_STATIONARY_ARMS","No arm motion anywhere in a guarded gripper sequence")
            changes=not np.allclose(accepted[[7,15]],previous[[7,15]],rtol=0.,atol=1e-12)
            gripper_changes|=changes
            if changes:
                if not guarded_gripper and not contact_mode:raise PlatformError("GRIPPER_SWEEP_NOT_CERTIFIED","Use guarded stationary phase or explicit contact_contract; no full geometry PASS")
                if guarded_gripper and not np.allclose(accepted[[0,1,2,3,4,5,6,8,9,10,11,12,13,14]],previous[[0,1,2,3,4,5,6,8,9,10,11,12,13,14]],atol=1e-12,rtol=0.):
                    raise PlatformError("GUARDED_GRIPPER_REQUIRES_STATIONARY_ARMS")
            if not np.allclose(accepted,values,rtol=0.,atol=1e-9):
                raise PlatformError("TRAJECTORY_REQUIRES_RATE_LIMITING",{"index":index,"accepted":accepted.tolist()})
            if not contact_mode and ENFORCE_COLLISION_CERTIFICATE:
                result=self._check(qprevious,qnext,contacts)
                if not result["pass"]:
                    raise PlatformError("MOTION_NOT_CERTIFIED",{"index":index,"result":result})
            checked.append(accepted)
            previous,qprevious=accepted,qnext
        if guarded_gripper and not self._arms_settled():
            raise PlatformError("GUARDED_GRIPPER_REQUIRES_SETTLED_ARMS","Measured joint speed <=0.01 rad/s and tracking error <=0.005 rad required")
        preview=(self.contact.preview([q.tolist() for q in checked],contact_contract)
                 if (contact_mode and ENFORCE_CONTACT_POLICY) else None)
        if preview is not None and not preview["pass"]:
            raise PlatformError("CONTACT_PREFLIGHT_REJECTED",preview)
        # Nothing above this line mutates the live controller, queue or recorder.
        if active_contact:
            self._seal_evidence("CONTACT_PHASE_TRANSITION")
        execution_id=uuid.uuid4().hex
        try:
            writer=(PhysicsEvidence(self.data_root,self,source,execution_id=execution_id,
                    contact_contract=contact_contract,contact_preflight=preview) if self.data_root and self.record_physics else None)
        except Exception as error:
            self.controller.hold("EVIDENCE_OPEN_FAILURE")
            self._finish("FAILED",str(error))
            raise PlatformError("EVIDENCE_OPEN_FAILURE",str(error)) from error
        if contact_mode:
            self.contact.activate(contact_contract)
            if contact_contract["target"] in DEVICE_TARGETS:
                self.controller.contact_guard.allow_device_contact(contact_contract["target"])
        for name in contacts:
            if name in {"faucet_handle_geom","microwave_door_handle","microwave_start_geom"}:
                self.controller.contact_guard.allow_device_contact(name)
        self.evidence=writer
        self.queue=checked
        self.execution={"id":execution_id,"status":"RUNNING","source":source,"start_tick":self.controller.tick,
            "epoch":self.epoch,"scene_hash":self.scene.scene_sha256,
            "waypoints_total":len(checked),"waypoints_started":0,"contact_targets":list(contacts),
            "task_success":"NOT_INFERRED","evidence_path":str(writer.path) if writer else None,
            "evidence_complete":False,"evidence_prefix_saved":False,
            "motion_certification":"ARM_GEOMETRY_ONLY_CONTACT_DYNAMICS_NOT_CERTIFIED" if gripper_changes else "ARM_JOINT_CONTINUOUS_BOUND",
            "gripper_sweep_certified":False if gripper_changes else None,
            "guarded_gripper":bool(guarded_gripper),
            "contact_contract":copy.deepcopy(contact_contract),"contact_preflight":preview,
            "start_configuration_hash":digest(self.scene.data.qpos.tolist())}
        if contact_mode:
            self.execution["motion_certification"]="DISCRETE_CONTACT_DYNAMICS_PREFLIGHT_NOT_CONTINUOUS_OR_TASK_PASS"
        return copy.deepcopy(self.execution)

    def _seal_evidence(self,reason):
        if not self.execution:
            return
        if self.evidence and not self.evidence.file.closed:
            try:
                self.evidence.close(reason,execution=copy.deepcopy(self.execution),contact_feedback=self.contact.feedback())
                self.execution["evidence_prefix_saved"]=True
            except Exception as error:
                self.evidence.failed=True
                self.execution["evidence_error"]=str(error)
                self.controller.hold("EVIDENCE_SEAL_FAILURE")
                self.queue=[]
                self.execution.update(status="FAILED",reason="EVIDENCE_SEAL_FAILURE",end_tick=self.controller.tick,
                    evidence_complete=False,evidence_prefix_saved=True,evidence_pending_contact_release=False,
                    last_recorded_tick=self.evidence.last_tick,contact_feedback=self.contact.feedback())
                self.contact.clear()
                self.controller.contact_guard.device_contacts.clear()
                try:
                    if not self.evidence.file.closed:self.evidence.file.close()
                except Exception as close_error:
                    self.execution["evidence_close_error"]=str(close_error)
                self.history[self.execution["id"]]=copy.deepcopy(self.execution)
                raise PlatformError("EVIDENCE_SEAL_FAILURE",str(error)) from error
            self.execution["evidence_complete"]=bool(not self.evidence.failed and
                self.evidence.last_tick==self.controller.tick and
                self.evidence.samples==self.controller.tick-self.execution["start_tick"])
            self.execution["last_recorded_tick"]=self.evidence.last_tick
        self.execution["evidence_pending_contact_release"]=False
        self.history[self.execution["id"]]=copy.deepcopy(self.execution)

    def _finish(self,status,reason):
        contact_active=self.contact.feedback()["active"]
        if self.execution and (self.execution["status"]=="RUNNING" or contact_active):
            self.execution.update(status=status,end_tick=self.controller.tick,reason=reason)
            if contact_active:
                self.execution["contact_feedback"]=self.contact.feedback()
            if status=="COMPLETED" and contact_active:
                self.execution["evidence_pending_contact_release"]=True
            else:
                self._seal_evidence(reason)
            self.history[self.execution["id"]]=copy.deepcopy(self.execution)
            while len(self.history)>128:self.history.popitem(last=False)
        self.queue=[]
        if status!="COMPLETED" or not contact_active:
            self.contact.clear()
            self.controller.contact_guard.device_contacts.clear()

    def _arms_settled(self):
        return all(np.max(np.abs(self.scene.data.qvel[list(arm.dofs)]))<=.01 and
                   np.max(np.abs(self.scene.data.qpos[list(arm.qpos)]-self.controller.reference[offset:offset+7]))<=.005
                   for offset,arm in ((0,self.scene.arms["left"]),(8,self.scene.arms["right"])))

    def _record_and_guard(self,controller):
        if self.evidence and not self.evidence.file.closed:self.evidence.sample(controller)
        # Contact policy and task-quality predicates now run once per 25Hz control
        # frame (user-approved speedup); the 500Hz action stream is still recorded
        # per substep below. The engine advances its timers by the frame duration.
        boundary = controller.frame_step == controller.substeps - 1
        if boundary:
            violations=self.contact.sample(controller)
            if violations:
                if self.execution:self.execution["contact_violation"]=violations
                controller.hold("CONTACT_POLICY_VIOLATION")
            if self.execution and self.execution["status"]=="RUNNING" and self.execution.get("guarded_gripper") and not self._arms_settled():
                controller.hold("GUARDED_GRIPPER_ARMS_NOT_SETTLED")
            for observer in tuple(self.post_step_observers):observer(controller)
        if self.state_recorder and not self.state_recorder.closed:
            self.state_recorder.sample(controller)

    def advance(self,count):
        with self.lock:
            return self._advance_locked(count)

    def _advance_locked(self,count):
        if type(count) is not int or not 1<=count<=5000:
            raise PlatformError("INVALID_STEP_COUNT","1..5000 exact physics substeps")
        for _ in range(count):
            try:
                if self.controller.frame_step==0 and self.queue and not self.controller.stopped:
                    # The whole list was already certified in _start_execution
                    # (collision certificate per waypoint, or one scratch
                    # dynamics preview for the contracted batch). Re-checking
                    # every 40ms target against the live state doubled the cost
                    # without adding evidence; the live 25Hz contact policy and
                    # guard still hold on any real violation.
                    self.controller.accept_joint_targets(self.queue.pop(0))
                    self.execution["waypoints_started"]+=1
                self.controller.advance_substeps(1,self._record_and_guard)
            except Exception as error:
                self.controller.hold("PHYSICS_OR_EVIDENCE_FAILURE")
                self._finish("FAILED",str(error))
                raise PlatformError("PHYSICS_OR_EVIDENCE_FAILURE",str(error)) from error
            if self.controller.stopped:
                self._finish("FAILED",self.controller.stop_reason)
            elif self.execution and self.execution["status"]=="RUNNING" and not self.queue and not self.controller.frame_step:
                self._finish("COMPLETED","WAYPOINTS_CONSUMED_NOT_TASK_SUCCESS")
        return self.observe()

    def _query_objects(self,ids=None,object_type=None):
        if isinstance(ids,str):ids=[ids]
        if ids is not None:
            if not isinstance(ids,(list,tuple)) or any(not isinstance(name,str) for name in ids):
                raise PlatformError("INVALID_OBJECT_IDS","Use a semantic ID or list of semantic IDs")
            for name in ids:
                if name in REMOVED_V3:raise PlatformError("UNSUPPORTED_OBJECT_IN_SCENE_VERSION",name)
                if name not in self.registry.entries:raise PlatformError("UNKNOWN_OBJECT",name)
        return self.registry.query(ids,object_type)

    def _dispatch(self,method,args):
        if method=="get_scene_manifest":return self.manifest()
        if method=="observe":return self.observe()
        if method=="get_world_state":return self.world()
        if method=="query_objects":return self._query_objects(args.get("ids"),args.get("object_type"))
        if method=="get_object_states":
            from .object_state import object_states,key_object_states
            if args.get("detail")=="key":
                result=key_object_states(self.scene,self.controller)
                if args.get("ids") is not None:
                    self._query_objects(args["ids"],args.get("object_type"))
                    result["objects"]=[o for o in result["objects"] if o["id"] in args["ids"]]
                return result
            selected=self._query_objects(args.get("ids"),args.get("object_type"))
            return {**object_states(self.scene,self.controller,ids=[item["id"] for item in selected]),
                    "identity":self.token(),"episode":copy.deepcopy(self.episode)}
        if method=="get_replay_snapshot":
            from .state_recording import snapshot
            from .scene_identity import model_identity
            return {"schema":"kitchen_replay_snapshot_v1","identity":self.token(),
                    "episode":copy.deepcopy(self.episode),"layout":self.scene.layout_id,
                    "base_seed":self.scene.seed,"initialization":copy.deepcopy(self.initialization),
                    "model_identity":model_identity(self.scene,builder_sha256=self.scene.builder_sha256),
                    "snapshot":snapshot(self.scene,self.controller),"images_included":False}
        if method=="start_state_recording":
            if args:raise PlatformError("UNKNOWN_RECORDING_ARGUMENT")
            if self.mode!="manual":raise PlatformError("STATE_RECORDING_REQUIRES_MANUAL_MODE")
            if self.data_root is None:raise PlatformError("STATE_RECORDING_REQUIRES_DATA_ROOT")
            if self.state_recorder and not self.state_recorder.closed:raise PlatformError("STATE_RECORDING_ACTIVE")
            from .state_recording import StateRecorder
            try:
                self.state_recorder=StateRecorder(self.data_root,self)
            except Exception as error:
                raise PlatformError("STATE_RECORDING_START_FAILED",str(error)) from error
            return self.state_recorder.info()
        if method=="stop_state_recording":
            if args:raise PlatformError("UNKNOWN_RECORDING_ARGUMENT")
            if self.state_recorder is None:raise PlatformError("NO_STATE_RECORDING")
            try:
                return self.state_recorder.close(self,"OPERATOR_STOP")
            except Exception as error:
                self.controller.hold("STATE_RECORDING_SEAL_FAILED")
                raise PlatformError("STATE_RECORDING_SEAL_FAILED",str(error)) from error
        if method=="get_execution_status":
            requested=args.get("execution_id")
            if requested is None:return copy.deepcopy(self.execution)
            if self.execution and requested==self.execution["id"]:return copy.deepcopy(self.execution)
            if requested in self.history:return copy.deepcopy(self.history[requested])
            raise PlatformError("UNKNOWN_EXECUTION_ID",requested)
        if method=="check_configuration":
            q=self._configuration(args["joint_targets"])
            return self._check(q,q,args.get("contact_targets",()))
        if method=="check_motion":
            a=self.scene.data.qpos.copy() if "start_joint_targets" not in args else self._configuration(args["start_joint_targets"])
            b=self._configuration(args["end_joint_targets"])
            return {**self._check(a,b,args.get("contact_targets",())),"start_configuration_hash":digest(a.tolist()),
                    "end_configuration_hash":digest(b.tolist()),"identity":self.token()}
        if method=="check_contact_motion":
            return {**self.contact.preview(args["waypoints"],args["contact_contract"]),"identity":self.token()}
        if method=="get_task_feedback":
            ids=[item["id"] for item in self._query_objects(args.get("object_ids",list(self.scene.objects)))]
            device=self.controller.devices
            return {"task_instance_id":args.get("task_instance_id"),"execution_id":self.execution["id"] if self.execution else None,
                    "predicate_version":"kitchen-local-contacts-v3.1","evidence_tick":self.controller.tick,
                    "objects":{name:{"grasp":device.grasp_evidence(name),"in_basket":device.in_basket(name) if name in device.clean_seconds else False,
                                "in_microwave":device.in_microwave(name)} for name in ids if name in self.scene.objects},
                    "device_state":device.snapshot(),"events":copy.deepcopy(device.events),"contact_execution":self.contact.feedback(),"task_success":"NOT_VERIFIED",
                    "trajectory_completion_is_task_success":False}
        if method=="reset":
            if self.state_recorder and not self.state_recorder.closed:
                raise PlatformError("STATE_RECORDING_ACTIVE_STOP_FIRST")
            if set(args)-{"seed"}:
                raise PlatformError("UNKNOWN_RESET_ARGUMENT",self._record_reset_failure(
                    args.get("seed"),"UNKNOWN_RESET_ARGUMENT","INPUT_VALIDATION"))
            if self.execution and self.execution["status"]=="RUNNING":raise PlatformError("EXECUTION_BUSY_CANCEL_FIRST")
            if self.contact.feedback()["active"]:raise PlatformError("CONTACT_ACTIVE_CANCEL_FIRST")
            seed=args.get("seed",None if self.initialization_profile=="research" else self.episode["seed"])
            if seed is None and self.initialization_profile=="fixed":seed=self.episode["seed"]
            if seed is not None and (type(seed) is not int or not 0<=seed<2**32):
                raise PlatformError("INVALID_SEED",self._record_reset_failure(seed,"INVALID_SEED","INPUT_VALIDATION"))
            # Layout changes require a new runtime, not a hidden identity mutation.
            self._load(self.scene.layout_id,seed)
            return self.observe()
        if method=="end_contact":
            feedback=self.contact.feedback()
            if not feedback["active"] or feedback["contract"]["phase"]!="release":
                raise PlatformError("RELEASE_PHASE_REQUIRED")
            if self.queue or (self.execution and self.execution["status"]=="RUNNING") or self.controller.frame_step:
                raise PlatformError("CONTACT_EXECUTION_OR_FRAME_ACTIVE")
            measured=feedback["measured"]
            if ENFORCE_CONTACT_POLICY and (
                    feedback["evidence_tick"]!=self.controller.tick or feedback["violations"] or
                    feedback["measured_pad_separation_s"]<.05-1e-12 or
                    not measured.get("grasp",{}).get("pads_separated",False) or
                    any(f>=.5 for f in measured.get("grasp",{}).get("pad_normal_forces_n",[1.,1.]))):
                raise PlatformError("MEASURED_PAD_SEPARATION_REQUIRED")
            for name in feedback["contract"]["payload_ids"]:
                if not measured.get("support",{}).get(name,{}).get("supported",False):
                    raise PlatformError("RELEASE_SUPPORT_REQUIRED",name)
                body=self.scene.objects[name]
                velocity=np.zeros(6)
                mujoco.mj_objectVelocity(self.scene.model,self.scene.data,mujoco.mjtObj.mjOBJ_BODY,body,velocity,0)
                if np.linalg.norm(velocity[3:])>.02 or np.linalg.norm(velocity[:3])>.05:
                    raise PlatformError("RELEASE_NOT_STABLE",name)
            self._seal_evidence("MEASURED_CONTACT_RELEASE")
            self.contact.clear()
            self.controller.contact_guard.device_contacts.clear()
            return {"released":True,"feedback":feedback,"task_success":"NOT_INFERRED"}
        if method in ("cancel","hold"):
            self.controller.hold("CANCELLED_BY_CLIENT" if method=="cancel" else "HELD_BY_CLIENT")
            self._finish("CANCELLED" if method=="cancel" else "HELD",self.controller.stop_reason)
            return self.observe()
        if method=="step":
            if self.mode!="manual":raise PlatformError("REALTIME_CLOCK_OWNS_STEP")
            return self.advance(args.get("n_substeps",20))
        if method=="submit_joint_targets":
            values=self.controller.prepare_joint_targets(args["joint_targets"])
            result=self._start_execution([values.tolist()],args.get("source","external_planner"),args.get("contact_targets",()),args.get("guarded_gripper",False),args.get("contact_contract"))
            return {"execution":result,"requested":args["joint_targets"],"accepted":values.tolist(),"rate_limited":not np.allclose(values,args["joint_targets"])}
        if method=="execute_trajectory":
            return self._start_execution(args["waypoints"],args.get("source","external_planner"),args.get("contact_targets",()),args.get("guarded_gripper",False),args.get("contact_contract"))
        raise PlatformError("UNKNOWN_METHOD",method)

    def request(self,method,args=None,*,request_id=None,token=None,writer_id=None,connection_id=None):
        args={} if args is None else args
        with self.lock:
            fingerprint=None
            mutating=isinstance(method,str) and method in self.mutation_methods
            try:
                if not isinstance(method,str):raise PlatformError("INVALID_METHOD")
                if not isinstance(args,dict):raise PlatformError("INVALID_ARGUMENTS")
                if method not in self.read_methods|self.mutation_methods:raise PlatformError("UNKNOWN_METHOD")
                fingerprint=digest({"method":method,"args":args,"token":token,"writer_id":writer_id})
                if mutating:
                    if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise PlatformError("REQUEST_ID_REQUIRED")
                    if request_id in self.cache:
                        old,reply=self.cache[request_id]
                        if old!=fingerprint:raise PlatformError("REQUEST_ID_CONFLICT")
                        return copy.deepcopy(reply)
                    self._validate_token(token,fresh=method not in ("cancel","hold"))
                    if not isinstance(writer_id,str) or not 16<=len(writer_id)<=128:raise PlatformError("WRITER_ID_REQUIRED")
                    if self.writer_owner is not None and self.writer_owner!=(writer_id,connection_id):raise PlatformError("WRITER_OWNERSHIP_CONFLICT")
                result=self._dispatch(method,args)
                if mutating:self.writer_owner=(writer_id,connection_id)
                reply={"ok":True,"result":result,"token":self.token()}
            except (PlatformError,ValueError,TypeError,KeyError,RuntimeError,OverflowError) as error:
                reply={"ok":False,"error":{"code":error.code if isinstance(error,PlatformError) else "INVALID_REQUEST",
                    "detail":error.detail if isinstance(error,PlatformError) else str(error)},"token":self.token()}
            if fingerprint is not None and mutating and isinstance(request_id,str) and request_id not in self.cache:
                self.cache[request_id]=(fingerprint,copy.deepcopy(reply))
                while len(self.cache)>2048:self.cache.popitem(last=False)
            return reply

    def release_writer(self,writer_id,connection_id=None):
        with self.lock:
            if writer_id and self.writer_owner==(writer_id,connection_id):
                if self.contact.feedback()["active"] or (self.execution and self.execution["status"]=="RUNNING"):
                    self.controller.hold("WRITER_DISCONNECTED")
                    self._finish("CANCELLED","WRITER_DISCONNECTED")
                self.writer_owner=None

    def close(self):
        with self.lock:
            self.controller.hold("SERVER_SHUTDOWN")
            self._finish("CANCELLED","SERVER_SHUTDOWN")
            if self.evidence:self.evidence.close("SERVER_SHUTDOWN")
            if self.state_recorder:self.state_recorder.close(self,"SERVER_SHUTDOWN")
