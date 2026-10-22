"""Finite APPLE witness; existing IK/controller, full collision and real contacts.

Hypothetical payload poses are scratch-only. No runtime free-body pose writes,
new/payload constraints, friction changes, disabled collisions, or private controller.
"""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path
import sys
import time
import mujoco
import numpy as np
from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.contact import ContactTracker, contact_evidence, object_geom_ids
from robot_arm.kitchen.control import DualArmController, solve_ik, tcp_pose
from robot_arm.kitchen.gripper import fingertip_gap
from robot_arm.kitchen.mechanics import coupling_manifest, coupling_sample
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.payload_geometry import CarriedGeometry
from robot_arm.kitchen.platform import PhysicsEvidence
from robot_arm.kitchen.registry import SceneRegistry, geometry_bounds, object_bounds

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "experiments/kitchen_platform_v3/20261021T013529"
FULL = RUN / "fullops_20261021"
PLAN = ROOT / "docs/kitchen_platform_v3_20261020/APPROVED_PLAN.md"
AUTH = FULL / "AUTHORIZATION.md"
PLAN_HASH = "135b22143a15d5082717c04345072d1568481abe0d8a419140a1a13f3a4fd2d3"
AUTH_HASH = "1c2cb757610535066725c3aa1006fab09c3e96089c5c3103df6add761ab722b3"
GOAL = "01a15914-fce0-7501-9b2f-cf19e1690c24"
FRUIT_SOURCE = RUN / "p1/apple_depth15/apple.json"
FAUCET_SOURCE = RUN / "resume_20261021/faucet_geometry_005/faucet.json"
DOWN = np.array([[0., -1., 0.], [-1., 0., 0.], [0., 0., -1.]])

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write_json(path, value):
    serialized = json.dumps(value, indent=2, allow_nan=False)
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(serialized)

class WitnessFailure(RuntimeError):
    pass

def source_identity(scene):
    return {"goal_id": GOAL, "worktree": str(ROOT), "run_directory": str(RUN),
        "protocol_path": str(PLAN), "approved_protocol_sha256": PLAN_HASH,
        "authorization_path": str(AUTH), "authorization_sha256": AUTH_HASH,
        "witness_path": str(Path(__file__).resolve()), "witness_sha256": sha(__file__),
        "python_executable": sys.executable, "arguments": sys.argv[1:],
        "geometry_implementation_sha256": hashlib.sha256(inspect.getsource(Geometry).encode("utf-8")).hexdigest(),
        "manifest": SceneRegistry(scene).manifest(), "fixed_object": "apple",
        "internal_mechanical_constraints": coupling_manifest(scene),
        "exact_imported_hardware_transmission": "NOT_CERTIFIED",
        "source": "programmatic_validation",
        "old_pose_references_not_inherited_passes": {
            str(FRUIT_SOURCE): sha(FRUIT_SOURCE), str(FAUCET_SOURCE): sha(FAUCET_SOURCE)},
        "approximation": "Existing wash state timer only; no fluid physics claim",
        "public_api_integration": "MAIN_AGENT_RESPONSIBILITY_NOT_VERIFIED_HERE"}

def snapshot(scene):
    velocity = np.zeros(6)
    mujoco.mj_objectVelocity(scene.model, scene.data, mujoco.mjtObj.mjOBJ_BODY,
                            scene.objects["apple"], velocity, 0)
    return {"position_m": scene.data.xpos[scene.objects["apple"]].tolist(),
            "angular_rad_s": float(np.linalg.norm(velocity[:3])),
            "linear_m_s": float(np.linalg.norm(velocity[3:]))}

