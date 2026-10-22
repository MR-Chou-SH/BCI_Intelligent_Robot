"""Finite side-approach maintenance, explicitly independent of paused Goals.

No historical main/authorization guard is bypassed or modified. This entry reuses
the original AppleCycle strict physical predicates under the current user's
independent maintenance authorization. It never exports presentation assets.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

import mujoco
import numpy as np

from integration.kitchen import platform_apple_cycle as original
from integration.kitchen.platform_apple_side_maintenance_diagnostics import diagnose, sha, write
from robot_arm.kitchen.collision import CollisionGeometry
from robot_arm.kitchen.control import solve_ik, tcp_pose
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.registry import SceneRegistry, geometry_bounds

ROOT = Path(__file__).resolve().parents[2]
SOURCE = original.FULL / "apple013_center_fullcycle_02_disk_retry"
OUTPUT_ROOT = Path("D:/VLA_Kitchen_Data/kitchen_maintenance_20261022")
LAYOUT = "kitchen_v3_norice_candidate_013"
COMPILED = "1b6b0706d6e622937aa4512274b80f51fef261877e06492813d199fdf61f484f"
CANDIDATES = {
    "right_front": {"gate": [-.75, -.02, .98], "front": [-.80, .1144, .90]},
    "right_high": {"gate": [-.72, -.02, 1.14], "front": [-.78, .1144, 1.10]},
}


def identity(scene):
    manifest = SceneRegistry(scene).manifest()
    if manifest["compiled_model_hash"] != COMPILED or scene.layout_id != LAYOUT:
        raise ValueError("ORIGINAL_013_COMPILED_MODEL_REQUIRED")
    return {"authorization": "2026-10-22 explicit independent apple-side maintenance request",
        "goal_manager_attestation": None, "long_run_goal": "PAUSED_NOT_OPERATED",
        "research_stage_started": False, "worktree": str(ROOT), "manifest": manifest,
        "entry_path": str(Path(__file__).resolve()), "entry_sha256": sha(__file__),
        "diagnostics_sha256": sha(Path(__file__).with_name("platform_apple_side_maintenance_diagnostics.py")),
        "original_entry_path": str(Path(original.__file__).resolve()),
        "original_entry_sha256": sha(original.__file__),
        "original_report_path": str(SOURCE / "report.json"),
        "original_report_sha256": sha(SOURCE / "report.json"),
        "faucet_reference_sha256": sha(original.FAUCET_SOURCE),
        "python_executable": sys.executable, "arguments": sys.argv[1:],
        "approximation": "Wash status timer only, no fluid dynamics or real hardware claim",
        "review_scope": "Local finite trajectories only; no shared interface, research protocol or frozen baseline changed"}


class JetRegion:
    """Conservative AABB/cylinder overlap, a diagnostic and not fluid physics."""
    def __init__(self, scene):
        self.scene, self.m = scene, scene.model
        d, m = scene.data, scene.model
        spout = m.geom("faucet_spout").id
        axis = d.geom_xmat[spout].reshape(3, 3)[:, 2]
        ends = [d.geom_xpos[spout] + sign * m.geom_size[spout, 1] * axis for sign in (-1, 1)]
        self.top = min(ends, key=lambda p: p[1])
        self.radius = .025
        self.bottom = geometry_bounds(m, d, m.geom("sink_floor").id)[1][2]
        self.robot = [g for g in range(m.ngeom) if (m.geom_contype[g] or m.geom_conaffinity[g])
            and m.body(int(m.geom_bodyid[g])).name.startswith(("left_", "right_"))]
        self.samples, self.overlap, self.worst, self.names = 0, 0, float("inf"), set()

    def sample(self, data):
        names, minimum = [], float("inf")
        for geom in self.robot:
            lo, hi = geometry_bounds(self.m, data, geom)
            if hi[2] < self.bottom or lo[2] > self.top[2]:
                continue
            delta = np.maximum(np.maximum(lo[:2] - self.top[:2], self.top[:2] - hi[:2]), 0.)
            distance = float(np.linalg.norm(delta) - self.radius)
            minimum = min(minimum, distance)
            if distance <= 0:
                names.append(self.m.geom(geom).name)
        self.samples += 1
        self.overlap += bool(names)
        self.worst = min(self.worst, minimum)
        self.names.update(names)
        return names

    def summary(self):
        return {"outlet_center_m": self.top.tolist(), "radius_m": self.radius,
            "bottom_z_m": float(self.bottom), "samples": self.samples,
            "potential_overlap_samples": self.overlap,
            "minimum_sampled_radial_aabb_clearance_m": None if not np.isfinite(self.worst) else self.worst,
            "potentially_overlapping_geoms": sorted(self.names),
            "semantics": "Conservative active-robot world AABB versus vertical cylinder; overlap is possible, not proven water contact; samples are not a continuous clearance proof"}


class SideGeometry:
    def __init__(self, scene, diagnosis, candidate):
        self.scene, self.m = scene, scene.model
        self.arm = scene.arms["left"]
        self.q = np.asarray(diagnosis["snapshots"]["basket_stable"]["qpos"]).copy()
        self.start = self.q.copy()
        self.d = mujoco.MjData(self.m)
        self.checker = CollisionGeometry(scene)
        self.pairs = self.checker.pairs(("faucet_handle",))
        self.intentional = [(p, self.m.geom("faucet_handle_geom").id) for p in self.arm.pad_geoms]
        self.hinge = int(self.m.jnt_qposadr[self.m.joint("faucet_switch").id])
        self.rows, self.jet = [], JetRegion(scene)
        self.candidate, self.started = candidate, time.monotonic()

    def fk(self, q):
        self.d.qpos[:] = q
        mujoco.mj_kinematics(self.m, self.d)
        return tcp_pose(self.d, self.arm)

    def append(self, label, target, kind="joints"):
        if time.monotonic() - self.started > 180:
            raise ValueError("FINITE_GEOMETRY_180S_BOUND")
        proof = self.checker.certificate(self.q, target, self.pairs,
            intentional_pairs=self.intentional, max_evaluations=3500)
        p0 = self.fk(self.q)[0].copy()
        p1 = self.fk(target)[0].copy()
        row = {"stage": label, "kind": kind, "q7": target[list(self.arm.qpos)].tolist(),
            "qpos": target.tolist(), "continuous": proof, "tcp_m": p1.tolist(),
            "endpoint_tcp_distance_m": float(np.linalg.norm(p1 - p0))}
        self.rows.append(row)
        if not proof["pass"]:
            raise ValueError("LOCAL_GEOMETRY_FAIL_" + label)
        count = max(1, int(np.ceil(np.max(np.abs(target[:7] - self.q[:7])) / .025)))
        for alpha in np.linspace(0, 1, count + 1):
            self.fk((1 - alpha) * self.q + alpha * target)
            self.jet.sample(self.d)
        self.q = target.copy()

    def point(self, label, position, rotation):
        self.fk(self.q)
        result = solve_ik(replace(self.scene, data=self.d), "left", position, rotation,
            max_iterations=350, position_tolerance=.0002, rotation_tolerance=.002)
        if not result.success:
            self.rows.append({"stage": label, "kind": "ik_failure", "target_m": position,
                "position_error_m": result.position_error, "rotation_error_rad": result.rotation_error})
            raise ValueError("LOCAL_IK_FAIL_" + label)
        target = self.q.copy()
        target[list(self.arm.qpos)] = result.q
        self.append(label, target)

    def run(self):
        reason = None
        config = CANDIDATES[self.candidate]
        reference = json.loads(original.FAUCET_SOURCE.read_text())["rows"]
        initial = self.q.copy()
        initial[list(self.arm.qpos)] = reference[0]["qpos"][:7]
        rotation = self.fk(initial)[1].copy()
        try:
            self.point("side_gate_entry", config["gate"], rotation)
            self.point("side_front_entry", config["front"], rotation)
            for old in reference:
                label = old["stage"]
                if label == "home":
                    continue
                target = self.q.copy()
                target[list(self.arm.qpos)] = old["qpos"][:7]
                target[list(self.arm.fingers)] = old["qpos"][7:9]
                if label.startswith(("faucet_", "return_faucet_")):
                    target[self.hinge] = np.deg2rad(int(label.split("_")[-1]))
                kind = "grip_close" if label == "close_gap_28.0mm" else "grip_open" if label == "release" else "joints"
                self.append("tap_" + label, target, kind)
            self.point("side_front_exit", config["front"], rotation)
            self.point("side_gate_exit", config["gate"], rotation)
            self.append("side_basket_return", self.start)
        except (ValueError, RuntimeError) as error:
            reason = str(error)
        return {"status": "LOCAL_SIDE_GEOMETRY_PASS_ONLY" if reason is None else "LOCAL_SIDE_GEOMETRY_FAIL",
            "reason": reason, "candidate": self.candidate, "local_geometry_pass": reason is None,
            "scope": "Basket-stable to faucet contact cycle and return only; no carried payload proof or dynamics claim",
            "rows": self.rows, "jet_region": self.jet.summary(),
            "physics_steps": 0, "elapsed_s": time.monotonic() - self.started}


class SideCycle(original.AppleCycle):
    def __init__(self, scene, output, geometry):
        super().__init__(scene, output)
        self.route = geometry
        self.jet = JetRegion(scene)
        self.phase_segments = []

    def sample(self, controller):
        super().sample(controller)
        if not self.phase_segments or self.phase_segments[-1]["phase"] != self.phase:
            self.phase_segments.append({"phase": self.phase, "first_tick": controller.tick,
                "last_tick": controller.tick})
        self.phase_segments[-1]["last_tick"] = controller.tick
        if controller.tick % 20 == 0 and (self.phase.startswith(("side_", "tap_", "faucet_"))):
            self.jet.sample(self.scene.data)

    def faucet_cycle(self, *, verify_wash=True):
        self.controller.contact_guard.allow_device_contact("faucet_handle_geom")
        on_seen = False
        for row in self.route["rows"]:
            label = row["stage"]
            if row["kind"] == "grip_close":
                self.grip(.035)
                self.mark("faucet_grasp")
                if self.handle_seconds < .05:
                    raise original.WitnessFailure("NO_FAUCET_BILATERAL_GRASP_50MS")
                self.checks["faucet_bilateral_contact_50ms"] = True
            elif row["kind"] == "grip_open":
                self.grip(0.)
            else:
                self.joints(label, row["q7"])
            if label == "tap_faucet_45":
                self.mark("faucet_on")
                on_seen = bool(self.controller.devices.snapshot()["water_on"])
                if not on_seen:
                    raise original.WitnessFailure("FAUCET_ACTUAL_ON_THRESHOLD_NOT_REACHED")
                self.checks["actual_faucet_on"] = True
                self.wait(3.2)
                if verify_wash:
                    if "apple" not in self.controller.devices.cleaned:
                        raise original.WitnessFailure("APPLE_WASH_STATE_NOT_COMPLETE")
                    self.checks["actual_faucet_on_and_wash_state_3s"] = True
                    self.mark("apple_wash_state_complete")
        self.wait(.4)
        self.mark("faucet_off_released")
        self.checks["actual_faucet_off"] = bool(on_seen and not self.controller.devices.snapshot()["water_on"]
            and abs(self.controller.devices.angle("faucet_switch")) < np.deg2rad(5))
        if not self.checks["actual_faucet_off"]:
            raise original.WitnessFailure("FAUCET_NOT_OFF")


def archive(scene, output, source_identity):
    mujoco.mj_saveModel(scene.model, str(output / "scene.mjb"))
    with (output / "scene.xml").open("x", encoding="utf-8") as stream:
        stream.write(scene.spec.to_xml())
    folder = output / "source"
    folder.mkdir()
    paths = [ROOT / "robot_arm/kitchen" / n for n in
             source_identity["manifest"]["control_identity"]["configuration"]["sources"]]
    paths += [Path(original.__file__), Path(__file__), Path(__file__).with_name("platform_apple_side_maintenance_diagnostics.py")]
    paths += [ROOT / "robot_arm/kitchen" / n for n in ("model.py", "layout_v3.py", "layouts.py", "layouts_v3.json",
        "contact.py", "contact_v4.py", "payload_geometry.py", "observation.py", "gripper.py", "scene_identity.py")]
    for path in paths:
        with (folder / path.name).open("xb") as stream:
            stream.write(path.read_bytes())
    source_identity["source_snapshot_sha256"] = {p.name: sha(p) for p in sorted(folder.iterdir())}
    source_identity["scene_artifacts"] = {n: sha(output / n) for n in ("scene.mjb", "scene.xml")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("diagnosis", "geometry", "run"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnosis", type=Path)
    parser.add_argument("--geometry", type=Path)
    parser.add_argument("--candidate", choices=tuple(CANDIDATES))
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != OUTPUT_ROOT.resolve() or not output.name.startswith("apple_side_"):
        parser.error("New direct child of kitchen_maintenance_20261022/apple_side_* required")
    if output.exists():
        parser.error("Never overwrite maintenance evidence")
    if args.mode == "diagnosis":
        output.mkdir(parents=True, exist_ok=False)
        diagnose(SOURCE, output)
        return 0
    scene = build_scene(layout_id=LAYOUT, seed=0)
    source_identity = identity(scene)
    if args.mode == "geometry":
        if args.diagnosis is None or args.candidate is None:
            parser.error("Geometry requires --diagnosis and one finite --candidate")
        diagnosis = json.loads(args.diagnosis.read_text())
        if diagnosis["hash_chain"] != "PASS" or diagnosis["source_report_sha256"] != sha(SOURCE / "report.json"):
            parser.error("Original sealed diagnosis required")
        output.mkdir(parents=True, exist_ok=False)
        result = SideGeometry(scene, diagnosis, args.candidate).run()
        source_identity["diagnosis_sha256"] = sha(args.diagnosis)
    else:
        if args.geometry is None:
            parser.error("Run requires a new same-source local --geometry")
        geometry = json.loads(args.geometry.read_text())
        old_id = geometry["identity"]
        if not geometry["local_geometry_pass"] or any(old_id[k] != source_identity[k] for k in
            ("entry_sha256", "diagnostics_sha256", "original_entry_sha256", "original_report_sha256", "faucet_reference_sha256")):
            parser.error("Complete unchanged-source maintenance geometry required")
        for k in ("scene_hash", "compiled_model_hash", "control_identity"):
            if old_id["manifest"][k] != source_identity["manifest"][k]:
                parser.error("Geometry model/control identity mismatch")
        output.mkdir(parents=True, exist_ok=False)
        source_identity["geometry_path"] = str(args.geometry.resolve())
        source_identity["geometry_sha256"] = sha(args.geometry)
        archive(scene, output, source_identity)
        write(output / "source_identity.json", source_identity)
        cycle = SideCycle(scene, output, geometry)
        result = cycle.run()
        result["candidate"] = geometry["candidate"]
        result["jet_region_25hz_side_phases"] = cycle.jet.summary()
        write(output / "phase_intervals.json", cycle.phase_segments)
    result["identity"] = source_identity
    write(output / "report.json", result)
    print(json.dumps({"status": result["status"], "reason": result["reason"], "output": str(output)}), flush=True)
    return 0 if result.get("local_geometry_pass") or result["status"] == "APPLE_FULL_CYCLE_MUJOCO_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
