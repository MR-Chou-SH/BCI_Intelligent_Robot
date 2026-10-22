"""One fixed native-reset microwave opening prefix for the public socket witness.

Only recorded joint/tendon targets can move the public runtime. Loading the source
MJB below is read-only offline kinematics, never an internal dynamics execution.
"""
from __future__ import annotations

import math
import mujoco
import numpy as np

from integration.kitchen import platform_public_physics_witness as w
from robot_arm.kitchen.model import ArmHandle
from robot_arm.kitchen.registry import control_identity


class DoorRecordGeometry:
    def __init__(self, manifest, model_path):
        path = w.scoped(model_path)
        if w.sha(path) != manifest['compiled_model_hash']:
            raise ValueError('SOURCE_MJB_COMPILED_IDENTITY_MISMATCH')
        self.model = mujoco.MjModel.from_binary_path(str(path))
        self.data = mujoco.MjData(self.model)
        m = self.model
        self.handle = m.geom('microwave_door_handle').id
        self.body = int(m.body_weldid[int(m.geom_bodyid[self.handle])])
        self.pads = ArmHandle.resolve(m, 'right').pad_geoms
        self.previous_qpos = None
        hinge = m.joint('microwave_hinge').id
        if any(int(t) == int(mujoco.mjtTrn.mjTRN_JOINT) and int(ids[0]) == hinge
               for t, ids in zip(m.actuator_trntype, m.actuator_trnid)):
            raise ValueError('DIRECT_DOOR_ACTUATOR_PRESENT')

    def sample(self, row):
        qpos = np.asarray(row['qpos'], dtype=float)
        if qpos.shape != (self.model.nq,) or not np.isfinite(qpos).all():
            raise ValueError('INVALID_SOURCE_QPOS')
        # Native mj_step Cartesian/contact fields precede Euler qpos integration.
        self.data.qpos[:] = qpos if self.previous_qpos is None else self.previous_qpos
        mujoco.mj_kinematics(self.model, self.data)
        self.previous_qpos = qpos.copy()
        distances = [float(mujoco.mj_geomDistance(self.model, self.data, p, self.handle, .01, None))
                     for p in self.pads]
        tcp = row['tcp_pose'][1]
        rotation = np.empty(9)
        mujoco.mju_quat2Mat(rotation, np.asarray(tcp[3:7]))
        rotation = rotation.reshape(3, 3)
        relative_p = rotation.T @ (self.data.xpos[self.body] - tcp[:3])
        relative_r = rotation.T @ self.data.xmat[self.body].reshape(3, 3)
        return distances, relative_p, relative_r


def handle_forces(row, handle, pads):
    forces, counts = [0., 0.], [0, 0]
    for c in row['contacts']:
        if len(c) != 23 or not all(math.isfinite(v) for v in c):
            raise ValueError('INVALID_NATIVE_CONTACT_RECORD')
        a, b = int(c[0]), int(c[2])
        for i, pad in enumerate(pads):
            if {a, b} == {pad, handle}:
                if not 0. <= c[17] <= 40. or c[4] < -.003-1e-9:
                    raise ValueError('HANDLE_POINT_EXCEEDS_UNCHANGED_NATIVE_CAP')
                forces[i] += c[17]
                counts[i] += 1
    if max(forces) > 40.:
        raise ValueError('HANDLE_PAD_EXCEEDS_UNCHANGED_40N_CAP')
    return forces, counts


