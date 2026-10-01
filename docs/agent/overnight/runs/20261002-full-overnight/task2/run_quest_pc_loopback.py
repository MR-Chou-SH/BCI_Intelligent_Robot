"""Drive the real M20 PC receiver with a synthetic Quest TCP batch."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time

from integration.m16_paged_queue import confirmed_batch_payloads
from integration.m20_assistive_desk_e2e import _select_pair
from integration.m20_assistive_scene_contract import DEFAULT_SPEC_PATH, load_spec
from integration.m20_quest_pc_acceptance import receive_and_execute_one_batch
from integration.m20_scene_layout_snapshot import create_scene_layout_snapshot, serialize_scene_layout_snapshot
from integration.m8_selection_transport.simulated_batch_consumer import send_line


ROOT = Path(__file__).resolve().parents[5]
OUTPUT_ROOT = Path(__file__).resolve().parent / "quest-pc-loopback-01"
SEED = 190926


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return int(reservation.getsockname()[1])


def _connect_when_ready(port: int, timeout_seconds: float = 5.0):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=0.25)
        except OSError as error:
            last_error = error
            time.sleep(0.025)
    raise TimeoutError("synthetic Quest client could not connect: {}".format(last_error))


def run() -> dict:
    if OUTPUT_ROOT.exists():
        raise FileExistsError("refusing to overwrite loopback evidence: {}".format(OUTPUT_ROOT))
    OUTPUT_ROOT.mkdir(parents=True)
    spec, _spec_hash = load_spec(DEFAULT_SPEC_PATH)
    snapshot, runtime_spec = create_scene_layout_snapshot(
        spec, SEED, scene_id="m20-quest-pc-loopback-{}".format(SEED), created_utc="2026-10-02T00:00:00Z"
    )
    _queue, _backend, _session, _events, plan, _trigger_info = _select_pair(
        runtime_spec,
        "assist_medicine_box",
        "assist_user_zone",
        (0, 2),
        "m20-quest-pc-loopback",
    )
    payloads = confirmed_batch_payloads(plan, batch_id_prefix="m20-quest-pc-loopback-batch")
    if len(payloads) != 1:
        raise AssertionError("source/destination selection must be a single ordered confirmed batch")
    payload = payloads[0]
    payload["confirmedBatch"]["sceneId"] = snapshot["sceneId"]
    payload["confirmedBatch"]["sceneLayoutSnapshotJson"] = serialize_scene_layout_snapshot(snapshot)

    port = _free_local_port()
    outcome = {}
    receiver_output = OUTPUT_ROOT / "receiver"

    def serve():
        try:
            outcome["report"] = receive_and_execute_one_batch(
                host="127.0.0.1",
                port=port,
                timeout_seconds=45.0,
                output_dir=receiver_output,
            )
        except BaseException as error:
            outcome["error"] = error

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    with _connect_when_ready(port) as connection:
        connection.settimeout(45.0)
        send_line(connection, payload)
        buffered = b""
        while b"\n" not in buffered:
            chunk = connection.recv(4096)
            if not chunk:
                raise RuntimeError("M20 PC closed the TCP connection before batch_ack")
            buffered += chunk
        ack_line, _remainder = buffered.split(b"\n", 1)
        ack = json.loads(ack_line.decode("utf-8"))

    server.join(timeout=45.0)
    if server.is_alive():
        raise TimeoutError("M20 PC receiver did not finish its MuJoCo execution")
    if "error" in outcome:
        raise outcome["error"]
    report = outcome["report"]
    if report["status"] != "PASS" or ack.get("messageType") != "batch_ack":
        raise AssertionError("synthetic Quest-to-MuJoCo loopback did not pass")
    if ack.get("batchId") != payload["confirmedBatch"]["batchId"]:
        raise AssertionError("PC ACK did not identify the accepted Quest batch")
    if report["sceneId"] != snapshot["sceneId"] or report["randomSeed"] != SEED:
        raise AssertionError("PC execution used a different Quest layout snapshot")

    result = {
        "schemaVersion": 1,
        "status": "PASS",
        "syntheticQuestTcpSender": True,
        "listener": "production receive_and_execute_one_batch / consume_one_batch",
        "sceneSnapshotValidatedBeforeAck": True,
        "sceneId": snapshot["sceneId"],
        "randomSeed": SEED,
        "orderedTargetIds": report["orderedTargetIds"],
        "batchId": report["batchId"],
        "ack": ack,
        "execution": report["execution"],
        "nd8Opened": False,
        "physicalRobotOperated": False,
        "questBuildRun": False,
        "receiverEvidenceDirectory": str((receiver_output / "attempt-01").relative_to(ROOT)).replace("\\", "/"),
    }
    result_path = OUTPUT_ROOT / "loopback_acceptance.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
