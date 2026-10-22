"""One fixed, contact-only burger microwave cycle, never a general planner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import mujoco
import numpy as np

from integration.kitchen.platform_fullops_witness import CarrierWitness
from integration.kitchen.platform_microwave_dynamics import DoorFailure
from robot_arm.kitchen.control import tcp_pose,solve_ik
from robot_arm.kitchen.contact_execution import ContactExecution
from robot_arm.kitchen.registry import object_bounds


class MicrowaveCycle(CarrierWitness):
    def __init__(self,opening,continuation,output):
        if continuation['status']!='FINITE_MICROWAVE_CONTINUATION_GEOMETRY_PASS':
            raise ValueError('FINITE_CONTINUATION_GEOMETRY_REQUIRED')
        super().__init__(opening,output,'cycle',.04)
        for key in ('scene_hash','control_identity'):
            if continuation['manifest'][key]!=self.manifest()[key]:
                self.writer.close('CONTINUATION_IDENTITY_MISMATCH');raise ValueError('CONTINUATION_IDENTITY_MISMATCH')
        self.continuation=continuation
        self.rows.update({r['stage']:np.asarray(r['qpos']) for r in continuation['rows'] if r['pass']})
        self.cycle_evidence={}
        self.button_policy=None
        self.writer.write(dict(type='finite_continuation',source_record_audit=continuation['source_record_audit'],
            start_semantics='geometry reference only; runtime starts from reset and runs every physical phase continuously'))
        mujoco.mj_saveModel(self.scene.model,str(output/'scene.mjb'),None)
        (output/'scene.xml').write_text(self.scene.spec.to_xml(),encoding='utf-8')

    def ordered(self,prefix,reverse=False):
        names=[n for n in self.rows if n.startswith(prefix) and n[len(prefix):].isdigit()]
        return sorted(names,key=lambda n:int(n[len(prefix):]),reverse=reverse)

    def sample(self,controller):
        if self.button_policy is not None:
            violations=self.button_policy.sample(controller)
            if violations:
                controller.stop_evidence=violations;controller.hold('BUTTON_CONTACT_POLICY')
        super().sample(controller)

    def physical_button_start(self):
        self.wait(.6)
        if self.controller.devices.heating=='burger_container':return True
        row=next(r for r in self.continuation['rows'] if r['stage']=='button_press')
        oven=self.scene.model.body('microwave').id
        normal=self.scene.data.xmat[oven].reshape(3,3)[:,0]
        target=np.asarray(row['target_m'])+normal*.0012
        ik=solve_ik(self.scene,'right',target,np.asarray(row['rotation']),max_iterations=400,
            position_tolerance=.0003,rotation_tolerance=.005)
        if not ik.success:raise DoorFailure('BUTTON_PRELOAD_IK_FAILED')
        values=self.controller.reference.copy();values[8:15]=ik.q
        contract=dict(arm='right',target='microwave_start_geom',phase='acquire',
            payload_ids=list(self.payload),supports={'burger_container':['microwave_bottom'],'burger':['burger_plate']})
        policy=ContactExecution(self.scene,self.controller)
        preview=policy.preview([values.tolist()]*8,contract)
        self.cycle_evidence['button_preload']=dict(target_m=target.tolist(),tcp_additional_preload_m=.0012,
            preflight=preview,scope='existing discrete contact policy; not a zero-compliance geometric certificate')
        self.writer.write(dict(type='button_preload_preflight',**self.cycle_evidence['button_preload']))
        if not preview['pass']:raise DoorFailure('BUTTON_PRELOAD_PREFLIGHT_'+preview['status'])
        policy.activate(contract);self.button_policy=policy
        try:
            self.move_q('button_force_preload',ik.q);self.wait(.8)
            return self.controller.devices.heating=='burger_container'
        finally:
            self.button_policy=None;policy.clear()

    def complete_cycle(self,checks):
        # The successful measured placement is a reference, not a restored state.
        reference=np.asarray(self.continuation['start_qpos'])
        actual=self.scene.data.qpos
        error=float(np.max(np.abs(actual-reference)))
        self.cycle_evidence['placement_reference_max_qpos_difference']=error
        if error>.002:raise DoorFailure('CONTINUATION_START_STATE_CHANGED')
        self.release_support=None
        self.move('closing_above');self.move('closing_grasp');self.gripper(.04)
        checks['closing_regrasp_bilateral_50ms']=self.bilateral_seconds>=.05
        if not checks['closing_regrasp_bilateral_50ms']:raise DoorFailure('CLOSING_REGRASP_FAILED')
        self.require_grip=True
        for name in self.ordered('closing_',True):self.move(name)
        self.wait(.5)
        checks['door_physically_closed_under3deg']=abs(self.scene.data.qpos[self.joint])<np.deg2rad(3)
        if not checks['door_physically_closed_under3deg']:raise DoorFailure('CLOSING_NOT_REACHED')
        self.require_grip=False;self.gripper(0.);self.move('closed_withdraw');self.move('closed_home')
        self.controller.contact_guard.allow_device_contact('microwave_start_geom')
        self.gripper((.083444-.010)/2)
        self.move('button_pre');self.move('button_touch');self.move('button_press')
        checks['physical_button_started_heating']=self.physical_button_start()
        if not checks['physical_button_started_heating']:raise DoorFailure('PHYSICAL_BUTTON_START_FAILED')
        self.mark('physical_heat_started')
        self.move('button_retreat');self.gripper(0.);self.move('heated_home');self.wait(5.5)
        checks['explicit_state_heating_completed']='burger_container' in self.controller.devices.heated
        if not checks['explicit_state_heating_completed']:raise DoorFailure('STATE_HEATING_NOT_COMPLETED')
        self.move('reopen_pre');self.move('reopen_grasp');self.gripper(.04)
        checks['reopen_bilateral_50ms']=self.bilateral_seconds>=.05
        if not checks['reopen_bilateral_50ms']:raise DoorFailure('REOPEN_GRASP_FAILED')
        self.require_grip=True
        for name in self.ordered('reopening_'):self.move(name)
        self.wait(.5)
        checks['reopened_physically_over70deg']=self.scene.data.qpos[self.joint]>=np.deg2rad(70)
        if not checks['reopened_physically_over70deg']:raise DoorFailure('REOPEN_ANGLE_FAILED')
        self.require_grip=False;self.gripper(0.);self.move('reopened_above');self.move('reopened_home')
        for name in ('retrieve_pre','retrieve_front','retrieve_lip','retrieve_grasp'):self.move(name)
        self.gripper(.05)
        checks['retrieval_bilateral_50ms']=self.grasp_age>=.05
        if not checks['retrieval_bilateral_50ms']:raise DoorFailure('RETRIEVAL_GRASP_FAILED')
        self.anchor,self.rotation_anchor=self.relative_container()
        self.initial_z=float(self.scene.data.xpos[self.scene.objects['burger_container'],2])
        self.hold_age=self.peak_hold=0.;self.loss_age=0.;self.meal_required=True
        self.move('retrieve_lift');self.wait(1.2)
        checks['retrieval_lift30mm_hold1s']=self.peak_hold>=1.
        if not checks['retrieval_lift30mm_hold1s']:raise DoorFailure('RETRIEVAL_LOAD_HOLD_FAILED')
        for name in ('retrieve_lip_out','retrieve_front_out','retrieve_outside'):self.move(name)
        self.wait(1.5)
        body=self.scene.objects['burger_container'];oven=self.scene.model.body('microwave').id
        local=self.scene.data.xmat[oven].reshape(3,3).T@(self.scene.data.xpos[body]-self.scene.data.xpos[oven])
        bounds=[object_bounds(self.scene,name,frame_body='microwave') for name in self.payload]
        checks['whole_payload_retrieved_outside_cavity']=all(high[0]<-.22 for low,high in bounds)
        checks['retrieved_safe_hold_bilateral_1s']=self.hold_age>=1. and self.grasp_age>=1.
        if not checks['whole_payload_retrieved_outside_cavity']:raise DoorFailure('PAYLOAD_STILL_INSIDE_CAVITY')
        if not checks['retrieved_safe_hold_bilateral_1s']:raise DoorFailure('FINAL_SAFE_HOLD_FAILED')
        self.cycle_evidence.update(retrieval_held_seconds=self.peak_hold,
            final_safe_hold_s=self.hold_age,final_payload_local_bounds=[(low.tolist(),high.tolist()) for low,high in bounds],
            final_container_local_m=local.tolist(),final_pose='held outside; finite return/release requires separate route',
            heat_model='explicit 5s device state proxy, not thermal physics',device_events=self.controller.devices.events)
        self.meal_required=False
        self.mark('retrieved_outside')


def main():
    p=argparse.ArgumentParser();p.add_argument('--opening',type=Path,required=True);p.add_argument('--continuation',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    witness=MicrowaveCycle(json.loads(a.opening.read_text()),json.loads(a.continuation.read_text()),a.output)
    witness.writer.write(dict(type='finite_inputs',opening_sha256=hashlib.sha256(a.opening.read_bytes()).hexdigest(),
        continuation_sha256=hashlib.sha256(a.continuation.read_bytes()).hexdigest()))
    result=witness.run()
    print(json.dumps({k:result[k] for k in ('status','reason','checks','physics_seconds')}))
    return 0 if result['status']=='COMPLETE_MICROWAVE_CYCLE_PASS' else 1


if __name__=='__main__':raise SystemExit(main())