class DoorQualification:
    def __init__(self, marks, manifest, model_path):
        self.marks = marks
        self.geometry = DoorRecordGeometry(manifest, model_path)
        self.acquire = w.ExactWindow(marks['acquired']-25, marks['acquired'])
        self.device = w.ExactWindow(marks['acquired'], marks['release_start'])
        self.opened = w.ExactWindow(marks['door_74'], marks['release_start'])
        self.release = w.ExactWindow(marks['end_contact']-25, marks['end_contact'])
        self.stable = w.ExactWindow(marks['finished_door']-1000, marks['finished_door'])
        self.retreat = w.ExactWindow(marks['finished_door']-20, marks['finished_door'])
        self.initial_angle = self.anchor = self.release_tcp = None
        self.loss_ticks = self.bilateral_ticks = self.maximum_bilateral_ticks = 0
        self.maximum_force = self.maximum_drift = self.maximum_rotation = 0.
        self.minimum_stable_angle = math.inf
        self.boundaries = {}

    def sample(self, row, metadata):
        tick = row['sim_tick']
        contract = metadata['physics_contract']
        allowed = {c['id'] for c in contract['internal_mechanical_constraints']
            if c['type'] == 'mjEQ_JOINT' and c['name'] in ('left_umi_jaw_sync', 'right_umi_jaw_sync')}
        if not set(row['active_equality_ids']).issubset(allowed):
            raise ValueError('UNDECLARED_OR_OBJECT_EQUALITY_IN_DOOR_RECORD')
        forces, contacts = handle_forces(row, self.geometry.handle, self.geometry.pads)
        distances, relative_p, relative_r = self.geometry.sample(row)
        angle, speed = row['device_joint_state'][contract['device_joint_names'].index('microwave_hinge')]
        if not np.isfinite([angle, speed, *distances]).all():
            raise ValueError('NONFINITE_DOOR_MEASUREMENT')
        bilateral = min(forces) >= .5
        self.bilateral_ticks = self.bilateral_ticks+1 if bilateral else 0
        self.maximum_bilateral_ticks = max(self.maximum_bilateral_ticks, self.bilateral_ticks)
        self.loss_ticks = 0 if bilateral else self.loss_ticks+1
        self.maximum_force = max(self.maximum_force, *forces)
        if tick == 1:
            self.initial_angle = angle
        if tick == self.marks['acquired']:
            self.anchor = relative_p.copy(), relative_r.copy()
        if tick == self.marks['end_contact']:
            self.release_tcp = np.asarray(row['tcp_pose'][1][:3])
        drift = rotation = 0.
        if self.anchor is not None:
            drift = float(np.linalg.norm(relative_p-self.anchor[0]))
            r = self.anchor[1].T @ relative_r
            rotation = math.acos(float(np.clip((np.trace(r)-1.)/2., -1., 1.)))
        if self.marks['acquired'] < tick <= self.marks['release_start']:
            self.maximum_drift = max(self.maximum_drift, drift)
            self.maximum_rotation = max(self.maximum_rotation, rotation)
        safe = not row['controller_stopped']
        separated = not any(contacts) and max(forces) < .5 and min(distances) >= .0005
        open_stable = safe and separated and angle >= math.radians(70.) and abs(speed) <= .05
        self.acquire.sample(tick, safe and bilateral)
        self.device.sample(tick, safe and self.loss_ticks <= 25 and self.anchor is not None
            and drift <= .015 and rotation <= math.radians(30.))
        self.opened.sample(tick, safe and angle >= math.radians(70.))
        self.release.sample(tick, open_stable)
        self.stable.sample(tick, open_stable)
        if self.stable.start < tick <= self.stable.end:
            self.minimum_stable_angle = min(self.minimum_stable_angle, angle)
        withdrawal = (float(np.linalg.norm(np.asarray(row['tcp_pose'][1][:3])-self.release_tcp))
            if self.release_tcp is not None else 0.)
        self.retreat.sample(tick, open_stable and withdrawal >= .1)
        if tick in self.marks.values() or tick == 1:
            self.boundaries[str(tick)] = dict(door_angle_deg=math.degrees(angle), hinge_rad_s=speed,
                pad_normal_forces_n=forces, pad_handle_contact_counts=contacts,
                pad_handle_distances_capped10mm_m=distances, pads_separated=separated,
                bilateral=bilateral, measured_grasp_age_s=self.bilateral_ticks*.002,
                relative_drift_m=drift, relative_rotation_deg=math.degrees(rotation),
                withdrawal_m=withdrawal, right_tcp_position_m=row['tcp_pose'][1][:3])

    def result(self):
        result = {k: getattr(self, k).result() for k in ('acquire', 'device', 'opened', 'release', 'stable', 'retreat')}
        result.update(initially_closed=dict(pass_all=self.initial_angle is not None
                and abs(self.initial_angle) <= math.radians(1.), angle_rad=self.initial_angle),
            boundaries=self.boundaries, maximum_bilateral_seconds=self.maximum_bilateral_ticks*.002,
            maximum_pad_force_n=self.maximum_force, maximum_relative_drift_m=self.maximum_drift,
            maximum_relative_rotation_deg=math.degrees(self.maximum_rotation),
            minimum_stable_angle_deg=math.degrees(self.minimum_stable_angle) if math.isfinite(self.minimum_stable_angle) else None)
        return result


