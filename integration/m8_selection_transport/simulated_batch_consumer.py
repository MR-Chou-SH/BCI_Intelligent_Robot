"""Minimal stdlib-only PC consumer for Quest M8.4 confirmed batches."""
import argparse
import json
import socket
import time


def validate_batch_message(payload):
    if payload.get("protocolVersion") != 1:
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


def receive_batch(connection, timeout_seconds):
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
                return payload, validate_batch_message(payload)
    raise TimeoutError("timed out waiting for target_batch_confirmed")


def main():
    parser = argparse.ArgumentParser(description="Receive one Quest M8.4 target_batch_confirmed message.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11001)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--expect-batch-id")
    args = parser.parse_args()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.host, args.port))
        listener.listen(1)
        listener.settimeout(args.timeout_seconds)
        print("Waiting for Quest TCP client on {}:{} ...".format(args.host, args.port))
        connection, address = listener.accept()
        with connection:
            print("Quest connected from {}:{}".format(address[0], address[1]))
            payload, batch = receive_batch(connection, args.timeout_seconds)
            if args.expect_batch_id and batch["batchId"] != args.expect_batch_id:
                raise SystemExit("unexpected batchId: {}".format(batch["batchId"]))
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
