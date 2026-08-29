"""Minimal stdlib-only PC consumer for Quest M8.4/M8.5 confirmed batches."""
import argparse
from dataclasses import dataclass
import json
import socket
import time


PROTOCOL_VERSION = 1


def validate_batch_message(payload):
    if payload.get("protocolVersion") != PROTOCOL_VERSION:
        raise ValueError("protocolVersion must be 1")
    if payload.get("messageType") != "target_batch_confirmed":
        raise ValueError("messageType must be target_batch_confirmed")
    batch = payload.get("confirmedBatch")
    if not isinstance(batch, dict):
        raise ValueError("confirmedBatch must be an object")
    if not batch.get("batchId") or not batch.get("groupId"):
        raise ValueError("batchId and groupId are required")
    selections = batch.get("selections")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 3:
        raise ValueError("confirmed batch must contain 1-3 selections")
    for selection in selections:
        if not selection.get("targetId") or "slotIndex" not in selection:
            raise ValueError("each selection requires targetId and slotIndex")
    return batch


def batch_ack(batch_id):
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "messageType": "batch_ack",
        "batchId": batch_id,
    }


@dataclass(frozen=True)
class BatchConsumerReceipt:
    payload: dict
    batch: dict
    downstream_accepted: bool
    ack: dict


class BatchIdempotentConsumer:
    """PC boundary: accept each batchId downstream once, ACK every valid delivery."""
    def __init__(self):
        self._accepted_batch_ids = []
        self._seen_batch_ids = set()

    @property
    def accepted_batch_ids(self):
        return list(self._accepted_batch_ids)

    def accept(self, payload):
        batch = validate_batch_message(payload)
        batch_id = batch["batchId"]
        downstream_accepted = batch_id not in self._seen_batch_ids
        if downstream_accepted:
            self._seen_batch_ids.add(batch_id)
            self._accepted_batch_ids.append(batch_id)
        return BatchConsumerReceipt(payload, batch, downstream_accepted, batch_ack(batch_id))


def send_line(connection, payload):
    connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))


def receive_batch(connection, timeout_seconds, receiver=None):
    receiver = receiver or BatchIdempotentConsumer()
    connection.settimeout(timeout_seconds)
    buffered = b""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("Quest closed the connection before target_batch_confirmed")
        buffered += chunk
        while b"\n" in buffered:
            line, buffered = buffered.split(b"\n", 1)
            if not line:
                continue
            payload = json.loads(line.decode("utf-8"))
            if payload.get("messageType") == "target_batch_confirmed":
                receipt = receiver.accept(payload)
                send_line(connection, receipt.ack)
                return receipt
    raise TimeoutError("timed out waiting for target_batch_confirmed")


def consume_one_batch(host="0.0.0.0", port=11001, timeout_seconds=30.0, receiver=None):
    """Bind the released M8 port, receive one batch, and return its matching ACK receipt."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(1)
        listener.settimeout(timeout_seconds)
        connection, _ = listener.accept()
        with connection:
            return receive_batch(connection, timeout_seconds, receiver=receiver)


def main():
    parser = argparse.ArgumentParser(description="Receive one Quest M8.4 target_batch_confirmed message.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11001)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--expect-batch-id")
    args = parser.parse_args()

    print("Waiting for Quest TCP client on {}:{} ...".format(args.host, args.port))
    receipt = consume_one_batch(args.host, args.port, args.timeout_seconds)
    payload, batch = receipt.payload, receipt.batch
    if args.expect_batch_id and batch["batchId"] != args.expect_batch_id:
        raise SystemExit("unexpected batchId: {}".format(batch["batchId"]))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
