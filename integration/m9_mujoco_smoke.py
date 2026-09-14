"""Optional headless M9 smoke against the copied FR3/UMI MuJoCo baseline.

Run only in an existing Python environment that already has the documented
MuJoCo dependencies. This uses a synthetic confirmed selection and writes no
files; it does not access Quest, EEG hardware, or a physical robot.
"""

import argparse
import json
import sys

from integration.m9_mujoco_execution import (
    DEFAULT_M9_SCENE_BINDINGS,
    SceneBindingRegistry,
    RobotOperation,
    create_execution_requests,
    create_fr3_umi_mujoco_adapter,
)
from integration.m9_robot_adapter import (
    CONFIRMED_BATCH_MESSAGE,
    CONFIRMED_BATCH_PROVENANCE,
    FROZEN_SELECTION_PROVENANCE,
    PROTOCOL_VERSION,
)


SMOKE_TARGET_ID = "target-m9-smoke"
SMOKE_LOGICAL_BLOCK_ID = "block_sim_01"


def _confirmed_smoke_batch():
    batch_id = "m9-smoke-batch"
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "messageType": CONFIRMED_BATCH_MESSAGE,
        "batchId": batch_id,
        "confirmedBatch": {
            "batchId": batch_id,
            "groupId": "m9-smoke-group",
            "groupIndex": 0,
            "submittedUtc": "2026-09-14T05:00:00.0000000Z",
            "provenance": CONFIRMED_BATCH_PROVENANCE,
            "selections": [
                {
                    "selectionId": "m9-smoke-selection",
                    "predictedClassIndex": 0,
                    "slotIndex": 0,
                    "targetId": SMOKE_TARGET_ID,
                    "semanticLabel": "simulation smoke only",
                    "resolvedUtc": "2026-09-14T05:00:00.0000000Z",
                    "provenance": FROZEN_SELECTION_PROVENANCE,
                }
            ],
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation",
        choices=[operation.value for operation in RobotOperation],
        default=RobotOperation.PICK_AND_PLACE.value,
    )
    args = parser.parse_args(argv)
    operation = RobotOperation(args.operation)

    request = create_execution_requests(
        _confirmed_smoke_batch(),
        {SMOKE_TARGET_ID: SMOKE_LOGICAL_BLOCK_ID},
        operation,
        request_id_factory=lambda: "m9-smoke-request",
    )[0]
    try:
        adapter = create_fr3_umi_mujoco_adapter(
            scene_bindings=SceneBindingRegistry(DEFAULT_M9_SCENE_BINDINGS)
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "BLOCKED",
                    "requestId": request.request_id,
                    "logicalBlockId": request.logical_block_id,
                    "operation": request.operation.value,
                    "reason": "{}: {}".format(type(error).__name__, str(error)),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    result = adapter.execute(request)
    payload = {
        "status": "PASS" if result.success else "FAIL",
        "requestId": result.request_id,
        "logicalBlockId": result.logical_block_id,
        "operation": result.operation.value,
        "success": result.success,
        "failureCode": result.failure_code,
        "failureReason": result.failure_reason,
        "executionProvenance": result.execution_provenance,
        "backendExecutionId": result.backend_execution_id,
        "startedUtc": result.started_utc,
        "completedUtc": result.completed_utc,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
