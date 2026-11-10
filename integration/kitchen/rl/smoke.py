"""Bounded GPU control/state/goal test, not a trained policy benchmark."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
from .warp_environment import WarpAppleEnvironment
from .anchors import load_bank


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worlds',type=int,default=2)
    parser.add_argument('--steps',type=int,default=5)
    parser.add_argument('--bank',type=Path)
    parser.add_argument('--allow-noslip-variant',action='store_true')
    parser.add_argument('--box-foot-proxy',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise FileExistsError(args.output)
    env=WarpAppleEnvironment(nworld=args.worlds,horizon=max(args.steps,75),allow_noslip_variant=args.allow_noslip_variant,
                             box_foot_proxy=args.box_foot_proxy)
    try:
        observation=env.observation()
        # Last reset world shares the CPU prototype's exact initial physical state.
        initial_error=float(np.abs(observation[-1]-env.prototype.observation()).max())
        # Compare the bounded adapter against the original CPU controller for
        # saturated mixed-joint/jaw targets before any physical step.
        rng=np.random.default_rng(7);control_error=0.
        cpu=env.prototype.platform.controller
        saved_reference=cpu.reference.copy()
        for _ in range(8):
            targets=env.control.reference.detach().cpu().numpy().copy()
            targets+=rng.uniform(-.2,.2,size=targets.shape)
            expected=cpu.accept_joint_targets(targets[-1]).copy()
            actual=env.control.prepare(env.t.as_tensor(targets,dtype=env.t.float32,device=env.device))[-1].cpu().numpy()
            control_error=max(control_error,float(np.max(np.abs(actual-expected))))
            cpu.reference=saved_reference.copy()
        if args.bank:
            bank=load_bank(args.bank)
            saved=bank['demos'][0]['snapshots'][-1]['snapshot']
            env.reset(snapshots=[saved]*args.worlds)
        steps=0;success=np.zeros(args.worlds,dtype=bool);terminated=success.copy();truncated=success.copy()
        started=time.perf_counter()
        while steps<args.steps:
            action=np.zeros((args.worlds,8));action[:,7]=1
            obs,reward,terminated,truncated,info=env.step(action);steps+=1;success|=info['success']
            if np.any(terminated|truncated):break
        elapsed=time.perf_counter()-started
        result={'schema':'kitchen_warp_env_smoke_v1','identity':env.b.identity,'steps':steps,
            'observation_shape':list(obs.shape),'initial_cpu_gpu_observation_max_abs':initial_error,
            'bounded_target_cpu_gpu_max_abs':control_error,
            'world_control_steps_per_second':args.worlds*steps/elapsed,'success':success.tolist(),
            'truncated':truncated.tolist(),'buffers':env.b.check_buffers(),
            'final_task_evidence':{k:v.detach().cpu().tolist() for k,v in env.task_evidence.items()},
            'scope':'Idle or near-goal anchor hold; not normal-initial-state RL success'}
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:env.close()


if __name__=='__main__':main()