class Geometry:
    """One finite route; all hypothetical payload writes target scratch states."""
    def __init__(self, scene):
        self.scene, self.m = scene, scene.model
        self.arm = scene.arms["left"]
        self.rows = []
        self.home = scene.data.qpos.copy()
        self.q = self.home.copy()
        self.started = time.monotonic()
        self.checker = CollisionGeometry(scene)
        self.carried = None
        self.apple_geoms = object_geom_ids(scene, "apple")
        self.intentional = [(p, g) for p in self.arm.pad_geoms for g in self.apple_geoms]
        self.intentional += [(p, self.m.geom("faucet_handle_geom").id) for p in self.arm.pad_geoms]
        self.intentional += [(g, self.m.geom(n).id) for g in self.apple_geoms
                             for n in ("basket_bottom", "tray_bottom")]
        self.intentional += [(g, i) for g in self.apple_geoms for i in self.checker.active
                             if self.m.geom(i).name.startswith("v3_counter_")]
        self.faucet = json.loads(FAUCET_SOURCE.read_text(encoding="utf-8"))
        self.hinge = int(self.m.jnt_qposadr[self.m.joint("faucet_switch").id])

    def data(self):
        d = mujoco.MjData(self.m)
        d.qpos[:] = self.q
        mujoco.mj_forward(self.m, d)
        return d

    def append(self, name, target, *, support_separation=False):
        if time.monotonic() - self.started > 300:
            raise WitnessFailure("BOUNDED_GEOMETRY_TIMEOUT")
        checker = self.carried or self.checker
        pairs = checker.pairs(("apple", "faucet_handle"))
        support = None
        invariant_support = []
        if self.carried is None:
            # Unmoving supported bodies have invariant support contact, not a
            # robot clearance claim. Report the native convex-distance residue.
            selected = []
            for pair in pairs:
                support_pair = (any(int(g) in self.apple_geoms for g in pair) and
                    any(self.m.geom(int(g)).name.startswith("v3_counter_") or
                        self.m.geom(int(g)).name in ("basket_bottom", "tray_bottom") for g in pair))
                if support_pair:
                    distance = float(checker.distances(self.q, np.asarray([pair]))[0])
                    invariant_support.append({"pair": [self.m.geom(int(g)).name for g in pair],
                                              "distance_m": distance})
                    if distance < -.00001:
                        raise WitnessFailure("SCRATCH_SUPPORT_PENETRATION")
                else:
                    selected.append(pair)
            pairs = np.asarray(selected, dtype=int).reshape(-1, 2)
        if support_separation:
            support = checker.support_lift_proof(self.q, target, .8)
            pairs = np.array([p for p in pairs if not (
                any(int(g) in self.apple_geoms for g in p) and
                any(self.m.geom(int(g)).name.startswith("v3_counter_") for g in p))],
                dtype=int).reshape(-1, 2)
        proof = checker.certificate(self.q, target, pairs,
            intentional_pairs=self.intentional, max_evaluations=3500)
        row = {"stage": name, "qpos": target.tolist(), "continuous": proof,
            "scratch_payload_hypothesis": self.carried is not None,
            "invariant_static_support_contacts": invariant_support,
            "pass": bool(proof["pass"] and (support is None or support["pass"]))}
        if support is not None:
            row["support_separation"] = support
        self.rows.append(row)
        if not row["pass"]:
            raise WitnessFailure("GEOMETRY_FAIL_" + name)
        self.q = checker.evaluated(target) if self.carried else target.copy()

    def point(self, name, position, rotation=DOWN, *, support_separation=False):
        result = solve_ik(replace(self.scene, data=self.data()), "left", position,
                          rotation, max_iterations=350,
                          position_tolerance=.0002, rotation_tolerance=.002)
        if not result.success:
            self.rows.append({"stage": name, "pass": False, "reason": "IK_FAILED",
                "target_m": np.asarray(position).tolist(),
                "position_error_m": result.position_error,
                "rotation_error_rad": result.rotation_error})
            raise WitnessFailure("IK_FAIL_" + name)
        target = self.q.copy()
        target[list(self.arm.qpos)] = result.q
        self.append(name, target, support_separation=support_separation)

    def line(self, name, position, rotation=DOWN, *, spacing=.025, support_separation=False):
        start = tcp_pose(self.data(), self.arm)[0]
        count = max(1, int(np.ceil(np.linalg.norm(np.asarray(position) - start) / spacing)))
        for index in range(1, count + 1):
            self.point(name + "_" + str(index), start + (np.asarray(position) - start) * index / count,
                       rotation, support_separation=support_separation and index == 1)

    def fingers(self, name, slide):
        q = self.q.copy()
        q[list(self.arm.fingers)] = slide
        self.append(name, q)

    def run(self):
        reason = None
        try:
            d = self.data()
            p = d.xpos[self.scene.objects["apple"]].copy()
            basket = d.xpos[self.m.body("basket").id].copy()
            tray = d.xpos[self.scene.objects["wood_tray"]].copy()
            self.point("pick_pregrasp", p + [0, 0, .18])
            self.line("pick_down", p + [0, 0, -.005])
            self.fingers("pick_close_scratch74mm", (.0834440004842794 - .074) / 2)
            self.carried = CarriedGeometry(self.scene, self.q, "left", ["apple"])
            self.line("pick_lift", p + [0, 0, .18], support_separation=True)
            self.line("basket_over", basket + [0, 0, .40])
            self.line("basket_lower", basket + [0, 0, .0415])
            self.carried = None
            self.fingers("basket_open", 0.)
            self.line("basket_withdraw", basket + [0, 0, .40])
            home = self.q.copy()
            home[list(self.arm.qpos)] = self.home[list(self.arm.qpos)]
            self.append("basket_via_home_before_faucet", home)
            for row in self.faucet["rows"]:
                q = self.q.copy()
                old = np.asarray(row["qpos"])
                q[list(self.arm.qpos)] = old[:7]
                q[list(self.arm.fingers)] = old[7:9]
                if row["stage"].startswith(("faucet_", "return_faucet_")):
                    q[self.hinge] = np.deg2rad(int(row["stage"].split("_")[-1]))
                self.append("tap_" + row["stage"], q)
            self.point("regrasp_over", basket + [0, 0, .40])
            self.line("regrasp_down", basket + [0, 0, .0415])
            self.fingers("regrasp_close_scratch74mm", (.0834440004842794 - .074) / 2)
            self.carried = CarriedGeometry(self.scene, self.q, "left", ["apple"])
            self.line("regrasp_lift", basket + [0, 0, .40])
            self.line("tray_front_left_corridor", np.array([basket[0], tray[1], 1.06]))
            self.line("tray_over", tray + [0, 0, .23])
            self.line("tray_lower", tray + [0, 0, .0415])
            self.carried = None
            self.fingers("tray_open", 0.)
            self.line("tray_withdraw", tray + [0, 0, .23])
        except (WitnessFailure, ValueError) as error:
            reason = str(error)
        return {"status": "APPLE_GEOMETRY_PASS_ONLY" if reason is None else "GEOMETRY_FAIL",
            "reason": reason, "full_geometric_chain": reason is None,
            "physics_steps": 0, "rows": self.rows, "elapsed_s": time.monotonic() - self.started,
            "payload_hypothesis": "Scratch rigid transform only; never a runtime attachment",
            "geometry_does_not_prove_contact_tracking_or_grasp": True}


