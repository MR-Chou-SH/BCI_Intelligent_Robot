"""Finite faucet clearance maintenance; no Goal, shared controller, or model edit."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import time

import mujoco
import numpy as np

from integration.kitchen import platform_apple_cycle as apple
from integration.kitchen import platform_apple_side_maintenance as prior
from integration.kitchen.platform_apple_side_maintenance_diagnostics import sha, write
from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.control import solve_ik, tcp_pose
from robot_arm.kitchen.model import build_scene

MAINTENANCE = Path("D:/VLA_Kitchen_Data/kitchen_maintenance_20261022")
DIAGNOSIS = MAINTENANCE / "apple_side_01_diagnosis/diagnosis.json"
OLD = MAINTENANCE / "apple_side_05_right_front_fullcycle"


class WaterClearance:
    """Exact convex robot geometry against a private, margin-inflated cylinder."""

    def __init__(self, scene):
        self.real = scene.model
        self.model = mujoco.MjModel.from_binary_path(str(OLD / "scene.mjb"))
        self.data = mujoco.MjData(self.model)
        m, d = scene.model, scene.data
        spout = m.geom("faucet_spout").id
        axis = d.geom_xmat[spout].reshape(3, 3)[:, 2]
        endpoints = [d.geom_xpos[spout] + sign * m.geom_size[spout, 1] * axis
                     for sign in (-1, 1)]
        stem = d.geom_xpos[m.geom("faucet_stem").id]
        self.outlet = max(endpoints, key=lambda p: np.linalg.norm(p[:2] - stem[:2]))
        self.bottom = float(d.geom_xpos[m.geom("sink_floor").id, 2]
                            + m.geom_size[m.geom("sink_floor").id, 2])
        self.nozzle_radius = float(m.geom_size[spout, 0])
        self.margin = .015
        self.radius = self.nozzle_radius + self.margin
        self.robot = [g for g in range(m.ngeom) if (m.geom_contype[g] or m.geom_conaffinity[g])
                      and m.body(int(m.geom_bodyid[g])).name.startswith(("left_", "right_"))]
        # Only the diagnostic model's static floor is replaced; never simulated.
        self.probe = self.model.geom("floor").id
        self.model.geom_type[self.probe] = mujoco.mjtGeom.mjGEOM_CYLINDER
        self.model.geom_size[self.probe] = [self.radius, (self.outlet[2] - self.bottom) / 2, 0]
        self.model.geom_pos[self.probe] = [*self.outlet[:2], (self.outlet[2] + self.bottom) / 2]
        self.model.geom_quat[self.probe] = [1, 0, 0, 0]
        self.data = mujoco.MjData(self.model)
        self.motion = CollisionGeometry(scene)

    def distances(self, q):
        self.data.qpos[:] = q
        mujoco.mj_kinematics(self.model, self.data)
        self.data.geom_xpos[self.probe] = self.model.geom_pos[self.probe]
        self.data.geom_xmat[self.probe] = np.eye(3).ravel()
        return np.asarray([mujoco.mj_geomDistance(self.model, self.data, g, self.probe, 2., None)
                           for g in self.robot])

    def sample(self, q):
        values = self.distances(q)
        k = int(np.argmin(values))
        g = self.robot[k]
        return {"distance_m": float(values[k]), "geom_id": g,
                "body": self.real.body(int(self.real.geom_bodyid[g])).name,
                "geom": self.real.geom(g).name, "pass": bool(values[k] >= 0)}

    def certificate(self, q0, q1, budget=1500):
        worst, evaluations, leaves = float("inf"), 0, 0
        for q in (q0, q1):
            result = self.sample(q)
            worst = min(worst, result["distance_m"])
            if not result["pass"]:
                return {"pass": False, "status": "JET_ENDPOINT_INTRUSION", **result}
        pending = [(q0.copy(), q1.copy(), 0)]
        while pending:
            a, b, depth = pending.pop()
            mid = self.motion.midpoint(a, b)
            values = self.distances(mid)
            evaluations += 1
            worst = min(worst, float(values.min()))
            if values.min() < 0:
                return {"pass": False, "status": "JET_INTERMEDIATE_INTRUSION",
                        "evaluations": evaluations, **self.sample(mid)}
            lower = values - self.motion.motion_bound(a, b)[self.robot] / 2
            if lower.min() >= 0:
                leaves += 1
            elif depth >= 14 or evaluations >= budget:
                return {"pass": False, "status": "JET_CLEARANCE_UNRESOLVED",
                        "evaluations": evaluations, "min_sampled_distance_m": worst}
            else:
                pending.extend(((a, mid, depth + 1), (mid, b, depth + 1)))
        return {"pass": True, "status": "JET_CONTINUOUS_BOUND_PASS",
                "min_sampled_distance_m": worst, "evaluations": evaluations, "leaves": leaves}

    def contract(self):
        return {"outlet_center_m": self.outlet.tolist(), "bottom_z_m": self.bottom,
                "direction_world": [0, 0, -1], "nozzle_radius_m": self.nozzle_radius,
                "extra_radial_margin_m": self.margin, "acceptance_radius_m": self.radius,
                "robot_collision_geoms": len(self.robot), "all_robot_bodies_not_tcp_only": True,
                "approximation": "Gravity-down potential water column at modeled terminal spout; no explicit nozzle or fluid physics exists",
                "diagnostic_only_floor_replacement": True, "simulation_model_unchanged": True}


def rz(angle):
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.]])


def geometry(scene, water, roll_degrees, radius, heading, yaw_degrees, follow_handle):
    started = time.monotonic()
    diagnosis = json.loads(DIAGNOSIS.read_text())
    q = np.asarray(diagnosis["snapshots"]["basket_stable"]["qpos"]).copy()
    arm, m = scene.arms["left"], scene.model
    scratch = mujoco.MjData(m)
    checker = CollisionGeometry(scene)
    pairs = checker.pairs(("faucet_handle",))
    handle = m.geom("faucet_handle_geom").id
    intentional = [(p, handle) for p in arm.pad_geoms]
    hinge = int(m.jnt_qposadr[m.joint("faucet_switch").id])
    origin = scene.data.xpos[m.body("faucet_handle").id].copy()
    if heading == "oblique":
        base_rotation = rz(np.deg2rad(yaw_degrees)) @ np.array([[1., 0, 0], [0, 0, 1.], [0, -1., 0]]) @ rz(np.deg2rad(roll_degrees))
    elif heading == "right":
        base_rotation = np.array([[0., 0, -1.], [0, 1., 0], [1., 0, 0]]) @ rz(np.deg2rad(roll_degrees - 90))
    else:
        base_rotation = np.array([[1., 0, 0], [0, 0, 1.], [0, -1., 0]]) @ rz(np.deg2rad(roll_degrees))
    rows = []
    failure = None

    def append(label, target, kind="joints"):
        nonlocal q
        if time.monotonic() - started > 180:
            raise ValueError("LOCAL_180S_BOUND")
        collision = checker.certificate(q, target, pairs, intentional_pairs=intentional,
                                        max_evaluations=2500)
        jet = water.certificate(q, target) if collision["pass"] else {"pass": False, "status": "NOT_CHECKED_COLLISION_FAIL"}
        row = {"stage": label, "kind": kind, "q7": target[list(arm.qpos)].tolist(),
               "qpos": target.tolist(), "continuous": collision, "water_clearance": jet}
        rows.append(row)
        if not collision["pass"]:
            raise ValueError("COLLISION_" + label)
        if not jet["pass"]:
            raise ValueError("WATER_" + label)
        q = target.copy()

    def point(label, pos, rotation, angle=0):
        scratch.qpos[:] = q
        mujoco.mj_kinematics(m, scratch)
        result = solve_ik(replace(scene, data=scratch), "left", pos, rotation,
                          max_iterations=500, position_tolerance=.0002, rotation_tolerance=.002)
        if not result.success:
            references = json.loads(apple.FAUCET_SOURCE.read_text())["rows"]
            for seed in (scene.data.qpos[list(arm.qpos)], np.asarray(references[0]["qpos"])[list(arm.qpos)]):
                scratch.qpos[:] = q
                scratch.qpos[list(arm.qpos)] = seed
                result = solve_ik(replace(scene, data=scratch), "left", pos, rotation,
                                  max_iterations=500, position_tolerance=.0002, rotation_tolerance=.002)
                if result.success:
                    break
        if not result.success:
            rows.append({"stage": label, "kind": "ik_failure", "target_m": np.asarray(pos).tolist(),
                         "position_error_m": result.position_error, "rotation_error_rad": result.rotation_error})
            raise ValueError("IK_" + label)
        target = q.copy()
        target[list(arm.qpos)] = result.q
        target[hinge] = np.deg2rad(angle)
        append(label, target)

    try:
        point("side_gate_entry", [-.72, -.04, 1.06], base_rotation)
        pre = origin + [.070, -radius, 0]
        point("side_pregrasp", pre, base_rotation)
        point("tap_approach", origin + [0, -radius, 0], base_rotation)
        target = q.copy()
        closed_reference = next(r for r in json.loads(apple.FAUCET_SOURCE.read_text())["rows"]
                                if r["stage"] == "close_gap_28.0mm")
        target[list(arm.fingers)] = np.asarray(closed_reference["qpos"])[list(arm.fingers)]
        append("tap_close_gap_28.0mm", target, "grip_close")
        for angle in range(0, 46, 5):
            point("tap_faucet_" + str(angle), origin + rz(np.deg2rad(angle)) @ [0, -radius, 0],
                  (rz(np.deg2rad(angle)) @ base_rotation) if follow_handle else base_rotation, angle)
        for angle in range(40, -1, -5):
            point("tap_return_faucet_" + str(angle), origin + rz(np.deg2rad(angle)) @ [0, -radius, 0],
                  (rz(np.deg2rad(angle)) @ base_rotation) if follow_handle else base_rotation, angle)
        target = q.copy()
        target[list(arm.fingers)] = 0.
        append("tap_release", target, "grip_open")
        point("side_pregrasp_exit", pre, base_rotation)
        point("side_gate_exit", [-.72, -.04, 1.06], base_rotation)
        target = q.copy()
        target[list(arm.qpos)] = np.asarray(diagnosis["snapshots"]["basket_stable"]["qpos"])[list(arm.qpos)]
        append("side_basket_return", target)
    except (ValueError, RuntimeError) as error:
        failure = str(error)
    return {"status": "SIDE_ALL_TIME_CLEAR_GEOMETRY_PASS_ONLY" if failure is None else "SIDE_GEOMETRY_FAIL",
            "local_geometry_pass": failure is None, "reason": failure,
            "candidate": f"{heading}_roll{roll_degrees}_radius{radius}", "roll_degrees": roll_degrees,
            "approach_heading": heading,
            "yaw_degrees": yaw_degrees, "follow_handle_orientation": follow_handle,
            "handle_grasp_radius_m": radius, "rows": rows, "water_contract": water.contract(),
            "elapsed_s": time.monotonic() - started, "physics_steps": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("baseline", "geometry", "local", "full"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--roll", type=float, default=90.)
    parser.add_argument("--radius", type=float, default=.065)
    parser.add_argument("--heading", choices=("front", "right", "oblique"), default="oblique")
    parser.add_argument("--yaw", type=float, default=30.)
    parser.add_argument("--follow-handle", action="store_true")
    parser.add_argument("--geometry", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != MAINTENANCE.resolve() or output.exists() or not output.name.startswith("apple_clear_"):
        parser.error("A fresh direct maintenance child apple_clear_* is required")
    scene = build_scene(layout_id=prior.LAYOUT, seed=0)
    identity = prior.identity(scene)
    identity.update(maintenance_entry=str(Path(__file__).resolve()), maintenance_entry_sha256=sha(__file__),
                    authorization="2026-10-22 explicit 90-minute bounded maintenance; Goal stays PAUSED")
    water = WaterClearance(scene)
    output.mkdir(parents=True, exist_ok=False)
    if args.mode == "baseline":
        old_geometry = json.loads((MAINTENANCE / "apple_side_08_readonly_verification/jet_geometry.json").read_text())
        worst = min(old_geometry["per_geom"], key=lambda r: r["minimum_signed_distance_m"])
        q = np.asarray(worst["worst_sample"]["qpos"])
        result = {"status": "BASELINE_INTRUSION_REPRODUCED" if not water.sample(q)["pass"] else "BASELINE_CLEAR",
                  "actual_occupancy": water.sample(q), "water_contract": water.contract(), "physics_steps": 0,
                  "source_tick": worst["worst_sample"]["tick"], "source_phase": worst["worst_sample"]["phase"]}
    elif args.mode == "geometry":
        result = geometry(scene, water, args.roll, args.radius, args.heading, args.yaw, args.follow_handle)
    else:
        if args.geometry is None:
            parser.error("Local/full dynamics require a passed same-source geometry")
        route = json.loads(args.geometry.read_text())
        if (not route["local_geometry_pass"] or route["identity"]["maintenance_entry_sha256"] != sha(__file__)
                or any(route["identity"]["manifest"][k] != identity["manifest"][k]
                       for k in ("scene_hash", "compiled_model_hash", "control_identity"))):
            parser.error("Passed geometry identity mismatch")
        prior.archive(scene, output, identity)
        (output / "source" / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
        identity["source_snapshot_sha256"][Path(__file__).name] = sha(__file__)
        cycle = prior.SideCycle(scene, output, route)
        cycle.water_minimum = 2.
        cycle.water_on_samples = 0
        cycle.water_failures = []
        cycle.water_active = False
        base_sample = cycle.sample
        base_faucet = cycle.faucet_cycle

        def faucet_cycle(**kwargs):
            cycle.water_active = True
            try:
                return base_faucet(**kwargs)
            finally:
                cycle.water_active = False

        cycle.faucet_cycle = faucet_cycle

        def sample(controller):
            base_sample(controller)
            if cycle.water_active or controller.devices.snapshot()["water_on"]:
                proof = water.sample(scene.data.qpos)
                cycle.water_minimum = min(cycle.water_minimum, proof["distance_m"])
                on = bool(controller.devices.snapshot()["water_on"])
                cycle.water_on_samples += int(on)
                if not proof["pass"]:
                    cycle.water_failures.append({"tick": controller.tick, "phase": cycle.phase, "water_on": on, **proof})
                    raise apple.WitnessFailure("REAL_OCCUPANCY_WATER_MARGIN_INTRUSION")

        cycle.sample = sample
        if args.mode == "full":
            result = cycle.run()
        else:
            reason = None
            try:
                cycle.wait(.4)
                start = route["rows"][-1]["q7"]
                cycle.joints("local_basket_start", start)
                cycle.faucet_cycle(verify_wash=False)
            except (ValueError, RuntimeError) as error:
                reason = str(error)
                cycle.controller.hold(reason)
            cycle.writer.close(reason)
            if cycle.renderer is not None:
                cycle.renderer.close()
            complete = not cycle.writer.failed and cycle.writer.last_tick == cycle.controller.tick
            passed = reason is None and complete and not cycle.controller.stopped and cycle.water_on_samples > 0 and all(cycle.checks.values())
            result = {"status": "LOCAL_FAUCET_CONTACT_CLEARANCE_PASS" if passed else "LOCAL_FAUCET_CONTACT_FAIL",
                      "reason": reason, "checks": cycle.checks, "ticks": cycle.controller.tick,
                      "maximum_faucet_angle_rad": cycle.maximum_faucet_angle, "evidence_path": str(cycle.writer.path),
                      "physics_samples": cycle.writer.samples, "evidence_complete": complete,
                      "trace": cycle.trace, "stop_reason": cycle.controller.stop_reason,
                      "stop_evidence": cycle.controller.stop_evidence_snapshot}
        result.update(water_contract=water.contract(), minimum_actual_water_clearance_m=cycle.water_minimum,
                      water_on_native_samples=cycle.water_on_samples, water_intrusions=cycle.water_failures,
                      geometry=str(args.geometry.resolve()), geometry_sha256=sha(args.geometry))
    result["identity"] = identity
    write(output / "report.json", result)
    print(json.dumps({"status": result["status"], "reason": result.get("reason"), "output": str(output)}), flush=True)
    return 0 if result["status"].endswith(("PASS", "PASS_ONLY", "REPRODUCED")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
