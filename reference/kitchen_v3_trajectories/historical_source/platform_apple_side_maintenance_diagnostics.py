"""Read-only apple record diagnostics for independent maintenance."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from robot_arm.kitchen.registry import digest


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def spans(mask, dt, minimum=.08):
    edges = np.diff(np.r_[False, mask, False].astype(int))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    return [{"start_s": float((a + 1) * dt), "duration_s": float((b - a) * dt)}
            for a, b in zip(starts, ends) if (b - a) * dt >= minimum - 1e-9]


def diagnose(source, output):
    """Verify every seal in one pass; retain numeric arrays, not contact lists."""
    source, output = Path(source), Path(output)
    report_path = source / "report.json"
    original = json.loads(report_path.read_text(encoding="utf-8"))
    record = next(source.glob("episode_*/physics.jsonl"))
    n = original["physics_samples"]
    times, tcp = np.empty(n), np.empty((n, 3))
    velocity, targets, controls = (np.empty((n, 7)) for _ in range(3))
    snapshots = {}
    requested = {r["tick"]: r["phase"] for r in original["trace"] if "time_s" in r}
    previous, count, metadata, end = "0" * 64, 0, None, None
    raw_hash = hashlib.sha256()
    with record.open("rb") as stream:
        for line in stream:
            raw_hash.update(line)
            envelope = json.loads(line)
            actual = envelope.pop("sha256")
            if envelope["previous_sha256"] != previous or digest(envelope) != actual:
                raise ValueError("RECORD_HASH_CHAIN_INVALID")
            previous = actual
            row = envelope["record"]
            if row["type"] == "metadata":
                metadata = row
            elif row["type"] == "end":
                end = row
            elif row["type"] == "physics":
                if row["sim_tick"] != count + 1 or count >= n:
                    raise ValueError("RECORD_TICK_GAP_OR_COUNT")
                times[count] = row["sim_time_s"]
                tcp[count] = row["tcp_pose"][0][:3]
                velocity[count] = row["qvel"][:7]
                targets[count] = row["accepted_target16"][:7]
                controls[count] = row["actual_ctrl16"][:7]
                if row["sim_tick"] in requested:
                    snapshots[requested[row["sim_tick"]]] = {
                        "tick": row["sim_tick"], "qpos": row["qpos"],
                        "tcp_pose": row["tcp_pose"][0], "device_joint_state": row["device_joint_state"]}
                count += 1
                if count % 25000 == 0:
                    print(json.dumps({"diagnosis_ticks": count, "total": n}), flush=True)
    if count != n or not end or end["last_recorded_tick"] != n or end["sampling_failed"]:
        raise ValueError("RECORD_END_OR_COUNT_INVALID")
    dt = metadata["physics_contract"]["time_step_s"]
    gaps = np.diff(times)
    if not np.allclose(gaps, dt, atol=1e-10, rtol=0):
        raise ValueError("RECORD_SAMPLE_TIME_GAP")
    speed = np.r_[0., np.linalg.norm(np.diff(tcp, axis=0), axis=1) / dt]
    joint_speed = np.max(np.abs(velocity), axis=1)
    quiet = (speed < .002) & (joint_speed < .01)
    pauses = spans(quiet, dt)
    changes = np.r_[True, np.max(np.abs(np.diff(targets, axis=0)), axis=1) > 1e-12]
    update_ticks = np.flatnonzero(changes) + 1
    segments, previous_tick = [], 0
    for mark in original["trace"]:
        if "time_s" not in mark:
            continue
        stop = mark["tick"]
        sl = slice(previous_tick, stop)
        segments.append({"end_phase": mark["phase"], "start_tick": previous_tick + 1,
            "end_tick": stop, "seconds": (stop - previous_tick) * dt,
            "tcp_distance_m": float(np.sum(speed[sl]) * dt),
            "mean_tcp_speed_m_s": float(np.mean(speed[sl])),
            "p95_tcp_speed_m_s": float(np.quantile(speed[sl], .95)),
            "max_joint_speed_rad_s": float(joint_speed[sl].max()),
            "quiet_seconds": float(np.sum(quiet[sl]) * dt)})
        previous_tick = stop
    acceleration = np.diff(controls, n=2, axis=0) / dt ** 2
    result = {"status": "ORIGINAL_RECORD_DIAGNOSIS_PASS_NOT_NEW_PHYSICS",
        "source_report": str(report_path.resolve()), "source_report_sha256": sha(report_path),
        "source_record": str(record.resolve()), "source_record_sha256": raw_hash.hexdigest(),
        "source_manifest": metadata["manifest"], "hash_chain": "PASS", "sealed_end": end,
        "physics_samples": count, "duration_s": float(times[-1]), "sampling_hz": 1 / dt,
        "minimum_sample_gap_s": float(gaps.min()), "maximum_sample_gap_s": float(gaps.max()),
        "accepted_target_update_count": int(len(update_ticks)),
        "target_updates_on_25hz_boundary": bool(np.all((update_ticks - 1) % 20 == 0)),
        "tcp_distance_m": float(speed.sum() * dt),
        "tcp_speed_m_s": {"max": float(speed.max()), "mean": float(speed.mean()),
            "p50": float(np.quantile(speed, .5)), "p95": float(np.quantile(speed, .95))},
        "max_joint_speed_rad_s": float(joint_speed.max()),
        "quiet_threshold": {"tcp_m_s": .002, "max_joint_rad_s": .01},
        "quiet_seconds": float(quiet.sum() * dt), "pause_count_ge_80ms": len(pauses),
        "longest_pauses": sorted(pauses, key=lambda r: r["duration_s"], reverse=True)[:20],
        "command_acceleration_abs_p99_rad_s2": float(np.quantile(np.abs(acceleration), .99)),
        "segments": segments, "snapshots": snapshots,
        "interpretation": [
            "Quiet intervals include required holds, release settling and waypoint convergence; not all are defects.",
            "Native sample cadence is checked separately from Unity presentation.",
            "Stored TCP finite differences are descriptive, not the Jacobian safety signal."]}
    write(output / "diagnosis.json", result)
    print(json.dumps({k: result[k] for k in ("status", "duration_s", "quiet_seconds",
        "pause_count_ge_80ms", "tcp_speed_m_s", "max_joint_speed_rad_s")}), flush=True)
    return result
