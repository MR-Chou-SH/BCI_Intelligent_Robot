"""One-command post-Build-And-Run verification for M13.6."""

import argparse
import json
from pathlib import Path
import time

from integration.m13_6_apk_verifier import DEFAULT_APK, verify_apk
from integration.m13_6_usb_visual_acceptance import (
    DEFAULT_ADB,
    PACKAGE,
    QUEST_VISUAL_PORT,
    _adb_path,
    _ensure_app,
    _forward,
    _quest_logs,
    _run_adb,
    _send_synthetic,
    _socket_state,
)


ROOT = Path(__file__).resolve().parents[1]


def run(args):
    apk = verify_apk(args.apk)
    adb = _adb_path(args.adb)
    adb_state = _run_adb(adb, "get-state", check=False).stdout.strip()
    device = adb_state == "device"
    runtime = {"adbState": adb_state, "package": PACKAGE, "pid": None, "forward": [], "socket": {}, "logs": {}}
    sender = None
    if device:
        runtime["pid"] = _ensure_app(adb)
        runtime["installedPackagePath"] = _run_adb(adb, "shell", "pm", "path", PACKAGE, check=False).stdout.strip()
        if not args.skip_forward:
            _, runtime["forward"] = _forward(adb, args.forward_port, QUEST_VISUAL_PORT)
        runtime["socket"] = _socket_state(adb, QUEST_VISUAL_PORT)
        if args.probe and runtime["socket"].get("tcpListen"):
            sender = _send_synthetic(args.forward_port, args.duration_seconds, args.rate_hz, args.start_sequence, host="127.0.0.1")
        time.sleep(max(0.0, args.wait_seconds))
        runtime["logs"] = _quest_logs(adb)

    diagnostics = bool(runtime["logs"].get("diagnostics"))
    bindings = bool(
        runtime["logs"].get("bindings")
        or runtime["logs"].get("sceneBindings")
        or runtime["logs"].get("bindingDiagnostics")
    )
    listener = bool(runtime["socket"].get("tcpListen"))
    runtime["sender"] = sender
    result = {
        "schemaVersion": 1,
        "recordType": "m13_6_post_build_verify",
        "apkVerification": apk,
        "runtime": runtime,
        "checks": {
            "adbDevice": device,
            "packageProcess": bool(runtime["pid"]),
            "packageInstalled": bool(runtime.get("installedPackagePath")),
            "tcp11002Listener": listener,
            "sceneBindingsLog": bindings,
            "bindingEvidenceSource": runtime["logs"].get("bindingEvidenceSource"),
            "periodicDiagnosticsLog": diagnostics,
            "diagnosticSourceNotStale": apk.get("status") == "PASS",
        },
        "status": "PASS" if device and runtime["pid"] and runtime.get("installedPackagePath") and listener and bindings and diagnostics and apk.get("status") == "PASS" else ("STALE_SOURCE_BUILD" if apk.get("status") == "STALE_SOURCE_BUILD" else "BLOCKED_POST_BUILD_CHECK"),
        "evidenceBoundary": {"nd8Operated": False, "com11Opened": False, "newEegCollected": False, "questVisualJudged": False},
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output), "diagnostics": diagnostics, "bindings": bindings}, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", default=str(DEFAULT_APK))
    parser.add_argument("--adb")
    parser.add_argument("--forward-port", type=int, default=21002)
    parser.add_argument("--skip-forward", action="store_true")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--duration-seconds", type=float, default=3.0)
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--start-sequence", type=int, default=200000)
    parser.add_argument("--wait-seconds", type=float, default=7.0)
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_post_build_verify.json"))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
