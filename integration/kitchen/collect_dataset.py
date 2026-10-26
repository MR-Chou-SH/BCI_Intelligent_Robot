"""Collect N qualified examples per concrete task, then render review videos.

Attempt directories are immutable. Missing reports after an interruption consume
an attempt; sealed reports are recovered without repeating simulation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil

from .collect_experts import one_case, save, source_identity, validate_previous
from .expert_catalog import cases
from .launch import run_path


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def replace_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def slots(plan):
    for case in plan['cases']:
        for sample in range(plan['samples_per_case']):
            yield case, sample


def slot_path(output, case, sample):
    return output / 'attempts' / case['case_id'].replace(':', '_') / f'sample_{sample + 1:02d}'


def attempt_seed(plan, sample, attempt):
    return plan['base_seed'] + sample + attempt * plan['samples_per_case']


def recover_attempt(directory, case, seed, output, identity):
    """Read only sealed reports; interrupted unsealed work never qualifies."""
    receipt = directory.with_suffix('.result.json')
    report_path = directory / 'report.json'
    if receipt.exists():
        row = read(receipt)
    elif report_path.exists():
        report = read(report_path)
        row = {'case_id': case['case_id'], 'seed': seed, 'status': report['status'],
               'task_success': report['task_success'], 'error': report.get('error'),
               'report': str(report_path), 'report_sha256': digest(report_path)}
        save(receipt, row)
    else:
        return {'status': 'INTERRUPTED', 'seed': seed, 'error': 'No sealed report; preserved partial attempt'}
    if row.get('report'):
        if Path(row['report']) != report_path:
            raise ValueError('Attempt report path mismatch')
        validate_previous([row], output, [case], [seed])
        report = read(report_path)
        if report['source_identity'] != identity:
            raise ValueError('Attempt source identity mismatch')
        if row['status'] == 'QUALIFIED_EXPERT':
            trajectory = run_path(report['trajectory'])
            if not trajectory.is_relative_to(directory):
                raise ValueError('Trajectory outside attempt')
    elif row.get('status') != 'WORKER_ERROR':
        raise ValueError('Invalid worker result')
    return row


def scan(output, plan):
    qualified, pending, missing = [], [], []
    for case, sample in slots(plan):
        parent = slot_path(output, case, sample)
        history = []
        for attempt in range(plan['max_attempts']):
            seed = attempt_seed(plan, sample, attempt)
            directory = parent / f'attempt_{attempt + 1:02d}_seed{seed}'
            marker = directory.with_suffix('.job.json')
            if not marker.exists():
                pending.append((case, sample, seed, directory))
                break
            if read(marker) != {'case_id': case['case_id'], 'sample': sample, 'seed': seed}:
                raise ValueError('Attempt job identity mismatch')
            row = recover_attempt(directory, case, seed, output, plan['identity'])
            history.append(row)
            if row['status'] == 'QUALIFIED_EXPERT':
                qualified.append({**row, 'sample': sample + 1})
                break
        else:
            missing.append({'case_id': case['case_id'], 'sample': sample + 1, 'attempts': history})
    return qualified, pending, missing


def collect(output, plan):
    while True:
        qualified, pending, missing = scan(output, plan)
        if not pending:
            return qualified, missing
        with ProcessPoolExecutor(max_workers=plan['jobs'], mp_context=multiprocessing.get_context('spawn')) as pool:
            futures = {}
            for case, sample, seed, directory in pending:
                directory.parent.mkdir(parents=True, exist_ok=True)
                save(directory.with_suffix('.job.json'), {'case_id': case['case_id'], 'sample': sample, 'seed': seed})
                job = (case, seed, str(directory), plan['profile'], plan['case_timeout'], plan['identity'], False)
                futures[pool.submit(one_case, job)] = (case, seed, directory)
            for future in as_completed(futures):
                case, seed, directory = futures[future]
                try:
                    row = future.result()
                except Exception as error:
                    row = {'case_id': case['case_id'], 'seed': seed, 'status': 'WORKER_ERROR', 'error': repr(error)}
                save(directory.with_suffix('.result.json'), row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        # Failed attempts leave their sample slot unfilled. Next round changes seed.


def render(output, plan, qualified):
    from .replay import video
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=run_path, required=True)
    parser.add_argument('--suite', choices=('all', 'atomic', 'composite'), default='all')
    parser.add_argument('--case-ids', nargs='+')
    parser.add_argument('--samples-per-case', type=int, default=2)
    parser.add_argument('--max-attempts', type=int, default=3)
    parser.add_argument('--base-seed', type=int, default=0)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--case-timeout', type=float, default=1800.)
    parser.add_argument('--profile', choices=('research', 'fixed'), default='research')
    parser.add_argument('--camera', default='v3_front')
    parser.add_argument('--fps', type=int, default=15)
    parser.add_argument('--width', type=int, default=960)
    parser.add_argument('--height', type=int, default=540)
    parser.add_argument('--speed', type=float, default=4.)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args(argv)
    if not (1 <= args.samples_per_case <= 20 and 1 <= args.max_attempts <= 10 and 1 <= args.jobs <= 4
            and 30 <= args.case_timeout <= 3600 and 0 <= args.base_seed
            and args.base_seed + args.samples_per_case * args.max_attempts <= 2**32
            and 1 <= args.fps <= 60 and .25 <= args.speed <= 8
            and args.width > 0 and args.height > 0 and args.width % 2 == args.height % 2 == 0):
        parser.error('Invalid collection bounds or video settings; see --help')
    selected = cases(args.suite)
    if args.case_ids:
        if set(args.case_ids) - {c['case_id'] for c in selected}:
            parser.error('Unknown case ID in selected suite')
        selected = [c for c in selected if c['case_id'] in args.case_ids]
    plan = {'schema': 'kitchen_qualified_dataset_v1', 'cases': selected, 'identity': source_identity(),
            'runner_sha256': digest(__file__), 'samples_per_case': args.samples_per_case,
            'max_attempts': args.max_attempts, 'base_seed': args.base_seed, 'jobs': args.jobs,
            'case_timeout': args.case_timeout, 'profile': args.profile,
            'video': {k: getattr(args, k) for k in ('camera', 'width', 'height', 'fps', 'speed')}}
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.runner.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another coordinator is using this output')
        if (output / 'plan.json').exists():
            if not args.resume:
                parser.error('Existing run requires --resume; no data overwritten')
            if read(output / 'plan.json') != plan:
                parser.error('Plan/source changed; use a new output directory')
        else:
            if args.resume:
                parser.error('No plan to resume')
            if any(p.name != '.runner.lock' for p in output.iterdir()):
                parser.error('Output must be empty for a new plan')
            save(output / 'plan.json', plan)
        target = len(selected) * args.samples_per_case
        print(json.dumps({'cases': len(selected), 'target': target, 'output': str(output)}), flush=True)
        if args.plan_only:
            return 0
        if shutil.which('ffmpeg') is None:
            parser.error('ffmpeg unavailable')
        os.environ.setdefault('MUJOCO_GL', 'egl')
        qualified, missing = collect(output, plan)
        rows = render(output, plan, qualified)
        summary = {'target': target, 'qualified': len(qualified), 'videos': sum(bool(r.get('video')) for r in rows),
                   'missing': missing, 'video_failures': [r for r in rows if not r.get('video')]}
        summary['complete'] = summary['qualified'] == summary['videos'] == target
        replace_json(output / 'manifest.json', rows)
        replace_json(output / 'summary.json', summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if summary['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
