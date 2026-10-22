"""Scratch-only MuJoCo clearance queries with conservative interval bounds.

These certificates describe geometry, not contact dynamics or a grasp proof.
The inherited collision masks, adjacent links and MJCF exclusions are retained.
"""
from __future__ import annotations

import mujoco
import numpy as np


class CollisionGeometry:
    def __init__(self, scene):
        self.scene, self.m = scene, scene.model
        self.d = mujoco.MjData(self.m)
        m = self.m
        self.active = [g for g in range(m.ngeom) if m.geom_contype[g] or m.geom_conaffinity[g]]
        self.robot = {g for g in self.active if m.body(int(m.geom_bodyid[g])).name.startswith(("left_", "right_"))}
        self.excluded = {int(x) for x in m.exclude_signature}
        self.radius = m.geom_rbound.copy()
        self.radius[m.geom_type == mujoco.mjtGeom.mjGEOM_PLANE] = 100.
        self.hinge = np.zeros((m.ngeom, m.nq))
        self.slide = np.zeros((m.ngeom, m.nq))
        self.free = {}
        for g in self.active:
            b = int(m.geom_bodyid[g])
            length = float(np.linalg.norm(m.geom_pos[g]) + self.radius[g])
            while b:
                start, count = int(m.body_jntadr[b]), int(m.body_jntnum[b])
                for j in range(start, start+count):
                    adr, kind = int(m.jnt_qposadr[j]), m.jnt_type[j]
                    if kind == mujoco.mjtJoint.mjJNT_HINGE:
                        self.hinge[g, adr] = length + np.linalg.norm(m.jnt_pos[j])
                    elif kind == mujoco.mjtJoint.mjJNT_SLIDE:
                        self.slide[g, adr] = 1.
                        length += float(np.max(np.abs(m.jnt_range[j])))
                    elif kind == mujoco.mjtJoint.mjJNT_FREE:
                        self.free[g] = (adr, length)
                length += float(np.linalg.norm(m.body_pos[b]) +
                                2*np.sum(np.linalg.norm(m.jnt_pos[start:start+count], axis=1)))
                b = int(m.body_parentid[b])

    def eligible(self, a, b):
        m = self.m
        ba, bb = int(m.geom_bodyid[a]), int(m.geom_bodyid[b])
        wa, wb = int(m.body_weldid[ba]), int(m.body_weldid[bb])
        if wa == wb:
            return False
        if wa and wb and (m.body_weldid[m.body_parentid[wa]] == wb or m.body_weldid[m.body_parentid[wb]] == wa):
            return False
        if ((min(ba, bb) << 16) | max(ba, bb)) in self.excluded:
            return False
        return bool((m.geom_contype[a] & m.geom_conaffinity[b]) or (m.geom_contype[b] & m.geom_conaffinity[a]))

    def pairs(self, moving_names=()):
        moving = self.robot | {g for g in self.active if any(
            self.m.body(int(self.m.geom_bodyid[g])).name.startswith(n) for n in moving_names)}
        return np.array([(a,b) for i,a in enumerate(self.active) for b in self.active[i+1:]
                         if (a in moving or b in moving) and self.eligible(a,b)], dtype=int).reshape(-1,2)

    def distances(self, q, pairs, cutoff=.3):
        self.d.qpos[:] = q
        mujoco.mj_kinematics(self.m, self.d)
        values = np.linalg.norm(self.d.geom_xpos[pairs[:,0]]-self.d.geom_xpos[pairs[:,1]], axis=1)
        values -= self.radius[pairs[:,0]]+self.radius[pairs[:,1]]
        for k in np.flatnonzero(values < cutoff):
            a,b = pairs[k]
            values[k] = mujoco.mj_geomDistance(self.m,self.d,int(a),int(b),cutoff,None)
        return values

    def motion_bound(self, a, b):
        delta = np.abs(b-a)
        bound = self.hinge@delta+self.slide@delta
        for g,(adr,radius) in self.free.items():
            qa,qb = a[adr+3:adr+7],b[adr+3:adr+7]
            angle = 2*np.arccos(np.clip(abs(float(qa@qb)),0,1))
            bound[g] += np.linalg.norm(a[adr:adr+3]-b[adr:adr+3])+radius*angle
        return bound

    def midpoint(self, a, b):
        velocity = np.zeros(self.m.nv)
        mujoco.mj_differentiatePos(self.m,velocity,1.,a,b)
        mid = a.copy()
        mujoco.mj_integratePos(self.m,mid,velocity,.5)
        return mid

    def _result(self, values, pairs, thresholds):
        k = int(np.argmin(values-thresholds))
        return {"distance_m":float(values[k]),"required_margin_m":float(thresholds[k]),
                "pair":[self.m.geom(int(g)).name for g in pairs[k]]}

    def intentional_ids(self, pairs):
        result=set()
        for pair in pairs:
            ids=[]
            for value in pair:
                if isinstance(value,(int,np.integer)) and 0<=int(value)<self.m.ngeom:
                    ids.append(int(value))
                elif isinstance(value,str) and value:
                    ids.append(self.m.geom(value).id)
                else:
                    break
            if len(ids)==2:
                result.add(frozenset(ids))
        return result

    def certificate(self, q0, q1, pairs=None, margin=.002, intentional_pairs=(), max_depth=16, max_evaluations=2048):
        pairs = self.pairs() if pairs is None else pairs
        if not len(pairs):
            return {"status":"NO_ELIGIBLE_PAIRS","pass":False}
        intentional = self.intentional_ids(intentional_pairs)
        thresholds = np.array([0. if frozenset(map(int,p)) in intentional else margin for p in pairs])
        accepted, evaluations, worst = 0,0,float("inf")
        for q in (q0,q1):
            values = self.distances(q,pairs)
            if np.any(values < thresholds-1e-9):
                return {"status":"ENDPOINT_COLLISION_OR_MARGIN_FAIL","pass":False,**self._result(values,pairs,thresholds)}
        pending = [(q0.copy(),q1.copy(),0)]
        while pending:
            a,b,depth = pending.pop()
            mid = self.midpoint(a,b)
            values = self.distances(mid,pairs)
            evaluations += 1
            worst = min(worst,float(values.min()))
            if np.any(values < thresholds-1e-9):
                return {"status":"CONTINUOUS_COLLISION_OR_MARGIN_FAIL","pass":False,
                        "evaluations":evaluations,"depth":depth,**self._result(values,pairs,thresholds)}
            movement = self.motion_bound(a,b)/2
            lower = values-movement[pairs[:,0]]-movement[pairs[:,1]]
            if np.all(lower >= thresholds-1e-9):
                accepted += 1
            elif depth >= max_depth or evaluations >= max_evaluations:
                return {"status":"UNRESOLVED","pass":False,"evaluations":evaluations,"depth":depth,
                        "bound_witness":self._result(lower,pairs,thresholds)}
            else:
                pending.extend(((mid,b,depth+1),(a,mid,depth+1)))
        return {"status":"CONTINUOUS_BOUND_PASS","pass":True,"certified_intervals":accepted,
                "evaluations":evaluations,"min_sampled_distance_m":worst,"margin_m":margin,
                "physics_verified":False,"interpolation":"joint-linear, free quaternion shortest geodesic"}
