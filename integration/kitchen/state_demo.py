"""Bounded PC-only API recording demo; small dual-arm motion, NOT a kitchen task.

Launches its own loopback service, records initial/object state and trajectories,
verifies raw-control re-simulation, then closes only its own service. No images.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import time

from .launch import ROOT, run_path
from .planner_client import PlannerClient
from .platform_research_handoff_example import Rpc
from .replay import verify


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)


def run(output, seed=0, frames=200, reference=None, diagnostics=False):
    output = run_path(output)
    output.mkdir(parents=True, exist_ok=False)
    ready = output / "ready.json"
    report = {"status": "FAIL", "scope": "small_motion_record_replay_smoke_not_kitchen_task",
              "task_success": "NOT_VERIFIED", "images_included": False}
    command = [sys.executable, "-B", "-m", "integration.kitchen.launch",
               "--data-root", str(output / "data"), "--ready-file", str(ready),
               "--seed", str(seed), "--planner-port", "0", "--world-port", "0", "--run-seconds", "900" if reference else "180"]
    calls = None
    if diagnostics:command.append("--diagnostics")
    if reference:
        if reference != "burger" or seed != 0:
            raise ValueError("Only a new, bounded burger reference test in fixed seed0 is supported")
        from .reference_trajectories import ASSETS, inspect
        report["source_reference"] = inspect(ASSETS)
        report["scope"] = "new_linux_public_reference_execution_record_replay_not_inherited_task_pass"
        report["source_reference_asset"] = "burger_public_calls.jsonl"
        report["source_reference_asset_sha256"] = hashlib.sha256((ASSETS / "burger_public_calls.jsonl").read_bytes()).hexdigest()
        calls = [json.loads(line) for line in (ASSETS / "burger_public_calls.jsonl").read_text().splitlines()]
        command.extend(["--initialization-profile", "fixed"])
    with (output / "stdout.log").open("x") as stdout, (output / "stderr.log").open("x") as stderr:
        process = subprocess.Popen(command, cwd=ROOT, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + 60
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.1)
            if not ready.exists() or process.poll() is not None:
                raise RuntimeError("Native service did not become ready; inspect stderr.log")
            configuration = json.loads(ready.read_text())
            with PlannerClient(port=configuration["planner_port"], timeout=120) as client:
                rpc = Rpc(client, output,diagnostics=diagnostics)
                if calls:
                    if calls[0]["method"] != "reset" or any(row["method"] == "reset" for row in calls[1:]):
                        raise ValueError("Reference must have exactly one initial reset")
                    rpc.call("reset", calls[0]["args"], True)
                if diagnostics:
                    save(output / "initial_state.json",rpc.call("get_replay_snapshot"))
                recording = rpc.call("start_state_recording", mutating=True)
                report["trajectory"] = recording["path"]
                try:
                    if calls:
                        started = time.monotonic()
                        for index, row in enumerate(calls[1:], 1):
                            if time.monotonic() - started > 780:
                                raise TimeoutError("Bounded reference execution wall time exceeded")
                            result = rpc.call(row["method"], row["args"], True)
                            state = rpc.call("observe") if row["method"] != "step" else result
                            if state["sim_tick"] != row["end_tick"]:
                                raise RuntimeError("New reference execution clock diverged")
                            if row["method"] != "hold" and (state["stopped"] or
                                    state["execution"] and state["execution"]["status"] == "FAILED"):
                                raise RuntimeError("New reference execution stopped: " + str(state["stop_reason"]))
                            if index % 50 == 0:
                                print(json.dumps({"reference_calls": index, "sim_tick": state["sim_tick"]}), flush=True)
                        report["source_calls_executed"] = len(calls)
                        report["feedback"] = rpc.call("get_task_feedback", {"object_ids": ["burger_container", "burger"]})
                    else:
                        small_motion(rpc, output, frames)
                finally:
                    rpc.call("hold", mutating=True)
                    report["recording"] = rpc.call("stop_state_recording", mutating=True)
            report["verification"] = verify(recording["path"])
            if report["verification"]["status"] != "CONTROL_RESIMULATION_PASS":
                raise RuntimeError("Control replay diverged")
            report["status"] = "PC_REFERENCE_RECORD_REPLAY_PASS" if calls else "PC_STATE_RECORD_REPLAY_SMOKE_PASS"
        except Exception as error:
            report["error"] = repr(error)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            report["owned_service_exit_code"] = process.returncode
            save(output / "report.json", report)
    return report


def small_motion(rpc, output, frames):
    # Uncommanded settling/wait steps are part of the recording.
    rpc.call("step", {"n_substeps": 100}, True)
    q = rpc.call("observe")["accepted_target16"]
    waypoints = []
    for frame in range(frames):
        target = list(q)
        phase = 2 * math.pi * (frame + 1) / frames
        target[0] += .08 * math.sin(phase)
        target[8] -= .06 * math.sin(phase)
        waypoints.append(target)
    rpc.call("execute_trajectory", {"waypoints": waypoints, "source": "programmatic_validation"}, True)
    for frame in range(frames):
        state = rpc.call("step", {"n_substeps": 20}, True)
        if state["stopped"] or state["execution"]["status"] == "FAILED":
            raise RuntimeError("Real motion stopped: " + str(state["stop_reason"]))
        if (frame + 1) % 50 == 0:
            print(json.dumps({"frames": frame + 1, "sim_tick": state["sim_tick"]}), flush=True)
    rpc.call("step", {"n_substeps": 100}, True)
    rpc.call("hold", mutating=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--frames", type=int, default=200)
    parser.add_argument("--reference", choices=("burger",), help="New fixed-seed0 execution of sanitized public reference RPCs")
    parser.add_argument("--diagnostics",action="store_true")
    args = parser.parse_args()
    if not 0 <= args.seed < 2**32 or not 100 <= args.frames <= 500:
        parser.error("seed must be uint32 and frames 100..500")
    print(json.dumps(run(args.output,args.seed,args.frames,args.reference,args.diagnostics),indent=2))


if __name__ == "__main__":
    main()
