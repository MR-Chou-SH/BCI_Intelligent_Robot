"""Explicit internal jaw transmission approximation; never constrains objects."""
from __future__ import annotations

import mujoco
import numpy as np

COUPLING = 'symmetric_parallel_jaw_v1'
SOLREF = [.005, 1.]
SOLIMP = [.95, .99, .001, .5, 2.]
POLY = [0., 1., 0., 0., 0.]


def add_jaw_coupling(spec, layout):
    if layout.get('umi_coupling') != COUPLING:
        return
    for side in ('left', 'right'):
        spec.add_equality(name=side+'_umi_jaw_sync', type=mujoco.mjtEq.mjEQ_JOINT,
            name1=side+'_umi_left_finger_joint', name2=side+'_umi_right_finger_joint',
            active=True, data=POLY+[0.]*6, solref=SOLREF, solimp=SOLIMP)


def coupling_manifest(scene):
    from .layouts import layout_manifest
    m = scene.model
    enabled = layout_manifest(scene.layout_id).get('umi_coupling') == COUPLING
    if int(m.neq) != (2 if enabled else 0):
        raise ValueError('UNDECLARED_EQUALITY_CONSTRAINT')
    rows = []
    for side in ('left', 'right') if enabled else ():
        e = m.eq(side+'_umi_jaw_sync').id
        j1 = m.joint(side+'_umi_left_finger_joint').id
        j2 = m.joint(side+'_umi_right_finger_joint').id
        if (int(m.eq_type[e]) != int(mujoco.mjtEq.mjEQ_JOINT) or
                int(m.eq_obj1id[e]) != j1 or int(m.eq_obj2id[e]) != j2 or
                not bool(m.eq_active0[e]) or
                not np.array_equal(m.eq_data[e], POLY+[0.]*6) or
                not np.allclose(m.eq_solref[e], SOLREF, rtol=0, atol=1e-12) or
                not np.allclose(m.eq_solimp[e], SOLIMP, rtol=0, atol=1e-12) or
                not np.array_equal(m.qpos0[m.jnt_qposadr[[j1,j2]]], [0.,0.])):
            raise ValueError('INVALID_JAW_TRANSMISSION_DEFINITION')
        rows.append(dict(id=int(e), name=m.eq(e).name, type='mjEQ_JOINT',
            joint_names=[m.joint(j1).name,m.joint(j2).name], joint_ids=[int(j1),int(j2)],
            polycoef=POLY, solref=SOLREF, solimp=SOLIMP,
            semantics='fixed internal 1:1 transmission approximation; not an object attachment',
            exact_imported_hardware_transmission='NOT_CERTIFIED', active_from_compile=True))
    return rows


def coupling_sample(scene, definitions):
    m,d = scene.model,scene.data
    result = []
    for spec in definitions:
        e = spec['id']
        joints = np.asarray(spec['joint_ids'])
        qs = d.qpos[m.jnt_qposadr[joints]]
        vs = d.qvel[m.jnt_dofadr[joints]]
        mask = (d.efc_type == int(mujoco.mjtConstraint.mjCNSTR_EQUALITY)) & (d.efc_id == e)
        result.append(dict(id=e, active=bool(d.eq_active[e]), finger_qpos_m=qs.tolist(),
            finger_qvel_m_s=vs.tolist(), residual_m=float(qs[0]-qs[1]),
            solver_residual_m=d.efc_pos[mask].tolist(), reaction_n=d.efc_force[mask].tolist()))
    return result
