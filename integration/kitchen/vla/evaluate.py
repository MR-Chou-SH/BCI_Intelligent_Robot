"""Sequential held-out L1 evaluation against an independently running service."""
import argparse
from datetime import datetime
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import subprocess

from integration.kitchen.data.files import digest, read, replace_json
from integration.kitchen.launch import ROOT, run_path
from .dataset import trajectory_paths
from .images import camera_rig

SCRIPT = ROOT/'scripts/kitchen_smolvla.sh'


def stop_process(process):
    """Only terminate the rollout group created by this batch, including ffmpeg."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def training_identity(checkpoint, seeds):
    """Obtain actual training seeds from the checkpoint's dataset provenance."""
    contract = read(checkpoint/'kitchen_policy.json')
    description_path = Path(contract['dataset'])/'kitchen_export.json'
    description = read(description_path)
    training_seeds = set()
    for episode in description['source_episodes']:
        report = read(Path(episode['source']).with_name('report.json'))
        if report['verification']['sha256'] != episode['source_sha256']:
            raise ValueError('Training source provenance changed')
        if report['initialization_profile'] == 'research':
            training_seeds.add(report['seed'])
    overlap = set(seeds) & training_seeds
    if overlap:
        raise ValueError(f'Evaluation seeds occur in training: {sorted(overlap)}')
    files = {p.name: digest(p) for p in sorted(checkpoint.iterdir()) if p.is_file()}
    return {'training_seeds': sorted(training_seeds), 'dataset_export_sha256': digest(description_path),
            'checkpoint_files': files, 'model_sha256': files['model.safetensors'],
            'camera_rig': camera_rig(contract.get('camera_rig', contract['camera']))}


def trial(command, directory, seed, model_sha, timeout):
    directory.parent.mkdir(parents=True, exist_ok=True)
    # The rollout creates directory itself. Preserve subprocess output beside it.
    log = directory.with_suffix('.log')
    with log.open('x') as stream:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=timeout)
            error = None
        except subprocess.TimeoutExpired:
            error = f'Episode exceeded wall-clock budget {timeout}s'
            code = None
        finally:
            stop_process(process)
    path = directory/'report.json'
    try:
        report = read(path) if path.is_file() else {}
    except (ValueError, OSError) as cause:
        report = {}
        error = f'Invalid episode report: {cause}'
    if error is None:
        if (report.get('initial_seed') != seed
                or report.get('initial_state_mode') != 'fresh_research_seed'):
            error = 'Missing report or evaluation initial-state identity mismatch'
        elif report.get('status') == 'POLICY_ERROR':
            error = report.get('error') or 'Policy interface error'
        elif (report.get('policy') or {}).get('model_sha256') != model_sha:
            error = 'Evaluated model differs from requested checkpoint'
        elif report.get('status') == 'POLICY_SUCCESS' and code == 0 and report.get('task_success') is True:
            if report.get('guard_violations'):
                error = 'Inconsistent success with guard violations'
        elif not (report.get('status') == 'POLICY_FAILED' and code == 1 and report.get('task_success') is False):
            error = f'Unexpected rollout status/exit code: {report.get("status")}/{code}'
    status = 'ERROR' if error else ('SUCCESS' if report['task_success'] else 'FAILED')
    return {'seed': seed, 'status': status, 'error': error, 'exit_code': code,
            'report': str(path) if path.is_file() else None,
            'report_sha256': digest(path) if path.is_file() else None,
            'log': str(log), 'simulated_seconds': report.get('simulated_seconds'),
            'guard_violations': len(report.get('guard_violations', [])),
            'video': report.get('video'), 'video_error': report.get('video_error')}


def summarize(output, seeds, rows, *, status='RUNNING', error=None):
    counts = {name: sum(r['status'] == name for r in rows) for name in ('SUCCESS', 'FAILED', 'ERROR')}
    result = {'schema': 'kitchen_policy_batch_summary_v1', 'status': status, 'error': error,
              'requested': len(seeds), 'completed': len(rows), 'pending': len(seeds)-len(rows),
              'success': counts['SUCCESS'], 'failed': counts['FAILED'], 'errors': counts['ERROR'],
              'success_rate': counts['SUCCESS']/len(seeds),
              'success_rate_among_valid': counts['SUCCESS']/(counts['SUCCESS']+counts['FAILED'])
                  if counts['SUCCESS']+counts['FAILED'] else None,
              'seeds': seeds, 'results': rows}
    replace_json(output/'summary.json', result)
    return result


