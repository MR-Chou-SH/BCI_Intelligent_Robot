"""Finite successful-component replay through the real public launcher/socket.

Preparation is read-only physics-record analysis, never simulation or task PASS.
No controller/KitchenPlatform instance or internal dynamics is used by this client.
The door component may load the exact source MJB for scratch kinematics only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import socket
import subprocess
import sys
import time

import mujoco
import numpy as np

from integration.kitchen.planner_client import PlannerClient
from robot_arm.kitchen.registry import digest
from robot_arm.kitchen.devices import (SUPPORT_CENTER_CLEARANCE_M,
    SUPPORT_FORCE_THRESHOLD_N, SUPPORT_NORMAL_MIN_UPWARD_Z)

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ROOT = Path('<ORIGINAL_WORKTREE>')
FULL = ROOT / 'experiments/kitchen_platform_v3/20261021T013529/fullops_20261021'
PLAN = ROOT / 'docs/kitchen_platform_v3_20261020/APPROVED_PLAN.md'
AUTH = FULL / 'AUTHORIZATION.md'
PLAN_SHA = '135b22143a15d5082717c04345072d1568481abe0d8a419140a1a13f3a4fd2d3'
AUTH_SHA = '1c2cb757610535066725c3aa1006fab09c3e96089c5c3103df6add761ab722b3'
GOAL = '01a15914-fce0-7501-9b2f-cf19e1690c24'
PYTHON = 'D:/VLA_Kitchen_Data/.envs/kitchen-sim/Scripts/python.exe'
PWSH = '<ORIGINAL_PWSH>'
IDENTITY_KEYS = ('layout_id', 'scene_hash', 'compiled_model_hash', 'control_identity')
MAX_LINE = 2 * 1024 * 1024


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    with Path(path).open(encoding='utf-8-sig') as stream:
        return json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def scoped(path):
    path = Path(path).resolve()
    path.relative_to(FULL.resolve())
    return path


def guard(output):
    if ROOT.resolve() != EXPECTED_ROOT.resolve() or sha(PLAN) != PLAN_SHA or sha(AUTH) != AUTH_SHA:
        raise ValueError('AUTHORIZED_WORKTREE_OR_PROTOCOL_MISMATCH')
    pointer = load(ROOT / '.codex/ACTIVE_GOAL.json')
    if pointer['current_goal_id'] != GOAL or pointer['status'] != 'active':
        raise ValueError('SAME_ACTIVE_GOAL_REQUIRED')
    if (ROOT / pointer['current_run_directory']).resolve() != FULL.parent.resolve():
        raise ValueError('ACTIVE_RUN_MISMATCH')
    output = scoped(output)
    if output.parent != FULL.resolve() or not output.name.startswith('api_'):
        raise ValueError('NEW_DIRECT_FULLOPS_API_DIRECTORY_REQUIRED')
    return output


def provenance():
    return dict(goal_id=GOAL, worktree=str(ROOT), protocol_path=str(PLAN), protocol_sha256=PLAN_SHA,
        authorization_path=str(AUTH), authorization_sha256=AUTH_SHA,
        source_path=str(Path(__file__).resolve()), source_sha256=sha(__file__),
        command=[sys.executable, '-B', '-m', 'integration.kitchen.platform_public_physics_witness', *sys.argv[1:]],
        task_success='NOT_INFERRED_FROM_QUEUE', real_hardware='NOT_ACCESSED', canonical_promoted=False)


def validate_identity(manifest):
    if not isinstance(manifest, dict):
        raise ValueError('IDENTITY_MANIFEST_REQUIRED')
    layout = manifest.get('layout_id')
    if not isinstance(layout, str) or not layout.strip():
        raise ValueError('NONEMPTY_LAYOUT_ID_REQUIRED')
    control = manifest.get('control_identity')
    hashes = {key: manifest.get(key) for key in ('scene_hash', 'compiled_model_hash')}
    hashes['control_hash'] = control.get('control_hash') if isinstance(control, dict) else None
    for key, value in hashes.items():
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in '0123456789abcdef' for c in value.lower())
                or value.lower() == hashlib.sha256(b'').hexdigest()):
            raise ValueError('INVALID_OR_EMPTY_IDENTITY_HASH:' + key)


def same_identity(expected, actual):
    validate_identity(expected)
    validate_identity(actual)
    mismatches = [key for key in IDENTITY_KEYS if expected.get(key) != actual.get(key)]
    if mismatches:
        raise ValueError('SCENE_CONTROL_IDENTITY_MISMATCH:' + ','.join(mismatches))


def scan_record(path, consume, *, allow_source_headers=False, selected_end_tick=None):
    """Validate the entire immutable chain, even when only a prefix is selected."""
    path = scoped(path)
    before = path.stat()
    if before.st_size > 2 * 1024**3:
        raise ValueError('BOUNDED_RECORD_SIZE_EXCEEDED')
    previous, metadata, end, count, first, last = '0' * 64, None, None, 0, None, None
    source_headers = []
    source_notes = []
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            raw = stream.readline(MAX_LINE + 1)
            if not raw:
                break
            h.update(raw)
            if len(raw) > MAX_LINE or not raw.endswith(b'\n'):
                raise ValueError('INCOMPLETE_OR_OVERSIZED_RECORD_LINE')
            envelope = json.loads(raw)
            signature = envelope.pop('sha256')
            if envelope['previous_sha256'] != previous or digest(envelope) != signature:
                raise ValueError('PHYSICS_RECORD_CHAIN_INVALID')
            previous = signature
            row = envelope['record']
            if end is not None:
                raise ValueError('RECORD_AFTER_END')
            if row['type'] == 'metadata':
                if metadata is not None or count:
                    raise ValueError('DUPLICATE_OR_LATE_METADATA')
                validate_identity(row.get('manifest'))
                metadata = row
                consume(row, metadata)
            elif row['type'] == 'physics':
                if metadata is None:
                    raise ValueError('METADATA_REQUIRED')
                tick = row['sim_tick']
                if type(tick) is not int or (last is not None and tick != last + 1):
                    raise ValueError('PHYSICS_TICK_GAP')
                first = tick if first is None else first
                last, count = tick, count + 1
                if count > 90000:
                    raise ValueError('FINITE_180S_RECORD_BOUND_EXCEEDED')
                consume(row, metadata)
            elif allow_source_headers and row['type'] in ('finite_continuation', 'finite_inputs'):
                expected_headers = ('finite_continuation', 'finite_inputs')
                if (metadata is None or count or len(source_headers) >= 2
                        or row['type'] != expected_headers[len(source_headers)]):
                    raise ValueError('INVALID_SOURCE_PROVENANCE_HEADER_ORDER')
                if row['type'] == 'finite_continuation':
                    if (set(row) != {'type', 'source_record_audit', 'start_semantics'} or
                            row['start_semantics'] != 'geometry reference only; runtime starts from reset and runs every physical phase continuously'):
                        raise ValueError('SOURCE_DOES_NOT_DECLARE_NATIVE_RESET')
                elif (set(row) != {'type', 'opening_sha256', 'continuation_sha256'} or
                        any(not isinstance(row[k], str) or len(row[k]) != 64 or
                            any(c not in '0123456789abcdef' for c in row[k])
                            for k in ('opening_sha256', 'continuation_sha256'))):
                    raise ValueError('INVALID_FINITE_INPUT_FINGERPRINTS')
                source_headers.append(row)
            elif allow_source_headers and row['type'] == 'button_preload_preflight':
                if (selected_end_tick is None or last is None or last <= selected_end_tick or source_notes
                        or set(row) != {'type', 'target_m', 'tcp_additional_preload_m', 'preflight', 'scope'}
                        or not isinstance(row['preflight'], dict) or row['preflight'].get('pass') is not True):
                    raise ValueError('UNEXPECTED_BUTTON_NOTE_INSIDE_SELECTED_DOOR_COMPONENT')
                source_notes.append(row)
            elif row['type'] == 'end':
                end = row
            else:
                raise ValueError('UNKNOWN_PHYSICS_RECORD_TYPE')
    if (metadata is None or end is None or not count or end['sampling_failed']
            or end['physics_samples'] != count or end['last_recorded_tick'] != last):
        raise ValueError('INCOMPLETE_PHYSICS_EVIDENCE')
    if allow_source_headers and len(source_headers) != 2:
        raise ValueError('BOTH_FROZEN_SOURCE_PROVENANCE_HEADERS_REQUIRED')
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('SOURCE_RECORD_CHANGED_DURING_READ')
    result = dict(path=str(path), sha256=h.hexdigest(), final_chain_sha256=previous,
        first_tick=first, last_tick=last, samples=count, metadata=metadata, end=end)
    if source_headers:
        result['source_headers'] = source_headers
    if source_notes:
        result['excluded_later_source_notes'] = source_notes
    return result


class ExactWindow:
    def __init__(self, start, end):
        self.start, self.end, self.count, self.bad = start, end, 0, []

    def sample(self, tick, good):
        if self.start < tick <= self.end:
            self.count += 1
            if not good and len(self.bad) < 8:
                self.bad.append(tick)

    def result(self):
        return dict(pass_all=not self.bad and self.count == self.end - self.start,
            start_tick=self.start, end_tick=self.end, samples=self.count, first_bad_ticks=self.bad,
            duration_s=(self.end - self.start) * .002)


def apple_measurement(row, contract):
    names = contract['body_names_by_id']
    body = names.index('apple')
    fingers = [names.index('left_umi_' + name + '_finger_holder') for name in ('left', 'right')]
    basket = contract['geom_names_by_id'].index('basket_bottom')
    state = row['object_pose_velocity'][contract['object_names'].index('apple')]
    forces, support = [0., 0.], 0.
    for c in row['contacts']:
        if len(c) != 23 or not all(math.isfinite(v) for v in c):
            raise ValueError('INVALID_NATIVE_CONTACT_RECORD')
        a, ba, b, bb = map(int, c[:4])
        other = bb if ba == body else ba if bb == body else None
        if other in fingers:
            forces[fingers.index(other)] += max(0., c[17])
        if ba == body or bb == body:
            g = b if ba == body else a
            upward = (-1. if ba == body else 1.) * c[10]
            if (g == basket and upward * c[17] > SUPPORT_FORCE_THRESHOLD_N and
                upward > SUPPORT_NORMAL_MIN_UPWARD_Z and c[7] < state[2] + SUPPORT_CENTER_CLEARANCE_M):
                support += upward * c[17]
    linear = math.sqrt(sum(v * v for v in state[10:13]))
    angular = math.sqrt(sum(v * v for v in state[7:10]))
    return dict(position_m=state[:3], pad_normal_forces_n=forces, bilateral=min(forces) >= .5,
        basket_upward_normal_n=support, linear_m_s=linear, angular_rad_s=angular,
        controller_stopped=bool(row['controller_stopped']),
        released_stable=bool(support > SUPPORT_FORCE_THRESHOLD_N and max(forces) < .5
            and linear <= .02 and angular <= .05 and not row['controller_stopped']))


class AppleQualification:
    def __init__(self, marks):
        self.marks, self.initial_z = marks, None
        self.hold = ExactWindow(marks['first_held_1s'] - 500, marks['first_held_1s'])
        self.release = ExactWindow(marks['basket_stable'] - 1000, marks['basket_stable'])
        self.boundaries = {}

    def sample(self, row, metadata):
        tick = row['sim_tick']
        q = apple_measurement(row, metadata['physics_contract'])
        if tick == self.marks['initial_settled']:
            self.initial_z = q['position_m'][2]
        if self.initial_z is not None:
            self.hold.sample(tick, q['bilateral'] and q['position_m'][2] - self.initial_z >= .03
                and not q['controller_stopped'])
        self.release.sample(tick, q['released_stable'])
        if tick in self.marks.values():
            q['left_tcp_position_m'] = row['tcp_pose'][0][:3]
            self.boundaries[str(tick)] = q

    def result(self):
        return dict(hold=self.hold.result(), release=self.release.result(), boundaries=self.boundaries)


class FrameExtractor:
    def __init__(self, manifest, end_tick, arm='left'):
        self.manifest, self.end_tick, self.frames = manifest, end_tick, []
        self.initial = self.previous = self.current = None
        self.closing_start = None
        self.tendon_index = {'left': 7, 'right': 15}[arm]

    def sample(self, row):
        tick = row['sim_tick']
        if tick > self.end_tick:
            return
        if row['controller_stopped']:
            raise ValueError('SELECTED_SOURCE_PREFIX_HAS_CONTROLLER_STOP')
        values = row['accepted_target16']
        if len(values) != 16 or not all(math.isfinite(v) for v in values):
            raise ValueError('EXPECTED_16_FINITE_ACCEPTED_TARGETS')
        if tick % 20 == 1:
            if self.previous is None:
                if tick != 1:
                    raise ValueError('REPLAY_MUST_BEGIN_AT_RESET_NO_STATE_INJECTION')
                self.initial = list(row['requested_reference16'])
                if len(self.initial) != 16 or not all(math.isfinite(v) for v in self.initial):
                    raise ValueError('EXPECTED_16_FINITE_RESET_REFERENCES')
                self.previous = self.initial
            for side, offset in (('left', 0), ('right', 8)):
                lower, upper = self.manifest['robots'][side]['joint_control_limits_rad']
                for k in range(7):
                    if not lower[k] <= values[offset + k] <= upper[k]:
                        raise ValueError('SOURCE_TARGET_OUTSIDE_NATIVE_JOINT_LIMIT')
            if any(abs(a - b) > (.002 if i in (7, 15) else .7 / 25) + 1e-9
                   for i, (a, b) in enumerate(zip(values, self.previous))):
                raise ValueError('SOURCE_ACCEPTED_TARGETS_EXCEED_NATIVE_25HZ_CAP')
            i = self.tendon_index
            if self.closing_start is None and values[i] > self.previous[i] + 1e-10:
                self.closing_start = tick - 1
            self.current = list(values)
        if self.current != values:
            raise ValueError('ACCEPTED_TARGET_CHANGED_WITHIN_20_SUBSTEPS')
        if tick % 20 == 0:
            self.frames.append(dict(end_tick=tick, target16=self.current))
            self.previous = self.current


def split_segments(frames, acquire_start, marks, contact_batch=5):
    if not 1 <= contact_batch <= 50:
        raise ValueError('API_CONTACT_BUDGET_MUST_NOT_BE_RELAXED')
    boundaries = [0, acquire_start, marks['first_grasp'], marks['basket_supported_before_open'],
                  marks['basket_opened'], marks['basket_stable']]
    if boundaries != sorted(set(boundaries)) or any(t % 20 for t in boundaries):
        raise ValueError('ALIGNED_ORDERED_CONTACT_PHASE_MARKERS_REQUIRED')
    result = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
        phase = ('transit', 'acquire', 'carry', 'release', 'transit')[index]
        contract = None if phase == 'transit' else dict(type='grasp', arm='left', target='apple',
            phase=phase, payload_ids=['apple'], supports={'apple': ['v3_counter_left', 'basket_bottom']})
        period = 50 if contract is None else contact_batch
        selected = [f for f in frames if start < f['end_tick'] <= end]
        for offset in range(0, len(selected), period):
            batch = selected[offset:offset + period]
            result.append(dict(phase=phase, start_tick=batch[0]['end_tick'] - 20,
                end_tick=batch[-1]['end_tick'], waypoints=[f['target16'] for f in batch],
                contact_contract=contract, end_contact=phase == 'release' and offset + period >= len(selected)))
    return result


def prepare_apple(report_path, contact_batch):
    report_path = scoped(report_path)
    report = load(report_path)
    required = ('first_bilateral_grasp_50ms', 'lift_30mm_hold_1s', 'basket_released_stable_2s')
    if not all(report.get('checks', {}).get(k) is True for k in required):
        raise ValueError('SUCCESSFUL_APPLE_BASKET_COMPONENT_REQUIRED_NO_FAILED_CHAIN_REPLAY')
    if (not report.get('evidence_complete') or report.get('object_pose_writes_during_execution') != 0
            or report.get('added_constraints') != 0):
        raise ValueError('COMPLETE_UNASSISTED_PHYSICS_EVIDENCE_REQUIRED')
    wanted = ('initial_settled', 'first_grasp', 'first_held_1s', 'basket_supported_before_open',
              'basket_opened', 'basket_withdrawn', 'basket_stable')
    marks = {row['phase']: row['tick'] for row in report['trace'] if row['phase'] in wanted}
    if set(marks) != set(wanted):
        raise ValueError('EXPLICIT_SUCCESSFUL_PREFIX_MARKERS_REQUIRED')
    manifest = report['identity']['manifest']
    extractor, quality = FrameExtractor(manifest, marks['basket_stable']), AppleQualification(marks)

    def consume(row, metadata):
        if row['type'] == 'metadata':
            same_identity(manifest, row['manifest'])
            clock = row['physics_contract']
            if clock['sampling_hz'] != 500 or clock['substeps_per_observation'] != 20 or clock['time_step_s'] != .002:
                raise ValueError('NATIVE_500HZ_25HZ_RECORD_REQUIRED')
        elif row['sim_tick'] <= marks['basket_stable']:
            extractor.sample(row)
            quality.sample(row, metadata)
    record = scan_record(report['evidence_path'], consume)
    if record['samples'] != report['physics_samples'] or len(extractor.frames) * 20 != marks['basket_stable']:
        raise ValueError('SOURCE_REPORT_RECORD_OR_PREFIX_COUNT_MISMATCH')
    qualified = quality.result()
    if not qualified['hold']['pass_all'] or not qualified['release']['pass_all']:
        raise ValueError('RAW_RECORD_DOES_NOT_CONFIRM_COMPLETE_COMPONENT_WINDOWS')
    segments = split_segments(extractor.frames, extractor.closing_start, marks, contact_batch)
    blockers = source_phase_blockers(marks, qualified)
    record.pop('metadata')
    return dict(schema=1, status='SUCCESSFUL_SOURCE_COMPONENT_PREPARED_NOT_REPLAYED', operation='apple_basket',
        end_tick=marks['basket_stable'], payload_ids=['apple'],
        manifest=manifest, marks=marks, initial_reference16=extractor.initial,
        source_report=str(report_path), source_report_sha256=sha(report_path), source_record=record,
        source_full_status=report['status'], source_full_reason=report['reason'],
        explicitly_excluded_source_ticks_after=marks['basket_stable'], source_component_checks=qualified,
        segments=segments, contact_batch=contact_batch, interface_preflight_blockers=blockers,
        eligible_for_exact_public_replay=not blockers,
        extraction='One unchanged accepted_target16 per complete native 20-substep frame; no averaging, IK, resampling or rate-limit bypass',
        dynamics_executed=False, final_candidate_identity='NOT_AUTHORIZED_BY_PREPARATION', **provenance())


def carrier_measurement(row, contract):
    bodies = contract['body_names_by_id']
    target = bodies.index('burger_container')
    fingers = [bodies.index('right_umi_' + s + '_finger_holder') for s in ('left', 'right')]
    names = ('burger_container', 'burger')
    states = {n: row['object_pose_velocity'][contract['object_names'].index(n)] for n in names}
    support_ids = {contract['geom_names_by_id'].index(g): n for n, g in
                   (('burger_container', 'v3_counter_right'), ('burger', 'burger_plate'))}
    owners = {bodies.index(n): n for n in names}
    forces, supports, pad_contacts = [0., 0.], dict.fromkeys(names, 0.), 0
    for c in row['contacts']:
        if len(c) != 23 or not all(math.isfinite(v) for v in c):
            raise ValueError('INVALID_NATIVE_CONTACT_RECORD')
        a, ba, b, bb = map(int, c[:4])
        other = bb if ba == target else ba if bb == target else None
        if other in fingers:
            forces[fingers.index(other)] += max(0., c[17])
        if (ba in fingers and bb in owners) or (bb in fingers and ba in owners):
            pad_contacts += 1
        for body, name in owners.items():
            if ba != body and bb != body:
                continue
            geom = b if ba == body else a
            upward = (-1. if ba == body else 1.) * c[10]
            vertical = upward * c[17]
            if (support_ids.get(geom) == name and upward > SUPPORT_NORMAL_MIN_UPWARD_Z
                    and vertical > SUPPORT_FORCE_THRESHOLD_N
                    and c[7] < states[name][2] + SUPPORT_CENTER_CLEARANCE_M):
                supports[name] = max(supports[name], vertical)
    speeds = {n: dict(linear_m_s=math.sqrt(sum(v*v for v in s[10:13])),
                     angular_rad_s=math.sqrt(sum(v*v for v in s[7:10]))) for n, s in states.items()}
    tcp = row['tcp_pose'][1]
    rt, ro = np.empty(9), np.empty(9)
    mujoco.mju_quat2Mat(rt, np.asarray(tcp[3:7]))
    mujoco.mju_quat2Mat(ro, np.asarray(states['burger_container'][3:7]))
    rotation = rt.reshape(3, 3)
    relative_p = rotation.T @ (np.asarray(states['burger_container'][:3]) - tcp[:3])
    relative_r = rotation.T @ ro.reshape(3, 3)
    return dict(position_m=states['burger_container'][:3], right_tcp_position_m=tcp[:3],
        relative_position_m=relative_p.tolist(), relative_rotation=relative_r.tolist(),
        pad_normal_forces_n=forces, pad_contact_count=pad_contacts,
        bilateral=min(forces) >= .5, supports_upward_n=supports, velocities=speeds,
        food_supported=supports['burger'] > SUPPORT_FORCE_THRESHOLD_N,
        controller_stopped=bool(row['controller_stopped']),
        released_stable=bool(pad_contacts == 0 and max(forces) < .5
            and min(supports.values()) > SUPPORT_FORCE_THRESHOLD_N
            and all(v['linear_m_s'] <= .02 and v['angular_rad_s'] <= .05 for v in speeds.values())
            and not row['controller_stopped']))


class CarrierQualification:
    def __init__(self, marks):
        self.marks, self.initial_z, self.anchor = marks, None, None
        self.acquire = ExactWindow(marks['acquired'] - 25, marks['acquired'])
        self.hold = ExactWindow(marks['qualified_burger_lift'] - 500, marks['qualified_burger_lift'])
        self.carry = ExactWindow(marks['acquired'], marks['release_start'])
        self.food = ExactWindow(marks['acquire_start'], marks['finished_lift'])
        self.release = ExactWindow(marks['finished_lift'] - 1000, marks['finished_lift'])
        self.boundaries, self.maximum_drift, self.maximum_angle = {}, 0., 0.
        self.maximum_pad_force = 0.

    def sample(self, row, metadata):
        tick = row['sim_tick']
        q = carrier_measurement(row, metadata['physics_contract'])
        declared = metadata['physics_contract']['internal_mechanical_constraints']
        allowed = {c['id'] for c in declared if c['type'] == 'mjEQ_JOINT'
                   and c['name'] in ('left_umi_jaw_sync', 'right_umi_jaw_sync')}
        if not set(row['active_equality_ids']).issubset(allowed):
            raise ValueError('UNDECLARED_OR_OBJECT_EQUALITY_IN_CARRIER_RECORD')
        if tick == self.marks['settled']:
            self.initial_z = q['position_m'][2]
        if tick == self.marks['acquired']:
            self.anchor = q
        retained = False
        if self.anchor is not None:
            drift = float(np.linalg.norm(np.asarray(q['relative_position_m']) - self.anchor['relative_position_m']))
            r = np.asarray(self.anchor['relative_rotation']).T @ q['relative_rotation']
            angle = math.acos(float(np.clip((np.trace(r)-1.)/2., -1., 1.)))
            q.update(relative_drift_m=drift, relative_rotation_rad=angle)
            retained = drift <= .015 and angle <= math.radians(30.)
            if tick <= self.marks['release_start']:
                self.maximum_drift = max(self.maximum_drift, drift)
                self.maximum_angle = max(self.maximum_angle, angle)
        good = q['bilateral'] and q['food_supported'] and not q['controller_stopped']
        self.acquire.sample(tick, good)
        self.carry.sample(tick, good and retained)
        self.food.sample(tick, q['food_supported'] and not q['controller_stopped'])
        if self.initial_z is not None:
            q['lift_m'] = q['position_m'][2] - self.initial_z
            self.hold.sample(tick, good and retained and q['lift_m'] >= .03)
        self.release.sample(tick, q['released_stable'])
        self.maximum_pad_force = max(self.maximum_pad_force, *q['pad_normal_forces_n'])
        if tick in self.marks.values():
            self.boundaries[str(tick)] = q

    def result(self):
        return dict(acquire=self.acquire.result(), hold=self.hold.result(), carry=self.carry.result(),
            food=self.food.result(), release=self.release.result(), boundaries=self.boundaries,
            maximum_relative_drift_m=self.maximum_drift, maximum_relative_rotation_rad=self.maximum_angle,
            maximum_pad_force_n=self.maximum_pad_force)


def prepare_carrier(report_path, contact_batch):
    report_path = scoped(report_path)
    report = load(report_path)
    required = ('carrier_bilateral_50ms', 'continuous_lift30mm_hold1s',
                'released_stable_separated_2s', 'food_continuously_supported')
    if (report.get('status') != 'CARRIER_LIFT_RELEASE_PASS' or report.get('scope') != 'lift'
            or report.get('isolated_lift') is not True
            or not all(report.get('checks', {}).get(k) is True for k in required)
            or report.get('live_object_pose_writes') != 0 or report.get('door_actuator_commands') != 0
            or report.get('stop_reason') is not None):
        raise ValueError('UNASSISTED_SUCCESSFUL_ISOLATED_CARRIER_LIFT_REQUIRED')
    if not 1 <= contact_batch <= 50:
        raise ValueError('API_CONTACT_BUDGET_MUST_NOT_BE_RELAXED')
    trace = report['trace']
    def first(phase):
        return next(r['tick'] for r in trace if r['phase'] == phase)
    def last(phase):
        return next(r['tick'] for r in reversed(trace) if r['phase'] == phase)
    marks = dict(settled=first('settled'), acquire_start=first('meal_grasp'),
        qualified_burger_lift=first('qualified_burger_lift'),
        release_start=last('meal_grasp'), opened=last('meal_grasp')+900,
        withdrawn=last('meal_pregrasp'), finished_lift=first('finished_lift'))
    # The original fixed witness closes/opens for exactly 45 native frames.
    marks['acquired'] = marks['acquire_start'] + 45*20
    manifest = report['manifest']
    extractor = FrameExtractor(manifest, marks['finished_lift'], 'right')
    quality = CarrierQualification(marks)
    def consume(row, metadata):
        if row['type'] == 'metadata':
            same_identity(manifest, row['manifest'])
            clock = row['physics_contract']
            if (clock['sampling_hz'], clock['substeps_per_observation'], clock['time_step_s']) != (500, 20, .002):
                raise ValueError('NATIVE_500HZ_25HZ_RECORD_REQUIRED')
        else:
            extractor.sample(row)
            quality.sample(row, metadata)
    record = scan_record(report['record'], consume)
    if (record['samples'] != report['physics_samples'] or record['last_tick'] != marks['finished_lift']
            or len(extractor.frames)*20 != marks['finished_lift']
            or extractor.closing_start != marks['acquire_start']):
        raise ValueError('CARRIER_SOURCE_CLOCK_OR_PHASE_MISMATCH')
    measured = quality.result()
    if not all(measured[k]['pass_all'] for k in ('acquire', 'hold', 'carry', 'food', 'release')):
        raise ValueError('RAW_CARRIER_RECORD_DOES_NOT_CONFIRM_COMPLETE_WINDOWS:' + json.dumps(measured))
    phases = [(0, marks['acquire_start'], 'transit'),
              (marks['acquire_start'], marks['acquired'], 'acquire'),
              (marks['acquired'], marks['release_start'], 'carry'),
              (marks['release_start'], marks['opened'], 'release'),
              (marks['opened'], marks['finished_lift'], 'transit')]
    segments = []
    for start, end, phase in phases:
        if start >= end or start % 20 or end % 20:
            raise ValueError('ALIGNED_ORDERED_CONTACT_PHASE_MARKERS_REQUIRED')
        contract = None if phase == 'transit' else dict(type='grasp', arm='right',
            target='burger_container', phase=phase, payload_ids=['burger_container', 'burger'],
            supports={'burger_container': ['v3_counter_right'], 'burger': ['burger_plate']})
        period = contact_batch if contract else 50
        frames = [f for f in extractor.frames if start < f['end_tick'] <= end]
        for i in range(0, len(frames), period):
            batch = frames[i:i+period]
            segments.append(dict(phase=phase, start_tick=batch[0]['end_tick']-20,
                end_tick=batch[-1]['end_tick'], waypoints=[f['target16'] for f in batch],
                contact_contract=contract, end_contact=phase == 'release' and i+period >= len(frames)))
    opened = measured['boundaries'][str(marks['opened'])]
    blockers = [] if opened['released_stable'] else [dict(code='CARRIER_NOT_STABLE_BEFORE_SOURCE_RETREAT', measured=opened)]
    record.pop('metadata')
    return dict(schema=1, status='SUCCESSFUL_SOURCE_COMPONENT_PREPARED_NOT_REPLAYED', operation='carrier_lift',
        end_tick=marks['finished_lift'], payload_ids=['burger_container', 'burger'], manifest=manifest,
        marks=marks, initial_reference16=extractor.initial, source_report=str(report_path),
        source_report_sha256=sha(report_path), source_record=record, source_full_status=report['status'],
        source_full_reason=report['reason'], explicitly_excluded_source_ticks_after=marks['finished_lift'],
        source_component_checks=measured, segments=segments, contact_batch=contact_batch,
        interface_preflight_blockers=blockers, eligible_for_exact_public_replay=not blockers,
        extraction='Exact source accepted_target16 at each native 20-substep frame; original 45-frame gripper phases',
        dynamics_executed=False, full_microwave_task='NOT_VERIFIED', **provenance())


def prepare(report_path, contact_batch):
    report = load(scoped(report_path))
    if report.get('status') == 'CARRIER_LIFT_RELEASE_PASS':
        return prepare_carrier(report_path, contact_batch)
    if report.get('status') == 'COMPLETE_MICROWAVE_CYCLE_PASS':
        from integration.kitchen.platform_public_door_witness import prepare_door
        return prepare_door(report_path, contact_batch)
    return prepare_apple(report_path, contact_batch)


def source_phase_blockers(marks, qualified):
    opened = qualified['boundaries'][str(marks['basket_opened'])]
    release_origin = qualified['boundaries'][str(marks['basket_supported_before_open'])]['left_tcp_position_m']
    withdrawn = qualified['boundaries'][str(marks['basket_withdrawn'])]['left_tcp_position_m']
    withdrawal_distance = math.sqrt(sum((a - b)**2 for a, b in zip(release_origin, withdrawn)))
    blockers = []
    if not opened['released_stable']:
        blockers.append(dict(code='SOURCE_END_CONTACT_NOT_STABLE_BEFORE_WITHDRAWAL',
            tick=marks['basket_opened'], measured=opened,
            required_linear_m_s=.02, required_angular_rad_s=.05,
            release_local_travel_limit_m=.03, source_withdrawal_distance_m=withdrawal_distance,
            public_api='end_contact demands stable support before clearing release; active release retains 30mm local limit',
            shared_api_modified=False, no_dynamics_retry=True))
    return blockers


class LoggedClient:
    def __init__(self, port, output):
        self.client = PlannerClient(port=port, timeout=30.)
        self.log = (output / 'rpc.jsonl').open('x', encoding='utf-8', buffering=1)
        self.next_sequence = 1

    def call(self, method, args=None, mutating=False):
        sequence = self.next_sequence
        self.next_sequence += 1
        request_id = 'public-witness-' + str(sequence)
        before = self.client.token
        try:
            response = self.client.call(method, args, mutating=mutating, request_id=request_id)
        except Exception as error:
            self.log.write(json.dumps(dict(sequence=sequence, method=method, args=args or {}, mutating=mutating,
                request_id=request_id, token_before=before, transport_error=str(error)), allow_nan=False) + '\n')
            raise
        self.log.write(json.dumps(dict(sequence=sequence, method=method, args=args or {},
            mutating=mutating, request_id=request_id, token_before=before, response=response),
            separators=(',', ':'), allow_nan=False) + '\n')
        if not response.get('ok'):
            raise RuntimeError('PUBLIC_API_REJECTED:' + json.dumps(response.get('error'), sort_keys=True))
        return response['result']

    def close(self):
        self.client.close()
        self.log.close()


def audit_replay(paths, plan, data_root):
    if plan['operation'] == 'microwave_door_open':
        from integration.kitchen.platform_public_door_witness import DoorQualification
        quality = DoorQualification(plan['marks'], plan['manifest'], plan['source_model'])
        required = ('initially_closed', 'acquire', 'device', 'opened', 'release', 'stable', 'retreat')
    else:
        quality = (CarrierQualification(plan['marks']) if plan['operation'] == 'carrier_lift'
                   else AppleQualification(plan['marks']))
        required = ('acquire', 'hold', 'carry', 'food', 'release') if plan['operation'] == 'carrier_lift' else ('hold', 'release')
    expected, audits = 1, []
    for path in paths:
        path = Path(path).resolve()
        path.relative_to(data_root.resolve())

        def consume(row, metadata):
            if row['type'] == 'metadata':
                same_identity(plan['manifest'], row['manifest'])
            else:
                if row['controller_stopped']:
                    raise ValueError('PUBLIC_REPLAY_RECORD_HAS_CONTROLLER_STOP')
                quality.sample(row, metadata)
        audit = scan_record(path, consume)
        if audit['first_tick'] != expected:
            raise ValueError('PUBLIC_PHYSICS_EPISODES_MISSING_OR_OVERLAPPING')
        expected = audit['last_tick'] + 1
        if audit['end'].get('execution', {}).get('status') != 'COMPLETED':
            raise ValueError('PUBLIC_EXECUTION_NOT_COMPLETED')
        feedback = audit['end'].get('contact_feedback', {})
        if feedback.get('violations'):
            raise ValueError('PUBLIC_CONTACT_RECORD_HAS_VIOLATIONS')
        audit.pop('metadata')
        audits.append(audit)
    measured = quality.result()
    if expected != plan['end_tick'] + 1:
        raise ValueError('PUBLIC_RECORD_DOES_NOT_COVER_ENTIRE_SELECTED_OPERATION')
    if not all(measured[k]['pass_all'] for k in required):
        raise ValueError('PUBLIC_RECORD_PHYSICAL_COMPONENT_FAILED')
    return dict(status='PUBLIC_RECORD_PHYSICAL_COMPONENT_CONFIRMED', windows=measured, episodes=audits,
        physics_samples=expected - 1, no_state_injection=True, not_queue_completion_only=True)


def public_run(args, output):
    plan = load(scoped(args.plan)) if args.plan else None
    if plan is not None:
        validate_identity(plan['manifest'])
    executing = args.mode == 'replay'
    if not args.layout:
        raise ValueError('EXPLICIT_LAYOUT_REQUIRED_NO_DEFAULT_CANDIDATE')
    if executing:
        if not (args.authorize_replay and plan and args.expected_scene_hash and args.expected_control_hash):
            raise ValueError('EXPLICIT_REPLAY_AND_FROZEN_SCENE_CONTROL_AUTHORIZATION_REQUIRED')
        if (args.layout != plan['manifest']['layout_id'] or
            args.expected_scene_hash != plan['manifest']['scene_hash'] or
            args.expected_control_hash != plan['manifest']['control_identity']['control_hash']):
            raise ValueError('AUTHORIZED_FINAL_IDENTITY_DIFFERS_FROM_PHYSICS_SOURCE_NO_MOTION')
        # Reconstruct from the actual record; an edited plan cannot substitute targets.
        fresh = prepare(plan['source_report'], plan['contact_batch'])
        for key in ('operation', 'end_tick', 'payload_ids', 'manifest', 'segments', 'marks',
                    'initial_reference16', 'source_report_sha256', 'source_record'):
            if fresh[key] != plan[key]:
                raise ValueError('PREPARED_PLAN_NO_LONGER_MATCHES_SOURCE_RECORD_' + key)
        if fresh['interface_preflight_blockers']:
            raise ValueError('KNOWN_SOURCE_API_PHASE_CONFLICT_NO_MOTION:' +
                json.dumps(fresh['interface_preflight_blockers'], sort_keys=True))
    ready_path, data_root = output / 'ready.json', output / 'data'
    launcher_output = FULL / output.relative_to(FULL.resolve())
    command = [PWSH, '-NoProfile', '-File', str(ROOT / 'scripts/start_kitchen_platform.ps1'),
        '-Layout', args.layout, '-Seed', '0', '-Mode', 'manual', '-DataRoot', str(launcher_output / 'data'),
        '-ReadyFile', str(launcher_output / 'ready.json'), '-PlannerPort', '0', '-WorldPort', '0', '-BindHost', '127.0.0.1',
        '-RunSeconds', str(900 if executing else 12), '-PythonExe', PYTHON]
    result = dict(status='PUBLIC_ENTRY_NOT_READY', launcher_command=command, physics_steps=0,
        source_plan=str(scoped(args.plan)) if args.plan else None, **provenance())
    payload = plan['payload_ids'] if plan else ['apple']
    if plan:
        result.update(operation=plan['operation'], source_report=plan['source_report'],
            source_report_sha256=plan['source_report_sha256'], expected_end_tick=plan['end_tick'],
            source_component_checks=plan['source_component_checks'], evidence_paths=[], full_task='NOT_VERIFIED')
    process = client = None
    stdout = (output / 'stdout.log').open('x', encoding='utf-8')
    stderr = (output / 'stderr.log').open('x', encoding='utf-8')
    try:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 25.
        while not ready_path.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.05)
        if not ready_path.exists():
            raise RuntimeError('REAL_LAUNCHER_READY_TIMEOUT_NO_MOTION')
        ready = load(ready_path)
        if (ready.get('status') != 'PLATFORM_READY' or ready.get('layout') != args.layout or
            ready.get('mode') != 'manual' or ready.get('seed') != 0 or
            Path(ready['import_root']).resolve() != ROOT.resolve() or
            Path(ready['data_root']).resolve() != data_root.resolve()):
            raise ValueError('LAUNCHER_PARAMETER_OR_IMPORT_ROOT_MISMATCH')
        result['ready'] = ready
        client = LoggedClient(ready['planner_port'], output)
        manifest = client.call('get_scene_manifest')
        validate_identity(manifest)
        initial = client.call('observe')
        result.update(manifest=manifest, initial_observation=initial,
            initial_feedback=client.call('get_task_feedback', {'object_ids': payload}))
        if (manifest['api_version'] != '1.1.0' or initial['sim_tick'] != 0 or
            initial['control_frame_phase'] != 0 or initial['stopped']):
            raise ValueError('API_1_1_FRESH_MANUAL_RUNTIME_REQUIRED')
        budgets = manifest['contact_execution_contract']
        if (budgets['maximum_waypoints'] != 50 or budgets['maximum_scratch_substeps'] != 1000 or
            budgets['maximum_preview_wall_s'] != 3.):
            raise ValueError('FROZEN_CONTACT_PREFLIGHT_BUDGET_CHANGED')
        token = client.client.token
        if (token != ready['token'] or token['scene_hash'] != manifest['scene_hash'] or
            token['control_hash'] != manifest['control_identity']['control_hash']):
            raise ValueError('READY_SOCKET_TOKEN_IDENTITY_MISMATCH')
        with socket.create_connection(('127.0.0.1', ready['world_port']), timeout=5) as world:
            with world.makefile('rb') as stream:
                raw = stream.readline(8 * 1024 * 1024)
            if not raw.endswith(b'\n'):
                raise ValueError('COMPLETE_WORLD_FRAME_REQUIRED')
            frame = json.loads(raw)
        if (frame['sceneHash'] != manifest['scene_hash'] or frame['simTick'] != 0 or
            frame['runtimeSession'] != token['runtime_session']):
            raise ValueError('PLANNER_WORLD_RUNTIME_MISMATCH')
        save(output / 'world_initial.json', frame)
        if plan:
            same_identity(plan['manifest'], manifest)
        if not executing:
            result.update(status='REAL_PUBLIC_ENTRY_READY_ONLY', physics_steps=0,
                representative_operation='NOT_EXECUTED_NOT_TASK_PASS')
            return result
        if any(abs(a - b) > 1e-9 for a, b in zip(initial['reference16'], plan['initial_reference16'])):
            raise ValueError('SOURCE_RESET_REFERENCE_MISMATCH_NO_STATE_INJECTION')
        evidence, deadline = [], time.monotonic() + 850.
        for segment in plan['segments']:
            result['last_segment'] = dict(phase=segment['phase'], start_tick=segment['start_tick'],
                end_tick=segment['end_tick'], waypoints=len(segment['waypoints']))
            if time.monotonic() >= deadline:
                raise RuntimeError('BOUNDED_PUBLIC_REPLAY_WALL_TIMEOUT')
            if client.client.token['sim_tick'] != segment['start_tick']:
                raise ValueError('REPLAY_SOURCE_CLOCK_ALIGNMENT_LOST')
            call_args = dict(waypoints=segment['waypoints'], source='programmatic_validation')
            contract = segment['contact_contract']
            if contract is not None:
                preview = client.call('check_contact_motion', dict(waypoints=segment['waypoints'], contact_contract=contract))
                result['last_contact_preflight'] = preview
                if not preview['pass']:
                    raise RuntimeError('PUBLIC_CONTACT_PREFLIGHT_BLOCKER:' + json.dumps(preview, sort_keys=True))
                call_args['contact_contract'] = contract
            execution = client.call('execute_trajectory', call_args, mutating=True)
            evidence.append(execution['evidence_path'])
            result['evidence_paths'] = list(evidence)
            observed = client.call('step', {'n_substeps': len(segment['waypoints']) * 20}, mutating=True)
            result['physics_steps'] = client.client.token['sim_tick']
            status = client.call('get_execution_status', {'execution_id': execution['id']})
            result['last_feedback'] = client.call('get_task_feedback',
                {'object_ids': payload, 'task_instance_id': 'finite-' + plan['operation']})
            if (observed['stopped'] or status['status'] != 'COMPLETED' or
                client.client.token['sim_tick'] != segment['end_tick']):
                raise RuntimeError('PUBLIC_EXECUTION_OR_CLOCK_FAILED:' + json.dumps(status, sort_keys=True))
            if segment['end_contact']:
                released = client.call('end_contact', mutating=True)
                result.setdefault('contact_releases', []).append(dict(tick=segment['end_tick'], result=released))
        final = client.call('observe')
        if final['contact_execution']['active'] or final['stopped']:
            raise RuntimeError('REPRESENTATIVE_OPERATION_DID_NOT_RELEASE_SAFELY')
        result.update(final_observation=final,
            final_feedback=client.call('get_task_feedback', {'object_ids': payload}),
            physical_audit=audit_replay(evidence, plan, data_root),
            status='REPRESENTATIVE_' + plan['operation'].upper() + '_PUBLIC_PHYSICS_PASS', physics_steps=final['sim_tick'])
    except Exception as error:
        result.update(status='PUBLIC_INTERFACE_OR_REPLAY_BLOCKER', error=str(error))
        if client is not None:
            result['physics_steps'] = (client.client.token or {}).get('sim_tick', 0)
            if executing:
                try:
                    result['failure_observation'] = client.call('observe')
                    result['failure_feedback'] = client.call('get_task_feedback', {'object_ids': payload})
                    result['cancelled_prefix'] = client.call('cancel', mutating=True)
                except Exception as cancel_error:
                    result['cancel_error'] = str(cancel_error)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception as error:
                result.update(status='PUBLIC_CLIENT_CLEANUP_BLOCKER', cleanup_error=str(error))
        if process is not None:
            try:
                process.wait(timeout=3 if executing else 16)
                result['server_exit_code'] = process.returncode
            except subprocess.TimeoutExpired:
                # Only this fresh launcher PID and its descendants; never existing services.
                try:
                    stopped = subprocess.run(['C:/Windows/System32/taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                        capture_output=True, text=True, timeout=15,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    process.wait(timeout=10)
                    result['owned_launcher_tree_cleanup'] = dict(pid=process.pid, returncode=stopped.returncode,
                        stdout=stopped.stdout, stderr=stopped.stderr, graceful_server_exit=False)
                except Exception as error:
                    result.update(status='OWNED_SERVER_CLEANUP_BLOCKER', owned_pid=process.pid, cleanup_error=str(error))
        stdout.close()
        stderr.close()
    return result


def software_tests():
    manifest = dict(layout_id='synthetic', scene_hash='a'*64, compiled_model_hash='b'*64,
        control_identity={'control_hash': 'c'*64})
    same_identity(manifest, dict(manifest))
    try:
        same_identity(manifest, {**manifest, 'scene_hash': 'wrong'})
    except ValueError:
        pass
    else:
        raise AssertionError('MISMATCHED_SCENE_ACCEPTED')
    for field in ('scene_hash', 'compiled_model_hash', 'control_hash'):
        for value in (None, '', ' ', 'a', 'g'*64, hashlib.sha256(b'').hexdigest()):
            broken = {**manifest, field: value} if field != 'control_hash' else {
                **manifest, 'control_identity': {'control_hash': value}}
            try:
                same_identity(broken, dict(broken))
            except ValueError:
                pass
            else:
                raise AssertionError('MATCHING_INVALID_IDENTITY_ACCEPTED:' + field)
    windows = [ExactWindow(0, 500) for _ in range(3)]
    for tick in range(1, 501):
        windows[0].sample(tick, True)
        windows[1].sample(tick, tick != 200)
        if tick != 200:
            windows[2].sample(tick, True)
    assert windows[0].result()['pass_all']
    assert not windows[1].result()['pass_all'] and not windows[2].result()['pass_all']
    marks = dict(first_grasp=80, basket_supported_before_open=140, basket_opened=200, basket_stable=260)
    frames = [dict(end_tick=i, target16=[0.] * 16) for i in range(20, 261, 20)]
    segments = split_segments(frames, 40, marks, 2)
    assert [q for s in segments for q in s['waypoints']] == [f['target16'] for f in frames]
    assert sum(len(s['waypoints']) for s in segments) * 20 == 260
    assert all(len(s['waypoints']) <= 2 for s in segments if s['contact_contract'])
    assert sum(s['end_contact'] for s in segments) == 1
    robot_manifest = dict(robots={s: dict(joint_control_limits_rad=[[-3.] * 7, [3.] * 7]) for s in ('left', 'right')})
    extractor = FrameExtractor(robot_manifest, 40)
    for tick in range(1, 41):
        values = [.001 if tick <= 20 else .002] + [0.] * 15
        extractor.sample(dict(sim_tick=tick, accepted_target16=values,
            requested_reference16=[0.] * 16, controller_stopped=False))
    assert [f['target16'][0] for f in extractor.frames] == [.001, .002]
    try:
        broken = FrameExtractor(robot_manifest, 20)
        broken.sample(dict(sim_tick=1, accepted_target16=[0.] * 16,
            requested_reference16=[0.] * 16, controller_stopped=False))
        broken.sample(dict(sim_tick=2, accepted_target16=[.001] + [0.] * 15, controller_stopped=False))
    except ValueError:
        pass
    else:
        raise AssertionError('WITHIN_FRAME_TARGET_CHANGE_ACCEPTED')
    m = dict(basket_opened=20, basket_supported_before_open=0, basket_withdrawn=40)
    blocked = dict(boundaries={'0': dict(left_tcp_position_m=[0, 0, 0]),
        '20': dict(released_stable=False, angular_rad_s=.3237), '40': dict(left_tcp_position_m=[0, 0, .35])})
    blockers = source_phase_blockers(m, blocked)
    assert blockers and blockers[0]['source_withdrawal_distance_m'] > .03
    right = FrameExtractor(robot_manifest, 20, 'right')
    for tick in range(1, 21):
        right.sample(dict(sim_tick=tick, accepted_target16=[0.]*15+[.002],
            requested_reference16=[0.]*16, controller_stopped=False))
    assert right.closing_start == 0 and right.frames[0]['target16'][15] == .002
    contract = dict(body_names_by_id=['burger_container', 'burger',
        'right_umi_left_finger_holder', 'right_umi_right_finger_holder', 'world'],
        geom_names_by_id=['burger_plate', 'v3_counter_right', 'burger_food'],
        object_names=['burger_container', 'burger'])
    def support(a, ba, b, bb):
        return [a, ba, b, bb, -.0001, 0., 0., 0., 0., 0., -1., 0., 1., 0., 1., 0., 0., 2., 0., 0., 0., 0., 0.]
    fixture = dict(object_pose_velocity=[[0.,0.,.01,1.,0.,0.,0.,0.,0.,0.,0.,0.,0.],
        [0.,0.,.02,1.,0.,0.,0.,0.,0.,0.,0.,0.,0.]], tcp_pose=[[0.,0.,0.,1.,0.,0.,0.]]*2,
        contacts=[support(0,0,1,4), support(2,1,0,0)], controller_stopped=False)
    assert carrier_measurement(fixture, contract)['released_stable']
    fixture['contacts'].pop()
    assert not carrier_measurement(fixture, contract)['food_supported']
    assert not carrier_measurement(fixture, contract)['released_stable']
    apple_contract = dict(body_names_by_id=['apple', 'left_umi_left_finger_holder',
        'left_umi_right_finger_holder', 'world'], geom_names_by_id=['basket_bottom', 'apple_geom'],
        object_names=['apple'])
    apple_fixture = dict(object_pose_velocity=[[0.,0.,.02,1.,0.,0.,0.,0.,0.,0.,0.,0.,0.]],
        contacts=[], controller_stopped=False)
    for reverse in (False, True):
        def apple_support(force, upward):
            point = support(0,3,1,0) if reverse else support(1,0,0,3)
            point[10], point[17] = (upward if reverse else -upward), force
            return point
        # Two 0.18N vertical points cannot jointly create a >0.25N qualifying point.
        apple_fixture['contacts'] = [apple_support(.3, .6), apple_support(.3, .6)]
        measured = apple_measurement(apple_fixture, apple_contract)
        assert measured['basket_upward_normal_n'] == 0. and not measured['released_stable']
        apple_fixture['contacts'] = [apple_support(.4, .625)]
        assert not apple_measurement(apple_fixture, apple_contract)['released_stable']
        apple_fixture['contacts'] = [apple_support(1., .5)]
        assert not apple_measurement(apple_fixture, apple_contract)['released_stable']
        apple_fixture['contacts'] = [apple_support(.3, .6), apple_support(.6, .6)]
        measured = apple_measurement(apple_fixture, apple_contract)
        assert measured['released_stable'] and math.isclose(measured['basket_upward_normal_n'], .36)
    resolved_fixture = FULL.resolve() / 'api_alias_fixture_no_write'
    lexical_fixture = FULL / resolved_fixture.relative_to(FULL.resolve())
    assert lexical_fixture.parent == FULL and lexical_fixture.resolve() == resolved_fixture
    return dict(status='SOFTWARE_FIXTURES_PASS_ONLY', physics_steps=0,
        checks=['identity_mismatch_rejected', 'matching_empty_or_malformed_identity_rejected',
                'empty_serialization_sha256_rejected', 'bad_hold_sample_rejected', 'missing_sample_rejected',
                'exact_target_order_retained', 'contact_batch_budget', 'release_phase_boundary',
                'native_accepted_20step_frame_extraction', 'within_frame_target_change_rejected',
                'known_release_withdrawal_conflict_rejected', 'right_native_tendon_extraction',
                'carrier_actual_food_support_required', 'apple_weak_vertical_points_not_summed_into_support',
                'apple_strict_point_vertical_and_upward_boundaries', 'apple_actual_strong_point_required_both_geom_orders',
                'launcher_lexical_alias_preserves_resolved_output_scope'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('prepare', 'readiness', 'replay', 'self-test'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-report', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--layout')
    parser.add_argument('--contact-batch', type=int, default=5)
    parser.add_argument('--authorize-replay', action='store_true')
    parser.add_argument('--expected-scene-hash')
    parser.add_argument('--expected-control-hash')
    args = parser.parse_args()
    output = guard(args.output)
    output.mkdir(exist_ok=False)
    result = dict(status='NOT_EXECUTED', **provenance())
    try:
        if args.mode == 'prepare':
            if args.source_report is None:
                raise ValueError('SOURCE_REPORT_REQUIRED')
            prepared = prepare(args.source_report, args.contact_batch)
            save(output / 'plan.json', prepared)
            result.update(status=prepared['status'], source_full_status=prepared['source_full_status'],
                operation=prepared['operation'], selected_end_tick=prepared['end_tick'], segments=len(prepared['segments']),
                plan_sha256=sha(output / 'plan.json'), physics_steps=0,
                selected_component=prepared['source_component_checks'],
                eligible_for_exact_public_replay=prepared['eligible_for_exact_public_replay'],
                interface_preflight_blockers=prepared['interface_preflight_blockers'])
        elif args.mode == 'self-test':
            result.update(software_tests())
        else:
            result = public_run(args, output)
    except Exception as error:
        result.update(status='PREPARATION_OR_IDENTITY_BLOCKER', error=str(error), physics_steps=0)
    save(output / 'report.json', result)
    print(json.dumps(dict(status=result['status'], physics_steps=result.get('physics_steps', 0),
        error=result.get('error'), report=str(output / 'report.json'))), flush=True)
    return 1 if 'BLOCKER' in result['status'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
