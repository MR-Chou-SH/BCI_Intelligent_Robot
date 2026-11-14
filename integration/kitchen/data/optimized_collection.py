"""Shared immutable expert backup, offline optimization and final selection.

All training receipts refer to the final files at the attempt root. Backups,
candidate actions and trials stay below it and are never counted as samples.
"""
import json
from pathlib import Path
import os
import shutil
import time

import numpy as np

from .collect_experts import one_case,save,source_identity,identity_sha256,validate_previous
from .files import digest,read
from .retiming_experiment import load_backup,restored_platform,build_candidate,run_candidate
from integration.kitchen.planning.action_retiming import ARM_COLUMNS


def receipt(report_path):
    report_path=Path(report_path);r=read(report_path)
    return {'case_id':r['case']['case_id'],'seed':r['seed'],'status':r['status'],
        'task_success':r['task_success'],'error':r.get('error'),'report':str(report_path),
        'report_sha256':digest(report_path),'wall_seconds':r.get('wall_seconds',0.)}


def check_report(path,root,case,seed,identity):
    path=Path(path)
    validate_previous([receipt(path)],Path(root),[case],[seed])
    r=read(path)
    if r.get('source_identity_sha256')!=identity_sha256(identity):raise ValueError('Optimization source identity mismatch')
    return r


def validate_backup_receipt(report,attempt):
    """Also bind the retained backup (or direct-fast source) on ordinary batch resume."""
    post=report.get('postprocessing',{})
    if post.get('status')=='DIRECT_FAST':
        path=Path(post['direct']['report']).resolve()
        if path!=Path(attempt).resolve()/'direct/report.json' or digest(path)!=post['direct']['report_sha256']:
            raise ValueError('Direct-fast receipt path/hash mismatch')
        direct=read(path)
        if direct['status']!='QUALIFIED_EXPERT' or digest(path.with_name('trajectory.h5'))!=direct['verification']['sha256']:
            raise ValueError('Direct-fast source not qualified')
        return
    item=post.get('backup')
    if not item:return
    path=Path(item['report']).resolve();attempt=Path(attempt).resolve()
    if path!=attempt/'backup/report.json' or digest(path)!=item['report_sha256']:
        raise ValueError('Backup receipt path/hash mismatch')
    backup=read(path);post=report['postprocessing']
    allowed={'QUALIFIED_EXPERT'}|({'EXECUTED_REPLAY_PENDING'} if post.get('settings',{}).get('defer_backup_replay') else set())
    if backup['status'] not in allowed or not backup['task_success']:raise ValueError('Unqualified backup')
    if backup['status']=='EXECUTED_REPLAY_PENDING' and post['status']=='ORIGINAL_FALLBACK':
        replay=attempt/'backup_replay.json'
        if not replay.exists() or not deferred_replay_passed(read(replay),backup):
            raise ValueError('Deferred backup replay missing or failed')
    data=path.with_name('trajectory.h5')
    if digest(data)!=backup['verification']['sha256']:raise ValueError('Backup data hash mismatch')
    if backup.get('annotations_sha256') and digest(path.with_name('annotations.json'))!=backup['annotations_sha256']:
        raise ValueError('Backup annotation hash mismatch')
    if backup.get('action_context_sha256') and digest(path.with_name('action_context.json'))!=backup['action_context_sha256']:
        raise ValueError('Backup sparse context hash mismatch')
    selected=Path(post['selected_report']).resolve()
    if not selected.is_relative_to(attempt) or digest(selected)!=post['selected_report_sha256']:
        raise ValueError('Selected component report path/hash mismatch')


def deferred_replay_passed(verification,backup):
    return (verification['status']=='CONTROL_RESIMULATION_PASS' and verification['task_success'] is True
            and verification['sha256']==backup['verification']['sha256'])


def copy_immutable(source,destination):
    """Publish a sealed file at its final path. A hard link stores the bytes
    once (sealed H5/annotations are never rewritten); copy if links fail."""
    source,destination=Path(source),Path(destination)
    if destination.exists():
        if digest(source)!=digest(destination):raise ValueError('Partial final file differs from selected source')
        return
    try:os.link(source,destination)
    except OSError:shutil.copy2(source,destination)


