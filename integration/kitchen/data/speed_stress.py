"""Offline, same-initial-state speed experiments on immutable planned actions.

All ordinary action phases, jaws and pauses are retimed. Only measured device
timer windows and task-required final holds retain physical time. No IK/planner
is called; relaxed limits apply to one private controller, never global defaults.
"""
import argparse
import hashlib
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path
import time
from types import MethodType
import numpy as np
from robot_arm.kitchen.control import DualArmController,tcp_pose
from .retiming_experiment import load_backup,restored_platform,outcome_snapshot
from .files import digest,replace_json


def relax(controller,scale):
    if not 1<=scale<=4:raise ValueError('Limit scale must be in [1,4]')
    controller.velocity_limit=DualArmController.velocity_limit*scale
    controller.tcp_command_velocity=DualArmController.tcp_command_velocity*scale
    controller.tcp_velocity_limit=DualArmController.tcp_velocity_limit*scale
    def prepare(self,values,reference=None):
        previous=self.reference.copy() if reference is None else np.asarray(reference).copy()
        goal=np.asarray(values,dtype=float).copy();goal[[7,15]]=previous[[7,15]]
        accepted=DualArmController.prepare_joint_targets(self,goal,reference=previous)
        accepted[[7,15]]=previous[[7,15]]+np.clip(
            np.clip(np.asarray(values)[[7,15]],0,.05)-previous[[7,15]],-.002*scale,.002*scale)
        return accepted
    controller.prepare_joint_targets=MethodType(prepare,controller)
    return {'joint_command_rad_s':controller.velocity_limit,'tcp_command_m_s':controller.tcp_command_velocity,
        'jaw_tendon_m_per_frame':.002*scale,'measured_tcp_m_s':controller.tcp_velocity_limit,
        'ordinary_force_n':15*scale,'intentional_force_n':40*scale,'ordinary_penetration_m':.003*scale,
        'finger_soft_limit_tolerance_m':.0005*scale,'jaw_coupling_residual_m':.0005*scale,
        'physics_hz':500,'control_hz':25,'actuator_torque_limits':'original model, unchanged'}


