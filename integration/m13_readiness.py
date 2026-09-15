"""PC-side M13 software/readiness checks; never opens hardware or Quest."""

import argparse
import importlib
import json
import socket
import sys
from pathlib import Path

from integration.m13_dynamic_stopping import (
    M13_FUSED_EVIDENCE_THRESHOLD,
    M13_MARGIN_THRESHOLD,
    M13_REQUIRED_CONSECUTIVE,
)
from integration.m13_trajectory_fixture import fixture_candidates


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_REPLAY_PATH = Path(r"D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json")
M8_PORT = 11001
REQUIRED_FILES = (
    "eeg/decoder/characterization.py",
    "eeg/decoder/pseudo_online.py",
    "integration/m11_context_prediction.py",
    "integration/m12_context_eeg_fusion.py",
    "integration/m13_dynamic_stopping.py",
    "integration/m13_m8_selection_integration.py",
    "integration/m8_selection_orchestration.py",
)


def _module_check(module_name):
    try:
        importlib.import_module(module_name)
    except Exception as error:  # readiness evidence, never hardware initialization
        return {"module": module_name, "ready": False, "detail": type(error).__name__ + ": " + str(error)}
    return {"module": module_name, "ready": True}


def _port_available(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError as error:
        return False
    finally:
        sock.close()


def run_readiness():
    modules = [_module_check(name) for name in (
        "eeg.decoder.fbcca",
        "eeg.decoder.pseudo_online",
        "integration.m11_context_prediction",
        "integration.m12_context_eeg_fusion",
        "integration.m13_dynamic_stopping",
        "integration.m13_m8_selection_integration",
    )]
    required_files = {path: (REPOSITORY_ROOT / path).is_file() for path in REQUIRED_FILES}
    mapping_ready = False
    mapping_detail = None
    try:
        candidates = fixture_candidates()
        mapping_ready = len(candidates) == 3 and tuple(item.slot_index for item in candidates) == (0, 1, 2)
    except Exception as error:
        mapping_detail = type(error).__name__ + ": " + str(error)
    try:
        importlib.import_module("eeg.acquisition.nd8_serial_adapter")
        nd8_adapter = {"importReady": True, "hardwareOpened": False, "detail": "module import only; no adapter instance and no COM11 access"}
    except Exception as error:
        nd8_adapter = {"importReady": False, "hardwareOpened": False, "detail": type(error).__name__ + ": " + str(error)}
    checks = {
        "python": {"executable": sys.executable, "version": sys.version.split()[0]},
        "modules": modules,
        "requiredFiles": required_files,
        "m13Defaults": {"fusedThreshold": M13_FUSED_EVIDENCE_THRESHOLD, "marginThreshold": M13_MARGIN_THRESHOLD, "requiredConsecutive": M13_REQUIRED_CONSECUTIVE, "frozen": (M13_FUSED_EVIDENCE_THRESHOLD == 0.70 and M13_MARGIN_THRESHOLD == 0.20 and M13_REQUIRED_CONSECUTIVE == 2)},
        "explicitSlotTargetLogicalMapping": {"ready": mapping_ready, "detail": mapping_detail},
        "m8TcpPort": {"port": M8_PORT, "availableForFutureListener": _port_available(M8_PORT), "probe": "local bind/close only; no Quest connection"},
        "historicalReplay": {"path": str(HISTORICAL_REPLAY_PATH), "available": HISTORICAL_REPLAY_PATH.is_file(), "optional": True},
        "nd8Adapter": nd8_adapter,
    }
    software_checks = all(item["ready"] for item in modules) and all(required_files.values()) and checks["m13Defaults"]["frozen"] and mapping_ready and checks["m8TcpPort"]["availableForFutureListener"]
    checks["status"] = "PASS" if software_checks else "FAIL"
    checks["overallStatus"] = checks["status"]
    checks["hardwareBoundary"] = {"questOperated": False, "nd8Operated": False, "com11Opened": False, "physicalRobotOperated": False}
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary-path", required=True)
    args = parser.parse_args(argv)
    summary = run_readiness()
    path = Path(args.summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "historicalAvailable": summary["historicalReplay"]["available"], "summaryPath": str(path)}, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
