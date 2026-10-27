"""Optional MP4 review export, separate from image-free collection."""
from pathlib import Path
import json
from integration.kitchen.launch import run_path
from integration.kitchen.data.collect_experts import save
from integration.kitchen.data.files import read,digest

def render(output, plan, qualified):
    from integration.kitchen.data.replay import video
    rows = []
    for row in qualified:
        row = dict(row)
        report = read(row['report'])
        trajectory = Path(report['trajectory'])
        parent = output / 'videos' / row['case_id'].replace(':', '_') / f"sample_{row['sample']:02d}"
        parent.mkdir(parents=True, exist_ok=True)
        receipt = parent / 'qualified_video.json'
        if receipt.exists():
            record = read(receipt)
            if record['trajectory_sha256'] != report['verification']['sha256']:
                raise ValueError('Video source hash mismatch')
            for name, expected in record['files'].items():
                path = run_path(name)
                if not path.is_relative_to(parent) or digest(path) != expected:
                    raise ValueError('Video artifact hash/path mismatch')
            row['video'] = record['video']
        else:
            number = 1
            while (parent / f'render_{number:03d}').exists():
                number += 1
            destination = parent / f'render_{number:03d}' / 'trajectory.mp4'
            destination.parent.mkdir()
            try:
                result = video(trajectory, destination, **plan['video'], max_seconds=3600.)
                if result['source_duration_s'] >= 3600:
                    raise RuntimeError('Video duration cap reached; refusing truncated review')
                record = {'video': str(destination), 'trajectory_sha256': result['trajectory_sha256'],
                          'files': {str(p): digest(p) for p in (destination, destination.with_suffix('.video.json'))}}
                save(receipt, record)
                row['video'] = str(destination)
            except Exception as error:
                row['video_error'] = repr(error)
        row['trajectory'] = str(trajectory)
        rows.append(row)
        print(json.dumps({'case_id': row['case_id'], 'sample': row['sample'],
                          'video': row.get('video'), 'error': row.get('video_error')}, ensure_ascii=False), flush=True)
    return rows