def schedule(data,context,factor,*,segment_factors=None,windows=None,clock=None):
    if not 1<=factor<=4:raise ValueError('Speed factor must be in [1,4]')
    segment_factors=segment_factors or {}
    if any(not 1<=v<=4 for v in segment_factors.values()):raise ValueError('Local speed caps must be in [1,4]')
    n=len(data['actions/tick']);start=data['start_tick'];protected=np.zeros(n,dtype=bool)
    windows=windows or []
    for window in windows:
        if not (start<=window['start_tick']<window['end_tick']<=data['end_tick'] and
                window['start_tick']%20==0 and window['end_tick']%20==0 and 1<=window['factor']<=4):
            raise ValueError('Invalid local timing window')
    def frame(tick):return int((tick-start)//20)
    # Preserve physical elapsed time which actually produced wash/heat markers.
    flags=data['states/task_flags'].astype(bool)
    for column,seconds in ((0,2),(1,5)):
        for index in np.flatnonzero(np.any(flags[1:,:,column]&~flags[:-1,:,column],axis=1))+1:
            end=frame(data['states/tick'][index]);protected[max(0,end-round(seconds*25)):end]=True
    for event in context['events']:
        if event['stage']=='skill_qualified':
            seconds=.5 if event['skill'] in ('R1','R3','R4') else 2.
            end=frame(event['tick']);protected[max(0,end-round(seconds*25)):end]=True
    cuts={0,n}
    cuts.update(frame(e['tick']) for e in context['events']+context['changes'])
    cuts.update((np.flatnonzero(protected[1:]!=protected[:-1])+1).tolist())
    cuts.update(frame(w[k]) for w in windows for k in ('start_tick','end_tick'))
    original=np.empty((n,16));original[:,[0,1,2,3,4,5,6,8,9,10,11,12,13,14]]=data['actions/joint_pos']
    original[:,[7,15]]=data['replay/gripper_target']
    extended=np.vstack([data['controller']['reference'],original]);out=[];mapping={0:0}
    modes=[(frame(e['tick']),e['args']) for e in context['changes'] if e['method']=='mode']
    segment_frames={}
    for a,b in zip(sorted(cuts),sorted(cuts)[1:]):
        mode=next((args for tick,args in reversed(modes) if tick<=a),{})
        target=mode.get('contact_contract',{}).get('target')
        local_factor=min(factor,segment_factors.get(target,factor))
        for window in windows:
            if frame(window['start_tick'])<=a<frame(window['end_tick']):local_factor=min(local_factor,window['factor'])
        count=b-a if protected[a] else math.ceil((b-a)/local_factor)
        if target in segment_factors:segment_frames[target]=segment_frames.get(target,0)+count
        times=np.linspace(a,b,count+1)[1:];indices=np.floor(times).astype(int).clip(max=n-1)
        u=times-indices
        values=extended[indices]+(extended[indices+1]-extended[indices])*u[:,None]
        values[-1]=extended[b];out.extend(values);mapping[b]=len(out)
        if clock is not None:clock.extend((start+20*times).tolist())
    events=[{**e,'source_tick':e['tick'],'tick':start+20*mapping[frame(e['tick'])]} for e in context['events']]
    events.extend({'stage':'action_mode','args':e['args'],'source_tick':e['tick'],
        'tick':start+20*mapping[frame(e['tick'])]} for e in context['changes'] if e['method']=='mode')
    events.sort(key=lambda e:e['tick'])
    return np.asarray(out),events,{'source_frames':n,'candidate_frames':len(out),
        'local_factors':segment_factors,'local_output_frames':segment_frames,
        'local_windows':windows,
        'protected_physical_timer_frames':int(protected.sum()),'time_compressed_source_frames':int((~protected).sum())}


class Audit:
    def __init__(self,p,limits):
        import mujoco
        self.mj=mujoco;self.p=p;self.limits=limits;self.peak_force=0.;self.peak_depth=0.;self.peak_tcp=0.;self.peak_joint=0.
        self.jp=np.zeros((3,p.scene.model.nv));self.jr=np.zeros_like(self.jp)
    def sample(self):
        p=self.p;m,d=p.scene.model,p.scene.data;g=p.controller.contact_guard
        for side in ('left','right'):
            a=p.scene.arms[side];point=tcp_pose(d,a)[0]
            self.mj.mj_jac(m,d,self.jp,self.jr,point,a.tcp_body)
            self.peak_tcp=max(self.peak_tcp,float(np.linalg.norm(self.jp@d.qvel)))
            self.peak_joint=max(self.peak_joint,float(np.abs(d.qvel[list(a.dofs)]).max()))
        for i in range(d.ncon):
            c=d.contact[i];a,b=int(c.geom1),int(c.geom2)
            if not ({a,b}&g.robot):continue
            if a in g.bases and b not in g.robot or b in g.bases and a not in g.robot:continue
            intentional=(a in g.pads and b in g.manipulable|g.device_contacts or b in g.pads and a in g.manipulable|g.device_contacts)
            f=np.zeros(6);self.mj.mj_contactForce(m,d,i,f)
            if not np.isfinite(f).all():raise RuntimeError('NONFINITE_CONTACT')
            self.peak_force=max(self.peak_force,float(f[0]))
            if not intentional:self.peak_depth=max(self.peak_depth,max(0.,-float(c.dist)))
        # Existing guard also checks mechanical coupling, joint ranges and NaNs.
        for violation in g.inspect():
            if 'normal_force_n' in violation:
                threshold=self.limits['intentional_force_n'] if violation['limit_n']==40 else self.limits['ordinary_force_n']
                if violation['normal_force_n']<=threshold:continue
                violation={**violation,'original_limit_n':violation['limit_n'],'limit_n':threshold}
            elif violation.get('kind')=='tcp_speed' and violation['speed_m_s']<=self.limits['measured_tcp_m_s']:continue
            elif violation.get('kind')=='finger_joint_limit':
                low,high=m.joint(violation['joint']).range;tol=self.limits['finger_soft_limit_tolerance_m']
                if np.isfinite(violation['q_m']) and low-tol<=violation['q_m']<=high+tol:continue
            elif violation.get('kind')=='jaw_transmission':
                state=violation['constraint']
                if state['active'] and abs(state['residual_m'])<=self.limits['jaw_coupling_residual_m']:continue
            raise RuntimeError('RELAXED_AUDIT: '+json.dumps(violation))
        if self.peak_depth>self.limits['ordinary_penetration_m']:raise RuntimeError('ORDINARY_PENETRATION: '+str(self.peak_depth))
    def report(self):return {'peak_robot_contact_force_n':self.peak_force,'peak_ordinary_penetration_m':self.peak_depth,
        'peak_tcp_m_s':self.peak_tcp,'peak_joint_rad_s':self.peak_joint,'sampling_hz':25}


def trial(backup,output,factor,scale,*,record=False,segment_factors=None,windows=None,timeout=900,wash_gate=False):
    from integration.kitchen.validation.quality import ExpertQuality
    from integration.kitchen.validation.success import validate_initial,validate_skill,validate_final
    from robot_arm.kitchen.state_recording import StateRecorder
    from .replay import verify
    data,context,report=load_backup(backup);output=Path(output);output.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter();p=None;audit=None;quality=None;recording=None
    button={'max_displacement_m':0.,'max_valid_contact_seconds':0.,'max_normal_force_n':0.};stage_evidence=[]
    grasp_losses=[];current_mode={};current_source_tick=data['start_tick'];carry_start=data['start_tick']
    tray_peak=0.;tray_peak_tick=None
    result={'schema':'kitchen_relaxed_speed_trial_v1','case_id':report['case']['case_id'],'case':report['case'],
        'seed':report['seed'],'factor':factor,'status':'FAILED_ATTEMPT','task_success':False,
        'source':str(Path(backup).resolve()),'source_sha256':digest(Path(backup)/'trajectory.h5'),'images_included':False}
    result['experiment_source_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        p=restored_platform(data,output/'data' if record else None,report['initialization_profile'],report['seed'])
        result['same_initial_state']=True;result['limits']=relax(p.controller,scale)
        for name in ('faucet_handle_geom','microwave_door_handle','microwave_start_geom'):p.controller.contact_guard.allow_device_contact(name)
        clock=[]
        commands,events,stats=schedule(data,context,factor,segment_factors=segment_factors,windows=windows,clock=clock);result['retiming']=stats
        result['planned_seconds']=len(commands)/25
        if not record:np.savez_compressed(output/'actions.npz',targets=commands)
        quality=ExpertQuality(p);validate_initial(p.controller,report['case']);audit=Audit(p,result['limits'])
        if record:
            p.state_recorder=recording=StateRecorder(output/'data',p)
            header=json.loads(recording.file.attrs['metadata_json'])
            header['offline_speed_experiment']={'limits':result['limits'],'factor':factor,
                'local_factors':segment_factors or {},'local_windows':windows or [],
                'wash_completion_gate':wash_gate,
                'source_sha256':result['experiment_source_sha256']}
            recording.file.attrs['metadata_json']=json.dumps(header,separators=(',',':'))
        by_tick={};actual=[];extra=0;limited=0;heating_wait=0
        wash_wait=0;pending_wash=[]
        if wash_gate:
            flags=data['states/task_flags'].astype(bool)
            for index,obj in enumerate(data['metadata']['object_ids']):
                for row in np.flatnonzero(flags[1:,index,0]&~flags[:-1,index,0])+1:
                    pending_wash.append((int(data['states/tick'][row]),obj))
        phase=''
        for event in events:by_tick.setdefault(event['tick'],[]).append(event)
        def step(goal):
            nonlocal limited,tray_peak,tray_peak_tick
            if time.perf_counter()-started>timeout:raise TimeoutError('Offline trial wall budget exceeded')
            accepted=p.controller.accept_joint_targets(goal)
            if np.max(np.abs(accepted-goal))>1e-8:limited+=1
            p.advance(20,detail='execution');audit.sample()
            if quality.tray_tilt_peak>tray_peak:
                tray_peak=quality.tray_tilt_peak;tray_peak_tick=current_source_tick
            contract=current_mode.get('contact_contract',{})
            target=contract.get('target')
            if contract.get('phase')=='carry' and target in p.scene.objects:
                # Diagnose the first actual loss in each source carry interval;
                # legitimate support transfer to the tray is excluded.
                dev=p.controller.devices
                supported=dev.supports(target,['tray_bottom','microwave_bottom','user_table'])
                if not dev.touching_pads(target) and not supported and not any(x['carry_start_tick']==carry_start for x in grasp_losses):
                    grasp_losses.append({'target':target,'carry_start_tick':carry_start,
                        'source_tick':current_source_tick,'actual_tick':p.controller.tick,
                        'position_world_m':p.scene.data.xpos[p.scene.objects[target]].tolist()})
            if phase=='R4':
                import mujoco
                dev=p.controller.devices;geom=dev.geom('microwave_start_geom');force=0.
                for contact_index in range(p.scene.data.ncon):
                    contact=p.scene.data.contact[contact_index];g1,g2=int(contact.geom1),int(contact.geom2)
                    if g1==geom and g2 in dev.pads or g2==geom and g1 in dev.pads:
                        f=np.zeros(6);mujoco.mj_contactForce(p.scene.model,p.scene.data,contact_index,f);force+=max(0.,f[0])
                button['max_displacement_m']=max(button['max_displacement_m'],dev.angle('microwave_start_slide'))
                button['max_valid_contact_seconds']=max(button['max_valid_contact_seconds'],dev.button_seconds)
                button['max_normal_force_n']=max(button['max_normal_force_n'],force)
            if p.controller.stopped:raise RuntimeError(p.controller.stop_reason)
            if not np.isfinite(p.scene.data.qpos).all() or not np.isfinite(p.scene.data.qvel).all():raise RuntimeError('NONFINITE_PHYSICS')
        def check(function):
            nonlocal extra
            for attempt in range(76):
                try:function();return
                except (ValueError,RuntimeError) as error:
                    if attempt==75 or not str(error).startswith('EXPERT_QUALITY_'):raise
                    step(p.controller.reference.copy());extra+=1
        def transition(tick):
            nonlocal heating_wait,phase,current_mode,carry_start
            for e in by_tick.get(tick,[]):
                if e['stage']=='action_mode':
                    current_mode=e['args'];carry_start=e['source_tick']
                if e['stage']=='action_mode' and e['args'].get('contact_contract',{}).get('target')=='microwave_door_handle':
                    dev=p.controller.devices;meal=dev.heating
                    if meal is not None:
                        for _ in range(150):
                            if meal in dev.heated:break
                            if dev.heating!=meal:raise RuntimeError('HEATING_ABORTED')
                            step(p.controller.reference.copy());heating_wait+=1
                        else:raise RuntimeError('HEATING_WAIT_BUDGET')
                if e['stage']=='skill_started':quality.start(e['skill']);phase=e['skill']
                if e['stage'] in ('action_mode','skill_started','skill_qualified'):
                    dev=p.controller.devices
                    stage_evidence.append({'source_tick':e['source_tick'],'actual_tick':p.controller.tick,
                        'stage':e['stage'],'target':e.get('args',{}).get('contact_contract',{}).get('target'),
                        'door_degrees':float(np.degrees(dev.angle('microwave_hinge'))),
                        'meal_ready':dev.meal_ready('hotdog_container') if report['case'].get('meal')=='hotdog' else None,
                        'heating':dev.heating,'heat_seconds':dev.heat_seconds})
                if e['stage']=='skill_qualified':check(lambda:validate_skill(quality,e['skill'],report['case']))
                actual.append({**e,'tick':p.controller.tick})
        for index,goal in enumerate(commands):
            current_source_tick=clock[index]
            before=data['start_tick'] if index==0 else clock[index-1]
            for tick,obj in list(pending_wash):
                if before<tick:continue
                for _ in range(76):
                    if obj in p.controller.devices.cleaned:break
                    step(p.controller.reference.copy());wash_wait+=1
                else:raise RuntimeError('WASH_COMPLETION_GATE_FAILED')
                pending_wash.remove((tick,obj))
            transition(data['start_tick']+index*20);step(goal)
        transition(data['start_tick']+len(commands)*20);check(lambda:validate_final(quality,report['case']))
        if factor==1:
            error=max(float(np.abs(p.scene.data.qpos-data['states/qpos'][-1]).max()),float(np.abs(p.scene.data.qvel-data['states/qvel'][-1]).max()))
            result['one_x_final_state_error']=error
            if error>1e-8:raise RuntimeError('BASELINE_RECONSTRUCTION_DIFFERS')
        result.update(task_success=True,status='CANDIDATE_TASK_PASS')
        if record:
            recording.annotate(report['case'],actual);recording.close(p,'EXPERIMENT_END')
            path=output/'trajectory.h5';recording.path.replace(path)
            verification=verify(path);result['verification']=verification
            if verification['status']!='CONTROL_RESIMULATION_PASS' or not verification['task_success']:raise RuntimeError('INDEPENDENT_REPLAY_FAILED')
            result.update(status='QUALIFIED_EXPERIMENT',trajectory=str(path.resolve()))
        result['extra_stability_seconds']=extra/25;result['extra_heating_seconds']=heating_wait/25;result['rate_limited_frames']=limited
        result['extra_wash_seconds']=wash_wait/25
    except Exception as error:
        result.update(task_success=False,status='FAILED_ATTEMPT',error=repr(error))
        if p is not None and quality is not None:result['outcome']=outcome_snapshot(p,report['case'],quality)
    finally:
        if p is not None:
            result['actual_seconds']=(p.controller.tick-data['start_tick'])/500
            result['device_state']=p.controller.devices.snapshot()
            result['device_events']=p.controller.devices.events
            result['button_evidence']=button if quality is not None else None
            result['stage_evidence']=stage_evidence if quality is not None else []
            result['grasp_losses']=grasp_losses
            result['failure_source_tick']=current_source_tick if not result['task_success'] else None
            result['tray_peak_source_tick']=tray_peak_tick
            result['observed_speedup']=report['simulated_seconds']/max(result['actual_seconds'],.04) if result['task_success'] else None
            if audit:result['audit']=audit.report()
            p.close()
        result['wall_seconds']=time.perf_counter()-started;replace_json(output/'report.json',result)
    return result


def case_job(job):
    backup,output,factors,scale,segment_factors=job;original={f.name:digest(f) for f in Path(backup).iterdir() if f.is_file()};rows=[]
    for factor in factors:
        row=trial(backup,Path(output)/f'trial_{factor:g}x',factor,scale,segment_factors=segment_factors);rows.append(row)
        print(json.dumps({k:row.get(k) for k in ('case_id','factor','status','actual_seconds','observed_speedup','error')}),flush=True)
        if factor==1 and not row['task_success']:break
    winners=sorted((r for r in rows if r['task_success'] and r['factor']>1),key=lambda r:r['actual_seconds'])
    selected=None
    for winner in winners:
        record=trial(backup,Path(output)/f'recorded_{winner["factor"]:g}x',winner['factor'],scale,record=True,segment_factors=segment_factors)
        if record['task_success']:selected=record;break
    if original!={f.name:digest(f) for f in Path(backup).iterdir() if f.is_file()}:raise RuntimeError('SOURCE_FILES_CHANGED')
    return {'source':str(backup),'trials':rows,'selected':selected,'source_files_unchanged':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-root',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case-ids',nargs='+',required=True);parser.add_argument('--factors',nargs='+',type=float,default=[1,2,3,4])
    parser.add_argument('--limit-scale',type=float,default=4);parser.add_argument('--jobs',type=int,default=2)
    parser.add_argument('--segment-factor',action='append',default=[],metavar='TARGET=CAP',help='Local speed cap for a named contact target; repeatable')
    args=parser.parse_args()
    segment_factors={}
    try:
        for item in args.segment_factor:
            target,value=item.split('=',1)
            if not target or target in segment_factors:raise ValueError('Empty or duplicate target')
            segment_factors[target]=float(value)
            if not 1<=segment_factors[target]<=4:raise ValueError('Cap outside [1,4]')
    except ValueError as error:parser.error(str(error))
    if (not 1<=args.limit_scale<=4 or args.factors[0]!=1 or len(set(args.factors))!=len(args.factors)
        or any(not 1<=f<=4 for f in args.factors) or not 1<=args.jobs<=4):parser.error('Invalid bounded experiment')
    sources={}
    for path in sorted(args.backup_root.glob('attempts/**/backup/report.json')):
        report=json.loads(path.read_text());key=report['case']['case_id']
        if key in args.case_ids and report['status']=='QUALIFIED_EXPERT' and key not in sources:sources[key]=path.parent
    if set(sources)!=set(args.case_ids):raise ValueError('Missing qualified planned source')
    args.output.mkdir(parents=True,exist_ok=False);jobs=[]
    for key in args.case_ids:jobs.append((sources[key],args.output/key.replace(':','_'),args.factors,args.limit_scale,segment_factors))
    replace_json(args.output/'plan.json',{'factors':args.factors,'limit_scale':args.limit_scale,
        'local_factors':segment_factors,
        'sources':{k:str(v.resolve()) for k,v in sources.items()},'scope':'Offline action retiming only; same model and initial states'})
    rows=[]
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for result in pool.map(case_job,jobs):
            rows.append(result);replace_json(args.output/'summary.json',rows)


if __name__=='__main__':main()
