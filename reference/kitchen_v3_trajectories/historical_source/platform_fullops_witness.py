"""Fixed burger carrier dynamics witness, using the existing bounded controller."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import mujoco
import numpy as np

from integration.kitchen.platform_microwave_dynamics import DoorFailure,DoorWitness
from robot_arm.kitchen.contact import object_geom_ids
from robot_arm.kitchen.control import tcp_pose


class CarrierWitness(DoorWitness):
    def __init__(self,source,output,scope,door_tendon=.036):
        if source['status']!='FINITE_CARRIER_GEOMETRY_PASS':raise ValueError('Complete finite geometry required')
        self.isolated_lift=bool(source.get('carrier',{}).get('isolated_lift',False))
        if self.isolated_lift and scope!='lift':raise ValueError('Isolated geometry cannot prove cavity operation')
        if not .03<=door_tendon<=.04:raise ValueError('Door grip calibration remains .03..04m')
        super().__init__(source,output,door_tendon,require_door_geometry=not self.isolated_lift)
        self.deadline=time.monotonic()+600
        self.opening_stages=[r['stage'] for r in source['rows'] if r['stage'].startswith('door_') and r['stage'][5:].isdigit()]
        self.scope=scope
        self.payload=('burger_container','burger')
        self.container_geoms=object_geom_ids(self.scene,'burger_container')
        self.payload_geoms=set().union(*(object_geom_ids(self.scene,n) for n in self.payload))
        self.meal_required=False
        self.grasp_age=self.loss_age=self.hold_age=self.peak_hold=0.
        self.peak_grasp_age=self.maximum_lift=self.max_drift=self.max_angle=0.
        self.anchor=None
        self.rotation_anchor=None
        self.initial_z=float(self.scene.data.xpos[self.scene.objects['burger_container'],2])
        self.release_support=None
        self.release_age=self.peak_release_age=0.
        self.continuous_food_support=True

    def meal_forces(self):
        forces=np.zeros(2)
        for i,c in enumerate(self.scene.data.contact):
            for k,pad in enumerate(self.scene.arms['right'].pad_geoms):
                if (int(c.geom1)==pad and int(c.geom2) in self.container_geoms) or (int(c.geom2)==pad and int(c.geom1) in self.container_geoms):
                    f=np.zeros(6);mujoco.mj_contactForce(self.scene.model,self.scene.data,i,f);forces[k]+=max(0.,float(f[0]))
        return forces

    def relative_container(self):
        p,r=tcp_pose(self.scene.data,self.scene.arms['right'])
        body=self.scene.objects['burger_container']
        return r.T@(self.scene.data.xpos[body]-p),r.T@self.scene.data.xmat[body].reshape(3,3)

    def released(self):
        m,d=self.scene.model,self.scene.data
        pads=self.scene.arms['right'].pad_geoms
        if any((int(c.geom1) in pads and int(c.geom2) in self.payload_geoms) or
               (int(c.geom2) in pads and int(c.geom1) in self.payload_geoms) for c in d.contact):return False
        if any(mujoco.mj_geomDistance(m,d,p,g,.005,None)<.0005 for p in pads for g in self.payload_geoms):return False
        if not self.controller.devices.supports('burger_container',[self.release_support]):return False
        if not self.controller.devices.supports('burger',['burger_plate']):return False
        for name in self.payload:
            v=np.zeros(6);mujoco.mj_objectVelocity(m,d,mujoco.mjtObj.mjOBJ_BODY,self.scene.objects[name],v,0)
            if np.linalg.norm(v[3:])>.02 or np.linalg.norm(v[:3])>.05:return False
        return True

    def sample(self,controller):
        super().sample(controller)
        dt=float(self.scene.model.opt.timestep)
        bilateral=bool(np.all(self.meal_forces()>=.5))
        self.grasp_age=self.grasp_age+dt if bilateral else 0.
        self.loss_age=0. if bilateral else self.loss_age+dt
        self.peak_grasp_age=max(self.peak_grasp_age,self.grasp_age)
        if self.meal_required:
            p,r=self.relative_container()
            drift=float(np.linalg.norm(p-self.anchor))
            angle=float(np.arccos(np.clip((np.trace(r@self.rotation_anchor.T)-1)/2,-1,1)))
            lift=float(self.scene.data.xpos[self.scene.objects['burger_container'],2])-self.initial_z
            support=controller.devices.supports('burger',['burger_plate'])
            self.continuous_food_support &= support
            self.maximum_lift=max(self.maximum_lift,lift)
            self.max_drift=max(self.max_drift,drift);self.max_angle=max(self.max_angle,angle)
            self.hold_age=self.hold_age+dt if bilateral and support and lift>=.03 else 0.
            self.peak_hold=max(self.peak_hold,self.hold_age)
            if self.loss_age>.05:controller.hold('MEAL_GRASP_LOST')
            elif drift>.015:controller.hold('MEAL_CONTAINER_SLIPPED')
            elif angle>np.deg2rad(30):controller.hold('MEAL_CONTAINER_ROTATED')
            elif not support:controller.hold('MEAL_FOOD_SUPPORT_LOST')
        if self.release_support:
            self.release_age=self.release_age+dt if self.released() else 0.
            self.peak_release_age=max(self.peak_release_age,self.release_age)

    def wait_release(self,support):
        self.release_support=support
        self.release_age=self.peak_release_age=0.
        for _ in range(125):
            self.frame()
            if self.release_age>=2.:return True
        return False

    def complete_cycle(self,checks):
        raise DoorFailure('NO_VALIDATED_CYCLE_CONTINUATION')

    def run(self):
        reason=None;checks={}
        try:
            self.wait(2.);self.mark('settled')
            if not self.isolated_lift:
                self.move('pregrasp');self.move('approach');self.gripper(self.tendon)
                checks['door_bilateral_50ms']=self.bilateral_seconds>=.05
                if not checks['door_bilateral_50ms']:raise DoorFailure('DOOR_GRASP_FAILED')
                self.require_grip=True
                for stage in self.opening_stages:self.move(stage)
                self.wait(.5)
                checks['door_physically_opened_70deg']=self.scene.data.qpos[self.joint]>=np.deg2rad(70)
                if not checks['door_physically_opened_70deg']:raise DoorFailure('EFFECTIVE_DOOR_OPENING_NOT_REACHED')
                self.require_grip=False;self.gripper(0.);self.move('withdraw_up');self.move('door_open_home')
            self.move('meal_pregrasp');self.move('meal_grasp');self.gripper(.05)
            checks['carrier_bilateral_50ms']=self.grasp_age>=.05
            if not checks['carrier_bilateral_50ms']:raise DoorFailure('CARRIER_GRASP_FAILED')
            self.anchor,self.rotation_anchor=self.relative_container();self.meal_required=True
            self.move('meal_lift_initial');self.move('meal_lift');self.wait(1.2)
            checks['continuous_lift30mm_hold1s']=self.peak_hold>=1.
            if not checks['continuous_lift30mm_hold1s']:raise DoorFailure('QUALIFIED_LOAD_HOLD_FAILED')
            self.mark('qualified_burger_lift')
            if self.scope=='lift':
                self.move('meal_lift_initial');self.move('meal_grasp')
                self.meal_required=False;self.gripper(0.);self.move('meal_pregrasp')
                checks['released_stable_separated_2s']=self.wait_release('v3_counter_right')
            else:
                for stage in ('meal_rotate_15','meal_rotate_30','meal_rotate_45','meal_pre_entry','meal_entry_front','meal_entry_lip','meal_inside_hover','meal_place'):
                    self.move(stage)
                self.meal_required=False;self.gripper(0.);self.move('meal_withdraw');self.move('meal_exit_home')
                checks['in_cavity_released_stable2s']=self.wait_release('microwave_bottom')
                checks['actual_meal_ready']=self.controller.devices.meal_ready('burger_container')
                if self.scope=='cycle' and all(checks.values()):self.complete_cycle(checks)
            checks['food_continuously_supported']=bool(self.continuous_food_support)
            self.mark('finished_'+self.scope)
            if not all(checks.values()):reason='COMPONENT_PREDICATE_FAILED'
        except (DoorFailure,RuntimeError,ValueError) as e:
            reason=str(e);self.meal_required=self.require_grip=False;self.mark('failed')
        finally:self.writer.close(reason or 'FINITE_CARRIER_'+self.scope.upper()+'_FINISHED')
        result=dict(status=('CARRIER_LIFT_RELEASE_PASS' if self.scope=='lift' else 'COMPLETE_MICROWAVE_CYCLE_PASS' if self.scope=='cycle' else 'CAVITY_PLACEMENT_COMPONENT_PASS') if checks and all(checks.values()) and not reason else 'PHYSICS_FAIL',
            reason=reason,checks={k:bool(v) for k,v in checks.items()},scope=self.scope,manifest=self.manifest(),trace=self.trace,
            physics_seconds=float(self.scene.data.time),physics_samples=self.writer.samples,record=str(self.writer.path),
            maximum_lift_m=self.maximum_lift,continuous_qualified_hold_s=self.peak_hold,continuous_released_stable_s=self.peak_release_age,
            maximum_relative_drift_m=self.max_drift,maximum_relative_rotation_deg=float(np.rad2deg(self.max_angle)),
            maximum_grasp_age_s=self.peak_grasp_age,stop_reason=self.controller.stop_reason,stop_evidence=self.controller.stop_evidence_snapshot,
            device_state=self.controller.devices.snapshot(),live_object_pose_writes=0,door_actuator_commands=0,
            active_equalities=np.flatnonzero(self.scene.data.eq_active).tolist(),
            isolated_lift=self.isolated_lift,
            door_tendon_target_m=self.tendon,
            cycle_evidence=getattr(self,'cycle_evidence',None),
            full_close_button_heat_reopen_retrieve='VERIFIED' if self.scope=='cycle' and checks and all(checks.values()) and not reason else 'NOT_VERIFIED',public_api_execution='NOT_VERIFIED_INTERNAL_WITNESS')
        (self.output/'report.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
        return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--scope',choices=('lift','place'),default='lift')
    p.add_argument('--door-tendon',type=float,default=.036)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    result=CarrierWitness(json.loads(args.source.read_text()),args.output,args.scope,args.door_tendon).run()
    print(json.dumps({k:result[k] for k in ('status','reason','checks','maximum_lift_m','continuous_qualified_hold_s','physics_seconds')}))
    return 0 if result['status']!='PHYSICS_FAIL' else 1


if __name__=='__main__':raise SystemExit(main())
