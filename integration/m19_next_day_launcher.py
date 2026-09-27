"""Safe preflight and dual-runtime launch entry points for M19 Demo/Research."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENDOR_PYTHON = Path(
    r"C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe"
)
DEFAULT_QUEST_PORT = 11001
DEFAULT_TELEMETRY_PORT = 11002
DEFAULT_MUJOCO_RPC_PORT = 11003


def _runtime_probe(executable: Path, code: str) -> dict:
    try:
        completed = subprocess.run(
            [str(executable), "-B", "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "FAIL", "error": "{}: {}".format(type(error).__name__, error)}
    if completed.returncode != 0:
        return {"status": "FAIL", "error": (completed.stderr or completed.stdout).strip()}
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as error:
        return {"status": "FAIL", "error": "runtime probe returned invalid JSON: {}".format(error)}
    payload["status"] = "PASS"
    return payload


def _port_available(host: str, port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def _output_root_checks(root: Path | None) -> dict:
    if root is None:
        return {"requested": False, "unique": True, "parentExists": True, "parentWritable": True}
    root = Path(root).resolve()
    parent = root.parent
    return {
        "requested": True,
        "path": str(root),
        "unique": not root.exists(),
        "parentExists": parent.is_dir(),
        "parentWritable": parent.is_dir() and os.access(str(parent), os.W_OK),
    }


def collect_preflight(
    *,
    vendor_python: Path,
    project_python: Path,
    com_port: str,
    quest_host: str,
    listen_host: str,
    quest_port: int,
    telemetry_port: int,
    mujoco_rpc_port: int,
    demo_output_root: Path | None = None,
    research_output_root: Path | None = None,
) -> dict:
    vendor_code = (
        "import json, sys; "
        "from integration.m8_live_nd8 import validate_vendor_cpython39_runtime; "
        "runtime=validate_vendor_cpython39_runtime(); "
        "from integration.m19_research_acquisition import build_plan, validate_plan; "
        "plan=build_plan(190019); validate_plan(plan); "
        "import integration.m19_paged_live_eeg_demo; "
        "print(json.dumps({'python':sys.version.split()[0], 'executable':sys.executable, "
        "'sdkModules':runtime['vendorSdkImports'], 'researchPlanFingerprint':plan['scheduleFingerprint']}))"
    )
    project_code = (
        "import json, sys, mujoco; "
        "from integration.m9_mujoco_execution import create_fr3_umi_mujoco_adapter, "
        "SceneBindingRegistry, DEFAULT_M9_SCENE_BINDINGS; "
        "adapter=create_fr3_umi_mujoco_adapter(scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS)); "
        "print(json.dumps({'python':sys.version.split()[0], 'executable':sys.executable, "
        "'mujoco':mujoco.__version__, 'adapterConstructed':adapter is not None}))"
    )
    vendor = _runtime_probe(vendor_python, vendor_code)
    project = _runtime_probe(project_python, project_code)
    demo_root = _output_root_checks(demo_output_root)
    research_root = _output_root_checks(research_output_root)
    checks = {
        "vendorPythonAndNeurodanceReady": vendor.get("status") == "PASS",
        "researchImportsAndFrozenPlanReady": vendor.get("status") == "PASS",
        "projectPythonAndMuJoCoAdapterReady": project.get("status") == "PASS",
        "comPortSyntaxValid": bool(re.fullmatch(r"COM[1-9][0-9]*", str(com_port).upper())),
        "questHostConfigured": isinstance(quest_host, str) and bool(quest_host.strip()),
        "questListenerPortAvailable": _port_available(listen_host, quest_port),
        "mujocoRpcPortAvailable": _port_available("127.0.0.1", mujoco_rpc_port),
        "telemetryPortValid": 1 <= int(telemetry_port) <= 65535,
        "demoOutputRootReady": demo_root["unique"] and demo_root["parentExists"] and demo_root["parentWritable"],
        "researchOutputRootReady": research_root["unique"] and research_root["parentExists"] and research_root["parentWritable"],
        "formalResearchCaptureIsFrozenFourSeconds": True,
        "noHardwareOpened": True,
        "noQuestBuildStarted": True,
        "noPhysicalRobotOperation": True,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL_CLOSED",
        "checks": checks,
        "runtimes": {"vendor": vendor, "project": project},
        "network": {
            "questListener": {"host": listen_host, "port": int(quest_port)},
            "questTelemetryDestinationPort": int(telemetry_port),
            "mujocoRpc": {"host": "127.0.0.1", "port": int(mujoco_rpc_port)},
        },
        "outputs": {"demo": demo_root, "research": research_root},
        "hardwareBoundary": {
            "COMOpened": False,
            "nd8Operated": False,
            "questBuilt": False,
            "humanEegCollected": False,
            "physicalRobotOperated": False,
        },
    }


def _run_research(args) -> int:
    if not args.confirm_live_human:
        raise ValueError("live Research launch requires --confirm-live-human")
    if not args.resume:
        preflight_command = [
            str(args.vendor_python), "-B", "-m", "integration.m19_research_acquisition", "preflight",
            "--source", "live-nd8", "--confirm-live-human", "--com", args.com_port,
            "--output-root", str(args.session_root), "--host", args.listen_host,
            "--port", str(args.quest_port), "--cue-backend", args.cue_backend,
        ]
        preflight = subprocess.run(preflight_command, cwd=str(ROOT), check=False)
        if preflight.returncode != 0:
            return preflight.returncode
    command = [
        str(args.vendor_python), "-B", "-m", "integration.m19_research_acquisition", "serve",
        "--session-root", str(args.session_root), "--session-id", args.session_id,
        "--seed", str(args.seed), "--source", "live-nd8", "--com", args.com_port,
        "--confirm-live-human", "--host", args.listen_host, "--port", str(args.quest_port),
        "--cue-backend", args.cue_backend, "--mode", args.mode,
    ]
    if args.resume:
        command.append("--resume")
    return subprocess.run(command, cwd=str(ROOT), check=False).returncode


def _run_demo(args) -> int:
    if not args.confirm_live_human:
        raise ValueError("live Demo launch requires --confirm-live-human")
    telemetry_host = args.telemetry_host or args.quest_host
    report = collect_preflight(
        vendor_python=args.vendor_python,
        project_python=args.project_python,
        com_port=args.com_port,
        quest_host=args.quest_host,
        listen_host=args.listen_host,
        quest_port=args.quest_port,
        telemetry_port=args.telemetry_port,
        mujoco_rpc_port=args.mujoco_rpc_port,
        demo_output_root=args.output_root,
    )
    if report["status"] != "PASS":
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 2

    args.output_root.mkdir(parents=False, exist_ok=False)
    from integration.m13_8_final_runtime_integration import LocalJsonlIpcServer
    from integration.m19_mujoco_rpc import M19MujocoRpcEndpoint
    from integration.m19_paged_live_eeg_demo import _create_mujoco_dispatcher
    from types import SimpleNamespace

    runtime_args = SimpleNamespace(
        operation=args.operation,
        telemetry_host=telemetry_host,
        telemetry_port=args.telemetry_port,
    )
    dispatcher, telemetry = _create_mujoco_dispatcher(runtime_args, None, args.output_root)
    server = LocalJsonlIpcServer(
        M19MujocoRpcEndpoint(dispatcher).handle,
        host="127.0.0.1",
        port=args.mujoco_rpc_port,
    )
    try:
        child_command = [
            str(args.vendor_python), "-B", "-m", "integration.m19_paged_live_eeg_demo",
            "--mode", "serve", "--eeg-source", "live-nd8", "--com", args.com_port,
            "--confirm-live-human", "--host", args.listen_host, "--port", str(args.quest_port),
            "--telemetry-host", telemetry_host, "--telemetry-port", str(args.telemetry_port),
            "--output-dir", str(args.output_root), "--event-log", str(args.output_root / "m19-events.jsonl"),
            "--m9-mode", "remote-mujoco", "--mujoco-rpc-host", "127.0.0.1",
            "--mujoco-rpc-port", str(server.address[1]), "--operation", args.operation,
        ]
        return subprocess.run(child_command, cwd=str(ROOT), check=False).returncode
    finally:
        server.close()
        telemetry.close()


def _common_launch_arguments(parser):
    parser.add_argument("--vendor-python", type=Path, default=DEFAULT_VENDOR_PYTHON)
    parser.add_argument("--com", dest="com_port", required=True)
    parser.add_argument("--confirm-live-human", action="store_true")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--quest-port", type=int, default=DEFAULT_QUEST_PORT)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="software-only checks; never opens COM or starts a Quest build")
    preflight.add_argument("--vendor-python", type=Path, default=DEFAULT_VENDOR_PYTHON)
    preflight.add_argument("--project-python", type=Path, default=Path(sys.executable))
    preflight.add_argument("--com", dest="com_port", required=True)
    preflight.add_argument("--quest-host", required=True, help="Quest IP used for telemetry")
    preflight.add_argument("--listen-host", default="0.0.0.0")
    preflight.add_argument("--quest-port", type=int, default=DEFAULT_QUEST_PORT)
    preflight.add_argument("--telemetry-port", type=int, default=DEFAULT_TELEMETRY_PORT)
    preflight.add_argument("--mujoco-rpc-port", type=int, default=DEFAULT_MUJOCO_RPC_PORT)
    preflight.add_argument("--demo-output-root", type=Path, required=True)
    preflight.add_argument("--research-output-root", type=Path, required=True)

    demo = sub.add_parser("demo", help="M19 live Demo: CPython 3.9 ND8 + CPython 3.12 MuJoCo worker")
    _common_launch_arguments(demo)
    demo.add_argument("--project-python", type=Path, default=Path(sys.executable))
    demo.add_argument("--quest-host", required=True, help="Quest IP that receives M13.6 telemetry")
    demo.add_argument("--telemetry-host", help="Quest telemetry IP; defaults to --quest-host")
    demo.add_argument("--telemetry-port", type=int, default=DEFAULT_TELEMETRY_PORT)
    demo.add_argument("--mujoco-rpc-port", type=int, default=DEFAULT_MUJOCO_RPC_PORT)
    demo.add_argument("--output-root", type=Path, required=True)
    demo.add_argument("--operation", choices=("pick", "pick_and_place"), default="pick_and_place")

    research = sub.add_parser("research", help="Phase-2 live Research acquisition in the Neurodance Python runtime")
    _common_launch_arguments(research)
    research.add_argument("--session-root", type=Path, required=True)
    research.add_argument("--session-id", required=True)
    research.add_argument("--seed", type=int, required=True)
    research.add_argument("--mode", choices=("research", "smoke-qc"), default="research")
    research.add_argument("--cue-backend", choices=("pc-winsound", "null"), default="pc-winsound")
    research.add_argument("--resume", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "preflight":
        report = collect_preflight(
            vendor_python=args.vendor_python,
            project_python=args.project_python,
            com_port=args.com_port,
            quest_host=args.quest_host,
            listen_host=args.listen_host,
            quest_port=args.quest_port,
            telemetry_port=args.telemetry_port,
            mujoco_rpc_port=args.mujoco_rpc_port,
            demo_output_root=args.demo_output_root,
            research_output_root=args.research_output_root,
        )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        if report["status"] == "PASS":
            print("M19 NEXT DAY PREFLIGHT PASS")
            return 0
        return 2
    if args.command == "demo":
        return _run_demo(args)
    return _run_research(args)


if __name__ == "__main__":
    raise SystemExit(main())
