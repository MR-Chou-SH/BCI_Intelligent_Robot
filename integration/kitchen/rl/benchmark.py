"""Explicit Warp model-compatibility and primitive physics throughput probe."""
import argparse
import json
from pathlib import Path
import time
import numpy as np
import mujoco
from robot_arm.kitchen.warp_backend import WarpBackend
from .environment import PrivilegedEnvironment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worlds',type=int,default=16)
    parser.add_argument('--steps',type=int,default=100)
    parser.add_argument('--allow-noslip-variant',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.worlds<1 or args.steps<1:parser.error('Positive worlds and steps required')
    if args.output.exists():raise FileExistsError(args.output)
    env=PrivilegedEnvironment(seed=0)
    try:
        source=env.platform.scene
        backend=WarpBackend(source,nworld=args.worlds,allow_noslip_variant=args.allow_noslip_variant)
        cpu=mujoco.MjData(backend.cpu_model);mujoco.mj_copyData(cpu,backend.cpu_model,source.data)
        controls=np.tile(source.data.ctrl,(args.worlds,1))
        backend.step_controls(controls,substeps=20);backend.wp.synchronize()
        for _ in range(20):mujoco.mj_step(backend.cpu_model,cpu)
        gpu=backend.download(0)
        error={'qpos_max_abs':float(np.abs(cpu.qpos-gpu.qpos).max()),
               'qvel_max_abs':float(np.abs(cpu.qvel-gpu.qvel).max())}
        start=time.perf_counter();backend.step_controls(controls,substeps=args.steps);backend.wp.synchronize()
        elapsed=time.perf_counter()-start
        result={'schema':'kitchen_warp_probe_v1','identity':backend.identity,
                'same_variant_cpu_gpu_20steps':error,'warm_wall_seconds':elapsed,
                'physics_world_steps_per_second':args.worlds*args.steps/elapsed,
                'equivalent25hz_control_frames_per_second':args.worlds*args.steps/elapsed/20,
                'buffers':backend.check_buffers(),'original_noslip_unchanged':int(source.model.opt.noslip_iterations)==2,
                'scope':'Constant native actuator controls only. Not RL training throughput or original-model equivalence.'}
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    finally:env.close()


if __name__=='__main__':main()
