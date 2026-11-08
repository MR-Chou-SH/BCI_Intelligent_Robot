"""Opt-in backup -> action-only retiming -> physical trial -> qualified recording.

No collection defaults change. Trials write actions and small reports, no H5.
The original expert H5/report/annotations remain under backup/. A selected
successful trial is executed again for recording and independently verified.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from integration.kitchen.runtime.client import LocalPlannerClient
from integration.kitchen.planning.action_retiming import retime,ARM_COLUMNS,MAX_FACTOR
from .collect_experts import one_case,save,source_identity,identity_sha256
from .files import digest,read
from .replay import read_compact,restore,verify


class ContextClient(LocalPlannerClient):
    """Sparse phase permissions and release events, never contact point logs."""
    def __init__(self,platform):
        super().__init__(platform);self.active=False;self.changes=[];self.last_mode=None

    def call(self,method,args=None,*,mutating=False):
        tick=int(self.platform.controller.tick)
        result=super().call(method,args,mutating=mutating)
        if not result.get('ok'):return result
        if method=='start_state_recording':self.active=True
        elif method=='stop_state_recording':self.active=False
        elif self.active and method=='execute_trajectory':
            args=args or {}
            mode={k:copy.deepcopy(args[k]) for k in ('contact_contract','guarded_gripper','contact_targets') if k in args}
            if mode!=self.last_mode:
                self.changes.append({'tick':tick,'method':'mode','args':mode});self.last_mode=mode
        elif self.active and method=='end_contact':
            self.changes.append({'tick':tick,'method':method,'args':{}});self.last_mode=None
        elif self.active and method in ('hold','cancel'):
            self.changes.append({'tick':tick,'method':method,'args':{}})
        return result


def load_backup(directory):
    directory=Path(directory);report=read(directory/'report.json')
    if report.get('status')!='QUALIFIED_EXPERT' or not report.get('task_success'):
        raise ValueError('Backup is not qualified')
    path=directory/'trajectory.h5'
    if digest(path)!=report['verification']['sha256']:raise ValueError('Backup trajectory hash mismatch')
    context_path=directory/'action_context.json'
    if digest(context_path)!=report.get('action_context_sha256'):raise ValueError('Sparse context hash mismatch')
    context=read(context_path);data=read_compact(path)
    if context['trajectory_sha256']!=report['verification']['sha256']:raise ValueError('Context belongs to another trajectory')
    if (data['schema']!='kitchen_state_actions_v3' or data['start_tick']%20
        or data['end_tick']%20 or not np.array_equal(data['actions/tick'],np.arange(data['start_tick'],data['end_tick'],20))
        or np.any(data['actions/interpolation_reset'])):
        raise ValueError('Pilot requires uniform boundary actions without mid-frame resets')
    return data,context,report


def restored_platform(data,root,profile,seed):
    """Restore ONCE, before candidate execution; never correct trial states."""
    import mujoco
    from robot_arm.kitchen.platform import KitchenPlatform
    from robot_arm.kitchen.scene_identity import model_identity
    p=KitchenPlatform(layout=data['metadata']['layout'],seed=seed,
        initialization_profile=profile,data_root=root,record_physics=False)
    try:
        if model_identity(p.scene,builder_sha256=p.scene.builder_sha256)!=data['metadata']['model_identity']:
            raise ValueError('Backup model mismatch')
        if data['metadata']['software']['mujoco']!=mujoco.__version__:raise ValueError('Backup MuJoCo mismatch')
        from robot_arm.kitchen.registry import control_identity
        sources=control_identity()['configuration']['sources']
        recorded=data['metadata']['control_identity']['configuration']['sources']
        if any(recorded.get(n)!=sources[n] for n in ('control.py','devices.py','action_codec.py')):
            raise ValueError('Backup controller semantics mismatch')
        restore(p.scene,{'integration_state':data['initial/integration_state'],'state_spec':data['state_spec']})
        c=p.controller;s=data['controller']
        for name in ('previous','accepted','reference'):setattr(c,name,np.array(s[name],dtype=float))
        c.tick=int(s['tick']);c.frame_step=int(s['frame_step']);c.stopped=bool(s['stopped'])
        c.devices.restore(data['devices']);p.scene.data.qfrc_bias[:]=data['initial/qfrc_bias']
        if c.stopped:raise ValueError('Backup starts with stopped controller')
        # Exact full-physics-state equality, not merely matching object poses.
        state=np.empty(mujoco.mj_stateSize(p.scene.model,data['state_spec']))
        mujoco.mj_getState(p.scene.model,p.scene.data,state,data['state_spec'])
        if not np.array_equal(state,data['initial/integration_state']):raise ValueError('Initial state restoration differs')
        return p
    except BaseException:
        p.close();raise


def build_candidate(data,context,factor,controller,*,continuous=False,platform=None):
    start=data['start_tick'];n=len(data['actions/tick'])
    def frame(tick):
        if (tick-start)%20 or not start<=tick<=data['end_tick']:raise ValueError('Non-boundary sparse event')
        return (tick-start)//20
    changes=[e for e in context['changes'] if e['tick']<data['end_tick']]
    # Modes at the same tick apply in API order; release is retained separately.
    mode_by_frame={}
    for e in changes:
        if e['method']=='mode':mode_by_frame[frame(e['tick'])]=e['args']
        elif e['method']!='end_contact':raise ValueError('Unsupported mid-episode hold/cancel')
    modes=sorted(mode_by_frame.items())
    events=context['events']
    cuts={frame(e['tick']) for e in changes+events}|{0,n}
    commands=np.empty((n,16));commands[:,ARM_COLUMNS]=data['actions/joint_pos']
    commands[:,[7,15]]=data['replay/gripper_target']
    smoothing={}
    pause_clear=None
    if continuous and factor!=1.:
        if platform is None:raise ValueError('Smoothing requires scratch platform geometry')
        from integration.kitchen.planning.action_smoothing import blend_source,carry_pause_clearance
        commands,smoothing=blend_source(commands,data,cuts,modes,platform)
        pause_clear=carry_pause_clearance(data,platform)
    output,mapping,stats=retime(commands,data['controller']['reference'],cuts,modes,factor,
        lambda goal,previous:controller.prepare_joint_targets(goal,reference=previous),
        continuous=continuous and factor!=1.,carry_pause_clear=pause_clear)
    stats.update(smoothing);stats['continuous']=continuous and factor!=1.
    from robot_arm.kitchen.limits import joint_control_limits
    normalized=output.copy()
    for offset,side in ((0,'left'),(8,'right')):
        lower,upper=joint_control_limits(controller.scene.model,controller.scene.arms[side]) if hasattr(controller,'scene') else (None,None)
        if lower is not None:normalized[:,offset:offset+7]=np.clip(output[:,offset:offset+7],lower,upper)
    normalized[:,[7,15]]=np.clip(normalized[:,[7,15]],0.,.05)
    if np.max(np.abs(normalized-output))>1e-9:raise ValueError('Candidate target outside original joint bounds')
    stats['boundary_roundoff_values']=int(np.count_nonzero(normalized!=output));output=normalized
    def remap(e):return {**copy.deepcopy(e),'tick':start+20*mapping[frame(e['tick'])]}
    return {'commands':output,'changes':[remap(e) for e in changes],
            'events':[remap(e) for e in events],'stats':stats,'factor':factor}


def run_candidate(data,context,backup_report,plan,directory,*,record=False,timeout=900):
    """Run through the existing platform API, including release/support gates."""
    from integration.kitchen.validation.quality import ExpertQuality
    from integration.kitchen.validation.success import validate_initial,validate_skill,validate_final
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter();deadline=time.monotonic()+timeout;p=None
    result={'status':'FAILED_ATTEMPT','task_success':False,'factor':plan['factor'],
        'case':backup_report['case'],'seed':backup_report['seed'],'images_included':False,
        'source_identity_sha256':identity_sha256(source_identity()),
        'initialization_profile':backup_report['initialization_profile'],
        'motion_profile':'offline_retimed','backup_sha256':backup_report['verification']['sha256'],
        'stats':plan['stats'],'simulated_seconds':len(plan['commands'])/25}
    try:
        p=restored_platform(data,directory/'data',backup_report['initialization_profile'],backup_report['seed'])
        result['same_initial_state']=True
        client=LocalPlannerClient(p)
        def call(method,args=None):
            if time.monotonic()>deadline:raise TimeoutError('Candidate wall budget exceeded')
            reply=client.call(method,args,mutating=True)
            if not reply.get('ok'):raise RuntimeError(f'{method}: {reply.get("error")}')
            return reply['result']
        quality=ExpertQuality(p);validate_initial(p.controller,backup_report['case'])
        if record:recording=call('start_state_recording')
        actual_events=[];wait_frames={'arms_settle':0,'task_stability':0,'heating':0}
        def hold_frame(reason):
            # Real control/physics hold, never a state correction. Preserve the
            # currently active contact permission while waiting for a predicate.
            args={'waypoints':[p.controller.reference.tolist()],'source':'programmatic_validation'}
            feedback=p.contact.feedback()
            if feedback['active']:args['contact_contract']=feedback['contract']
            call('execute_trajectory',args)
            response=call('step',{'n_substeps':20,'detail':'execution'})
            if response['stopped']:raise RuntimeError('Candidate boundary hold stopped')
            wait_frames[reason]+=1
        def validate_with_settling(check):
            for attempt in range(51):
                try:check();return
                except (ValueError,RuntimeError) as error:
                    if attempt==50 or not str(error).startswith('EXPERT_QUALITY_'):raise
                    hold_frame('task_stability')
        def transition(tick):
            for e in events.get(tick,[]):
                if e['stage']=='skill_qualified':
                    validate_with_settling(lambda:validate_skill(quality,e['skill'],backup_report['case']))
                elif e['stage']=='skill_started':quality.start(e['skill'])
                actual_events.append({**e,'tick':int(p.controller.tick)})
        events={};changes={}
        for event in plan['events']:events.setdefault(event['tick'],[]).append(event)
        for event in plan['changes']:changes.setdefault(event['tick'],[]).append(event)
        start=data['start_tick'];end=start+len(plan['commands'])*20
        boundaries=sorted(set(events)|set(changes)|{start,end})
        current_mode=None
        for tick,next_tick in zip(boundaries,boundaries[1:]):
            transition(tick)
            for e in changes.get(tick,[]):
                if e['method']=='end_contact':call('end_contact')
                elif e['method']=='mode':current_mode=e['args']
            if current_mode is None:raise ValueError('No action context at start')
            if current_mode.get('guarded_gripper'):
                for _ in range(25):
                    if p._arms_settled():break
                    hold_frame('arms_settle')
                else:raise RuntimeError('Candidate arms did not settle before guarded gripper')
            contract=current_mode.get('contact_contract',{})
            if contract.get('target')=='microwave_door_handle' and p.controller.devices.heating is not None:
                meal=p.controller.devices.heating
                for _ in range(150):
                    if meal in p.controller.devices.heated:break
                    if p.controller.devices.heating!=meal:raise RuntimeError('Candidate heat aborted before door')
                    hold_frame('heating')
                else:raise RuntimeError('Candidate heat budget before door')
            a=(tick-start)//20;b=(next_tick-start)//20
            for offset in range(a,b,25):
                targets=plan['commands'][offset:min(offset+25,b)]
                call('execute_trajectory',{'waypoints':targets.tolist(),'source':'programmatic_validation',**current_mode})
                response=call('step',{'n_substeps':len(targets)*20,'detail':'execution'})
                if response['stopped'] or response['execution']['status']=='FAILED':
                    raise RuntimeError('Candidate stopped: '+str(response['stop_reason']))
        transition(end)
        validate_with_settling(lambda:validate_final(quality,backup_report['case']))
        # Any final residual hold belongs to the final skill's real interval.
        if actual_events and actual_events[-1]['stage']=='skill_qualified':actual_events[-1]['tick']=int(p.controller.tick)
        result['boundary_wait_seconds']={k:v/25 for k,v in wait_frames.items()}
        result['planned_simulated_seconds']=result['simulated_seconds']
        result['simulated_seconds']=(p.controller.tick-data['start_tick'])/500
        if plan['factor']==1.:
            errors={'qpos':float(np.max(np.abs(p.scene.data.qpos-data['states/qpos'][-1]))),
                    'qvel':float(np.max(np.abs(p.scene.data.qvel-data['states/qvel'][-1])))}
            result['one_x_final_errors']=errors
            if max(errors.values())>1e-8:raise ValueError('1x reconstruction differs from backup')
        result['task_success']=True;result['execution_wall_seconds']=time.perf_counter()-started
        if record:
            p.state_recorder.annotate(backup_report['case'],actual_events)
            call('hold');call('stop_state_recording')
            path=directory/'trajectory.h5';Path(recording['path']).replace(path)
            result['trajectory']=str(path.resolve())
            replay_started=time.perf_counter();verification=verify(path)
            result['replay_wall_seconds']=time.perf_counter()-replay_started
            result['verification']={k:verification[k] for k in ('status','sha256','task_success','maximum_qpos_error','maximum_qvel_error')}
            if verification['status']!='CONTROL_RESIMULATION_PASS' or verification['task_success'] is not True:
                raise ValueError('Optimized recording independent replay failed')
            from .annotations import build_annotations
            ap=directory/'annotations.json';save(ap,build_annotations(path))
            result.update(status='QUALIFIED_EXPERT',annotations=str(ap.resolve()),annotations_sha256=digest(ap))
            with __import__('h5py').File(path) as f:
                if not np.array_equal(f['initial/integration_state'][:],data['initial/integration_state']):
                    raise ValueError('Recorded initial state differs from backup')
        else:result['status']='CANDIDATE_TASK_PASS'
    except Exception as error:
        result.update(status='FAILED_ATTEMPT',task_success=False,error=repr(error))
        if p is not None:
            try:result['failure_outcome']=outcome_snapshot(p,backup_report['case'],quality)
            except Exception as detail_error:result['failure_outcome_error']=repr(detail_error)
    finally:
        if p:p.close()
        result['wall_seconds']=time.perf_counter()-started
        save(directory/'report.json',result)
    return result


def outcome_snapshot(platform,case,quality):
    """Small measured failure explanation; no per-frame contact/state dump."""
    import mujoco
    from robot_arm.kitchen.registry import object_bounds
    c=platform.controller;scene=platform.scene;objects={}
    names=[case['fruit']] if case.get('fruit') else []
    if case.get('meal'):names.append(case['meal']+'_container')
    for name in names:
        velocity=np.zeros(6)
        mujoco.mj_objectVelocity(scene.model,scene.data,mujoco.mjtObj.mjOBJ_BODY,scene.objects[name],velocity,0)
        low,high=object_bounds(scene,name,frame_body='wood_tray')
        objects[name]={'position_world_m':scene.data.xpos[scene.objects[name]].tolist(),
            'linear_speed_m_s':float(np.linalg.norm(velocity[3:])),
            'angular_speed_rad_s':float(np.linalg.norm(velocity[:3])),
            'supported_on_tray':bool(c.devices.supports(name,['tray_bottom'])),
            'touching_pads':bool(c.devices.touching_pads(name)),
            'footprint_in_tray':bool(np.all(low[:2]>=[-.233,-.153]) and np.all(high[:2]<=[.233,.153])),
            'bounds_tray_m':[low.tolist(),high.tolist()],'cleaned':name in c.devices.cleaned}
    return {'tick':c.tick,'objects':objects,'quality':quality.report()}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case-ids',nargs='+',default=['kitchen_expert_v3:R5:burger','kitchen_expert_v3:R5:hotdog'])
    parser.add_argument('--seeds',nargs='+',type=int,default=[0])
    parser.add_argument('--factors',nargs='+',type=float,default=[1.,1.25,1.5])
    parser.add_argument('--case-timeout',type=float,default=900)
    parser.add_argument('--backup-root',type=Path,help='Reuse immutable sparse-context backups from a previous experiment')
    parser.add_argument('--continuous',action='store_true',help='Arc-length timing and small clearance-checked corner blends')
    args=parser.parse_args(argv)
    from integration.kitchen.launch import run_path
    from integration.kitchen.tasks.catalog import cases
    known={c['case_id']:c for c in cases('all')}
    if (not args.case_ids or len(set(args.case_ids))!=len(args.case_ids) or set(args.case_ids)-set(known)
        or len(set(args.seeds))!=len(args.seeds) or not 1<=len(args.seeds)<=4
        or any(not 0<=s<2**32 for s in args.seeds) or len(set(args.factors))!=len(args.factors)
        or not 1<=len(args.factors)<=5 or args.factors[0]!=1. or any(not 1<=f<=MAX_FACTOR for f in args.factors)
        or not 30<=args.case_timeout<=3600):parser.error('Invalid bounded experiment matrix')
    output=run_path(args.output);output.mkdir(parents=True,exist_ok=False)
    identity=source_identity();save(output/'plan.json',{'schema':'kitchen_retiming_experiment_v1',
        'source_identity':identity,'case_ids':args.case_ids,'seeds':args.seeds,
        'factors':args.factors,'profile':'research','motion_profile':'faster','case_timeout':args.case_timeout,
        'backup_root':str(args.backup_root.resolve()) if args.backup_root else None,'continuous':args.continuous})
    rows=[]
    for case_id in args.case_ids:
        for seed in args.seeds:
            root=output/(case_id.replace(':','_')+f'_seed{seed}');root.mkdir()
            backup=(args.backup_root.resolve()/root.name/'backup') if args.backup_root else root/'backup'
            if args.backup_root:
                _,_,old_report=load_backup(backup)
                if old_report['case']!=known[case_id] or old_report['seed']!=seed:raise ValueError('Backup case/seed mismatch')
                row={'case_id':case_id,'seed':seed,'status':old_report['status'],
                     'report':str(backup/'report.json'),'report_sha256':digest(backup/'report.json'),'reused':True}
            else:
                row=one_case((known[case_id],seed,str(backup),'research',args.case_timeout,identity,False,'faster'),capture_context=True)
            result={'case_id':case_id,'seed':seed,'backup':row,'trials':[],'selected':None}
            if row['status']=='QUALIFIED_EXPERT':
                data,context,backup_report=load_backup(backup)
                backup_hashes={p.name:digest(p) for p in backup.iterdir() if p.is_file()}
                for i,factor in enumerate(args.factors):
                    if source_identity()!=identity:raise RuntimeError('Sources changed during experiment')
                    planning_started=time.perf_counter()
                    p=restored_platform(data,None,backup_report['initialization_profile'],seed)
                    try:plan=build_candidate(data,context,factor,p.controller,continuous=args.continuous,platform=p)
                    finally:p.close()
                    planning_wall=time.perf_counter()-planning_started
                    action_path=root/f'candidate_{i:02}.npz'
                    with action_path.open('xb') as stream:
                        np.savez_compressed(stream,joint_pos=plan['commands'][:,ARM_COLUMNS],
                            gripper_opening=1-plan['commands'][:,[7,15]]/.05,
                            gripper_target=plan['commands'][:,[7,15]])
                    save(root/f'candidate_{i:02}.json',{'factor':factor,'changes':plan['changes'],
                        'events':plan['events'],'stats':plan['stats'],'actions_sha256':digest(action_path),
                        'backup_sha256':backup_report['verification']['sha256'],'planning_wall_seconds':planning_wall})
                    trial=run_candidate(data,context,backup_report,plan,root/f'trial_{i:02}',timeout=args.case_timeout)
                    result['trials'].append(trial)
                    print(json.dumps({'case':case_id,'seed':seed,'factor':factor,'status':trial['status'],
                        'simulated_seconds':trial['simulated_seconds'],'error':trial.get('error')},ensure_ascii=False),flush=True)
                    # A 1x control run checks that capture/reconstruction itself
                    # works before drawing conclusions about faster dynamics.
                    if factor==1. and trial['status']!='CANDIDATE_TASK_PASS':break
                winners=sorted((i for i,r in enumerate(result['trials']) if r['status']=='CANDIDATE_TASK_PASS'
                    and r['simulated_seconds']<backup_report['simulated_seconds']-1e-9),
                    key=lambda i:result['trials'][i]['simulated_seconds'])
                for i in winners:
                    meta=read(root/f'candidate_{i:02}.json')
                    with np.load(root/f'candidate_{i:02}.npz',allow_pickle=False) as arrays:
                        commands=np.empty((len(arrays['joint_pos']),16));commands[:,ARM_COLUMNS]=arrays['joint_pos']
                        commands[:,[7,15]]=arrays['gripper_target']
                    selected=run_candidate(data,context,backup_report,{**meta,'commands':commands},
                        root/f'optimized_{i:02}',record=True,timeout=args.case_timeout)
                    if selected['status']=='QUALIFIED_EXPERT':result['selected']=selected;break
                if {p.name:digest(p) for p in backup.iterdir() if p.is_file()}!=backup_hashes:
                    raise RuntimeError('Backup mutated during experiment')
                result['backup_unchanged']=True
            rows.append(result);save(root/'summary.json',result)
    save(output/'summary.json',{'schema':'kitchen_retiming_experiment_v1','cases':rows,
        'qualified_optimized':sum(r['selected'] is not None for r in rows),'images_included':False})
    return 0 if all(r['selected'] is not None for r in rows) else 1


if __name__=='__main__':raise SystemExit(main())
