"""Replay sealed expert controls once and derive aligned robot/RGB observations.

    These are expert replays, not learned-policy evaluation. Physics is advanced
    from the full initial state; recorded checkpoints are never injected.
"""
import fcntl
import json
from pathlib import Path
import numpy as np
from integration.kitchen.data.files import digest, read, replace_json
from integration.kitchen.data.lerobot_export import policy_episode
from .images import CameraRig, camera_rig, active_views
from .observation import robot_state


def sealed_file(root, name, sha):
    path = (Path(root)/name).resolve()
    if not path.is_relative_to(Path(root).resolve()) or digest(path) != sha:
        raise ValueError('Replay sidecar path/hash mismatch')
    return path


def replay_arrays(root, item, episode):
    """Validate the small measured-state/action sidecar before export/resume."""
    record = item['policy_data']
    path = sealed_file(root, record['file'], record['sha256'])
    with np.load(path, allow_pickle=False) as arrays:
        state, action, ticks = (arrays[k].copy() for k in ('state', 'action', 'ticks'))
    if (state.shape != episode['state'].shape or action.shape != episode['action'].shape
            or not np.isfinite(state).all() or not np.isfinite(action).all()
            or not np.array_equal(ticks, episode['ticks'])
            or not np.array_equal(action, episode['action'])
            or not np.allclose(state, episode['state'], atol=1e-7, rtol=0)):
        raise ValueError('Replay robot/action clock or measured state mismatch')
    return state, action


def validate_item(root, item, episode, rig):
    if (item['source_sha256'] != episode['source_sha256']
            or item['ticks'] != episode['ticks'].tolist()
            or set(item['views']) != {v['key'] for v in active_views(rig)}):
        raise ValueError('Replay receipt/source mismatch')
    if (item['verification']['status'] != 'CONTROL_RESIMULATION_PASS'
            or item['verification']['task_success'] is not True):
        raise ValueError('Unqualified independent replay')
    replay_arrays(root, item, episode)
    for view in item['views'].values():
        if len(view['files']) != len(item['ticks']) or len(view['sha256']) != len(item['ticks']):
            raise ValueError('Replay view length mismatch')
        for name, sha in zip(view['files'], view['sha256']):
            sealed_file(root, name, sha)


def replay_episode(episode, output, rig):
    from integration.kitchen.data.replay import verify, _write_png
    directory = output/episode['source_sha256']
    directory.mkdir(exist_ok=True)
    views = {v['key']: {'files': [], 'sha256': []} for v in active_views(rig)}
    for key in views:
        (directory/key.rsplit('.', 1)[-1]).mkdir(exist_ok=True)
    states = []
    camera = None

    def observe(scene, controller, index):
        nonlocal camera
        if index >= len(episode['ticks']):
            return  # Terminal checkpoint has no following action.
        if controller is None or controller.tick != episode['ticks'][index]:
            raise ValueError('Replay callback/action clock mismatch')
        if camera is None:
            camera = CameraRig(scene, rig)
        states.append(robot_state(scene))
        for key, rgb in camera.render_all().items():
            path = directory/key.rsplit('.', 1)[-1]/f'{controller.tick:09d}.png'
            _write_png(path, rgb)
            views[key]['files'].append(str(path.relative_to(output)))
            views[key]['sha256'].append(digest(path))

    try:
        verification = verify(episode['source'], observer=observe)
    finally:
        if camera is not None:
            camera.close()
    if verification['status'] != 'CONTROL_RESIMULATION_PASS' or verification['task_success'] is not True:
        raise ValueError('Independent expert replay did not qualify; partial images are not sealed')
    data_path = directory/'policy.npz'
    np.savez_compressed(data_path, ticks=episode['ticks'], state=np.asarray(states, dtype=np.float32),
                        action=episode['action'])
    item = {'source': episode['source'], 'source_sha256': episode['source_sha256'],
            'ticks': episode['ticks'].tolist(), 'views': views,
            'state_source': 'independent_control_replay',
            'policy_data': {'file': str(data_path.relative_to(output)), 'sha256': digest(data_path)},
            'verification': {k: verification[k] for k in ('status', 'task_success',
                'maximum_qpos_error', 'maximum_qvel_error', 'compared_checkpoints')}}
    validate_item(output, item, episode, rig)
    replace_json(directory/'episode.json', item)
    return item


def replay(paths, output, rig=None, *, resume=False):
    output, rig = Path(output), camera_rig(rig)
    episodes = [policy_episode(path) for path in paths]
    if len({e['source_sha256'] for e in episodes}) != len(episodes):
        raise ValueError('Duplicate source trajectory')
    contract = {'schema': 'kitchen_replay_dataset_plan_v1', 'camera_rig': rig,
                'sources': [{'source': e['source'], 'sha256': e['source_sha256']} for e in episodes],
                'implementation': {p.name: digest(p) for p in (
                    Path(__file__), Path(__file__).with_name('images.py'),
                    Path(__file__).with_name('observation.py'),
                    Path(__file__).parents[1]/'data/replay.py')}}
    if output.exists() and not resume:
        raise FileExistsError('Use a fresh replay output or --resume')
    if resume and not (output/'replay_plan.json').is_file():
        raise ValueError('No replay plan to resume')
    output.mkdir(parents=True, exist_ok=True)
    with (output/'.replay.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume:
            if read(output/'replay_plan.json') != contract:
                raise ValueError('Replay inputs/cameras/implementation changed; use a new directory')
        else:
            replace_json(output/'replay_plan.json', contract)
        rows = []
        for index, episode in enumerate(episodes):
            receipt = output/episode['source_sha256']/'episode.json'
            if receipt.exists():
                item = read(receipt)
                validate_item(output, item, episode, rig)
                reused = True
            else:
                # Only unsealed derived files may be regenerated after interruption.
                item = replay_episode(episode, output, rig)
                reused = False
            rows.append(item)
            print(json.dumps({'episode': index+1, 'total': len(episodes), 'frames': len(episode['ticks']),
                              'reused': reused, 'source': episode['source']}), flush=True)
        result = {'schema': 'kitchen_rgb_sidecars_v2', 'fps': 25, 'camera_rig': rig,
                  'state_source': 'independent_control_replay', 'episodes': rows}
        replace_json(output/'images.json', result)
    return result
