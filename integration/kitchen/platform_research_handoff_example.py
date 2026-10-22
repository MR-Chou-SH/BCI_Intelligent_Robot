"""Portable, bounded Research API example and optional real-launcher regression.

Run as a module from a checkout, or run this file by absolute path. No Goal,
Quest, training, task planning, dependency installation or shared source edits.
Only --launch owns a service; attach mode never terminates an existing server.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import traceback
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))

from integration.kitchen.planner_client import PlannerClient

LAYOUT = "kitchen_v3_norice_candidate_013"
TOKEN_FIELDS = ("runtime_session", "epoch", "sim_tick", "scene_hash",
                "control_hash", "episode_id", "initialization_hash")
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def repository_baseline():
    git = shutil.which("git")
    if git is None:
        return {"returncode": None, "status": "UNAVAILABLE", "reason": "Optional Git executable not installed"}
    try:
        result = subprocess.run([git, "status", "--branch", "--porcelain=v2", "--untracked-files=normal"],
            cwd=ROOT, capture_output=True, text=True, timeout=10., creationflags=CREATION_FLAGS)
        return dict(returncode=result.returncode, status=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"returncode": None, "status": "UNAVAILABLE", "reason": str(error)}


def parse_seed(value):
    if value.lower() in ("auto", "null"):
        return None
    try:
        seed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Seed must be uint32 or auto") from error
    if not 0 <= seed < 2**32:
        raise argparse.ArgumentTypeError("Seed must be uint32 or auto")
    return seed


class Rpc:
    def __init__(self, client, output):
        self.client, self.output, self.count = client, output, 0

    def request(self, method, args=None, mutating=False, **kwargs):
        if mutating:
            kwargs.setdefault("request_id", uuid.uuid4().hex)
        sent_token = copy.deepcopy(kwargs.get("token", self.client.token)) if mutating else None
        started = time.monotonic()
        reply = self.client.call(method, args, mutating=mutating, **kwargs)
        self.count += 1
        with (self.output / "rpc.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(sequence=self.count, method=method, args=args or {},
                mutating=mutating, request_id=kwargs.get("request_id"), token=sent_token,
                writer_id=self.client.writer_id if mutating else None, reply=reply,
                elapsed_s=time.monotonic()-started), allow_nan=False)+"\n")
        return reply

    def call(self, method, args=None, mutating=False, **kwargs):
        reply = self.request(method, args, mutating, **kwargs)
        check(reply.get("ok"), f"{method}: {reply}")
        return reply["result"]


def unchanged(before, after):
    fields = (*TOKEN_FIELDS, "episode", "qpos", "qvel", "accepted_target16", "actual_ctrl16", "execution")
    check(all(before[k] == after[k] for k in fields), "Rejected request changed live state")


def validate_episode(rpc, state, manifest):
    episode = state["episode"]
    check(manifest["layout_id"] == LAYOUT and manifest["runtime"]["mode"] == "manual",
          "Expected candidate013 / manual")
    check(manifest["environment"]["initialization_profile"] == "research", "Expected Research")
    check(manifest["episode"] == episode and manifest["initialization"]["seed"] == episode["seed"],
          "Manifest/observe mixed episodes")
    check(type(episode["seed"]) is int and 0 <= episode["seed"] < 2**32, "Invalid published seed")
    check(episode["base_scene_seed"] == 0, "Research base scene seed changed")
    check(episode["initialization_hash"] == manifest["initialization"]["episode_scene_hash"], "Hash mismatch")
    check(all(rpc.client.token[k] == state[k] for k in TOKEN_FIELDS), "Token/observe mismatch")
    check(state["sim_tick"] == 0 and state["qpos"] == manifest["initialization"]["initial_qpos"],
          "Not the actual unstepped initial state")
    geometry = manifest["initialization"]["geometry"]
    check(geometry["pass"] and not geometry["errors"] and len(geometry["groups"]) == 6,
          "Published full-shape initialization audit failed")
    objects = rpc.call("query_objects")
    check(objects == state["objects"] == manifest["objects"], "Semantic object query mismatch")
    world = rpc.call("get_world_state")
    check(world["episode"] == episode and world["simTick"] == 0, "World/observe mismatch")
    bodies = {body["name"]: body for body in world["bodies"]}
    compared = 0
    for item in objects:
        if "body_name" in item:
            body = bodies[item["body_name"]]
            check(body["position"] == item["position_world_m"] and
                  body["quaternion_wxyz"] == item["quaternion_wxyz"], "Public body pose mismatch")
            compared += 1
    return dict(published_geometry=geometry, queried_objects=len(objects),
                compared_public_body_poses=compared, world_bodies=len(bodies))


def audit_live_geometry(scene, state, manifest):
    # Reuse the production auditor on a read-only local clone of API-returned qpos.
    # This does not rerun initialization or step a second physics owner.
    import mujoco
    import numpy as np
    from robot_arm.kitchen.research_initialization import audit_research_initial_state
    from robot_arm.kitchen.registry import object_bounds

    check(scene.scene_sha256 == manifest["scene_hash"] and
          scene.compiled_model_sha256 == manifest["compiled_model_hash"], "Local/remote model mismatch")
    scene.data.qpos[:] = state["qpos"]
    scene.data.qvel[:] = state["qvel"]
    scene.data.time = state["sim_time_s"]
    mujoco.mj_forward(scene.model, scene.data)
    audit = audit_research_initial_state(scene)
    check(audit["pass"], f"Live full-shape audit: {audit['errors']}")
    maximum = 0.
    for item in state["objects"]:
        if item["id"] in scene.objects:
            actual = np.asarray(object_bounds(scene, item["id"]))
            delta = float(np.max(np.abs(actual-np.asarray(item["bounds_world_m"]))))
            maximum = max(maximum, delta)
    check(maximum <= 1e-9, "API-returned full-shape bounds differ from qpos clone")
    return dict(audit=audit, maximum_public_bounds_difference_m=maximum,
                input="actual Planner API qpos; no local sampling or dynamics")


def read_record(path, output, state, execution):
    before = path.stat()
    check(before.st_size <= 32*1024*1024, "Record exceeds finite example bound")
    previous, metadata, end, last, count = "0"*64, None, None, None, 0
    with path.open("rb") as stream:
        for raw in stream:
            check(len(raw) <= 16*1024*1024 and raw.endswith(b"\n"), "Incomplete physics record")
            envelope = json.loads(raw)
            signature = envelope.pop("sha256")
            check(envelope["previous_sha256"] == previous and digest(envelope) == signature,
                  "Invalid physics hash chain")
            previous, row = signature, envelope["record"]
            check(end is None, "Record after sealed end")
            if row["type"] == "metadata":
                check(metadata is None and count == 0, "Duplicate/late metadata")
                metadata = row
            elif row["type"] == "physics":
                check(metadata is not None, "Physics before metadata")
                check(row["sim_tick"] == execution["start_tick"]+count+1, "Physics tick gap")
                last, count = row, count+1
            elif row["type"] == "end":
                end = row
            else:
                raise RuntimeError("Unexpected record type")
    check(metadata is not None and end is not None and count == 20, "Not a sealed 20-tick action")
    check(not end["sampling_failed"] and end["physics_samples"] == count and
          end["last_recorded_tick"] == last["sim_tick"] == state["sim_tick"], "Invalid sealed sample count")
    check(metadata["execution_id"] == execution["id"] and
          metadata["manifest"]["episode"] == state["episode"] and
          metadata["manifest"]["control_identity"]["control_hash"] == state["control_hash"],
          "Sealed record identity mismatch")
    check(last["qpos"] == state["qpos"] and last["qvel"] == state["qvel"] and
          last["accepted_target16"] == state["accepted_target16"], "Last sample differs from observe")
    check(end["reason"] == "WAYPOINTS_CONSUMED_NOT_TASK_SUCCESS", "Unexpected execution termination")
    after = path.stat()
    check((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "Record changed during audit")
    destination = output / "records" / (path.parent.name+".jsonl")
    destination.parent.mkdir(exist_ok=True)
    check(not destination.exists(), "Refusing to overwrite copied evidence")
    shutil.copyfile(path, destination)
    check(sha(destination) == sha(path), "Evidence copy hash mismatch")
    return dict(path=str(path), copied_path=str(destination), sha256=sha(path), samples=count,
                final_chain_sha256=previous, end=end, live_state_equal=True)


def tiny_motion(rpc, args, output):
    if args.settle_substeps:
        settled = rpc.call("step", {"n_substeps": args.settle_substeps}, True)
        check(not settled["stopped"], f"Bounded settle stopped: {settled['stop_reason']}")
    before = rpc.call("observe")
    target = list(before["accepted_target16"])
    target[0] += .002
    preview = rpc.call("check_motion", {"end_joint_targets": target})
    check(preview["pass"], f"Tiny motion preflight rejected: {preview}")
    submitted = rpc.call("submit_joint_targets", {"joint_targets": target, "source": "external_planner"}, True)
    check(not submitted["rate_limited"] and submitted["accepted"] == target, "Unexpected target clipping")
    busy = rpc.request("reset", {"seed": 0}, True)
    check(not busy["ok"] and busy["error"]["code"] == "EXECUTION_BUSY_CANCEL_FIRST", "Busy reset not rejected")
    running = rpc.call("observe")
    check(running["qpos"] == before["qpos"] and running["episode"] == before["episode"], "Busy reset mutated state")
    after = rpc.call("step", {"n_substeps": 20}, True)
    check(after["sim_tick"] == before["sim_tick"]+20 and not after["stopped"], "Actual step failed/stopped")
    execution = rpc.call("get_execution_status", {"execution_id": submitted["execution"]["id"]})
    check(execution["status"] == "COMPLETED" and execution["evidence_complete"], "Execution/record not complete")
    check(after["accepted_target16"] == target and after["robots"]["left"]["qpos_rad"] !=
          before["robots"]["left"]["qpos_rad"], "No measured robot motion")
    feedback = rpc.call("get_task_feedback", {"task_instance_id": "bounded-handoff-example",
                                             "object_ids": ["apple", "burger"]})
    check(feedback["evidence_tick"] == after["sim_tick"] and
          feedback["execution_id"] == execution["id"] and feedback["task_success"] == "NOT_VERIFIED" and
          feedback["trajectory_completion_is_task_success"] is False, "Feedback identity/semantics changed")
    record = read_record(Path(execution["evidence_path"]), output, after, execution) if args.read_records else None
    return dict(status="PASS", requested_delta_rad=.002, requested_substeps=20,
                settle_substeps=args.settle_substeps, measured_joint_delta_rad=
                after["robots"]["left"]["qpos_rad"][0]-before["robots"]["left"]["qpos_rad"][0],
                preview=preview, execution=execution, feedback=feedback, sealed_record=record,
                busy_reset_protected=True, task_success="NOT_VERIFIED")


def run_client(args, output, ready):
    host = args.host or (ready or {}).get("planner_host", "127.0.0.1")
    check(host not in ("0.0.0.0", "::"), "Supply --host with a reachable client address")
    port = (ready or {}).get("planner_port", 11031) if args.port is None or args.launch else args.port
    rows, failures, seen, fingerprints = [], [], set(), {}
    world_socket = world_reader = None
    scene = None
    try:
        with PlannerClient(host=host, port=port, timeout=args.timeout) as client:
            rpc = Rpc(client, output)
            initial = rpc.call("observe")
            if ready:
                check(ready["status"] == "PLATFORM_READY" and ready["layout"] == LAYOUT and
                      ready["mode"] == "manual" and ready["initialization_profile"] == "research" and
                      ready["token"]["runtime_session"] == initial["runtime_session"],
                      "Ready snapshot does not identify the connected runtime")
                if args.launch:
                    check(ready["episode"] == initial["episode"], "New launcher initial episode mismatch")
                    check(Path(ready["data_root"]).resolve() == args.data_root.resolve() and
                          Path(ready["import_root"]).resolve() == ROOT, "Launcher root/data pass-through mismatch")
                    if args.initial_seed is not None:
                        check(initial["episode"]["seed"] == args.initial_seed, "Launcher Seed pass-through mismatch")
                world_socket = socket.create_connection((host, ready["world_port"]), timeout=8.)
                world_reader = world_socket.makefile("rb")
            save(output / "initial_observe.json", initial)
            seen.add(initial["episode"]["episode_id"])
            for index, seed in enumerate(args.seeds):
                row = dict(index=index, requested_seed=seed)
                rows.append(row)
                try:
                    prior = rpc.call("observe")
                    token_before = copy.deepcopy(client.token)
                    request_id = uuid.uuid4().hex
                    reply = rpc.request("reset", {"seed": seed}, True, request_id=request_id)
                    if not reply["ok"]:
                        retained = rpc.call("observe")
                        unchanged(prior, retained)
                        row.update(reset_failure=reply["error"], failed_reset_retained=True)
                        raise RuntimeError(f"Representative reset failed safely: {reply['error']}")
                    state, episode = reply["result"], reply["result"]["episode"]
                    row["episode"] = episode
                    check(episode["episode_id"] not in seen and state["epoch"] == prior["epoch"]+1,
                          "Successful reset did not publish a fresh episode/epoch")
                    seen.add(episode["episode_id"])
                    check(seed is None and episode["seed"] != prior["episode"]["seed"] or episode["seed"] == seed,
                          "Explicit/new seed semantics failed")
                    duplicate = rpc.request("reset", {"seed": seed}, True, request_id=request_id, token=token_before)
                    check(duplicate == reply, "Exact duplicate reset not idempotent")
                    stale = rpc.request("step", {"n_substeps": 1}, True, token=token_before)
                    check(not stale["ok"] and stale["error"]["code"] == "STALE_OR_MISMATCHED_TOKEN", "Old episode token accepted")
                    unchanged(state, rpc.call("observe"))
                    manifest = rpc.call("get_scene_manifest")
                    save(output / f"episode_{index:02d}_manifest.json", manifest)
                    row.update(validate_episode(rpc, state, manifest))
                    fingerprint = digest(dict(qpos=state["qpos"], qvel=state["qvel"],
                                              objects=state["objects"], initialization_hash=episode["initialization_hash"]))
                    if episode["seed"] in fingerprints:
                        check(fingerprint == fingerprints[episode["seed"]], "Same seed not exactly reproducible")
                        row["same_seed_reproduced"] = True
                    fingerprints[episode["seed"]] = fingerprint
                    row["initial_fingerprint_sha256"] = fingerprint
                    if args.audit_geometry:
                        if scene is None:
                            from robot_arm.kitchen.model import build_scene
                            scene = build_scene(layout_id=manifest["layout_id"], seed=0)
                        row["live_geometry"] = audit_live_geometry(scene, state, manifest)
                    if world_reader:
                        deadline = time.monotonic()+8.
                        matched = False
                        while time.monotonic() < deadline:
                            line = world_reader.readline(2*1024*1024)
                            check(line and line.endswith(b"\n"), "Missing complete world stream frame")
                            with (output / "world_frames.jsonl").open("ab") as stream:
                                stream.write(line)
                            frame = json.loads(line)
                            if frame["episode"] == episode:
                                check(frame["simTick"] == 0 and frame["sceneHash"] == state["scene_hash"], "World stream identity mismatch")
                                matched = True
                                break
                        check(matched, "Persistent world stream did not reach current episode")
                        row["persistent_world_episode_match"] = True
                    invalid_seeds = (-1, True, 2**32, 1.5, "1") if index == 0 else (-1,)
                    invalid_rows = []
                    for invalid in invalid_seeds:
                        rejected = rpc.request("reset", {"seed": invalid}, True)
                        retained = rpc.call("observe")
                        check(not rejected["ok"] and rejected["error"]["code"] == "INVALID_SEED", "Invalid seed accepted")
                        unchanged(state, retained)
                        check(rejected["error"]["detail"] == retained["last_reset_failure"] and
                              retained["last_reset_failure"]["retained_episode_id"] == episode["episode_id"], "Reset failure not bound to retained episode")
                        invalid_rows.append(dict(seed=invalid, retained_episode_id=episode["episode_id"]))
                    row["invalid_seed_atomic"] = invalid_rows
                    row["duplicate_reset_idempotent"] = row["stale_control_rejected"] = True
                    row["motion"] = tiny_motion(rpc, args, output)
                    row["status"] = "PASS"
                except Exception as error:
                    row.update(status="FAIL", error=str(error))
                    failures.append(dict(index=index, seed=seed, error=str(error)))
                    rpc.request("hold", mutating=True)
                print(json.dumps(dict(index=index, seed=seed, status=row["status"],
                                      error=row.get("error"))), flush=True)
            rpc.call("hold", mutating=True)
            final = rpc.call("observe")
            save(output / "final_observe.json", final)
            if args.read_records and ready:
                directory = Path(ready["data_root"]) / ("session_"+initial["runtime_session"])
                reset_failures = [json.loads(line) for line in (directory / "reset_failures.jsonl").read_text().splitlines()]
                expected = [item for row in rows for item in row.get("invalid_seed_atomic", [])]
                check(len(reset_failures) >= len(expected) and all(any(
                    failure["attempted_seed"] == item["seed"] and
                    failure["retained_episode_id"] == item["retained_episode_id"] for failure in reset_failures)
                    for item in expected), "Persisted reset failure evidence missing")
                save(output / "reset_failures_readback.json", reset_failures)
                for row in rows:
                    if "episode" in row:
                        prepared = load(directory / "initializations" / (row["episode"]["episode_id"]+".json"))
                        check(prepared["episode"] == row["episode"], "Prepared metadata/live episode mismatch")
                save(output / "initialization_readback.json", dict(episodes=len(rows), live_ids_matched=True))
            return dict(status="PC_RESEARCH_HANDOFF_PASS" if not failures else "PARTIAL",
                        host=host, port=port, initial_episode=initial["episode"], episodes=rows,
                        failures=failures, rpc_count=rpc.count, final_safe_hold=final["stopped"],
                        readback_requested=args.read_records, geometry_clone_requested=args.audit_geometry)
    finally:
        if world_reader is not None:
            world_reader.close()
        if world_socket is not None:
            world_socket.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--output", type=Path, required=True, help="New evidence directory; never overwritten")
    parser.add_argument("--ready-file", "--readyfile", type=Path)
    parser.add_argument("--seeds", nargs="+", type=parse_seed, default=[0], help="At most 8: uint32 values or auto")
    parser.add_argument("--timeout", type=float, default=120.)
    parser.add_argument("--settle-substeps", type=int, default=0, help="Optional 0..1000 initial stationary substeps per episode")
    parser.add_argument("--audit-geometry", action="store_true", help="Local same-model read-only audit of network qpos")
    parser.add_argument("--read-records", action="store_true", help="Read local server records and copy verified chains")
    parser.add_argument("--launch", action="store_true", help="Start/stop the checkout's actual PS launcher")
    parser.add_argument("--data-root", type=Path, help="New lexical experiments/kitchen_platform_v3/... path for --launch")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--powershell", default=shutil.which("pwsh") or shutil.which("powershell"))
    parser.add_argument("--initial-seed", type=parse_seed, default=0)
    args = parser.parse_args(argv)
    if not 1 <= len(args.seeds) <= 8 or not 0 <= args.settle_substeps <= 1000 or args.settle_substeps % 20:
        parser.error("Require 1..8 resets and 0..1000 settle substeps in complete 20-tick frames")
    if not 1 <= args.timeout <= 180 or args.port is not None and not 0 <= args.port <= 65535:
        parser.error("Require timeout 1..180 s and port 0..65535")
    if args.launch and (not args.data_root or not args.ready_file or not args.powershell):
        parser.error("--launch requires --data-root, --ready-file and an available PowerShell")
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    result = dict(status="PARTIAL", scope="INDEPENDENT_MAINTENANCE_NOT_A_GOAL_STAGE",
                  started_at_utc=started, worktree=str(ROOT), source_sha256=sha(__file__),
                  argv=sys.argv, goal_actions="NONE", quest="NOT_ATTEMPTED", task_success="NOT_VERIFIED")
    plan = dict(result, criteria=["actual launcher/ready/Planner/world identity", "bounded representative resets",
        "full-shape geometry from live qpos", "same seed/new episode", "failed reset protection",
        "preflight then tiny joint action/20 real steps/feedback", "sealed physical chain readback", "owned process cleanup"],
        shared_sources={str(p.relative_to(ROOT)): sha(p) for p in [ROOT/"scripts/start_kitchen_platform.ps1",
            ROOT/"integration/kitchen/platform_server.py", ROOT/"integration/kitchen/planner_client.py",
            ROOT/"robot_arm/kitchen/platform.py", ROOT/"robot_arm/kitchen/research_initialization.py",
            ROOT/"robot_arm/kitchen/model.py", ROOT/"robot_arm/kitchen/control.py", ROOT/".codex/ACTIVE_GOAL.json"]
            if p.exists()})
    plan["repository_baseline"] = repository_baseline()
    handoff = ROOT / "docs/kitchen_platform_v3_20261020/RESEARCH_TECH_HANDOFF_20261022.md"
    if handoff.exists():
        plan["designated_handoff"] = dict(path=str(handoff), observed_sha256=sha(handoff),
                                         approval="latest explicit maintenance request; not a Goal-stage protocol approval")
    save(output / "plan.json", plan)
    process, stdout, stderr, ready = None, None, None, None
    try:
        if args.launch:
            check(not args.data_root.exists() and not args.ready_file.exists(), "Require fresh launcher data/ready paths")
            command = [args.powershell, "-NoProfile", "-File", str(ROOT/"scripts/start_kitchen_platform.ps1"),
                "-Layout", LAYOUT, "-InitializationProfile", "research", "-Mode", "manual",
                "-DataRoot", str(args.data_root.absolute()), "-ReadyFile", str(args.ready_file.absolute()),
                "-PlannerPort", str(args.port or 0), "-WorldPort", "0", "-BindHost", args.host or "127.0.0.1",
                "-RunSeconds", "180", "-PythonExe", args.python]
            if args.initial_seed is not None:
                command.extend(["-Seed", str(args.initial_seed)])
            result["launcher_command"] = command
            stdout, stderr = (output/"stdout.log").open("x"), (output/"stderr.log").open("x")
            process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr, creationflags=CREATION_FLAGS)
            result["owned_launcher_pid"] = process.pid
            deadline = time.monotonic()+90.
            while not args.ready_file.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.1)
            check(args.ready_file.exists() and process.poll() is None, "Actual launcher did not expose live readiness")
        ready = load(args.ready_file) if args.ready_file else None
        if ready:
            save(output / "ready_snapshot.json", ready)
        result.update(run_client(args, output, ready))
    except Exception as error:
        result.update(status="PARTIAL", error=str(error), traceback=traceback.format_exc())
    finally:
        if process is not None:
            try:
                try:
                    process.wait(timeout=2.)
                    result["owned_launcher_exit_code"] = process.returncode
                except subprocess.TimeoutExpired:
                    # Only this freshly spawned launcher tree; never attach-mode services.
                    if sys.platform == "win32":
                        cleanup = subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                            capture_output=True, text=True, timeout=20., creationflags=CREATION_FLAGS)
                        result["owned_cleanup"] = dict(command=cleanup.args, returncode=cleanup.returncode,
                            stdout=cleanup.stdout, stderr=cleanup.stderr, graceful=False)
                    else:
                        process.terminate()
                    process.wait(timeout=10.)
                if ready:
                    listeners = []
                    for port in (ready["planner_port"], ready["world_port"]):
                        with socket.socket() as probe:
                            probe.settimeout(.5)
                            listeners.append(dict(port=port, connect_code=probe.connect_ex((args.host or "127.0.0.1", port))))
                    result["owned_listener_cleanup"] = listeners
                    check(all(item["connect_code"] != 0 for item in listeners), "Owned service listener still reachable")
            except Exception as error:
                result.update(status="PARTIAL", cleanup_error=str(error))
            if stdout is not None:
                stdout.close()
            if stderr is not None:
                stderr.close()
        result["shared_sources_unchanged"] = all(sha(ROOT/name) == expected for name, expected in plan["shared_sources"].items())
        if not result["shared_sources_unchanged"]:
            result["status"] = "PARTIAL"
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        result["evidence_files"] = {str(path.relative_to(output)): dict(bytes=path.stat().st_size, sha256=sha(path))
                                    for path in output.rglob("*") if path.is_file()}
        save(output/"report.json", result)
        save(output/"state.json", dict(status=result["status"], completed_at_utc=result["finished_at_utc"],
             report_path=str(output/"report.json"), owned_launcher_running=bool(process and process.poll() is None)))
        with (output/"worklog.jsonl").open("x", encoding="utf-8") as stream:
            for sequence, (event, at) in enumerate((("RUN_STARTED", started), ("VERIFICATION_COMPLETED", result["finished_at_utc"])), 1):
                stream.write(json.dumps(dict(sequence=sequence, event=event, timestamp_utc=at,
                    scope=result["scope"], status=result["status"], report_path=str(output/"report.json")))+"\n")
    print(result["status"], str(output/"report.json"), flush=True)
    return 0 if result["status"] == "PC_RESEARCH_HANDOFF_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