def evaluate(args):
    run = args.run
    checkpoint = (args.checkpoint or run/'train/checkpoint_010000').resolve()
    manifest = args.manifest or run/'collection/manifest.json'
    source = trajectory_paths(manifest=manifest)[0]
    from integration.kitchen.data.replay import read_compact
    from integration.kitchen.data.collect_experts import source_identity
    data = read_compact(source)
    if data['task']['task'] != 'L1' or read(source.with_name('report.json'))['initialization_profile'] != 'research':
        raise ValueError('Batch fresh-state evaluation currently requires research L1')
    seeds = list(range(args.seed_start, args.seed_start+args.episodes))
    identity = training_identity(checkpoint, seeds)
    output = args.output or run/f'eval_batch_{datetime.now():%Y%m%d_%H%M%S_%f}'
    plan = {'schema': 'kitchen_policy_batch_plan_v1', 'source': str(source), 'source_sha256': digest(source),
            'checkpoint': str(checkpoint), 'seeds': seeds, 'seconds': args.seconds, 'clock': args.clock,
            'replan_remaining': args.replan_remaining,
            'host': args.host, 'port': args.port, 'video': args.video, 'audit': args.audit,
            'episode_timeout': args.episode_timeout, 'identity': identity,
            'simulation_identity': source_identity(),
            'adapter_sources': {p.name: digest(p) for p in Path(__file__).parent.glob('*.py')}}
    if args.resume and not (output/'plan.json').is_file():
        raise ValueError('--resume requires an existing batch plan and explicit --output')
    output.mkdir(parents=True, exist_ok=args.resume)
    with (output/'.evaluation.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.resume:
            if read(output/'plan.json') != plan:
                raise ValueError('Checkpoint, inputs or evaluation settings changed; use a new batch')
        else:
            replace_json(output/'plan.json', plan)
            replace_json(output/'camera_rig.json', identity['camera_rig'])
        if camera_rig(output/'camera_rig.json') != identity['camera_rig']:
            raise ValueError('Batch camera rig changed')
        rows = []
        pending = []
        for seed in seeds:
            receipt = output/f'seed_{seed}'/'result.json'
            if receipt.exists():
                row = read(receipt)
                if row['seed'] != seed or row['status'] not in ('SUCCESS', 'FAILED', 'ERROR'):
                    raise ValueError('Invalid sealed evaluation receipt')
                if row.get('report') and digest(row['report']) != row['report_sha256']:
                    raise ValueError('Sealed evaluation report changed')
                rows.append(row)
            else:
                pending.append(seed)
        summarize(output, seeds, rows)
        print(json.dumps({'output': str(output), 'requested': len(seeds), 'reused': len(rows)}), flush=True)
        try:
            if pending:
                for seed in pending:
                    parent = output/f'seed_{seed}'
                    parent.mkdir(exist_ok=True)
                    # Preserve partial attempts if the user interrupted a rollout.
                    attempt = 1
                    while (parent/f'attempt_{attempt:02d}').exists() or (parent/f'attempt_{attempt:02d}.log').exists():
                        attempt += 1
                    directory = parent/f'attempt_{attempt:02d}'
                    command = ['bash', str(SCRIPT), 'rollout', '--trajectory', str(source),
                        '--initial-seed', str(seed), '--output', str(directory),
                        '--host', args.host, '--port', str(args.port), '--seconds', str(args.seconds),
                        '--clock', args.clock, '--replan-remaining', str(args.replan_remaining),
                        '--camera-rig', str(output/'camera_rig.json')]
                    if args.video:
                        command.append('--video')
                    if args.audit:
                        command.append('--audit')
                    row = trial(command, directory, seed, identity['model_sha256'], args.episode_timeout)
                    replace_json(parent/'result.json', row)
                    rows.append(row)
                    rows.sort(key=lambda r: r['seed'])
                    summarize(output, seeds, rows)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
            result = summarize(output, seeds, rows, status='COMPLETE')
        except BaseException as error:
            summarize(output, seeds, rows, status='INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'ERROR',
                      error=repr(error))
            raise
    print(json.dumps({'output': str(output), **{k: result[k] for k in (
        'requested', 'completed', 'success', 'failed', 'errors', 'success_rate')}}, ensure_ascii=False), flush=True)
    return 2 if result['errors'] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=run_path, required=True, help='Training run containing collection/ and train/')
    parser.add_argument('--checkpoint', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=run_path, help='Default: fresh timestamped directory under --run')
    parser.add_argument('--seed-start', type=int, default=10000)
    parser.add_argument('--episodes', type=int, default=10)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--clock', choices=('lockstep', 'realtime'), default='realtime')
    parser.add_argument('--replan-remaining', type=int, default=40, help='With50-step chunks,40 requests new observations every10 steps')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--video', action='store_true')
    parser.add_argument('--audit', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--episode-timeout', type=float, default=600)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if (not 1 <= args.episodes <= 1000 or not 0 <= args.seed_start <= 2**32-args.episodes
            or not 1 <= args.port <= 65535 or not 1 <= args.replan_remaining < 50
            or args.resume and args.output is None
            or any(not math.isfinite(x) or x <= 0 for x in
                   (args.seconds, args.episode_timeout))):
        parser.error('Invalid seeds, episode count, positive budgets or resume output')
    return evaluate(args)


if __name__ == '__main__':
    raise SystemExit(main())
