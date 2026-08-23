"""PC-side M6 final-decision to M8 Quest selection orchestration.

This module intentionally owns no EEG decoding and never derives a TargetId.  The
Quest remains the authority that resolves an accepted class index through its
frozen BciSelectionSnapshot.
"""
from datetime import datetime, timezone
import json
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


class QuestSelectionTcpServer:
    """One persistent PC listener serving M8 newline-delimited JSON requests."""
    def __init__(self, host="0.0.0.0", port=11001, accept_timeout_seconds=30.0, ack_timeout_seconds=5.0):
        self.host = host
        self.port = int(port)
        self.accept_timeout_seconds = float(accept_timeout_seconds)
        self.ack_timeout_seconds = float(ack_timeout_seconds)
        self._listener = None
        self._connection = None
        self._buffer = b""

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

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def open_selection(self, selection_id):
        return self._request({"messageType": "selection_open", "selectionId": selection_id}, selection_id)

    def submit_eeg_selection(self, selection_id, predicted_class_index, predicted_label=None):
        request = {
            "messageType": "eeg_selection",
            "selectionId": selection_id,
            "predictedClassIndex": int(predicted_class_index),
        }
        if predicted_label is not None:
            request["predictedLabel"] = predicted_label
        return self._request(request, selection_id)

    def abort_selection(self, selection_id):
        return self._request({"messageType": "selection_abort", "selectionId": selection_id}, selection_id)

    def _request(self, payload, selection_id):
        connection = self._ensure_connection()
        message = {
            "protocolVersion": PROTOCOL_VERSION,
            "pcMonotonicNs": time.monotonic_ns(),
            "pcUtc": _utc_now(),
            **payload,
        }
        try:
            connection.sendall((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
            return normalize_selection_ack(self._read_json_line(connection), selection_id)
        except (OSError, ValueError, json.JSONDecodeError, QuestSelectionTransportError) as error:
            self._drop_connection()
            if isinstance(error, QuestSelectionTransportError):
                raise
            raise QuestSelectionTransportError("Quest selection transport failure: {}".format(error)) from error

    def _ensure_connection(self):
        if self._listener is None:
            raise QuestSelectionTransportError("Quest selection listener was not started")
        if self._connection is None:
            try:
                self._connection, _ = self._listener.accept()
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
        if self.live_controller.start_trial(association):
            return True
        self.selection_orchestrator.abort_trial(trial_id, "m6_trial_start_rejected")
        return False

    def stop_trial(self, reason="stimulus_stopped"):
        result = self.live_controller.stop_trial(reason)
        if result is None:
            return None
        combined = dict(result)
        combined["m8Selection"] = self.selection_orchestrator.submit_final_decision(result)
        return combined

    def abort_trial(self, trial_id, reason):
        selection = self.selection_orchestrator.abort_trial(trial_id, reason)
        decoder_result = self.live_controller.stop_trial(reason)
        return {"decoderResult": decoder_result, "m8Selection": selection}
