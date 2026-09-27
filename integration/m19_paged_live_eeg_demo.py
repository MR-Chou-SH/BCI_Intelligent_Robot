"""Opt-in M19 M16 PagedQueueV1 + user-triggered EEG runtime.

Examples::

    python -m integration.m19_paged_live_eeg_demo --mode preflight
    python -m integration.m19_paged_live_eeg_demo --mode synthetic-e2e
    python -m integration.m19_paged_live_eeg_demo --mode historical-e2e --historical-session PATH
    python -m integration.m19_paged_live_eeg_demo --mode serve --eeg-source synthetic

The live-nd8 branch is only opened when ``--confirm-live-human`` is present.
This launcher never derives a TargetId from the decoded class; Quest owns the
frozen page mapping and returns its normal M8 selection ACK.
"""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid

from integration.m16_paged_queue import Candidate, PagedSelectionQueue, confirmed_batch_payloads
from integration.m8_selection_orchestration import QuestSelectionTcpServer
from integration.m8_selection_transport.simulated_batch_consumer import BatchIdempotentConsumer
from integration.m19_eeg_backends import (
    M19BackendError,
    M19HistoricalEegBackend,
    M19LiveNd8Backend,
    M19SyntheticEegBackend,
    sha256_file,
)
from integration.m19_paged_live_eeg import M19TrialRegistry
from integration.m9_batch_dispatch import M9BatchDispatcher
from integration.m19_mujoco_rpc import M19RemoteMujocoBatchDispatcher
from integration.m9_mujoco_execution import (
    BackendExecutionOutcome,
    DEFAULT_M9_SCENE_BINDINGS,
    MujocoRobotExecutionAdapter,
    RobotOperation,
    SceneBindingRegistry,
    create_fr3_umi_mujoco_adapter,
)
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _safe_json(value):
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_json(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class M19AppendOnlyEventLog:
    """Durable JSONL M19 event sink; existing records are only ever appended."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        if self.path.is_file():
            with self.path.open("r", encoding="utf-8") as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as error:
                        raise ValueError("existing M19 event log is invalid at line {}".format(line_number)) from error
                    if isinstance(record, dict) and isinstance(record.get("sequence"), int):
                        self.sequence = max(self.sequence, record["sequence"] + 1)
        self._stream = self.path.open("a", encoding="utf-8", newline="\n")

    def emit(self, event_type, **values):
        record = {
            "recordType": "m19_runtime_event",
            "schemaVersion": 1,
            "sequence": self.sequence,
            "eventType": str(event_type),
            "utcTimestamp": _utc_now(),
            "monotonicNs": time.monotonic_ns(),
            **_safe_json(values),
        }
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False)
        self._stream.write(encoded + "\n")
        self._stream.flush()
        import os
        os.fsync(self._stream.fileno())
        self.sequence += 1
        return record

    def close(self):
        if not self._stream.closed:
            self._stream.close()


class M19TelemetryBoundRobotAdapter:
    """Preserve M13.6 frame emission and associate it with each M9 selection."""

    def __init__(self, adapter, telemetry_callback, event_sink=None):
        if not callable(getattr(adapter, "execute", None)):
            raise TypeError("adapter must implement execute(request)")
        if not callable(telemetry_callback):
            raise TypeError("telemetry_callback must be callable")
        self.adapter = adapter
        self.telemetry_callback = telemetry_callback
        self.event_sink = event_sink

    def execute(self, request):
        self._emit(request, "executing")
        result = self.adapter.execute(request)
        self._emit(request, "completed" if result.success else "execution_failed")
        return result

    def _emit(self, request, state):
        frame = self.telemetry_callback(request, state)
        values = {
            "requestId": request.request_id,
            "selectionId": request.selection.selection_id,
            "targetId": request.selection.source_target_id,
            "logicalBlockId": request.logical_block_id,
            "simulationState": state,
            "telemetrySequence": frame.get("sequence") if isinstance(frame, dict) else None,
            "telemetryFrame": frame,
        }
        if callable(getattr(self.event_sink, "emit", None)):
            self.event_sink.emit("robot_telemetry_association", **values)
        elif callable(self.event_sink):
            self.event_sink("robot_telemetry_association", values)


class M19RealtimeTelemetryPacer:
    """Pace existing MuJoCo steps and mirror latest state at the Quest's 30 Hz visual rate."""

    def __init__(self, simulation_data, telemetry, rate_hz=30.0, monotonic=None, sleep=None):
        if simulation_data is None or not hasattr(simulation_data, "time"):
            raise TypeError("simulation_data must expose MuJoCo simulation time")
        if not callable(getattr(telemetry, "emit", None)):
            raise TypeError("telemetry must provide emit(simulation_state)")
        if not isinstance(rate_hz, (int, float)) or isinstance(rate_hz, bool):
            raise TypeError("rate_hz must be a finite positive number")
        if not math.isfinite(float(rate_hz)) or rate_hz <= 0.0:
            raise ValueError("rate_hz must be a finite positive number")
        self._simulation_data = simulation_data
        self._telemetry = telemetry
        self._interval_seconds = 1.0 / float(rate_hz)
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self._start_simulation_time = None
        self._start_wall_time = None
        self._next_emit_simulation_time = None
        self._last_simulation_time = None

    def on_simulation_step(self):
        simulation_time = float(self._simulation_data.time)
        if not math.isfinite(simulation_time):
            raise ValueError("MuJoCo simulation time must remain finite")
        now = float(self._monotonic())
        if (self._start_simulation_time is None or
                simulation_time + 1e-9 < self._last_simulation_time):
            self._start_simulation_time = simulation_time
            self._start_wall_time = now
            self._next_emit_simulation_time = simulation_time + self._interval_seconds
            self._last_simulation_time = simulation_time
            return

        self._last_simulation_time = simulation_time
        if simulation_time + 1e-9 < self._next_emit_simulation_time:
            return

        target_wall_time = self._start_wall_time + (
            self._next_emit_simulation_time - self._start_simulation_time
        )
        delay = target_wall_time - now
        if delay > 0.0:
            self._sleep(delay)

        # The existing Quest receiver interpolates each latest-state pair over
        # 1/30 s. Do not enqueue missed samples when MuJoCo runs ahead: sample
        # only the current state and advance to the next future simulation tick.
        self._telemetry.emit("executing")
        elapsed_after_tick = max(0.0, simulation_time - self._next_emit_simulation_time)
        skipped_intervals = int(math.floor(elapsed_after_tick / self._interval_seconds)) + 1
        self._next_emit_simulation_time += skipped_intervals * self._interval_seconds


class M19PagedLiveOrchestrator:
    """Serve M19 trigger/abort events and existing M9 confirmed batches."""

    def __init__(self, transport, eeg_backend, event_sink=None, robot_dispatcher=None):
        self.transport = transport
        self.eeg_backend = eeg_backend
        self.event_sink = event_sink
        self.robot_dispatcher = robot_dispatcher
        self.registry = M19TrialRegistry(event_sink=self._registry_event)
        self.batch_consumer = BatchIdempotentConsumer()

    def _emit(self, event_type, **values):
        sink = self.event_sink
        if callable(getattr(sink, "emit", None)):
            sink.emit(event_type, **values)
        elif callable(sink):
            sink(event_type, dict(values))

    def _registry_event(self, event_type, payload):
        self._emit(event_type, **payload)

    def poll_once(self, timeout_seconds=0.0):
        events = self.transport.poll_controller_events(timeout_seconds)
        for event in events:
            message_type = event.get("messageType") if isinstance(event, dict) else None
            if message_type == "eeg_decode_request":
                self._handle_decode_request(event)
            elif message_type == "eeg_decode_abort":
                self._handle_decode_abort(event)
            elif message_type == "selection_undo":
                self._emit("selection_undo", payload=event)
            elif message_type == "target_batch_confirmed":
                self._handle_confirmed_batch(event)
            else:
                self._emit("unsupported_quest_event", payload=event)
        return len(events)

    def _handle_decode_request(self, payload):
        ack = self.registry.accept_request(payload)
        selection_id = ack.get("selectionId")
        if isinstance(selection_id, str) and selection_id and selection_id == selection_id.strip():
            self.transport.send_m19_decode_ack(ack)
        self._emit("decode_request_received", request=payload, accepted=ack["accepted"])
        self._emit("decode_request_acknowledged", ack=ack)
        if not ack["accepted"]:
            return
        request = self.registry.active_request
        if request is None:
            self._emit("stale_selection_rejected", selectionId=ack.get("selectionId"),
                       reason="accepted_request_missing_from_registry")
            return

        try:
            decoded = self.eeg_backend.decode(self.registry.active_request)
        except Exception as error:
            decoded = {
                "decisionMade": False,
                "classIndex": None,
                "evidence": {
                    "backend": getattr(self.eeg_backend, "name", type(self.eeg_backend).__name__),
                    "failureType": type(error).__name__,
                    "failureReason": " ".join(str(error).split()),
                },
            }
            self._emit("decode_backend_failed_closed", selectionId=request.selection_id,
                       failureType=type(error).__name__, failureReason=str(error))

        result = self.registry.resolve(
            request.selection_id,
            decoded.get("classIndex"),
            decision_made=decoded.get("decisionMade"),
            page_id=request.page_id,
            page_epoch=request.page_epoch,
            confidence=decoded.get("confidence"),
            evidence=decoded.get("evidence"),
            timing=decoded.get("timing"),
        )
        self._emit("decode_result", result=result, decoder=decoded)
        if result.get("accepted"):
            try:
                quest_ack = self.transport.submit_m19_eeg_selection(result)
                self._emit("selection_acknowledged", selectionId=request.selection_id, ack=quest_ack)
                if not quest_ack.get("accepted"):
                    self._emit("stale_selection_rejected", selectionId=request.selection_id,
                               reason="quest_rejected_late_or_invalid_class", ack=quest_ack)
            except Exception as error:
                self._emit("selection_transport_failed", selectionId=request.selection_id,
                           failureType=type(error).__name__, failureReason=str(error))
        else:
            close_result = dict(result)
            close_result.update({
                "messageType": "eeg_decode_result",
                "trialId": request.trial_id,
                "pageId": request.page_id,
                "pageEpoch": request.page_epoch,
                "decisionMade": False,
                "classIndex": None,
                "accepted": False,
            })
            try:
                quest_ack = self.transport.submit_m19_no_decision(close_result)
                self._emit("selection_aborted_without_decision", selectionId=request.selection_id,
                           rejectionReason=result.get("rejectionReason"), ack=quest_ack)
            except Exception as error:
                self._emit("selection_abort_transport_failed", selectionId=request.selection_id,
                           failureType=type(error).__name__, failureReason=str(error))

    def _handle_decode_abort(self, payload):
        abort_ack = self.registry.abort(
            payload.get("selectionId"),
            payload.get("abortReason", "cancelled"),
            trial_id=payload.get("trialId"),
            page_id=payload.get("pageId"),
            page_epoch=payload.get("pageEpoch"),
        )
        self.transport.send_m19_decode_abort_ack(abort_ack)
        self._emit("decode_abort_acknowledged", request=payload, ack=abort_ack)

    def _handle_confirmed_batch(self, payload):
        try:
            receipt = self.batch_consumer.accept(payload)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            self._emit("confirmed_batch_rejected", failureType=type(error).__name__, failureReason=str(error),
                       batchId=payload.get("batchId"))
            return
        self._emit("target_batch_confirmed", batch=receipt.batch,
                   downstreamAccepted=receipt.downstream_accepted, ackSentByQuestSelectionServer=True)
        if self.robot_dispatcher is None:
            self._emit("robot_dispatch_skipped", batchId=receipt.batch.get("batchId"),
                       reason="m9_dispatcher_not_configured")
            return
        result = self.robot_dispatcher.dispatch(receipt)
        self._emit("m9_batch_dispatch", result=result.to_public_dict(),
                   requestedSelectionOrder=[item.get("selectionId") for item in receipt.batch.get("selections", [])])
        for attempt in result.executions:
            self._emit("robot_execution_feedback", attempt=attempt.to_public_dict())

    def run_forever(self, poll_interval_seconds=0.1):
        try:
            while True:
                self.poll_once(poll_interval_seconds)
        except KeyboardInterrupt:
            self._emit("operator_stop", reason="keyboard_interrupt")


class M19InProcessQuestSession:
    """Deterministic Quest-side queue harness used by software acceptance."""

    def __init__(self, queue, eeg_backend, event_sink=None, selection_prefix="m19-e2e-selection"):
        self.queue = queue
        self.eeg_backend = eeg_backend
        self.event_sink = event_sink
        self.registry = M19TrialRegistry(event_sink=self._registry_event)
        self.selection_prefix = selection_prefix
        self._trial_count = 0
        self._results = []

    def _emit(self, event_type, **values):
        if callable(getattr(self.event_sink, "emit", None)):
            self.event_sink.emit(event_type, **values)
        elif callable(self.event_sink):
            self.event_sink(event_type, dict(values))

    def _registry_event(self, event_type, payload):
        self._emit(event_type, **payload)

    def trigger(self):
        self._trial_count += 1
        selection_id = "{}-{:02d}".format(self.selection_prefix, self._trial_count)
        trial_id = "m19-e2e-trial-{:02d}".format(self._trial_count)
        trial = self.queue.open_trial(selection_id)
        if trial is None:
            raise M19BackendError("Quest queue refused a user trigger")
        labels_by_target = {
            item.target_id: item.label for item in self.queue.current_page.candidates
        }
        slots = [
            {
                "slotIndex": candidate.slot_index,
                "frequencyHz": candidate.nominal_frequency_hz,
                "targetId": candidate.target_id,
                "semanticLabel": labels_by_target.get(candidate.target_id, ""),
                "provenance": "m16_frozen_page_snapshot",
            }
            for candidate in trial.candidates if candidate.active
        ]
        request_payload = {
            "contractId": "m19_decode_request_v1",
            "protocolVersion": 1,
            "messageType": "eeg_decode_request",
            "trialId": trial_id,
            "selectionId": selection_id,
            "pageId": trial.page_id,
            "pageIndex": self.queue.page_index,
            "pageEpoch": trial.page_epoch,
            "createdUtc": _utc_now(),
            "activeSlotCount": len(slots),
            "slots": slots,
            "presentationPolicy": "demo_preview",
        }
        ack = self.registry.accept_request(request_payload)
        self._emit("trigger_snapshot_frozen", request=request_payload, ack=ack)
        if not ack["accepted"]:
            self.queue.cancel_trial(selection_id, "pc_rejected_request")
            return {"accepted": False, "ack": ack, "request": request_payload}

        request = self.registry.active_request
        try:
            decoded = self.eeg_backend.decode(request)
        except Exception as error:
            decoded = {"decisionMade": False, "classIndex": None,
                       "evidence": {"failureType": type(error).__name__, "failureReason": str(error)}}
        result = self.registry.resolve(
            selection_id,
            decoded.get("classIndex"),
            decision_made=decoded.get("decisionMade"),
            page_id=request.page_id,
            page_epoch=request.page_epoch,
            confidence=decoded.get("confidence"),
            evidence=decoded.get("evidence"),
        )
        if result.get("accepted"):
            frozen_candidate = trial.candidate_for_slot(result["classIndex"])
            local_queue_result = self.queue.accept_result(
                selection_id,
                result["classIndex"],
                target_id=frozen_candidate.target_id,
                page_epoch=trial.page_epoch,
            )
            if not local_queue_result.accepted:
                self._emit("stale_selection_rejected", selectionId=selection_id,
                           reason=local_queue_result.reason)
            else:
                self._emit("queue_selection_committed", selectionId=selection_id,
                           targetId=local_queue_result.entry.target_id,
                           classIndex=result["classIndex"], queue=self.queue.selected_target_ids)
        else:
            self.queue.cancel_trial(selection_id, result.get("rejectionReason", "no_decision"))
            if result.get("rejectionReason") != "no_decision":
                self._emit("stale_selection_rejected", selectionId=selection_id,
                           reason=result.get("rejectionReason"))
        entry = {
            "request": request_payload,
            "ack": ack,
            "decoder": decoded,
            "result": result,
            "selectedTargetIds": self.queue.selected_target_ids,
        }
        self._results.append(entry)
        return entry

    def _cancel_trial_before_queue_action(self, reason):
        trial = self.queue.current_trial
        if trial is None:
            return
        request = self.registry.active_request
        abort_ack = self.registry.abort(
            trial.selection_id,
            reason,
            trial_id=None if request is None else request.trial_id,
            page_id=trial.page_id,
            page_epoch=trial.page_epoch,
        )
        self.queue.cancel_trial(trial.selection_id, reason)
        self._emit("trial_aborted_before_queue_action", selectionId=trial.selection_id,
                   reason=reason, abortAck=abort_ack)

    def navigate_next(self):
        self._cancel_trial_before_queue_action("page_changed")
        changed = self.queue.navigate_next()
        if changed:
            self._emit("page_navigation", pageIndex=self.queue.page_index,
                       pageId=self.queue.current_page.page_id, pageEpoch=self.queue.page_epoch)
        return changed

    def navigate_previous(self):
        self._cancel_trial_before_queue_action("page_changed")
        changed = self.queue.navigate_previous()
        if changed:
            self._emit("page_navigation", pageIndex=self.queue.page_index,
                       pageId=self.queue.current_page.page_id, pageEpoch=self.queue.page_epoch)
        return changed

    def undo_last(self):
        self._cancel_trial_before_queue_action("undo")
        entry = self.queue.undo_last()
        self._emit("queue_undo", targetId=None if entry is None else entry.target_id,
                   queue=self.queue.selected_target_ids)
        return entry

    def submit(self):
        self._cancel_trial_before_queue_action("submit")
        result = self.queue.submit()
        self._emit("queue_submit", accepted=result.accepted, reason=result.reason,
                   orderedTargets=() if result.plan is None else tuple(
                       entry.target_id for entry in result.plan.ordered_selections
                   ))
        return result


class _SyntheticM9Backend:
    def __init__(self, event_sink=None):
        self.calls = []
        self.event_sink = event_sink

    def execute(self, request, simulator_object):
        self.calls.append({
            "selectionId": request.selection.selection_id,
            "targetId": request.selection.source_target_id,
            "logicalBlockId": request.logical_block_id,
            "slotIndex": request.selection.slot_index,
            "simulatorObjectId": simulator_object.simulator_object_id,
        })
        return BackendExecutionOutcome(
            success=True,
            execution_provenance="m19_synthetic_m9_dispatch_acceptance",
            backend_execution_id="m19-fake-execution-{:02d}".format(len(self.calls)),
        )


def _new_subdirectory(parent, name):
    parent = Path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / name
    index = 1
    while candidate.exists():
        candidate = parent / (name + "_attempt_{}".format(index))
        index += 1
    candidate.mkdir()
    return candidate


def run_synthetic_acceptance(output_root):
    run_dir = _new_subdirectory(output_root, "synthetic-e2e")
    event_log = M19AppendOnlyEventLog(run_dir / "m19-events.jsonl")
    mapping = load_virtual_block_target_mapping()
    target_order = (
        "m9-vblock-yellow-01",
        "m9-vblock-blue-01",
        "m9-vblock-green-01",
        "m9-vblock-red-01",
    )
    candidates = tuple(Candidate(mapping[target_id], target_id, target_id.rsplit("-", 2)[1].title())
                       for target_id in target_order)
    queue = PagedSelectionQueue(
        candidates,
        selection_id_factory=(lambda sequence=iter(range(1, 100)): "m16-e2e-selection-{:02d}".format(next(sequence))),
        event_sink=lambda name, payload: event_log.emit("queue_" + name, payload=payload),
    )
    session = M19InProcessQuestSession(
        queue,
        M19SyntheticEegBackend((1, 0, 0, 2)),
        event_sink=event_log,
    )
    backend = _SyntheticM9Backend()
    scene_lookup = {name: index for index, name in enumerate(DEFAULT_M9_SCENE_BINDINGS.values())}
    adapter = MujocoRobotExecutionAdapter(
        SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
        lambda name: scene_lookup.get(name, -1),
        backend,
    )
    telemetry_records = []

    def telemetry_callback(request, state):
        frame = {
            "sequence": len(telemetry_records),
            "messageType": "m13_6_robot_state",
            "simulationState": state,
        }
        telemetry_records.append({
            "selectionId": request.selection.selection_id,
            "targetId": request.selection.source_target_id,
            "logicalBlockId": request.logical_block_id,
            "state": state,
            "frame": frame,
        })
        return frame

    telemetry_adapter = M19TelemetryBoundRobotAdapter(adapter, telemetry_callback, event_sink=event_log)
    dispatcher = M9BatchDispatcher(
        mapping,
        telemetry_adapter,
        requested_operation=RobotOperation.PICK_AND_PLACE,
        request_id_factory=(lambda sequence=iter(range(1, 100)): "m19-e2e-request-{:02d}".format(next(sequence))),
    )

    page_one_first = session.trigger()
    session.navigate_next()
    page_two = session.trigger()
    session.navigate_previous()
    selected_persistence = tuple(
        item.target_id for item in queue.current_page.candidates if item.selected
    )
    page_one_yellow = session.trigger()
    undone = session.undo_last()
    page_one_green = session.trigger()
    submission = session.submit()
    if not submission.accepted or submission.plan is None:
        event_log.close()
        raise RuntimeError("synthetic M19 queue did not submit")

    payloads = confirmed_batch_payloads(submission.plan, batch_id_prefix="m19-e2e-batch")
    dispatch_results = []
    batch_consumer = BatchIdempotentConsumer()
    for payload in payloads:
        receipt = batch_consumer.accept(payload)
        result = dispatcher.dispatch(receipt)
        dispatch_results.append(result)
        event_log.emit("m9_batch_dispatch", payload=payload, result=result.to_public_dict())

    expected_order = (
        "m9-vblock-blue-01",
        "m9-vblock-red-01",
        "m9-vblock-green-01",
    )
    executed_order = tuple(item["targetId"] for item in backend.calls)
    if selected_persistence != ("m9-vblock-blue-01",):
        raise AssertionError("selected Blue did not remain selected across the page switch")
    if page_one_first["result"].get("classIndex") != 1 or page_two["result"].get("classIndex") != 0:
        raise AssertionError("the first two synthetic classes did not resolve through their current pages")
    if page_one_yellow["result"].get("classIndex") != 0 or undone is None or undone.target_id != "m9-vblock-yellow-01":
        raise AssertionError("Undo Last did not restore the most recent Yellow selection")
    if page_one_green["result"].get("classIndex") != 2:
        raise AssertionError("the final synthetic class did not resolve to current Page 1 slot 2")
    if queue.selected_target_ids != expected_order or executed_order != expected_order:
        raise AssertionError("M19 queue or M9 dispatch changed free user order")
    if len(dispatch_results) != 1 or not dispatch_results[0].success:
        raise AssertionError("M9 synthetic Mjoco adapter did not accept the committed batch")
    if tuple(item["selectionId"] for item in telemetry_records[::2]) != tuple(
        entry.selection_id for entry in submission.plan.ordered_selections
    ):
        raise AssertionError("M13.6 telemetry associations did not preserve selected order")

    report = {
        "status": "PASS",
        "runDirectory": str(run_dir),
        "candidatePages": [
            ["Yellow", "Blue", "Green"],
            ["Red"],
        ],
        "syntheticClasses": [1, 0, 0, 2],
        "selectedPersistenceAfterPageSwitch": list(selected_persistence),
        "undoTargetId": undone.target_id,
        "committedTargetOrder": list(queue.selected_target_ids),
        "m9ExecutionTargetOrder": list(executed_order),
        "m9LogicalBlockOrder": [item["logicalBlockId"] for item in backend.calls],
        "internalBatchSizes": [len(item["confirmedBatch"]["selections"]) for item in payloads],
        "batchIds": [item["batchId"] for item in payloads],
        "telemetryAssociations": telemetry_records,
        "m13_6TelemetryContract": "messageType=m13_6_robot_state; fake frame observer in software acceptance",
        "historicalOrLiveAccuracyClaim": False,
        "hardwareBoundary": {
            "comOpened": False,
            "nd8Operated": False,
            "questBuilt": False,
            "physicalRobotOperated": False,
        },
    }
    event_log.emit("synthetic_e2e_complete", report=report)
    event_log.close()
    (run_dir / "synthetic-e2e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def run_historical_acceptance(output_root, session_root, trial_id=None):
    run_dir = _new_subdirectory(output_root, "historical-e2e")
    event_log = M19AppendOnlyEventLog(run_dir / "m19-events.jsonl")
    backend = M19HistoricalEegBackend(session_root, trial_id=trial_id)
    raw_hash_before = sha256_file(backend.replay.raw_path)
    mapping = load_virtual_block_target_mapping()
    target_ids = (
        "m9-vblock-yellow-01",
        "m9-vblock-blue-01",
        "m9-vblock-green-01",
    )
    candidates = tuple(Candidate(mapping[target_id], target_id, target_id) for target_id in target_ids)
    queue = PagedSelectionQueue(candidates, selection_id_factory=lambda: "m19-historical-selection-01")
    session = M19InProcessQuestSession(queue, backend, event_sink=event_log,
                                       selection_prefix="m19-historical-selection")
    trial_result = session.trigger()
    raw_hash_after = sha256_file(backend.replay.raw_path)
    if raw_hash_before != raw_hash_after:
        raise AssertionError("historical raw EEG source changed during replay")
    if not trial_result["result"].get("accepted"):
        raise AssertionError("historical decoder class did not resolve through the current three-slot page")
    report = {
        "status": "PASS",
        "runDirectory": str(run_dir),
        "sourceSessionId": backend.replay.manifest.get("sessionId"),
        "sourceRawFile": str(backend.replay.raw_path),
        "sourceRawSha256Before": raw_hash_before,
        "sourceRawSha256After": raw_hash_after,
        "sourceRawUnchanged": raw_hash_before == raw_hash_after,
        "historicalTrialId": trial_result["decoder"]["evidence"]["historicalTrialId"],
        "currentRequestSelectionId": trial_result["request"]["selectionId"],
        "decodedClassIndex": trial_result["result"].get("classIndex"),
        "resolvedCurrentPageTargetId": queue.selected_target_ids[0],
        "currentPageTargetIds": list(target_ids),
        "decoder": trial_result["decoder"]["evidence"]["decoder"],
        "historicalGroundTruthReadByDecoder": False,
        "humanAccuracyClaim": False,
        "hardwareBoundary": {
            "comOpened": False,
            "nd8Operated": False,
            "questBuilt": False,
            "physicalRobotOperated": False,
        },
    }
    event_log.emit("historical_e2e_complete", report=report)
    event_log.close()
    (run_dir / "historical-e2e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _synthetic_decisions(text):
    if not text:
        return (1, 0, 2)
    values = []
    for item in text.split(","):
        token = item.strip().lower()
        if token in ("none", "no-decision", "no_decision"):
            values.append(None)
        else:
            index = int(token)
            if index not in (0, 1, 2):
                raise ValueError("synthetic decisions must be 0, 1, 2, or none")
            values.append(index)
    return tuple(values)


def _m19_mujoco_visual_contract():
    """Reuse the accepted M13.6 scene, table frame, and canonical block scale."""
    from integration.m13_6_visual_sync import (
        M13_6_MUJOCO_TABLE_TOP_Z,
        M13_6_VISUAL_STACK_TARGETS,
        M13_6_VISUAL_BLOCK_EDGE_MUJOCO,
        M13_6_VISUAL_BLOCK_EDGE_QUEST,
        _m13_6_prepare_stack_collision_categories,
        _m13_6_set_object_collision_category,
        m13_6_visual_runtime_anchor_positions_mujoco,
    )

    phase_anchors = m13_6_visual_runtime_anchor_positions_mujoco()
    front_table_y = float(M13_6_VISUAL_STACK_TARGETS["block_sim_01"]["position"][1])
    placement_by_logical_id = {
        logical_id: (
            float(phase_anchors[logical_id][0]),
            front_table_y,
            M13_6_MUJOCO_TABLE_TOP_Z + M13_6_VISUAL_BLOCK_EDGE_MUJOCO / 2.0,
        )
        for logical_id in DEFAULT_M9_SCENE_BINDINGS
    }
    if set(placement_by_logical_id) != set(DEFAULT_M9_SCENE_BINDINGS):
        raise M19BackendError("M19 M13.6 placement layout does not cover the frozen logical blocks")

    def resolve_placement_target(_build_slot_index, _model, _data, simulator_object, _slot_map):
        try:
            return placement_by_logical_id[simulator_object.logical_block_id]
        except KeyError as error:
            raise RuntimeError("M19 has no M13.6 visual target for this logical block") from error

    def prepare_active_visual_block(mujoco, model, _data, request, simulator_object):
        if request.operation is not RobotOperation.PICK_AND_PLACE:
            return
        _m13_6_prepare_stack_collision_categories(
            mujoco, model, DEFAULT_M9_SCENE_BINDINGS
        )
        _m13_6_set_object_collision_category(
            mujoco, model, simulator_object.logical_block_id, 1
        )

    def settle_released_visual_block(mujoco, model, _data, _planner, simulator_object, _target):
        _m13_6_set_object_collision_category(
            mujoco, model, simulator_object.logical_block_id, 2
        )

    visual_size = (M13_6_VISUAL_BLOCK_EDGE_QUEST,) * 3
    return {
        "adapter_options": {
            "persistent_world": True,
            "placement_targets": tuple(
                placement_by_logical_id[logical_id]
                for logical_id in sorted(DEFAULT_M9_SCENE_BINDINGS)
            ),
            "m13_6_visual_scene": True,
            "placement_target_resolver": resolve_placement_target,
            "before_execute_callback": prepare_active_visual_block,
            "post_release_callback": settle_released_visual_block,
            "post_release_settle_steps": 240,
        },
        "phase_anchor_positions_mujoco": phase_anchors,
        "visual_block_size_quest_meters": visual_size,
    }


def _create_mujoco_dispatcher(args, event_log, output_dir):
    if not str(getattr(args, "telemetry_host", "") or "").strip():
        raise M19BackendError(
            "M19 MuJoCo Demo requires --telemetry-host so Quest can display the robot on TCP 11002"
        )
    mapping = load_virtual_block_target_mapping()
    visual_contract = _m19_mujoco_visual_contract()
    adapter = create_fr3_umi_mujoco_adapter(
        scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS),
        **visual_contract["adapter_options"]
    )
    from integration.m13_9_final_live_acceptance import M139TelemetryCapture

    telemetry = M139TelemetryCapture(
        adapter,
        Path(output_dir) / "m13_6-robot-telemetry.jsonl",
        quest_host=args.telemetry_host,
        quest_port=args.telemetry_port,
        phase_anchor_positions_mujoco=visual_contract["phase_anchor_positions_mujoco"],
        visual_block_size_quest_meters=visual_contract["visual_block_size_quest_meters"],
    )
    backend = getattr(adapter, "_backend", None)
    set_step_callback = getattr(backend, "set_simulation_step_callback", None)
    simulation_data = getattr(backend, "_data", None)
    if not callable(set_step_callback) or simulation_data is None:
        telemetry.close()
        raise M19BackendError(
            "M19 MuJoCo backend cannot publish realtime simulation steps to Quest"
        )
    telemetry_pacer = M19RealtimeTelemetryPacer(
        simulation_data,
        telemetry,
        rate_hz=30.0,
    )
    set_step_callback(telemetry_pacer.on_simulation_step)
    wrapped = M19TelemetryBoundRobotAdapter(
        adapter,
        lambda request, state: telemetry.emit(state),
        event_sink=event_log,
    )
    dispatcher = M9BatchDispatcher(
        mapping,
        wrapped,
        requested_operation=RobotOperation(args.operation),
    )
    return dispatcher, telemetry


def _default_output_dir():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return Path("artifacts") / ("m19_paged_live_eeg_cli_{}_{}".format(stamp, uuid.uuid4().hex[:6]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("serve", "preflight", "synthetic-e2e", "historical-e2e"), default="serve")
    parser.add_argument("--eeg-source", choices=("synthetic", "historical", "live-nd8"), default="synthetic")
    parser.add_argument("--host", default="0.0.0.0", help="Quest selection TCP listener host")
    parser.add_argument("--port", type=int, default=11001, help="Quest selection TCP listener port")
    parser.add_argument("--telemetry-host", default=None, help="optional existing M13.6 Quest telemetry endpoint")
    parser.add_argument("--telemetry-port", type=int, default=11002)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--event-log", type=Path, default=None, help="append-only runtime JSONL path")
    parser.add_argument("--historical-session", type=Path, default=None, help="immutable recorded ND8 session directory")
    parser.add_argument("--historical-trial-id", default=None)
    parser.add_argument("--com", dest="com_port", default=None)
    parser.add_argument("--confirm-live-human", action="store_true",
                        help="explicitly authorize opening the configured live ND8 source")
    parser.add_argument("--synthetic-decisions", default="1,0,2",
                        help="comma-separated injected classes: 0,1,2,none")
    parser.add_argument("--m9-mode", choices=("disabled", "mujoco", "remote-mujoco"), default="disabled")
    parser.add_argument("--mujoco-rpc-host", default="127.0.0.1")
    parser.add_argument("--mujoco-rpc-port", type=int, default=11003)
    parser.add_argument("--operation", choices=[item.value for item in RobotOperation],
                        default=RobotOperation.PICK_AND_PLACE.value)
    parser.add_argument("--poll-interval-seconds", type=float, default=0.1)
    args = parser.parse_args(argv)
    args.output_dir = args.output_dir or _default_output_dir()

    if args.mode == "preflight":
        report = {
            "status": "PREFLIGHT_PASS",
            "outputDirectory": str(args.output_dir),
            "eegSource": args.eeg_source,
            "questListener": {"host": args.host, "port": args.port},
            "m13_6Telemetry": {"host": args.telemetry_host, "port": args.telemetry_port},
            "liveConfirmationProvided": bool(args.confirm_live_human),
            "liveHardwareOpened": False,
            "questBuilt": False,
            "physicalRobotOperated": False,
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    if args.mode == "synthetic-e2e":
        report = run_synthetic_acceptance(args.output_dir)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1
    if args.mode == "historical-e2e":
        if args.historical_session is None:
            parser.error("--historical-session is required for historical-e2e")
        report = run_historical_acceptance(args.output_dir, args.historical_session, args.historical_trial_id)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1

    event_path = args.event_log or args.output_dir / "m19-events.jsonl"
    event_log = M19AppendOnlyEventLog(event_path)
    backend = None
    telemetry = None
    transport = None
    try:
        robot_dispatcher = None
        if args.m9_mode == "remote-mujoco":
            robot_dispatcher = M19RemoteMujocoBatchDispatcher(
                args.mujoco_rpc_host, args.mujoco_rpc_port
            )
            if not robot_dispatcher.health():
                raise M19BackendError("the CPython 3.12 MuJoCo worker is not ready")
        elif args.m9_mode == "mujoco":
            robot_dispatcher, telemetry = _create_mujoco_dispatcher(args, event_log, args.output_dir)

        if args.eeg_source == "synthetic":
            backend = M19SyntheticEegBackend(_synthetic_decisions(args.synthetic_decisions))
        elif args.eeg_source == "historical":
            if args.historical_session is None:
                parser.error("--historical-session is required for --eeg-source historical")
            backend = M19HistoricalEegBackend(args.historical_session, trial_id=args.historical_trial_id)
        else:
            backend = M19LiveNd8Backend(
                args.com_port,
                confirm_live_human=args.confirm_live_human,
                session_root=args.output_dir / "eeg-session",
            )
            backend.open()
        transport = QuestSelectionTcpServer(args.host, args.port)
        transport.start()
        event_log.emit("m19_service_started", eegSource=args.eeg_source, m9Mode=args.m9_mode,
                       questHost=args.host, questPort=transport.port,
                       telemetryHost=args.telemetry_host, telemetryPort=args.telemetry_port,
                       contextAssist=False,
                       hardwareBoundary={"comOpened": args.eeg_source == "live-nd8",
                                         "nd8Operated": args.eeg_source == "live-nd8",
                                         "questBuilt": False,
                                         "physicalRobotOperated": False})
        service = M19PagedLiveOrchestrator(transport, backend, event_log, robot_dispatcher)
        service.run_forever(args.poll_interval_seconds)
        return 0
    except M19BackendError as error:
        event_log.emit("m19_preflight_failed_closed", eegSource=args.eeg_source,
                       failureType=type(error).__name__, failureReason=str(error),
                       hardwareOpened=False)
        print("M19 preflight rejected: {}".format(error))
        return 2
    finally:
        if transport is not None:
            transport.close()
        if backend is not None and callable(getattr(backend, "close", None)):
            backend.close()
        if telemetry is not None:
            telemetry.close()
        event_log.close()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "M19AppendOnlyEventLog", "M19InProcessQuestSession", "M19PagedLiveOrchestrator",
    "M19TelemetryBoundRobotAdapter", "run_historical_acceptance", "run_synthetic_acceptance",
]
