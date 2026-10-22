"""One sealed 013 microwave source through public API, never a planner.

Preparation loads recorded qpos into a detached exact MJB for kinematics only.
Runtime preserves every source target frame, with bounded public settling at
all eight frozen guarded batches; real extra steps have an explicit clock map.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import subprocess
import time

import mujoco
import numpy as np

from integration.kitchen import platform_public_physics_witness as base
from integration.kitchen.platform_public_door_witness import handle_forces
from integration.kitchen.platform_public_support_transition_witness import AUTH, AUTH_SHA
from robot_arm.kitchen.model import ArmHandle
from robot_arm.kitchen.registry import geometry_bounds


SOURCE = base.FULL / 'cycle013_physics_01/report.json'
SOURCE_SHA = 'db37491f70dd19c4f40f425cb34dbc9fe2add288ba518ed4572a472d73b36dc8'
SOURCE_CONTROL = '2ff69af80d5c6fc0724382dcd478ffa2e0699a227153c46d263a368c5b246155'
END_TICK = 64160
PAYLOAD = ['burger_container', 'burger']
DOOR, CARRIER, BUTTON = 'microwave_door_handle', 'burger_container', 'microwave_start_geom'
VERSION = 'finite-native-microwave-public-v3-eight-boundary-settling-clock'
CONFIRMED_RUNTIME_CONTROL = 'b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2'
MAX_CLIENT_WALL_SECONDS = 2700.
WALL_BUDGET_BASIS = ('ONE_AUTHORIZED_FRESH_RESET_RUN: prior run used 827 s for 26980 ticks; '
    '64160 ticks project to 1968 s, plus finite sealing/audit/cleanup margin; '
    '2700 s finite maximum; run03 used 1202.375 s for 40400 native ticks, projecting 1910 s '
    'for the source alone; up to 8x25 public settling frames and full raw audit need additional '
    'margin and may exhaust this cap; unchanged source targets, physics and preview limits')
SETTLING_SOURCE_TICKS = (35500, 35600, 35700, 35800, 40400, 40500, 40600, 40700)
SETTLING_MAX_TICKS = 500
LANDMARKS = dict(settled=1000, pregrasp=2120, approach=2600, door_74=8040,
    door_open_home=11020, meal_pregrasp=12940, meal_grasp=13520,
    qualified_burger_lift=15660, meal_place=19060, meal_exit_home=23260,
    closing_above=25260, closing_grasp=25840, closing_0=32600, closed_home=35500,
    button_pre=38840, physical_heat_started=39980, button_retreat=40400,
    button_press=39260, heated_home=43740, reopen_pre=47620, reopen_grasp=48100,
    reopening_74=53540, reopened_home=56520, retrieve_lip=59220,
    retrieve_grasp=59940, retrieve_lift=61100, retrieved_outside=64160)


def settling_policy():
    return dict(source_ticks=list(SETTLING_SOURCE_TICKS), gate_count=len(SETTLING_SOURCE_TICKS), step_ticks=20,
        maximum_extra_ticks_per_gate=SETTLING_MAX_TICKS, maximum_holds_per_gate=25,
        maximum_extra_ticks=len(SETTLING_SOURCE_TICKS)*SETTLING_MAX_TICKS,
        maximum_extra_seconds=len(SETTLING_SOURCE_TICKS), speed_limit_rad_s=.01, tracking_limit_rad=.005,
        command='PUBLIC_EXECUTE_UNCHANGED_REFERENCE16_THEN_STEP20',
        source_frames='3208_ONCE_IN_ORDER_UNCHANGED', normal_hold_rpc=False,
        runtime_validation='NOT_PROVEN_BY_SOURCE_OR_PREPARATION')


def clock_policy():
    return dict(source_sample='t + sum(H[a] for a < t)',
        source_frame_entry='t + sum(H[a] for a <= t)',
        H='ACTUAL_RECORDED_TICKS_PER_FROZEN_GATE_0_TO_500',
        source_anchors=list(SETTLING_SOURCE_TICKS), actual_end_tick_min=END_TICK,
        actual_end_tick_max=END_TICK+len(SETTLING_SOURCE_TICKS)*SETTLING_MAX_TICKS)


class ExecutionClock:
    """Insert only after the eight declared source samples, never resample."""
    def __init__(self):
        self.gates = []

    @property
    def extra_ticks(self):
        return sum(gate['extra_ticks'] for gate in self.gates)

    def begin_gate(self, source_tick, reference):
        if (len(self.gates) >= len(SETTLING_SOURCE_TICKS)
                or source_tick != SETTLING_SOURCE_TICKS[len(self.gates)]):
            raise ValueError('FROZEN_SETTLING_GATES_ONCE_IN_SOURCE_ORDER_REQUIRED')
        self.gates.append(dict(source_tick=source_tick, extra_ticks=0, hold_reference=list(reference)))

    def add_hold(self, start, end):
        if not self.gates:
            raise ValueError('DECLARED_SETTLING_HOLD_CLOCK_MISMATCH')
        gate = self.gates[-1]
        if (start != self.entry_tick(gate['source_tick']) or not 0 < end-start <= 20
                or gate['extra_ticks']+end-start > SETTLING_MAX_TICKS):
            raise ValueError('DECLARED_SETTLING_HOLD_CLOCK_MISMATCH')
        gate['extra_ticks'] += end-start

    def sample_tick(self, source_tick):
        return source_tick+sum(g['extra_ticks'] for g in self.gates if g['source_tick'] < source_tick)

    def entry_tick(self, source_tick):
        return source_tick+sum(g['extra_ticks'] for g in self.gates if g['source_tick'] <= source_tick)

    def mapped_marks(self, marks):
        return {key: self.sample_tick(value) for key, value in marks.items()}

    def frame_map(self):
        return [dict(source_frame=i, source_start_tick=end-20, source_end_tick=end,
                     execution_start_tick=self.entry_tick(end-20), execution_end_tick=self.sample_tick(end),
                     time_offset_s=(self.sample_tick(end)-end)*.002)
                for i, end in enumerate(range(20, END_TICK+1, 20))]

    def descriptor(self, plan):
        return dict(schema=2, semantics='SOURCE_SAMPLES_AND_FRAME_ENTRIES_DIFFER_AT_EACH_INSERTION',
            source_sample_rule=clock_policy()['source_sample'],
            source_frame_entry_rule=clock_policy()['source_frame_entry'],
            gates=copy.deepcopy(self.gates), maximum_extra_ticks=settling_policy()['maximum_extra_ticks'],
            inserted_steps_are_source_frames=False, source_frame_count=plan['frame_count'],
            extra_actual_ticks=self.extra_ticks, extra_actual_seconds=self.extra_ticks*.002,
            source_end_tick=END_TICK, execution_end_tick=self.sample_tick(END_TICK),
            mapped_marks=self.mapped_marks(plan['marks']),
            mapped_phases=[{**p, 'source_start_tick': p['start_tick'], 'source_end_tick': p['end_tick'],
                            'start_tick': self.entry_tick(p['start_tick']), 'end_tick': self.sample_tick(p['end_tick']),
                            **(dict(source_local_origin_tick=p['local_origin_tick'],
                                    local_origin_tick=self.entry_tick(p['local_origin_tick']))
                               if 'local_origin_tick' in p else {})}
                           for p in plan['phases']])


def settled_feedback(observed):
    reference = np.asarray(observed['reference16'], dtype=float)
    if reference.shape != (16,) or not np.isfinite(reference).all():
        raise ValueError('FINITE_REFERENCE16_REQUIRED')
    arms = {}
    for side, offset in (('left', 0), ('right', 8)):
        robot = observed['robots'][side]
        q, v = np.asarray(robot['qpos_rad']), np.asarray(robot['qvel_rad_s'])
        if q.shape != (7,) or v.shape != (7,) or not np.isfinite(q).all() or not np.isfinite(v).all():
            raise ValueError('FINITE_MEASURED_SEVEN_JOINT_FEEDBACK_REQUIRED')
        arms[side] = dict(max_speed_rad_s=float(np.max(np.abs(v))),
                          max_tracking_error_rad=float(np.max(np.abs(q-reference[offset:offset+7]))))
    return dict(settled=all(a['max_speed_rad_s'] <= .01 and a['max_tracking_error_rad'] <= .005
                            for a in arms.values()), arms=arms, actual_tick=observed['sim_tick'])


def bounded_settle(call, observed, clock, evidence_paths, journal, source_tick):
    """Use measured feedback, with at most25 real recorded unchanged-reference frames."""
    if (observed['sim_tick'] != clock.entry_tick(source_tick)
            or observed['stopped'] or observed['execution']['status'] != 'COMPLETED'
            or observed['contact_execution']['active']):
        raise ValueError('SETTLING_ONLY_AT_RELEASED_FROZEN_GUARDED_BOUNDARY')
    reference = list(observed['reference16'])
    feedback = settled_feedback(observed)
    clock.begin_gate(source_tick, reference)
    journal(dict(type='SETTLING_GATE_ENTRY', source_tick=source_tick,
                 cumulative_extra_ticks_before_gate=clock.extra_ticks, feedback=feedback))
    for attempt in range(25):
        if feedback['settled']:
            break
        start = observed['sim_tick']
        execution = call('execute_trajectory', dict(waypoints=[reference], source='programmatic_validation'), mutating=True)
        evidence_paths.append(execution['evidence_path'])
        journal(dict(type='SETTLING_HOLD_ACCEPTED', attempt=attempt+1, source_anchor_tick=source_tick,
                     execution_start_tick=start, reference16=reference, evidence_path=execution['evidence_path']))
        observed = call('step', {'n_substeps': 20}, mutating=True)
        clock.add_hold(start, observed['sim_tick'])
        feedback = settled_feedback(observed)
        journal(dict(type='SETTLING_HOLD_ACTUAL', attempt=attempt+1, source_anchor_tick=source_tick,
                     execution_start_tick=start, execution_end_tick=observed['sim_tick'],
                     actual_extra_ticks=observed['sim_tick']-start, feedback=feedback,
                     stopped=observed['stopped'], execution_status=observed['execution']['status']))
        if observed['stopped'] or observed['execution']['status'] != 'COMPLETED':
            raise ValueError('REAL_PUBLIC_SETTLING_STOPPED')
        if observed['sim_tick'] != start+20 or observed['reference16'] != reference:
            raise ValueError('SETTLING_REFERENCE_OR_FRAME_CLOCK_CHANGED')
    journal(dict(type='SETTLING_GATE_EXIT', source_tick=source_tick,
                 gate_extra_actual_ticks=clock.gates[-1]['extra_ticks'],
                 extra_actual_ticks=clock.extra_ticks, feedback=feedback))
    if not feedback['settled']:
        raise ValueError('REAL_PUBLIC_SETTLING_BUDGET_EXHAUSTED_500_TICKS')
    return observed


def markers(report):
    found = {}
    for row in report['trace']:
        if row['phase'] in LANDMARKS:
            if row['phase'] in found:
                raise ValueError('DUPLICATE_FROZEN_TRACE_MARKER')
            found[row['phase']] = row['tick']
    if found != LANDMARKS:
        raise ValueError('EXACT_FINITE_SOURCE_TRACE_REQUIRED')
    return {**found, 'door_acquired': 3500, 'door_release': 8280, 'door_end': 9180,
        'meal_acquired': 14420, 'meal_end': 19960, 'cavity_stable_end': 24260,
        'closing_acquired': 26740, 'closing_release': 32840, 'closing_end': 33740,
        'reopen_acquired': 49000, 'reopen_release': 53780, 'reopen_end': 54680,
        'retrieve_acquired': 60840, 'retrieve_load': 61700,
        'button_end': 40080}  # Raw separation reaches 50 ms at 40070; next native frame.


def contract(target, phase):
    payload = PAYLOAD if target == CARRIER else []
    supports = ({'burger_container': ['v3_counter_right', 'microwave_bottom'],
                 'burger': ['burger_plate']} if payload else {})
    value = dict(type='grasp', arm='right', target=target, phase=phase,
                 payload_ids=list(payload), supports=supports)
    if target == CARRIER and phase in ('carry', 'release'):
        value['target_support_transition'] = True
    return value


def local_start(positions, pregrasp, acquired):
    """Earliest recorded boundary whose entire local phase fits 30 mm."""
    for start in range(pregrasp, acquired, 20):
        if np.max(np.linalg.norm(positions[start:acquired+1] - positions[start], axis=1)) <= .030:
            return start
    raise ValueError('NO_NATIVE_LOCAL_ACQUIRE_INTERVAL')


def cavity_contains(bounds):
    # Match DeviceState._inside_local, including its existing 1 mm contact tolerance.
    lower = np.array([-.20, -.156, .024]) - .001
    upper = np.array([.176, .156, .316]) + .001
    return all(np.all(low >= lower) and np.all(high <= upper) for low, high in bounds.values())


def phases_for(marks, positions):
    m, contacts = marks, []
    for name, pre, close, acquired, release, end in (
        ('opening', m['pregrasp'], m['approach'], m['door_acquired'], m['door_release'], m['door_end']),
        ('meal', m['meal_pregrasp'], m['meal_grasp'], m['meal_acquired'], m['meal_place'], m['meal_end']),
        ('closing', m['closing_above'], m['closing_grasp'], m['closing_acquired'], m['closing_release'], m['closing_end']),
        ('reopening', m['reopen_pre'], m['reopen_grasp'], m['reopen_acquired'], m['reopen_release'], m['reopen_end']),
        ('retrieval', m['retrieve_lip'], m['retrieve_grasp'], m['retrieve_acquired'], END_TICK, END_TICK)):
        start = local_start(positions, pre, acquired)
        if start > close:
            raise ValueError('LOCAL_ACQUIRE_WOULD_SKIP_RECORDED_CLOSURE')
        target = CARRIER if name in ('meal', 'retrieval') else DOOR
        contacts.extend([(start, acquired, name, 'acquire', target),
                         (acquired, release, name, 'carry' if target == CARRIER else 'device', target)])
        if end > release:
            contacts.append((release, end, name, 'release', target))
    # Consecutive acquire/release share the policy's local origin, without a held phase.
    button_start = local_start(positions, m['button_pre'], m['button_end'])
    contacts.extend([(button_start, m['physical_heat_started'], 'button', 'acquire', BUTTON),
                     (m['physical_heat_started'], m['button_end'], 'button', 'release', BUTTON)])
    result, previous = [], 0
    for start, end, name, phase, target in sorted(contacts):
        if start < previous or not 0 <= start < end <= END_TICK or start % 20 or end % 20:
            raise ValueError('ALIGNED_NONOVERLAPPING_PHASES_REQUIRED')
        if previous < start:
            result.append(dict(start_tick=previous, end_tick=start, name='transit', phase='transit', target=None))
        origin = button_start if name == 'button' else start
        travel = float(np.max(np.linalg.norm(positions[start:end+1] - positions[origin], axis=1)))
        if phase in ('acquire', 'release') and travel > .030:
            raise ValueError('SOURCE_LOCAL_PHASE_EXCEEDS_30MM:' + name + ':' + phase)
        result.append(dict(start_tick=start, end_tick=end, name=name, phase=phase, target=target,
                           local_origin_tick=origin, measured_max_displacement_m=travel, end_contact=phase == 'release'))
        previous = end
    if previous != END_TICK:
        raise ValueError('ENTIRE_SOURCE_MUST_END_IN_RETRIEVAL_HOLD')
    return result


def segments_for(frames, phases, initial):
    if len(frames)*20 != END_TICK or [f['end_tick'] for f in frames] != list(range(20, END_TICK+1, 20)):
        raise ValueError('ALL_SOURCE_FRAMES_ONCE_FROM_RESET_REQUIRED')
    result, previous = [], initial
    for phase in phases:
        selected = frames[phase['start_tick']//20:phase['end_tick']//20]
        batch, guarded = [], None

        def emit():
            if batch:
                result.append({**phase, 'start_tick': batch[0]['end_tick']-20,
                    'end_tick': batch[-1]['end_tick'], 'waypoints': [f['target16'] for f in batch],
                    'contact_contract': contract(phase['target'], phase['phase']) if phase['target'] else None,
                    'guarded_gripper': guarded,
                    'end_contact': bool(phase.get('end_contact') and batch[-1]['end_tick'] == phase['end_tick'])})

        for frame in selected:
            values = frame['target16']
            changes = any(abs(values[i]-previous[i]) > 1e-12 for i in (7, 15))
            guard = bool(changes and phase['target'] is None)
            if guard and any(abs(values[i]-previous[i]) > 1e-12 for i in (*range(7), *range(8, 15))):
                raise ValueError('NONCONTACT_GRIPPER_FRAME_HAS_ARM_MOTION')
            if batch and (len(batch) == 5 or guard != guarded):
                emit()
                batch = []
            guarded = guard
            batch.append(frame)
            previous = values
        emit()
    if sum(len(s['waypoints']) for s in result) != len(frames):
        raise ValueError('FRAME_DUPLICATION_OR_OMISSION')
    return result


def guarded_boundaries(segments, initial):
    """Enumerate, rather than infer online, this source's complete guarded set."""
    previous, active, released, boundaries = initial, None, None, []
    for index, segment in enumerate(segments):
        target = segment['target']
        if active is not None and target != active:
            raise ValueError('FROZEN_TARGET_CHANGE_WITHOUT_ANNOTATED_RELEASE')
        if segment['guarded_gripper']:
            same_arms = all(values[i] == previous[i] for values in segment['waypoints']
                            for i in (*range(7), *range(8, 15)))
            if (target is not None or segment['contact_contract'] is not None
                    or active is not None or released is None or not same_arms):
                raise ValueError('FROZEN_GUARDED_BOUNDARY_MUST_HAVE_UNCHANGED_ARMS_AND_ENDED_CONTACT')
            boundaries.append(dict(segment_index=index, source_tick=segment['start_tick'],
                source_end_tick=segment['end_tick'], source_frame_count=len(segment['waypoints']),
                both_seven_joint_targets_unchanged=True, preceding_release_end_source_tick=released,
                contact_scope='PLAN_ANNOTATION_ONLY_RUNTIME_MUST_INDEPENDENTLY_CONFIRM_INACTIVE'))
        if target is not None:
            active = target
        if segment.get('end_contact'):
            active, released = None, segment['end_tick']
        previous = segment['waypoints'][-1]
    if [g['source_tick'] for g in boundaries] != list(SETTLING_SOURCE_TICKS):
        raise ValueError('EXACT_EIGHT_FROZEN_GUARDED_BATCHES_REQUIRED')
    return boundaries


