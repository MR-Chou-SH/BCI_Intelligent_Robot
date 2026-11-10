"""Offline image sidecars then genuine LeRobot export, using separate environments."""
import argparse
import json
from pathlib import Path
import numpy as np
from integration.kitchen.data.files import digest
from integration.kitchen.data.lerobot_export import policy_episode, features, episode_views, plan


def trajectory_paths(trajectories=None, manifest=None):
    """Read final qualified manifest rows, never recursively include backups."""
    if manifest is not None:
        from integration.kitchen.launch import run_path
        rows = json.loads(Path(manifest).read_text())
        paths = []
        groups = {}
        for row in rows:
            if row['status'] != 'QUALIFIED_EXPERT' or row['task_success'] is not True:
                raise ValueError('Manifest contains an unqualified episode')
            if row.get('report_sha256') and digest(run_path(row['report'])) != row['report_sha256']:
                raise ValueError('Manifest report hash mismatch')
            group=row.get('source_group')
            # Independent latent variants deliberately share a physical initial
            # state and must remain one train/test split group. Backup copies
            # still fail this check. Trust only a hashed, matching sealed report.
            variant=None
            if row.get('variant_id') and row.get('report_sha256'):
                report=json.loads(run_path(row['report']).read_text())
                if (report.get('generator',{}).get('method')=='discodemo'
                    and report.get('variant_id')==row['variant_id']
                    and report.get('postprocessing',{}).get('source_group')==group
                    and run_path(report.get('trajectory',''))==run_path(row['trajectory'])):
                    variant=row['variant_id']
            if group in groups and (variant is None or None in groups[group] or variant in groups[group]):
                raise ValueError('Duplicate source group/variant in manifest')
            if group:groups.setdefault(group,set()).add(variant)
            paths.append(run_path(row['trajectory']))
    else:
        paths = [Path(p).resolve() for p in trajectories or []]
    if not paths or len({p.resolve() for p in paths}) != len(paths):
        raise ValueError('Empty or duplicate source trajectories')
    return paths