def prepare_door(report_path, contact_batch):
    if not 1 <= contact_batch <= 50:
        raise ValueError('API_CONTACT_BUDGET_MUST_NOT_BE_RELAXED')
    report_path = w.scoped(report_path)
    report = w.load(report_path)
    if (report.get('status') != 'COMPLETE_MICROWAVE_CYCLE_PASS' or report.get('scope') != 'cycle'
            or report.get('live_object_pose_writes') != 0 or report.get('door_actuator_commands') != 0
            or not all(report.get('checks', {}).get(k) is True
                for k in ('door_bilateral_50ms', 'door_physically_opened_70deg'))):
        raise ValueError('SUCCESSFUL_NATIVE_RESET_CONTACT_ONLY_DOOR_SOURCE_REQUIRED')
    manifest = report['manifest']
    w.same_identity(manifest, manifest)
    if manifest['control_identity'] != control_identity():
        raise ValueError('CURRENT_CONTROL_DIFFERS_FROM_FROZEN_SOURCE')
    wanted = ('settled', 'approach', 'door_74', 'withdraw_up', 'door_open_home')
    marks = {r['phase']: r['tick'] for r in report['trace'] if r['phase'] in wanted}
    if set(marks) != set(wanted):
        raise ValueError('EXACT_OPENING_SOURCE_BOUNDARIES_REQUIRED')
    marks.update(acquire_start=marks['approach'], acquired=marks['approach']+45*20,
        release_start=marks['door_74']+round(.5*25)*20,
        end_contact=marks['door_74']+round(.5*25)*20+45*20, finished_door=marks['door_open_home'])
    source_model = report_path.parent/'scene.mjb'
    quality = DoorQualification(marks, manifest, source_model)
    extractor = w.FrameExtractor(manifest, marks['finished_door'], 'right')

    def consume(row, metadata):
        if row['type'] == 'metadata':
            w.same_identity(manifest, row['manifest'])
            clock = row['physics_contract']
            if (clock['sampling_hz'], clock['substeps_per_observation'], clock['time_step_s']) != (500, 20, .002):
                raise ValueError('NATIVE_500HZ_25HZ_RECORD_REQUIRED')
        elif row['sim_tick'] <= marks['finished_door']:
            extractor.sample(row)
            quality.sample(row, metadata)

    record = w.scan_record(report['record'], consume, allow_source_headers=True,
        selected_end_tick=marks['finished_door'])
    if (record['samples'] != report['physics_samples'] or record['first_tick'] != 1
            or len(extractor.frames)*20 != marks['finished_door'] or extractor.closing_start != marks['acquire_start']):
        raise ValueError('NATIVE_RESET_DOOR_FRAME_OR_COUNT_MISMATCH')
    frames = extractor.frames
    by_tick = {f['end_tick']: f['target16'] for f in frames}
    for start, end, opening in ((marks['acquire_start'], marks['acquired'], False),
                               (marks['release_start'], marks['end_contact'], True)):
        anchor = by_tick[start]
        if any(by_tick[t][8:15] != anchor[8:15] for t in range(start+20, end+1, 20)):
            raise ValueError('SOURCE_GRIPPER_PHASE_ARMS_NOT_STATIONARY')
        if (by_tick[end][15] != (0. if opening else .04) or
                (by_tick[start+20][15] >= anchor[15]) == opening):
            raise ValueError('SOURCE_GRIPPER_PHASE_DIRECTION_OR_END_MISMATCH')
    measured = quality.result()
    required = ('initially_closed', 'acquire', 'device', 'opened', 'release', 'stable', 'retreat')
    if not all(measured[k]['pass_all'] for k in required):
        raise ValueError('RAW_SOURCE_DOOR_COMPONENT_WINDOW_FAILED:' + w.json.dumps(measured, sort_keys=True))
    phases = [(0, marks['acquire_start'], 'transit'),
        (marks['acquire_start'], marks['acquired'], 'acquire'),
        (marks['acquired'], marks['release_start'], 'device'),
        (marks['release_start'], marks['end_contact'], 'release'),
        (marks['end_contact'], marks['finished_door'], 'transit')]
    segments = []
    for start, end, phase in phases:
        if start >= end or start % 20 or end % 20:
            raise ValueError('ALIGNED_ORDERED_CONTACT_PHASE_MARKERS_REQUIRED')
        contract = None if phase == 'transit' else dict(type='grasp', arm='right',
            target='microwave_door_handle', phase=phase, payload_ids=[], supports={})
        period = contact_batch if contract else 50
        selected = [f for f in frames if start < f['end_tick'] <= end]
        for i in range(0, len(selected), period):
            batch = selected[i:i+period]
            segments.append(dict(phase=phase, start_tick=batch[0]['end_tick']-20,
                end_tick=batch[-1]['end_tick'], waypoints=[f['target16'] for f in batch],
                contact_contract=contract, end_contact=phase == 'release' and i+period >= len(selected)))
    record.pop('metadata')
    return dict(schema=1, status='SUCCESSFUL_SOURCE_COMPONENT_PREPARED_NOT_REPLAYED',
        operation='microwave_door_open', end_tick=marks['finished_door'], payload_ids=[], manifest=manifest,
        marks=marks, initial_reference16=extractor.initial, source_report=str(report_path),
        source_report_sha256=w.sha(report_path), source_record=record, source_model=str(source_model),
        source_model_sha256=w.sha(source_model), source_full_status=report['status'], source_full_reason=report['reason'],
        explicitly_excluded_source_ticks_after=marks['finished_door'], source_component_checks=measured,
        segments=segments, contact_batch=contact_batch, interface_preflight_blockers=[],
        eligible_for_exact_public_replay=True, dynamics_executed=False,
        extraction='Exact first 551 native 25Hz frames; no target interpolation, state injection, or direct door commands',
        full_microwave_task='NOT_VERIFIED_BY_PUBLIC_COMPONENT',
        door_helper_sha256=w.sha(__file__), **w.provenance())


def software_tests():
    handle, pads = 10, (20, 30)
    def point(a, b, force):
        row = [a, 0, b, 0, -.0001]+[0.]*12+[force]+[0.]*5
        assert len(row) == 23
        return row
    row = {'contacts': [point(handle, pads[0], 1.), point(pads[1], handle, 2.)]}
    assert handle_forces(row, handle, pads) == ([1., 2.], [1, 1])
    row['contacts'].append(point(handle, 40, 10.))
    assert handle_forces(row, handle, pads) == ([1., 2.], [1, 1])
    row['contacts'] = [point(handle, pads[0], 40.01)]
    try:
        handle_forces(row, handle, pads)
    except ValueError:
        pass
    else:
        raise AssertionError('DOOR_FORCE_CAP_BYPASSED')
    window = w.ExactWindow(0, 1000)
    for tick in range(1, 1001):
        window.sample(tick, tick != 501)
    assert not window.result()['pass_all']
    return dict(status='SOFTWARE_DOOR_FIXTURES_PASS_ONLY', physics_steps=0,
        checks=['actual_handle_pair_both_orders', 'other_device_contact_not_grasp',
                'native_40n_cap_retained', 'single_bad_stable_tick_rejected'])