class CycleGeometry:
    """Detached MJB kinematics driven only by consecutive recorded qpos."""
    def __init__(self, manifest, model_path):
        if base.sha(model_path) != manifest['compiled_model_hash']:
            raise ValueError('EXACT_SOURCE_MJB_REQUIRED')
        self.model = m = mujoco.MjModel.from_binary_path(str(model_path))
        self.data = mujoco.MjData(m)
        self.previous = None
        self.pads = ArmHandle.resolve(m, 'right').pad_geoms
        self.geoms = {}
        for name in PAYLOAD:
            body = m.body(name).id
            self.geoms[name] = [g for g in range(m.ngeom) if int(m.body_weldid[int(m.geom_bodyid[g])]) == body
                               and (m.geom_contype[g] or m.geom_conaffinity[g])]
            if not self.geoms[name]:
                raise ValueError('MISSING_NATIVE_PAYLOAD_GEOMETRY')
        self.targets = {DOOR: [m.geom(DOOR).id], BUTTON: [m.geom(BUTTON).id],
                        CARRIER: self.geoms[CARRIER] + self.geoms['burger']}
        self.oven = m.body('microwave').id
        self.door_body = int(m.body_weldid[int(m.geom_bodyid[m.geom(DOOR).id])])
        hinge = m.joint('microwave_hinge').id
        if any(int(t) == int(mujoco.mjtTrn.mjTRN_JOINT) and int(ids[0]) == hinge
               for t, ids in zip(m.actuator_trntype, m.actuator_trnid)):
            raise ValueError('DIRECT_DOOR_ACTUATOR_FORBIDDEN')

    def sample(self, row, metadata):
        m, d = self.model, self.data
        qpos = np.asarray(row['qpos'])
        if qpos.shape != (m.nq,) or not np.isfinite(qpos).all():
            raise ValueError('INVALID_RECORDED_QPOS')
        d.qpos[:] = qpos if self.previous is None else self.previous
        mujoco.mj_kinematics(m, d)
        self.previous = qpos.copy()
        clock = metadata['physics_contract']
        meal = base.carrier_measurement(row, clock)
        states = {n: row['object_pose_velocity'][clock['object_names'].index(n)] for n in PAYLOAD}
        forces, counts, separated = {}, {}, {}
        for name in (DOOR, BUTTON):
            forces[name], counts[name] = handle_forces(row, m.geom(name).id, self.pads)
        forces[CARRIER], counts[CARRIER] = meal['pad_normal_forces_n'], [meal['pad_contact_count']]
        for name, geoms in self.targets.items():
            distance = min(float(mujoco.mj_geomDistance(m, d, p, g, .01, None)) for p in self.pads for g in geoms)
            separated[name] = not any(counts[name]) and max(forces[name]) < .5 and distance >= .0005
        cavity_support = False
        bottom, carrier = m.geom('microwave_bottom').id, set(self.geoms[CARRIER])
        for c in row['contacts']:
            a, b = int(c[0]), int(c[2])
            if (a in carrier and b == bottom) or (b in carrier and a == bottom):
                upward = (-1. if a in carrier else 1.)*c[10]
                cavity_support |= (upward > base.SUPPORT_NORMAL_MIN_UPWARD_Z
                    and upward*c[17] > base.SUPPORT_FORCE_THRESHOLD_N
                    and c[7] < states[CARRIER][2] + base.SUPPORT_CENTER_CLEARANCE_M)
        bounds = {}
        for name, geoms in self.geoms.items():
            boxes = [geometry_bounds(m, d, g, d.xpos[self.oven], d.xmat[self.oven].reshape(3, 3)) for g in geoms]
            bounds[name] = (np.min([v[0] for v in boxes], axis=0), np.max([v[1] for v in boxes], axis=0))
        inside = cavity_contains(bounds)
        outside = all(high[0] < -.22 for _, high in bounds.values())
        angle, speed = row['device_joint_state'][clock['device_joint_names'].index('microwave_hinge')]
        button_position = row['device_joint_state'][clock['device_joint_names'].index('microwave_start_slide')][0]
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, np.asarray(row['tcp_pose'][1][3:7]))
        rotation = rotation.reshape(3, 3)
        door_relative = (rotation.T @ (d.xpos[self.door_body]-row['tcp_pose'][1][:3]), rotation.T @ d.xmat[self.door_body].reshape(3, 3))
        stable = all(v['linear_m_s'] <= .02 and v['angular_rad_s'] <= .05 for v in meal['velocities'].values())
        return dict(meal=meal, forces=forces, separated=separated, inside=bool(inside), outside=bool(outside),
            cavity_support=bool(cavity_support), stable=stable, angle=angle, hinge_speed=speed,
            button_position=button_position, door_relative=door_relative)


