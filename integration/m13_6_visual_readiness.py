"""PC-side readiness and static Quest receiver checks for M13.6."""

import argparse
import importlib.util
import json
from pathlib import Path
import socket

from integration.m13_6_visual_sync import (
    M13_6_MESSAGE_TYPE,
    M13_6_MAPPING_VERSION,
    M13_6_PROTOCOL_VERSION,
    M13_6_UDP_PORT,
)
from integration.m9_mujoco_execution import DEFAULT_M9_SCENE_BINDINGS


ROOT = Path(__file__).resolve().parents[1]
RECEIVER_PATH = ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncReceiver.cs"
INSTALLER_PATH = ROOT / "m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncAutoInstaller.cs"
CATALOG_PATH = ROOT / "m7_unity6000/Assets/Resources/BCI/M9/virtual_blocks.json"


def _check(name, passed, detail, status_if_false="BLOCKED"):
    return {"name": name, "status": "PASS" if passed else status_if_false, "detail": detail}


def _port_available(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def readiness_report():
    receiver_source = RECEIVER_PATH.read_text(encoding="utf-8") if RECEIVER_PATH.exists() else ""
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8") if INSTALLER_PATH.exists() else ""
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8")) if CATALOG_PATH.exists() else {}
    catalog_ids = {item.get("logicalBlockId") for item in catalog.get("blocks", [])}
    mapping_ids = set(DEFAULT_M9_SCENE_BINDINGS)
    checks = [
        _check("protocol_version", M13_6_PROTOCOL_VERSION == 1 and M13_6_MESSAGE_TYPE == "m13_6_robot_world_state", "M13.6 protocol v1 is defined"),
        _check("independent_udp_port", M13_6_UDP_PORT == 11002 and _port_available(M13_6_UDP_PORT), "UDP 11002 is available for the visual stream; TCP 11001 remains control-only"),
        _check("frozen_mapping", mapping_ids == {"block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"} and catalog_ids == mapping_ids, "logical block mapping and Quest catalog contain the same four logical IDs"),
        _check("unity_receiver_files", RECEIVER_PATH.is_file() and INSTALLER_PATH.is_file(), "Quest receiver and runtime installer files exist"),
        _check("unity_main_thread_update", "private void Update()" in receiver_source and "ApplyInterpolatedFrame" in receiver_source, "Transform writes are driven from Unity Update"),
        _check("unity_sequence_gate", "m_staleFrameCount" in receiver_source and "m_duplicateFrameCount" in receiver_source and "frame.sequence <= m_lastAcceptedSequence" in receiver_source, "stale and duplicate frames are rejected"),
        _check("unity_selection_isolation", "11001" not in receiver_source and "BciSelection" not in receiver_source and "BciSelection" not in installer_source, "visual receiver does not own M8 selection/control"),
        _check("identity_privacy_guard", "StartsWith(\"obj_\"" in receiver_source and M13_6_MAPPING_VERSION == "m9-virtual-block-logical-mapping-v1", "public state uses logical IDs and rejects internal object names"),
        _check("mujoco_import", importlib.util.find_spec("mujoco") is not None, "MuJoCo Python import is available in the selected environment", "WARNING"),
        _check("unity_project_static", (ROOT / "m7_unity6000/ProjectSettings/ProjectVersion.txt").is_file(), "Unity project is present; editor/device build not run here", "WARNING"),
    ]
    hard_failures = [item for item in checks if item["status"] == "BLOCKED"]
    return {
        "schemaVersion": 1,
        "recordType": "m13_6_visual_sync_readiness",
        "status": "PASS" if not hard_failures else "BLOCKED",
        "stream": {"protocolVersion": M13_6_PROTOCOL_VERSION, "messageType": M13_6_MESSAGE_TYPE, "udpPort": M13_6_UDP_PORT, "mappingVersion": M13_6_MAPPING_VERSION, "targetRateHz": "30-60", "selectionTcpPort": 11001},
        "checks": checks,
        "hardware": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "newEegCollected": False, "physicalRobotOperated": False},
        "limitations": ["Quest Unity compile/build and visual observation are not claimed", "UDP availability is a local PC bind check only", "table and block visual assets are not geometrically identical to the MuJoCo scene and require Quest observation/calibration"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    report = readiness_report()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


__all__ = ["readiness_report"]


if __name__ == "__main__":
    raise SystemExit(main())
