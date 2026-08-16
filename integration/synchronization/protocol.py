import json
from dataclasses import dataclass

PROTOCOL_VERSION = 1
REQUIRED_EVENT_FIELDS = {
    "schemaVersion", "eventType", "sessionId", "trialId", "sequence",
    "trialState", "unityFrame", "commonStartFrame", "globalStimulusFrame",
    "lastActiveGlobalStimulusFrame", "questMonotonicSeconds", "utc",
    "xrRefreshRateAvailable", "xrRefreshRateHz", "targetConfigurationId",
    "targets", "stopReason", "timingSemantics",
}


class JsonLineDecoder:
    """Incrementally frames UTF-8 JSON lines across arbitrary TCP receive chunks."""

    def __init__(self, maximum_line_bytes=1_048_576):
        self._buffer = bytearray()
        self.maximum_line_bytes = maximum_line_bytes

    def feed(self, chunk):
        self._buffer.extend(chunk)
        if len(self._buffer) > self.maximum_line_bytes and b"\n" not in self._buffer:
            self._buffer.clear()
            raise ValueError("json_line_exceeds_limit")
        values = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            raw = bytes(self._buffer[:newline]).rstrip(b"\r")
            del self._buffer[:newline + 1]
            if not raw:
                continue
            values.append(json.loads(raw.decode("utf-8")))
        return values


def encode_line(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def validate_stimulus_event(message):
    if message.get("protocolVersion") != PROTOCOL_VERSION:
        return "unsupported_protocol_version"
    if message.get("messageType") != "stimulus_event":
        return "unexpected_message_type"
    event = message.get("eventPayload")
    if not isinstance(event, dict):
        return "missing_event_payload"
    missing = sorted(REQUIRED_EVENT_FIELDS.difference(event))
    if missing:
        return "missing_fields:" + ",".join(missing)
    if event["eventType"] not in {
        "session_started", "stimulus_started_software", "stimulus_stopped_software"
    }:
        return "unsupported_event_type"
    if "software_render_scheduling" not in event["timingSemantics"]:
        return "invalid_timing_semantics"
    return "valid"


@dataclass
class SequenceResult:
    status: str
    expected_next: int


class SequenceTracker:
    def __init__(self):
        self._next = {}
        self._seen = set()

    def observe(self, session_id, sequence):
        key = (session_id, sequence)
        expected = self._next.get(session_id, 0)
        if key in self._seen:
            return SequenceResult("duplicate", expected)
        self._seen.add(key)
        if sequence == expected:
            status = "in_order"
            self._next[session_id] = expected + 1
        elif sequence > expected:
            status = "gap"
            self._next[session_id] = sequence + 1
        else:
            status = "out_of_order"
        return SequenceResult(status, self._next.get(session_id, expected))
