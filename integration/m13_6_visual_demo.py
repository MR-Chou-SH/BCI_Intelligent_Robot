"""Small return-to-lab launcher for M13.6 visual acceptance.

USB mode uses the existing ADB forward. Wi-Fi mode discovers the Quest
address from wlan0 and verifies the same-subnet/TCP listener before sending.
``--human`` pauses between synthetic, single MuJoCo, and four-block phases so
the operator can observe the headset; it never declares visual acceptance.
"""

import argparse
import json
from pathlib import Path
import re
import time

from integration.m13_6_usb_visual_acceptance import (
    DEFAULT_ADB,
    PACKAGE,
    QUEST_VISUAL_PORT,
    _adb_path,
    _ensure_app,
    _forward,
    _run_mujoco,
    _quest_logs,
    _send_synthetic,
    _send_joint_diagnostic,
    _socket_state,
)
from integration.m13_6_visual_preflight import report as wifi_preflight


ROOT = Path(__file__).resolve().parents[1]
_SEQUENCE_RE = re.compile(r"(?:lastSeq|sequence)\s*[=:]\s*(-?\d+)")


def _choose_start_sequence(logs, requested=None, wall_clock_seed=None):
    """Avoid replaying a lower sequence into a still-running Quest receiver."""
    if requested is not None:
        return int(requested)
    observed = []
    for key in ("diagnostics", "frameAccepted", "allM136Lines"):
        for line in logs.get(key, []):
            observed.extend(int(match.group(1)) for match in _SEQUENCE_RE.finditer(line))
    # Keep the JSON integer comfortably small for Unity/IL2CPP while still
    # moving past the prior session in normal operation. Retained diagnostics
    # remain the stronger source when a receiver has a larger sequence.
    clock_seed = int(time.time()) if wall_clock_seed is None else int(wall_clock_seed)
    return max(clock_seed, max(observed, default=-1) + 1)


def _wifi_target(adb):
    result = wifi_preflight(str(adb), probe=True)
    same_subnet = next((item for item in result["checks"] if item["name"] == "same_subnet"), None)
    detail = (same_subnet or {}).get("detail", {})
    quest_ip = detail.get("quest") if isinstance(detail, dict) else None
    if result["status"] == "BLOCKED" or not quest_ip:
        raise RuntimeError("Wi-Fi preflight did not find a usable Quest wlan0 address")
    tcp_probe = next((item for item in result["checks"] if item["name"] == "pc_to_quest_tcp_11002"), None)
    if not tcp_probe or tcp_probe["status"] != "PASS":
        raise RuntimeError("PC to Quest TCP 11002 probe did not pass")
    return quest_ip, result


