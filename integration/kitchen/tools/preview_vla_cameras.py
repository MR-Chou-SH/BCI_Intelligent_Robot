"""Render frontal camera candidates and the enabled left wrist from one H5.

This is a camera review, not a training configuration change. Only detached
states/render cameras are updated; source H5/model physics are unchanged.
"""
import argparse
import json
import math
from pathlib import Path
import numpy as np


CANDIDATES = [
    {'id': 'A', 'label': 'Frontal overview', 'position_world_m': [0., -2.70, 1.95],
     'lookat': [0., -.18, .95], 'fovy_deg': 50.},
    {'id': 'B', 'label': 'Frontal closer', 'position_world_m': [0., -2.25, 1.80],
     'lookat': [0., -.18, .95], 'fovy_deg': 50.},
    {'id': 'C', 'label': 'Frontal higher', 'position_world_m': [0., -2.10, 2.20],
     'lookat': [0., -.18, .90], 'fovy_deg': 50.},
    {'id': 'D', 'label': 'Frontal tight', 'position_world_m': [0., -1.95, 1.75],
     'lookat': [0., -.18, .95], 'fovy_deg': 50.},
]


def frontal_view(config):
    import mujoco
    view = mujoco.MjvCamera()
    mujoco.mjv_defaultCamera(view)
    view.lookat[:] = config['lookat']
    offset = np.asarray(config['position_world_m'])-np.asarray(config['lookat'])
    view.distance = float(np.linalg.norm(offset))
    view.azimuth = math.degrees(math.atan2(offset[1], offset[0]))+180.
    view.elevation = -math.degrees(math.asin(float(offset[2]/view.distance)))
    return view


def render(trajectory, output, size=768):
    from integration.kitchen.data.replay import read_compact, scene_for, _write_png
    from integration.kitchen.data.files import digest
    from integration.kitchen.vla.images import Camera
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    data = read_compact(trajectory)
    scene = scene_for(data['metadata'])
    seconds = [0, 8, 20]
    snapshots = [(second, int(np.searchsorted(data['states/tick'], data['start_tick']+int(second*500))))
                 for second in seconds]
    if any(index >= len(data['states/tick']) for _, index in snapshots):
        raise ValueError('Camera review requires at least20s of source trajectory')
    rows = []
    original_fovy = float(scene.model.vis.global_.fovy)
    camera = Camera(scene, {'camera': 'v3_front', 'width': size, 'height': size, 'zoom': 1., 'lookat': None})
    try:
        for config in CANDIDATES:
            camera.view = frontal_view(config)
            scene.model.vis.global_.fovy = config['fovy_deg']
            for second, index in snapshots:
                name = f"{config['id']}_{second:02d}s.png"
                _write_png(output/name, camera.render(data['states/qpos'][index]))
                rows.append({'view': config['id'], 'relative_time_s': second,
                             'tick': int(data['states/tick'][index]), 'file': name})
    finally:
        scene.model.vis.global_.fovy = original_fovy
        camera.close()
    wrist = Camera(scene, {'camera': 'left_wrist', 'width': size, 'height': size, 'zoom': 1., 'lookat': None})
    try:
        for second, index in snapshots:
            name = f'left_wrist_{second:02d}s.png'
            _write_png(output/name, wrist.render(data['states/qpos'][index]))
            rows.append({'view': 'left_wrist', 'relative_time_s': second,
                         'tick': int(data['states/tick'][index]), 'file': name})
    finally:
        wrist.close()
    index = {'schema': 'kitchen_vla_camera_preview_v1', 'source': str(Path(trajectory).resolve()),
             'source_sha256': digest(trajectory), 'resolution': [size, size],
             'candidates': CANDIDATES, 'frames': rows,
             'proposed_slots': [
                 {'key': 'camera1', 'role': 'global', 'enabled': True, 'selection': 'pending_camera_review'},
                 {'key': 'camera2', 'role': 'left_wrist', 'enabled': True},
                 {'key': 'camera3', 'role': 'right_wrist', 'enabled': False}],
             'scope': 'Preview only; no current training/inference camera contract changed; right wrist not rendered'}
    (output/'index.json').write_text(json.dumps(index, indent=2)+'\n')
    gallery = ['<!doctype html><meta charset="utf-8"><title>Frontal camera review</title>',
               '<style>body{font:16px sans-serif;background:#eee;margin:24px}table{border-spacing:12px}',
               'img{width:min(29vw,512px)}th{text-align:left}td{vertical-align:top}</style>',
               '<h1>全景正对工作台：四组距离与高度候选</h1>',
               '<p>列：初态0s、抓取8s、转运20s。同一专家轨迹、同一物理状态。右腕相机关闭。</p>',
               '<table><tr><th>视角</th><th>初态0s</th><th>抓取8s</th><th>转运20s</th></tr>']
    for config in [*CANDIDATES, {'id': 'left_wrist', 'label': 'Enabled left wrist'}]:
        gallery.append(f"<tr><th>{config['id']}<br>{config['label']}</th>"+
                       ''.join(f'<td><a href="{config["id"]}_{s:02d}s.png"><img src="{config["id"]}_{s:02d}s.png"></a></td>'
                               for s in seconds)+'</tr>')
    gallery.append('</table>')
    (output/'index.html').write_text('\n'.join(gallery))
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trajectory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--size', type=int, default=768)
    args = parser.parse_args()
    if not 256 <= args.size <= 1024:
        parser.error('Preview size must be256..1024')
    result = render(args.trajectory, args.output, args.size)
    print(json.dumps({'output': str(args.output), 'frames': len(result['frames'])}))


if __name__ == '__main__':
    main()
