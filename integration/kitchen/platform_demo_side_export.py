"""Derive a versioned Demo from sealed side-approach physics; never run dynamics."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import struct

import mujoco
import numpy as np

from integration.kitchen.platform_demo_export import CONTROL, MAGIC, SCENE, read, sha, write
from robot_arm.kitchen.model import build_scene
from robot_arm.kitchen.registry import digest

SIDE_CONTROL = "b6722bb912fddedf853d54925fda092b8ea2833cbcaeb904040ecde5965335d2"
RESOURCE = "KitchenDemoV3Side20261022"


def export(source: Path, audit_path: Path, output: Path, resource=RESOURCE, clearance_path=None):
    root = Path(__file__).resolve().parents[2]
    audit = read(audit_path)
    report_path = source / "report.json"
    report = read(report_path)
    record_path = Path(report["evidence_path"])
    if audit["status"] != "AUDITED_APPLE_SIDE_FULL_CYCLE_MUJOCO_PASS" or not audit["sealed_complete_end"]:
        raise ValueError("New complete independent physics audit required")
    if (audit["source_report_sha256"] != sha(report_path) or
            audit["source_record_sha256"] != sha(record_path) or
            Path(audit["source_record"]).resolve() != record_path.resolve()):
        raise ValueError("Audited record/report identity mismatch")
    clearance = None
    if clearance_path is not None:
        clearance = read(clearance_path)
        if (clearance["status"] != "APPLE_FULL_CYCLE_WATER_CLEARANCE_AUDITED_PASS" or
                clearance["source_record_sha256"] != audit["source_record_sha256"] or
                clearance["water_scan_source_record_sha256"] != audit["source_record_sha256"] or
                clearance["source_report_sha256"] != audit["source_report_sha256"] or
                Path(clearance["original_physics_audit"]).resolve() != audit_path.resolve() or
                clearance["native_intrusion_samples"] != 0 or clearance["unresolved_native_intervals"] != 0 or
                clearance["minimum_native_clearance_m"] <= 0):
            raise ValueError("Water-clearance certificate does not bind this sealed physics record")
    if resource == "KitchenDemoV3Clear20261023" and (clearance is None or
            audit["source_record_sha256"] != "4e7ccecda30014113c59df98b2bdab70507345c0f2e5dd79ed6ae4513dfe228c"):
        raise ValueError("Apple14 delivery requires the exact user-approved record and clearance certificate")
    if report["status"] != "APPLE_FULL_CYCLE_MUJOCO_PASS" or not all(report["checks"].values()):
        raise ValueError("Complete representative apple physics required")
    binding = report["identity"]["manifest"]
    if binding["scene_hash"] != SCENE or binding["control_identity"]["control_hash"] != SIDE_CONTROL:
        raise ValueError("Wrong scene or recorded control identity")
    scene = build_scene(seed=0, layout_id="kitchen_v3_norice_candidate_013")
    old_assets = root / "m7_unity6000/Assets/Resources/KitchenDemoV3"
    catalog = read(old_assets / "catalog.json")
    if (catalog["scene_hash"] != scene.scene_sha256 or
            catalog["compiled_model_hash"] != scene.compiled_model_sha256 or
            binding["compiled_model_hash"] != scene.compiled_model_sha256):
        raise ValueError("Same compiled candidate013 model required")
    manifest_path = root / "m7_unity6000/Assets/Resources" / catalog["model_resource"] / "scene.json"
    manifest = read(manifest_path)
    ids = [b["id"] for b in manifest["bodies"]]
    if ids != list(range(scene.model.nbody)) or sha(manifest_path) != catalog["manifest_sha256"]:
        raise ValueError("Immutable complete presentation body map required")
    output.mkdir(parents=True, exist_ok=False)
    assets = output / resource
    assets.mkdir()
    model, data = scene.model, mujoco.MjData(scene.model)
    phases = {int(p["tick"]): p["phase"] for p in report["trace"] if p.get("tick") is not None}
    selected, water_events, metadata, ending = [], [], None, None
    previous, count, water = "0" * 64, 0, False
    raw_hash = hashlib.sha256()
    with record_path.open("rb") as stream:
        for line_number, raw in enumerate(stream):
            raw_hash.update(raw)
            row = json.loads(raw)
            signature = row.pop("sha256")
            if row["previous_sha256"] != previous or digest(row) != signature or ending is not None:
                raise ValueError("Broken or unsealed chain at line " + str(line_number + 1))
            previous, value = signature, row["record"]
            if value["type"] == "metadata":
                if metadata is not None or line_number != 0:
                    raise ValueError("Metadata is not first and unique")
                metadata = value
                if (metadata["manifest"]["scene_hash"] != SCENE or
                        metadata["manifest"]["compiled_model_hash"] != scene.compiled_model_sha256 or
                        metadata["manifest"]["control_identity"] != binding["control_identity"]):
                    raise ValueError("Record metadata binding mismatch")
                continue
            if metadata is None:
                raise ValueError("Missing record metadata")
            if value["type"] == "end":
                ending = value
                continue
            if value["type"] in {"finite_inputs", "finite_continuation", "button_preload_preflight"}:
                continue
            if value["type"] != "physics":
                raise ValueError("Unknown source row type")
            count += 1
            if value["sim_tick"] != count or value["controller_stopped"]:
                raise ValueError("Missing physical substep or controller stopped")
            qpos = np.asarray(value["qpos"])
            if qpos.shape != (model.nq,) or not np.isfinite(qpos).all():
                raise ValueError("Invalid source qpos")
            contract = metadata["physics_contract"]
            angle = value["device_joint_state"][contract["device_joint_names"].index("faucet_switch")][0]
            new_water = bool(value["device_state"]["enabled"] and angle >= np.deg2rad(40))
            changed = new_water != water
            if changed:
                water_events.append({"time_s": value["sim_time_s"], "tick": count, "water_on": new_water})
            water = new_water
            if count != 1 and count % 20 and count not in phases and count != report["physics_samples"] and not changed:
                continue
            # Detached forward kinematics of the recorded post-step state, never mj_step.
            data.qpos[:] = qpos
            mujoco.mj_kinematics(model, data)
            state = value["device_state"]
            flags = int(water) | int(state["heating_object_name"] is not None) << 1
            flags |= int("apple" in state["cleaned_object_names"]) << 2 | int("burger" in state["heated_object_names"]) << 3
            clean = state["wash_timer_s"][contract["wash_timer_object_names"].index("apple")]
            poses = np.c_[data.xpos[ids], data.xquat[ids]]
            if not np.isfinite(poses).all() or np.max(np.abs(np.sum(poses[:, 3:] ** 2, axis=1) - 1)) > .001:
                raise ValueError("Invalid derived body pose")
            selected.append((value["sim_time_s"], count, flags, clean, state["heat_timer_s"], poses.copy(), signature))
    if (not ending or ending["sampling_failed"] or ending["physics_samples"] != count or
            count != report["physics_samples"] or count != audit["physics_samples"] or
            ending["last_recorded_tick"] != count or raw_hash.hexdigest() != audit["source_record_sha256"]):
        raise ValueError("Incomplete sealed physical record")
    max_gap = max(b[0] - a[0] for a, b in zip(selected, selected[1:]))
    if max_gap > .040001 or selected[0][1] != 1 or selected[-1][1] != count:
        raise ValueError("Incomplete 25Hz display coverage")
    binary = assets / "apple.bytes"
    with binary.open("xb") as stream:
        stream.write(MAGIC + struct.pack("<II", len(selected), len(ids)))
        stream.write(struct.pack("<" + "i" * len(ids), *ids))
        for seconds, tick, flags, clean, heat, poses, _ in selected:
            stream.write(struct.pack("<dqIff", seconds, tick, flags, clean, heat))
            stream.write(np.asarray(poses, dtype="<f4").tobytes())
    provenance = {"status": "SAME_RECORD_KINEMATIC_PACKET_DERIVATION_PASS", "hash_chain": "PASS",
        "source_report": str(report_path), "source_report_sha256": sha(report_path),
        "source_record": str(record_path), "source_record_sha256": raw_hash.hexdigest(),
        "record_final_chain_sha256": previous, "source_physics_samples": count,
        "source_duration_s": report["physics_seconds"], "scene_hash": SCENE,
        "compiled_model_hash": scene.compiled_model_sha256, "control_hash": SIDE_CONTROL,
        "manifest_sha256": sha(manifest_path), "independent_audit": str(audit_path),
        "independent_audit_sha256": sha(audit_path), "tool_sha256": sha(Path(__file__)),
        "display_hz": 25, "max_gap_s": max_gap, "packets": len(selected),
        "water_state_transitions": water_events, "physics_steps": 0,
        "pose_rule": "Recorded poststep qpos; detached mj_kinematics only; display LERP/SLERP; no smoothing of physical trajectory",
        "boundary": "Independent apple episode, not continuous with burger; device timer, not fluid simulation",
        "side_approach_limit": "Declared potential-water-cylinder clearance audited; not fluid dynamics or physical hardware" if clearance else
            "Full physics PASS is not a full jet-clearance PASS; archived geometry audit finds water-off approach intrusion, no sampled water-on intrusion"}
    if clearance is not None:
        provenance["water_clearance"] = {"audit": str(clearance_path), "sha256": sha(clearance_path),
            "status": clearance["status"], "contract": clearance["water_contract"],
            "checked_native_samples": clearance["checked_native_samples"],
            "minimum_native_clearance_m": clearance["minimum_native_clearance_m"],
            "minimum_water_on_clearance_m": clearance["minimum_water_on_clearance_m"],
            "native_intrusion_samples": 0, "unresolved_native_intervals": 0}
    write(assets / "apple_provenance.json", provenance)
    write(assets / "apple_samples.json", [{"tick": r[1], "time_s": r[0], "sample_sha256": r[6]} for r in selected])
    apple = {"id": "apple", "title": "APPLE / SIDE TAP / TRAY", "resource": resource + "/apple",
        "sha256": sha(binary), "frame_count": len(selected), "body_count": len(ids),
        "duration_s": selected[-1][0], "first_time_s": selected[0][0], "terminal_tick": count,
        "terminal_state": "APPLE_RELEASED_STABLE_ON_TRAY", "control_hash": SIDE_CONTROL,
        "source_record_sha256": raw_hash.hexdigest(), "source_report_sha256": sha(report_path),
        "record_final_chain_sha256": previous, "provenance_sha256": sha(assets / "apple_provenance.json"),
        "water_events": water_events, "phases": [{"label": label, "tick": tick, "time_s": tick * .002} for tick, label in sorted(phases.items())]}
    burger = copy.deepcopy(catalog["episodes"][1])
    burger.update(resource=resource + "/burger", control_hash=CONTROL, terminal_tick=64160)
    for suffix in (".bytes", "_provenance.json", "_samples.json"):
        shutil.copyfile(old_assets / ("burger" + suffix), assets / ("burger" + suffix))
    if sha(assets / "burger.bytes") != burger["sha256"] or sha(assets / "burger_provenance.json") != burger["provenance_sha256"]:
        raise ValueError("Protected burger replay changed")
    catalog.update(schema=2, control_hash="PER_EPISODE_RECORDED_CONTROL", episodes=[apple, burger],
        playback_rule="Apple 25Hz and exact phases/water events from new sealed native physics; burger byte-identical original; position LERP/quaternion SLERP only; explicit replay speeds do not change physics execution speed")
    write(assets / "catalog.json", catalog)
    write(output / "export_report.json", {"status": "VERSIONED_SIDE_DEMO_EXPORT_PASS", "assets": str(assets),
        "catalog_sha256": sha(assets / "catalog.json"), "apple_frames": len(selected), "apple_max_gap_s": max_gap,
        "source_record_sha256": raw_hash.hexdigest(), "record_final_chain_sha256": previous,
        "original_catalog_sha256": sha(old_assets / "catalog.json"), "burger_bytes_unchanged": True,
        "physics_steps": 0, "goal": "PAUSED_NOT_OPERATED", "quest": "HARDWARE_REQUIRED"})
    print(json.dumps({"status": "VERSIONED_SIDE_DEMO_EXPORT_PASS", "assets": str(assets), "frames": len(selected)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--resource", choices=(RESOURCE, "KitchenDemoV3Clear20261023"), default=RESOURCE)
    parser.add_argument("--clearance-audit", type=Path)
    args = parser.parse_args()
    export(args.source.resolve(), args.audit.resolve(), args.output.resolve(), args.resource,
           args.clearance_audit.resolve() if args.clearance_audit else None)