def run(args):
    adb = _adb_path(args.adb)
    pid = _ensure_app(adb)
    host = "127.0.0.1"
    port = args.forward_port
    transport = args.transport
    preflight = None
    if transport == "usb":
        forwarded, forwards = _forward(adb, args.forward_port, QUEST_VISUAL_PORT)
        if not forwarded:
            raise RuntimeError("ADB visual forward was not listed")
    else:
        host, preflight = _wifi_target(adb)
        port = QUEST_VISUAL_PORT
        forwards = []

    runs = []
    phase_observations = []
    initial_logs = _quest_logs(adb)
    sequence = _choose_start_sequence(initial_logs, args.start_sequence)
    initial_sequence = sequence
    single_phase_final_block_positions_mujoco = None
    single_phase_final_source_block_positions_mujoco = None

    def synthetic_phase():
        nonlocal sequence
        item = _send_synthetic(host=host, local_port=port, duration_seconds=args.duration_seconds, rate_hz=args.rate_hz, start_sequence=sequence)
        item["kind"] = "synthetic"
        runs.append(item)
        sequence = item["nextSequence"]

    def single_phase():
        nonlocal sequence, single_phase_final_block_positions_mujoco, single_phase_final_source_block_positions_mujoco
        item = _run_mujoco(
            "single", port, args.logical_block_id, args.rate_hz, args.max_steps, sequence,
            host=host, realtime=True,
            m13_6_visual_mode=True,
        )
        item["kind"] = "mujoco_single"
        runs.append(item)
        sequence = item["nextSequence"]
        single_phase_final_block_positions_mujoco = item["finalBlockPositionsMujoco"]
        single_phase_final_source_block_positions_mujoco = item["finalSourceBlockPositionsMujoco"]

    def sequential_phase():
        nonlocal sequence
        initial_block_positions_mujoco = None
        if single_phase_final_source_block_positions_mujoco and "block_sim_01" in single_phase_final_source_block_positions_mujoco:
            initial_block_positions_mujoco = {
                "block_sim_01": single_phase_final_source_block_positions_mujoco["block_sim_01"]
            }
        item = _run_mujoco(
            "sequential", port, args.logical_block_id, args.rate_hz, args.max_steps, sequence,
            host=host, realtime=True,
            m13_6_visual_mode=True,
            phase_anchor_positions_mujoco=single_phase_final_block_positions_mujoco,
            initial_block_positions_mujoco=initial_block_positions_mujoco,
        )
        item["kind"] = "mujoco_sequential"
        runs.append(item)
        sequence = item["nextSequence"]

    def joint_phase():
        nonlocal sequence
        item = _send_joint_diagnostic(host=host, local_port=port, duration_seconds=args.joint_duration_seconds, rate_hz=args.rate_hz, start_sequence=sequence)
        item["kind"] = "single_joint_diagnostic"
        runs.append(item)
        sequence = item["nextSequence"]

    phases = [args.phase] if args.phase != "all" else ["synthetic", "single", "sequential"]
    actions = {"synthetic": synthetic_phase, "joint": joint_phase, "single": single_phase, "sequential": sequential_phase}
    for index, phase in enumerate(phases):
        if args.human:
            input("请确认用户已准备好观察当前 phase，按 Enter 开始（动作将按真实时间播放）：")
        actions[phase]()
        time.sleep(0.5)
        phase_observations.append({
            "phase": phase,
            "run": runs[-1],
            "questSocketState": _socket_state(adb, QUEST_VISUAL_PORT),
            "questLogs": _quest_logs(adb),
        })
        if args.human and index < len(phases) - 1:
            input("请观察当前 Quest 画面并记录结果，按 Enter 进入下一 phase（不会自动判定视觉 PASS）：")

    result = {
        "schemaVersion": 1,
        "recordType": "m13_6_visual_demo_launcher",
        "transport": transport,
        "host": host,
        "port": port,
        "package": PACKAGE,
        "pid": pid,
        "forwardList": forwards,
        "preflight": preflight,
        "sequenceStart": initial_sequence,
        "runs": runs,
        "phaseObservations": phase_observations,
        "questSocketState": _socket_state(adb, QUEST_VISUAL_PORT),
        "visualAcceptance": "PENDING_USER_OBSERVATION",
        "nd8Operated": False,
        "com11Opened": False,
        "newEegCollected": False,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "transport": transport, "phase": args.phase, "status": "READY_FOR_USER_OBSERVATION"}, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("usb", "wifi"), default="usb")
    parser.add_argument("--phase", choices=("synthetic", "joint", "single", "sequential", "all"), default="all")
    parser.add_argument("--human", action="store_true")
    parser.add_argument("--adb")
    parser.add_argument("--forward-port", type=int, default=21002)
    parser.add_argument("--duration-seconds", type=float, default=15.0)
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--start-sequence", type=int, default=None, help="override the automatic monotonic sequence seed")
    parser.add_argument("--joint-duration-seconds", type=float, default=8.0)
    parser.add_argument("--logical-block-id", default="block_sim_01")
    parser.add_argument("--max-steps", type=int, default=9000)
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_visual_demo.json"))
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