class CycleAudit:
    def __init__(self, marks, manifest, model_path, end_tick=END_TICK):
        self.marks, self.geometry = marks, CycleGeometry(manifest, model_path)
        self.end_tick = end_tick
        m = marks
        ranges = dict(food=(m['settled'], end_tick), initial_load=(m['qualified_burger_lift']-500, m['qualified_burger_lift']),
            retrieval_load=(m['retrieve_load']-500, m['retrieve_load']),
            cavity_released_2s=(m['meal_exit_home'], m['cavity_stable_end']),
            final_outside_held_1s=(end_tick-500, end_tick))
        self.held = []
        for name, acquired, release, target in (
            ('opening', m['door_acquired'], m['door_release'], DOOR),
            ('meal', m['meal_acquired'], m['meal_place'], CARRIER),
            ('closing', m['closing_acquired'], m['closing_release'], DOOR),
            ('reopening', m['reopen_acquired'], m['reopen_release'], DOOR),
            ('retrieval', m['retrieve_acquired'], end_tick, CARRIER)):
            ranges[name+'_acquired'] = acquired-25, acquired
            ranges[name+'_retained'] = acquired, release
            self.held.append((name, acquired, release, target))
        for name, end in (('opening', m['door_end']), ('meal', m['meal_end']),
                          ('closing', m['closing_end']), ('reopening', m['reopen_end']), ('button', m['button_end'])):
            ranges[name+'_released'] = end-25, end
        for name, end in (('opened', m['door_release']), ('closed', m['closing_release']), ('reopened', m['reopen_release'])):
            ranges[name] = end-25, end
        self.windows = {name: base.ExactWindow(*interval) for name, interval in ranges.items()}
        self.positions = np.zeros((end_tick+1, 3))
        self.anchors, self.loss = {}, {}
        self.initial_z = self.retrieval_z = None
        self.heat_start = self.heat_complete = None
        self.button_run = 0
        self.button_proven = self.initial_closed = False
        self.maximum_button_force = 0.

    def sample(self, row, metadata):
        tick = row['sim_tick']
        if not 1 <= tick <= self.end_tick or row['controller_stopped']:
            raise ValueError('STOPPED_OR_OUT_OF_RANGE_PHYSICS')
        declared = metadata['physics_contract']['internal_mechanical_constraints']
        allowed = {c['id'] for c in declared if c['type'] == 'mjEQ_JOINT'
                   and c['name'] in ('left_umi_jaw_sync', 'right_umi_jaw_sync')}
        if not set(row['active_equality_ids']).issubset(allowed):
            raise ValueError('UNDECLARED_OBJECT_EQUALITY')
        q = self.geometry.sample(row, metadata)
        meal, m = q['meal'], self.marks
        self.positions[tick] = row['tcp_pose'][1][:3]
        if tick == 1:
            self.positions[0] = self.positions[1]
            self.initial_closed = abs(q['angle']) <= math.radians(1.)
        if tick == m['settled']:
            self.initial_z = meal['position_m'][2]
        if tick == m['retrieve_grasp']:
            self.retrieval_z = meal['position_m'][2]
        for name, acquired, release, target in self.held:
            bilateral = min(q['forces'][target]) >= .5
            relative = q['door_relative'] if target == DOOR else (np.asarray(meal['relative_position_m']), np.asarray(meal['relative_rotation']))
            if tick == acquired:
                self.anchors[name] = relative
                self.loss[name] = 0
            if acquired < tick <= release:
                self.loss[name] = 0 if bilateral else self.loss[name]+1
                anchor = self.anchors[name]
                drift = float(np.linalg.norm(relative[0]-anchor[0]))
                angle = math.acos(float(np.clip((np.trace(anchor[1].T @ relative[1])-1.)/2., -1., 1.)))
                self.windows[name+'_retained'].sample(tick, self.loss[name] <= 25 and drift <= .015 and angle <= math.radians(30.))
            self.windows[name+'_acquired'].sample(tick, bilateral)
        self.windows['food'].sample(tick, meal['food_supported'])
        for name, baseline in (('initial_load', self.initial_z), ('retrieval_load', self.retrieval_z)):
            self.windows[name].sample(tick, baseline is not None and meal['position_m'][2]-baseline >= .03
                                      and meal['bilateral'] and meal['food_supported'])
        for name in ('opening', 'closing', 'reopening'):
            self.windows[name+'_released'].sample(tick, q['separated'][DOOR] and abs(q['hinge_speed']) <= .05)
        released = q['separated'][CARRIER] and q['cavity_support'] and meal['food_supported'] and q['stable'] and q['inside']
        self.windows['meal_released'].sample(tick, released)
        self.windows['cavity_released_2s'].sample(tick, released)
        self.windows['button_released'].sample(tick, q['separated'][BUTTON])
        self.windows['opened'].sample(tick, q['angle'] >= math.radians(70.))
        self.windows['reopened'].sample(tick, q['angle'] >= math.radians(70.))
        self.windows['closed'].sample(tick, abs(q['angle']) < math.radians(3.))
        self.windows['final_outside_held_1s'].sample(tick, q['outside'] and meal['bilateral'] and meal['food_supported'])
        button_force = sum(q['forces'][BUTTON])
        self.maximum_button_force = max(self.maximum_button_force, button_force)
        ready = q['inside'] and q['cavity_support'] and meal['food_supported'] and q['separated'][CARRIER]
        good_button = button_force >= 1. and q['button_position'] >= .003 and q['angle'] <= math.radians(5.) and ready
        self.button_run = self.button_run+1 if good_button else 0
        state = row['device_state']
        if state['heating_object_name'] == CARRIER and self.heat_start is None:
            self.heat_start = tick
            self.button_proven = self.button_run >= 25
        if CARRIER in state['heated_object_names'] and self.heat_complete is None:
            self.heat_complete = tick

    def result(self):
        windows = {name: window.result() for name, window in self.windows.items()}
        checks = dict(initially_closed=self.initial_closed, physical_button_force_and_start=self.button_proven,
            explicit_5s_heat_completed=bool(self.heat_start and self.heat_complete
                and self.marks['closing_end'] < self.heat_start < self.heat_complete < self.marks['reopen_pre']
                and self.heat_complete-self.heat_start >= 2500))
        return dict(pass_all=all(checks.values()) and all(w['pass_all'] for w in windows.values()),
            checks=checks, windows=windows, heat_start_tick=self.heat_start, heat_complete_tick=self.heat_complete,
            maximum_button_force_n=self.maximum_button_force,
            heat_model='EXPLICIT_DEVICE_STATE_PROXY_NOT_THERMAL_PHYSICS',
            final_pose='HELD_OUTSIDE_NOT_TABLE_RELEASE_OR_TRAY_DELIVERY')