def render(paths, output, rig=None):
    from integration.kitchen.data.replay import read_compact, scene_for, _write_png
    from .images import CameraRig, camera_rig, active_views
    rig = camera_rig(rig)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    episodes = []
    for path in paths:
        episode = policy_episode(path)
        data = read_compact(path)
        scene = scene_for(data['metadata'])
        directory = output / episode['source_sha256']
        directory.mkdir()
        camera = CameraRig(scene, rig)
        views = {view['key']: {'files': [], 'sha256': []} for view in active_views(rig)}
        for key in views:
            (directory/key.rsplit('.', 1)[-1]).mkdir()
        try:
            for tick in episode['ticks']:
                index = np.searchsorted(data['states/tick'], tick)
                for key, rgb in camera.render_all(data['states/qpos'][index]).items():
                    path = directory/key.rsplit('.', 1)[-1]/f'{int(tick):09d}.png'
                    _write_png(path, rgb)
                    views[key]['files'].append(str(path.relative_to(output)))
                    views[key]['sha256'].append(digest(path))
        finally:
            camera.close()
        episodes.append({'source': episode['source'], 'source_sha256': episode['source_sha256'],
                         'ticks': episode['ticks'].tolist(), 'views': views})
    result = {'schema': 'kitchen_rgb_sidecars_v2', 'fps': 25, 'camera_rig': rig, 'episodes': episodes}
    (output / 'images.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def export(paths, sidecar, output, repo_id, level='coarse'):
    from PIL import Image
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    output, sidecar = Path(output), Path(sidecar)
    if output.exists():
        raise FileExistsError('Use a new dataset directory')
    description = plan(paths, level)
    images = json.loads(sidecar.read_text())
    if images['schema'] not in ('kitchen_rgb_sidecars_v1', 'kitchen_rgb_sidecars_v2') or images['fps'] != 25:
        raise ValueError('Unsupported image contract')
    from .images import camera_rig, active_views
    rig = camera_rig(images['camera_rig'] if images['schema'].endswith('v2') else images['camera'])
    active = {v['key']: v['camera'] for v in active_views(rig)}
    if images['schema'].endswith('v1'):
        for item in images['episodes']:
            item['views'] = {images['image_key']: {'files': item['files'], 'sha256': item['sha256']}}
    lookup = {e['source_sha256']: e for e in images['episodes']}
    episodes = [policy_episode(p) for p in paths]
    for episode in episodes:
        item = lookup[episode['source_sha256']]
        if item['ticks'] != episode['ticks'].tolist() or set(item['views']) != set(active):
            raise ValueError('RGB/action clock mismatch')
        for view in item['views'].values():
            if len(view['files']) != len(item['ticks']) or len(view['sha256']) != len(item['ticks']):
                raise ValueError('RGB view/action length mismatch')
            for file, sha in zip(view['files'], view['sha256']):
                path = (sidecar.parent / file).resolve()
                if not path.is_relative_to(sidecar.parent.resolve()) or digest(path) != sha:
                    raise ValueError('Image path/hash mismatch')
        if item.get('state_source') == 'independent_control_replay':
            from .replay_dataset import validate_item
            validate_item(sidecar.parent, item, episode, rig)
        elif images.get('state_source') == 'independent_control_replay' or 'policy_data' in item:
            raise ValueError('Missing replay observation provenance')
    spec = features()
    for key, c in active.items():
        spec[key] = {'dtype': 'image', 'shape': (3, c['height'], c['width']),
                     'names': ['channels', 'height', 'width']}
    ds = LeRobotDataset.create(repo_id=repo_id, root=output, fps=25, features=spec,
                              robot_type='kitchen_fr3_umi', use_videos=False)
    for episode in episodes:
        item = lookup[episode['source_sha256']]
        if item.get('state_source') == 'independent_control_replay':
            from .replay_dataset import replay_arrays
            state, action = replay_arrays(sidecar.parent, item, episode)
        else:
            state, action = episode['state'], episode['action']
        for first, last, prompt in episode_views(episode, level):
            for i in range(first, last):
                frame = {'observation.state': state[i], 'action': action[i], 'task': prompt}
                for key, c in active.items():
                    with Image.open(sidecar.parent / item['views'][key]['files'][i]) as image:
                        rgb = np.asarray(image.convert('RGB'))
                    if rgb.shape != (c['height'], c['width'], 3):
                        raise ValueError('RGB shape mismatch')
                    frame[key] = rgb
                ds.add_frame(frame)
            ds.save_episode()
    ds.finalize()
    info = json.loads((output/'meta/info.json').read_text())
    if info['codebase_version'] != 'v3.0':
        raise ValueError('Installed LeRobot writer must produce dataset v3.0')
    description.update(vision_ready=True, image_contract=active['observation.images.camera1'], camera_rig=rig,
                       features=spec, images_sha256=digest(sidecar), image_keys=list(active),
                       lerobot_version=info['codebase_version'],
                       state_source=images.get('state_source', 'recorded_h5_checkpoints'))
    (output / 'kitchen_export.json').write_text(json.dumps(description, indent=2) + '\n')
    return description


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['render', 'replay', 'export'])
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument('--trajectories', type=Path, nargs='+')
    sources.add_argument('--manifest', type=Path, help='Final qualified samples only, excluding backups/candidates')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--images', type=Path)
    parser.add_argument('--repo-id', default='local/kitchen_l1_apple')
    parser.add_argument('--instruction-level', choices=['coarse', 'fine'], default='coarse')
    parser.add_argument('--camera-rig', type=Path, help='Render rig JSON; default C global+left wrist, right disabled')
    parser.add_argument('--resume', action='store_true', help='Resume replay from sealed episode receipts')
    parser.add_argument('--expected-episodes', type=int, help='Require exactly this many input trajectories')
    args = parser.parse_args(argv)
    paths = trajectory_paths(args.trajectories, args.manifest)
    if args.expected_episodes is not None and len(paths) != args.expected_episodes:
        parser.error(f'Expected {args.expected_episodes} episodes, found {len(paths)}')
    if args.resume and args.command != 'replay':
        parser.error('--resume is supported by replay only')
    if args.command == 'render':
        result = render(paths, args.output, args.camera_rig)
    elif args.command == 'replay':
        from .replay_dataset import replay
        result = replay(paths, args.output, args.camera_rig, resume=args.resume)
    else:
        if not args.images:
            parser.error('--images is required for export')
        result = export(paths, args.images, args.output, args.repo_id, args.instruction_level)
    print(json.dumps({'output': str(args.output), 'schema': result['schema']}))


if __name__ == '__main__':
    main()
