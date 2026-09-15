"""Compose the frozen M10 sequence with the existing M9 MuJoCo path.

This module is deliberately an orchestration seam, not a new robot or task
engine. One synthetic confirmed/frozen selection is converted by the existing
M9 identity adapter, pre-validated by the frozen M10 state machine, dispatched
through the existing M9 dispatcher, and committed to M10 only after a
successful robot execution result.
"""

import argparse
import json
from pathlib import Path

from integration.m8_selection_transport.simulated_batch_consumer import (
    BatchIdempotentConsumer,
)
from integration.m10_task_benchmark import (
    FIXTURE_PATH,
    TaskDefinition,
    apply_selection,
    episode_outcome,
    initial_state,
    load_task_definitions,
)
from integration.m9_batch_dispatch import M9BatchDispatcher
from integration.m9_mujoco_execution import (
    DEFAULT_M9_SCENE_BINDINGS,
    RobotOperation,
    SceneBindingRegistry,
    create_fr3_umi_mujoco_adapter,
)
from integration.m9_robot_adapter import confirmed_batch_to_robot_requests
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping
from integration.m9_virtual_e2e import make_confirmed_batch


ACCEPTANCE_ID = "m10.2-sequential-mujoco-e2e"
SUMMARY_SCHEMA_VERSION = 1
DEFAULT_EVIDENCE_PATH = Path("artifacts") / "m10" / "sequential_mujoco_e2e.jsonl"


def _reverse_mapping(target_id_to_logical_block_id):
    reverse = {}
    for target_id, logical_block_id in target_id_to_logical_block_id.items():
        if logical_block_id in reverse:
            raise ValueError(
                "TargetId mapping is not one-to-one for logical block {!r}".format(
                    logical_block_id
                )
            )
        reverse[logical_block_id] = target_id
    return reverse


def _require_task(definitions, task_id):
    try:
        definition = definitions[task_id]
    except KeyError as error:
        raise ValueError("unknown M10 taskId: {!r}".format(task_id)) from error
    if not isinstance(definition, TaskDefinition):
        raise TypeError("task definitions must contain TaskDefinition values")
    return definition


def _episode_payload(definition, logical_block_id, reverse_mapping, step_index, prefix):
    try:
        target_id = reverse_mapping[logical_block_id]
    except KeyError as error:
        raise ValueError(
            "M9 TargetId mapping has no target for logical block {!r}".format(
                logical_block_id
            )
        ) from error
    return make_confirmed_batch(
        target_id,
        batch_id="{}-{}-batch-{:02d}".format(prefix, definition.task_id, step_index),
        selection_id="{}-{}-selection-{:02d}".format(
            prefix, definition.task_id, step_index
        ),
        slot_index=step_index % 3,
    )


