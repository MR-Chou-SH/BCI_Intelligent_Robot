"""USB/ADB engineering runner for the real M13.6 Quest visual receiver.

This is an engineering transport path only. It forwards PC localhost TCP to
the Quest visual receiver over USB and deliberately does not touch M8 TCP
11001, ND8, COM11, EEG acquisition, or the physical robot.
"""

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time

from integration.m13_6_visual_sync import (
    BlockWorldState,
    M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST,
    MujocoToQuestTransform,
    RobotWorldStateFrame,
    TcpLatestStateSender,
    run_mujoco_pick_place_stream,
    run_mujoco_sequential_stream,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ADB = Path(
    r"C:\Program Files\Unity\Hub\Editor\6000.0.66f2\Editor\Data\PlaybackEngines\AndroidPlayer\SDK\platform-tools\adb.exe"
)
PACKAGE = "com.samples.passthroughcamera"
QUEST_VISUAL_PORT = 11002
DEFAULT_FORWARD_PORT = 21002
_TCP_LISTEN_STATE = "0A"


def _adb_path(value=None):
    if value:
        return Path(value)
    if DEFAULT_ADB.is_file():
        return DEFAULT_ADB
    discovered = shutil.which("adb")
    if discovered:
        return Path(discovered)
    raise FileNotFoundError("adb.exe was not found")


def _run_adb(adb, *args, timeout=20, check=True):
    result = subprocess.run(
        [str(adb), *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            "ADB command failed ({}): {}\n{}".format(
                result.returncode, " ".join(str(arg) for arg in args), result.stderr.strip()
            )
        )
    return result


def _ensure_device(adb):
    result = _run_adb(adb, "get-state")
    if result.stdout.strip() != "device":
        raise RuntimeError("Quest ADB state is not device: {}".format(result.stdout.strip()))
    details = _run_adb(adb, "devices", "-l").stdout.strip()
    return details


def _ensure_app(adb):
    pid = _run_adb(adb, "shell", "pidof", PACKAGE, check=False).stdout.strip()
    if not pid:
        _run_adb(adb, "shell", "monkey", "-p", PACKAGE, "1")
        time.sleep(2.0)
        pid = _run_adb(adb, "shell", "pidof", PACKAGE, check=False).stdout.strip()
    if not pid:
        raise RuntimeError("Quest app did not obtain a running PID")
    return pid.split()[0]


def _forward(adb, local_port, quest_port):
    _run_adb(adb, "forward", "--remove", "tcp:{}".format(local_port), check=False)
    _run_adb(adb, "forward", "tcp:{}".format(local_port), "tcp:{}".format(quest_port))
    forwards = _run_adb(adb, "forward", "--list").stdout.splitlines()
    expected = "tcp:{} tcp:{}".format(local_port, quest_port)
    return any(expected in line for line in forwards), forwards


def _socket_state(adb, port):
    port_hex = "{:04X}".format(int(port))
    tcp = _run_adb(adb, "shell", "cat", "/proc/net/tcp", check=False).stdout
    udp = _run_adb(adb, "shell", "cat", "/proc/net/udp", check=False).stdout
    return {
        "tcpListen": any(
            re.search(r"00000000:{}\s+00000000:0000\s+{}\b".format(port_hex, _TCP_LISTEN_STATE), line)
            for line in tcp.splitlines()
        ),
        "udpBound": any("00000000:{}".format(port_hex) in line for line in udp.splitlines()),
    }


def _synthetic_frame(sequence, elapsed, state="usb_synthetic"):
    phase = float(elapsed) * 0.8
    joints = (0.08 * math.sin(phase), -0.25, 0.18, -1.7, 0.1 * math.sin(phase * 0.7), 1.65, 0.75)
    transform = MujocoToQuestTransform()
    blocks = tuple(
        BlockWorldState(
            "block_sim_0{}".format(index),
            transform.position_to_mujoco(M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST[index - 1]),
            (1.0, 0.0, 0.0, 0.0),
        )
        for index in range(1, 5)
    )
    return RobotWorldStateFrame(
        sequence=int(sequence),
        simulation_time_seconds=float(elapsed),
        joint_positions_radians=joints,
        left_finger_position_meters=0.018,
        right_finger_position_meters=0.018,
        gripper_opening_meters=0.036,
        robot_base_position_mujoco=(0.0, 0.0, 0.0),
        robot_base_quaternion_mujoco_wxyz=(1.0, 0.0, 0.0, 0.0),
        blocks=blocks,
        simulation_state=state,
        sent_timestamp_unix_seconds=time.time(),
    )


def _joint_diagnostic_q1(elapsed_seconds):
    """Slow, deliberately visible 0 -> + -> 0 -> - -> 0 q1 trajectory."""
    elapsed = max(0.0, min(8.0, float(elapsed_seconds)))
    if elapsed < 2.0:
        return 0.35 * (elapsed / 2.0)
    if elapsed < 3.0:
        return 0.35
    if elapsed < 5.0:
        return 0.35 * (1.0 - (elapsed - 3.0) / 2.0)
    if elapsed < 7.0:
        return -0.35 * ((elapsed - 5.0) / 2.0)
    return -0.35 * (1.0 - (elapsed - 7.0) / 1.0)


def _joint_diagnostic_frame(sequence, elapsed, state="usb_joint_diagnostic"):
    frame = _synthetic_frame(sequence, elapsed, state=state)
    q = [0.0, -0.25, 0.18, -1.7, 0.0, 1.65, 0.75]
    q[0] = _joint_diagnostic_q1(elapsed)
    return RobotWorldStateFrame(
        sequence=frame.sequence,
        simulation_time_seconds=frame.simulation_time_seconds,
        joint_positions_radians=tuple(q),
        left_finger_position_meters=frame.left_finger_position_meters,
        right_finger_position_meters=frame.right_finger_position_meters,
        gripper_opening_meters=frame.gripper_opening_meters,
        robot_base_position_mujoco=frame.robot_base_position_mujoco,
        robot_base_quaternion_mujoco_wxyz=frame.robot_base_quaternion_mujoco_wxyz,
        blocks=frame.blocks,
        simulation_state=frame.simulation_state,
        sent_timestamp_unix_seconds=frame.sent_timestamp_unix_seconds,
        protocol_version=frame.protocol_version,
        mapping_version=frame.mapping_version,
    )


def _send_synthetic(local_port, duration_seconds, rate_hz, start_sequence=0, host="127.0.0.1"):
    sender = TcpLatestStateSender(host, local_port)
    started = time.monotonic()
    sequence = int(start_sequence)
    send_calls = 0
    q1_values = []
    try:
        interval = 1.0 / float(rate_hz)
        next_send = started
        while time.monotonic() - started < float(duration_seconds):
            now = time.monotonic()
            if now < next_send:
                time.sleep(min(next_send - now, 0.01))
                continue
            elapsed = now - started
            frame = _synthetic_frame(sequence, elapsed)
            sender.send(frame)
            q1_values.append(frame.joint_positions_radians[0])
            send_calls += 1
            sequence += 1
            next_send += interval
        time.sleep(0.25)
    finally:
        sender.close()
    return {
        "sendCalls": send_calls,
        "senderSentCount": sender.sent_count,
        "sequenceStart": int(start_sequence),
        "nextSequence": sequence,
        "durationSeconds": float(duration_seconds),
        "configuredRateHz": float(rate_hz),
        "jointTargets": {"q1First": q1_values[0] if q1_values else None, "q1Last": q1_values[-1] if q1_values else None, "q1Min": min(q1_values) if q1_values else None, "q1Max": max(q1_values) if q1_values else None},
        "lastError": str(sender.last_error) if sender.last_error else None,
    }


def _send_joint_diagnostic(local_port, duration_seconds, rate_hz, start_sequence=0, host="127.0.0.1"):
    sender = TcpLatestStateSender(host, local_port)
    started = time.monotonic()
    sequence = int(start_sequence)
    q1_values = []
    send_calls = 0
    try:
        interval = 1.0 / float(rate_hz)
        next_send = started
        while time.monotonic() - started < float(duration_seconds):
            now = time.monotonic()
            if now < next_send:
                time.sleep(min(next_send - now, 0.01))
                continue
            elapsed = now - started
            frame = _joint_diagnostic_frame(sequence, elapsed)
            sender.send(frame)
            q1_values.append(frame.joint_positions_radians[0])
            send_calls += 1
            sequence += 1
            next_send += interval
        time.sleep(0.25)
    finally:
        sender.close()
    return {
        "sendCalls": send_calls,
        "senderSentCount": sender.sent_count,
        "sequenceStart": int(start_sequence),
        "nextSequence": sequence,
        "durationSeconds": float(duration_seconds),
        "configuredRateHz": float(rate_hz),
        "jointTargets": {"q1First": q1_values[0] if q1_values else None, "q1Last": q1_values[-1] if q1_values else None, "q1Min": min(q1_values) if q1_values else None, "q1Max": max(q1_values) if q1_values else None},
        "lastError": str(sender.last_error) if sender.last_error else None,
    }


def _run_mujoco(mode, local_port, logical_block_id, rate_hz, max_steps, start_sequence, host="127.0.0.1", realtime=False, phase_anchor_positions_mujoco=None, initial_block_positions_mujoco=None, m13_6_visual_mode=True):
    sender = TcpLatestStateSender(host, local_port)
    started = time.monotonic()
    try:
        if mode == "single":
            result = run_mujoco_pick_place_stream(
                logical_block_id=logical_block_id,
                sink=sender,
                rate_hz=rate_hz,
                max_steps=max_steps,
                place=True,
                start_sequence=start_sequence,
                realtime=realtime,
                rebase_blocks_to_quest_catalog=False,
                phase_anchor_positions_mujoco=phase_anchor_positions_mujoco,
                m13_6_visual_mode=m13_6_visual_mode,
            )
        else:
            result = run_mujoco_sequential_stream(
                sink=sender,
                rate_hz=rate_hz,
                max_steps_per_block=max_steps,
                start_sequence=start_sequence,
                realtime=realtime,
                rebase_blocks_to_quest_catalog=False,
                phase_anchor_positions_mujoco=phase_anchor_positions_mujoco,
                initial_block_positions_mujoco=initial_block_positions_mujoco,
                m13_6_visual_mode=m13_6_visual_mode,
            )
    finally:
        # The stream functions own and close the injected sender. A second
        # close is safe and ensures a sender is closed if setup fails early.
        sender.close()
    result["senderSentCount"] = sender.sent_count
    result["senderLastError"] = str(sender.last_error) if sender.last_error else None
    result["wallClockDurationSeconds"] = time.monotonic() - started
    result["realtimePlayback"] = bool(realtime)
    return result


def _parse_quest_log_lines(lines):
    """Parse M13.6 receiver evidence from already-filtered logcat lines.

    Older receiver builds emitted a dedicated ``scene_bindings`` line. The
    current receiver also exposes the same binding result in every periodic
    diagnostics line as ``bindings=True``. Both are authoritative runtime
    evidence; the latter must not be missed merely because the dedicated line
    was emitted before the logcat capture window.
    """
    lines = [line for line in lines if "M13_6_VISUAL" in line]
    diagnostics = [line for line in lines if " diagnostics " in line]
    scene_bindings = [line for line in lines if "scene_bindings" in line]
    binding_diagnostics = [
        line
        for line in diagnostics
        if re.search(r"\bbindings\s*=\s*(?:true|1)\b", line, re.IGNORECASE)
    ]
    if scene_bindings:
        binding_evidence_source = "scene_bindings"
        binding_evidence = scene_bindings
    elif binding_diagnostics:
        binding_evidence_source = "diagnostics_bindings_true"
        binding_evidence = binding_diagnostics
    else:
        binding_evidence_source = None
        binding_evidence = []
    return {
        "lineCount": len(lines),
        "receiverStarted": [line for line in lines if "receiver_started" in line],
        "sceneBindings": scene_bindings,
        "bindingDiagnostics": binding_diagnostics,
        "bindingEvidence": binding_evidence,
        "bindingEvidenceSource": binding_evidence_source,
        "bindings": bool(binding_evidence),
        "frameAccepted": [line for line in lines if "frame_accepted" in line],
        "diagnostics": diagnostics,
        "warnings": [line for line in lines if "Warning" in line or "rejected" in line or "error=" in line],
        "allM136Lines": lines[-80:],
    }


def _quest_logs(adb):
    output = _run_adb(adb, "logcat", "-d", "-v", "brief", "-s", "Unity", check=False).stdout
    return _parse_quest_log_lines(output.splitlines())


def run(args):
    adb = _adb_path(args.adb)
    device_details = _ensure_device(adb)
    pid = _ensure_app(adb)
    forwarded, forwards = _forward(adb, args.forward_port, QUEST_VISUAL_PORT)
    if not forwarded:
        raise RuntimeError("ADB TCP forward was not listed after creation")

    report = {
        "schemaVersion": 1,
        "recordType": "m13_6_usb_visual_acceptance",
        "transport": {
            "kind": "adb_tcp_forward",
            "localHost": "127.0.0.1",
            "localPort": args.forward_port,
            "questPort": QUEST_VISUAL_PORT,
            "m8ControlPortUntouched": 11001,
        },
        "quest": {
            "package": PACKAGE,
            "pid": pid,
            "adbDevices": device_details,
            "socketStateBefore": _socket_state(adb, QUEST_VISUAL_PORT),
            "forwardList": forwards,
        },
        "runs": [],
        "evidenceBoundary": {
            "questReceiverOperated": True,
            "questVisualAccepted": False,
            "userWoreQuest": False,
            "nd8Operated": False,
            "com11Opened": False,
            "newEegCollected": False,
            "physicalRobotOperated": False,
        },
    }

    next_sequence = 0
    if args.mode in ("synthetic", "all"):
        synthetic = _send_synthetic(
            args.forward_port,
            args.duration_seconds,
            args.rate_hz,
            args.start_sequence,
        )
        report["runs"].append({"kind": "synthetic", **synthetic})
        next_sequence = synthetic["nextSequence"]
    if args.mode in ("single", "all"):
        single = _run_mujoco("single", args.forward_port, args.logical_block_id, args.rate_hz, args.max_steps, next_sequence)
        report["runs"].append({"kind": "mujoco_single", **single})
        next_sequence = single["nextSequence"]
    if args.mode in ("sequential", "all"):
        sequential = _run_mujoco("sequential", args.forward_port, args.logical_block_id, args.rate_hz, args.max_steps, next_sequence)
        report["runs"].append({"kind": "mujoco_sequential", **sequential})
        next_sequence = sequential["nextSequence"]

    time.sleep(0.5)
    report["quest"]["socketStateAfter"] = _socket_state(adb, QUEST_VISUAL_PORT)
    report["quest"]["logs"] = _quest_logs(adb)
    sender_errors = [
        item.get("lastError") or item.get("senderLastError")
        for item in report["runs"]
        if item.get("lastError") or item.get("senderLastError")
    ]
    connected = bool(
        report["quest"]["logs"]["receiverStarted"]
        or any("tcp_client_connected" in line for line in report["quest"]["logs"]["allM136Lines"])
    )
    listener_alive = bool(report["quest"]["socketStateAfter"]["tcpListen"])
    report["transportEvidence"] = {
        "senderErrors": sender_errors,
        "senderHealthy": not sender_errors,
        "tcpClientConnectedObserved": connected,
        "questTcpListenerAlive": listener_alive,
        "frameAcceptanceLogObserved": bool(report["quest"]["logs"]["frameAccepted"]),
    }
    if report["quest"]["logs"]["frameAccepted"]:
        report["status"] = "PASS"
    elif connected and listener_alive and not sender_errors:
        report["status"] = "TRANSPORT_ACTIVE_DIAGNOSTIC_BUILD_NEEDED"
    else:
        report["status"] = "BLOCKED_NO_TRANSPORT_EVIDENCE"
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "output": args.output, "acceptedLogCount": len(report["quest"]["logs"]["frameAccepted"])}, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("synthetic", "single", "sequential", "all"), default="synthetic")
    parser.add_argument("--adb")
    parser.add_argument("--forward-port", type=int, default=DEFAULT_FORWARD_PORT)
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--rate-hz", type=float, default=30.0)
    parser.add_argument("--start-sequence", type=int, default=0)
    parser.add_argument("--logical-block-id", default="block_sim_01")
    parser.add_argument("--max-steps", type=int, default=9000)
    parser.add_argument("--output", default=str(ROOT / "artifacts/m13_6_usb_visual_acceptance.json"))
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
