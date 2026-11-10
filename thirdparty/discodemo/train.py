"""Bounded kitchen FlashSAC + parallel reverse curriculum + gated METRA training.

Uses the official algorithm modules, without importing the Isaac-specific runner.
Checkpoints contain replay, in-flight n-step queue, RNG, curriculum and physical state.
GPU physical restore recomputes contact caches; it is not a bitwise physics guarantee.
"""
import argparse
from dataclasses import asdict,replace
from pathlib import Path
from collections import deque
import json
import random
import time
import numpy as np
from thirdparty.discodemo.adapter import verify_upstream,make_config,create_agent,configuration,resume_decay_steps
from integration.kitchen.rl.anchors import load_bank
from integration.kitchen.data.files import digest,replace_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bank',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--worlds',type=int,default=16)
    parser.add_argument('--steps',type=int,default=100000,help='Total world control transitions, including warmup')
    parser.add_argument('--batch-size',type=int,default=128)
    parser.add_argument('--buffer-size',type=int,default=50000)
    parser.add_argument('--warmup',type=int,default=512)
    parser.add_argument('--updates',type=int,default=1,help='Gradient updates per vector interaction')
    parser.add_argument('--alpha',type=float,default=.3,help='0: reverse-curriculum baseline; .3: gated METRA')
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--save-every',type=int,default=10000)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--allow-noslip-variant',action='store_true')
    parser.add_argument('--box-foot-proxy',action='store_true')
    args=parser.parse_args()
    if min(args.worlds,args.steps,args.batch_size,args.buffer_size,args.warmup,args.updates,args.save_every)<1:
        parser.error('Counts must be positive')
    if args.alpha<0 or args.batch_size<2 or args.buffer_size<max(args.warmup,args.worlds):
        parser.error('Invalid alpha, batch or replay capacity')
    if args.output.exists():raise FileExistsError('Use a fresh output even when resuming')
    receipt_sha=verify_upstream()
    import torch
    from flash_rl.rfcl.curriculum_core import CurriculumCore,DemoCurriculumMetadata
    from integration.kitchen.rl.warp_environment import WarpAppleEnvironment
    torch.set_num_threads(1);torch.manual_seed(args.seed);np.random.seed(args.seed);random.seed(args.seed)
    bank=load_bank(args.bank)
    cfg=make_config(seed=args.seed,steps=args.steps,batch=args.batch_size,capacity=args.buffer_size,warmup=args.warmup)
    previous=json.loads((args.resume/'kitchen.json').read_text()) if args.resume else None
    if previous is not None:
        cfg=replace(cfg,learning_rate_decay_step=resume_decay_steps(configuration(cfg),previous,args.steps))
    env=WarpAppleEnvironment(nworld=args.worlds,seed=args.seed,allow_noslip_variant=args.allow_noslip_variant,
                             box_foot_proxy=args.box_foot_proxy)
    agent,gate,z_dim=create_agent(cfg,args.worlds,len(env.layout.low),args.alpha,bank=bank)
    core=CurriculumCore(args.worlds,[len(d['snapshots']) for d in bank['demos']],None,
        reverse_step_size=1,advance_threshold=.9,frontier_window=10,minimum_episode_steps=75)
    args.output.mkdir(parents=True)
    metadata={'schema':'kitchen_discodemo_checkpoint_v1','agent_config':configuration(cfg),
        'upstream_receipt_sha256':receipt_sha,'bank_sha256':digest(args.bank),
        'bank_observation_adapter':bank['observation_adapter'],
        'physical_dim':len(env.layout.low),'observation':env.layout.schema(),'alpha':args.alpha,
        'worlds':args.worlds,'physics':env.b.identity,'arguments':vars(args)|{'bank':str(args.bank),
        'output':str(args.output),'resume':str(args.resume) if args.resume else None},
        'task':'kitchen_expert_v3:L1:apple','scope':'Engineering pilot, not a validated data generator',
        'resume_semantics':'Native physical state restored; contacts recomputed, not bitwise identical'}
    if previous is not None:
        metadata['resume_budget']={'checkpoint_step':previous['step'],'total_steps':args.steps,
            'learning_rate_decay_step':cfg.learning_rate_decay_step,'schedule':'preserved_from_checkpoint'}
    rng=np.random.default_rng(args.seed);latents=np.zeros((args.worlds,z_dim),dtype='f4')
    uid=np.zeros(args.worlds,dtype=np.int64);next_uid=1;starts={};phase='reverse';step=0;episodes=0;successes=0
    forward_start=np.zeros(args.worlds,dtype=bool);forward_episodes=0;forward_successes=0

    def reset(worlds):
        nonlocal next_uid
        forward_start[worlds]=phase=='forward'
        if phase=='reverse':
            states=[];limits=[]
            for world in worlds:
                demo,index=core.assign_reset(world);starts[int(world)]=(demo,index)
                row=bank['demos'][demo];anchor=row['snapshots'][index]
                states.append(anchor['snapshot']);limits.append(75+row['frames']-anchor['frame'])
            env.reset(worlds,snapshots=states)
            env.time_limits[worlds]=torch.tensor(limits,device=env.device)
        else:env.reset(worlds)
        if z_dim:latents[worlds]=rng.normal(size=(len(worlds),z_dim)).clip(-1,1)
        uid[worlds]=np.arange(next_uid,next_uid+len(worlds));next_uid+=len(worlds)
        if gate is not None:
            if next_uid>=1<<20:raise RuntimeError('Episode UID capacity exhausted; never reuse gate slots')
            agent.set_skill_episode_uid_next(next_uid)

    def observation():return np.c_[env.observation(),latents].astype('f4')

    def save():
        directory=args.output/f'step{step:09d}'
        directory.mkdir()
        agent.save(str(directory));agent.save_replay_buffer(str(directory))
        online=agent._replay_buffer._online
        native={name:env.wp.to_torch(getattr(env.b.data,name)).cpu().clone()
                for name in ('qpos','qvel','qacc_warmstart','ctrl','qfrc_applied','xfrc_applied','time')}
        loop={'version':1,'step':step,'episodes':episodes,'successes':successes,'phase':phase,'starts':starts,
            'forward_start':forward_start,'forward_episodes':forward_episodes,'forward_successes':forward_successes,
            'latents':latents,'uid':uid,'next_uid':next_uid,'rng':rng.bit_generator.state,
            'numpy_rng':np.random.get_state(),'python_rng':random.getstate(),'torch_rng':torch.get_rng_state(),
            'cuda_rng':torch.cuda.get_rng_state_all(),'env_rng':env.rng.bit_generator.state,
            'native':native,'control':{k:getattr(env.control,k).cpu().clone() for k in ('reference','accepted','previous')},
            'task':{k:getattr(env,k).cpu().clone() for k in ('steps','holds','done','time_limits')},
            'tick':env.b.tick.cpu().clone(),'seeds':env.seeds,'queue':list(online._n_step_transitions),
            'curriculum':[asdict(md) for md in core.demo_metadata],'frontier':core.frontier_state(),
            'curriculum_rng':core._state_rng.get_state(),'noise':[agent._cached_noise,agent._cur_noise_repeat_count,agent._cur_noise_repeat_n]}
        torch.save(loop,directory/'loop.pt')
        replace_json(directory/'kitchen.json',metadata|{'step':step,'updates':agent._update_step,
            'episodes':episodes,'successes':successes,'phase':phase,
            'forward_episodes':forward_episodes,'forward_successes':forward_successes})
        replace_json(args.output/'latest.json',{'checkpoint':str(directory.resolve())})
        return directory

    try:
        if args.resume:
            for key in ('agent_config','upstream_receipt_sha256','bank_sha256','worlds','physics','alpha'):
                if previous[key]!=metadata[key]:raise ValueError(f'Resume mismatch: {key}')
            if previous.get('bank_observation_adapter') is not None and previous['bank_observation_adapter']!=metadata['bank_observation_adapter']:
                raise ValueError('Resume mismatch: bank_observation_adapter')
            if previous.get('bank_observation_adapter') is None:
                print(json.dumps({'resume_observation_adapter':metadata['bank_observation_adapter'],
                    'reason':'Align legacy demo TCP quaternion signs with unchanged GPU rollout convention'}),flush=True)
            agent.load(str(args.resume));agent.load_replay_buffer(str(args.resume))
            # Only load checkpoints generated locally by this runner; never load remote pickle.
            loop=torch.load(args.resume/'loop.pt',map_location='cpu',weights_only=False)
            if loop['version']!=1:raise ValueError('Unsupported runner checkpoint')
            step=loop['step'];episodes=loop['episodes'];successes=loop['successes'];phase=loop['phase'];starts=loop['starts']
            if phase=='forward' and 'forward_start' not in loop:raise ValueError('Legacy forward-phase checkpoint lacks episode origins')
            forward_start=loop.get('forward_start',forward_start)
            forward_episodes=loop.get('forward_episodes',0);forward_successes=loop.get('forward_successes',0)
            latents=loop['latents'];uid=loop['uid'];next_uid=loop['next_uid'];env.seeds=loop['seeds']
            for key,value in loop['native'].items():env.wp.to_torch(getattr(env.b.data,key)).copy_(value.to(env.device))
            with env.wp.ScopedDevice(env.device):env.b.mjw.forward(env.b.model,env.b.data)
            for key,value in loop['control'].items():getattr(env.control,key).copy_(value.to(env.device))
            for key,value in loop['task'].items():getattr(env,key).copy_(value.to(env.device))
            env.b.tick.copy_(loop['tick'].to(env.device));env.rng.bit_generator.state=loop['env_rng']
            core.demo_metadata=[DemoCurriculumMetadata(**md) for md in loop['curriculum']]
            core.set_frontier_state(loop['frontier']);core._state_rng.set_state(loop['curriculum_rng']);core.mark_active_dirty()
            agent._replay_buffer._online._n_step_transitions=deque(
                [{k:v.to(env.device) for k,v in row.items()} for row in loop['queue']],maxlen=cfg.n_step)
            agent._cached_noise,agent._cur_noise_repeat_count,agent._cur_noise_repeat_n=[v.to(env.device) for v in loop['noise']]
            rng.bit_generator.state=loop['rng'];np.random.set_state(loop['numpy_rng']);random.setstate(loop['python_rng'])
            torch.set_rng_state(loop['torch_rng']);torch.cuda.set_rng_state_all(loop['cuda_rng'])
        else:reset(list(range(args.worlds)))
        obs=observation();started=time.perf_counter();initial_step=step;last_saved=step;metrics={}
        with (args.output/'metrics.jsonl').open('x') as log:
            while step<args.steps:
                action=agent.sample_actions(step,{'next_observation':obs},training=True)
                physical,reward,terminated,truncated,info=env.step(action)
                nxt=np.c_[physical,latents].astype('f4')
                row={'observation':obs,'action':action,'next_observation':nxt,'reward':reward,
                     'terminated':terminated,'truncated':truncated}
                if gate is not None:row['ep_uid']=uid.copy()
                agent.process_transition(row);step+=args.worlds
                ended=np.flatnonzero(terminated|truncated).tolist()
                if ended:
                    if gate is not None:gate.set(torch.as_tensor(uid[ended]),torch.as_tensor(info['success'][ended]))
                    episodes+=len(ended);successes+=int(info['success'][ended].sum())
                    forward_episodes+=int(forward_start[ended].sum())
                    forward_successes+=int((info['success'][ended]&forward_start[ended]).sum())
                    if phase=='reverse':
                        for world in ended:
                            demo,index=starts[world];core.record_outcome(int(info['success'][world]),index,demo)
                        core.step_curriculum()
                        if core.reverse_solved_frac>=.5:
                            phase='forward'
                            # Mark interrupted reverse episodes as failed for the gate. The
                            # replay boundary must be explicit before replacing their state.
                            # Switch once all current episodes end naturally via short limits.
                            env.time_limits[:]=env.steps+1
                    reset(ended)
                obs=observation()
                if agent.can_start_training():
                    for _ in range(args.updates):metrics=agent.update(step)
                    if not all(np.isfinite(v) for v in metrics.values()):raise FloatingPointError('Nonfinite learner metrics')
                if ended or step%(args.worlds*25)==0:
                    item={'step':step,'phase':phase,'episodes':episodes,'successes':successes,
                        'forward_episodes':forward_episodes,'forward_successes':forward_successes,
                        'frontiers':[md.start_step for md in core.demo_metadata],
                        'world_steps_per_second':(step-initial_step)/max(time.perf_counter()-started,1e-9),**metrics}
                    log.write(json.dumps(item)+'\n');log.flush();print(json.dumps(item),flush=True)
                if step-last_saved>=args.save_every:save();last_saved=step
            if step!=last_saved:save()
    finally:env.close()


if __name__=='__main__':main()
