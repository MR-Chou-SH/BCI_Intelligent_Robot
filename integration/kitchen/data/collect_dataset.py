"""Collect N qualified, replayable state/action examples per concrete task. No rendering.

Attempt directories are immutable. Missing reports after an interruption consume
an attempt; sealed reports are recovered without repeating simulation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import fcntl
import json
import multiprocessing
from pathlib import Path

from .collect_experts import one_case, save, source_identity, validate_previous,identity_sha256
from integration.kitchen.tasks.catalog import cases
from integration.kitchen.launch import run_path


from .files import digest,read,replace_json


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
        if report.get('source_identity') != identity and report.get('source_identity_sha256')!=identity_sha256(identity):
            raise ValueError('Attempt source identity mismatch')
        if row['status'] == 'QUALIFIED_EXPERT':
            trajectory = run_path(report['trajectory'])
            if not trajectory.is_relative_to(directory):
                raise ValueError('Trajectory outside attempt')
            from .optimized_collection import validate_backup_receipt
            validate_backup_receipt(report,directory)
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
            if row['status']=='INTERRUPTED' and plan.get('postprocessing',{}).get('mode') in ('continuous','adaptive'):
                backup=directory/'backup/report.json'
                if backup.exists() and read(backup).get('status')=='QUALIFIED_EXPERT':
                    pending.append((case,sample,seed,directory));break
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
        with ProcessPoolExecutor(max_workers=plan['jobs'], mp_context=multiprocessing.get_context('spawn'),max_tasks_per_child=1) as pool:
            futures = {}
            for case, sample, seed, directory in pending:
                directory.parent.mkdir(parents=True, exist_ok=True)
                marker=directory.with_suffix('.job.json')
                if not marker.exists():save(marker, {'case_id': case['case_id'], 'sample': sample, 'seed': seed})
                job = (case, seed, str(directory), plan['profile'], plan['case_timeout'], plan['identity'], False,plan.get('motion_profile','standard'))
                settings=plan.get('postprocessing',{})
                if settings.get('mode') in ('continuous','adaptive'):
                    from .optimized_collection import optimized_case
                    future=pool.submit(optimized_case,job,settings)
                else:future=pool.submit(one_case, job)
                futures[future] = (case, seed, directory)
            for future in as_completed(futures):
                case, seed, directory = futures[future]
                try:
                    row = future.result()
                except Exception as error:
                    row = {'case_id': case['case_id'], 'seed': seed, 'status': 'WORKER_ERROR', 'error': repr(error)}
                save(directory.with_suffix('.result.json'), row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        # Failed attempts leave their sample slot unfilled. Next round changes seed.



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
    parser.add_argument('--motion-profile',choices=('standard','faster'),default='standard')
    parser.add_argument('--postprocess',choices=('continuous','adaptive','none'),default='continuous')
    parser.add_argument('--optimization-factors',nargs='+',type=float)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args(argv)
    if args.optimization_factors is None:args.optimization_factors=[4.] if args.postprocess=='adaptive' else [3.,5.]
    if args.postprocess=='adaptive' and args.optimization_factors!=[4.]:
        parser.error('adaptive starts at4x and derives local repairs; use --optimization-factors 4')
    from integration.kitchen.planning.action_retiming import MAX_FACTOR
    if (not 1<=len(args.optimization_factors)<=4 or len(set(args.optimization_factors))!=len(args.optimization_factors)
        or any(not 1<f<=MAX_FACTOR for f in args.optimization_factors)):
        parser.error('Use 1..4 unique optimization factors >1 and <=5')
    if not (1 <= args.samples_per_case <= 1000 and 1 <= args.max_attempts <= 10 and 1 <= args.jobs <= 4
            and 30 <= args.case_timeout <= 3600 and 0 <= args.base_seed
            and args.base_seed + args.samples_per_case * args.max_attempts <= 2**32):
        parser.error('Invalid collection bounds; see --help')
    selected = cases(args.suite)
    if args.case_ids:
        if set(args.case_ids) - {c['case_id'] for c in selected}:
            parser.error('Unknown case ID in selected suite')
        selected = [c for c in selected if c['case_id'] in args.case_ids]
    plan = {'schema': 'kitchen_qualified_dataset_v1', 'cases': selected, 'identity': source_identity(),
            'runner_sha256': digest(__file__), 'samples_per_case': args.samples_per_case,
            'max_attempts': args.max_attempts, 'base_seed': args.base_seed, 'jobs': args.jobs,
            'case_timeout': args.case_timeout, 'profile': args.profile, 'format':'kitchen_state_actions_v3','motion_profile':args.motion_profile,
            'postprocessing':{'mode':args.postprocess,'factors':args.optimization_factors}}
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
        qualified, missing = collect(output, plan)
        rows=[]
        for r in qualified:
            report=read(r['report'])
            annotation={k:report[k] for k in ('annotations','annotations_sha256') if k in report}
            post=report.get('postprocessing',{})
            rows.append({**r,'trajectory':report['trajectory'],**annotation,
                'postprocessing_status':post.get('status','NOT_REQUESTED'),
                'source_group':post.get('source_group',report['verification']['sha256'])})
        summary={'target':target,'qualified':len(qualified),'missing':missing,'complete':len(qualified)==target,
                 'format':'kitchen_state_actions_v3','images_included':False}
        summary['optimized']=sum(r['postprocessing_status']=='OPTIMIZED' for r in rows)
        summary['original_fallback']=sum(r['postprocessing_status']=='ORIGINAL_FALLBACK' for r in rows)
        replace_json(output / 'manifest.json', rows)
        replace_json(output / 'summary.json', summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        return 0 if summary['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