def candidate_plan(root,index,backup,data,context,base,factor,identity,timeout):
    """Generate bounded action candidates; reuse only complete hashed files."""
    name=f'candidate_{index:02d}';meta_path=root/(name+'.json');array_path=root/(name+'.npz')
    expected={'backup_sha256':base['verification']['sha256'],'source_identity_sha256':identity_sha256(identity),'factor':factor}
    if meta_path.exists():
        meta=read(meta_path)
        if any(meta.get(k)!=v for k,v in expected.items()) or digest(array_path)!=meta['actions_sha256']:
            raise ValueError('Candidate receipt mismatch')
    else:
        # An interrupted unsealed candidate is kept, not overwritten.
        if array_path.exists():return None,'Interrupted candidate action file without receipt'
        if source_identity()!=identity:raise RuntimeError('Sources changed during optimization')
        started=time.perf_counter();p=restored_platform(data,None,base['initialization_profile'],base['seed'])
        try:plan=build_candidate(data,context,factor,p.controller,continuous=True,platform=p)
        finally:p.close()
        with array_path.open('xb') as stream:
            np.savez_compressed(stream,joint_pos=plan['commands'][:,ARM_COLUMNS],
                gripper_opening=1-plan['commands'][:,[7,15]]/.05,gripper_target=plan['commands'][:,[7,15]])
        meta={**expected,'changes':plan['changes'],'events':plan['events'],'stats':plan['stats'],
            'actions_sha256':digest(array_path),'planning_wall_seconds':time.perf_counter()-started}
        save(meta_path,meta)
    with np.load(array_path,allow_pickle=False) as arrays:
        commands=np.empty((len(arrays['joint_pos']),16));commands[:,ARM_COLUMNS]=arrays['joint_pos']
        commands[:,[7,15]]=arrays['gripper_target']
    return {**meta,'commands':commands},None


