"""Pure-software M9 seam from confirmed Quest target IDs to robot requests.

This module deliberately knows neither Quest implementation classes nor MuJoCo
object names. A caller supplies the explicit TargetId-to-logical-block registry;
the eventual simulator adapter owns any logical-ID-to-scene-object resolution.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Optional, Protocol, runtime_checkable


PROTOCOL_VERSION = 1
CONFIRMED_BATCH_MESSAGE = "target_batch_confirmed"
CONFIRMED_BATCH_PROVENANCE = "quest_confirmed_target_batch"
FROZEN_SELECTION_PROVENANCE = "quest_frozen_selection_snapshot"

_LOGICAL_BLOCK_ID = re.compile(r"^block_[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)*_[0-9]+$")


class ConfirmedBatchContractError(ValueError):
    """The payload is not a valid, user-confirmed M8 target batch."""


class IllegalTargetIdError(ConfirmedBatchContractError):
    """A target ID is empty, malformed, or not an exact opaque identifier."""


class DuplicateTargetIdError(ConfirmedBatchContractError):
    """One target or logical block occurs more than once in a confirmed batch."""


class UnknownTargetIdError(ConfirmedBatchContractError):
    """The confirmed TargetId is absent from the caller-supplied registry."""


class InvalidTargetMappingError(ValueError):
    """The explicit TargetId-to-logical-block registry is invalid."""


def _required_identifier(value, field):
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ConfirmedBatchContractError("{} must be a non-empty exact identifier".format(field))
    return value


def _target_id(value):
    try:
        return _required_identifier(value, "targetId")
    except ConfirmedBatchContractError as error:
        raise IllegalTargetIdError(str(error)) from error


def validate_logical_block_id(value):
    """Return a canonical logical block ID or reject it without normalization."""
    if not isinstance(value, str) or _LOGICAL_BLOCK_ID.fullmatch(value) is None:
        raise InvalidTargetMappingError(
            "logical block ID must match block_<name>_<number>, for example block_red_01"
        )
    return value


def _required_integer(value, field, minimum, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            expected = "an integer >= {}".format(minimum)
        else:
            expected = "an integer from {} through {}".format(minimum, maximum)
        raise ConfirmedBatchContractError("{} must be {}".format(field, expected))
    return value


def _validated_target_mapping(target_id_to_logical_block_id):
    if not isinstance(target_id_to_logical_block_id, Mapping) or not target_id_to_logical_block_id:
        raise InvalidTargetMappingError("an explicit non-empty TargetId mapping is required")

    validated = {}
    for target_id, logical_block_id in target_id_to_logical_block_id.items():
        target_id = _target_id(target_id)
        logical_block_id = validate_logical_block_id(logical_block_id)
        if target_id in validated:
            raise InvalidTargetMappingError("duplicate TargetId in mapping: {!r}".format(target_id))
        validated[target_id] = logical_block_id
    return validated


@dataclass(frozen=True)
class RobotObjectRequest:
    """Logical selection plus source provenance presented at the adapter seam.

    World coordinates and simulator-specific names are intentionally absent.
    This record conveys object identity, not a robot action or completion claim.
    """

    logical_block_id: str
    batch_id: str
    group_id: str
    group_index: int
    submitted_utc: str
    selection_id: str
    source_target_id: str
    predicted_class_index: int
    slot_index: int
    semantic_label: Optional[str]
    resolved_utc: str
    batch_provenance: str
    selection_provenance: str

    def __post_init__(self):
        validate_logical_block_id(self.logical_block_id)
        _required_identifier(self.batch_id, "batchId")
        _required_identifier(self.group_id, "groupId")
        _required_identifier(self.selection_id, "selectionId")
        _target_id(self.source_target_id)
        _required_identifier(self.submitted_utc, "submittedUtc")
        _required_identifier(self.resolved_utc, "resolvedUtc")
        _required_integer(self.group_index, "groupIndex", 0)
        _required_integer(self.predicted_class_index, "predictedClassIndex", 0, 2)
        _required_integer(self.slot_index, "slotIndex", 0, 2)
        if self.semantic_label is not None and not isinstance(self.semantic_label, str):
            raise ConfirmedBatchContractError("semanticLabel must be a string when present")
        if self.batch_provenance != CONFIRMED_BATCH_PROVENANCE:
            raise ConfirmedBatchContractError("unexpected confirmed-batch provenance")
        if self.selection_provenance != FROZEN_SELECTION_PROVENANCE:
            raise ConfirmedBatchContractError("unexpected frozen-selection provenance")


@runtime_checkable
class RobotAdapter(Protocol):
    """Port that accepts logical identity without exposing simulator internals."""

    def accept_object_request(self, request: RobotObjectRequest) -> None:
        """Receive an object-identity request; this does not imply robot motion."""
        ...


def confirmed_batch_to_robot_requests(message, target_id_to_logical_block_id):
    """Convert one actual M8 `target_batch_confirmed` JSON message to requests.

    The explicit mapping is configuration owned by the caller. Selection order
    is preserved. Repeated targets, selections, slots, or logical block IDs are
    rejected instead of silently selecting or dispatching the same object twice.
    """
    if not isinstance(message, Mapping):
        raise ConfirmedBatchContractError("message must be an object")
    if type(message.get("protocolVersion")) is not int or message.get("protocolVersion") != PROTOCOL_VERSION:
        raise ConfirmedBatchContractError("protocolVersion must be 1")
    if message.get("messageType") != CONFIRMED_BATCH_MESSAGE:
        raise ConfirmedBatchContractError("messageType must be target_batch_confirmed")

    batch = message.get("confirmedBatch")
    if not isinstance(batch, Mapping):
        raise ConfirmedBatchContractError("confirmedBatch must be an object")
    batch_id = _required_identifier(batch.get("batchId"), "confirmedBatch.batchId")
    envelope_batch_id = _required_identifier(message.get("batchId"), "batchId")
    if envelope_batch_id != batch_id:
        raise ConfirmedBatchContractError("envelope and confirmedBatch batchId do not match")
    group_id = _required_identifier(batch.get("groupId"), "groupId")
    group_index = _required_integer(batch.get("groupIndex"), "groupIndex", 0)
    submitted_utc = _required_identifier(batch.get("submittedUtc"), "submittedUtc")
    if batch.get("provenance") != CONFIRMED_BATCH_PROVENANCE:
        raise ConfirmedBatchContractError("batch was not confirmed by the Quest batch boundary")

    selections = batch.get("selections")
    if not isinstance(selections, list) or not 1 <= len(selections) <= 3:
        raise ConfirmedBatchContractError("confirmed batch must contain 1-3 selections")

    target_mapping = _validated_target_mapping(target_id_to_logical_block_id)
    seen_selection_ids = set()
    seen_target_ids = set()
    seen_slots = set()
    seen_logical_ids = set()
    requests = []

    for index, selection in enumerate(selections):
        if not isinstance(selection, Mapping):
            raise ConfirmedBatchContractError("selection[{}] must be an object".format(index))
        selection_id = _required_identifier(selection.get("selectionId"), "selectionId")
        target_id = _target_id(selection.get("targetId"))
        predicted_class_index = _required_integer(
            selection.get("predictedClassIndex"), "predictedClassIndex", 0, 2
        )
        slot_index = _required_integer(selection.get("slotIndex"), "slotIndex", 0, 2)
        resolved_utc = _required_identifier(selection.get("resolvedUtc"), "resolvedUtc")
        semantic_label = selection.get("semanticLabel")
        if semantic_label is not None and not isinstance(semantic_label, str):
            raise ConfirmedBatchContractError("semanticLabel must be a string when present")
        selection_provenance = selection.get("provenance")
        if selection_provenance != FROZEN_SELECTION_PROVENANCE:
            raise ConfirmedBatchContractError("selection was not resolved from a frozen Quest snapshot")

        if selection_id in seen_selection_ids:
            raise ConfirmedBatchContractError("duplicate selectionId: {!r}".format(selection_id))
        if target_id in seen_target_ids:
            raise DuplicateTargetIdError("duplicate targetId: {!r}".format(target_id))
        if slot_index in seen_slots:
            raise ConfirmedBatchContractError("duplicate slotIndex: {}".format(slot_index))
        if target_id not in target_mapping:
            raise UnknownTargetIdError("TargetId is not registered: {!r}".format(target_id))
        logical_block_id = target_mapping[target_id]
        if logical_block_id in seen_logical_ids:
            raise DuplicateTargetIdError(
                "multiple selections resolve to logical block: {!r}".format(logical_block_id)
            )

        seen_selection_ids.add(selection_id)
        seen_target_ids.add(target_id)
        seen_slots.add(slot_index)
        seen_logical_ids.add(logical_block_id)
        requests.append(
            RobotObjectRequest(
                logical_block_id=logical_block_id,
                batch_id=batch_id,
                group_id=group_id,
                group_index=group_index,
                submitted_utc=submitted_utc,
                selection_id=selection_id,
                source_target_id=target_id,
                predicted_class_index=predicted_class_index,
                slot_index=slot_index,
                semantic_label=semantic_label,
                resolved_utc=resolved_utc,
                batch_provenance=batch["provenance"],
                selection_provenance=selection_provenance,
            )
        )

    return tuple(requests)
