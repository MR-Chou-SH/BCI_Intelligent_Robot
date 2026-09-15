"""Append-only structured JSONL logging for M13.5 software sessions."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Iterable

from eeg.sample_association.jsonl import AppendOnlyJsonl


LOG_FILENAME = "m13.5-session.jsonl"
SCHEMA_VERSION = 1


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _contains_forbidden_identity(value):
    if isinstance(value, str):
        return "obj_" in value
    if isinstance(value, dict):
        return any(_contains_forbidden_identity(key) or _contains_forbidden_identity(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_identity(item) for item in value)
    return False


def read_jsonl(path):
    """Read a session log strictly, preserving line numbers for analyzers."""
    records = []
    with Path(path).open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("invalid JSONL at line {}: {}".format(line_number, error)) from error
            if not isinstance(record, dict):
                raise ValueError("JSONL line {} is not an object".format(line_number))
            record["_lineNumber"] = line_number
            records.append(record)
    return records


class M135SessionLogger:
    """One append-only event stream with session metadata and identity checks."""

    def __init__(self, root, session_id, runtime_mode, source_type, software_commit, policy, mapping):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.session_id = str(session_id)
        self.runtime_mode = str(runtime_mode)
        self.source_type = str(source_type)
        self.path = self.root / LOG_FILENAME
        self._jsonl = AppendOnlyJsonl(self.path)
        self._sequence = 0
        self.append(
            "session_started",
            sourceType=self.source_type,
            softwareCommit=software_commit,
            policy=dict(policy),
            mapping=list(mapping),
            logSchemaVersion=SCHEMA_VERSION,
        )

    def append(self, event_type, **values):
        record = {
            "recordType": "m13_5_session_event",
            "schemaVersion": SCHEMA_VERSION,
            "eventType": str(event_type),
            "sessionId": self.session_id,
            "runtimeMode": self.runtime_mode,
            "sourceType": self.source_type,
            "sequence": self._sequence,
            "createdUtc": _utc_now(),
            **values,
        }
        if _contains_forbidden_identity(record):
            raise ValueError("M13.5 public/session evidence cannot contain MuJoCo object IDs")
        self._jsonl.append(record)
        self._sequence += 1
        return record


def ensure_no_forbidden_identity(records):
    if _contains_forbidden_identity(records):
        raise ValueError("M13.5 evidence contains a forbidden obj_N identity")
    return True


__all__ = ["LOG_FILENAME", "SCHEMA_VERSION", "M135SessionLogger", "read_jsonl", "ensure_no_forbidden_identity"]