def historical_diagnosis():
    """Replay only the three preserved APPLE reports and round-three hash chain."""
    from robot_arm.kitchen.platform import digest
    reports = []
    for index in (1, 2, 3):
        path = RUN / ("p3/apple_round" + str(index) + "/report.json")
        report = json.loads(path.read_text(encoding="utf-8"))
        reports.append({"path": str(path), "sha256": sha(path),
            "status": report["status"], "reason": report["reason"],
            "checks": report["checks"], "scene_hash": report["scene_hash"],
            "maximum_lift_m": report["maximum_lift_m"]})
    source = RUN / "p3/apple_round3/episode_d79f96b4fbb5482e93e9bd57e0d7a2fb/physics.jsonl"
    previous, count, last_tick = "0" * 64, 0, 0
    samples = []
    metadata = None
    with source.open(encoding="utf-8") as stream:
        for line in stream:
            envelope = json.loads(line)
            expected = envelope.pop("sha256")
            if envelope["previous_sha256"] != previous or digest(envelope) != expected:
                raise WitnessFailure("HISTORICAL_HASH_CHAIN_INVALID")
            previous = expected
            record = envelope["record"]
            if record["type"] == "metadata":
                metadata = record
                apple_index = record["physics_contract"]["object_names"].index("apple")
            elif record["type"] == "physics":
                if record["sim_tick"] != last_tick + 1:
                    raise WitnessFailure("HISTORICAL_TICK_GAP")
                last_tick = record["sim_tick"]
                count += 1
                if last_tick in (10240, 11740, 13780, 14780):
                    state = record["object_pose_velocity"][apple_index]
                    fingers = record["finger_qpos"][0]
                    samples.append({"tick": last_tick, "time_s": record["sim_time_s"],
                        "position_m": state[:3], "angular_rad_s": float(np.linalg.norm(state[7:10])),
                        "linear_m_s": float(np.linalg.norm(state[10:13])),
                        "fingers_m": fingers, "finger_difference_m": abs(fingers[0] - fingers[1])})
    return {"status": "HISTORICAL_DIAGNOSIS_COMPLETE_NOT_NEW_PHYSICS_PASS",
        "reason": None, "goal_id": GOAL, "approved_protocol_sha256": PLAN_HASH,
        "authorization_sha256": AUTH_HASH, "historical_reports": reports,
        "trace_path": str(source), "trace_sha256": sha(source),
        "verified_hash_chain": True, "physics_samples_read": count,
        "historical_manifest": metadata["manifest"], "selected_real_samples": samples,
        "ranked_hypotheses": [
            "Mean-tendon differential finger drift explains asymmetric pinch/spin; compare explicit native finger synchronization only after main authorization",
            "Support-before-open and measured jaw separation should reduce release disturbance without changing friction",
            "Vertical withdrawal should avoid the observed post-open increase in angular velocity"],
        "new_geometry": "Home connector avoids faucet spout; front-left corridor avoids base-near IK failure",
        "historical_failures_preserved": True, "new_physics_steps": 0}


