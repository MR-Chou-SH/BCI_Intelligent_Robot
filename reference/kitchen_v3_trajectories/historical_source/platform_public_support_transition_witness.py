"""Finite old-source/new-policy carrier validation through the real public API.

Every target is recovered from the sealed native source. Source and runtime
control identities stay separate: this is a new physical validation, not a
relabeled old-policy exact replay or a general task planner.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path
import subprocess
import time

from integration.kitchen import platform_public_physics_witness as base


AUTH = base.FULL / 'demo_research_20261022/AUTHORIZATION.md'
AUTH_SHA = '4d9baa229c52d2c78494201b26d48bfb429a9d114c4919e50b1cd3397a8d79e3'
SOURCE = base.FULL / 'carrier013_isolated_lift_01/report.json'


def audit(paths, plan, manifest, root):
    quality = base.CarrierQualification(plan['marks'])
    expected, episodes = 1, []
    for path in paths:
        path = Path(path).resolve()
        path.relative_to(root.resolve())

        def consume(row, metadata):
            if row['type'] == 'metadata':
                base.same_identity(manifest, row['manifest'])
            else:
                quality.sample(row, metadata)
                if row['controller_stopped']:
                    raise ValueError('REAL_CONTROLLER_STOP_IN_RECORD')

        result = base.scan_record(path, consume)
        if result['first_tick'] != expected:
            raise ValueError('PUBLIC_RECORD_GAP_OR_OVERLAP')
        expected = result['last_tick'] + 1
        end = result['end']
        if end.get('execution', {}).get('status') != 'COMPLETED':
            raise ValueError('PUBLIC_EXECUTION_DID_NOT_COMPLETE')
        if end.get('contact_feedback', {}).get('violations'):
            raise ValueError('REAL_CONTACT_POLICY_VIOLATION')
        result.pop('metadata')
        episodes.append(result)
    windows = quality.result()
    if expected != plan['end_tick'] + 1:
        raise ValueError('SELECTED_PHYSICAL_OPERATION_NOT_COMPLETE')
    if not all(windows[k]['pass_all'] for k in ('acquire', 'hold', 'carry', 'food', 'release')):
        raise ValueError('ACTUAL_PHYSICAL_QUALIFICATION_FAILED')
    return dict(status='PUBLIC_CARRIER_PHYSICAL_WINDOWS_PASS', windows=windows,
        physics_samples=expected - 1, episodes=episodes, queue_completion_is_task_success=False)


def run(args, output):
    if base.sha(AUTH) != AUTH_SHA:
        raise ValueError('SUPPLEMENTAL_AUTHORIZATION_MISMATCH')
    source = base.scoped(args.source_report)
    plan = base.prepare(source, 5)
    if plan['operation'] != 'carrier_lift' or plan['interface_preflight_blockers']:
        raise ValueError('FINITE_NATIVE_CARRIER_SOURCE_REQUIRED')
    base.save(output / 'source_plan.json', plan)
    source_manifest = plan['manifest']
    if args.expected_scene_hash != source_manifest['scene_hash']:
        raise ValueError('EXPECTED_SCENE_DIFFERS_FROM_NATIVE_SOURCE')
    lexical = base.FULL / output.relative_to(base.FULL.resolve())
    command = [base.PWSH, '-NoProfile', '-File', str(base.ROOT / 'scripts/start_kitchen_platform.ps1'),
        '-Layout', source_manifest['layout_id'], '-Seed', '0', '-Mode', 'manual',
        '-DataRoot', str(lexical / 'data'), '-ReadyFile', str(lexical / 'ready.json'),
        '-PlannerPort', '0', '-WorldPort', '0', '-BindHost', '127.0.0.1',
        '-RunSeconds', str(args.wall_seconds + 90.), '-PythonExe', base.PYTHON]
    result = dict(status='PUBLIC_SUPPORT_TRANSITION_NOT_RUN', launcher_command=command,
        source_manifest=source_manifest, source_report_sha256=base.sha(source),
        source_record=plan['source_record'], expected_runtime_control_hash=args.expected_control_hash,
        source_and_runtime_control_identity='EXPLICITLY_DIFFERENT_NOT_BITWISE_OLD_POLICY_REPLAY',
        supplemental_authorization_sha256=AUTH_SHA, physics_steps=0,
        full_microwave='NOT_VERIFIED', quest='HARDWARE_REQUIRED', canonical_promoted=False,
        client_wall_budget_s=args.wall_seconds, physical_limits='UNCHANGED',
        preview_policy='Explicit query at phase entry; every execute_trajectory still runs mandatory server preflight')
    client = process = None
    paths = []
    with (output / 'stdout.log').open('x', encoding='utf-8') as stdout, \
            (output / 'stderr.log').open('x', encoding='utf-8') as stderr:
        try:
            process = subprocess.Popen(command, cwd=base.ROOT, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            deadline = time.monotonic() + 25.
            ready_path = output / 'ready.json'
            while not ready_path.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            if not ready_path.exists():
                raise RuntimeError('REAL_LAUNCHER_READY_TIMEOUT')
            ready = base.load(ready_path)
            result['ready'] = ready
            if (ready['layout'] != source_manifest['layout_id'] or ready['seed'] != 0 or
                    ready['mode'] != 'manual' or
                    Path(ready['import_root']).resolve() != base.ROOT.resolve() or
                    Path(ready['data_root']).resolve() != (output / 'data').resolve()):
                raise ValueError('REAL_LAUNCHER_PARAMETER_MISMATCH')
            client = base.LoggedClient(ready['planner_port'], output)
            manifest = client.call('get_scene_manifest')
            base.validate_identity(manifest)
            for key in ('layout_id', 'scene_hash', 'compiled_model_hash', 'source_model_hash', 'layout_hash'):
                if manifest[key] != source_manifest[key]:
                    raise ValueError('PHYSICAL_MODEL_DIFFERS_FROM_SOURCE:' + key)
            if manifest['control_identity']['control_hash'] != args.expected_control_hash:
                raise ValueError('RUNTIME_CONTROL_HASH_NOT_EXPLICITLY_EXPECTED')
            result['runtime_manifest'] = manifest
            initial = client.call('observe')
            if initial['sim_tick'] != 0 or initial['reference16'] != plan['initial_reference16']:
                raise ValueError('NATIVE_RESET_NOT_SOURCE_INITIAL_STATE')
            deadline = time.monotonic() + args.wall_seconds
            previous_phase=None
            for segment in plan['segments']:
                if time.monotonic() > deadline:
                    raise RuntimeError('FINITE_PUBLIC_CARRIER_WALL_BUDGET')
                if client.client.token['sim_tick'] != segment['start_tick']:
                    raise ValueError('SOURCE_TARGET_CLOCK_MISMATCH')
                result['last_segment'] = dict(phase=segment['phase'], start_tick=segment['start_tick'],
                    end_tick=segment['end_tick'])
                values = dict(waypoints=segment['waypoints'], source='programmatic_validation')
                contract = copy.deepcopy(segment['contact_contract'])
                if contract is not None:
                    if contract['phase'] in ('carry', 'release'):
                        contract['target_support_transition'] = True
                    if contract['phase'] != previous_phase:
                        preview = client.call('check_contact_motion', dict(waypoints=segment['waypoints'],
                            contact_contract=contract))
                        result['last_preflight'] = preview
                        if not preview['pass']:
                            raise RuntimeError('CONTACT_PREFLIGHT_REJECTED:' + str(preview))
                    values['contact_contract'] = contract
                previous_phase=contract['phase'] if contract else None
                execution = client.call('execute_trajectory', values, mutating=True)
                paths.append(execution['evidence_path'])
                result['evidence_paths'] = list(paths)
                observation = client.call('step', dict(n_substeps=20 * len(segment['waypoints'])), mutating=True)
                result['physics_steps'] = client.client.token['sim_tick']
                status = client.call('get_execution_status', dict(execution_id=execution['id']))
                if observation['stopped'] or status['status'] != 'COMPLETED':
                    raise RuntimeError('REAL_PUBLIC_EXECUTION_STOPPED:' + str(status))
                if result['physics_steps'] != segment['end_tick']:
                    raise ValueError('SOURCE_END_CLOCK_MISMATCH')
                if segment['end_contact']:
                    result['contact_release'] = client.call('end_contact', mutating=True)
            final = client.call('observe')
            result['final_observation'] = final
            result['final_feedback'] = client.call('get_task_feedback', dict(object_ids=plan['payload_ids']))
            if final['stopped'] or final['contact_execution']['active']:
                raise ValueError('REAL_OPERATION_NOT_RELEASED')
            result['physical_audit'] = audit(paths, plan, manifest, output / 'data')
            result['status'] = 'PUBLIC_CARRIER_LIFT_HOLD_RELEASE_PHYSICS_PASS'
        except Exception as error:
            result.update(status='PUBLIC_SUPPORT_TRANSITION_BLOCKER', error=str(error))
            if client is not None:
                try:
                    result['failure_observation'] = client.call('observe')
                    result['cancelled_prefix'] = client.call('cancel', mutating=True)
                except Exception as cleanup:
                    result['cancel_error'] = str(cleanup)
        finally:
            if client is not None:
                client.close()
            if process is not None:
                try:
                    process.wait(timeout=3.)
                    result['owned_launcher_exit'] = process.returncode
                except subprocess.TimeoutExpired:
                    stopped = subprocess.run(['C:/Windows/System32/taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                        capture_output=True, text=True, timeout=15.,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    process.wait(timeout=10.)
                    result['owned_launcher_cleanup'] = dict(pid=process.pid, returncode=stopped.returncode,
                        stdout=stopped.stdout, stderr=stopped.stderr, graceful=False)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-report', type=Path, default=SOURCE)
    parser.add_argument('--expected-scene-hash', required=True)
    parser.add_argument('--expected-control-hash', required=True)
    parser.add_argument('--authorize-replay', action='store_true', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wall-seconds', type=float, default=600.)
    args = parser.parse_args()
    if not 60. <= args.wall_seconds <= 1200.:
        raise ValueError('FINITE_CLIENT_WALL_BUDGET_60_TO_1200_SECONDS_REQUIRED')
    output = base.guard(args.output)
    output.mkdir(exist_ok=False)
    result = run(args, output)
    base.save(output / 'report.json', result)
    print(result['status'], result.get('physics_steps'), flush=True)
    raise SystemExit(0 if result['status'] == 'PUBLIC_CARRIER_LIFT_HOLD_RELEASE_PHYSICS_PASS' else 1)


if __name__ == '__main__':
    main()
