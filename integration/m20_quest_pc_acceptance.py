"""Receive one Quest M20 batch, validate its scene snapshot, then simulate it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from integration.m16_paged_queue import Candidate, CommittedBatch, CommitPlan, QueueEntry
from integration.m20_assistive_desk_e2e import (
    M20ActionResolutionError,
    _execute_action,
    _new_attempt_dir,
    _write_json,
    resolve_source_destination,
)
from integration.m20_assistive_scene_contract import DEFAULT_SPEC_PATH, M20AssistiveSceneError, load_spec
from integration.m20_scene_layout_snapshot import M20SceneSnapshotRegistry, serialize_scene_layout_snapshot
from integration.m8_selection_transport.simulated_batch_consumer import (
    BatchIdempotentConsumer,
    consume_one_batch,
    validate_batch_message,
)


SLOT_FREQUENCIES_HZ = (7.2, 9.0, 12.0)


class M20SceneBoundBatchConsumer(BatchIdempotentConsumer):
    """Validate scene identity and M20 source/destination semantics before ACK."""

    def __init__(self, canonical_spec: dict[str, Any]):
        super().__init__()
        self.canonical_spec = canonical_spec
        self.scene_registry = M20SceneSnapshotRegistry()
        self.snapshot = None
        self.runtime_spec = None
        self.plan = None
        self.action = None

    def accept(self, payload):
        batch = validate_batch_message(payload)
        snapshot, runtime_spec = self.scene_registry.accept_confirmed_batch(batch, self.canonical_spec)
        selections = batch["selections"]
        if len(selections) != 2:
            raise M20ActionResolutionError("M20 PC runtime requires one ordered source/destination pair")

        entries = []
        for index, selection in enumerate(selections):
            target_id = selection["targetId"]
            entity = next((
                item for item in runtime_spec["entities"]
                if item.get("targetId") == target_id and item.get("selectable")
            ), None)
            if entity is None:
                raise M20AssistiveSceneError("M20 batch TargetId is absent from its accepted scene snapshot")
            slot = selection["slotIndex"]
            entries.append(QueueEntry(
                selection_id=selection["selectionId"],
                candidate=Candidate(entity["logicalBlockId"], target_id, entity["displayLabel"]),
                page_id=selection.get("pageId") or "quest-m20-submit",
                page_epoch=int(selection.get("pageEpoch", 0)),
                slot_index=slot,
                nominal_frequency_hz=SLOT_FREQUENCIES_HZ[slot],
                build_slot_index=None,
            ))
        plan = CommitPlan(
            ordered_selections=tuple(entries),
            batches=(CommittedBatch(0, tuple(entries)),),
        )
        action = resolve_source_destination(plan.ordered_selections, runtime_spec)

        # All fail-closed M20 checks above run before the generic consumer returns
        # and the socket receiver sends its batch_ack.
        receipt = super().accept(payload)
        self.snapshot = snapshot
        self.runtime_spec = runtime_spec
        self.plan = plan
        self.action = action
        return receipt


def receive_and_execute_one_batch(
    *,
    host: str = "0.0.0.0",
    port: int = 11001,
    timeout_seconds: float = 600.0,
    output_dir: Path,
    spec_path: Path = DEFAULT_SPEC_PATH,
) -> dict[str, Any]:
    canonical_spec, spec_sha256 = load_spec(spec_path)
    consumer = M20SceneBoundBatchConsumer(canonical_spec)
    receipt = consume_one_batch(host, port, timeout_seconds, receiver=consumer)
    attempt_dir = _new_attempt_dir(output_dir)
    _write_json(attempt_dir / "quest_confirmed_batch.json", receipt.payload)
    _write_json(attempt_dir / "scene_layout_snapshot.json", consumer.snapshot)
    execution = _execute_action(
        consumer.plan,
        consumer.action,
        consumer.runtime_spec,
        attempt_dir,
        consumer.snapshot,
        consumer.scene_registry,
        canonical_spec,
    )
    report = {
        "schemaVersion": 1,
        "status": execution["status"],
        "source": "quest_tcp_target_batch_confirmed",
        "batchId": receipt.batch["batchId"],
        "sceneId": consumer.snapshot["sceneId"],
        "randomSeed": consumer.snapshot["randomSeed"],
        "canonicalSpecSha256": spec_sha256,
        "orderedTargetIds": [item.target_id for item in consumer.plan.ordered_selections],
        "sceneSnapshotSha256": execution["sceneSnapshotSha256"],
        "batchAckSent": True,
        "nd8Opened": False,
        "physicalRobotOperated": False,
        "execution": execution,
        "evidenceDirectory": str(attempt_dir),
    }
    _write_json(attempt_dir / "acceptance.json", report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=11001)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output_dir = args.output_dir if args.output_dir.is_absolute() else Path(__file__).resolve().parents[1] / args.output_dir
    try:
        report = receive_and_execute_one_batch(
            host=args.host,
            port=args.port,
            timeout_seconds=args.timeout_seconds,
            output_dir=output_dir,
            spec_path=args.spec,
        )
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        return 0 if report["status"] == "PASS" else 1
    except Exception as error:
        failure = {
            "status": "FAIL_CLOSED",
            "failureType": type(error).__name__,
            "failure": " ".join(str(error).split()),
            "nd8Opened": False,
            "physicalRobotOperated": False,
        }
        print(json.dumps(failure, indent=2, sort_keys=True, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
