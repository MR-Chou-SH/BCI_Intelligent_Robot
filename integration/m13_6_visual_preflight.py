"""Future Wi-Fi preflight for the M13.6 Quest visual TCP stream.

This command is intentionally separate from the USB/ADB engineering runner.
It reports a warning when Quest wlan0 is unavailable, which is expected while
the device is being verified over USB.
"""

import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import shutil

from integration.m13_6_usb_visual_acceptance import (
    DEFAULT_ADB,
    PACKAGE,
    QUEST_VISUAL_PORT,
    _adb_path,
    _run_adb,
    _socket_state,
)


ROOT = Path(__file__).resolve().parents[1]


def _pc_ipv4_candidates():
    output = subprocess.run(["ipconfig"], capture_output=True, text=True, check=False).stdout
    values = []
    for value in re.findall(r"IPv4 Address[^:]*:\s*([0-9.]+)", output, re.IGNORECASE):
        if value.startswith(("127.", "169.254.")):
            continue
        try:
            ipaddress.ip_address(value)
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return values


def _quest_wlan_ipv4(adb):
    output = _run_adb(adb, "shell", "ip", "-4", "addr", "show", "wlan0", check=False).stdout
    match = re.search(r"inet\s+([0-9.]+)/([0-9]+)", output)
    return (match.group(1), int(match.group(2))) if match else None


def _same_subnet(pc_ip, quest_ip, prefix_length):
    try:
        return ipaddress.ip_address(pc_ip) in ipaddress.ip_network(
            "{}/{}".format(quest_ip, prefix_length), strict=False
        )
    except ValueError:
        return False


def _adb_ping(adb, pc_ip):
    result = _run_adb(adb, "shell", "ping", "-c", "1", "-W", "1", pc_ip, check=False)
    return result.returncode == 0


def report(adb_value=None, probe=False):
    adb = _adb_path(adb_value)
    adb_state = _run_adb(adb, "get-state", check=False).stdout.strip()
    pc_ips = _pc_ipv4_candidates()
    quest_wlan = _quest_wlan_ipv4(adb) if adb_state == "device" else None
    quest_ip, quest_prefix = quest_wlan or (None, None)
    matching_pc = next(
        (ip for ip in pc_ips if quest_ip and _same_subnet(ip, quest_ip, quest_prefix)),
        None,
    )
    pid = _run_adb(adb, "shell", "pidof", PACKAGE, check=False).stdout.strip() if adb_state == "device" else ""
    sockets = _socket_state(adb, QUEST_VISUAL_PORT) if adb_state == "device" else {
        "tcpListen": False,
        "udpBound": False,
    }
    checks = [
        {
            "name": "adb_device",
            "status": "PASS" if adb_state == "device" else "BLOCKED",
            "detail": adb_state or "Quest is not in ADB device state",
        },
        {
            "name": "pc_ipv4",
            "status": "PASS" if pc_ips else "WARNING",
            "detail": pc_ips or "No non-loopback IPv4 detected",
        },
        {
            "name": "quest_wlan0_ipv4",
            "status": "PASS" if quest_wlan else "WARNING",
            "detail": quest_wlan or "Quest wlan0 is unavailable; USB/ADB mode remains usable",
        },
        {
            "name": "same_subnet",
            "status": "PASS" if matching_pc else "WARNING",
            "detail": {"pc": matching_pc, "quest": quest_ip, "prefixLength": quest_prefix},
        },
        {
            "name": "quest_app",
            "status": "PASS" if pid else "WARNING",
            "detail": pid or "Quest app is not running",
        },
        {
            "name": "quest_tcp_11002_listener",
            "status": "PASS" if sockets["tcpListen"] else "WARNING",
            "detail": sockets,
        },
    ]
    if probe and matching_pc and quest_ip:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(1.0)
        try:
            client.connect((quest_ip, QUEST_VISUAL_PORT))
            probe_status = "PASS"
            probe_detail = "TCP connection established"
        except OSError as error:
            probe_status = "WARNING"
            probe_detail = str(error)
        finally:
            client.close()
        checks.append({"name": "pc_to_quest_tcp_11002", "status": probe_status, "detail": probe_detail})

    blocked = [item for item in checks if item["status"] == "BLOCKED"]
    return {
        "schemaVersion": 1,
        "recordType": "m13_6_visual_wifi_preflight",
        "status": "BLOCKED" if blocked else ("READY" if all(item["status"] == "PASS" for item in checks) else "READY_WITH_WARNINGS"),
        "pcIpv4Candidates": pc_ips,
        "questWlan0": {"ip": quest_ip, "prefixLength": quest_prefix},
        "questTcpPort": QUEST_VISUAL_PORT,
        "m8ControlPortUntouched": 11001,
        "checks": checks,
        "hardwareBoundary": {
            "nd8Operated": False,
            "com11Opened": False,
            "newEegCollected": False,
            "physicalRobotOperated": False,
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb")
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_visual_wifi_preflight.json"))
    args = parser.parse_args(argv)
    result = report(args.adb, args.probe)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(output)}, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
