"""M14 full sequential shared-autonomy software closed loop.

This module composes the frozen M10 task state, M11 observable-history prior,
M12 context/evidence fusion, M13 stopping policy, M8 final-decision lifecycle,
and the existing M9 MuJoCo adapter.  It is intentionally an orchestrator, not
a new decoder, predictor, stopping policy, or robot controller.

The positive path uses deterministic synthetic EEG trajectories for the M13
input.  ``run_mujoco_acceptance`` swaps only the robot adapter for the existing
FR3/UMI MuJoCo adapter; no Quest, ND8, or physical robot is accessed.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping, Optional, Sequence

from integration.m8_selection_orchestration import M8SelectionOrchestrator
from integration.m8_selection_transport.simulated_batch_consumer import BatchIdempotentConsumer
from integration.m10_task_benchmark import (
    BENCHMARK_LOGICAL_BLOCK_IDS,
    TaskDefinition,
    apply_selection,
    episode_outcome,
    initial_state,
    load_task_definitions,
    task_context,
)
from integration.m11_context_prediction import make_observation, predict_context_prior
from integration.m12_context_eeg_fusion import ActiveSsvepCandidate, fuse_context_and_eeg
from integration.m13_dynamic_stopping import DynamicStoppingPolicy, DynamicStoppingSnapshot
from integration.m13_m8_selection_integration import dynamic_stopping_decision_to_m8_final_decision
from integration.m9_batch_dispatch import M9BatchDispatcher
from integration.m9_mujoco_execution import (
    DEFAULT_M9_SCENE_BINDINGS,
    RobotExecutionResult,
    RobotOperation,
    SceneBindingRegistry,
    create_fr3_umi_mujoco_adapter,
)
from integration.m9_robot_adapter import confirmed_batch_to_robot_requests
from integration.m9_virtual_block_mapping import load_virtual_block_target_mapping
from integration.m9_virtual_e2e import make_confirmed_batch


M14_ACCEPTANCE_ID = "m14-full-sequential-shared-autonomy-closed-loop"
M14_SCHEMA_VERSION = 1
M14_SOURCE_TYPE = "SYNTHETIC_EEG_SYNTHETIC_CONTEXT"


def _contains_forbidden_identity(value):
    if isinstance(value, str):
        return "obj_" in value
    if isinstance(value, Mapping):
        return any(_contains_forbidden_identity(key) or _contains_forbidden_identity(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_identity(item) for item in value)
    return False


def _reverse_mapping(mapping):
    reverse = {}
    for target_id, logical_block_id in mapping.items():
        if logical_block_id in reverse:
            raise ValueError("TargetId mapping is not one-to-one")
        reverse[logical_block_id] = target_id
    if set(reverse) != set(BENCHMARK_LOGICAL_BLOCK_IDS):
        raise ValueError("M14 requires the frozen four-logical-block mapping")
    return reverse


def _active_candidates(expected_logical_block_id, mapping):
    """Select three of four frozen candidates while always including the target."""
    reverse = _reverse_mapping(mapping)
    if expected_logical_block_id not in reverse:
        raise ValueError("expected logical block is absent from the frozen mapping")
    others = [item for item in BENCHMARK_LOGICAL_BLOCK_IDS if item != expected_logical_block_id]
    selected = (expected_logical_block_id, others[0], others[1])
    return tuple(
        ActiveSsvepCandidate.from_frozen_mapping(slot, reverse[logical_id], mapping)
        for slot, logical_id in enumerate(selected)
    )


class M14QuestTransport:
    """Minimal M8 transport double with a frozen per-selection snapshot."""

    def __init__(self):
        self._candidates_by_selection = {}
        self.opened = []
        self.submitted = []
        self.aborted = []

    def register_snapshot(self, selection_id, candidates, snapshot_id=None, snapshot_version=None):
        self._candidates_by_selection[str(selection_id)] = {
            "candidates": tuple(candidates),
            "snapshotId": snapshot_id,
            "snapshotVersion": snapshot_version,
        }

    @staticmethod
    def _ack(selection_id, accepted=True, **values):
        return {
            "protocolVersion": 1,
            "messageType": "selection_ack",
            "selectionId": selection_id,
            "accepted": bool(accepted),
            **values,
        }

    def open_selection(self, selection_id):
        if selection_id not in self._candidates_by_selection:
            return self._ack(selection_id, False, rejectionReason="missing_frozen_snapshot")
        self.opened.append(selection_id)
        record = self._candidates_by_selection[selection_id]
        return self._ack(
            selection_id,
            candidateSnapshotId=record.get("snapshotId"),
            candidateSnapshotVersion=record.get("snapshotVersion"),
        )

    def submit_eeg_selection(self, selection_id, class_index):
        record = self._candidates_by_selection.get(selection_id, {})
        candidates = record.get("candidates", ())
        self.submitted.append((selection_id, int(class_index)))
        if not isinstance(class_index, int) or class_index < 0 or class_index >= len(candidates):
            return self._ack(selection_id, False, rejectionReason="class_index_out_of_range")
        candidate = candidates[class_index]
        return self._ack(
            selection_id,
            True,
            resolvedTargetId=candidate.target_id,
            resolvedLogicalBlockId=candidate.logical_block_id,
            resolvedSlot=candidate.slot_index,
            candidateSnapshotId=record.get("snapshotId"),
            candidateSnapshotVersion=record.get("snapshotVersion"),
            provenance="quest_frozen_selection_snapshot",
        )

    def abort_selection(self, selection_id):
        self.aborted.append(selection_id)
        return self._ack(selection_id, True)


@dataclass
class SyntheticRobotAdapter:
    """Deterministic M9 adapter double used for negative and replay checks."""

    fail_on_call: Optional[int] = None

    def __post_init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(request.logical_block_id)
        failed = self.fail_on_call is not None and len(self.calls) == self.fail_on_call
        return RobotExecutionResult(
            request_id=request.request_id,
            logical_block_id=request.logical_block_id,
            operation=request.operation,
            success=not failed,
            failure_code="injected_failure" if failed else None,
            failure_reason="M14 injected robot execution failure" if failed else None,
            source_batch_id=request.selection.batch_id,
            source_selection_id=request.selection.selection_id,
            source_target_id=request.selection.source_target_id,
            batch_provenance=request.selection.batch_provenance,
            selection_provenance=request.selection.selection_provenance,
            backend_execution_id="m14-synthetic-execution-{:02d}".format(len(self.calls)),
            execution_provenance="m14-synthetic:place_ok" if not failed else "m14-synthetic:failed",
            started_utc="2026-09-15T18:00:00+00:00",
            completed_utc="2026-09-15T18:00:01+00:00",
        )


def _strong_snapshots(window_expected_logical_id, context_prior, candidates, provenance):
    scores = {
        candidate.logical_block_id: (10.0 if candidate.logical_block_id == window_expected_logical_id else 0.01)
        for candidate in candidates
    }
    snapshots = []
    for window_index in range(2):
        fused = fuse_context_and_eeg(context_prior, candidates, scores)
        snapshots.append(
            DynamicStoppingSnapshot.from_m12(
                window_index,
                fused,
                analysis_window_seconds=1.5,
                effective_acquisition_seconds=2.0 + 0.2 * window_index,
                provenance={
                    "sourceType": M14_SOURCE_TYPE,
                    "trajectory": provenance,
                    "windowSchedule": "M6 formal window grid",
                },
            )
        )
    return tuple(snapshots)


def _no_decision_snapshots():
    # Empty input is an explicit M13 no-decision outcome, not a fabricated class.
    return ()


def _public_mapping(candidates):
    return [candidate.to_public_dict() for candidate in candidates]


class M14SequentialEpisodeRunner:
    """Run one identity-scoped sequential episode through the frozen seams."""

    def __init__(self, robot_adapter, mapping=None, operation=RobotOperation.PICK_AND_PLACE, source_type=M14_SOURCE_TYPE):
        if not callable(getattr(robot_adapter, "execute", None)):
            raise TypeError("robot_adapter must provide execute(request)")
        if not isinstance(operation, RobotOperation):
            raise ValueError("operation must be an explicit RobotOperation")
        self.mapping = load_virtual_block_target_mapping() if mapping is None else dict(mapping)
        _reverse_mapping(self.mapping)
        self.robot_adapter = robot_adapter
        self.operation = operation
        self.source_type = str(source_type)

    def run_episode(
        self,
        definition: TaskDefinition,
        target_overrides=None,
        no_decision_steps=(),
        duplicate_final_step=None,
        evidence_prefix="m14",
        snapshot_overrides=None,
    ):
        if not isinstance(definition, TaskDefinition):
            raise TypeError("definition must be a TaskDefinition")
        target_overrides = dict(target_overrides or {})
        no_decision_steps = set(no_decision_steps or ())
        snapshot_overrides = dict(snapshot_overrides or {})
        receiver = BatchIdempotentConsumer()
        transport = M14QuestTransport()
        orchestrator = M8SelectionOrchestrator(transport)
        dispatcher = M9BatchDispatcher(
            self.mapping,
            self.robot_adapter,
            requested_operation=self.operation,
            request_id_factory=lambda: "{}-request-{:02d}".format(evidence_prefix, len(dispatcher_requests)),
        )
        dispatcher_requests = []
        state = initial_state(definition)
        steps = []
        duplicate_results = []

        for step_index, expected_logical_block_id in enumerate(definition.ordered_logical_block_ids):
            selected_logical_block_id = target_overrides.get(step_index, expected_logical_block_id)
            candidates = _active_candidates(selected_logical_block_id, self.mapping)
            trial_id = "{}-{}-trial-{:02d}".format(evidence_prefix, definition.task_id, step_index)
            selection_id = "{}-selection".format(trial_id)
            batch_id = "{}-batch".format(trial_id)
            transport.register_snapshot(selection_id, candidates)
            opened = orchestrator.open_selection(selection_id, trial_id)
            context_before = task_context(definition, state)
            m11_observation = make_observation(state.completed_sequence, available=BENCHMARK_LOGICAL_BLOCK_IDS)
            context_prior = predict_context_prior(m11_observation)
            if not context_prior.valid:
                raise AssertionError("M11 prior unexpectedly invalid for a valid M10 prefix")

            if step_index in snapshot_overrides:
                if step_index in no_decision_steps:
                    raise ValueError("snapshot_overrides and no_decision_steps cannot target the same step")
                snapshots = tuple(snapshot_overrides[step_index])
                if not snapshots or not all(isinstance(item, DynamicStoppingSnapshot) for item in snapshots):
                    raise TypeError("snapshot_overrides must contain non-empty DynamicStoppingSnapshot sequences")
            else:
                snapshots = _no_decision_snapshots() if step_index in no_decision_steps else _strong_snapshots(
                    selected_logical_block_id,
                    context_prior,
                    candidates,
                    "m14-sequential-strong-agreement",
                )
            policy = DynamicStoppingPolicy()
            evaluations = []
            if opened:
                for snapshot in snapshots:
                    evaluation = policy.observe(snapshot)
                    evaluations.append(evaluation.to_public_dict())
                    if policy.decision is not None:
                        break
            decision = policy.finalize()
            final_decision = dynamic_stopping_decision_to_m8_final_decision(decision, trial_id, "m14-session")
            m8_result = orchestrator.submit_final_decision(final_decision)
            selection_record = {
                "trialId": trial_id,
                "selectionId": selection_id,
                "opened": opened,
                "activeCandidateSnapshot": _public_mapping(candidates),
                "m13FinalDecision": decision.to_public_dict(),
                "m8FinalDecision": final_decision,
                "m8Result": m8_result,
            }

            robot_record = {
                "preflightAccepted": False,
                "preflightReasonCode": None,
                "dispatchAttempted": False,
                "dispatchResult": None,
                "executionSuccess": False,
                "m10Committed": False,
            }
            state_before = state.to_public_dict()
            if decision.decision_made and m8_result.get("status") == "quest_accepted":
                if m8_result.get("predictedClassIndex") != decision.selected_slot_index:
                    raise AssertionError("M8 class index disagrees with the frozen M13 slot")
                if m8_result.get("ack", {}).get("resolvedTargetId") != decision.selected_target_id:
                    raise AssertionError("M8 ACK target disagrees with the frozen M13 TargetId")
                payload = make_confirmed_batch(
                    decision.selected_target_id,
                    batch_id=batch_id,
                    selection_id=selection_id,
                    slot_index=decision.selected_slot_index,
                )
                receipt = receiver.accept(payload)
                requests = confirmed_batch_to_robot_requests(receipt.payload, self.mapping)
                if len(requests) != 1:
                    raise AssertionError("M14 step must dispatch exactly one frozen selection")
                request = requests[0]
                dispatcher_requests.append(request)
                preflight = apply_selection(definition, state, request.logical_block_id)
                robot_record.update(
                    {
                        "preflightAccepted": preflight.accepted,
                        "preflightReasonCode": preflight.reason_code,
                    }
                )
                if preflight.accepted:
                    robot_record["dispatchAttempted"] = True
                    dispatch_result = dispatcher.dispatch(receipt)
                    robot_record["dispatchResult"] = dispatch_result.to_public_dict()
                    robot_record["executionSuccess"] = dispatch_result.success
                    if dispatch_result.success:
                        committed = apply_selection(definition, state, request.logical_block_id)
                        if not committed.accepted:
                            raise AssertionError("M10 commit rejected after successful robot execution")
                        state = committed.state
                        robot_record["m10Committed"] = True
                    # A failed execution deliberately leaves state unchanged.
                else:
                    # Preserve the accepted prefix while retaining M10's invalid state.
                    state = preflight.state

                if duplicate_final_step == step_index:
                    duplicate_results.append(
                        orchestrator.submit_final_decision(final_decision)
                    )

            next_context = task_context(definition, state)
            steps.append(
                {
                    "stepIndex": step_index,
                    "expectedLogicalBlockId": expected_logical_block_id,
                    "selectedLogicalBlockIdInput": selected_logical_block_id,
                    "stateBefore": state_before,
                    "m11Input": {
                        "completedLogicalBlockHistory": list(m11_observation.completed_logical_block_history),
                        "availableLogicalBlockIds": list(m11_observation.available_logical_block_ids),
                        "stepIndex": m11_observation.step_index,
                    },
                    "m11ContextPrior": context_prior.to_public_dict(),
                    "activeCandidateSnapshot": _public_mapping(candidates),
                    "evidenceProvenance": {
                        "sourceType": self.source_type,
                        "contextSource": "M11 observable completed history",
                        "eegSource": "historical M6 raw packet replay" if "historical" in self.source_type.lower() else "deterministic synthetic trajectory",
                    },
                    "m12FusedTrajectory": [snapshot.fused_evidence.to_public_dict() for snapshot in snapshots],
                    "m13Evaluations": evaluations,
                    "selection": selection_record,
                    "robot": robot_record,
                    "m10Commit": {
                        "committedOnlyAfterRobotSuccess": True,
                        "committed": robot_record["m10Committed"],
                        "stateAfter": state.to_public_dict(),
                    },
                    "nextObservableContext": next_context.to_public_dict(),
                }
            )
            if state.status.value in ("invalid", "completed"):
                break
            if robot_record["dispatchAttempted"] and not robot_record["executionSuccess"]:
                # A backend failure is terminal for this episode. Do not feed
                # the next intended target into an unchanged M10 state.
                break
            if not decision.decision_made:
                # No-decision suppresses robot/task progression and ends the episode.
                break

        record = {
            "schemaVersion": M14_SCHEMA_VERSION,
            "recordType": "m14_sequential_episode",
            "acceptanceId": M14_ACCEPTANCE_ID,
            "sourceType": self.source_type,
            "taskId": definition.task_id,
            "taskName": definition.task_name,
            "requestedLogicalBlockIds": list(definition.ordered_logical_block_ids),
            "steps": steps,
            "finalState": state.to_public_dict(),
            "episodeOutcome": episode_outcome(state).value,
            "successfulRobotExecutionCount": sum(1 for step in steps if step["robot"]["executionSuccess"]),
            "robotDispatchAttemptCount": sum(1 for step in steps if step["robot"]["dispatchAttempted"]),
            "m13DecisionCount": sum(1 for step in steps if step["selection"]["m13FinalDecision"]["decisionMade"]),
            "duplicateFinalDecisionResults": duplicate_results,
            "publicIdentityCheck": not _contains_forbidden_identity(steps),
        }
        return record


def _positive_checks(records):
    checks = []
    for record in records:
        successful_steps = [step for step in record["steps"] if step["robot"]["executionSuccess"]]
        checks.append(
            {
                "taskId": record["taskId"],
                "completed": record["finalState"]["status"] == "completed",
                "fourM13Decisions": record["m13DecisionCount"] == 4,
                "fourRobotDispatches": record["robotDispatchAttemptCount"] == 4,
                "fourSuccessfulExecutions": record["successfulRobotExecutionCount"] == 4,
                "fourM10Commits": len([step for step in successful_steps if step["m10Commit"]["committed"]]) == 4,
                "sequenceMatchesDefinition": record["finalState"]["completedSequence"] == record["requestedLogicalBlockIds"],
                "m13Participated": all(step["m13Evaluations"] for step in record["steps"]),
                "identityPrivate": record["publicIdentityCheck"],
            }
        )
    return checks


def _semantic_projection(record):
    return {
        "taskId": record["taskId"],
        "finalState": record["finalState"],
        "episodeOutcome": record["episodeOutcome"],
        "steps": [
            {
                "stepIndex": step["stepIndex"],
                "expected": step["expectedLogicalBlockId"],
                "m11History": step["m11Input"]["completedLogicalBlockHistory"],
                "m11Top": step["m11ContextPrior"]["topTargets"],
                "m12Top": [entry["logicalBlockId"] for entry in step["m12FusedTrajectory"][-1]["entries"] if entry["logicalBlockId"] in step["selection"]["m13FinalDecision"]["finalFusedEvidence"] and entry["normalizedFusedEvidence"] == max(item["normalizedFusedEvidence"] for item in step["m12FusedTrajectory"][-1]["entries"])],
                "m13": {
                    "decisionMade": step["selection"]["m13FinalDecision"]["decisionMade"],
                    "target": step["selection"]["m13FinalDecision"]["selectedLogicalBlockId"],
                    "stopReason": step["selection"]["m13FinalDecision"]["stopReason"],
                    "stopWindow": step["selection"]["m13FinalDecision"]["stopWindow"],
                },
                "robot": {
                    "preflight": step["robot"]["preflightAccepted"],
                    "dispatch": step["robot"]["dispatchAttempted"],
                    "success": step["robot"]["executionSuccess"],
                    "commit": step["robot"]["m10Committed"],
                },
            }
            for step in record["steps"]
        ],
    }


def run_synthetic_acceptance(evidence_path=None):
    definitions = load_task_definitions()
    positive_runner = M14SequentialEpisodeRunner(SyntheticRobotAdapter())
    positives = [
        positive_runner.run_episode(definitions[task_id], evidence_prefix="m14-positive-{}".format(task_id))
        for task_id in ("house", "tower", "bridge")
    ]

    no_decision = M14SequentialEpisodeRunner(SyntheticRobotAdapter()).run_episode(
        definitions["house"], no_decision_steps=(0,), evidence_prefix="m14-negative-no-decision"
    )
    wrong_target = M14SequentialEpisodeRunner(SyntheticRobotAdapter()).run_episode(
        definitions["house"], target_overrides={0: "block_sim_03"}, evidence_prefix="m14-negative-wrong-target"
    )
    failed_robot_adapter = SyntheticRobotAdapter(fail_on_call=1)
    failed_robot = M14SequentialEpisodeRunner(failed_robot_adapter).run_episode(
        definitions["house"], evidence_prefix="m14-negative-robot-failure"
    )
    duplicate = M14SequentialEpisodeRunner(SyntheticRobotAdapter()).run_episode(
        definitions["house"], duplicate_final_step=0, evidence_prefix="m14-negative-duplicate"
    )
    replay_a = M14SequentialEpisodeRunner(SyntheticRobotAdapter()).run_episode(
        definitions["tower"], evidence_prefix="m14-deterministic-a"
    )
    replay_b = M14SequentialEpisodeRunner(SyntheticRobotAdapter()).run_episode(
        definitions["tower"], evidence_prefix="m14-deterministic-b"
    )

    positive_checks = _positive_checks(positives)
    checks = {
        "positiveEpisodesPass": all(all(value for key, value in check.items() if key != "taskId") for check in positive_checks),
        "m13NoDecisionSuppressesRobot": no_decision["episodeOutcome"] == "valid_incomplete" and no_decision["robotDispatchAttemptCount"] == 0 and no_decision["finalState"]["completedSequence"] == [],
        "invalidTargetPreservesAcceptedPrefix": wrong_target["episodeOutcome"] == "invalid" and wrong_target["finalState"]["completedSequence"] == [] and wrong_target["robotDispatchAttemptCount"] == 0,
        "robotFailureDoesNotCommit": failed_robot["finalState"]["completedSequence"] == [] and failed_robot["successfulRobotExecutionCount"] == 0 and failed_robot["robotDispatchAttemptCount"] == 1,
        "duplicateFinalDecisionSuppressed": len(duplicate["duplicateFinalDecisionResults"]) == 1 and duplicate["duplicateFinalDecisionResults"][0]["status"] == "duplicate_final_decision" and duplicate["robotDispatchAttemptCount"] == 4,
        "deterministicReplay": _semantic_projection(replay_a) == _semantic_projection(replay_b),
        "publicIdentityPrivate": all(record["publicIdentityCheck"] for record in positives + [no_decision, wrong_target, failed_robot, duplicate]),
    }
    summary = {
        "schemaVersion": M14_SCHEMA_VERSION,
        "recordType": "m14_sequential_closed_loop_acceptance",
        "acceptanceId": M14_ACCEPTANCE_ID,
        "mode": "synthetic",
        "overallStatus": "PASS" if all(checks.values()) else "FAIL",
        "positiveEpisodes": positive_checks,
        "negativeCases": {
            "noDecision": {"outcome": no_decision["episodeOutcome"], "dispatches": no_decision["robotDispatchAttemptCount"]},
            "wrongTarget": {"outcome": wrong_target["episodeOutcome"], "completedSequence": wrong_target["finalState"]["completedSequence"], "dispatches": wrong_target["robotDispatchAttemptCount"]},
            "robotFailure": {"completedSequence": failed_robot["finalState"]["completedSequence"], "dispatches": failed_robot["robotDispatchAttemptCount"]},
            "duplicateFinalDecision": duplicate["duplicateFinalDecisionResults"],
        },
        "checks": checks,
        "provenance": "synthetic EEG trajectory + synthetic context overlay + existing M8/M9 seams",
        "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "physicalRobotOperated": False},
    }
    records = positives + [no_decision, wrong_target, failed_robot, duplicate]
    if evidence_path is not None:
        _write_jsonl(evidence_path, records)
    return summary, records


def run_mujoco_acceptance(evidence_path=None):
    try:
        adapter = create_fr3_umi_mujoco_adapter(scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS))
    except (ImportError, RuntimeError, OSError, ValueError) as error:
        return {
            "schemaVersion": M14_SCHEMA_VERSION,
            "recordType": "m14_sequential_closed_loop_acceptance",
            "acceptanceId": M14_ACCEPTANCE_ID,
            "mode": "mujoco",
            "overallStatus": "BLOCKED",
            "error": "{}: {}".format(type(error).__name__, error),
            "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "physicalRobotOperated": False},
        }, []
    definitions = load_task_definitions()
    runner = M14SequentialEpisodeRunner(adapter, source_type="SYNTHETIC_EEG_SYNTHETIC_CONTEXT_MUJOCO")
    records = [runner.run_episode(definitions[task_id], evidence_prefix="m14-mujoco-{}".format(task_id)) for task_id in ("house", "tower", "bridge")]
    positive_checks = _positive_checks(records)
    checks = {
        "positiveEpisodesPass": all(all(value for key, value in check.items() if key != "taskId") for check in positive_checks),
        "mujocoExecutionsObserved": all(record["successfulRobotExecutionCount"] == 4 for record in records),
        "publicIdentityPrivate": all(record["publicIdentityCheck"] for record in records),
    }
    summary = {
        "schemaVersion": M14_SCHEMA_VERSION,
        "recordType": "m14_sequential_closed_loop_acceptance",
        "acceptanceId": M14_ACCEPTANCE_ID,
        "mode": "mujoco",
        "overallStatus": "PASS" if all(checks.values()) else "FAIL",
        "positiveEpisodes": positive_checks,
        "checks": checks,
        "provenance": "synthetic EEG trajectory + synthetic context overlay + existing M8/M9/FR3-UMI MuJoCo adapter",
        "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "physicalRobotOperated": False},
    }
    if evidence_path is not None:
        _write_jsonl(evidence_path, records)
    return summary, records


def _write_jsonl(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            if _contains_forbidden_identity(record):
                raise ValueError("M14 public evidence contains a forbidden obj_N identity")
            output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("synthetic", "mujoco"), default="synthetic")
    parser.add_argument("--evidence-path")
    parser.add_argument("--summary-path")
    args = parser.parse_args(argv)
    summary, _records = run_synthetic_acceptance(args.evidence_path) if args.mode == "synthetic" else run_mujoco_acceptance(args.evidence_path)
    if args.summary_path:
        path = Path(args.summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["overallStatus"] == "PASS" else (2 if summary["overallStatus"] == "BLOCKED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "M14_ACCEPTANCE_ID",
    "M14SequentialEpisodeRunner",
    "M14QuestTransport",
    "SyntheticRobotAdapter",
    "run_synthetic_acceptance",
    "run_mujoco_acceptance",
]
