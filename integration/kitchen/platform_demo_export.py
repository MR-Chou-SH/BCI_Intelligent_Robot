"""Package audited candidate013 packets, without physics or controller execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

MAGIC = b"KDPREC01"
SCENE = "f52f3483a7d4863ead908a98aa2aae726f13b5f67e6468a1ed5323c5f3771e27"
CONTROL = "2ff69af80d5c6fc0724382dcd478ffa2e0699a227153c46d263a368c5b246155"
SOURCES = (
    ("apple", "APPLE / WASH / TRAY", "unity_candidate013_apple_record_02", "APPLE_FULL_CYCLE_MUJOCO_PASS"),
    ("burger", "BURGER / MICROWAVE", "unity_candidate013_record_replay_02", "COMPLETE_MICROWAVE_CYCLE_PASS"),
)


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write("\n")


def devices(packet):
    value = packet.get("recorded_device_state", packet["device_state"])
    cleaned = "apple" in value.get("cleaned_object_names", [])
    heated = "burger" in value.get("heated_object_names", [])
    heating = value.get("heating_object_name") is not None
    water = packet["device_state"].get("water_on", False)
    clean_time = packet["device_state"].get("clean_seconds", {}).get("apple", 0)
    return int(water) | int(heating) << 1 | int(cleaned) << 2 | int(heated) << 3, clean_time, value.get("heat_timer_s", 0)


def export(root: Path, fullops: Path, assets: Path, output: Path):
    if assets.exists() or output.exists():
        raise FileExistsError("Use new Demo asset and audit directories; never overwrite evidence")
    manifest_path = root / "m7_unity6000/Assets/Resources/KitchenPlatformV3/ModelCandidate013/scene.json"
    manifest = read(manifest_path)
    if manifest["model_scene_sha256"] != SCENE:
        raise ValueError("Wrong candidate013 scene")
    assets.mkdir(parents=True)
    output.mkdir(parents=True)
    catalog = {
        "schema": 1, "label": "OFFLINE VERIFIED MUJOCO RECORD",
        "model_resource": "KitchenPlatformV3/ModelCandidate013", "scene_hash": SCENE,
        "compiled_model_hash": manifest["model_compiled_sha256"], "control_hash": CONTROL,
        "manifest_sha256": sha(manifest_path), "quest": "HARDWARE_REQUIRED",
        "episodes": [],
        "playback_rule": "All audited packets retained (5 Hz plus exact phase samples). Native world body poses float32; position LERP and quaternion shortest-path SLERP for display only. No extrapolation. Device state zero-order hold, except exact audited apple water events. No new mj_step, animation, IK or controller execution.",
        "episode_boundary": "Independent reset episodes, never a continuous combined task",
    }
    reports = []
    for key, label, folder, expected_status in SOURCES:
        source = fullops / folder
        provenance_path = source / "component_provenance.json"
        packets_path = source / "component_frames.jsonl"
        provenance = read(provenance_path)
        if provenance["status"] != "SAME_RECORD_KINEMATIC_PACKET_DERIVATION_PASS" or provenance["hash_chain"] != "PASS":
            raise ValueError("Existing chain audit is not PASS")
        if provenance["scene_hash"] != SCENE or provenance["control_hash"] != CONTROL or provenance["compiled_model_hash"] != catalog["compiled_model_hash"]:
            raise ValueError("Episode immutable model/control identity mismatch")
        if provenance["input_sha256"] != sha(packets_path) or provenance["manifest_sha256"] != catalog["manifest_sha256"]:
            raise ValueError("Audited packet or model source changed")
        original_report = Path(provenance["source_report"])
        original_record = Path(provenance["source_record"])
        if sha(original_report) != provenance["source_report_sha256"] or read(original_report)["status"] != expected_status:
            raise ValueError("Original physical report changed")
        print("Hashing preserved original record:", key, original_record.stat().st_size, flush=True)
        if sha(original_record) != provenance["source_record_sha256"]:
            raise ValueError("Original recorded evidence changed")
        packets = [json.loads(line) for line in packets_path.open(encoding="utf-8")]
        if len(packets) != provenance["packets"]:
            raise ValueError("Truncated audited packet sequence")
        ids = [body["id"] for body in manifest["bodies"]]
        samples = []
        previous_tick, previous_time = -1, -1.0
        max_gap = 0.0
        binary = assets / (key + ".bytes")
        with binary.open("xb") as target:
            target.write(MAGIC + struct.pack("<II", len(packets), len(ids)))
            target.write(struct.pack("<" + "i" * len(ids), *ids))
            for packet in packets:
                world = packet["world"]
                tick, seconds = world["simTick"], packet["time_s"]
                if tick <= previous_tick or seconds <= previous_time or world["sceneHash"] != SCENE:
                    raise ValueError("Nonmonotonic or wrong-scene packet")
                if [body["id"] for body in world["bodies"]] != ids:
                    raise ValueError("Incomplete body map")
                if previous_time >= 0:
                    max_gap = max(max_gap, seconds - previous_time)
                previous_tick, previous_time = tick, seconds
                flags, clean, heat = devices(packet)
                target.write(struct.pack("<dqIff", seconds, tick, flags, clean, heat))
                for body in world["bodies"]:
                    values = body["position"] + body["quaternion_wxyz"]
                    if not all(math.isfinite(x) for x in values) or abs(sum(x*x for x in values[3:]) - 1) > .001:
                        raise ValueError("Invalid recorded pose")
                    target.write(struct.pack("<7f", *values))
                samples.append({"tick": tick, "time_s": seconds, "sample_sha256": packet["sample_sha256"],
                                "actual_ctrl16": packet["actual_ctrl16"], "label": packet["label"],
                                "device_state": packet.get("recorded_device_state", packet["device_state"])})
        if previous_tick != provenance["source_physics_samples"] or max_gap > .20001 or packets[0]["world"]["simTick"] != 1:
            raise ValueError("Incomplete full-time coverage")
        (assets / (key + "_provenance.json")).write_bytes(provenance_path.read_bytes())
        write(assets / (key + "_samples.json"), samples)
        events = [{"time_s": event["tick"] * .002, "tick": event["tick"], "water_on": event["water_on"]}
                  for event in provenance.get("water_state_transitions", [])]
        entry = {"id": key, "title": label, "resource": "KitchenDemoV3/" + key,
                 "sha256": sha(binary), "frame_count": len(packets), "body_count": len(ids),
                 "duration_s": provenance["source_duration_s"], "first_time_s": packets[0]["time_s"],
                 "terminal_state": provenance["terminal_state"], "source_record_sha256": provenance["source_record_sha256"],
                 "source_report_sha256": provenance["source_report_sha256"],
                 "record_final_chain_sha256": provenance["record_final_chain_sha256"],
                 "provenance_sha256": sha(assets / (key + "_provenance.json")),
                 "water_events": events,
                 "phases": [{"label": p["label"], "time_s": p["time_s"], "tick": p["tick"]} for p in provenance["phases"]]}
        catalog["episodes"].append(entry)
        reports.append({"id": key, "source_packet_path": str(packets_path), "source_packet_sha256": sha(packets_path),
                        "source_provenance_path": str(provenance_path), "source_provenance_sha256": sha(provenance_path),
                        "original_record": str(original_record), "original_record_sha256_verified": provenance["source_record_sha256"],
                        "chain": "Reused complete original chain audit; original file hash freshly verified; not reexecuted",
                        "max_packet_gap_s": max_gap, "binary_bytes": binary.stat().st_size})
    write(assets / "catalog.json", catalog)
    report = {"status": "OFFLINE_DEMO_ASSET_EXPORT_PASS", "catalog_sha256": sha(assets / "catalog.json"),
              "physics_steps": 0, "full_frame_rendering": 0, "quest": "HARDWARE_REQUIRED", "episodes": reports,
              "files": [{"path": str(p), "sha256": sha(p), "bytes": p.stat().st_size} for p in assets.iterdir() if p.is_file()]}
    write(output / "export_report.json", report)
    print(json.dumps({"status": report["status"], "episodes": len(reports), "assets": str(assets)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fullops", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(Path.cwd(), args.fullops, args.assets, args.output)


if __name__ == "__main__":
    main()