def prepare(output=None):
    if base.sha(SOURCE) != SOURCE_SHA:
        raise ValueError('ONLY_FROZEN_CYCLE013_SOURCE_REPORT_ALLOWED')
    report = base.load(SOURCE)
    manifest = report['manifest']
    base.validate_identity(manifest)
    if (report['status'] != 'COMPLETE_MICROWAVE_CYCLE_PASS' or report['scope'] != 'cycle'
            or report['live_object_pose_writes'] != 0 or report['door_actuator_commands'] != 0
            or report['physics_samples'] != END_TICK or manifest['control_identity']['control_hash'] != SOURCE_CONTROL):
        raise ValueError('FROZEN_NATIVE_SOURCE_REQUIRED')
    marks = markers(report)
    model = base.scoped(SOURCE.parent / 'scene.mjb')
    quality = CycleAudit(marks, manifest, model)
    extractor = base.FrameExtractor(manifest, END_TICK, 'right')

    def consume(row, metadata):
        if row['type'] == 'metadata':
            base.same_identity(manifest, row['manifest'])
            clock = row['physics_contract']
            if (clock['sampling_hz'], clock['substeps_per_observation'], clock['time_step_s']) != (500, 20, .002):
                raise ValueError('UNCHANGED_NATIVE_CLOCK_REQUIRED')
        else:
            extractor.sample(row)
            quality.sample(row, metadata)

    # The note-placement parameter does not exclude any physical source rows.
    record = base.scan_record(base.ROOT / report['record'], consume, allow_source_headers=True,
                              selected_end_tick=marks['button_press'])
    record['button_preload_provenance'] = record.pop('excluded_later_source_notes', [])
    if record['samples'] != END_TICK or record['first_tick'] != 1 or record['last_tick'] != END_TICK:
        raise ValueError('COMPLETE_128_32S_SOURCE_CHAIN_REQUIRED')
    record.pop('metadata')
    measured = quality.result()
    if output is not None:
        base.save(output/'source_measurements.json', dict(source_report_sha256=SOURCE_SHA,
            source_record=record, source_manifest=manifest, source_control_hash=SOURCE_CONTROL,
            expected_runtime_control_hash=CONFIRMED_RUNTIME_CONTROL, runtime_manifest='NOT_STARTED',
            source_raw_audit=measured, frames=extractor.frames, marks=marks,
            initial_reference16=extractor.initial, dynamics_executed=False))
    phases = phases_for(marks, quality.positions)
    segments = segments_for(extractor.frames, phases, extractor.initial)
    boundaries = guarded_boundaries(segments, extractor.initial)
    blockers = [] if measured['pass_all'] else ['SOURCE_RAW_PHYSICAL_GATE_FAILED']
    result = dict(schema=3, algorithm=VERSION, status='PREPARED_ONLY_NOT_PUBLIC_PHYSICS_PASS',
        source_report=str(SOURCE), source_report_sha256=SOURCE_SHA, source_record=record,
        source_model=str(model), source_model_sha256=base.sha(model), source_manifest=manifest,
        source_control_hash=SOURCE_CONTROL, expected_runtime_control_hash=CONFIRMED_RUNTIME_CONTROL,
        end_tick=END_TICK, physics_seconds=END_TICK*.002,
        frame_count=len(extractor.frames), initial_reference16=extractor.initial,
        marks=marks, phases=phases, segments=segments, source_raw_audit=measured,
        interface_preflight_blockers=blockers, actual_public_policy='NOT_EXECUTED_OR_INFERRED',
        client_source_sha256=base.sha(__file__), supplemental_authorization_sha256=AUTH_SHA,
        client_wall_cap_s=MAX_CLIENT_WALL_SECONDS, client_wall_budget_basis=WALL_BUDGET_BASIS,
        settling_policy=settling_policy(), guarded_boundaries=boundaries, clock_policy=clock_policy(),
        target_derivation='ALL_ACCEPTED_TARGET16_FRAMES_UNCHANGED_NO_RESAMPLING_OR_REPLANNING',
        final_pose='HELD_OUTSIDE_NOT_TABLE_RELEASE_OR_TRAY_DELIVERY')
    result['plan_hash'] = base.digest(result)
    return result


