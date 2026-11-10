"""MuJoCo process: proprioception/RGB -> remote chunk inference -> bounded actions."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import subprocess
import time
from .environment import PolicyEnvironment
from .protocol import PolicyClient, ChunkBuffer


def run(trajectory, output, client, *, seconds=60, clock='realtime', replan=40, timeout=120, guard=True, video=False, audit=False, camera_rig=None, initial_seed=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    env = PolicyEnvironment(trajectory, guard=guard, camera_rig=camera_rig, initial_seed=initial_seed)
    executor = ThreadPoolExecutor(max_workers=1)
    future = None
    encoder = None
    from .performance import PerformanceAudit
    performance = PerformanceAudit() if audit else None
    pending_observation = None
    boundary = False
    report = {'schema': 'kitchen_policy_rollout_v1', 'trajectory_initial_state_source': str(trajectory),
              'initial_state_mode': 'recorded' if initial_seed is None else 'fresh_research_seed',
              'initial_seed': initial_seed, 'initialization_hash': env.platform.episode['initialization_hash'],
              'replan_remaining': replan, 'action_hz': 25,
              'task_success': False, 'clock': clock, 'guard_enabled': guard,
              'inferences': 0, 'queue_underrun_frames': 0, 'expired_chunks': 0}
    started = time.perf_counter()
    try:
        if video:
            c = env.camera.config
            encoder = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-n', '-f', 'rawvideo',
                '-pixel_format', 'rgb24', '-video_size', f"{c['width']}x{c['height']}",
                '-framerate', '25', '-i', 'pipe:0', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                str(output/'rollout.mp4')], stdin=subprocess.PIPE)
        buffer = ChunkBuffer()
        captured_at = time.perf_counter()
        initial = env.observation()
        if performance:
            performance.add('observation_ms', (time.perf_counter()-captured_at)*1000)
        actions, model = client.infer(initial, reset=True)
        buffer.merge(0, actions, model, 0)
        pending_observation, boundary = captured_at, True
        report['inferences'] += 1
        delay, last_progress = 0, time.monotonic()
        # First inference is a warmup before the simulation clock starts.
        deadline = time.perf_counter()
        while env.frame < math.ceil(seconds * 25):
            if future is not None and future.done():
                actions, model = future.result()
                elapsed = env.frame - observed
                delay = elapsed
                old_action = buffer.get(env.frame)
                if not buffer.merge(observed, actions, model, env.frame):
                    report['expired_chunks'] += 1
                else:
                    pending_observation, boundary = requested_capture, True
                    if performance:
                        performance.replacement(old_action, buffer.get(env.frame))
                        performance.add('observation_age_frames', elapsed)
                report['inferences'] += 1
                future = None
            if future is None and buffer.remaining(env.frame) <= replan:
                captured_at = time.perf_counter()
                obs = env.observation()
                if performance:
                    performance.add('observation_ms', (time.perf_counter()-captured_at)*1000)
                observed = env.frame
                future = executor.submit(client.infer, obs, prefix=buffer.prefix(env.frame), delay=delay)
                requested_at = time.monotonic()
                requested_capture = captured_at
            if future is not None and time.monotonic()-requested_at > timeout:
                raise TimeoutError('Policy inference deadline exceeded')
            action = buffer.get(env.frame)
            if action is None:
                if clock == 'lockstep':
                    # Pause wall-clock computation, NOT a synthetic simulated hold.
                    actions, model = future.result(timeout=timeout)
                    if not buffer.merge(observed, actions, model, env.frame):
                        raise RuntimeError('Inference chunk entirely expired')
                    pending_observation, boundary = requested_capture, True
                    if performance:
                        performance.add('observation_age_frames', env.frame-observed)
                    future = None
                    report['inferences'] += 1
                    action = buffer.get(env.frame)
                else:
                    report['queue_underrun_frames'] += 1
                    if time.monotonic()-last_progress > 10:
                        raise RuntimeError('No timely actions for10s; inference cannot sustain this control rate')
                    from robot_arm.kitchen.action_codec import encode
                    from integration.kitchen.data.lerobot_export import pack16
                    q, g = encode(env.platform.controller.reference)
                    action = pack16(q, g)
            else:
                last_progress = time.monotonic()
            if encoder:
                encoder.stdin.write(env.camera.render().tobytes())
            applied_at = time.perf_counter()
            if clock == 'realtime' and performance:
                performance.add('schedule_lateness_ms', max(0., applied_at-deadline)*1000)
            if performance:
                previous = env.platform.controller.reference.copy()
                if pending_observation is not None:
                    performance.add('warmup_observation_to_apply_ms' if env.frame == 0 else 'observation_to_apply_ms',
                                    (applied_at-pending_observation)*1000)
            success = env.step(action)
            if performance:
                controller = env.platform.controller
                side = 'left' if env.case['task'] == 'L1' else 'right'
                performance.step(applied_at, time.perf_counter()-applied_at, previous, controller.reference,
                                 env.platform.scene.data.qvel[list(env.platform.scene.arms[side].dofs)], boundary)
            pending_observation, boundary = None, False
            if success or env.platform.controller.stopped:
                report['task_success'] = success
                break
            if clock == 'realtime':
                # Do not execute a burst of steps to catch up after rendering or
                # scheduling overruns. Report the overrun, then resume25Hz pacing.
                deadline = max(deadline+.04, time.perf_counter())
                time.sleep(max(0., deadline-time.perf_counter()))
        report['status'] = 'POLICY_SUCCESS' if report['task_success'] else 'POLICY_FAILED'
    except Exception as error:
        report.update(status='POLICY_ERROR', error=repr(error))
    finally:
        report.update(wall_seconds=time.perf_counter()-started, simulated_seconds=env.frame/25,
                      rate_limited_frames=env.rate_limited_frames, guard_violations=env.guard_violations,
                      final_success_error=env.last_success_error, quality=env.quality.report(),
                      policy=getattr(client, 'metadata', None))
        report['action_limits'] = env.action_limits.report()
        executor.shutdown(wait=True, cancel_futures=True)
        if performance:
            report['performance'] = performance.report(client)
        if encoder:
            try:
                encoder.stdin.close()
                status = encoder.wait(timeout=20)
                if status:
                    report['video_error'] = f'Encoder exit{status}'
                else:
                    report['video'] = str(output/'rollout.mp4')
            except (OSError, subprocess.TimeoutExpired) as error:
                encoder.kill()
                encoder.wait(timeout=10)
                report['video_error'] = repr(error)
        env.close()
        (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trajectory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--clock', choices=['lockstep', 'realtime'], default='realtime')
    parser.add_argument('--replan-remaining', type=int, default=40)
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--video', action='store_true', help='Record actual25fps simulation frames, playback1x')
    parser.add_argument('--audit', action='store_true', help='Summarize inference latency, scheduling and motion continuity')
    parser.add_argument('--camera-rig', type=Path, help='Rig JSON, must match checkpoint; default C global+left wrist')
    parser.add_argument('--initial-seed', type=int, help='L1 only: evaluate a fresh research reset, not the recorded training initial state')
    args = parser.parse_args()
    if (args.seconds <= 0 or args.timeout <= 0 or not 1 <= args.replan_remaining < 50
            or args.initial_seed is not None and not 0 <= args.initial_seed < 2**32):
        parser.error('Invalid positive rollout budget/replan threshold')
    report = run(args.trajectory, args.output, PolicyClient(args.host, args.port, args.timeout),
                 seconds=args.seconds, clock=args.clock, replan=args.replan_remaining, timeout=args.timeout, video=args.video, audit=args.audit, camera_rig=args.camera_rig, initial_seed=args.initial_seed)
    print(json.dumps(report))
    return 0 if report['task_success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