class AppleCycle:
    def __init__(self, scene, output):
        self.scene, self.output = scene, output
        self.controller = DualArmController(scene)
        self.registry = SceneRegistry(scene)
        self.arm = scene.arms["left"]
        self.home_q7 = scene.data.qpos[list(self.arm.qpos)].copy()
        self.tracker = ContactTracker(scene, "left", "apple")
        self.phase = "initialized"
        self.trace, self.checks = [], {}
        self.geometry = []
        self.carrying = False
        self.grasp_frame = None
        self.loss_seconds = 0.
        self.minimum_hold_lift = float("inf")
        self.minimum_second_hold_lift = float("inf")
        self.second_origin_z = None
        self.maximum_lift = 0.
        self.initial_z = None
        self.max_tcp_speed = 0.
        self.max_joint_speed = 0.
        self.stability = None
        self.handle_seconds = 0.
        self.maximum_handle_seconds = 0.
        self.maximum_faucet_angle = 0.
        self.deadline = time.monotonic() + 900.
        self.writer = PhysicsEvidence(output, self, "programmatic_validation")
        self.original_masks = (scene.model.geom_contype.copy(), scene.model.geom_conaffinity.copy())
        self.original_friction = scene.model.geom_friction.copy()
        self.original_condim = scene.model.geom_condim.copy()
        self.original_eq_count = scene.model.neq
        self.coupling_definitions = coupling_manifest(scene)
        self.joint_coupling_metrics = {row["id"]: {**row,
            "maximum_residual_m": 0., "maximum_solver_residual_m": 0.,
            "maximum_constraint_reaction_n": 0.} for row in self.coupling_definitions}
        self.renderer = None

    def manifest(self):
        return self.registry.manifest()

    def sample(self, controller):
        self.writer.sample(controller)
        self.tracker.update(controller)
        m, d = self.scene.model, self.scene.data
        for row in coupling_sample(self.scene, self.coupling_definitions):
            metric = self.joint_coupling_metrics[row["id"]]
            metric["maximum_residual_m"] = max(metric["maximum_residual_m"], abs(row["residual_m"]))
            metric["maximum_solver_residual_m"] = max(metric["maximum_solver_residual_m"],
                max((abs(v) for v in row["solver_residual_m"]), default=0.))
            metric["maximum_constraint_reaction_n"] = max(metric["maximum_constraint_reaction_n"],
                max((abs(v) for v in row["reaction_n"]), default=0.))
        velocity = np.zeros(6)
        for arm in self.scene.arms.values():
            jac = np.zeros((3, m.nv))
            jacr = np.zeros((3, m.nv))
            mujoco.mj_jac(m, d, jac, jacr, tcp_pose(d, arm)[0], arm.tcp_body)
            self.max_tcp_speed = max(self.max_tcp_speed, float(np.linalg.norm(jac @ d.qvel)))
            self.max_joint_speed = max(self.max_joint_speed, float(np.max(np.abs(d.qvel[list(arm.dofs)]))))
        if self.initial_z is not None:
            lift = float(d.xpos[self.scene.objects["apple"], 2] - self.initial_z)
            self.maximum_lift = max(self.maximum_lift, lift)
            if self.phase == "first_hold":
                self.minimum_hold_lift = min(self.minimum_hold_lift, lift)
                if lift < .03 or not self.tracker.latest["bilateral"]:
                    raise WitnessFailure("LIFT_30MM_1S_RETENTION_FAILED")
            if self.phase == "second_hold":
                second_lift = float(d.xpos[self.scene.objects["apple"], 2] - self.second_origin_z)
                self.minimum_second_hold_lift = min(self.minimum_second_hold_lift, second_lift)
                if second_lift < .03 or not self.tracker.latest["bilateral"]:
                    raise WitnessFailure("SECOND_LIFT_30MM_1S_RETENTION_FAILED")
        if self.carrying:
            self.loss_seconds = 0. if self.tracker.latest["bilateral"] else self.loss_seconds + m.opt.timestep
            tcp, rotation = tcp_pose(d, self.arm)
            frame = rotation.T @ (d.xpos[self.scene.objects["apple"]] - tcp)
            if self.loss_seconds > .1 or np.linalg.norm(frame - self.grasp_frame) > .015:
                raise WitnessFailure("APPLE_GRASP_LOSS_OR_15MM_SLIP")
        handle = m.geom("faucet_handle_geom").id
        forces = {int(p): 0. for p in self.arm.pad_geoms}
        for index in range(d.ncon):
            c = d.contact[index]
            a, b = int(c.geom1), int(c.geom2)
            pad = b if a == handle else a if b == handle else None
            if pad in forces:
                wrench = np.zeros(6)
                mujoco.mj_contactForce(m, d, index, wrench)
                forces[pad] += max(0., float(wrench[0]))
        self.handle_seconds = self.handle_seconds + m.opt.timestep if min(forces.values()) >= .5 else 0.
        self.maximum_handle_seconds = max(self.maximum_handle_seconds, self.handle_seconds)
        self.maximum_faucet_angle = max(self.maximum_faucet_angle, controller.devices.angle("faucet_switch"))
        if self.stability is not None:
            window = self.stability
            state = snapshot(self.scene)
            inside = controller.devices.in_basket("apple") if window["label"] == "basket" else self.in_tray()
            okay = (state["linear_m_s"] < .02 and state["angular_rad_s"] < .05 and inside and
                controller.devices.supports("apple", [window["support"]]) and
                not controller.devices.touching_pads("apple"))
            if okay:
                window["seconds"] += m.opt.timestep
                window["max_linear"] = max(window["max_linear"], state["linear_m_s"])
                window["max_angular"] = max(window["max_angular"], state["angular_rad_s"])
            else:
                window.update(seconds=0., max_linear=0., max_angular=0.)

    def in_tray(self):
        lo, hi = object_bounds(self.scene, "apple", frame_body="wood_tray")
        return bool(np.all(lo >= [-.23, -.15, .007]) and np.all(hi <= [.23, .15, .14]))

    def frame(self):
        if self.scene.data.time >= 300 or time.monotonic() >= self.deadline:
            raise WitnessFailure("BOUNDED_WITNESS_TIMEOUT")
        self.controller.advance(self.sample)
        if self.controller.stopped:
            raise WitnessFailure(str(self.controller.stop_reason))

    def wait(self, seconds):
        for _ in range(int(np.ceil(seconds * self.controller.control_hz))):
            self.frame()

    def mark(self, phase):
        self.phase = phase
        self.trace.append({"phase": phase, "tick": self.controller.tick,
            "time_s": float(self.scene.data.time), "apple": snapshot(self.scene),
            "tcp_m": tcp_pose(self.scene.data, self.arm)[0].tolist(),
            "contact": contact_evidence(self.scene, "left", "apple"),
            "basket_support": self.controller.devices.support_evidence("apple", ["basket_bottom"]),
            "tray_support": self.controller.devices.support_evidence("apple", ["tray_bottom"]),
            "device_state": self.controller.devices.snapshot(),
            "finger_gap": fingertip_gap(self.scene.model, self.scene.data, self.arm)})
        if self.renderer is None:
            self.renderer = mujoco.Renderer(self.scene.model, height=700, width=1000)
        from PIL import Image
        camera = "v3_front" if phase.startswith("tray") else "v3_faucet"
        self.renderer.update_scene(self.scene.data, camera=camera)
        Image.fromarray(self.renderer.render()).save(self.output / (phase + ".png"))
        print(json.dumps({"phase": phase, "time_s": float(self.scene.data.time),
                          "apple": snapshot(self.scene)}), flush=True)

    def joints(self, name, q):
        self.phase = name
        q = np.asarray(q)
        for _ in range(350):
            target = self.controller.reference.copy()
            delta = q - target[:7]
            peak = float(np.max(np.abs(delta)))
            target[:7] += delta * (min(1., .012 / peak) if peak else 1.)
            self.controller.accept_joint_targets(target)
            self.frame()
            error = np.max(np.abs(self.scene.data.qpos[list(self.arm.qpos)] - q))
            if error < .0008 and np.max(np.abs(self.controller.reference[:7] - q)) < 1e-7:
                return
        raise WitnessFailure("JOINT_TRACKING_TIMEOUT_" + name)

    def point(self, name, position, rotation=DOWN, *, position_tolerance=.0002):
        ik = solve_ik(self.scene, "left", position, rotation, max_iterations=350,
                      position_tolerance=position_tolerance, rotation_tolerance=.002)
        self.geometry.append({"phase": name, "target_m": np.asarray(position).tolist(),
            "ik_success": ik.success, "position_error_m": ik.position_error,
            "rotation_error_rad": ik.rotation_error, "q7": ik.q.tolist()})
        if not ik.success:
            raise WitnessFailure("RUNTIME_IK_FAILED_" + name)
        self.joints(name, ik.q)

    def line(self, name, position, rotation=DOWN, spacing=.010):
        start = tcp_pose(self.scene.data, self.arm)[0].copy()
        count = max(1, int(np.ceil(np.linalg.norm(np.asarray(position) - start) / spacing)))
        for index in range(1, count + 1):
            self.point(name, start + (np.asarray(position) - start) * index / count, rotation)

    def grip(self, value, maximum_delta=.002):
        distance = abs(value - float(self.controller.reference[7]))
        count = max(50, int(np.ceil(distance / maximum_delta)) + 25)
        for _ in range(count):
            target = self.controller.reference.copy()
            target[7] += np.clip(value - target[7], -maximum_delta, maximum_delta)
            self.controller.accept_joint_targets(target)
            self.frame()

    def acquire(self, label):
        self.grip(.025)
        if self.tracker.consecutive_seconds < .05:
            raise WitnessFailure("NO_BILATERAL_50MS_" + label)
        tcp, rotation = tcp_pose(self.scene.data, self.arm)
        self.grasp_frame = rotation.T @ (self.scene.data.xpos[self.scene.objects["apple"]] - tcp)
        self.carrying, self.loss_seconds = True, 0.
        self.checks[label + "_bilateral_grasp_50ms"] = True
        self.mark(label + "_grasp")

    def balance_supported_load(self, label, support):
        weight = float(self.scene.model.body_mass[self.scene.objects["apple"]] *
                       -self.scene.model.opt.gravity[2])
        command = np.asarray(self.geometry[-1]["target_m"]).copy()
        origin_z = float(command[2])
        step, previous_direction, stable_seconds = .0002, 0, 0.
        for index in range(32):
            evidence = self.controller.devices.support_evidence("apple", [support])
            force = sum(max(0., c["normal_force_world_z_n"]) for c in evidence["contacts"])
            okay = evidence["supported"] and .7 * weight <= force <= 1.3 * weight
            self.trace.append({"phase": label + "_load_balance", "iteration": index,
                "tick": self.controller.tick, "support_upward_n": force, "weight_n": weight,
                "command_tcp_m": command.tolist(), "weight_like": bool(okay)})
            if okay:
                stable_seconds += .16
                if stable_seconds >= .32:
                    self.mark(label + "_weight_balanced_before_open")
                    return
            else:
                stable_seconds = 0.
                direction = 1 if force > 1.3 * weight else -1
                if previous_direction and direction != previous_direction:
                    step = max(.0000125, step / 2)
                previous_direction = direction
                command[2] += direction * step
                if abs(command[2] - origin_z) > .003 + 1e-9:
                    raise WitnessFailure("SUPPORTED_UNLOAD_3MM_BOUND_" + label)
                self.point(label + "_load_balance", command, position_tolerance=.000002)
            self.wait(.16)
        raise WitnessFailure("NO_WEIGHT_BALANCED_SUPPORT_" + label)

    def release_on(self, label, support):
        devices = self.controller.devices
        # The small downward search ends on measured support, not an assumed z.
        for _ in range(8):
            if devices.supports("apple", [support]):
                break
            self.line(label + "_seek_support", tcp_pose(self.scene.data, self.arm)[0] - [0, 0, .001], spacing=.001)
        if not devices.supports("apple", [support]):
            raise WitnessFailure("NO_SUPPORT_BEFORE_OPEN_" + label)
        self.wait(1.)
        self.balance_supported_load(label, support)
        quiet_seconds = 0.
        for _ in range(75):
            state = snapshot(self.scene)
            okay = (state["linear_m_s"] < .02 and state["angular_rad_s"] < .05 and
                    devices.supports("apple", [support]))
            quiet_seconds = quiet_seconds + .04 if okay else 0.
            if quiet_seconds >= .2 - 1e-8:
                break
            self.wait(.04)
        if quiet_seconds < .2 - 1e-8:
            raise WitnessFailure("NO_QUIET_SUPPORTED_RELEASE_" + label)
        self.mark(label + "_supported_before_open")
        self.carrying = False
        self.phase = label + "_opening"
        self.grip(0.)
        self.wait(.4)
        self.mark(label + "_opened")
        gap = fingertip_gap(self.scene.model, self.scene.data, self.arm)["inner_gap_m"]
        if devices.touching_pads("apple") or gap < .075:
            raise WitnessFailure("PADS_NOT_SEPARATED_BEFORE_WITHDRAW_" + label)
        tcp = tcp_pose(self.scene.data, self.arm)[0].copy()
        tcp[2] = self.clearance_height(label)
        self.line(label + "_vertical_withdraw", tcp, spacing=.006)
        self.mark(label + "_withdrawn")
        self.stable(label, support)

    def stable(self, label, support):
        self.stability = {"label": label, "support": support, "seconds": 0.,
                          "max_linear": 0., "max_angular": 0.}
        self.phase = label + "_stability"
        for _ in range(300):
            self.frame()
            window = self.stability
            if window["seconds"] >= 2. - 1e-8:
                self.checks[label + "_released_stable_2s"] = True
                self.trace.append({"phase": label + "_stable_window", "seconds": window["seconds"],
                    "sample_rate_hz": 500, "maximum_linear_m_s": window["max_linear"],
                    "maximum_angular_rad_s": window["max_angular"],
                    "support": self.controller.devices.support_evidence("apple", [support])})
                self.stability = None
                self.mark(label + "_stable")
                return
        self.stability = None
        raise WitnessFailure("RELEASE_STABILITY_2S_FAILED_" + label)

    def faucet_cycle(self, *, verify_wash=True):
        rows = json.loads(FAUCET_SOURCE.read_text(encoding="utf-8"))["rows"]
        controller = self.controller
        self.joints("basket_via_home_before_faucet", self.home_q7)
        controller.contact_guard.allow_device_contact("faucet_handle_geom")
        on_seen = False
        for row in rows:
            label = row["stage"]
            if label == "close_gap_28.0mm":
                self.grip(.035)
                self.mark("faucet_grasp")
                if self.handle_seconds < .05:
                    raise WitnessFailure("NO_FAUCET_BILATERAL_GRASP_50MS")
                self.checks["faucet_bilateral_contact_50ms"] = True
            elif label == "release":
                self.grip(0.)
            else:
                self.joints("tap_" + label, np.asarray(row["qpos"])[:7])
            if label == "faucet_45":
                self.mark("faucet_on")
                on_seen = bool(controller.devices.snapshot()["water_on"])
                if not on_seen:
                    raise WitnessFailure("FAUCET_ACTUAL_ON_THRESHOLD_NOT_REACHED")
                self.checks["actual_faucet_on"] = True
                self.wait(3.2)
                if verify_wash:
                    if "apple" not in controller.devices.cleaned:
                        raise WitnessFailure("APPLE_WASH_STATE_NOT_COMPLETE")
                    self.checks["actual_faucet_on_and_wash_state_3s"] = True
                    self.mark("apple_wash_state_complete")
                else:
                    self.mark("faucet_dwell_3s_no_apple_wash_claim")
        self.wait(.4)
        self.mark("faucet_off_released")
        self.checks["actual_faucet_off"] = bool(on_seen and not controller.devices.snapshot()["water_on"] and
            abs(controller.devices.angle("faucet_switch")) < np.deg2rad(5))
        if not self.checks["actual_faucet_off"]:
            raise WitnessFailure("FAUCET_NOT_OFF")

    def perform_apple_cycle(self):
        p = self.scene.data.xpos[self.scene.objects["apple"]].copy()
        self.point("pick_pregrasp", p + [0, 0, .18])
        self.line("pick_down", p + [0, 0, -.005])
        self.acquire("first")
        self.line("pick_lift", p + [0, 0, .18])
        self.phase = "first_hold"
        self.wait(1.)
        self.checks["lift_30mm_hold_1s"] = self.minimum_hold_lift >= .03
        self.mark("first_held_1s")
        self.place("basket", "basket_bottom", np.array([0., 0.]))
        self.faucet_cycle()
        p = self.scene.data.xpos[self.scene.objects["apple"]].copy()
        self.second_origin_z = float(p[2])
        self.point("regrasp_over", p + [0, 0, .35])
        self.line("regrasp_down", p + [0, 0, -.005])
        self.acquire("second")
        self.line("regrasp_lift", p + [0, 0, .35])
        self.phase = "second_hold"
        self.wait(1.)
        self.checks["second_lift_30mm_hold_1s"] = self.minimum_second_hold_lift >= .03
        self.mark("second_held_1s")
        # Center the load on the free tray; eccentric loading tilts its support.
        self.place("tray", "tray_bottom", np.array([0., 0.]))

    def run(self, *, faucet_only=False):
        status, reason = "PHYSICS_FAIL", None
        try:
            self.wait(1.)
            p = self.scene.data.xpos[self.scene.objects["apple"]].copy()
            self.initial_z = p[2]
            self.mark("initial_settled")
            if faucet_only:
                self.faucet_cycle(verify_wash=False)
            else:
                self.perform_apple_cycle()
            self.checks["no_safety_stop"] = not self.controller.stopped
            if all(self.checks.values()):
                status = "FAUCET_ON_OFF_MUJOCO_PASS_ONLY" if faucet_only else "APPLE_FULL_CYCLE_MUJOCO_PASS"
        except (RuntimeError, ValueError) as error:
            reason = str(error)
            self.controller.hold(reason)
            try:
                self.mark("failed")
            except Exception as render_error:
                self.trace.append({"phase": "failure_render_error", "error": str(render_error)})
        finally:
            self.writer.close(reason or status)
            if self.renderer is not None:
                self.renderer.close()
        complete = not self.writer.failed and self.writer.last_tick == self.controller.tick
        if not complete:
            status, reason = "PHYSICS_FAIL", "INCOMPLETE_EVIDENCE"
        return {"status": status, "reason": reason, "checks": self.checks, "trace": self.trace,
            "scope": "FAUCET_ONLY_NO_APPLE_WASH_OR_REGRASP_CLAIM" if faucet_only else "APPLE_FULL_CYCLE",
            "geometry_ik": self.geometry, "physics_seconds": float(self.scene.data.time),
            "physics_samples": self.writer.samples, "evidence_complete": complete,
            "evidence_path": str(self.writer.path), "contact": self.tracker.summary(),
            "maximum_lift_m": self.maximum_lift,
            "minimum_first_hold_lift_m": None if not np.isfinite(self.minimum_hold_lift) else self.minimum_hold_lift,
            "minimum_second_hold_lift_m": None if not np.isfinite(self.minimum_second_hold_lift) else self.minimum_second_hold_lift,
            "maximum_measured_tcp_speed_m_s": self.max_tcp_speed,
            "maximum_measured_joint_speed_rad_s": self.max_joint_speed,
            "device_events": self.controller.devices.events,
            "maximum_faucet_angle_deg": float(np.rad2deg(self.maximum_faucet_angle)),
            "maximum_bilateral_handle_contact_s": self.maximum_handle_seconds,
            "native_internal_joint_couplings": list(self.joint_coupling_metrics.values()),
            "native_internal_joint_couplings_are_not_payload_constraints": True,
            "supported_opening_tendon_delta_m_per_frame": .002,
            "supported_release_load_ratio_range": [.7, 1.3],
            "maximum_load_balance_excursion_m": .003,
            "faucet_grasp_tendon_target_m": .035,
            "finite_limits": {"sim_seconds": 300, "wall_seconds": 900},
            "stop_reason": self.controller.stop_reason,
            "stop_evidence": self.controller.stop_evidence_snapshot,
            "collision_masks_unchanged": all(np.array_equal(a, b) for a, b in zip(self.original_masks,
                (self.scene.model.geom_contype, self.scene.model.geom_conaffinity))),
            "friction_unchanged": np.array_equal(self.original_friction, self.scene.model.geom_friction),
            "contact_dimensions_unchanged": np.array_equal(self.original_condim, self.scene.model.geom_condim),
            "equality_count_unchanged": self.original_eq_count == self.scene.model.neq,
            "object_pose_writes_during_execution": 0, "added_constraints": 0,
            "quest": "HARDWARE_REQUIRED", "general_planner_implemented": False}

    def clearance_height(self, label):
        body = self.scene.model.body("basket").id if label == "basket" else self.scene.objects["wood_tray"]
        return float(self.scene.data.xpos[body, 2] + (.40 if label == "basket" else .23))

    def place(self, label, support, offset_xy):
        m, d = self.scene.model, self.scene.data
        support_id = m.geom(support).id
        _, upper = geometry_bounds(m, d, support_id)
        center = d.geom_xpos[support_id].copy()
        center[:2] += offset_xy
        tcp, _ = tcp_pose(d, self.arm)
        offset = d.xpos[self.scene.objects["apple"]] - tcp
        above = center - offset
        above[2] = self.clearance_height(label)
        if label == "tray":
            corridor = tcp.copy()
            corridor[1:] = above[1:]
            corridor[2] = 1.06
            self.line("tray_front_left_corridor", corridor)
        self.line(label + "_over", above)
        target = center - offset
        target[2] = upper[2] + .035 + .001 - offset[2]
        self.line(label + "_lower", target, spacing=.008)
        self.mark(label + "_lowered")
        self.release_on(label, support)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--mode", choices=("geometry", "run", "diagnosis", "faucet-probe"), default="geometry")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--geometry", type=Path)
    args = parser.parse_args()
    physics_mode = args.mode in ("run", "faucet-probe")
    if sha(PLAN) != PLAN_HASH or sha(AUTH) != AUTH_HASH:
        parser.error("Approved protocol/authorization identity mismatch")
    output = args.output.resolve()
    if output.parent != FULL.resolve() or not output.name.startswith(("apple_", "apple012_", "apple013_")):
        parser.error("Output must be a NEW fullops_20261021/apple_*, apple012_* or apple013_* directory")
    if args.mode == "diagnosis":
        output.mkdir(parents=True, exist_ok=False)
        result = historical_diagnosis()
        write_json(output / "report.json", result)
        print(json.dumps({"status": result["status"], "output": str(output)}), flush=True)
        return 0
    scene = build_scene(layout_id=args.layout, seed=0)
    identity = source_identity(scene)
    if physics_mode:
        if args.geometry is None:
            parser.error("Run requires a same-scene complete --geometry report")
        g = json.loads(args.geometry.read_text(encoding="utf-8"))
        if (not g["full_geometric_chain"] or
                g["identity"]["manifest"]["scene_hash"] != scene.scene_sha256 or
                g["identity"]["manifest"]["control_identity"] != identity["manifest"]["control_identity"] or
                g["identity"].get("geometry_implementation_sha256") != identity["geometry_implementation_sha256"]):
            parser.error("Complete matching-scene/control geometry is required; old PASS not inherited")
        identity["geometry_path"] = str(args.geometry.resolve())
        identity["geometry_sha256"] = sha(args.geometry)
    output.mkdir(parents=True, exist_ok=False)
    if physics_mode:
        # Preserve the actual compiled scene, not just a source hash that may
        # later change while the main worker develops a different candidate.
        mujoco.mj_saveModel(scene.model, str(output / "scene.mjb"))
        (output / "scene.xml").write_text(scene.spec.to_xml(), encoding="utf-8")
        sources = output / "source"
        sources.mkdir()
        names = set(identity["manifest"]["control_identity"]["configuration"]["sources"])
        names.update(("model.py", "layout_v3.py", "layouts.py", "layouts_v3.json", "contact.py", "contact_v4.py",
                      "payload_geometry.py", "observation.py", "gripper.py", "scene_identity.py"))
        for name in sorted(names):
            (sources / name).write_bytes((ROOT / "robot_arm/kitchen" / name).read_bytes())
        (sources / "platform_apple_cycle.py").write_bytes(Path(__file__).read_bytes())
        identity["scene_artifacts"] = {name: sha(output / name) for name in ("scene.mjb", "scene.xml")}
        identity["source_snapshot_sha256"] = {path.name: sha(path) for path in sorted(sources.iterdir())}
    write_json(output / "source_identity.json", identity)
    result = Geometry(scene).run() if args.mode == "geometry" else AppleCycle(scene, output).run(faucet_only=args.mode == "faucet-probe")
    result["identity"] = identity
    write_json(output / "report.json", result)
    print(json.dumps({"status": result["status"], "reason": result["reason"],
                      "output": str(output)}), flush=True)
    return 0 if result["status"].endswith(("PASS_ONLY", "MUJOCO_PASS")) else 1

if __name__ == "__main__":
    raise SystemExit(main())
