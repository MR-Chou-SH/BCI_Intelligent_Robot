"""Render qualified expert episodes into an OpenPI/LeRobot staging directory.

Each episode is replayed once from its sealed controls (the microwave lamp and
water need the replayed device state), every enabled VLA camera is encoded
straight to H.264, and the aligned 25 Hz measured state, action and
coarse/fine instruction ranges are saved. No PNG frames are written. The
LeRobot v2.1 dataset is assembled later in the OpenPI environment
(``examples/kitchen_bci/build_lerobot.py`` on the OpenPI branch).
"""
import argparse
import json
import multiprocessing
import os
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault('MUJOCO_GL', 'egl')

import numpy as np

from integration.kitchen.data.files import digest, read, replace_json

SCHEMA = 'kitchen_openpi_staging_v1'
STATS_EVERY = 25  # one image-stat sample per second of video


def encoder(path, width, height, gop, crf):
    command = ['ffmpeg', '-nostdin', '-loglevel', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
               '-video_size', f'{width}x{height}', '-framerate', '25', '-i', 'pipe:0', '-an',
               '-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf), '-g', str(gop), '-bf', '0',
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(path)]
    return subprocess.Popen(command, stdin=subprocess.PIPE)


def frame_count(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_packets',
                             '-show_entries', 'stream=nb_read_packets', '-of', 'csv=p=0', str(path)],
                            capture_output=True, text=True, check=True)
    return int(result.stdout.strip())


def image_stats(samples):
    pixels = np.stack(samples).astype(np.float64) / 255.  # n, h, w, 3
    per_channel = pixels.reshape(-1, 3)
    shape = lambda values: [[[float(v)]] for v in values]
    return {'min': shape(per_channel.min(0)), 'max': shape(per_channel.max(0)),
            'mean': shape(per_channel.mean(0)), 'std': shape(per_channel.std(0)), 'count': [len(samples)]}


def stage_episode(index, trajectory, output, rig, gop, crf):
    """Replay one sealed episode; publish its directory only when complete."""
    from integration.kitchen.data.lerobot_export import policy_episode
    from integration.kitchen.data.replay import verify
    from .images import CameraRig, active_views, device_visual_contract
    from .observation import robot_state

    final = Path(output) / f'{index:06d}'
    if (final / 'episode.json').is_file():
        return read(final / 'episode.json')
    partial = final.with_name(final.name + '.partial')
    if partial.exists():
        shutil.rmtree(partial)  # unsealed derived files from an interrupted run
    partial.mkdir(parents=True)
    episode = policy_episode(trajectory)
    report = read(Path(trajectory).with_name('report.json'))
    views = active_views(rig)
    encoders = {v['key']: encoder(partial / f"{v['key'].rsplit('.', 1)[-1]}.mp4", v['camera']['width'],
                                  v['camera']['height'], gop, crf) for v in views}
    samples = {v['key']: [] for v in views}
    states, camera = [], None

    def observe(scene, controller, step):
        nonlocal camera
        if step >= len(episode['ticks']):
            return  # terminal checkpoint has no following action
        if controller is None or controller.tick != episode['ticks'][step]:
            raise ValueError('Replay callback/action clock mismatch')
        if camera is None:
            camera = CameraRig(scene, rig, devices=controller.devices)
        states.append(robot_state(scene))
        for key, rgb in camera.render_all().items():
            encoders[key].stdin.write(np.ascontiguousarray(rgb).tobytes())
            if step % STATS_EVERY == 0:
                samples[key].append(rgb)

    try:
        verification = verify(trajectory, observer=observe)
    finally:
        if camera is not None:
            camera.close()
        for process in encoders.values():
            process.stdin.close()
            process.wait()
    if any(p.returncode for p in encoders.values()):
        raise RuntimeError('ffmpeg failed for episode %d' % index)
    if verification['status'] != 'CONTROL_RESIMULATION_PASS' or verification['task_success'] is not True:
        raise ValueError('Independent expert replay did not qualify')
    state = np.asarray(states, dtype=np.float32)
    count = len(episode['ticks'])
    if state.shape != episode['state'].shape or not np.allclose(state, episode['state'], atol=1e-7, rtol=0):
        raise ValueError('Replay measured state differs from the recorded state')
    files = {}
    for v in views:
        path = partial / f"{v['key'].rsplit('.', 1)[-1]}.mp4"
        if frame_count(path) != count:
            raise ValueError('Encoded frame count mismatch: ' + path.name)
        files[v['key']] = {'file': path.name, 'sha256': digest(path)}
    np.savez_compressed(partial / 'policy.npz', ticks=episode['ticks'], state=state, action=episode['action'])
    annotation = episode['annotation']
    item = {'schema': SCHEMA, 'episode_index': index, 'frames': count, 'fps': 25,
            'source': episode['source'], 'source_sha256': episode['source_sha256'],
            'source_group': episode['source_group'], 'case': report['case'], 'seed': report['seed'],
            'task_type': report['case']['task'],
            'postprocessing_status': report.get('postprocessing', {}).get('status', 'NOT_REQUESTED'),
            'coarse_instruction': annotation['coarse_instruction'],
            'segments': [{'skill': s['skill'], 'instruction': s['instruction'],
                          'start': s['export_start'], 'end': s['export_end']} for s in episode['segments']],
            'policy_data': {'file': 'policy.npz', 'sha256': digest(partial / 'policy.npz')},
            'videos': files, 'image_stats': {k: image_stats(v) for k, v in samples.items()},
            'verification': {k: verification[k] for k in ('status', 'task_success',
                             'maximum_qpos_error', 'maximum_qvel_error')}}
    replace_json(partial / 'episode.json', item)
    partial.rename(final)
    return item


