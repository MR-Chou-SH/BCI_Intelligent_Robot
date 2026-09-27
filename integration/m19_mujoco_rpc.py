"""Loopback bridge from the CPython 3.9 M19 service to the CPython 3.12 MuJoCo runtime.

The ND8 vendor SDK stays in its supported CPython 3.9 environment. The existing
project MuJoCo adapter stays in the repository's CPython 3.12 environment. Only
already-confirmed Quest batches cross this local JSONL boundary.
"""

from __future__ import annotations

import json
import socket
from typing import Any

from integration.m8_selection_transport.simulated_batch_consumer import BatchConsumerReceipt


class M19RemoteExecutionAttempt:
    def __init__(self, payload: dict[str, Any]):
        self._payload = dict(payload)
        self.success = bool(self._payload.get("success"))

    def to_public_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class M19RemoteBatchDispatchResult:
    """Small M9 result view consumed by M19's existing event logger."""

    def __init__(self, payload: dict[str, Any]):
        self._payload = dict(payload)
        self.executions = [
            M19RemoteExecutionAttempt(item)
            for item in self._payload.get("executions", [])
        ]
        self.accepted = bool(self._payload.get("accepted"))
        self.duplicate_batch = bool(self._payload.get("duplicateBatch"))

    def to_public_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class M19MujocoRpcEndpoint:
    """Adapt JSONL requests to the existing M9BatchDispatcher instance."""

    def __init__(self, dispatcher):
        if not callable(getattr(dispatcher, "dispatch", None)):
            raise TypeError("dispatcher must provide dispatch(receipt)")
        self.dispatcher = dispatcher

    def handle(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"accepted": False, "error": "request must be a JSON object"}
        if payload.get("messageType") == "m19_mujoco_health":
            return {"accepted": True, "messageType": "m19_mujoco_health_ack"}
        if payload.get("messageType") != "m19_confirmed_batch_dispatch":
            return {"accepted": False, "error": "unsupported M19 MuJoCo RPC message"}

        source_payload = payload.get("payload")
        batch = payload.get("batch")
        accepted = payload.get("downstreamAccepted")
        ack = payload.get("ack")
        if (
            not isinstance(source_payload, dict)
            or not isinstance(batch, dict)
            or type(accepted) is not bool
            or not isinstance(ack, dict)
        ):
            return {"accepted": False, "error": "confirmed-batch receipt is malformed"}
        receipt = BatchConsumerReceipt(source_payload, batch, accepted, ack)
        try:
            result = self.dispatcher.dispatch(receipt)
        except Exception as error:
            return {
                "accepted": False,
                "batchId": batch.get("batchId"),
                "error": "{}: {}".format(type(error).__name__, " ".join(str(error).split())),
            }
        return {"accepted": True, "result": result.to_public_dict()}


class M19RemoteMujocoBatchDispatcher:
    """M9 dispatcher port used by CPython 3.9 without importing MuJoCo."""

    def __init__(self, host="127.0.0.1", port=11003, timeout_seconds=900.0):
        self.address = (str(host), int(port))
        self.timeout_seconds = float(timeout_seconds)
        if not 1 <= self.address[1] <= 65535 or self.timeout_seconds <= 0:
            raise ValueError("invalid M19 MuJoCo RPC endpoint or timeout")

    def health(self) -> bool:
        return self._request({"messageType": "m19_mujoco_health"}).get("accepted") is True

    def dispatch(self, receipt):
        if not isinstance(receipt, BatchConsumerReceipt):
            raise TypeError("receipt must be an M8 BatchConsumerReceipt")
        try:
            response = self._request({
                "messageType": "m19_confirmed_batch_dispatch",
                "payload": receipt.payload,
                "batch": receipt.batch,
                "downstreamAccepted": receipt.downstream_accepted,
                "ack": receipt.ack,
            })
            if response.get("accepted") is not True or not isinstance(response.get("result"), dict):
                raise RuntimeError(response.get("error", "invalid M19 MuJoCo RPC response"))
            return M19RemoteBatchDispatchResult(response["result"])
        except Exception as error:
            batch_id = receipt.batch.get("batchId") if isinstance(receipt.batch, dict) else None
            return M19RemoteBatchDispatchResult({
                "batchId": batch_id,
                "accepted": False,
                "duplicateBatch": False,
                "state": "rejected",
                "acceptedSelectionIds": [],
                "duplicateSelectionIds": [],
                "failureCode": "mujoco_runtime_rpc_failed",
                "failureReason": "{}: {}".format(type(error).__name__, " ".join(str(error).split())),
                "executions": [],
            })

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        with socket.create_connection(self.address, timeout=min(self.timeout_seconds, 15.0)) as client:
            client.settimeout(self.timeout_seconds)
            client.sendall(encoded)
            stream = client.makefile("rb")
            line = stream.readline()
        if not line:
            raise RuntimeError("M19 MuJoCo worker disconnected without a response")
        response = json.loads(line.decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError("M19 MuJoCo worker returned a non-object response")
        return response
