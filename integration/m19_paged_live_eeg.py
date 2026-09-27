"""M19 user-triggered paged EEG request contract and trial lifecycle.

The PC validates the frozen page description but never resolves a decoded
class to a TargetId. That final class-to-target lookup remains in Quest's
immutable selection snapshot.
"""

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Optional, Tuple


M19_PROTOCOL_VERSION = 1
M19_DECODE_REQUEST = "eeg_decode_request"
M19_DECODE_ACK = "eeg_decode_ack"
M19_DECODE_ABORT = "eeg_decode_abort"
M19_DECODE_ABORT_ACK = "eeg_decode_abort_ack"
M19_DECODE_RESULT = "eeg_decode_result"
SSVEP_FREQUENCIES_HZ = (7.2, 9.0, 12.0)

_REQUEST_FIELDS = {
    "contractId", "protocolVersion", "messageType", "trialId", "selectionId",
    "pageId", "pageIndex", "pageEpoch", "createdUtc", "activeSlotCount",
    "slots", "questUtc", "provenance", "presentationPolicy", "formalStimulusOnsetUtc",
}
_SLOT_FIELDS = {"slotIndex", "frequencyHz", "targetId", "semanticLabel", "provenance"}


class M19ProtocolError(ValueError):
    """An M19 request or result does not satisfy the frozen protocol contract."""


@dataclass(frozen=True)
class M19DecodeSlot:
    slot_index: int
    frequency_hz: float
    target_id: str
    semantic_label: Optional[str] = None
    provenance: Optional[str] = None

    def to_payload(self):
        result = {
            "slotIndex": self.slot_index,
            "frequencyHz": self.frequency_hz,
            "targetId": self.target_id,
        }
        if self.semantic_label is not None:
            result["semanticLabel"] = self.semantic_label
        if self.provenance is not None:
            result["provenance"] = self.provenance
        return result


@dataclass(frozen=True)
class M19DecodeRequest:
    contract_id: Optional[str]
    protocol_version: int
    trial_id: str
    selection_id: str
    page_id: str
    page_index: int
    page_epoch: int
    created_utc: str
    slots: Tuple[M19DecodeSlot, ...]
    quest_utc: Optional[str] = None
    provenance: Optional[str] = None
    presentation_policy: Optional[str] = None
    formal_stimulus_onset_utc: Optional[str] = None

    @property
    def active_slot_count(self):
        return len(self.slots)

    def slot_for_class(self, class_index):
        for slot in self.slots:
            if slot.slot_index == class_index:
                return slot
        return None

    def to_payload(self):
        payload = {
            "protocolVersion": self.protocol_version,
            "messageType": M19_DECODE_REQUEST,
            "trialId": self.trial_id,
            "selectionId": self.selection_id,
            "pageId": self.page_id,
            "pageIndex": self.page_index,
            "pageEpoch": self.page_epoch,
            "createdUtc": self.created_utc,
            "activeSlotCount": self.active_slot_count,
            "slots": [slot.to_payload() for slot in self.slots],
        }
        if self.contract_id is not None:
            payload["contractId"] = self.contract_id
        if self.quest_utc is not None:
            payload["questUtc"] = self.quest_utc
        if self.provenance is not None:
            payload["provenance"] = self.provenance
        if self.presentation_policy is not None:
            payload["presentationPolicy"] = self.presentation_policy
        if self.formal_stimulus_onset_utc is not None:
            payload["formalStimulusOnsetUtc"] = self.formal_stimulus_onset_utc
        return payload


def _required_text(payload, key):
    value = payload.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        raise M19ProtocolError("{} must be a non-empty trimmed string".format(key))
    return value


def _required_index(payload, key):
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise M19ProtocolError("{} must be a non-negative integer".format(key))
    return value


