"""Finite contact-only microwave door witness; never commands the door joint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import mujoco
import numpy as np
from PIL import Image

from robot_arm.kitchen.control import DualArmController, solve_ik, tcp_pose
from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.platform import PhysicsEvidence
from robot_arm.kitchen.registry import SceneRegistry


class DoorFailure(RuntimeError):
    pass


class DoorWitness:
    def __init__(self, source, output, tendon, *, require_door_geometry=True):
        if require_door_geometry and not source["door_open_release_retreat"]:
            raise ValueError("A complete opening/release/retreat geometric witness is required")
        self.scene=build_scene(layout_id=source["manifest"]["layout_id"],seed=0)
        self.registry=SceneRegistry(self.scene)
        if self.scene.scene_sha256 != source["manifest"]["scene_hash"]:
            raise ValueError("Geometric source and live model identities differ")
        self.controller=DualArmController(self.scene)
        self.controller.contact_guard.allow_device_contact("microwave_door_handle")
        self.output,self.tendon=output,tendon
        self.rows={row["stage"]:np.asarray(row["qpos"],dtype=float) for row in source["rows"] if row["pass"]}
        self.writer=PhysicsEvidence(output,self,"programmatic_validation")
        self.joint=int(self.scene.model.jnt_qposadr[self.scene.model.joint("microwave_hinge").id])
        self.handle=self.scene.model.geom("microwave_door_handle").id
        self.phase="settling"
        self.trace=[]
        self.bilateral_seconds=0.
        self.maximum_bilateral_seconds=0.
        self.maximum_angle=0.
        self.peak_force=0.
        self.lost_seconds=0.
        self.require_grip=False
        self.deadline=time.monotonic()+300

    def manifest(self):
        return self.registry.manifest()

    def forces(self):
        m,d=self.scene.model,self.scene.data
        result=np.zeros(2)
        for index,contact in enumerate(d.contact):
            for k,pad in enumerate(self.scene.arms["right"].pad_geoms):
                if {int(contact.geom1),int(contact.geom2)}=={pad,self.handle}:
                    force=np.zeros(6)
                    mujoco.mj_contactForce(m,d,index,force)
                    result[k]+=max(0.,float(force[0]))
        return result

    def sample(self,controller):
        self.writer.sample(controller)
        force=self.forces()
        bilateral=bool(np.all(force>=.5))
        dt=self.scene.model.opt.timestep
        self.bilateral_seconds=self.bilateral_seconds+dt if bilateral else 0.
        self.maximum_bilateral_seconds=max(self.maximum_bilateral_seconds,self.bilateral_seconds)
        self.peak_force=max(self.peak_force,float(force.max()))
        self.maximum_angle=max(self.maximum_angle,float(np.rad2deg(self.scene.data.qpos[self.joint])))
        self.lost_seconds=0. if bilateral else self.lost_seconds+dt
        if self.require_grip and self.lost_seconds>.08:
            controller.hold("HANDLE_GRASP_LOST")

    def frame(self):
        if self.scene.data.time>=150 or time.monotonic()>=self.deadline:
            raise DoorFailure("BOUNDED_DOOR_TIMEOUT")
        self.controller.advance(self.sample)
        if self.controller.stopped:
            raise DoorFailure(self.controller.stop_reason)

    def wait(self,seconds):
        for _ in range(round(seconds*25)):
            self.frame()

    def mark(self,phase):
        self.phase=phase
        self.trace.append({"phase":phase,"tick":self.controller.tick,"time_s":float(self.scene.data.time),
            "door_angle_deg":float(np.rad2deg(self.scene.data.qpos[self.joint])),
            "pad_normal_forces_n":self.forces().tolist(),
            "tcp_position_m":tcp_pose(self.scene.data,self.scene.arms["right"])[0].tolist()})
        with mujoco.Renderer(self.scene.model,height=700,width=1000) as renderer:
            renderer.update_scene(self.scene.data,camera="v3_microwave")
            Image.fromarray(renderer.render()).save(self.output/(str(len(self.trace)).zfill(2)+"_"+phase+".png"))

    def move(self,stage):
        q=self.rows[stage][list(self.scene.arms["right"].qpos)]
        return self.move_q(stage,q)

    def move_q(self,stage,q):
        self.phase=stage
        for _ in range(300):
            target=self.controller.reference.copy()
            target[8:15]=q
            self.controller.accept_joint_targets(target)
            self.frame()
            if np.max(np.abs(self.scene.data.qpos[list(self.scene.arms["right"].qpos)]-q))<.008 and np.max(np.abs(self.controller.reference[8:15]-q))<1e-6:
                self.mark(stage)
                return
        raise DoorFailure("JOINT_TRACKING_TIMEOUT_"+stage)

    def measured_regrasp(self):
        from integration.kitchen.platform_device_geometry import FixedDoorWitness
        fixture=FixedDoorWitness(self.scene,75,-1,75)
        angle=float(self.scene.data.qpos[self.joint])
        position,rotation,_=fixture.target(angle,self.scene.data.qpos)
        ik=solve_ik(self.scene,"right",position,rotation,max_iterations=300)
        if not ik.success:
            raise DoorFailure("MEASURED_OPEN_HANDLE_IK_FAILED")
        q=self.scene.data.qpos.copy()
        q[list(self.scene.arms["right"].qpos)]=ik.q
        geometry=CollisionGeometry(self.scene)
        proof=geometry.certificate(self.scene.data.qpos.copy(),q,geometry.pairs(("microwave_door",)),
            intentional_pairs=fixture.contacts,max_evaluations=6000)
        if not proof["pass"]:
            raise DoorFailure("MEASURED_OPEN_HANDLE_APPROACH_NOT_CERTIFIED_"+proof["status"])
        self.move_q("measured_handle_reapproach",ik.q)

    def gripper(self,value):
        for _ in range(45):
            target=self.controller.reference.copy()
            target[15]=value
            self.controller.accept_joint_targets(target)
            self.frame()

    def run(self):
        checks={}
        reason=None
        try:
            self.wait(2.)
            self.mark("settled")
            self.move("pregrasp")
            self.move("approach")
            self.gripper(self.tendon)
            self.mark("grasp_closed")
            checks["bilateral_handle_grasp_50ms"]=self.bilateral_seconds>=.05
            if not checks["bilateral_handle_grasp_50ms"]:
                raise DoorFailure("NO_SUSTAINED_HANDLE_GRASP")
            self.require_grip=True
            for degrees in range(5,76,5):
                self.move("door_"+str(degrees))
            self.wait(.5)
            checks["opened_at_least_70deg"]=bool(self.scene.data.qpos[self.joint]>=np.deg2rad(70))
            if not checks["opened_at_least_70deg"]:
                raise DoorFailure("EFFECTIVE_DOOR_OPENING_NOT_REACHED")
            self.require_grip=False
            self.gripper(0.)
            self.mark("released_open")
            self.move("withdraw_up")
            self.move("door_open_home")
            checks["released_and_retracted_with_door_open"]=bool(self.scene.data.qpos[self.joint]>=np.deg2rad(68))
            self.move("withdraw_up")
            self.measured_regrasp()
            self.gripper(self.tendon)
            checks["regrasp_open_handle_50ms"]=self.bilateral_seconds>=.05
            if not checks["regrasp_open_handle_50ms"]:
                raise DoorFailure("OPEN_HANDLE_REGRASP_FAILED")
            self.require_grip=True
            for degrees in range(70,0,-5):
                self.move("door_"+str(degrees))
            self.move("approach")
            self.wait(.5)
            checks["closed_under_3deg"]=bool(abs(self.scene.data.qpos[self.joint])<np.deg2rad(3))
            self.require_grip=False
            self.gripper(0.)
            self.mark("released_closed")
            self.move("pregrasp")
            if not all(checks.values()):
                reason="DOOR_CYCLE_PREDICATE_FAILED"
        except (DoorFailure,RuntimeError,ValueError) as error:
            reason=str(error)
            self.require_grip=False
            self.mark("failed")
        finally:
            self.writer.close(reason or "DOOR_COMPONENT_CYCLE_FINISHED")
        result={"status":"DOOR_CONTACT_COMPONENT_PASS" if checks and all(checks.values()) and not reason else "PHYSICS_FAIL",
            "reason":reason,"checks":checks,"manifest":self.manifest(),"trace":self.trace,
            "physics_seconds":float(self.scene.data.time),"physics_samples":self.writer.samples,
            "record":str(self.writer.path),"peak_pad_force_n":self.peak_force,
            "maximum_bilateral_seconds":self.maximum_bilateral_seconds,"maximum_angle_deg":self.maximum_angle,
            "stop_reason":self.controller.stop_reason,"stop_evidence":self.controller.stop_evidence_snapshot,
            "tendon_target_m":self.tendon,"door_actuator_commands":0,"live_object_pose_writes":0,
            "active_equalities":np.flatnonzero(self.scene.data.eq_active).tolist(),
            "food_placement_and_retrieval":"NOT_VERIFIED; door-only component witness"}
        (self.output/"report.json").write_text(json.dumps(result,indent=2,allow_nan=False),encoding="utf-8")
        return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--tendon",type=float,default=.036)
    args=parser.parse_args()
    if not .03<=args.tendon<=.04:
        parser.error("Bounded handle-grip calibration domain is .03..04 m")
    args.output.mkdir(parents=True,exist_ok=False)
    result=DoorWitness(json.loads(args.source.read_text(encoding="utf-8")),args.output,args.tendon).run()
    print(json.dumps({k:result[k] for k in ("status","reason","checks","maximum_angle_deg","physics_seconds")}))
    return 0 if result["status"]=="DOOR_CONTACT_COMPONENT_PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