def run_sequential_episode(
    definition,
    logical_block_ids,
    robot_adapter,
    target_id_to_logical_block_id=None,
    operation=RobotOperation.PICK_AND_PLACE,
    evidence_prefix="m10-2",
):
    """Run one bounded sequence through M9 identity/dispatch and M10 state.

    The returned public record intentionally contains no simulator-internal
    object names. ``logical_block_ids`` is an episode input, not a predictor
    output; the M10 task definition remains the deterministic evaluator.
    """
    if not isinstance(definition, TaskDefinition):
        raise TypeError("definition must be a TaskDefinition")
    logical_block_ids = tuple(logical_block_ids)
    if not callable(getattr(robot_adapter, "execute", None)):
        raise TypeError("robot_adapter must provide execute(request)")
    if not isinstance(operation, RobotOperation):
        raise ValueError("operation must be an explicit RobotOperation")
    target_mapping = (
        load_virtual_block_target_mapping()
        if target_id_to_logical_block_id is None
        else dict(target_id_to_logical_block_id)
    )
    reverse_mapping = _reverse_mapping(target_mapping)
    receiver = BatchIdempotentConsumer()
    dispatcher = M9BatchDispatcher(
        target_mapping,
        robot_adapter,
        requested_operation=operation,
        request_id_factory=lambda: "{}-request-{:02d}".format(
            evidence_prefix, len(events)
        ),
    )
    state = initial_state(definition)
    events = []

    for step_index, logical_block_id in enumerate(logical_block_ids):
        payload = _episode_payload(
            definition,
            logical_block_id,
            reverse_mapping,
            step_index,
            evidence_prefix,
        )
        receipt = receiver.accept(payload)
        requests = confirmed_batch_to_robot_requests(receipt.payload, target_mapping)
        if len(requests) != 1:
            raise ValueError("M10.2 sequential episodes must contain one selection per step")
        request = requests[0]
        if request.logical_block_id != logical_block_id:
            raise AssertionError(
                "M9 logical-ID resolution disagrees with the requested episode input"
            )

        preflight = apply_selection(definition, state, request.logical_block_id)
        event = {
            "stepIndex": step_index,
            "targetId": request.source_target_id,
            "logicalBlockId": request.logical_block_id,
            "selectionId": request.selection_id,
            "m9IdentityResolution": "target_id_to_logical_block_id",
            "m10PreflightAccepted": preflight.accepted,
            "m10PreflightReasonCode": preflight.reason_code,
            "robotExecutionDispatched": False,
            "robotExecutionAttemptCount": 0,
        }

        if not preflight.accepted:
            state = preflight.state
            event.update(
                {
                    "progressedAfterRobotSuccess": False,
                    "dispatchResult": None,
                    "stateAfterStep": state.to_public_dict(),
                }
            )
            events.append(event)
            break

        dispatch_result = dispatcher.dispatch(receipt)
        event["robotExecutionDispatched"] = True
        event["robotExecutionAttemptCount"] = len(dispatch_result.executions)
        event["dispatchResult"] = dispatch_result.to_public_dict()
        if not dispatch_result.success:
            event.update(
                {
                    "progressedAfterRobotSuccess": False,
                    "stateAfterStep": state.to_public_dict(),
                }
            )
            events.append(event)
            break

        committed = apply_selection(definition, state, request.logical_block_id)
        if not committed.accepted:
            raise AssertionError(
                "M10 commit unexpectedly rejected after successful robot execution"
            )
        state = committed.state
        event.update(
            {
                "progressedAfterRobotSuccess": True,
                "stateAfterStep": state.to_public_dict(),
            }
        )
        events.append(event)

    public_record = {
        "recordType": "episodeEvidence",
        "acceptanceId": ACCEPTANCE_ID,
        "taskId": definition.task_id,
        "taskName": definition.task_name,
        "requestedLogicalBlockIds": list(logical_block_ids),
        "events": events,
        "finalState": state.to_public_dict(),
        "episodeOutcome": episode_outcome(state).value,
        "successfulRobotExecutionCount": sum(
            event["robotExecutionAttemptCount"]
            for event in events
            if event["progressedAfterRobotSuccess"]
        ),
        "publicSimulatorIdentityCheck": "obj_" not in json.dumps(
            {"events": events, "finalState": state.to_public_dict()},
            sort_keys=True,
        ),
    }
    return public_record


def _make_real_adapter():
    return create_fr3_umi_mujoco_adapter(
        scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS)
    )


def run_real_mujoco_task(
    task_id,
    definitions=None,
    target_id_to_logical_block_id=None,
    operation=RobotOperation.PICK_AND_PLACE,
    include_negative=False,
):
    definitions = load_task_definitions(FIXTURE_PATH) if definitions is None else definitions
    definition = _require_task(definitions, task_id)
    mapping = (
        load_virtual_block_target_mapping()
        if target_id_to_logical_block_id is None
        else dict(target_id_to_logical_block_id)
    )
    positive = run_sequential_episode(
        definition,
        definition.ordered_logical_block_ids,
        _make_real_adapter(),
        target_id_to_logical_block_id=mapping,
        operation=operation,
        evidence_prefix="m10-2-positive",
    )
    records = [positive]
    if include_negative:
        if task_id != "house":
            raise ValueError("the representative wrong-order negative is defined for house")
        negative = run_sequential_episode(
            definition,
            ("block_sim_01", "block_sim_03"),
            _make_real_adapter(),
            target_id_to_logical_block_id=mapping,
            operation=operation,
            evidence_prefix="m10-2-negative",
        )
        records.append(negative)
    return records


def _positive_checks(records):
    checks = []
    for record in records:
        successful_events = [
            event
            for event in record["events"]
            if event["progressedAfterRobotSuccess"]
        ]
        place_ok = all(
            event["dispatchResult"]["executions"][0]["executionProvenance"].endswith(
                ":place_ok"
            )
            for event in successful_events
        )
        checks.append(
            {
                "taskId": record["taskId"],
                "completed": record["finalState"]["status"] == "completed",
                "fourRobotDispatches": len(successful_events) == 4,
                "fourPlaceOkExecutions": len(successful_events) == 4 and place_ok,
                "logicalSequenceMatchesDefinition": record["finalState"][
                    "completedSequence"
                ]
                == record["requestedLogicalBlockIds"],
                "publicSimulatorIdentityHidden": record["publicSimulatorIdentityCheck"],
            }
        )
    return checks