def validate_decode_request(payload):
    """Parse and validate a Quest-frozen M19 request without inferring identity."""
    if not isinstance(payload, dict):
        raise M19ProtocolError("decode request must be a JSON object")
    unknown = set(payload) - _REQUEST_FIELDS
    if unknown:
        raise M19ProtocolError("unsupported request fields: {}".format(", ".join(sorted(unknown))))
    if payload.get("protocolVersion") != M19_PROTOCOL_VERSION:
        raise M19ProtocolError("unsupported M19 protocolVersion")
    if payload.get("messageType") != M19_DECODE_REQUEST:
        raise M19ProtocolError("messageType must be eeg_decode_request")

    selection_id = _required_text(payload, "selectionId")
    trial_id = _required_text(payload, "trialId")
    page_id = _required_text(payload, "pageId")
    page_index = _required_index(payload, "pageIndex")
    page_epoch = _required_index(payload, "pageEpoch")
    created_utc = _required_text(payload, "createdUtc")
    normalized_created_utc = created_utc
    if normalized_created_utc.endswith("Z") and "." in normalized_created_utc:
        prefix, fractional = normalized_created_utc[:-1].rsplit(".", 1)
        if len(fractional) > 6:
            normalized_created_utc = prefix + "." + fractional[:6] + "Z"
    try:
        parsed_utc = datetime.fromisoformat(
            normalized_created_utc.replace("Z", "+00:00")
        )
    except ValueError as error:
        raise M19ProtocolError("createdUtc must be an ISO-8601 timestamp") from error
    if parsed_utc.tzinfo is None:
        raise M19ProtocolError("createdUtc must include a timezone")

    raw_slots = payload.get("slots")
    count = payload.get("activeSlotCount")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 3:
        raise M19ProtocolError("activeSlotCount must be an integer from 1 through 3")
    if not isinstance(raw_slots, list) or len(raw_slots) != count:
        raise M19ProtocolError("slots length must equal activeSlotCount")

    slots = []
    seen_indices = set()
    seen_targets = set()
    for index, raw in enumerate(raw_slots):
        if not isinstance(raw, dict):
            raise M19ProtocolError("slots[{}] must be an object".format(index))
        extra = set(raw) - _SLOT_FIELDS
        if extra:
            raise M19ProtocolError("unsupported slot fields: {}".format(", ".join(sorted(extra))))
        slot_index = raw.get("slotIndex")
        if isinstance(slot_index, bool) or not isinstance(slot_index, int) or not 0 <= slot_index < 3:
            raise M19ProtocolError("slotIndex must be 0, 1, or 2")
        if slot_index in seen_indices:
            raise M19ProtocolError("slotIndex values must be unique")
        frequency = raw.get("frequencyHz")
        if isinstance(frequency, bool) or not isinstance(frequency, (int, float)):
            raise M19ProtocolError("frequencyHz must be numeric")
        frequency = float(frequency)
        if not math.isfinite(frequency) or not math.isclose(
            frequency, SSVEP_FREQUENCIES_HZ[slot_index], rel_tol=0.0, abs_tol=0.01
        ):
            raise M19ProtocolError("frequencyHz does not match the frozen slot mapping")
        target_id = _required_text(raw, "targetId")
        if target_id in seen_targets:
            raise M19ProtocolError("TargetIds in active slots must be unique")
        label = raw.get("semanticLabel")
        provenance = raw.get("provenance")
        if label is not None and not isinstance(label, str):
            raise M19ProtocolError("semanticLabel must be a string when present")
        if provenance is not None and not isinstance(provenance, str):
            raise M19ProtocolError("slot provenance must be a string when present")
        slots.append(M19DecodeSlot(slot_index, frequency, target_id, label, provenance))
        seen_indices.add(slot_index)
        seen_targets.add(target_id)

    contract_id = payload.get("contractId")
    quest_utc = payload.get("questUtc")
    provenance = payload.get("provenance")
    presentation_policy = payload.get("presentationPolicy")
    formal_onset = payload.get("formalStimulusOnsetUtc")
    for name, value in (
        ("contractId", contract_id), ("questUtc", quest_utc), ("provenance", provenance),
        ("presentationPolicy", presentation_policy), ("formalStimulusOnsetUtc", formal_onset),
    ):
        if value is not None and not isinstance(value, str):
            raise M19ProtocolError("{} must be a string when present".format(name))
    if contract_id is not None and contract_id != "m19_decode_request_v1":
        raise M19ProtocolError("contractId must be m19_decode_request_v1")
    if presentation_policy not in (None, "", "demo_preview", "research_strict"):
        raise M19ProtocolError("presentationPolicy must be demo_preview or research_strict")
    if presentation_policy == "research_strict" and not formal_onset:
        raise M19ProtocolError("research_strict request must include formalStimulusOnsetUtc")

    request = M19DecodeRequest(
        contract_id, M19_PROTOCOL_VERSION, trial_id, selection_id, page_id,
        page_index, page_epoch, created_utc, tuple(slots), quest_utc, provenance,
        presentation_policy, formal_onset,
    )
    normalized_payload = dict(payload)
    for optional in ("questUtc", "provenance", "presentationPolicy", "formalStimulusOnsetUtc"):
        if normalized_payload.get(optional, "missing") is None and optional not in request.to_payload():
            normalized_payload.pop(optional)
    for index, raw_slot in enumerate(raw_slots):
        normalized_slot = dict(raw_slot)
        for optional in ("semanticLabel", "provenance"):
            if normalized_slot.get(optional, "missing") is None and optional not in request.slots[index].to_payload():
                normalized_slot.pop(optional)
        normalized_payload["slots"][index] = normalized_slot
    if request.to_payload() != normalized_payload:
        # Normalization must not discard fields or alter the frozen mapping.
        raise M19ProtocolError("decode request is not in canonical wire form")
    return request


