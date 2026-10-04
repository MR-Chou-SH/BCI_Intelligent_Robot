"""PC-side M6 final-decision to M8 Quest selection orchestration.

This module intentionally owns no EEG decoding and never derives a TargetId.  The
Quest remains the authority that resolves an accepted class index through its
frozen BciSelectionSnapshot.
"""
from datetime import datetime, timezone
import json
from collections import deque
import select
import socket
import time


PROTOCOL_VERSION = 1
M6_FINAL_LABEL_TO_CANONICAL_CLASS = {
    "target_left": 0,
    "target_center": 1,
    "target_right": 2,
}


class QuestSelectionTransportError(RuntimeError):
    """The PC could not safely complete an M8 request/ACK exchange."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def canonical_class_index(final_decision_label):
    """Map the frozen M6 final label vocabulary to the M8 slot vocabulary."""
    try:
        return M6_FINAL_LABEL_TO_CANONICAL_CLASS[final_decision_label]
    except KeyError as error:
        raise ValueError("unsupported M6 final decision label: {!r}".format(final_decision_label)) from error


def normalize_selection_ack(ack, expected_selection_id):
    """Validate a Quest ACK and remove only transport line-ending contamination."""
    if not isinstance(ack, dict):
        raise QuestSelectionTransportError("Quest ACK was not a JSON object")
    if ack.get("protocolVersion") != PROTOCOL_VERSION:
        raise QuestSelectionTransportError("Quest ACK protocol version mismatch")
    if ack.get("messageType") != "selection_ack":
        raise QuestSelectionTransportError("Quest response was not selection_ack")
    if ack.get("selectionId") != expected_selection_id:
        raise QuestSelectionTransportError("Quest ACK selection ID mismatch")
    if not isinstance(ack.get("accepted"), bool):
        raise QuestSelectionTransportError("Quest ACK accepted field was not boolean")

    normalized = dict(ack)
    class_name = normalized.get("resolvedClassName")
    if isinstance(class_name, str):
        normalized["resolvedClassName"] = class_name.rstrip("\r\n")
    return normalized


def _public_candidate(candidate):
    """Serialize the authoritative Python candidate without guessing identity."""
    if isinstance(candidate, dict):
        value = dict(candidate)
    elif callable(getattr(candidate, "to_public_dict", None)):
        value = dict(candidate.to_public_dict())
    else:
        value = {
            "slotIndex": int(candidate.slot_index),
            "targetId": str(candidate.target_id),
            "logicalBlockId": str(candidate.logical_block_id),
            "nominalFrequencyHz": float(candidate.nominal_frequency_hz),
        }
    active = bool(value.get("active", not str(value["logicalBlockId"]).startswith("__inactive")))
    logical_block_id = value.get("logicalBlockId")
    target_id = value.get("targetId")
    if not active and (logical_block_id is None or target_id is None):
        logical_block_id = None
        target_id = None
    return {
        "slotIndex": int(value["slotIndex"]),
        "targetId": None if target_id is None else str(target_id),
        "logicalBlockId": None if logical_block_id is None else str(logical_block_id),
        "nominalFrequencyHz": float(value.get("nominalFrequencyHz", value.get("frequencyHz"))),
        "active": active,
    }


class QuestSelectionTcpServer:
    """One persistent PC listener serving M8 newline-delimited JSON requests."""
    def __init__(
        self,
        host="0.0.0.0",
        port=11001,
        accept_timeout_seconds=30.0,
        ack_timeout_seconds=5.0,
        request_event_sink=None,
    ):
        self.host = host
        self.port = int(port)
        self.accept_timeout_seconds = float(accept_timeout_seconds)
        self.ack_timeout_seconds = float(ack_timeout_seconds)
        # Optional diagnostics-only hook. It observes the fully serialized
        # request immediately before sendall(); it never changes the payload.
        self.request_event_sink = request_event_sink
        self._listener = None
        self._connection = None
        self._buffer = b""
        self._connection_accepted = False
        self._connection_peer = None
        self._request_count = 0
        self._ack_count = 0
        self._batch_close_ack_count = 0
        self._batch_close_ids = []
        self._m19_decode_request_count = 0
        self._m19_decode_abort_count = 0
        self._m19_research_message_count = 0
        self._m19_decode_ack_count = 0
        self._controller_events = deque()
        self._snapshots_by_selection = {}
        self._listener_ready_observed = False
        self._listener_closed = False
        self._listener_start_count = 0

    @property
    def evidence(self):
        """Return durable facts about a real M8 Quest TCP exchange."""
        return {
            "mode": "m8_selection_tcp",
            "host": self.host,
            "port": self.port,
            "listenerReadyObserved": self._listener_ready_observed,
            "listenerClosed": self._listener_closed,
            "listenerStartCount": self._listener_start_count,
            "connectionAccepted": self._connection_accepted,
            "connectionActive": self._connection is not None,
            "peer": self._connection_peer,
            "requestCount": self._request_count,
            "ackCount": self._ack_count,
            "batchCloseAckCount": self._batch_close_ack_count,
            "batchCloseIds": list(self._batch_close_ids),
            "m19DecodeRequestCount": self._m19_decode_request_count,
            "m19DecodeAbortCount": self._m19_decode_abort_count,
            "m19ResearchMessageCount": self._m19_research_message_count,
            "m19DecodeAckCount": self._m19_decode_ack_count,
            "controllerEventCount": len(self._controller_events),
            "confirmedBatchCount": sum(1 for item in self._controller_events if item.get("messageType") == "target_batch_confirmed"),
            "undoEventCount": sum(1 for item in self._controller_events if item.get("messageType") == "selection_undo"),
            "authoritativeSnapshotCount": len(self._snapshots_by_selection),
            "authoritativeSnapshots": [
                {
                    "selectionId": selection_id,
                    "candidateSnapshotId": record["candidateSnapshotId"],
                    "candidateSnapshotVersion": record["candidateSnapshotVersion"],
                    "pageId": record.get("pageId"),
                    "pageEpoch": record.get("pageEpoch"),
                    "candidates": list(record["candidateSnapshot"]),
                }
                for selection_id, record in sorted(self._snapshots_by_selection.items())
            ],
        }

    def start(self):
        if self._listener is not None:
            return self
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(1)
        listener.settimeout(self.accept_timeout_seconds)
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._listener_ready_observed = True
        self._listener_closed = False
        self._listener_start_count += 1
        return self

    def close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
        if self._listener is not None:
            try:
                self._listener.close()
            finally:
                self._listener = None
        if self._listener_ready_observed:
            self._listener_closed = True

    def accept_research_peer(self):
        """Accept the Quest Research-only client before exchanging offers."""
        if self._connection is not None:
            return self._connection_peer
        self._ensure_connection()
        return self._connection_peer

    def send_research_message(self, payload):
        """Send an allow-listed Research lifecycle message without Demo fields."""
        if not isinstance(payload, dict) or payload.get("protocolVersion") != PROTOCOL_VERSION:
            raise QuestSelectionTransportError("Research message has an invalid protocol header")
        message_type = payload.get("messageType")
        if message_type not in (
            "m19_research_offer",
            "m19_research_block_pause",
            "m19_research_trial_complete",
            "m19_research_session_complete",
            "m19_research_stimulus_start",
            "m19_research_stimulus_stop",
        ):
            raise QuestSelectionTransportError("unsupported PC-to-Quest Research message type")
        forbidden = {"context", "contextcondition", "condition", "split", "logicalblockid", "targetid", "classindex", "eeg"}

        def contains_forbidden_key(value):
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = str(key).replace("_", "").lower()
                    if normalized in forbidden or contains_forbidden_key(nested):
                        return True
            elif isinstance(value, (list, tuple)):
                return any(contains_forbidden_key(item) for item in value)
            return False

        if contains_forbidden_key(payload):
            raise QuestSelectionTransportError("Research UI message contains hidden context, split, EEG, or TargetId fields")
        if message_type == "m19_research_offer":
            required = ("sessionId", "trialId", "attemptId", "blockId", "ordinal", "slotIndex", "frequencyHz", "targetLabel", "cueBeepCount")
            if any(key not in payload for key in required):
                raise QuestSelectionTransportError("Research offer is missing a fixed target or attempt identity")
            slot = payload["slotIndex"]
            if isinstance(slot, bool) or slot not in (0, 1, 2):
                raise QuestSelectionTransportError("Research offer slot must be 0, 1, or 2")
            frequencies = (7.2, 9.0, 12.0)
            labels = ("YELLOW", "BLUE", "GREEN")
            try:
                frequency = float(payload["frequencyHz"])
                cue_beep_count = int(payload["cueBeepCount"])
                ordinal = int(payload["ordinal"])
            except (TypeError, ValueError) as error:
                raise QuestSelectionTransportError("Research offer has malformed numeric fields") from error
            if (
                not all(isinstance(payload[key], str) and payload[key].strip() for key in ("sessionId", "trialId", "attemptId", "blockId"))
                or ordinal < 1
                or abs(frequency - frequencies[slot]) > 0.01
                or payload["targetLabel"] != labels[slot]
                or cue_beep_count != slot + 1
            ):
                raise QuestSelectionTransportError("Research offer violates the frozen slot, frequency, color, or cue contract")
        connection = self._require_active_connection()
        self._send_line(connection, payload)
        self._m19_research_message_count += 1
        return dict(payload)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def register_snapshot(
        self,
        selection_id,
        candidates,
        snapshot_id=None,
        snapshot_version=None,
        page_id=None,
        page_epoch=None,
    ):
        if not selection_id or not candidates:
            raise QuestSelectionTransportError("authoritative candidate snapshot is required")
        serialized = [_public_candidate(candidate) for candidate in candidates]
        if snapshot_id is None:
            snapshot_id = "snapshot-" + str(selection_id)
        record = {
            "candidateSnapshotId": str(snapshot_id),
            "candidateSnapshotVersion": int(snapshot_version or 1),
            "candidateSnapshot": serialized,
        }
        if page_id is not None:
            record["pageId"] = str(page_id)
        if page_epoch is not None:
            record["pageEpoch"] = int(page_epoch)
        self._snapshots_by_selection[str(selection_id)] = record
        return dict(record)

    def open_selection(self, selection_id, snapshot=None, page_id=None, page_epoch=None):
        if snapshot is not None:
            snapshot_id = snapshot.get("snapshotId") if isinstance(snapshot, dict) else None
            snapshot_version = snapshot.get("snapshotVersion") if isinstance(snapshot, dict) else None
            candidates = snapshot.get("candidates") if isinstance(snapshot, dict) else snapshot
            if isinstance(snapshot, dict):
                page_id = snapshot.get("pageId", page_id)
                page_epoch = snapshot.get("pageEpoch", page_epoch)
            self.register_snapshot(
                selection_id,
                candidates,
                snapshot_id,
                snapshot_version,
                page_id,
                page_epoch,
            )
        payload = {"messageType": "selection_open", "selectionId": selection_id}
        registered = self._snapshots_by_selection.get(str(selection_id))
        if registered is not None:
            payload.update(registered)
        return self._request(payload, selection_id)

    def submit_eeg_selection(self, selection_id, predicted_class_index, predicted_label=None):
        request = {
            "messageType": "eeg_selection",
            "selectionId": selection_id,
            "predictedClassIndex": int(predicted_class_index),
        }
        if predicted_label is not None:
            request["predictedLabel"] = predicted_label
        registered = self._snapshots_by_selection.get(str(selection_id))
        if registered is not None:
            request["candidateSnapshotId"] = registered["candidateSnapshotId"]
            request["candidateSnapshotVersion"] = registered["candidateSnapshotVersion"]
            if "pageId" in registered:
                request["pageId"] = registered["pageId"]
            if "pageEpoch" in registered:
                request["pageEpoch"] = registered["pageEpoch"]
        return self._request(request, selection_id)

    def abort_selection(self, selection_id):
        return self._request({"messageType": "selection_abort", "selectionId": selection_id}, selection_id)

    def send_m19_decode_ack(self, ack):
        """Send the explicit PC decision for one Quest-originated M19 request."""
        if not isinstance(ack, dict) or ack.get("messageType") != "eeg_decode_ack":
            raise QuestSelectionTransportError("M19 decode ACK must be an eeg_decode_ack object")
        selection_id = ack.get("selectionId")
        if (
            ack.get("protocolVersion") != PROTOCOL_VERSION
            or not isinstance(selection_id, str)
            or not selection_id
            or selection_id != selection_id.strip()
            or not isinstance(ack.get("accepted"), bool)
        ):
            raise QuestSelectionTransportError("M19 decode ACK is missing protocol or selection identity")
        connection = self._require_active_connection()
        self._send_line(connection, ack)
        self._m19_decode_ack_count += 1
        return dict(ack)

    def send_m19_decode_abort_ack(self, ack):
        """Acknowledge an abort after invalidating the PC-side active trial."""
        if not isinstance(ack, dict) or ack.get("messageType") != "eeg_decode_abort_ack":
            raise QuestSelectionTransportError("M19 abort ACK must be an eeg_decode_abort_ack object")
        selection_id = ack.get("selectionId")
        if (
            ack.get("protocolVersion") != PROTOCOL_VERSION
            or not isinstance(selection_id, str)
            or not selection_id
            or selection_id != selection_id.strip()
            or not isinstance(ack.get("accepted"), bool)
        ):
            raise QuestSelectionTransportError("M19 abort ACK is missing protocol or selection identity")
        connection = self._require_active_connection()
        self._send_line(connection, ack)
        return dict(ack)

    def submit_m19_eeg_selection(self, result):
        """Return a class-only M19 result to Quest and await its existing ACK."""
        if not isinstance(result, dict):
            raise QuestSelectionTransportError("M19 decode result must be an object")
        if result.get("messageType") != "eeg_decode_result" or result.get("decisionMade") is not True:
            raise QuestSelectionTransportError("only a decided eeg_decode_result can select a page slot")
        class_index = result.get("classIndex")
        if isinstance(class_index, bool) or not isinstance(class_index, int) or class_index not in (0, 1, 2):
            raise QuestSelectionTransportError("M19 classIndex must be 0, 1, or 2")
        if result.get("slotIndex") != class_index or result.get("accepted") is not True:
            raise QuestSelectionTransportError("M19 result class/slot identity is inconsistent")
        if "targetId" in result or "resolvedTargetId" in result:
            raise QuestSelectionTransportError("PC must not resolve or send a TargetId")
        selection_id = result.get("selectionId")
        if not selection_id:
            raise QuestSelectionTransportError("M19 result is missing selectionId")
        request = {
            "messageType": "eeg_selection",
            "selectionId": selection_id,
            "trialId": result.get("trialId"),
            "pageId": result.get("pageId"),
            "pageEpoch": result.get("pageEpoch"),
            "predictedClassIndex": class_index,
            "classIndex": class_index,
            "slotIndex": class_index,
            "hasDecisionMade": True,
            "decisionMade": True,
        }
        return self._request(request, selection_id)

    def submit_m19_no_decision(self, result):
        """Close a no-decision trial without inventing a class or TargetId."""
        if not isinstance(result, dict) or result.get("messageType") != "eeg_decode_result":
            raise QuestSelectionTransportError("M19 no-decision result must be an eeg_decode_result object")
        if result.get("decisionMade") is not False or result.get("classIndex") is not None:
            raise QuestSelectionTransportError("no-decision result must not contain a decoded class")
        if "targetId" in result or "resolvedTargetId" in result:
            raise QuestSelectionTransportError("PC must not resolve or send a TargetId")
        selection_id = result.get("selectionId")
        if not selection_id:
            raise QuestSelectionTransportError("M19 result is missing selectionId")
        request = {
            "messageType": "selection_abort",
            "selectionId": selection_id,
            "trialId": result.get("trialId"),
            "pageId": result.get("pageId"),
            "pageEpoch": result.get("pageEpoch"),
            "abortReason": "no_decision",
            "hasDecisionMade": True,
            "decisionMade": False,
            "classIndex": -1,
            "slotIndex": -1,
        }
        return self._request(request, selection_id)

    def _require_active_connection(self):
        if self._connection is None:
            raise QuestSelectionTransportError("Quest connection is not active")
        return self._connection

    @staticmethod
    def _send_line(connection, payload):
        try:
            connection.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError as error:
            raise QuestSelectionTransportError("Quest response send failed: {}".format(error)) from error

    def close_batch(self, payload):
        """Close the current Quest M8 group using the existing batch contract.

        Physical Quest A already publishes ``target_batch_confirmed`` to this
        listener.  The Showcase has no physical controller, so it sends the
        same confirmed selection payload in the opposite direction.  Unity
        validates the current selected selection IDs/slots/TargetIds before
        clearing its active group and returns a ``batch_ack``.  This is a
        lifecycle bridge only; it does not select a target or alter snapshot
        validation.
        """
        if not isinstance(payload, dict):
            raise QuestSelectionTransportError("confirmed batch payload must be an object")
        confirmed = payload.get("confirmedBatch")
        if not isinstance(confirmed, dict):
            confirmed = payload
        batch_id = payload.get("batchId") or confirmed.get("batchId")
        selections = confirmed.get("selections")
        if not batch_id or not isinstance(selections, list) or not selections:
            raise QuestSelectionTransportError("confirmed batch close requires batchId and selections")

        connection = self._ensure_connection()
        self._request_count += 1
        message = {
            "protocolVersion": PROTOCOL_VERSION,
            "pcMonotonicNs": time.monotonic_ns(),
            "pcUtc": _utc_now(),
            "messageType": "target_batch_confirmed",
            "batchId": str(batch_id),
            "confirmedBatch": confirmed,
        }
        try:
            connection.sendall((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            while True:
                response = self._read_json_line(connection)
                if self._handle_controller_event(response, connection):
                    continue
                if not isinstance(response, dict) or response.get("protocolVersion") != PROTOCOL_VERSION:
                    raise QuestSelectionTransportError("Quest batch ACK protocol version mismatch")
                if response.get("messageType") != "batch_ack":
                    raise QuestSelectionTransportError("Quest response was not batch_ack")
                if response.get("batchId") != str(batch_id):
                    raise QuestSelectionTransportError("Quest batch ACK ID mismatch")
                if not isinstance(response.get("accepted"), bool):
                    raise QuestSelectionTransportError("Quest batch ACK accepted field was not boolean")
                self._batch_close_ack_count += 1
                self._batch_close_ids.append(str(batch_id))
                if not response["accepted"]:
                    raise QuestSelectionTransportError(
                        "Quest rejected batch close {}: {}".format(
                            batch_id, response.get("rejectionReason", "unknown")
                        )
                    )
                return dict(response)
        except (OSError, ValueError, json.JSONDecodeError, QuestSelectionTransportError) as error:
            self._drop_connection()
            if isinstance(error, QuestSelectionTransportError):
                raise
            raise QuestSelectionTransportError("Quest batch close transport failure: {}".format(error)) from error

    def _request(self, payload, selection_id):
        connection = self._ensure_connection()
        self._request_count += 1
        message = {
            "protocolVersion": PROTOCOL_VERSION,
            "pcMonotonicNs": time.monotonic_ns(),
            "pcUtc": _utc_now(),
            **payload,
        }
        try:
            if (
                message.get("messageType") == "selection_open"
                and callable(self.request_event_sink)
            ):
                self.request_event_sink(dict(message))
            connection.sendall((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            while True:
                response = self._read_json_line(connection)
                if self._handle_controller_event(response, connection):
                    continue
                response = normalize_selection_ack(response, selection_id)
                self._ack_count += 1
                return response
        except (OSError, ValueError, json.JSONDecodeError, QuestSelectionTransportError) as error:
            self._drop_connection()
            if isinstance(error, QuestSelectionTransportError):
                raise
            raise QuestSelectionTransportError("Quest selection transport failure: {}".format(error)) from error

    def _handle_controller_event(self, payload, connection):
        if not isinstance(payload, dict):
            return False
        message_type = payload.get("messageType")
        if message_type == "target_batch_confirmed":
            self._controller_events.append(dict(payload))
            batch = payload.get("confirmedBatch") or {}
            batch_id = payload.get("batchId") or batch.get("batchId")
            ack = {"protocolVersion": PROTOCOL_VERSION, "messageType": "batch_ack", "batchId": batch_id}
            connection.sendall((json.dumps(ack, separators=(",", ":")) + "\n").encode("utf-8"))
            return True
        if message_type == "selection_undo":
            self._controller_events.append(dict(payload))
            return True
        if message_type == "eeg_decode_request":
            self._m19_decode_request_count += 1
            self._controller_events.append(dict(payload))
            return True
        if message_type == "eeg_decode_abort":
            self._m19_decode_abort_count += 1
            self._controller_events.append(dict(payload))
            return True
        if message_type in (
            "m19_research_ready",
            "m19_research_offer_ack",
            "m19_research_trigger",
            "m19_research_cancel",
            "m19_research_resume",
            "m19_research_stimulus_started",
            "m19_research_stimulus_stopped",
        ):
            self._m19_research_message_count += 1
            self._controller_events.append(dict(payload))
            return True
        return False

    def drain_controller_events(self):
        events = []
        while self._controller_events:
            events.append(self._controller_events.popleft())
        return events

    def poll_controller_events(self, timeout_seconds=0.0):
        """Read Quest-originated batch/undo notifications without opening a port."""
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        connection = self._connection
        if connection is None:
            if self._listener is None:
                raise QuestSelectionTransportError("Quest selection listener was not started")
            remaining = max(0.0, deadline - time.monotonic())
            readable, _writable, _errors = select.select(
                [self._listener], [], [], remaining
            )
            if not readable:
                return self.drain_controller_events()
            connection = self._ensure_connection()
        while True:
            # A single recv may contain multiple newline-delimited messages.
            # Drain complete lines already in the userspace buffer before
            # selecting on the socket again; otherwise a coalesced second
            # message can remain stranded until unrelated future traffic.
            if b"\n" not in self._buffer:
                remaining = max(0.0, deadline - time.monotonic())
                readable, _writable, _errors = select.select([connection], [], [], remaining)
                if not readable:
                    break
            try:
                payload = self._read_json_line(connection)
            except socket.timeout:
                break
            if not self._handle_controller_event(payload, connection):
                # Selection ACKs are consumed by _request; do not reinterpret them
                # as controller commands when a poll races with a request.
                continue
            if timeout_seconds <= 0:
                continue
        return self.drain_controller_events()

    def wait_for_confirmed_batch(self, timeout_seconds=30.0):
        deadline = time.monotonic() + float(timeout_seconds)
        while time.monotonic() < deadline:
            events = self.poll_controller_events(min(0.1, max(0.0, deadline - time.monotonic())))
            for event in events:
                if event.get("messageType") == "target_batch_confirmed":
                    return event
        raise QuestSelectionTransportError("timed out waiting for Quest target_batch_confirmed")

    def _ensure_connection(self):
        if self._listener is None:
            raise QuestSelectionTransportError("Quest selection listener was not started")
        if self._connection is None:
            try:
                self._connection, address = self._listener.accept()
                self._connection_accepted = True
                self._connection_peer = str(address)
                self._connection.settimeout(self.ack_timeout_seconds)
            except OSError as error:
                raise QuestSelectionTransportError("Quest connection accept failed: {}".format(error)) from error
        return self._connection

    def _read_json_line(self, connection):
        while b"\n" not in self._buffer:
            data = connection.recv(4096)
            if not data:
                raise QuestSelectionTransportError("Quest closed selection connection before ACK")
            self._buffer += data
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line.rstrip(b"\r").decode("utf-8"))

    def _drop_connection(self):
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
                self._buffer = b""


class M8SelectionOrchestrator:
    """Gates M6's terminal decision record through one frozen Quest selection."""
    def __init__(self, transport, event_sink=None):
        self.transport = transport
        self.event_sink = event_sink
        self._active_by_trial_id = {}
        self._completed_trial_ids = set()
        self._submitted_final_trial_ids = set()
        self._used_selection_ids = set()
        self.events = []

    def open_selection(self, selection_id, trial_id):
        if not selection_id or not trial_id or selection_id in self._used_selection_ids or trial_id in self._active_by_trial_id or trial_id in self._completed_trial_ids:
            self._record("selection_open_rejected", selection_id, trial_id, status="invalid_or_duplicate_selection")
            return False
        self._used_selection_ids.add(selection_id)
        try:
            ack = self.transport.open_selection(selection_id)
            ack = normalize_selection_ack(ack, selection_id)
        except QuestSelectionTransportError as error:
            self._record("selection_open_transport_failure", selection_id, trial_id, status="transport_failure", reason=str(error))
            self._completed_trial_ids.add(trial_id)
            return False

        self._record("selection_open_ack", selection_id, trial_id, status="quest_accepted" if ack["accepted"] else "quest_rejected", ack=ack)
        if not ack["accepted"]:
            self._completed_trial_ids.add(trial_id)
            return False
        self._active_by_trial_id[trial_id] = selection_id
        return True

    def submit_final_decision(self, final_decision):
        trial_id = final_decision.get("trialId") if isinstance(final_decision, dict) else None
        selection_id = self._active_by_trial_id.get(trial_id)
        if selection_id is None:
            status = "duplicate_final_decision" if trial_id in self._submitted_final_trial_ids else "stale_or_unknown_trial"
            return self._record("final_decision_rejected", None, trial_id, status=status)

        self._active_by_trial_id.pop(trial_id)
        self._completed_trial_ids.add(trial_id)
        common = {
            "sessionId": final_decision.get("sessionId"),
            "stabilizer": final_decision.get("stabilizer"),
            "decisionMade": bool(final_decision.get("decisionMade")),
            "finalDecisionLabel": final_decision.get("finalDecisionLabel"),
            "decisionPredictionIndex": final_decision.get("decisionPredictionIndex"),
            "decisionRelativeTimeSeconds": final_decision.get("decisionRelativeTimeSeconds"),
        }
        if not final_decision.get("decisionMade"):
            return self._abort_selection(
                selection_id,
                trial_id,
                status="no_decision",
                reason=final_decision.get("reason"),
                **common
            )
        self._submitted_final_trial_ids.add(trial_id)
        try:
            class_index = canonical_class_index(final_decision.get("finalDecisionLabel"))
        except ValueError as error:
            return self._abort_selection(
                selection_id,
                trial_id,
                status="invalid_final_label",
                reason=str(error),
                **common
            )
        try:
            ack = self.transport.submit_eeg_selection(selection_id, class_index)
            ack = normalize_selection_ack(ack, selection_id)
        except QuestSelectionTransportError as error:
            return self._record("eeg_selection_transport_failure", selection_id, trial_id, status="transport_failure",
                                reason=str(error), predictedClassIndex=class_index, **common)
        status = "quest_accepted" if ack["accepted"] else "quest_rejected"
        return self._record("eeg_selection_ack", selection_id, trial_id, status=status, ack=ack,
                            predictedClassIndex=class_index, rejectionReason=ack.get("rejectionReason"), **common)

    def abort_trial(self, trial_id, reason):
        selection_id = self._active_by_trial_id.pop(trial_id, None)
        if selection_id is None:
            return self._record("trial_abort_rejected", None, trial_id, status="stale_or_unknown_trial", reason=reason)
        self._completed_trial_ids.add(trial_id)
        return self._abort_selection(selection_id, trial_id, status="aborted", reason=reason)

    def _abort_selection(self, selection_id, trial_id, status, reason, **common):
        try:
            ack = self.transport.abort_selection(selection_id)
            ack = normalize_selection_ack(ack, selection_id)
        except QuestSelectionTransportError as error:
            return self._record(
                "selection_abort_transport_failure",
                selection_id,
                trial_id,
                status="transport_failure",
                reason=str(error),
                **common
            )

        if not ack["accepted"]:
            return self._record(
                "selection_abort_ack",
                selection_id,
                trial_id,
                status="quest_rejected",
                reason=reason,
                ack=ack,
                rejectionReason=ack.get("rejectionReason"),
                **common
            )
        return self._record(
            "selection_abort_ack",
            selection_id,
            trial_id,
            status=status,
            reason=reason,
            ack=ack,
            **common
        )

    def _record(self, event_type, selection_id, trial_id, **values):
        record = {
            "recordType": "m8_selection_orchestration",
            "eventType": event_type,
            "selectionId": selection_id,
            "trialId": trial_id,
            "pcUtc": _utc_now(),
            **values,
        }
        self.events.append(record)
        if self.event_sink is not None:
            self.event_sink(record)
        return record


