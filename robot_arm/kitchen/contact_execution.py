"""Bounded contact policy and scratch dynamics, never a planner or attachment.

The owner must serialize calls with physics, sample every post-step (including
after queue completion), hold on violations, and clear on cancel/reset. Device
allowances in the original ContactGuard remain the owner's responsibility.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import math
import time

import mujoco
import numpy as np

from .collision import CollisionGeometry
from .contact import object_geom_ids
from .control import DualArmController, tcp_pose
from .devices import (SUPPORT_CENTER_CLEARANCE_M, SUPPORT_FORCE_THRESHOLD_N,
                      SUPPORT_NORMAL_MIN_UPWARD_Z)
from .registry import geometry_bounds


DEVICE_TARGETS = frozenset(("faucet_handle_geom", "microwave_door_handle", "microwave_start_geom"))
LOCAL_PHASES = frozenset(("acquire", "release"))
HELD_PHASES = frozenset(("carry", "device"))
MAX_WAYPOINTS, MAX_SUBSTEPS, MAX_WALL_S = 50, 1000, 3.
ORDINARY_MARGIN_M, PAD_COMPLIANCE_M, SUPPORT_COMPLIANCE_M = .002, .003, .001
ORDINARY_FORCE_N, CONTACT_FORCE_N = 5., 40.
LOCAL_TRAVEL_M, GRASP_AGE_S, LOSS_GRACE_S = .030, .050, .050
GRASP_DRIFT_M, GRASP_ROTATION_RAD = .015, math.radians(30.)
RELEASE_SEPARATION_M = .0005
PAYLOAD_SPEED_M_S, PAYLOAD_ANGULAR_RAD_S = .30, 4.


def validate_contract(value):
    """Pure structural normalization; the instance also resolves scene geometry."""
    required = {"arm", "target", "phase", "payload_ids", "supports"}
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError("INVALID_CONTACT_CONTRACT")
    if set(value) - required - {"type", "target_support_transition"}:
        raise ValueError("UNKNOWN_CONTACT_CONTRACT_FIELD")
    if not required.issubset(value):
        raise ValueError("MISSING_CONTACT_CONTRACT_FIELD")
    if value["arm"] not in ("left", "right"):
        raise ValueError("INVALID_CONTACT_ARM")
    if not isinstance(value["target"], str) or not value["target"]:
        raise ValueError("INVALID_CONTACT_TARGET")
    if not isinstance(value["phase"], str) or value["phase"] not in LOCAL_PHASES | HELD_PHASES:
        raise ValueError("INVALID_CONTACT_PHASE")
    if value.get("type", "grasp") != "grasp":
        raise ValueError("UNSUPPORTED_CONTACT_TYPE")
    payload = value["payload_ids"]
    if (not isinstance(payload, list) or any(not isinstance(n, str) or not n for n in payload)
            or len(set(payload)) != len(payload)):
        raise ValueError("INVALID_PAYLOAD_IDS")
    if not isinstance(value["supports"], dict):
        raise ValueError("INVALID_SUPPORTS")
    supports = {}
    for name, geoms in value["supports"].items():
        if (name not in payload or not isinstance(geoms, list)
                or any(not isinstance(g, str) or not g for g in geoms)
                or len(set(geoms)) != len(geoms)):
            raise ValueError("INVALID_OBJECT_SUPPORTS")
        supports[name] = list(geoms)
    result = {"type": "grasp", "arm": value["arm"], "target": value["target"],
              "phase": value["phase"], "payload_ids": list(payload), "supports": supports}
    if "target_support_transition" in value:
        if type(value["target_support_transition"]) is not bool:
            raise ValueError("TARGET_SUPPORT_TRANSITION_MUST_BE_BOOLEAN")
        result["target_support_transition"] = value["target_support_transition"]
    return result


def _json_safe(value):
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class _PreviewAbort(Exception):
    def __init__(self, status, violations=()):
        super().__init__(status)
        self.status, self.violations = status, list(violations)


class ContactExecution:
    """Read-only live observations plus an independent bounded preflight engine."""

    def __init__(self, scene, controller):
        if controller.scene is not scene or type(controller) is not DualArmController:
            raise ValueError("CONTACT_CONTROLLER_SCENE_MISMATCH")
        if (controller.substeps != 20 or controller.control_hz != 25
                or not math.isclose(float(scene.model.opt.timestep), .002, rel_tol=0., abs_tol=1e-12)):
            raise ValueError("UNSUPPORTED_CONTACT_CLOCK")
        self.scene, self.controller = scene, controller
        self.geometry = CollisionGeometry(scene)
        self._active_geoms = set(self.geometry.active)
        self._objects = {n: object_geom_ids(scene, n) & self._active_geoms for n in scene.objects}
        self._owners = {g: n for n, geoms in self._objects.items() for g in geoms}
        self._device_bodies, self._device_geoms = {}, set()
        m = scene.model
        for name in DEVICE_TARGETS:
            g = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, name)
            if g >= 0:
                body = int(m.body_weldid[int(m.geom_bodyid[g])])
                if body:
                    self._device_bodies[name] = body
                    self._device_geoms.update(self._descendants(body))
        self.clear()

    def _descendants(self, body):
        result = set()
        for g in self._active_geoms:
            b = int(self.scene.model.geom_bodyid[g])
            while b:
                if b == body:
                    result.add(g)
                    break
                b = int(self.scene.model.body_parentid[b])
        return result

    def validate_contract(self, value):
        """Resolve names without changing the helper, controller, data or model."""
        contract = validate_contract(value)
        m = self.scene.model
        target, phase = contract["target"], contract["phase"]
        if target in DEVICE_TARGETS:
            if target not in self._device_bodies or phase == "carry":
                raise ValueError("INVALID_DEVICE_TARGET_OR_PHASE")
            if m.geom(target).id not in self._active_geoms:
                raise ValueError("TARGET_HAS_NO_COLLISION_GEOMETRY")
        elif target not in self._objects or target not in contract["payload_ids"]:
            raise ValueError("FREE_TARGET_MUST_BE_DECLARED_PAYLOAD")
        elif phase == "device":
            raise ValueError("DEVICE_PHASE_REQUIRES_DEVICE_TARGET")
        if contract.get("target_support_transition", False) and (
                target not in self._objects or phase not in ("carry", "release")):
            raise ValueError("SUPPORT_TRANSITION_REQUIRES_FREE_HELD_TARGET")
        for name in contract["payload_ids"]:
            if name not in self._objects or not self._objects[name]:
                raise ValueError("UNKNOWN_OR_NONCOLLIDING_PAYLOAD")
            body = self.scene.objects[name]
            joints = range(int(m.body_jntadr[body]), int(m.body_jntadr[body] + m.body_jntnum[body]))
            if not any(m.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE for j in joints):
                raise ValueError("PAYLOAD_MUST_BE_FREE_OBJECT")
        pads = tuple(self.scene.arms[contract["arm"]].pad_geoms)
        if len(set(pads)) != 2 or not set(pads).issubset(self.geometry.robot & self._active_geoms):
            raise ValueError("TWO_ACTUAL_COLLISION_PADS_REQUIRED")
        bounded_faces = (mujoco.mjtGeom.mjGEOM_BOX, mujoco.mjtGeom.mjGEOM_CYLINDER,
                         mujoco.mjtGeom.mjGEOM_MESH)
        for name, supports in contract["supports"].items():
            for support in supports:
                g = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, support)
                if (g < 0 or g not in self._active_geoms or g in self.geometry.robot
                        or g in self._objects[name] or m.geom_type[g] not in bounded_faces):
                    raise ValueError("INVALID_BOUNDED_NONROBOT_SUPPORT")
                lower, upper = geometry_bounds(m, self.scene.data, g)
                if (not np.isfinite([lower, upper]).all() or np.any(upper <= lower)
                        or not math.isfinite(float(m.geom_rbound[g])) or m.geom_rbound[g] <= 0):
                    raise ValueError("INVALID_SUPPORT_BOUNDS")
        return contract

    def clear(self):
        """Forget all measured age and retention evidence; never mutate the owner."""
        self._contract = None
        self._target_geoms = set()
        self._target_body = None
        self._moving = set()
        self._pairs = np.empty((0, 2), dtype=int)
        self._pad_pairs = set()
        self._support_ids = {}
        self._local_origin = None
        self._grasp_anchor = None
        self._age_s = self._loss_s = self._separation_s = 0.
        self._last_tick = int(self.controller.tick)
        self._last_time = float(self.scene.data.time)
        self._warning_counts = [int(w.number) for w in self.scene.data.warning]
        self._samples = 0
        self._measured = {}
        self._violations = []
        self._deadline_at = None
        self._query_counts = {}

    def activate(self, contract):
        contract = self.validate_contract(contract)
        if self.controller.stopped:
            raise ValueError("CONTROLLER_STOPPED")
        previous = self._contract
        if previous:
            if any(previous[k] != contract[k] for k in ("arm", "target")):
                raise ValueError("CONTACT_TARGET_CHANGE_REQUIRES_CLEAR")
            if set(previous["payload_ids"]) != set(contract["payload_ids"]):
                raise ValueError("PAYLOAD_CHANGE_REQUIRES_CLEAR")
        target = contract["target"]
        geoms = self._objects[target] if target in self._objects else {self.scene.model.geom(target).id}
        if contract["phase"] in HELD_PHASES:
            fresh = (self._last_tick == self.controller.tick and
                     math.isclose(self._last_time, float(self.scene.data.time), rel_tol=0., abs_tol=1e-10))
            grasp = self._read_grasp(contract["arm"], geoms)
            # Re-certifying the exact active held phase is not a new acquisition.
            continuation = previous == contract
            if (not previous or not fresh or self._violations or self._grasp_anchor is None
                    or (not continuation and (not grasp["bilateral_contact"]
                        or self._age_s < GRASP_AGE_S - 1e-12))):
                raise ValueError("MEASURED_50MS_BILATERAL_GRASP_REQUIRED")
            if continuation and self._loss_s > LOSS_GRACE_S + 1e-12:
                raise ValueError("MEASURED_GRASP_CONTINUITY_REQUIRED")
        m = self.scene.model
        self._target_geoms = set(geoms)
        self._target_body = (self.scene.objects[target] if target in self._objects
                             else self._device_bodies[target])
        self._moving = self.geometry.robot | self._device_geoms | set().union(
            *(self._objects[n] for n in contract["payload_ids"]))
        active = self.geometry.active
        self._pairs = np.asarray([(a, b) for i, a in enumerate(active) for b in active[i+1:]
                                  if (a in self._moving or b in self._moving)
                                  and self.geometry.eligible(a, b)], dtype=int).reshape(-1, 2)
        self._pad_pairs = {tuple(sorted((p, g))) for p in self.scene.arms[contract["arm"]].pad_geoms
                           for g in geoms}
        self._support_ids = {n: {m.geom(g).id for g in names}
                             for n, names in contract["supports"].items()}
        if contract["phase"] in LOCAL_PHASES and (not previous or previous["phase"] not in LOCAL_PHASES):
            self._local_origin = tcp_pose(self.scene.data, self.scene.arms[contract["arm"]])[0].copy()
        if contract["phase"] == "release" and (not previous or previous["phase"] != "release"):
            self._separation_s = 0.
        self._contract = contract
        return copy.deepcopy(contract)

    def _read_grasp(self, side, geoms):
        m, d = self.scene.model, self.scene.data
        pads = tuple(self.scene.arms[side].pad_geoms)
        forces = np.zeros(2)
        contacts = np.zeros(2, dtype=int)
        for i in range(d.ncon):
            c = d.contact[i]
            a, b = int(c.geom1), int(c.geom2)
            for k, pad in enumerate(pads):
                if (a == pad and b in geoms) or (b == pad and a in geoms):
                    contacts[k] += 1
                    force = np.zeros(6)
                    mujoco.mj_contactForce(m, d, i, force)
                    if not np.isfinite(force).all():
                        raise ValueError("NONFINITE_CONTACT_FORCE")
                    forces[k] += max(0., float(force[0]))
        distances = [min(float(mujoco.mj_geomDistance(m, d, pad, g, .01, None))
                         for g in sorted(geoms)) for pad in pads]
        return {"arm": side, "pad_normal_forces_n": forces.tolist(),
                "pad_target_native_contacts": contacts.tolist(), "pad_target_min_distance_m": distances,
                "pads_separated": bool(not np.any(contacts) and min(distances) >= RELEASE_SEPARATION_M),
                "separation_margin_m": RELEASE_SEPARATION_M,
                "bilateral_contact": bool(np.all(forces >= .5)), "mode": "pure_contact"}

    def _relative_pose(self):
        d = self.scene.data
        p, r = tcp_pose(d, self.scene.arms[self._contract["arm"]])
        return r.T @ (d.xpos[self._target_body] - p), r.T @ d.xmat[self._target_body].reshape(3, 3)

    def _check_budget(self):
        if self._deadline_at is not None and time.monotonic() >= self._deadline_at:
            raise _PreviewAbort("WALL_BUDGET_EXCEEDED")

    def _support_contact(self, a, b, contact, normal_force):
        if (float(contact.dist) < -SUPPORT_COMPLIANCE_M - 1e-9
                or not SUPPORT_FORCE_THRESHOLD_N < normal_force <= CONTACT_FORCE_N):
            return False
        for obj_geom, support, sign in ((a, b, -1.), (b, a, 1.)):
            name = self._owners.get(obj_geom)
            if name not in self._support_ids or support not in self._support_ids[name]:
                continue
            upward = sign * float(contact.frame[2])
            center = self.scene.data.xpos[self.scene.objects[name], 2]
            if (upward > SUPPORT_NORMAL_MIN_UPWARD_Z and upward * normal_force > SUPPORT_FORCE_THRESHOLD_N
                    and float(contact.pos[2]) < float(center) + SUPPORT_CENTER_CLEARANCE_M):
                return True
        return False

    def _support_contact_role(self, a, b, contact, normal_force, data=None):
        """Per-point collision eligibility, not evidence that this point bears load."""
        if (float(contact.dist) < -SUPPORT_COMPLIANCE_M - 1e-9
                or not 0. <= normal_force <= CONTACT_FORCE_N):
            return None
        for obj_geom, support, sign in ((a, b, -1.), (b, a, 1.)):
            name = self._owners.get(obj_geom)
            if name not in self._support_ids or support not in self._support_ids[name]:
                continue
            center = float((self.scene.data if data is None else data).xpos[self.scene.objects[name], 2])
            if (math.isfinite(center) and sign * float(contact.frame[2]) > SUPPORT_NORMAL_MIN_UPWARD_Z
                    and float(contact.pos[2]) < center + SUPPORT_CENTER_CLEARANCE_M):
                return obj_geom, support
        return None

    def _base_support(self, a, b, contact):
        m = self.scene.model
        for base, support, sign in ((a, b, -1.), (b, a, 1.)):
            body = m.body(int(m.geom_bodyid[base])).name
            if (base in self.geometry.robot and body.endswith("fr3_link0")
                    and support not in self.geometry.robot and m.geom(support).name.startswith("counter_")
                    and sign * float(contact.frame[2]) > SUPPORT_NORMAL_MIN_UPWARD_Z
                    and float(contact.dist) >= -SUPPORT_COMPLIANCE_M - 1e-9):
                return True
        return False

    def _transition_ready(self):
        """Extra collision permission requires current retention, never loss grace."""
        contract = self._contract
        if (not contract or not contract.get("target_support_transition", False)
                or contract["phase"] not in ("carry", "release")
                or self._last_tick != self.controller.tick
                or not math.isclose(self._last_time, float(self.scene.data.time), rel_tol=0., abs_tol=1e-10)
                or self._age_s < GRASP_AGE_S - 1e-12 or self._grasp_anchor is None):
            return False
        if not self._read_grasp(contract["arm"], self._target_geoms)["bilateral_contact"]:
            return False
        position, rotation = self._relative_pose()
        origin, initial_rotation = self._grasp_anchor
        distance = float(np.linalg.norm(position - origin))
        angle = float(np.arccos(np.clip((np.trace(initial_rotation.T @ rotation) - 1.) / 2., -1., 1.)))
        return (math.isfinite(distance) and math.isfinite(angle)
                and distance <= GRASP_DRIFT_M and angle <= GRASP_ROTATION_RAD)

    def _static_target_support_role(self, a, b):
        m, target = self.scene.model, self._contract["target"]
        for obj_geom, support in ((a, b), (b, a)):
            if (self._owners.get(obj_geom) == target
                    and support in self._support_ids.get(target, ())
                    and int(m.body_weldid[int(m.geom_bodyid[support])]) == 0):
                return obj_geom, support
        return None

    def _transition_gap(self, role, a, b, distance, closest):
        if role is None or not math.isfinite(distance) or distance < 0.:
            return False
        points = np.asarray(closest).reshape(2, 3)
        if not np.isfinite(points).all():
            return False
        obj, support = role
        support_point, object_point = (points[0], points[1]) if a == support else (points[1], points[0])
        vector = object_point - support_point
        length = float(np.linalg.norm(vector))
        center = float(self.scene.data.xpos[self.scene.objects[self._contract["target"]], 2])
        return (math.isfinite(center) and math.isfinite(length) and length > 0.
                and vector[2] / length > SUPPORT_NORMAL_MIN_UPWARD_Z
                and support_point[2] < center + SUPPORT_CENTER_CLEARANCE_M)

    def _new_postintegration_support(self, data, pair):
        """A new compliant contact may appear after the last solver's force sample.

        Scratch collision supplies geometry only, never an invented contact force
        or load witness. The next actual solver sample still enforces force limits.
        """
        role = self._static_target_support_role(*pair)
        found = False
        if role is None:
            return False
        for i in range(data.ncon):
            contact = data.contact[i]
            a, b = int(contact.geom1), int(contact.geom2)
            if tuple(sorted((a, b))) != pair:
                continue
            found = True
            if (not np.isfinite(np.r_[contact.dist, contact.pos, contact.frame]).all()
                    or self._support_contact_role(a, b, contact, 0., data) != role):
                return False
        return found

    def _pair_limits(self, pair, supports, bases):
        if pair in self._pad_pairs:
            return -PAD_COMPLIANCE_M, CONTACT_FORCE_N
        if pair in bases:
            return -SUPPORT_COMPLIANCE_M, None
        if pair in supports:
            return -SUPPORT_COMPLIANCE_M, CONTACT_FORCE_N
        return ORDINARY_MARGIN_M, ORDINARY_FORCE_N

    def _contact_checks(self):
        m, d = self.scene.model, self.scene.data
        violations, supports, bases = [], set(), set()
        contacts, support_roles, native_pairs = [], set(), set()
        transition = self._transition_ready()
        native_count = 0
        # First collect original strong-point witnesses from this substep only.
        for i in range(d.ncon):
            self._check_budget()
            c = d.contact[i]
            a, b = int(c.geom1), int(c.geom2)
            if not ({a, b} & self._moving):
                continue
            native_count += 1
            pair = tuple(sorted((a, b)))
            native_pairs.add(pair)
            force = np.zeros(6)
            mujoco.mj_contactForce(m, d, i, force)
            if not np.isfinite(force).all() or not np.isfinite(np.r_[c.dist, c.pos, c.frame]).all():
                violations.append({"code": "NONFINITE_CONTACT", "pair_ids": [a, b],
                                   "pair": [m.geom(a).name, m.geom(b).name]})
                continue
            normal_force = float(force[0])
            role = self._support_contact_role(a, b, c, normal_force)
            base = self._base_support(a, b, c)
            if role is not None and self._support_contact(a, b, c, normal_force):
                support_roles.add(role)
                supports.add(pair)
            if transition and role is not None and role == self._static_target_support_role(a, b):
                supports.add(pair)
            if base:
                bases.add(pair)
            contacts.append((a, b, c, pair, normal_force, role, base))
        # A weak point borrows classification only from its exact object->support role.
        for a, b, c, pair, normal_force, role, base in contacts:
            self._check_budget()
            supported = role is not None and (role in support_roles or
                (transition and role == self._static_target_support_role(a, b)))
            margin, limit = self._pair_limits(pair, {pair} if supported else set(), {pair} if base else set())
            if float(c.dist) < margin - 1e-9 or (limit is not None and normal_force > limit):
                violations.append({"code": "CONTACT_POLICY_VIOLATION", "pair_ids": [a, b],
                                   "pair": [m.geom(a).name, m.geom(b).name],
                                   "distance_m": float(c.dist), "minimum_distance_m": margin,
                                   "normal_force_n": normal_force, "limit_n": limit})
        # Query post-integration geometry on another scratch MjData. No live mj_forward.
        gd = self.geometry.d
        gd.qpos[:] = d.qpos
        mujoco.mj_kinematics(m, gd)
        if not np.isfinite(gd.geom_xpos).all() or not np.isfinite(gd.geom_xmat).all():
            raise ValueError("NONFINITE_PREDICTED_GEOMETRY")
        pairs = self._pairs
        lower = np.linalg.norm(gd.geom_xpos[pairs[:, 0]] - gd.geom_xpos[pairs[:, 1]], axis=1)
        lower -= self.geometry.radius[pairs[:, 0]] + self.geometry.radius[pairs[:, 1]]
        planes = np.any(m.geom_type[pairs] == mujoco.mjtGeom.mjGEOM_PLANE, axis=1)
        lower[planes] = -np.inf
        queries, postintegration_pairs = 0, []
        scratch_collision_ready = False
        for k in np.flatnonzero(lower < ORDINARY_MARGIN_M + 1e-9):
            self._check_budget()
            a, b = map(int, pairs[k])
            closest = np.zeros(6)
            distance = float(mujoco.mj_geomDistance(m, gd, a, b, ORDINARY_MARGIN_M + 1e-6, closest))
            queries += 1
            margin, _ = self._pair_limits((a, b), supports, bases)
            if (transition and (a, b) not in native_pairs and
                    self._transition_gap(self._static_target_support_role(a, b), a, b, distance, closest)):
                margin = 0.
            if (transition and (a, b) not in native_pairs and
                    -SUPPORT_COMPLIANCE_M - 1e-9 <= distance < 0.
                    and self._static_target_support_role(a, b) is not None):
                if not scratch_collision_ready:
                    mujoco.mj_collision(m, gd)
                    scratch_collision_ready = True
                if self._new_postintegration_support(gd, (a, b)):
                    margin = -SUPPORT_COMPLIANCE_M
                    postintegration_pairs.append([a, b])
            if not math.isfinite(distance) or distance < margin - 1e-9:
                violations.append({"code": "CLEARANCE_VIOLATION", "pair_ids": [a, b],
                                   "pair": [m.geom(a).name, m.geom(b).name],
                                   "distance_m": distance, "minimum_distance_m": margin})
        self._query_counts = {"eligible_near_pairs": len(pairs), "exact_distance_queries": queries,
                              "native_contacts_checked": native_count,
                              "held_target_support_transition_ready": transition,
                              "new_postintegration_support_pairs": postintegration_pairs}
        return violations

    def _payload_support_violations(self, contract, measured):
        if contract["phase"] not in HELD_PHASES:
            return []
        m, edges = self.scene.model, {}
        for name, evidence in measured.items():
            edges[name] = set()
            for row in evidence["contacts"]:
                g = m.geom(row["support_geom"]).id
                if row["qualifies_as_upward_support"] and self._owners.get(g) in contract["payload_ids"]:
                    edges[name].add(self._owners[g])
        result = []
        for name in contract["payload_ids"]:
            if name == contract["target"]:
                continue
            seen, pending = set(), [name]
            while pending:
                n = pending.pop()
                if n not in seen:
                    seen.add(n)
                    pending.extend(edges.get(n, set()) - seen)
            if contract["target"] not in seen:
                result.append({"code": "PAYLOAD_SUPPORT_LOST", "object": name,
                               "required_support_chain_to": contract["target"]})
        return result

    def sample(self, controller):
        """Observe exactly one real post-step; duplicates cannot earn contact age."""
        if controller is not self.controller or controller.scene is not self.scene:
            return [{"code": "CONTACT_CONTROLLER_SCENE_MISMATCH"}]
        if self._contract is None:
            return []
        try:
            violations = self._sample(controller)
        except (ValueError, RuntimeError, IndexError, KeyError, FloatingPointError) as error:
            violations = [{"code": "CONTACT_SAMPLE_ERROR", "detail": str(error)}]
        self._violations = _json_safe(violations)
        return copy.deepcopy(self._violations)

    def _sample(self, controller):
        self._check_budget()
        m, d = self.scene.model, self.scene.data
        if not math.isfinite(float(d.time)) or any(not np.isfinite(getattr(d, key)).all() for key in
                ("qpos", "qvel", "qacc", "qacc_warmstart", "ctrl", "act", "xpos", "xmat", "geom_xpos", "geom_xmat")):
            return [{"code": "NONFINITE_PHYSICS_STATE"}]
        violations = []
        counts = [int(w.number) for w in d.warning]
        if any(now > old for now, old in zip(counts, self._warning_counts)):
            violations.append({"code": "PHYSICS_WARNING", "warning_counts": counts})
        self._warning_counts = counts
        elapsed = float(d.time) - self._last_time
        delta_tick = int(controller.tick) - self._last_tick
        advanced = delta_tick == 1 and math.isclose(elapsed, float(m.opt.timestep), rel_tol=0., abs_tol=1e-10)
        if not advanced and (delta_tick != 0 or not math.isclose(elapsed, 0., rel_tol=0., abs_tol=1e-10)):
            violations.append({"code": "CONTACT_SAMPLE_GAP_OR_RESET", "delta_tick": delta_tick, "elapsed_s": elapsed})
            self._age_s = 0.
            self._separation_s = 0.
            if self._contract["phase"] in HELD_PHASES:
                self._loss_s = LOSS_GRACE_S + float(m.opt.timestep)
            else:
                self._grasp_anchor = None
        self._last_tick, self._last_time = int(controller.tick), float(d.time)
        grasp = self._read_grasp(self._contract["arm"], self._target_geoms)
        if not grasp["pads_separated"]:
            self._separation_s = 0.
        if advanced:
            self._samples += 1
            if self._contract["phase"] == "release" and grasp["pads_separated"]:
                self._separation_s += float(m.opt.timestep)
            if grasp["bilateral_contact"]:
                self._age_s += float(m.opt.timestep)
                self._loss_s = 0.
            else:
                self._age_s = 0.
                self._loss_s += float(m.opt.timestep)
                if self._contract["phase"] not in HELD_PHASES:
                    self._grasp_anchor = None
        if self._age_s >= GRASP_AGE_S - 1e-12 and self._grasp_anchor is None:
            self._grasp_anchor = self._relative_pose()
        phase = self._contract["phase"]
        if phase in LOCAL_PHASES:
            p, _ = tcp_pose(d, self.scene.arms[self._contract["arm"]])
            distance = float(np.linalg.norm(p - self._local_origin))
            if distance > LOCAL_TRAVEL_M + 1e-9:
                violations.append({"code": "LOCAL_PHASE_TRAVEL_LIMIT", "distance_m": distance, "limit_m": LOCAL_TRAVEL_M})
        else:
            if self._loss_s > LOSS_GRACE_S + 1e-12:
                violations.append({"code": "GRASP_LOST", "lost_seconds": self._loss_s, "limit_s": LOSS_GRACE_S})
            if self._grasp_anchor is None:
                violations.append({"code": "GRASP_ANCHOR_MISSING"})
            else:
                p, r = self._relative_pose()
                origin, rotation = self._grasp_anchor
                distance = float(np.linalg.norm(p - origin))
                angle = float(np.arccos(np.clip((np.trace(rotation.T @ r) - 1.) / 2., -1., 1.)))
                if distance > GRASP_DRIFT_M or angle > GRASP_ROTATION_RAD:
                    violations.append({"code": "GRASP_RELATIVE_MOTION", "translation_m": distance, "rotation_rad": angle,
                                       "translation_limit_m": GRASP_DRIFT_M, "rotation_limit_rad": GRASP_ROTATION_RAD})
        supports = {n: controller.devices.support_evidence(n, self._contract["supports"].get(n, []))
                    for n in self._contract["payload_ids"]}
        violations.extend(self._payload_support_violations(self._contract, supports))
        bodies = {n: self.scene.objects[n] for n in self._contract["payload_ids"]}
        bodies.update(self._device_bodies)
        velocities = {}
        for name, body in bodies.items():
            velocity = np.zeros(6)
            mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, body, velocity, 0)
            linear, angular = float(np.linalg.norm(velocity[3:])), float(np.linalg.norm(velocity[:3]))
            velocities[name] = {"linear_m_s": linear, "angular_rad_s": angular}
            if not np.isfinite(velocity).all() or linear > PAYLOAD_SPEED_M_S or angular > PAYLOAD_ANGULAR_RAD_S:
                violations.append({"code": "PAYLOAD_OR_DEVICE_SPEED", "object": name,
                                   **velocities[name], "linear_limit_m_s": PAYLOAD_SPEED_M_S,
                                   "angular_limit_rad_s": PAYLOAD_ANGULAR_RAD_S})
        violations.extend({"code": "ORIGINAL_CONTACT_GUARD", "evidence": item}
                          for item in controller.contact_guard.inspect())
        violations.extend(self._contact_checks())
        if controller.stopped:
            violations.append({"code": "CONTROLLER_STOPPED", "reason": controller.stop_reason})
        self._measured = {"grasp": grasp, "support": supports, "velocities": velocities}
        self._check_budget()
        return violations

    def feedback(self):
        return _json_safe({"active": self._contract is not None, "contract": copy.deepcopy(self._contract),
                           "evidence_tick": self._last_tick if self._samples else None,
                           "measured_post_step_samples": self._samples, "measured_grasp_age_s": self._age_s,
                           "grasp_loss_s": self._loss_s, "measured_pad_separation_s": self._separation_s,
                           "measured": copy.deepcopy(self._measured),
                           "violations": copy.deepcopy(self._violations), "checks": dict(self._query_counts),
                           "task_status": "NOT_INFERRED", "task_success": "NOT_INFERRED",
                           "release_support_success": "NOT_INFERRED", "trajectory_completion_is_task_success": False})

    def _clone(self):
        """Copy integration/warm-start state and all controller/guard/device fields."""
        m = self.scene.model
        data = mujoco.MjData(m)
        mujoco.mj_copyData(data, m, self.scene.data)
        scene = replace(self.scene, data=data)
        controller = DualArmController(scene)
        nested = {"scene", "ik_scratch", "tcp_scratch", "contact_guard", "devices"}
        for key, value in vars(self.controller).items():
            if key not in nested:
                setattr(controller, key, copy.deepcopy(value))
        for key in ("ik_scratch", "tcp_scratch"):
            mujoco.mj_copyData(getattr(controller, key), m, getattr(self.controller, key))
        for key in ("contact_guard", "devices"):
            source, destination = getattr(self.controller, key), getattr(controller, key)
            for field, value in vars(source).items():
                if field != "scene":
                    setattr(destination, field, copy.deepcopy(value))
            destination.scene = scene
        engine = ContactExecution(scene, controller)
        for key, value in vars(self).items():
            if key not in {"scene", "controller", "geometry"}:
                setattr(engine, key, copy.deepcopy(value))
        return engine

    def preview(self, waypoints, contract, max_wall_s=MAX_WALL_S):
        """Discrete scratch dynamics only; no continuous or task-success claim."""
        started = time.monotonic()
        result = {"pass": False, "status": "NOT_CHECKED", "kind": "DYNAMICS_PREFLIGHT",
                  "evidence": "SCRATCH_DYNAMICS_ONLY", "continuous_certificate": False,
                  "task_success": "NOT_INFERRED", "waypoints_checked": 0, "physics_substeps": 0,
                  "budget": {"max_waypoints": MAX_WAYPOINTS, "max_physics_substeps": MAX_SUBSTEPS,
                             "max_wall_s": MAX_WALL_S}}
        engine = None
        tick, sim_time = int(self.controller.tick), float(self.scene.data.time)
        try:
            if (isinstance(max_wall_s, bool) or not isinstance(max_wall_s, (int, float))
                    or not math.isfinite(max_wall_s) or not 0 < max_wall_s <= MAX_WALL_S):
                raise ValueError("INVALID_WALL_BUDGET")
            result["budget"]["max_wall_s"] = float(max_wall_s)
            deadline = started + float(max_wall_s)
            if self.controller.frame_step:
                raise ValueError("CONTROL_BOUNDARY_REQUIRED")
            if self.controller.stopped:
                raise ValueError("CONTROLLER_STOPPED")
            if not isinstance(waypoints, (list, tuple)) or not 1 <= len(waypoints) <= MAX_WAYPOINTS:
                raise ValueError("INVALID_WAYPOINT_COUNT")
            contract = self.validate_contract(contract)
            engine = self._clone()
            engine._deadline_at = deadline
            engine._check_budget()
            engine.activate(contract)
            controller = engine.controller
            if contract["target"] in DEVICE_TARGETS:
                controller.contact_guard.allow_device_contact(contract["target"])
            result["start_tick"] = tick
            result["scope"] = {"robot_arms": ["left", "right"], "payload_ids": contract["payload_ids"],
                               "moving_device_bodies": sorted({engine.scene.model.body(b).name
                                                               for b in engine._device_bodies.values()}),
                               "native_contacts": "robot, declared payload and whole moving devices",
                               "near_pairs": "all eligible pairs touching monitored bodies; inherited masks/exclusions",
                               "eligible_pair_count": len(engine._pairs), "support_compliance_m": SUPPORT_COMPLIANCE_M,
                               "pad_target_compliance_m": PAD_COMPLIANCE_M,
                               "sampling": "initial state and every 2ms post-step; no between-step certificate"}
            violations = engine.sample(controller)
            if violations:
                raise _PreviewAbort("INITIAL_CONTACT_POLICY_FAIL", violations)

            def observe(scratch_controller):
                result["physics_substeps"] += 1
                if result["physics_substeps"] > MAX_SUBSTEPS:
                    raise _PreviewAbort("SUBSTEP_BUDGET_EXCEEDED")
                violations = engine.sample(scratch_controller)
                if violations:
                    raise _PreviewAbort("CONTACT_POLICY_FAIL", violations)
                engine._check_budget()

            for index, waypoint in enumerate(waypoints):
                engine._check_budget()
                values = np.asarray(waypoint, dtype=float)
                if values.shape != (16,) or not np.isfinite(values).all():
                    raise ValueError("EXPECTED_16_FINITE_TARGETS")
                prepared = controller.prepare_joint_targets(values)
                if not np.allclose(prepared, values, rtol=0., atol=1e-9):
                    result["waypoint_index"] = index
                    raise ValueError("TRAJECTORY_REQUIRES_RATE_LIMITING")
                accepted = controller.accept_joint_targets(values)
                if not np.allclose(accepted, values, rtol=0., atol=1e-9):
                    raise ValueError("ACCEPTED_PATH_CHANGED")
                controller.advance_substeps(20, observe)
                result["waypoints_checked"] += 1
            engine._check_budget()
            result.update({"pass": True, "status": "DISCRETE_DYNAMICS_PREFLIGHT_PASS"})
        except _PreviewAbort as error:
            result.update(status=error.status, violations=error.violations)
        except (ValueError, TypeError, RuntimeError, IndexError, KeyError, OverflowError, FloatingPointError) as error:
            result.update(status="PREFLIGHT_REJECTED", error=str(error))
        finally:
            result["elapsed_wall_s"] = time.monotonic() - started
            if result["elapsed_wall_s"] > result["budget"]["max_wall_s"]:
                result.update({"pass": False, "status": "WALL_BUDGET_EXCEEDED"})
            if engine is not None:
                result["predicted_feedback"] = engine.feedback()
                result["end_tick"] = int(engine.controller.tick)
            if self.controller.tick != tick or self.scene.data.time != sim_time:
                result.update({"pass": False, "status": "LIVE_STATE_CHANGED_DURING_PREVIEW"})
        return _json_safe(result)