def selected_rows(batch, per_case):
    rows = read(Path(batch) / 'manifest.json')
    if per_case is None:
        return rows
    seen, chosen = {}, []
    for row in rows:
        if seen.get(row['case_id'], 0) < per_case:
            seen[row['case_id']] = seen.get(row['case_id'], 0) + 1
            chosen.append(row)
    return chosen


def main(argv=None):
    from integration.kitchen.launch import run_path
    from .images import camera_rig, device_visual_contract
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', type=run_path, required=True, help='qualified collection directory')
    parser.add_argument('--output', type=run_path, required=True)
    parser.add_argument('--per-case', type=int, help='only the first K samples of every case (pipeline tests)')
    parser.add_argument('--jobs', type=int, default=6)
    parser.add_argument('--gop', type=int, default=10, help='H.264 keyframe interval (random-access cost)')
    parser.add_argument('--crf', type=int, default=20)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if not (1 <= args.jobs <= (os.cpu_count() or 4) and 1 <= args.gop <= 250 and 0 <= args.crf <= 51
            and (args.per_case is None or args.per_case >= 1)):
        parser.error('Invalid bounds')
    rows = selected_rows(args.batch, args.per_case)
    rig = camera_rig(None)
    implementation = {p.name: digest(p) for p in (Path(__file__), Path(__file__).with_name('images.py'),
                      Path(__file__).with_name('observation.py'),
                      Path(__file__).parents[3] / 'robot_arm/kitchen/device_visuals.py')}
    plan = {'schema': SCHEMA + '_plan', 'batch': str(args.batch), 'manifest_sha256': digest(args.batch / 'manifest.json'),
            'per_case': args.per_case, 'episodes': [r['trajectory'] for r in rows], 'camera_rig': rig,
            'device_visuals': device_visual_contract(), 'video': {'codec': 'h264', 'pix_fmt': 'yuv420p',
            'gop': args.gop, 'crf': args.crf, 'b_frames': 0}, 'implementation': implementation}
    output = args.output
    if (output / 'plan.json').exists():
        if not args.resume:
            parser.error('Existing staging requires --resume')
        if read(output / 'plan.json') != plan:
            parser.error('Inputs, cameras or implementation changed; use a new output directory')
    else:
        if args.resume:
            parser.error('No plan to resume')
        output.mkdir(parents=True, exist_ok=False)
        replace_json(output / 'plan.json', plan)
    print(json.dumps({'episodes': len(rows), 'output': str(output)}), flush=True)
    failures = []
    with ProcessPoolExecutor(args.jobs, mp_context=multiprocessing.get_context('spawn'), max_tasks_per_child=20) as pool:
        futures = {pool.submit(stage_episode, i, r['trajectory'], str(output), rig, args.gop, args.crf): i
                   for i, r in enumerate(rows)}
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                item = future.result()
                if done % 50 == 0 or done == len(futures):
                    print(json.dumps({'done': done, 'total': len(futures), 'last': item['episode_index']}), flush=True)
            except Exception as error:
                failures.append({'episode_index': futures[future], 'error': repr(error)})
                print(json.dumps(failures[-1]), flush=True)
    summary = {'schema': SCHEMA + '_summary', 'episodes': len(rows), 'failed': failures,
               'complete': not failures}
    replace_json(output / 'staging.json', summary)
    print(json.dumps(summary), flush=True)
    return 0 if not failures else 1


if __name__ == '__main__':
    raise SystemExit(main())
