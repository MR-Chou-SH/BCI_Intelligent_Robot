"""Compose the frozen M8 batch receipt with the M9 execution adapter.

M8 remains responsible for wire validation, batchId deduplication, and ACKs.
This module adds the M9-only TargetId/logical-ID conversion and process-lifetime
selectionId deduplication immediately before robot execution.
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from uuid import uuid4

from integration.m8_selection_transport.simulated_batch_consumer import (
    BatchConsumerReceipt,
)
from integration.m9_mujoco_execution import (
    RobotExecutionResult,
    RobotOperation,
    create_execution_requests,
)
from integration.m9_robot_adapter import (
    ConfirmedBatchContractError,
    InvalidTargetMappingError,
    UnknownTargetIdError,
)


@dataclass(frozen=True)
class M9ExecutionAttempt:
    """One accepted selection's result, without simulator-internal identity."""

    request_id: str
    selection_id: str
    target_id: str
    logical_block_id: str
    result: Optional[RobotExecutionResult]
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None

    @property
    def success(self):
        return self.result is not None and self.result.success

    def to_public_dict(self):
        result = self.result
        return {
            "requestId": self.request_id,
            "selectionId": self.selection_id,
            "targetId": self.target_id,
            "logicalBlockId": self.logical_block_id,
            "success": self.success,
            "simulatorObjectResolution": (
                self._resolution_state(result)
            ),
            "executionState": "completed" if self.success else "failed",
            "failureCode": (
                result.failure_code if result is not None else self.failure_code
            ),
            "failureReason": (
                result.failure_reason if result is not None else self.failure_reason
            ),
            "executionProvenance": (
                result.execution_provenance if result is not None else None
            ),
            "backendExecutionId": (
                result.backend_execution_id if result is not None else None
            ),
            "startedUtc": result.started_utc if result is not None else None,
            "completedUtc": result.completed_utc if result is not None else None,
        }

    @staticmethod
    def _resolution_state(result):
        if result is None:
            return "not_attempted"
        if result.failure_code in {
            "invalid_logical_block_id",
            "unknown_logical_block",
            "missing_simulator_object",
            "scene_lookup_failed",
        }:
            return "failed"
        return "resolved"


@dataclass(frozen=True)
class M9BatchDispatchResult:
    """Structured outcome for one M8 receipt and its downstream M9 dispatch."""

    batch_id: Optional[str]
    accepted: bool
    duplicate_batch: bool
    accepted_selection_ids: Tuple[str, ...] = ()
    duplicate_selection_ids: Tuple[str, ...] = ()
    executions: Tuple[M9ExecutionAttempt, ...] = ()
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None

    @property
    def success(self):
        return (
            self.accepted
            and bool(self.executions)
            and all(attempt.success for attempt in self.executions)
        )

    @property
    def state(self):
        if self.duplicate_batch or (self.accepted and not self.accepted_selection_ids):
            return "duplicate"
        return "accepted" if self.accepted else "rejected"

    def to_public_dict(self):
        return {
            "batchId": self.batch_id,
            "accepted": self.accepted,
            "duplicateBatch": self.duplicate_batch,
            "state": self.state,
            "acceptedSelectionIds": list(self.accepted_selection_ids),
            "duplicateSelectionIds": list(self.duplicate_selection_ids),
            "failureCode": self.failure_code,
            "failureReason": self.failure_reason,
            "executions": [attempt.to_public_dict() for attempt in self.executions],
        }


class M9BatchDispatcher:
    """Dispatch each M8 batch and selection at most once per process lifetime."""

    def __init__(
        self,
        target_id_to_logical_block_id,
        robot_adapter,
        requested_operation=RobotOperation.PICK_AND_PLACE,
        request_id_factory=None,
    ):
        if not callable(getattr(robot_adapter, "execute", None)):
            raise TypeError("robot_adapter must provide execute(request)")
        if not isinstance(requested_operation, RobotOperation):
            raise ValueError("requested_operation must be an explicit RobotOperation")
        if request_id_factory is None:
            request_id_factory = lambda: uuid4().hex
        if not callable(request_id_factory):
            raise ValueError("request_id_factory must be callable")
        self._target_mapping = target_id_to_logical_block_id
        self._robot_adapter = robot_adapter
        self._operation = requested_operation
        self._request_id_factory = request_id_factory
        self._seen_selection_ids = set()

    def dispatch(self, receipt):
        """Validate and execute a receipt; the M8 receiver has already ACKed it."""
        if not isinstance(receipt, BatchConsumerReceipt):
            raise TypeError("receipt must be an M8 BatchConsumerReceipt")
        batch_id = receipt.batch.get("batchId") if isinstance(receipt.batch, dict) else None
        if not receipt.downstream_accepted:
            return M9BatchDispatchResult(
                batch_id=batch_id,
                accepted=False,
                duplicate_batch=True,
            )

        try:
            requests = create_execution_requests(
                receipt.payload,
                self._target_mapping,
                self._operation,
                request_id_factory=self._request_id_factory,
            )
        except UnknownTargetIdError as error:
            return self._rejected(batch_id, "unknown_target", str(error))
        except InvalidTargetMappingError as error:
            return self._rejected(batch_id, "invalid_target_mapping", str(error))
        except ConfirmedBatchContractError as error:
            return self._rejected(batch_id, "invalid_confirmed_batch", str(error))
        except (TypeError, ValueError) as error:
            return self._rejected(batch_id, "invalid_execution_request", str(error))

        fresh_requests = []
        duplicate_selection_ids = []
        for request in requests:
            selection_id = request.selection.selection_id
            if selection_id in self._seen_selection_ids:
                duplicate_selection_ids.append(selection_id)
            else:
                self._seen_selection_ids.add(selection_id)
                fresh_requests.append(request)

        attempts = []
        for request in fresh_requests:
            selection = request.selection
            try:
                result = self._robot_adapter.execute(request)
            except Exception as error:
                attempts.append(
                    M9ExecutionAttempt(
                        request_id=request.request_id,
                        selection_id=selection.selection_id,
                        target_id=selection.source_target_id,
                        logical_block_id=selection.logical_block_id,
                        result=None,
                        failure_code="robot_adapter_exception",
                        failure_reason="robot adapter raised {}".format(type(error).__name__),
                    )
                )
                continue

            if not isinstance(result, RobotExecutionResult):
                attempts.append(
                    M9ExecutionAttempt(
                        request_id=request.request_id,
                        selection_id=selection.selection_id,
                        target_id=selection.source_target_id,
                        logical_block_id=selection.logical_block_id,
                        result=None,
                        failure_code="invalid_robot_adapter_result",
                        failure_reason="robot adapter returned an unsupported result type",
                    )
                )
                continue
            attempts.append(
                M9ExecutionAttempt(
                    request_id=request.request_id,
                    selection_id=selection.selection_id,
                    target_id=selection.source_target_id,
                    logical_block_id=selection.logical_block_id,
                    result=result,
                )
            )

        return M9BatchDispatchResult(
            batch_id=batch_id,
            accepted=True,
            duplicate_batch=False,
            accepted_selection_ids=tuple(
                request.selection.selection_id for request in fresh_requests
            ),
            duplicate_selection_ids=tuple(duplicate_selection_ids),
            executions=tuple(attempts),
        )

    @staticmethod
    def _rejected(batch_id, code, reason):
        return M9BatchDispatchResult(
            batch_id=batch_id,
            accepted=False,
            duplicate_batch=False,
            failure_code=code,
            failure_reason=" ".join(str(reason).split()),
        )
