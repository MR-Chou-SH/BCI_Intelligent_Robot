"""Loopback TCP coverage for the M20 Quest snapshot/PC/MuJoCo boundary."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from integration.m16_paged_queue import confirmed_batch_payloads
from integration.m20_assistive_desk_e2e import _select_pair
from integration.m20_assistive_scene_contract import DEFAULT_SPEC_PATH, load_spec
from integration.m20_quest_pc_acceptance import (
    M20SceneBoundBatchConsumer,
    receive_and_execute_one_batch,
)
from integration.m20_scene_layout_snapshot import (
    create_scene_layout_snapshot,
    serialize_scene_layout_snapshot,
)
from integration.m8_selection_transport.simulated_batch_consumer import (
    consume_one_batch,
    send_line,
)


def _free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def _connect_when_ready(port, timeout_seconds=5.0):
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            return socket.create_connection(("127.0.0.1", port), timeout=0.25)
        except OSError as error:
            last_error = error
            time.sleep(0.025)
    raise AssertionError("loopback Quest client could not connect: {}".format(last_error))


def _make_quest_payload(spec, seed=190926, scene_id=None):
    snapshot, runtime_spec = create_scene_layout_snapshot(
        spec,
        seed,
        scene_id=scene_id or "m20-tcp-{}".format(seed),
        created_utc="2026-10-02T00:00:00Z",
    )
    _queue, _backend, _session, _events, plan, _trigger_info = _select_pair(
        runtime_spec,
        "assist_medicine_box",
        "assist_user_zone",
        (0, 2),
        "m20-loopback-{}".format(seed),
    )
    payloads = confirmed_batch_payloads(plan, batch_id_prefix="m20-loopback-batch-{}".format(seed))
    if len(payloads) != 1:
        raise AssertionError("Quest source/destination pair did not serialize as one confirmed batch")
    payload = payloads[0]
    payload["confirmedBatch"]["sceneId"] = snapshot["sceneId"]
    payload["confirmedBatch"]["sceneLayoutSnapshotJson"] = serialize_scene_layout_snapshot(snapshot)
    return payload, snapshot


class M20QuestPcAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canonical, _ = load_spec(DEFAULT_SPEC_PATH)

    def test_loopback_quest_batch_ack_uses_snapshot_for_mujoco_execution(self):
        payload, snapshot = _make_quest_payload(self.canonical)
        port = _free_local_port()
        outcome = {}

        with tempfile.TemporaryDirectory(prefix="m20-quest-pc-") as temporary:
            evidence_root = Path(temporary) / "evidence"

            def serve():
                try:
                    outcome["report"] = receive_and_execute_one_batch(
                        host="127.0.0.1",
                        port=port,
                        timeout_seconds=45.0,
                        output_dir=evidence_root,
                    )
                except BaseException as error:  # surfaced in the parent test thread
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
                        self.fail("PC closed Quest TCP before returning batch_ack")
                    buffered += chunk
                ack_line, _remainder = buffered.split(b"\n", 1)
                ack = json.loads(ack_line.decode("utf-8"))

            server.join(timeout=45.0)
            self.assertFalse(server.is_alive(), "M20 PC receiver did not finish its MuJoCo action")
            if "error" in outcome:
                raise outcome["error"]

            report = outcome["report"]
            self.assertEqual("batch_ack", ack["messageType"])
            self.assertEqual(payload["confirmedBatch"]["batchId"], ack["batchId"])
            self.assertEqual("PASS", report["status"])
            self.assertEqual(snapshot["sceneId"], report["sceneId"])
            self.assertEqual(snapshot["randomSeed"], report["randomSeed"])
            self.assertEqual(
                ["assist_medicine_box", "assist_user_zone"],
                report["orderedTargetIds"],
            )
            self.assertTrue(report["batchAckSent"])
            self.assertFalse(report["nd8Opened"])
            self.assertFalse(report["physicalRobotOperated"])

    def test_missing_scene_snapshot_fails_before_batch_ack(self):
        payload, _snapshot = _make_quest_payload(self.canonical, seed=190927)
        del payload["confirmedBatch"]["sceneLayoutSnapshotJson"]
        port = _free_local_port()
        outcome = {}
        consumer = M20SceneBoundBatchConsumer(self.canonical)

        def serve():
            try:
                outcome["receipt"] = consume_one_batch(
                    "127.0.0.1", port, timeout_seconds=10.0, receiver=consumer
                )
            except BaseException as error:  # surfaced in the parent test thread
                outcome["error"] = error

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        with _connect_when_ready(port) as connection:
            connection.settimeout(10.0)
            send_line(connection, payload)
            reply = connection.recv(4096)

        server.join(timeout=10.0)
        self.assertFalse(server.is_alive(), "invalid M20 batch receiver did not terminate")
        self.assertEqual(b"", reply, "invalid M20 scene received an ACK")
        self.assertIn("error", outcome)
        self.assertEqual([], consumer.accepted_batch_ids)

    def test_changed_pose_for_frozen_scene_fails_before_batch_ack(self):
        first_payload, first_snapshot = _make_quest_payload(self.canonical, seed=190928)
        changed_payload, changed_snapshot = _make_quest_payload(
            self.canonical,
            seed=190929,
            scene_id=first_snapshot["sceneId"],
        )
        self.assertNotEqual(
            first_snapshot["objects"],
            changed_snapshot["objects"],
            "negative fixture must contain a different frozen object pose",
        )
        consumer = M20SceneBoundBatchConsumer(self.canonical)
        first_receipt = consumer.accept(first_payload)
        self.assertEqual(first_payload["confirmedBatch"]["batchId"], first_receipt.batch["batchId"])
        port = _free_local_port()
        outcome = {}

        def serve():
            try:
                outcome["receipt"] = consume_one_batch(
                    "127.0.0.1", port, timeout_seconds=10.0, receiver=consumer
                )
            except BaseException as error:  # surfaced in the parent test thread
                outcome["error"] = error

        server = threading.Thread(target=serve, daemon=True)
        server.start()
        with _connect_when_ready(port) as connection:
            connection.settimeout(10.0)
            send_line(connection, changed_payload)
            reply = connection.recv(4096)

        server.join(timeout=10.0)
        self.assertFalse(server.is_alive(), "changed-pose M20 receiver did not terminate")
        self.assertEqual(b"", reply, "changed frozen layout received an ACK")
        self.assertIn("error", outcome)
        self.assertEqual([first_payload["confirmedBatch"]["batchId"]], consumer.accepted_batch_ids)


if __name__ == "__main__":
    unittest.main()