class M8LiveTrialBridge:
    """Keep M6 controller semantics intact while attaching its stopped-trial result."""
    def __init__(self, live_controller, selection_orchestrator):
        self.live_controller = live_controller
        self.selection_orchestrator = selection_orchestrator

    def start_trial(self, selection_id, association):
        trial_id = association.get("trialId")
        if not self.selection_orchestrator.open_selection(selection_id, trial_id):
            return False
        controller_association = dict(association)
        if getattr(self.live_controller, "accepts_selection_id", False):
            controller_association["selectionId"] = selection_id
        if self.live_controller.start_trial(controller_association):
            return True
        self.selection_orchestrator.abort_trial(trial_id, "m6_trial_start_rejected")
        return False

    def stop_trial(self, reason="stimulus_stopped"):
        result = self.live_controller.stop_trial(reason)
        if result is None:
            return None
        combined = dict(result)
        combined["m8Selection"] = self.selection_orchestrator.submit_final_decision(result)
        recorder = getattr(self.live_controller, "record_final_submission", None)
        if callable(recorder):
            recorder(dict(combined["m8Selection"], m13Decision=result.get("m13Decision"),
                          m13SelectedTargetId=result.get("m13SelectedTargetId"),
                          m13SelectedLogicalBlockId=result.get("m13SelectedLogicalBlockId")))
        return combined

    def abort_trial(self, trial_id, reason):
        selection = self.selection_orchestrator.abort_trial(trial_id, reason)
        decoder_result = self.live_controller.stop_trial(reason)
        recorder = getattr(self.live_controller, "record_final_submission", None)
        if callable(recorder):
            recorder(selection)
        return {"decoderResult": decoder_result, "m8Selection": selection}
