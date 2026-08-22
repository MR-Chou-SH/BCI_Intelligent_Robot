"""PC-side stdlib-only sender for the M8.1 simulated EEG selection transport."""
import argparse
import json
import socket
import time
from datetime import datetime, timezone


LABELS = {"LEFT": 0, "CENTER": 1, "RIGHT": 2, "TARGET_LEFT": 0, "TARGET_CENTER": 1, "TARGET_RIGHT": 2}


def parse_class(value):
    normalized = value.strip().upper()
    if normalized in LABELS:
        return LABELS[normalized]
    return int(value)


def message(message_type, selection_id, class_index=None):
    result = {
        "protocolVersion": 1,
        "messageType": message_type,
        "selectionId": selection_id,
        "pcMonotonicNs": time.monotonic_ns(),
        "pcUtc": datetime.now(timezone.utc).isoformat(),
    }
    if class_index is not None:
        result["predictedClassIndex"] = class_index
    return result


def send_line(connection, payload):
    connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))


def receive_acks(connection, expected):
    connection.settimeout(5.0)
    buffered = b""
    received = []
    while len(received) < expected:
        chunk = connection.recv(4096)
        if not chunk:
            raise RuntimeError("Quest closed the selection connection before its ACK.")
        buffered += chunk
        while b"\n" in buffered:
            line, buffered = buffered.split(b"\n", 1)
            if line:
                item = json.loads(line.decode("utf-8"))
                received.append(item)
                print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return received


def main():
    parser = argparse.ArgumentParser(description="Send one simulated EEG selection to a Quest M8.1 build.")
    parser.add_argument("--host", default="0.0.0.0", help="PC interface to listen on; configure Quest with this PC's LAN IP.")
    parser.add_argument("--port", type=int, default=11001)
    parser.add_argument("--selection-id", required=True)
    parser.add_argument("--class", dest="class_value", help="0/1/2 or LEFT/CENTER/RIGHT; required unless --open-only.")
    parser.add_argument("--open-only", action="store_true")
    parser.add_argument("--decision-only", action="store_true")
    args = parser.parse_args()
    if args.open_only and args.decision_only:
        parser.error("--open-only and --decision-only cannot be combined.")
    if not args.open_only and args.class_value is None:
        parser.error("--class is required unless --open-only is used.")

    class_index = parse_class(args.class_value) if args.class_value is not None else None
    messages = []
    if not args.decision_only:
        messages.append(message("selection_open", args.selection_id))
    if not args.open_only:
        messages.append(message("eeg_selection", args.selection_id, class_index))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.host, args.port))
        listener.listen(1)
        print(f"Waiting for Quest TCP client on {args.host}:{args.port} ...")
        connection, address = listener.accept()
        with connection:
            print(f"Quest connected from {address[0]}:{address[1]}")
            for payload in messages:
                send_line(connection, payload)
            acks = receive_acks(connection, len(messages))
            if any(not item.get("accepted") for item in acks):
                raise SystemExit(2)


if __name__ == "__main__":
    main()