class M19TrialRegistry:
    """Single-active-trial PC lifecycle with explicit abort and stale rejection."""

    STATE_BROWSE = "BROWSE"
    STATE_WAITING = "WAITING_FOR_DECODER"

    def __init__(self, event_sink=None):
        self._active_request = None
        self._seen_selection_ids = set()
        self._seen_trial_ids = set()
        self._terminal_selection_ids = set()
        self._event_sink = event_sink

    @property
    def state(self):
        return self.STATE_WAITING if self._active_request is not None else self.STATE_BROWSE

    @property
    def active_request(self):
        return self._active_request

    def accept_request(self, payload):
        try:
            request = validate_decode_request(payload)
        except M19ProtocolError as error:
            return self._ack(payload if isinstance(payload, dict) else {}, False, "invalid_request", str(error))
        if request.selection_id in self._seen_selection_ids:
            return self._ack(payload, False, "duplicate_selection_id")
        if request.trial_id in self._seen_trial_ids:
            return self._ack(payload, False, "duplicate_trial_id")
        if self._active_request is not None:
            return self._ack(payload, False, "trial_already_active")

        self._active_request = request
        self._seen_selection_ids.add(request.selection_id)
        self._seen_trial_ids.add(request.trial_id)
        self._emit("decode_request_accepted", {"selectionId": request.selection_id, "pageEpoch": request.page_epoch})
        return self._ack(payload, True, "accepted")

    def abort(self, selection_id, reason="cancelled", trial_id=None, page_id=None, page_epoch=None):
        request = self._active_request
        accepted = request is not None and request.selection_id == selection_id
        if accepted and trial_id is not None:
            accepted = request.trial_id == trial_id
        if accepted and page_id is not None:
            accepted = request.page_id == page_id
        if accepted and page_epoch is not None:
            accepted = request.page_epoch == page_epoch
        if accepted:
            self._active_request = None
            self._terminal_selection_ids.add(selection_id)
            self._emit("decode_aborted", {"selectionId": selection_id, "reason": reason})
        else:
            self._emit("stale_selection_rejected", {
                "selectionId": selection_id,
                "reason": "abort_identity_mismatch_or_no_active_trial",
            })
        return {
            "protocolVersion": M19_PROTOCOL_VERSION,
            "messageType": M19_DECODE_ABORT_ACK,
            "selectionId": selection_id,
            "accepted": accepted,
            "rejectionReason": "None" if accepted else "STALE_SELECTION_REJECTED",
        }

    def resolve(self, selection_id, class_index, decision_made=True, page_id=None, page_epoch=None,
                confidence=None, evidence=None, timing=None):
        request = self._active_request
        if request is None or request.selection_id != selection_id:
            self._emit("stale_selection_rejected", {
                "selectionId": selection_id,
                "reason": "no_matching_active_trial",
            })
            return self._rejected_result(selection_id, "STALE_SELECTION_REJECTED")
        if page_id != request.page_id or page_epoch != request.page_epoch:
            self._active_request = None
            self._terminal_selection_ids.add(selection_id)
            self._emit("stale_selection_rejected", {
                "selectionId": selection_id,
                "reason": "page_identity_mismatch",
                "pageId": page_id,
                "pageEpoch": page_epoch,
            })
            return self._rejected_result(selection_id, "STALE_SELECTION_REJECTED")
        if not isinstance(decision_made, bool):
            self._active_request = None
            self._terminal_selection_ids.add(selection_id)
            self._emit("decode_result_rejected", {
                "selectionId": selection_id,
                "reason": "invalid_decision_made",
            })
            return self._rejected_result(selection_id, "invalid_decision_made")
        if not decision_made:
            if class_index is not None:
                self._active_request = None
                self._terminal_selection_ids.add(selection_id)
                self._emit("decode_result_rejected", {
                    "selectionId": selection_id,
                    "reason": "no_decision_must_not_include_class",
                })
                return self._rejected_result(selection_id, "no_decision_must_not_include_class")
            self._active_request = None
            self._terminal_selection_ids.add(selection_id)
            self._emit("decode_no_decision", {"selectionId": selection_id})
            return {
                "protocolVersion": M19_PROTOCOL_VERSION,
                "messageType": M19_DECODE_RESULT,
                "selectionId": selection_id,
                "trialId": request.trial_id,
                "pageId": request.page_id,
                "pageEpoch": request.page_epoch,
                "decisionMade": False,
                "classIndex": None,
                "slotIndex": None,
                "accepted": False,
                "rejectionReason": "no_decision",
            }
        if isinstance(class_index, bool) or not isinstance(class_index, int) or not 0 <= class_index < 3:
            self._active_request = None
            self._terminal_selection_ids.add(selection_id)
            self._emit("decode_result_rejected", {
                "selectionId": selection_id,
                "reason": "invalid_class_index",
            })
            return self._rejected_result(selection_id, "invalid_class_index")
        slot = request.slot_for_class(class_index)
        if slot is None:
            self._active_request = None
            self._terminal_selection_ids.add(selection_id)
            self._emit("decode_result_rejected", {
                "selectionId": selection_id,
                "reason": "inactive_slot",
                "classIndex": class_index,
            })
            return self._rejected_result(selection_id, "inactive_slot")

        self._active_request = None
        self._terminal_selection_ids.add(selection_id)
        result = {
            "protocolVersion": M19_PROTOCOL_VERSION,
            "messageType": M19_DECODE_RESULT,
            "selectionId": request.selection_id,
            "trialId": request.trial_id,
            "pageId": request.page_id,
            "pageEpoch": request.page_epoch,
            "decisionMade": True,
            "classIndex": class_index,
            "slotIndex": class_index,
            "accepted": True,
        }
        if confidence is not None:
            result["confidence"] = float(confidence)
        if evidence is not None:
            result["evidence"] = evidence
        if timing is not None:
            result["timing"] = dict(timing)
        # Deliberately omit TargetId; Quest resolves it through the frozen trial.
        self._emit("decode_result", dict(result))
        return result

    def _ack(self, payload, accepted, reason, detail=None):
        response = {
            "protocolVersion": M19_PROTOCOL_VERSION,
            "messageType": M19_DECODE_ACK,
            "selectionId": payload.get("selectionId"),
            "trialId": payload.get("trialId"),
            "pageId": payload.get("pageId"),
            "pageEpoch": payload.get("pageEpoch"),
            "accepted": bool(accepted),
            "rejectionReason": "None" if accepted else reason,
        }
        if detail:
            response["detail"] = detail
        return response

    @staticmethod
    def _rejected_result(selection_id, reason):
        return {
            "protocolVersion": M19_PROTOCOL_VERSION,
            "messageType": M19_DECODE_RESULT,
            "selectionId": selection_id,
            "decisionMade": False,
            "accepted": False,
            "rejectionReason": reason,
        }

    def _emit(self, event, payload):
        if callable(self._event_sink):
            self._event_sink(event, dict(payload))


__all__ = [
    "M19DecodeRequest", "M19DecodeSlot", "M19ProtocolError", "M19TrialRegistry",
    "M19_DECODE_ABORT", "M19_DECODE_ABORT_ACK", "M19_DECODE_ACK", "M19_DECODE_REQUEST",
    "M19_DECODE_RESULT", "M19_PROTOCOL_VERSION", "SSVEP_FREQUENCIES_HZ", "validate_decode_request",
]