def verify_prepared(path):
    path = base.scoped(path)
    plan = base.load(path)
    expected = plan.pop('plan_hash')
    if (base.digest(plan) != expected or plan['client_source_sha256'] != base.sha(__file__)
            or plan['algorithm'] != VERSION or plan.get('settling_policy') != settling_policy()
            or plan.get('clock_policy') != clock_policy()
            or plan.get('guarded_boundaries') != guarded_boundaries(plan['segments'], plan['initial_reference16'])):
        raise ValueError('PREPARED_PLAN_OR_CLIENT_CHANGED')
    plan['plan_hash'] = expected
    if (plan['source_report_sha256'] != SOURCE_SHA or base.sha(SOURCE) != SOURCE_SHA
            or base.scoped(plan['source_record']['path']) != base.scoped(base.ROOT / base.load(SOURCE)['record'])
            or base.sha(plan['source_record']['path']) != plan['source_record']['sha256']
            or base.sha(plan['source_model']) != plan['source_model_sha256']):
        raise ValueError('SEALED_SOURCE_IDENTITY_CHANGED')
    return plan


def guard(output):
    output = base.guard(output)
    if not output.name.startswith('api_microwave_cycle013_') or base.sha(AUTH) != AUTH_SHA:
        raise ValueError('MICROWAVE_OUTPUT_OR_SUPPLEMENTAL_AUTHORIZATION_MISMATCH')
    checked = subprocess.run([base.PWSH, '-NoProfile', '-File', str(base.ROOT / '.codex/validate_active_goal.ps1'),
        '-GoalManagerGoalId', base.GOAL, '-GoalManagerStatus', 'active', '-Stage', 'P1',
        '-RunDirectory', str(base.FULL.parent.relative_to(base.ROOT)), '-ProtocolPath', str(base.PLAN),
        '-ApprovedProtocolPath', str(base.PLAN), '-ApprovedProtocolSHA256', base.PLAN_SHA],
        cwd=base.ROOT, capture_output=True, text=True, timeout=30., creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if checked.returncode or 'PROTOCOL_PASS' not in checked.stdout:
        raise ValueError('OFFICIAL_GOAL_PROTOCOL_GUARD_FAILED:' + checked.stdout + checked.stderr)
    return output, checked.stdout


def execution_frames(plan, clock):
    """Keep native frame IDs intact; explicitly label extra recorded hold frames."""
    if any(g['extra_ticks'] % 20 for g in clock.gates):
        raise ValueError('FULL_AUDIT_REQUIRES_COMPLETE_SETTLING_FRAMES')
    native = [values for s in plan['segments'] for values in s['waypoints']]
    if len(native) != END_TICK//20:
        raise ValueError('ALL_3208_NATIVE_FRAMES_REQUIRED')
    for gate in clock.gates:
        if not np.allclose(native[gate['source_tick']//20-1], gate['hold_reference'], rtol=0., atol=1e-9):
            raise ValueError('HOLD_MUST_PRESERVE_ACTUAL_COMPLETED_SOURCE_REFERENCE')
    gates = {g['source_tick']: g for g in clock.gates}
    frames = []
    for item, values in zip(clock.frame_map(), native):
        if item['source_start_tick'] in gates:
            anchor = item['source_start_tick']
            gate = gates[anchor]
            frames.extend(dict(kind='EXTRA_SETTLING_HOLD_NOT_SOURCE', source_frame=None,
                source_anchor_tick=anchor, execution_start_tick=start, execution_end_tick=start+20,
                target16=gate['hold_reference'])
                for start in range(clock.sample_tick(anchor), clock.entry_tick(anchor), 20))
        frames.append(dict(kind='NATIVE_SOURCE_FRAME', **item, target16=values))
    return frames


def audit_wire_targets(path, frames):
    index = 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['method'] != 'execute_trajectory':
                continue
            values = row['args']['waypoints']
            expected = frames[index:index+len(values)]
            if (not 1 <= len(values) <= 5 or len(expected) != len(values)
                    or values != [item['target16'] for item in expected]
                    or row['token_before']['sim_tick'] != expected[0]['execution_start_tick']
                    or not row.get('response', {}).get('ok')):
                raise ValueError('PUBLIC_WIRE_SOURCE_OR_HOLD_FRAME_SEQUENCE_CHANGED')
            index += len(values)
    if index != len(frames):
        raise ValueError('PUBLIC_WIRE_SOURCE_OR_HOLD_FRAMES_MISSING')
    return dict(requested_native_source_targets_bitwise_equal=True,
                source_frames=sum(item['kind'] == 'NATIVE_SOURCE_FRAME' for item in frames),
                declared_extra_hold_frames=len(frames)-END_TICK//20)


def audit_public(paths, plan, manifest, data_root, deadline=None, clock=None):
    clock = clock or ExecutionClock()
    actual_end = clock.sample_tick(END_TICK)
    quality = CycleAudit(clock.mapped_marks(plan['marks']), plan['source_manifest'], plan['source_model'], actual_end)
    frames = execution_frames(plan, clock)
    wire = audit_wire_targets(data_root.parent/'rpc.jsonl', frames)
    max_difference = 0.
    audits, expected = [], 1
    for index, name in enumerate(paths):
        path = base.scoped(name)
        path.relative_to(data_root.resolve())

        def consume(row, metadata):
            nonlocal max_difference
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('CLIENT_BUDGET_DURING_PHYSICAL_AUDIT')
            if row['type'] == 'metadata':
                base.same_identity(manifest, row['manifest'])
                if row['manifest'].get('episode') != manifest['episode']:
                    raise ValueError('PUBLIC_RAW_RESET_EPISODE_IDENTITY_MISMATCH')
            else:
                tick = row['sim_tick']
                if not 1 <= tick <= actual_end:
                    raise ValueError('PUBLIC_RAW_ACTUAL_CLOCK_OUT_OF_RANGE')
                target = np.asarray(row['accepted_target16'])
                reference = np.asarray(frames[(tick-1)//20]['target16'])
                if target.shape != (16,) or not np.isfinite(target).all():
                    raise ValueError('FINITE_RECORDED_ACCEPTED_TARGET16_REQUIRED')
                difference = float(np.max(np.abs(target-reference)))
                max_difference = max(max_difference, difference)
                # Existing execute API comparison, not a new physical threshold.
                if difference > 1e-9:
                    raise ValueError('ACCEPTED_SOURCE_OR_DECLARED_HOLD_TARGET_CHANGED')
                quality.sample(row, metadata)

        record = base.scan_record(path, consume)
        if record['first_tick'] != expected:
            raise ValueError('PUBLIC_PHYSICS_GAP_OR_OVERLAP')
        end = record['end']
        status = end.get('execution', {}).get('status')
        terminal = index == len(paths)-1 and status == 'HELD' and end.get('reason') == 'HELD_BY_CLIENT'
        if (status != 'COMPLETED' and not terminal) or end.get('contact_feedback', {}).get('violations'):
            raise ValueError('PUBLIC_EXECUTION_OR_CONTACT_GATE_FAILED')
        expected = record['last_tick']+1
        record.pop('metadata')
        audits.append(record)
    measured = quality.result()
    if expected != actual_end+1 or not measured['pass_all']:
        raise ValueError('COMPLETE_PUBLIC_MICROWAVE_PHYSICS_NOT_QUALIFIED:' + str(measured))
    return dict(status='SAME_PUBLIC_RUN_FULL_PHYSICAL_WINDOWS_PASS', measured=measured,
                records=audits, physics_samples=expected-1, wire_targets=wire,
                accepted_target_max_abs_difference=max_difference,
                accepted_target_comparison='EXISTING_API_RTOL0_ATOL1E-9_NOT_BITWISE',
                execution_clock=clock.descriptor(plan))


def run(plan, output, expected_control_hash, wall_seconds):
    if plan['interface_preflight_blockers']:
        raise ValueError('SOURCE_PREPARATION_BLOCKED')
    if expected_control_hash != CONFIRMED_RUNTIME_CONTROL:
        raise ValueError('MAIN_CONFIRMED_STABLE_RUNTIME_CONTROL_HASH_REQUIRED')
    source = plan['source_manifest']
    lexical = base.FULL / output.relative_to(base.FULL.resolve())
    command = [base.PWSH, '-NoProfile', '-File', str(base.ROOT / 'scripts/start_kitchen_platform.ps1'),
        '-Layout', source['layout_id'], '-Seed', '0', '-Mode', 'manual', '-DataRoot', str(lexical/'data'),
        '-ReadyFile', str(lexical/'ready.json'), '-PlannerPort', '0', '-WorldPort', '0',
        '-BindHost', '127.0.0.1', '-RunSeconds', str(wall_seconds+90.), '-PythonExe', base.PYTHON]
    result = dict(status='PUBLIC_MICROWAVE_PARTIAL_NOT_TASK_PASS', launcher_command=command,
        source_control_hash=plan['source_control_hash'], expected_runtime_control_hash=expected_control_hash,
        identity_semantics='NEW_PUBLIC_POLICY_VALIDATION_NOT_OLD_CONTROL_REPLAY',
        source_plan_hash=plan['plan_hash'], physics_steps=0, evidence_paths=[], client_wall_budget_s=wall_seconds,
        client_wall_cap_s=MAX_CLIENT_WALL_SECONDS, client_wall_budget_basis=WALL_BUDGET_BASIS,
        final_pose_scope=plan['final_pose'], task_success='NOT_INFERRED_FROM_QUEUE',
        settling_policy=settling_policy(), native_source_frames_completed=0,
        physical_limits='UNCHANGED', real_hardware='NOT_ACCESSED', canonical_promoted=False)
    clock = ExecutionClock()
    client = process = None
    deadline = time.monotonic()+wall_seconds

    def call(method, args=None, mutating=False):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('FINITE_CLIENT_WALL_BUDGET_EXHAUSTED')
        client.client.socket.settimeout(min(30., remaining))
        return client.call(method, args, mutating=mutating)

    with (output/'stdout.log').open('x', encoding='utf-8') as stdout, \
            (output/'stderr.log').open('x', encoding='utf-8') as stderr, \
            (output/'execution_clock.jsonl').open('x', encoding='utf-8', buffering=1) as clock_log:
        def journal(event):
            clock_log.write(json.dumps(event, separators=(',', ':'), allow_nan=False)+'\n')

        try:
            process = subprocess.Popen(command, cwd=base.ROOT, stdout=stdout, stderr=stderr,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            ready_path = output/'ready.json'
            ready_deadline = min(deadline, time.monotonic()+25.)
            while not ready_path.exists() and process.poll() is None and time.monotonic() < ready_deadline:
                time.sleep(.05)
            if not ready_path.exists():
                raise RuntimeError('REAL_LAUNCHER_READY_TIMEOUT')
            ready = base.load(ready_path)
            result['ready'] = ready
            if (ready['layout'] != source['layout_id'] or ready['seed'] != 0 or ready['mode'] != 'manual'
                    or Path(ready['import_root']).resolve() != base.ROOT.resolve()
                    or Path(ready['data_root']).resolve() != (output/'data').resolve()):
                raise ValueError('REAL_LAUNCHER_PARAMETER_MISMATCH')
            client = base.LoggedClient(ready['planner_port'], output)
            manifest = call('get_scene_manifest')
            result['runtime_manifest'] = manifest
            base.validate_identity(manifest)
            for key in ('layout_id', 'scene_hash', 'compiled_model_hash', 'source_model_hash', 'layout_hash'):
                if manifest[key] != source[key]:
                    raise ValueError('SOURCE_RUNTIME_MODEL_MISMATCH:' + key)
            if manifest['control_identity']['control_hash'] != expected_control_hash:
                raise ValueError('RUNTIME_CONTROL_HASH_NOT_CONFIRMED_STABLE_HASH')
            reset = call('reset', {'seed': 0}, mutating=True)
            result['reset_episode'] = reset.get('episode')
            if (reset['sim_tick'] != 0 or reset['reference16'] != plan['initial_reference16']
                    or reset['episode']['initialization_profile'] != 'fixed'):
                raise ValueError('PUBLIC_FIXED_NATIVE_RESET_REQUIRED')
            after_reset = call('get_scene_manifest')
            base.same_identity(manifest, after_reset)
            if after_reset['episode'] != reset['episode']:
                raise ValueError('PUBLIC_RESET_MANIFEST_EPISODE_MISMATCH')
            result['pre_reset_runtime_manifest'] = manifest
            result['runtime_manifest'] = manifest = after_reset
            active = None
            for segment in plan['segments']:
                if segment['guarded_gripper']:
                    if active is not None or not segment['guarded_gripper'] or segment['contact_contract'] is not None:
                        raise ValueError('SETTLING_BOUNDARY_MUST_BE_RELEASED_GUARDED_GRIPPER')
                    observed = call('observe')
                    observed = bounded_settle(call, observed, clock, result['evidence_paths'], journal,
                                             segment['start_tick'])
                start, end = clock.entry_tick(segment['start_tick']), clock.sample_tick(segment['end_tick'])
                if client.client.token['sim_tick'] != start:
                    raise ValueError('SOURCE_NATIVE_FRAME_CLOCK_MISMATCH')
                target = segment['target']
                if active is not None and target != active:
                    raise ValueError('TARGET_CHANGE_WITHOUT_REAL_RELEASE_AND_END_CONTACT')
                result['last_segment'] = {**{k: segment[k] for k in ('name', 'phase', 'target')},
                    'source_start_tick': segment['start_tick'], 'source_end_tick': segment['end_tick'],
                    'start_tick': start, 'end_tick': end}
                values = dict(waypoints=segment['waypoints'], source='programmatic_validation')
                if segment['contact_contract'] is not None:
                    values['contact_contract'] = copy.deepcopy(segment['contact_contract'])
                elif segment['guarded_gripper']:
                    values['guarded_gripper'] = True
                # The real execute entry already performs the bounded preview.
                execution = call('execute_trajectory', values, mutating=True)
                result['evidence_paths'].append(execution['evidence_path'])
                journal(dict(type='SOURCE_BATCH_ACCEPTED', **result['last_segment'],
                             source_frames=len(segment['waypoints']), evidence_path=execution['evidence_path']))
                active = target
                observed = call('step', {'n_substeps': 20*len(segment['waypoints'])}, mutating=True)
                result['physics_steps'] = observed['sim_tick']
                if observed['stopped'] or observed['execution']['status'] != 'COMPLETED':
                    raise ValueError('REAL_PUBLIC_EXECUTION_STOPPED:' + str(observed['execution']))
                if observed['sim_tick'] != end:
                    raise ValueError('SOURCE_FRAME_END_CLOCK_MISMATCH')
                for source_end in range(segment['start_tick']+20, segment['end_tick']+1, 20):
                    journal(dict(type='SOURCE_FRAME_ACTUAL', source_frame=source_end//20-1,
                                 source_end_tick=source_end, execution_start_tick=clock.entry_tick(source_end-20),
                                 execution_end_tick=clock.sample_tick(source_end), actual_source_ticks=20,
                                 offset_ticks=clock.sample_tick(source_end)-source_end))
                result['native_source_frames_completed'] += len(segment['waypoints'])
                if segment['end_contact']:
                    released = call('end_contact', mutating=True)
                    result.setdefault('measured_releases', []).append(dict(tick=observed['sim_tick'],
                        source_tick=segment['end_tick'], target=active, result=released))
                    active = None
            result['final_observation'] = call('observe')
            result['final_feedback'] = call('get_task_feedback', {'object_ids': PAYLOAD})
            if active != CARRIER or result['final_observation']['sim_tick'] != clock.sample_tick(END_TICK):
                raise ValueError('EXPECTED_FINAL_OUTSIDE_HELD_EPISODE_MISSING')
            # This seals the held endpoint; it is not a normal contact release.
            result['terminal_safety_hold_not_release'] = call('hold', mutating=True)
            result['physical_audit'] = audit_public(result['evidence_paths'], plan, manifest, output/'data', deadline, clock)
            result['status'] = 'PUBLIC_FULL_MICROWAVE_CYCLE_HELD_OUTSIDE_PHYSICS_PASS'
        except Exception as error:
            result['error'] = str(error)
            if client is not None:
                try:
                    client.client.socket.settimeout(5.)
                    result['failure_observation'] = client.call('observe')
                    result['physics_steps'] = result['failure_observation']['sim_tick']
                    result['failure_safety_hold_not_release'] = client.call('hold', mutating=True)
                except Exception as cleanup:
                    result['failure_capture_error'] = str(cleanup)
        finally:
            try:
                result['execution_clock'] = clock.descriptor(plan)
                result['source_frame_map'] = clock.frame_map()
                result['source_frame_map_scope'] = 'FULL_SCHEDULE_NOT_PROOF_OF_EXECUTION_SEE_JOURNAL_AND_COMPLETED_COUNT'
                journal(dict(type='FINITE_CLIENT_RESULT', status=result['status'], error=result.get('error'),
                             physics_steps=result['physics_steps'], extra_actual_ticks=clock.extra_ticks,
                             native_source_frames_completed=result['native_source_frames_completed']))
            except Exception as seal_error:
                result['status'] = 'PUBLIC_MICROWAVE_PARTIAL_NOT_TASK_PASS'
                result['clock_journal_seal_error'] = str(seal_error)
            if client is not None:
                client.close()
            if process is not None:
                try:
                    process.wait(timeout=3.)
                    result['owned_launcher_exit'] = process.returncode
                except subprocess.TimeoutExpired:
                    cleanup = subprocess.run(['C:/Windows/System32/taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                        capture_output=True, text=True, timeout=15., creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    process.wait(timeout=10.)
                    result['owned_launcher_cleanup'] = dict(pid=process.pid, returncode=cleanup.returncode,
                                                           stdout=cleanup.stdout, stderr=cleanup.stderr)
    result['client_wall_seconds_including_cleanup'] = wall_seconds-(deadline-time.monotonic())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepared-plan', type=Path)
    parser.add_argument('--authorize-replay', action='store_true')
    parser.add_argument('--expected-control-hash')
    parser.add_argument('--wall-seconds', type=float, default=1800.)
    args = parser.parse_args()
    if not 60. <= args.wall_seconds <= MAX_CLIENT_WALL_SECONDS or not math.isfinite(args.wall_seconds):
        raise ValueError('FINITE_CLIENT_BUDGET_60_TO_2700_SECONDS_REQUIRED')
    if args.authorize_replay and (not args.prepared_plan or not args.expected_control_hash):
        raise ValueError('PREPARED_PLAN_AND_MAIN_CONFIRMED_RUNTIME_HASH_REQUIRED')
    output, proof = guard(args.output)
    output.mkdir(exist_ok=False)
    try:
        plan = verify_prepared(args.prepared_plan) if args.prepared_plan else prepare(output)
        base.save(output/'source_plan.json', plan)
        result = (run(plan, output, args.expected_control_hash, args.wall_seconds) if args.authorize_replay else
            dict(status=plan['status'], frame_count=plan['frame_count'], phases=plan['phases'],
                 source_control_hash=SOURCE_CONTROL, expected_runtime_control_hash=CONFIRMED_RUNTIME_CONTROL,
                 source_raw_audit=plan['source_raw_audit'], interface_preflight_blockers=plan['interface_preflight_blockers'],
                 source_plan_hash=plan['plan_hash'], settling_policy=plan['settling_policy'], clock_policy=plan['clock_policy'],
                 runtime='NOT_STARTED_PREPARATION_ONLY'))
    except Exception as error:
        result = dict(status='MICROWAVE_PREPARATION_OR_RUNTIME_BLOCKER', error=str(error))
    result.update(official_guard=proof, protocol_path=str(base.PLAN), protocol_sha256=base.PLAN_SHA,
        supplemental_authorization_path=str(AUTH), supplemental_authorization_sha256=AUTH_SHA,
        source_report_sha256=SOURCE_SHA, client_source_sha256=base.sha(__file__), goal_id=base.GOAL)
    base.save(output/'report.json', result)
    print(result['status'], result.get('physics_steps', 0), flush=True)
    return 0 if result['status'] in ('PREPARED_ONLY_NOT_PUBLIC_PHYSICS_PASS', 'PUBLIC_FULL_MICROWAVE_CYCLE_HELD_OUTSIDE_PHYSICS_PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
