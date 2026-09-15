"""Replay deterministic M10 sequential-task episodes without MuJoCo or hardware."""

import argparse
import json

from integration.m10_task_benchmark import (
    TASK_DEFINITIONS,
    episode_outcome,
    replay_sequence,
    task_context,
)


def run_episode(task_id, logical_block_ids, task_definitions=None):
    logical_block_ids = tuple(logical_block_ids)
    if task_definitions is None:
        task_definitions = TASK_DEFINITIONS
    try:
        definition = task_definitions[task_id]
    except KeyError as error:
        raise ValueError("unknown taskId: {!r}".format(task_id)) from error

    transitions, final_state = replay_sequence(definition, logical_block_ids)
    return {
        "episodeOutcome": episode_outcome(final_state).value,
        "task": definition.to_public_dict(),
        "inputLogicalBlockIds": list(logical_block_ids),
        "transitions": [transition.to_public_dict() for transition in transitions],
        "finalState": final_state.to_public_dict(),
        "taskContext": task_context(definition, final_state).to_public_dict(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASK_DEFINITIONS), default="house")
    parser.add_argument(
        "--logical-block-ids",
        help="Comma-separated logical IDs; defaults to the task's full valid sequence.",
    )
    args = parser.parse_args(argv)
    ids = (
        TASK_DEFINITIONS[args.task].ordered_logical_block_ids
        if args.logical_block_ids is None
        else tuple(value.strip() for value in args.logical_block_ids.split(","))
    )
    output = run_episode(args.task, ids)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if output["episodeOutcome"] != "invalid" else 1


if __name__ == "__main__":
    raise SystemExit(main())