def optimized_case(job,settings):
    """One bounded sample attempt; safe to resume after a qualified backup."""
    case,seed,path,profile,timeout,identity,diagnostics,*options=job
    root=Path(path);root.mkdir(parents=True,exist_ok=True)
    if (root/'report.json').exists():
        r=check_report(root/'report.json',root,case,seed,identity)
        validate_backup_receipt(r,root);return receipt(root/'report.json')
    started=time.perf_counter();backup=root/'backup';backup_report=backup/'report.json'
    if settings.get('direct_fast'):
        # Try the fast expert first; a failed or interrupted attempt is kept and
        # the ordinary backup + adaptive path below runs unchanged.
        direct=root/'direct';direct_report=direct/'report.json'
        if not direct.exists() and not backup.exists():
            one_case((case,seed,str(direct),profile,timeout,identity,False,*options),direct_fast=True)
        if direct_report.exists():
            fast=check_report(direct_report,root,case,seed,identity)
            if fast['status']=='QUALIFIED_EXPERT':
                copy_immutable(fast['trajectory'],root/'trajectory.h5')
                copy_immutable(fast['annotations'],root/'annotations.json')
                result={**fast,'trajectory':str(root/'trajectory.h5'),'annotations':str(root/'annotations.json'),
                    'annotations_sha256':digest(root/'annotations.json'),'wall_seconds':time.perf_counter()-started,
                    'postprocessing':{'schema':'kitchen_postprocessing_v1','settings':settings,'status':'DIRECT_FAST',
                        'direct':{'report':str(direct_report),'report_sha256':digest(direct_report)},
                        'final_simulated_seconds':fast['simulated_seconds'],'source_group':fast['verification']['sha256'],
                        'wall_scope':'current invocation: direct-fast expert, recording and independent replay'}}
                save(root/'report.json',result)
                validate_backup_receipt(result,root)
                return receipt(root/'report.json')
    if not backup_report.exists():
        if backup.exists():raise ValueError('Unsealed backup preserved; cannot overwrite it')
        one_case((case,seed,str(backup),profile,timeout,identity,False,*options),capture_context=True,
                 replay=not settings.get('defer_backup_replay',False))
    base=check_report(backup_report,root,case,seed,identity)
    pending=base['status']=='EXECUTED_REPLAY_PENDING' and settings.get('defer_backup_replay',False)
    if base['status']!='QUALIFIED_EXPERT' and not pending:
        save(root/'report.json',{**base,'postprocessing':{'status':'BACKUP_FAILED','backup_report':str(backup_report)},
            'wall_seconds':time.perf_counter()-started})
        return receipt(root/'report.json')
    backup_files={p.name:digest(p) for p in backup.iterdir() if p.is_file()}
    selected=base;selected_report=backup_report;trials=[];generation_errors=[]
    try:data,context,_=load_backup(backup)
    except ValueError as error:
        # Valid original experts with unsupported clocks still remain usable.
        # Hash/source corruption has already been checked above; propagate any
        # other malformed metadata instead of silently accepting it.
        if str(error)!='Pilot requires uniform boundary actions without mid-frame resets':raise
        generation_errors.append({'reason':str(error)})
        data=None
    if data is not None and settings.get('mode')=='adaptive':
        from .adaptive_optimization import optimize
        candidate,path,trials,generation_errors=optimize(backup,root/'adaptive',identity=identity,timeout=timeout)
        if candidate is not None and candidate['simulated_seconds']<base['simulated_seconds']:
            selected=candidate;selected_report=path
    elif data is not None:
        candidates=[]
        for index,factor in enumerate(settings['factors']):
            error_path=root/f'candidate_{index:02d}.error.json'
            if error_path.exists():generation_errors.append(read(error_path));continue
            try:plan,error=candidate_plan(root,index,backup,data,context,base,factor,identity,timeout)
            except (ValueError,RuntimeError) as error:
                # A malformed receipt must fail closed on resume. Only bounded
                # numerical generation failures may fall back to the expert.
                if 'mismatch' in str(error).lower() or 'changed' in str(error).lower():raise
                item={'factor':factor,'reason':repr(error)};save(error_path,item);generation_errors.append(item);continue
            if plan is None:generation_errors.append({'factor':factor,'reason':error});continue
            if len(plan['commands'])/25>=base['simulated_seconds']-1e-9:
                generation_errors.append({'factor':factor,'reason':'Candidate is not shorter'});continue
            directory=root/f'trial_{index:02d}'
            if (directory/'report.json').exists():
                trial=check_report(directory/'report.json',root,case,seed,identity)
            elif directory.exists():
                trials.append({'factor':factor,'status':'INTERRUPTED','reason':'Partial trial preserved'});continue
            else:trial=run_candidate(data,context,base,plan,directory,timeout=timeout)
            trials.append({'factor':factor,'report':str(directory/'report.json'),
                'report_sha256':digest(directory/'report.json'),'status':trial['status'],
                'simulated_seconds':trial['simulated_seconds'],'error':trial.get('error')})
            if trial['status']=='CANDIDATE_TASK_PASS' and trial['simulated_seconds']<base['simulated_seconds']-1e-9:
                candidates.append((trial['simulated_seconds'],index,plan))
        for _,index,plan in sorted(candidates,key=lambda item:item[0]):
            directory=root/f'optimized_{index:02d}'
            if (directory/'report.json').exists():r=check_report(directory/'report.json',root,case,seed,identity)
            elif directory.exists():continue
            else:r=run_candidate(data,context,base,plan,directory,record=True,timeout=timeout)
            if r['status']=='QUALIFIED_EXPERT':selected=r;selected_report=directory/'report.json';break
    if backup_files!={p.name:digest(p) for p in backup.iterdir() if p.is_file()}:raise ValueError('Backup mutated')
    if source_identity()!=identity:raise RuntimeError('Sources changed during optimization')
    if selected_report==backup_report and settings.get('mode')=='adaptive' and any(
            e.get('reason')=='SOURCE_PATH_COLLISION_AT_1X' for e in generation_errors):
        # Keep the historical task-success backup, but do not silently admit it
        # to a new adaptive dataset after its source path failed the new audit.
        save(root/'report.json',{'case':case,'seed':seed,'status':'FAILED_ATTEMPT','task_success':False,
            'source_identity_sha256':identity_sha256(identity),'error':'BACKUP_PATH_AUDIT_FAILED',
            'wall_seconds':time.perf_counter()-started,'postprocessing':{'status':'BACKUP_AUDIT_FAILED',
                'backup_report':str(backup_report),'trials':trials,'generation_errors':generation_errors}})
        return receipt(root/'report.json')
    deferred=None
    if pending:
        deferred='NOT_REQUIRED'
        if selected_report==backup_report:
            # The native backup becomes the sample: replay it now, exactly as an
            # eagerly verified backup would have been.
            replay_path=root/'backup_replay.json'
            if replay_path.exists():verification=read(replay_path)
            else:
                from .replay import verify
                verification=verify(base['trajectory'])
                verification={k:verification[k] for k in ('status','sha256','task_success','maximum_qpos_error',
                                                           'maximum_qvel_error','duration_s')}
                save(replay_path,verification)
            if not deferred_replay_passed(verification,base):
                save(root/'report.json',{'case':case,'seed':seed,'status':'FAILED_ATTEMPT','task_success':False,
                    'source_identity_sha256':identity_sha256(identity),'error':'BACKUP_REPLAY_FAILED',
                    'wall_seconds':time.perf_counter()-started,'postprocessing':{'status':'BACKUP_REPLAY_FAILED',
                        'backup_report':str(backup_report),'trials':trials,'generation_errors':generation_errors}})
                return receipt(root/'report.json')
            selected={**base,'status':'QUALIFIED_EXPERT','verification':{k:v for k,v in verification.items() if k!='duration_s'}}
            deferred='PASS'
    copy_immutable(selected['trajectory'],root/'trajectory.h5')
    copy_immutable(selected['annotations'],root/'annotations.json')
    result={**selected,'trajectory':str(root/'trajectory.h5'),'annotations':str(root/'annotations.json'),
        'annotations_sha256':digest(root/'annotations.json'),'wall_seconds':time.perf_counter()-started,
        'postprocessing':{'schema':'kitchen_postprocessing_v1','settings':settings,
            'status':'OPTIMIZED' if selected_report!=backup_report else 'ORIGINAL_FALLBACK',
            'backup':{'report':str(backup_report),'report_sha256':digest(backup_report)},
            'selected_report':str(selected_report),'selected_report_sha256':digest(selected_report),
            'original_simulated_seconds':base['simulated_seconds'],'final_simulated_seconds':selected['simulated_seconds'],
            'trials':trials,'generation_errors':generation_errors,
            'wall_scope':'current invocation including new backup/trials/recording/replay; reused stages excluded',
            'source_group':base['verification']['sha256'],**({'backup_replay':deferred} if deferred else {})}}
    # The sparse context belongs to the immutable original, not the final file.
    for key in ('action_context','action_context_sha256'):result.pop(key,None)
    save(root/'report.json',result)
    validate_backup_receipt(result,root)
    return receipt(root/'report.json')