def _negative_check(record):
    rejected = record["events"][-1] if record["events"] else {}
    return {
        "taskId": record["taskId"],
        "wrongOrderRejected": (
            record["finalState"]["status"] == "invalid"
            and rejected.get("m10PreflightReasonCode") == "wrong_order"
        ),
        "acceptedPrefixRetained": record["finalState"]["completedSequence"]
        == ["block_sim_01"],
        "rejectedTargetRobotExecutionCount": rejected.get("robotExecutionAttemptCount"),
        "rejectedTargetWasNotDispatched": (
            rejected.get("robotExecutionDispatched") is False
            and rejected.get("robotExecutionAttemptCount") == 0
            and rejected.get("dispatchResult") is None
        ),
        "publicSimulatorIdentityHidden": record["publicSimulatorIdentityCheck"],
    }


def _write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _write_summary(path, summary):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def run_acceptance(
    task_ids,
    evidence_path=None,
    operation=RobotOperation.PICK_AND_PLACE,
    include_negative=False,
):
    definitions = load_task_definitions(FIXTURE_PATH)
    all_records = []
    positive_records = []
    negative_records = []
    for task_id in task_ids:
        records = run_real_mujoco_task(
            task_id,
            definitions=definitions,
            operation=operation,
            include_negative=include_negative and task_id == "house",
        )
        positive_records.append(records[0])
        all_records.extend(records)
        negative_records.extend(records[1:])

    positive_checks = _positive_checks(positive_records)
    negative_checks = [_negative_check(record) for record in negative_records]
    checks = {
        "positiveEpisodesPass": all(
            all(value for key, value in check.items() if key != "taskId")
            for check in positive_checks
        ),
        "negativeEpisodePass": (
            not include_negative
            or bool(negative_checks)
            and all(
                all(
                    value
                    for key, value in check.items()
                    if key not in {"taskId", "rejectedTargetRobotExecutionCount"}
                )
                for check in negative_checks
            )
        ),
        "publicSimulatorIdentityHidden": all(
            record["publicSimulatorIdentityCheck"] for record in all_records
        ),
    }
    summary = {
        "schemaVersion": SUMMARY_SCHEMA_VERSION,
        "acceptanceId": ACCEPTANCE_ID,
        "operation": operation.value,
        "taskIds": list(task_ids),
        "overallStatus": "PASS" if all(checks.values()) else "FAIL",
        "positiveEpisodes": positive_checks,
        "negativeEpisodes": negative_checks,
        "checks": checks,
        "evidenceFormat": "JSONL append-only episodeEvidence records",
    }
    if evidence_path is not None:
        _write_jsonl(evidence_path, all_records)
    return summary, all_records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--task", choices=("house", "tower", "bridge"))
    group.add_argument("--all-tasks", action="store_true")
    parser.add_argument(
        "--operation",
        choices=[operation.value for operation in RobotOperation],
        default=RobotOperation.PICK_AND_PLACE.value,
    )
    parser.add_argument("--include-negative", action="store_true")
    parser.add_argument("--evidence-path", default=str(DEFAULT_EVIDENCE_PATH))
    parser.add_argument("--summary-path")
    args = parser.parse_args(argv)
    task_ids = ("house", "tower", "bridge") if args.all_tasks else (args.task or "house",)
    try:
        summary, _records = run_acceptance(
            task_ids,
            evidence_path=args.evidence_path,
            operation=RobotOperation(args.operation),
            include_negative=args.include_negative,
        )
    except RuntimeError as error:
        summary = {
            "schemaVersion": SUMMARY_SCHEMA_VERSION,
            "acceptanceId": ACCEPTANCE_ID,
            "overallStatus": "BLOCKED"
            if "MuJoCo is not installed" in str(error)
            else "FAIL",
            "error": "{}: {}".format(type(error).__name__, str(error)),
        }
    except (OSError, TypeError, ValueError, KeyError, AssertionError) as error:
        summary = {
            "schemaVersion": SUMMARY_SCHEMA_VERSION,
            "acceptanceId": ACCEPTANCE_ID,
            "overallStatus": "FAIL",
            "error": "{}: {}".format(type(error).__name__, str(error)),
        }
    if args.summary_path:
        _write_summary(args.summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    if summary["overallStatus"] == "PASS":
        return 0
    return 2 if summary["overallStatus"] == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
