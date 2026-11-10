"""Freeze an RL actor, execute on original CPU physics, qualify and independently replay.

No curriculum resets or object corrections are permitted during generation.
Only independently qualified trajectories enter the normal collection manifest.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from integration.kitchen.data.files import digest,replace_json
from integration.kitchen.data.replay import verify
from integration.kitchen.data.annotations import build_annotations
from .environment import PrivilegedEnvironment


def record_episode(env, policy, directory, provenance):
    """Policy(obs)->8D normalized left joint increments and absolute jaw opening."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
    p=env.platform;c=p.controller;p.data_root=directory/'data'
    report={'case':env.case,'seed':int(p.initialization['seed']),
        'status':'FAILED_ATTEMPT','task_success':False,'images_included':False,
        'initialization_profile':'research','generator':provenance,'physics':'original_cpu_model'}
    saved=env.capture()
    group=hashlib.sha256((p.scene.compiled_model_sha256+env.case['case_id']).encode()+
                        np.asarray(saved['integration_state'],dtype='<f8').tobytes()).hexdigest()
    report['postprocessing']={'source_group':group}
    report['variant_id']=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
    events=[{'stage':stage,'skill':'L1','tick':int(c.tick)} for stage in ('language_stage_started','skill_started')]
    started=time.perf_counter();recording=None
    def call(method):
        # Policy steps use the in-process bounded controller and advance the
        # clock directly; refresh the RPC lease token before recorder commands.
        env.client.token=p.token()
        reply=env.client.call(method,mutating=True)
        if not reply['ok']:raise RuntimeError(reply)
        return reply['result']
    try:
        recording=call('start_state_recording')
        while not env.done:
            _,_,_,_,info=env.step(policy(env.observation()))
            violations=c.contact_guard.inspect()
            if violations:
                report['guard_violations']=violations
                c.hold('RL_GENERATION_MEASURED_GUARD');raise RuntimeError('RL_GENERATION_MEASURED_GUARD')
        if not info['task']['success']:raise RuntimeError('POLICY_TASK_FAILED: '+str(info['task']['pending_reason']))
        events.append({'stage':'skill_qualified','skill':'L1','tick':int(c.tick)})
        p.state_recorder.annotate(env.case,events);call('stop_state_recording')
        trajectory=directory/'trajectory.h5';Path(recording['path']).replace(trajectory)
        result=verify(trajectory)
        report['verification']={k:result[k] for k in ('status','sha256','task_success','maximum_qpos_error','maximum_qvel_error')}
        if result['status']!='CONTROL_RESIMULATION_PASS' or not result['task_success']:
            raise RuntimeError('ORIGINAL_CPU_REPLAY_OR_TASK_FAILED')
        annotation=directory/'annotations.json';replace_json(annotation,build_annotations(trajectory))
        report.update(status='QUALIFIED_EXPERT',task_success=True,trajectory=str(trajectory.resolve()),
            annotations=str(annotation.resolve()),annotations_sha256=digest(annotation),simulated_seconds=result['duration_s'])
    except Exception as error:
        report['error']=repr(error)
    finally:
        if p.state_recorder and not p.state_recorder.closed:
            p.state_recorder.annotate(env.case,events);call('stop_state_recording')
        report['wall_seconds']=time.perf_counter()-started
        replace_json(directory/'report.json',report)
    row={k:report[k] for k in ('seed','status','task_success','variant_id')}
    row.update(case_id=env.case['case_id'],report=str((directory/'report.json').resolve()),
        report_sha256=digest(directory/'report.json'),source_group=group)
    if report['task_success']:row.update(trajectory=report['trajectory'])
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--seeds',nargs='+',type=int,default=[1000,1001,1002])
    parser.add_argument('--variants',type=int,default=3,help='Fixed latent samples per same physical initial state')
    parser.add_argument('--horizon',type=int,default=750)
    parser.add_argument('--device',default='cuda:0')
    args=parser.parse_args()
    if args.variants<1 or args.horizon<1 or len(set(args.seeds))!=len(args.seeds):parser.error('Invalid counts or duplicate seeds')
    if any(s<0 or s>=2**32 for s in args.seeds):parser.error('Seeds must be uint32')
    if args.output.exists():raise FileExistsError(args.output)
    from thirdparty.discodemo.adapter import load_policy
    agent,metadata,z_dim=load_policy(args.checkpoint,device=args.device)
    if not z_dim and args.variants!=1:parser.error('Baseline has no latent; use --variants 1 to avoid duplicates')
    args.output.mkdir(parents=True)
    rows=[];all_rows=[]
    for seed in args.seeds:
        for variant in range(args.variants):
            rng=np.random.default_rng(np.random.SeedSequence([seed,variant,0xD15C0]))
            z=rng.normal(size=z_dim).clip(-1,1).astype('f4')
            def policy(obs):
                return agent.sample_actions(0,{'next_observation':np.r_[obs,z][None]},training=False)[0]
            provenance={'method':'discodemo','actor_sha256':digest(args.checkpoint/'actor.pt'),
                'checkpoint_metadata_sha256':digest(args.checkpoint/'kitchen.json'),
                'training_physics':metadata['physics'],'latent':z.tolist(),'variant':variant}
            env=PrivilegedEnvironment(seed=seed,horizon=args.horizon)
            try:
                row=record_episode(env,policy,args.output/f'seed{seed:010d}_z{variant:03d}',provenance)
            finally:env.close()
            all_rows.append(row)
            if row['task_success']:rows.append(row)
            replace_json(args.output/'manifest.json',rows);replace_json(args.output/'attempts.json',all_rows)
            print(json.dumps(row),flush=True)
    replace_json(args.output/'summary.json',{'qualified':len(rows),'attempts':len(all_rows),
        'schema':'kitchen_discodemo_collection_v1','images_included':False,
        'scope':'CPU generation and independent replay; failed attempts never enter manifest'})


if __name__=='__main__':main()
