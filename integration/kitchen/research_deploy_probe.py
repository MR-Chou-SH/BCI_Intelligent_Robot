"""Bounded source-delivery verification; no actions, reset, step, or hardware."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
PACKAGES = ("mujoco", "numpy", "h5py", "absl-py", "etils", "fsspec", "glfw",
            "PyOpenGL", "typing_extensions", "zipp")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(compile_model=False):
    result = {"python": platform.python_version(), "python_full": sys.version,
              "python_executable_provenance_only": sys.executable,
              "platform": platform.platform(), "machine": platform.machine(),
              "packages": {name: importlib.metadata.version(name) for name in PACKAGES}}
    if compile_model:
        import h5py
        import mujoco
        from robot_arm.kitchen.model import build_scene
        from robot_arm.kitchen.registry import control_identity
        from robot_arm.kitchen.scene_identity import model_identity
        scene = build_scene(layout_id="kitchen_v3_norice_candidate_013")
        result["model_identity"] = model_identity(scene, builder_sha256=scene.builder_sha256)
        result["control_identity"] = control_identity()
        result["native_mujoco"] = mujoco.mj_versionString()
        result["native_hdf5"] = h5py.version.hdf5_version
        result["cameras"] = [{"name": scene.model.camera(i).name,
                              "fovy_degrees": float(scene.model.cam_fovy[i])}
                             for i in range(scene.model.ncam)]
        result["model_dimensions"] = {name: int(getattr(scene.model, name))
                                      for name in ("nq", "nv", "nbody", "ngeom", "ncam")}
    return result


def safe_file(root, relative):
    rel = PurePosixPath(relative)
    if not relative or rel.is_absolute() or ".." in rel.parts or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe package path: " + relative)
    path = root.joinpath(*rel.parts)
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Package links are prohibited: " + relative)
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Missing or external package file: " + relative)
    return path


def verify(root):
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8-sig"))
    if manifest.get("schema") != "kitchen_research_delivery_v1":
        raise ValueError("Unsupported manifest")
    expected = set()
    for item in manifest["files"]:
        name = item["path"]
        if name in expected or name == "MANIFEST.json":
            raise ValueError("Duplicate or self-referential manifest entry")
        path = safe_file(root, name)
        if path.stat().st_size != item["bytes"] or sha256(path) != item["sha256"]:
            raise ValueError("Manifest mismatch: " + name)
        if path.suffix == ".py":
            compile(path.read_bytes(), str(path), "exec")
        expected.add(name)
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
              and not path.relative_to(root).parts[0] == "experiments"}
    if actual != expected | {"MANIFEST.json"}:
        raise ValueError("Unmanifested or missing package files: " + str(actual ^ (expected | {"MANIFEST.json"})))
    xml = ET.parse(safe_file(root, "robot_arm/assets/fr3_umi_merged.xml")).getroot()
    compiler = xml.find("compiler")
    references = []
    for element in xml.iter():
        if element.tag == "include":
            raise ValueError("Unexpected MJCF include")
        value = element.get("file")
        if value is None:
            continue
        if element.tag not in ("texture", "mesh"):
            raise ValueError("Unsupported MJCF dependency: " + element.tag)
        subdir = compiler.get("texturedir" if element.tag == "texture" else "meshdir", "")
        for component in (subdir, value):
            if PurePosixPath(component).is_absolute() or ".." in PurePosixPath(component).parts or ":" in component or "\\" in component:
                raise ValueError("External MJCF dependency")
        name = (PurePosixPath("robot_arm/assets") / subdir / value).as_posix()
        safe_file(root, name)
        if name not in expected:
            raise ValueError("MJCF asset absent from manifest: " + name)
        references.append(name)
    return {"status": "MANIFEST_HASHES_AND_PYTHON_COMPILE_PASS", "files": len(expected),
            "mjcf_asset_references": len(references), "manifest_sha256": sha256(root / "MANIFEST.json")}


def require(reply):
    if not reply.get("ok"):
        raise RuntimeError(str(reply))
    return reply["result"]


def real_entry(root, powershell_exe):
    from integration.kitchen.planner_client import PlannerClient

    if Path(sys.modules[PlannerClient.__module__].__file__).resolve().parents[2] != root:
        raise RuntimeError("PlannerClient imported outside extracted package")
    run = root / "experiments/kitchen_platform_v3" / ("deploy_probe_" + uuid.uuid4().hex)
    run.mkdir(parents=True, exist_ok=False)
    ready_path = run / "ready.json"
    command = [powershell_exe, "-NoProfile", "-File", str(root / "scripts/start_kitchen_platform.ps1"),
               "-PythonExe", sys.executable, "-Layout", "kitchen_v3_norice_candidate_013",
               "-InitializationProfile", "research", "-Seed", "0", "-Mode", "manual",
               "-DataRoot", str(run / "data"), "-ReadyFile", str(ready_path),
               "-PlannerPort", "0", "-WorldPort", "0", "-BindHost", "127.0.0.1", "-RunSeconds", "12"]
    result = {"command": command, "data_root": str(run / "data"),
              "mutating_requests_sent": 0, "quest": "NOT_TESTED", "external_windows_pc": "NOT_TESTED"}
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with (run / "stdout.log").open("x", encoding="utf-8") as stdout, (run / "stderr.log").open("x", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, cwd=root, env=environment, stdout=stdout, stderr=stderr,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 100
            while not ready_path.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.1)
            if not ready_path.exists():
                raise RuntimeError("REAL_LAUNCHER_NO_READY; inspect " + str(run / "stderr.log"))
            ready = json.loads(ready_path.read_text(encoding="utf-8"))
            if (ready["status"] != "PLATFORM_READY" or Path(ready["import_root"]).resolve() != root
                    or ready["initialization_profile"] != "research" or ready["seed"] != 0
                    or Path(ready["data_root"]).resolve() != (run / "data").resolve()
                    or ready["planner_host"] != "127.0.0.1" or ready["mode"] != "manual"
                    or not 0 < ready["planner_port"] < 65536 or not 0 < ready["world_port"] < 65536
                    or ready["planner_port"] == ready["world_port"]):
                raise ValueError("Real launcher parameter pass-through mismatch")
            with PlannerClient(port=ready["planner_port"], timeout=5) as client:
                observed = require(client.call("observe"))
                manifest = require(client.call("get_scene_manifest"))
                objects = require(client.call("query_objects", {"ids": ["apple", "burger"]}))
                execution = require(client.call("get_execution_status"))
                world_rpc = require(client.call("get_world_state"))
                with socket.create_connection(("127.0.0.1", ready["world_port"]), timeout=5) as connection:
                    with connection.makefile("rb") as reader:
                        frames = [json.loads(reader.readline(2 * 1024 * 1024)) for _ in range(2)]
                after = require(client.call("observe"))
            if observed["sim_tick"] != 0 or after["sim_tick"] != 0 or observed["episode"] != ready["episode"]:
                raise ValueError("Read-only probe advanced or changed the episode")
            if (any(frame["episode"] != observed["episode"] or frame["simTick"] != 0 for frame in frames)
                    or frames[1]["sequence"] <= frames[0]["sequence"]
                    or world_rpc["episode"] != observed["episode"]):
                raise ValueError("Planner/world episode or sequence mismatch")
            expected = json.loads((root / "ENVIRONMENT.json").read_text(encoding="utf-8"))
            if (manifest["scene_hash"] != expected["model_identity"]["scene_sha256"]
                    or manifest["control_identity"]["control_hash"] != expected["control_identity"]["control_hash"]):
                raise ValueError("Relocated scene/control identity differs from source")
            result.update(status="PC_RELOCATED_REAL_LAUNCHER_READ_ONLY_PASS", ready=ready,
                          observation_keys=sorted(observed), scene_manifest=manifest,
                          objects=objects, execution=execution, world_frame_count=len(frames),
                          world_body_count=len(frames[-1]["bodies"]), final_tick=after["sim_tick"],
                          source_scene_and_control_identity_match=True,
                          rgb_in_observe=any(key in observed for key in ("rgb", "images", "camera_poses", "camera_intrinsics")))
            return_code = process.wait(timeout=20)
            result["launcher_exit_code"] = return_code
            if return_code:
                raise RuntimeError("Real launcher exited nonzero: " + str(return_code))
        finally:
            if process.poll() is None:
                # Only this probe's fresh process tree, never another service.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10, check=False)
                else:
                    process.terminate()
                process.wait(timeout=10)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", action="store_true")
    parser.add_argument("--compile-model", action="store_true")
    parser.add_argument("--run", action="store_true", help="Run the real launcher, with read-only RPCs only")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--powershell-exe", default=shutil.which("pwsh") or shutil.which("powershell"))
    args = parser.parse_args()
    if args.report and args.report.exists():
        parser.error("Report must be a fresh path")
    if args.run and (not args.powershell_exe or not Path(args.powershell_exe).is_file()):
        parser.error("Windows PowerShell 5.1 or PowerShell 7 executable is required")
    result = {"status": "FAIL", "scope": "deployment maintenance, not a research experiment"}
    try:
        if args.inventory:
            result = inventory(args.compile_model)
        else:
            result = verify(ROOT)
            if args.run:
                help_run = subprocess.run([sys.executable, "-B", "-m", "integration.kitchen.platform_server", "--help"],
                                          cwd=ROOT, capture_output=True, text=True, timeout=30, check=True)
                result["real_core_help"] = help_run.stdout
                result["target_environment"] = inventory()
                source_environment = json.loads((ROOT / "ENVIRONMENT.json").read_text(encoding="utf-8"))
                for field in ("python", "machine", "packages"):
                    if result["target_environment"][field] != source_environment[field]:
                        raise RuntimeError("Target environment differs from captured source: " + field)
                result["real_entry"] = real_entry(ROOT, args.powershell_exe)
                result["status"] = "PC_RELOCATION_PASS_EXTERNAL_PC_AND_QUEST_NOT_TESTED"
    except Exception as error:
        result.update(status="FAIL", error=repr(error))
        raise
    finally:
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False, allow_nan=False)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
