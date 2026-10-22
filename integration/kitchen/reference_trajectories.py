"""Fixed-scene reference assets, not a planner or a new full-task PASS.

inspect performs no simulation. apple executes recorded accepted targets through
the unchanged internal controller. burger reissues sanitized successful public
RPCs with fresh ownership/tokens. Both default to a short prefix and fail closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "reference/kitchen_v3_trajectories"
LAYOUT = "kitchen_v3_norice_candidate_013"
SCENE = "f52f3483a7d4863ead908a98aa2aae726f13b5f67e6468a1ed5323c5f3771e27"
CONTROL = "b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2"
APPLE_SHA = "4e7ccecda30014113c59df98b2bdab70507345c0f2e5dd79ed6ae4513dfe228c"


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


def extract(args):
    """Explicit external inputs only; never resolves original-machine D paths."""
    import numpy as np
    from robot_arm.kitchen.model import build_scene
    from robot_arm.kitchen.registry import digest

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    scene = build_scene(layout_id=LAYOUT, seed=0)
    report = load(args.apple_report)
    audit, water = load(args.apple_audit), load(args.water_audit)
    if (report["status"] != "APPLE_FULL_CYCLE_MUJOCO_PASS" or
            not all(report["checks"].values()) or
            audit["source_record_sha256"] != APPLE_SHA or
            water["status"] != "APPLE_FULL_CYCLE_WATER_CLEARANCE_AUDITED_PASS"):
        raise ValueError("SEALED_APPLE_AND_CLEARANCE_PASS_REQUIRED")
    previous, raw, count, first, metadata = "0" * 64, hashlib.sha256(), 0, None, None
    frames, ending = [], None
    with args.apple_record.open("rb") as stream:
        for line in stream:
            raw.update(line)
            envelope = json.loads(line)
            claimed = envelope.pop("sha256")
            if envelope["previous_sha256"] != previous or digest(envelope) != claimed:
                raise ValueError("SOURCE_HASH_CHAIN_MISMATCH")
            previous = claimed
            row = envelope["record"]
            if row["type"] == "metadata":
                metadata = row
            elif row["type"] == "end":
                ending = row
            elif row["type"] == "physics":
                count += 1
                if row["sim_tick"] != count or row["controller_stopped"]:
                    raise ValueError("SOURCE_GAP_OR_STOP")
                if (count - 1) % 20 == 0:
                    first = row
                if row["accepted_target16"] != first["accepted_target16"]:
                    raise ValueError("TARGET_CHANGED_INSIDE_CONTROL_FRAME")
                if count % 20 == 0:
                    q = np.asarray(row["qpos"])
                    measured = []
                    for side in ("left", "right"):
                        arm = scene.arms[side]
                        measured.extend(q[list(arm.qpos)].tolist())
                        measured.append(float(np.mean(q[list(arm.fingers)])))
                    frames.append(dict(start_tick=count-20, end_tick=count,
                        target16=first["accepted_target16"], expected_state16=measured,
                        device_contact_geoms=[scene.model.geom(g).name for g in
                            first.get("allowed_device_contact_geom_ids", [])]))
    if (raw.hexdigest() != APPLE_SHA or count != 125880 or len(frames) != 6294 or
            not ending or ending["sampling_failed"] or ending["last_recorded_tick"] != count):
        raise ValueError("SEALED_APPLE_RECORD_MISMATCH")
    manifest = metadata["manifest"]
    if manifest["scene_hash"] != SCENE or manifest["control_identity"]["control_hash"] != CONTROL:
        raise ValueError("APPLE_IDENTITY_MISMATCH")
    apple_controls = output / "apple_controls_25hz.jsonl"
    with apple_controls.open("x", encoding="utf-8") as stream:
        for frame in frames:
            stream.write(json.dumps(frame, allow_nan=False, separators=(",", ":")) + "\n")
    marks = [{k: row[k] for k in ("phase", "tick", "time_s", "tcp_m") if k in row}
             for row in report["trace"] if "tick" in row]
    water_fields = ("water_contract", "checked_native_samples", "minimum_native_clearance_m",
                    "minimum_water_on_clearance_m", "native_intrusion_samples", "unresolved_native_intervals")
    save(output / "apple_metadata.json", dict(schema=1, layout=LAYOUT, seed=0,
        initialization_profile="fixed", scene_hash=SCENE, control_hash=CONTROL,
        compiled_model_hash=manifest["compiled_model_hash"], physics_hz=500, control_hz=25,
        frame_count=len(frames), physics_ticks=count, duration_s=count*.002,
        target_derivation="accepted_target16 from FIRST post-step in each original 20-tick frame; never measured qpos or previous requested_reference16",
        source_record=dict(filename=args.apple_record.name, bytes=args.apple_record.stat().st_size,
            sha256=APPLE_SHA, final_chain_sha256=previous),
        source_report_sha256=sha(args.apple_report), source_audit_sha256=sha(args.apple_audit),
        water_audit_sha256=sha(args.water_audit), controls_sha256=sha(apple_controls),
        historical_status=report["status"], historical_checks=report["checks"],
        final_scope="APPLE_RELEASED_STABLE_ON_TRAY", public_api_full_cycle="NOT_VERIFIED",
        extracted_full_reexecution="NOT_TESTED", landmarks=marks,
        water_clearance={k: water[k] for k in water_fields},
        reusable_scope="one exact fixed scene; not certified for random reset scenes"))

    run = args.burger_run
    plan, result, summary = load(run/"source_plan.json"), load(run/"report.json"), load(run/"audit_summary.json")
    if result["status"] != "PUBLIC_FULL_MICROWAVE_CYCLE_HELD_OUTSIDE_PHYSICS_PASS":
        raise ValueError("PUBLIC_BURGER_HISTORICAL_PASS_REQUIRED")
    calls = []
    with (run/"rpc.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if not row.get("mutating"):
                continue
            if not row["response"].get("ok"):
                raise ValueError("FAILED_RPC_CANNOT_BE_PRESENTED_AS_SUCCESSFUL_REFERENCE")
            calls.append(dict(method=row["method"], args=row["args"],
                start_tick=row["token_before"]["sim_tick"], end_tick=row["response"]["token"]["sim_tick"]))
    burger_calls = output / "burger_public_calls.jsonl"
    with burger_calls.open("x", encoding="utf-8") as stream:
        for call in calls:
            stream.write(json.dumps(call, allow_nan=False, separators=(",", ":")) + "\n")
    public_fields = ("status", "same_public_run_full_raw_audit", "physical_checks", "physical_windows",
                     "source_frames", "source_ticks", "extra_actual_frames", "extra_actual_ticks", "actual_ticks",
                     "actual_simulated_seconds", "settling_by_anchor", "control_hash", "source_plan_hash")
    raw_manifest = run / "raw_records_manifest.json"
    raw_inventory = load(raw_manifest)
    save(output / "burger_metadata.json", dict(schema=1, layout=LAYOUT, seed=0,
        initialization_profile="fixed", scene_hash=SCENE, control_hash=CONTROL,
        initialization_hash=summary["reset_episode"]["initialization_hash"],
        source_control_hash=plan["source_control_hash"], source_record_sha256=
            "f4b117e30e3f0464a9be84d133458867f2a6c6b8945a99fd3500c7eec051a94b",
        source_record_ticks=64160, source_duration_s=128.32, public_actual_ticks=64360,
        public_duration_s=128.72, source_report_sha256=sha(run/"report.json"),
        rpc_sha256=sha(run/"rpc.jsonl"), public_calls_sha256=sha(burger_calls),
        raw_records_manifest_sha256=sha(raw_manifest), raw_inventory_type=type(raw_inventory).__name__,
        public_call_count=len(calls), source_plan_sha256=sha(run/"source_plan.json"),
        historical_audit={k: summary[k] for k in public_fields},
        final_scope="HELD_OUTSIDE_NOT_TABLE_RELEASE_OR_TRAY_DELIVERY",
        extracted_full_reexecution="NOT_TESTED", control_hz=25, physics_hz=500,
        landmarks=plan["marks"], phases=plan["phases"], execution_clock=result["execution_clock"],
        reusable_scope="actual successful RPC argument sequence; fresh tokens/owner required; fixed scene only"))
    files = {p.name: dict(bytes=p.stat().st_size, sha256=sha(p)) for p in output.iterdir() if p.is_file()}
    save(output/"manifest.json", dict(schema="kitchen_fixed_reference_v1", files=files,
         physical_evidence_is_external=True, extraction_tool_sha256=sha(__file__)))
    print(json.dumps(dict(status="SEALED_REFERENCE_EXTRACTION_PASS", files=files), indent=2))


def inspect(assets):
    import math
    manifest = load(assets/"manifest.json")
    for name, expected in manifest["files"].items():
        if Path(name).name != name or sha(assets/name) != expected["sha256"]:
            raise ValueError("REFERENCE_FILE_HASH_MISMATCH: " + name)
    with (assets/"apple_controls_25hz.jsonl").open(encoding="utf-8") as stream:
        frames = [json.loads(line) for line in stream]
    for i, row in enumerate(frames):
        if row["start_tick"] != i*20 or row["end_tick"] != (i+1)*20:
            raise ValueError("REFERENCE_CONTROL_CLOCK_GAP")
        if len(row["target16"]) != 16 or not all(map(math.isfinite, row["target16"])):
            raise ValueError("REFERENCE_TARGET_INVALID")
    with (assets/"burger_public_calls.jsonl").open(encoding="utf-8") as stream:
        calls = [json.loads(line) for line in stream]
    ticks, targets = 0, 0
    for row in calls:
        if row["start_tick"] != ticks or row["method"] not in {"reset", "execute_trajectory", "step", "end_contact", "hold"}:
            raise ValueError("PUBLIC_REFERENCE_CLOCK_OR_METHOD_MISMATCH")
        if row["method"] == "execute_trajectory":
            for q in row["args"]["waypoints"]:
                if len(q) != 16 or not all(map(math.isfinite, q)):
                    raise ValueError("PUBLIC_REFERENCE_TARGET_INVALID")
                targets += 1
        ticks = row["end_tick"]
    if len(frames) != 6294 or ticks != 64360 or targets != 3218:
        raise ValueError("REFERENCE_TERMINAL_COUNTS_MISMATCH")
    return dict(status="REFERENCE_HASHES_CLOCKS_AND_TARGETS_PASS", apple_frames=len(frames),
                burger_calls=len(calls), burger_frames=targets, burger_ticks=ticks,
                full_new_reexecution="NOT_TESTED", random_scene_generalization="NOT_VERIFIED")


def apple_prefix(args):
    import numpy as np
    from robot_arm.kitchen.control import DualArmController, state16
    from robot_arm.kitchen.model import build_scene
    from robot_arm.kitchen.platform import PhysicsEvidence
    from robot_arm.kitchen.registry import SceneRegistry, control_identity

    meta = load(args.assets/"apple_metadata.json")
    scene = build_scene(layout_id=LAYOUT, seed=0)
    if scene.scene_sha256 != SCENE or control_identity()["control_hash"] != CONTROL:
        raise ValueError("EXACT_FIXED_SCENE_AND_CONTROL_REQUIRED")
    controller = DualArmController(scene)

    class EvidenceOwner:
        def manifest(self):
            return SceneRegistry(scene).manifest()
    owner = EvidenceOwner()
    owner.scene, owner.controller = scene, controller
    evidence = PhysicsEvidence(args.output, owner, "programmatic_validation")
    max_error, frames, started = 0., 0, time.monotonic()
    try:
        with (args.assets/"apple_controls_25hz.jsonl").open() as stream:
            for line in stream:
                row = json.loads(line)
                if frames >= args.max_frames:
                    break
                if time.monotonic()-started > args.wall_seconds:
                    raise TimeoutError("BOUNDED_REFERENCE_WALL_LIMIT")
                controller.contact_guard.device_contacts.clear()
                for geom in row["device_contact_geoms"]:
                    controller.contact_guard.allow_device_contact(geom)
                accepted = controller.accept_joint_targets(row["target16"])
                if not np.allclose(accepted, row["target16"], rtol=0, atol=1e-12):
                    raise ValueError("RECORDED_ACCEPTED_TARGET_NO_LONGER_ACCEPTED")
                controller.advance(evidence.sample)
                if controller.stopped:
                    raise RuntimeError("REAL_CONTROLLER_STOP: " + str(controller.stop_reason))
                max_error = max(max_error, float(np.max(np.abs(state16(scene)-row["expected_state16"]))))
                if max_error > .002:
                    raise ValueError("MEASURED_PREFIX_DIVERGED_OVER_2MM_OR_2MRAD")
                frames += 1
        return dict(status="INTERNAL_FIXED_APPLE_PREFIX_PASS", executed_frames=frames,
            sim_tick=controller.tick, max_state16_error=max_error, evidence_file=str(evidence.path),
            full_cycle=frames == meta["frame_count"], full_task_success="NOT_REAUDITED",
            public_api="NOT_USED", task_success="NOT_INFERRED_FROM_PREFIX")
    finally:
        evidence.close("BOUNDED_REFERENCE_STOP_NOT_TASK_SUCCESS")
        controller.hold("BOUNDED_REFERENCE_STOP")


def burger_prefix(args):
    from integration.kitchen.planner_client import PlannerClient
    meta = load(args.assets/"burger_metadata.json")
    def require(reply):
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error")))
        return reply["result"]
    with PlannerClient(args.host, args.port, timeout=120.) as client:
        observed = require(client.call("observe"))
        ep = observed["episode"]
        if (observed["scene_hash"] != SCENE or observed["control_hash"] != CONTROL or
                ep["initialization_profile"] != "fixed" or ep["seed"] != 0 or observed["sim_tick"] != 0):
            raise ValueError("FRESH_FIXED_013_SEED0_CURRENT_CONTROL_REQUIRED_NOT_RESEARCH_RANDOM")
        started, frames, executed = time.monotonic(), 0, 0
        with (args.output/"rpc.jsonl").open("x", encoding="utf-8") as journal, \
                (args.assets/"burger_public_calls.jsonl").open(encoding="utf-8") as stream:
            try:
                for line in stream:
                    row = json.loads(line)
                    if row["method"] == "execute_trajectory" and frames + len(row["args"]["waypoints"]) > args.max_frames:
                        break
                    if time.monotonic()-started > args.wall_seconds:
                        raise TimeoutError("BOUNDED_REFERENCE_WALL_LIMIT")
                    reply = client.call(row["method"], row["args"], mutating=True)
                    journal.write(json.dumps(dict(method=row["method"], ok=reply.get("ok"),
                        error=reply.get("error"), token=reply.get("token"))) + "\n")
                    require(reply)
                    if client.token["sim_tick"] != row["end_tick"]:
                        raise ValueError("PUBLIC_REPLAY_CLOCK_DIVERGED")
                    if row["method"] == "execute_trajectory":
                        frames += len(row["args"]["waypoints"])
                    executed += 1
                final = require(client.call("observe"))
                return dict(status="PUBLIC_FIXED_BURGER_PREFIX_PASS", executed_frames=frames,
                    executed_calls=executed, sim_tick=final["sim_tick"],
                    historical_full_status=meta["historical_audit"]["status"],
                    full_new_reexecution="NOT_TESTED" if frames < 3218 else "EXECUTED_NOT_REAUDITED",
                    task_success="NOT_INFERRED_FROM_PREFIX", feedback=require(client.call("get_task_feedback")))
            finally:
                require(client.call("hold", mutating=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "apple", "burger", "extract"))
    parser.add_argument("--assets", type=Path, default=ASSETS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-frames", type=int, default=100)
    parser.add_argument("--wall-seconds", type=float, default=120.)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11031)
    for name in ("apple-record", "apple-report", "apple-audit", "water-audit", "burger-run"):
        parser.add_argument("--"+name, type=Path)
    args = parser.parse_args()
    if args.action == "extract":
        if any(getattr(args, name) is None for name in
               ("output", "apple_record", "apple_report", "apple_audit", "water_audit", "burger_run")):
            parser.error("extract requires explicit sealed source files and a fresh output")
        extract(args)
        return
    result = inspect(args.assets)
    if args.action != "inspect":
        if not args.output or not 1 <= args.max_frames <= 6294 or not 1 <= args.wall_seconds <= 2700:
            parser.error("execution requires fresh --output, 1..6294 frames, 1..2700 wall seconds")
        args.output.mkdir(parents=True, exist_ok=False)
        try:
            result = apple_prefix(args) if args.action == "apple" else burger_prefix(args)
        except Exception as error:
            save(args.output/"result.json", dict(status="FAIL", error=str(error), full_task_success="NOT_VERIFIED"))
            raise
        save(args.output/"result.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
