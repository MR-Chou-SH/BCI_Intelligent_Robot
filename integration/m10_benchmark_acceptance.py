"""Fixture-driven formal software acceptance for the M10 task benchmark."""

import argparse
import json
from pathlib import Path

from integration.m10_task_benchmark import (
    FIXTURE_PATH,
    EpisodeOutcome,
    TaskStatus,
    episode_outcome,
    load_benchmark_fixture,
    load_task_definitions,
    replay_sequence,
    task_context,
)
from integration.m10_task_benchmark_runner import run_episode


BENCHMARK_ID = "m10-sequential-task-benchmark"
BENCHMARK_VERSION = 1
ACCEPTANCE_ID = "m10.1-formal-software-benchmark"
SUMMARY_SCHEMA_VERSION = 1
EPISODE_GROUPS = ("validFull", "validPartial", "invalidTransitions")
DEFAULT_EVIDENCE_PATH = Path("artifacts") / "m10" / "formal_acceptance_episodes.jsonl"


def _outcome_for_task_status(status):
    if status == TaskStatus.COMPLETED.value:
        return EpisodeOutcome.COMPLETED.value
    if status == TaskStatus.INVALID.value:
        return EpisodeOutcome.INVALID.value
    return EpisodeOutcome.VALID_INCOMPLETE.value


def _input_sequence(group_name, case):
    if group_name == "invalidTransitions":
        return tuple(case["acceptedPrefix"]) + (case["logicalBlockId"],)
    return tuple(case["logicalBlockIds"])


def _evaluate_case(group_name, case, definitions):
    task_id = case["taskId"]
    logical_block_ids = _input_sequence(group_name, case)
    result = run_episode(task_id, logical_block_ids, task_definitions=definitions)
    final_state = result["finalState"]
    failures = []

    expected_task_status = case.get(
        "expectedStatus",
        TaskStatus.COMPLETED.value if group_name == "validFull" else None,
    )
    if expected_task_status is not None and final_state["status"] != expected_task_status:
        failures.append(
            "expected final task status {!r}, got {!r}".format(
                expected_task_status, final_state["status"]
            )
        )

    expected_episode_outcome = case.get(
        "expectedEpisodeOutcome", _outcome_for_task_status(expected_task_status)
    )
    actual_episode_outcome = result["episodeOutcome"]
    if actual_episode_outcome != expected_episode_outcome:
        failures.append(
            "expected episode outcome {!r}, got {!r}".format(
                expected_episode_outcome, actual_episode_outcome
            )
        )

    if group_name in ("validFull", "validPartial"):
        if not all(transition["accepted"] for transition in result["transitions"]):
            failures.append("valid fixture sequence contained a rejected transition")
        if final_state["completedSequence"] != list(logical_block_ids):
            failures.append("accepted completed sequence does not match input sequence")

    if group_name == "invalidTransitions":
        expected_prefix = list(case["acceptedPrefix"])
        if final_state["completedSequence"] != expected_prefix:
            failures.append(
                "expected accepted prefix {!r}, got {!r}".format(
                    expected_prefix, final_state["completedSequence"]
                )
            )
        if not result["transitions"] or result["transitions"][-1]["accepted"]:
            failures.append("declared invalid case did not end with a rejection")
        else:
            actual_reason = result["transitions"][-1]["reasonCode"]
            expected_reason = case["expectedReasonCode"]
            if actual_reason != expected_reason:
                failures.append(
                    "expected rejection {!r}, got {!r}".format(
                        expected_reason, actual_reason
                    )
                )
        if final_state["status"] == TaskStatus.INVALID.value and case["logicalBlockId"] in final_state[
            "completedSequence"
        ]:
            failures.append("rejected invalid selection entered the completed prefix")

    result.update(
        {
            "caseId": case["episodeId"],
            "caseGroup": group_name,
            "benchmarkCaseStatus": "PASS" if not failures else "FAIL",
            "benchmarkCaseFailures": failures,
            "expected": {
                "finalTaskStatus": expected_task_status,
                "episodeOutcome": expected_episode_outcome,
                "rejectionReason": case.get("expectedReasonCode"),
            },
        }
    )
    return result


def _shared_prefixes(definitions):
    prefixes = {}
    for definition in definitions.values():
        sequence = definition.ordered_logical_block_ids
        for length in range(1, len(sequence)):
            prefix = sequence[:length]
            prefixes.setdefault(prefix, []).append(definition.task_id)
    return tuple(
        (prefix, tuple(sorted(task_ids)))
        for prefix, task_ids in sorted(prefixes.items())
        if len(task_ids) > 1
    )


def _evaluate_branching(definitions):
    evidence = []
    for prefix, task_ids in _shared_prefixes(definitions):
        valid_next_by_task = {}
        failures = []
        for task_id in task_ids:
            definition = definitions[task_id]
            _transitions, state = replay_sequence(definition, prefix)
            context = task_context(definition, state)
            actual = list(context.valid_next_logical_block_ids)
            expected = [definition.ordered_logical_block_ids[len(prefix)]]
            valid_next_by_task[task_id] = actual
            if actual != expected:
                failures.append(
                    "{} expected valid-next {!r}, got {!r}".format(
                        task_id, expected, actual
                    )
                )

        distinct_next = sorted(
            {logical_id for values in valid_next_by_task.values() for logical_id in values}
        )
        if len(distinct_next) < 2:
            failures.append("shared prefix does not diverge to at least two next IDs")

        evidence.append(
            {
                "recordType": "branchingEvidence",
                "prefixLogicalBlockIds": list(prefix),
                "taskValidNextLogicalBlockIds": valid_next_by_task,
                "distinctValidNextLogicalBlockIds": distinct_next,
                "benchmarkCaseStatus": "PASS" if not failures else "FAIL",
                "failures": failures,
            }
        )
    return tuple(evidence)


def _summary_case(result):
    return {
        "caseId": result["caseId"],
        "caseGroup": result["caseGroup"],
        "taskId": result["task"]["taskId"],
        "benchmarkCaseStatus": result["benchmarkCaseStatus"],
        "episodeOutcome": result["episodeOutcome"],
        "finalTaskStatus": result["finalState"]["status"],
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


def run_formal_acceptance(fixture_path=FIXTURE_PATH, evidence_path=DEFAULT_EVIDENCE_PATH):
    """Run all fixture cases and emit deterministic summary/evidence records."""
    fixture = load_benchmark_fixture(fixture_path)
    definitions = load_task_definitions(fixture_path)
    episode_results = []
    for group_name in EPISODE_GROUPS:
        cases = fixture.get("episodes", {}).get(group_name)
        if not isinstance(cases, list) or not cases:
            raise ValueError("M10 fixture episode group must be a non-empty list: {}".format(group_name))
        for case in cases:
            episode_results.append(_evaluate_case(group_name, case, definitions))

    branching_evidence = _evaluate_branching(definitions)
    evidence_records = [
        dict(result, recordType="episodeEvidence") for result in episode_results
    ] + list(branching_evidence)
    if evidence_path is not None:
        _write_jsonl(evidence_path, evidence_records)

    passed_cases = sum(
        result["benchmarkCaseStatus"] == "PASS" for result in episode_results
    )
    failed_cases = len(episode_results) - passed_cases
    branching_passed = all(
        record["benchmarkCaseStatus"] == "PASS" for record in branching_evidence
    )
    overall_status = "PASS" if failed_cases == 0 and branching_passed else "FAIL"
    group_summaries = []
    for group_name in EPISODE_GROUPS:
        group_results = [
            result for result in episode_results if result["caseGroup"] == group_name
        ]
        group_summaries.append(
            {
                "caseGroup": group_name,
                "totalCases": len(group_results),
                "passedCases": sum(
                    result["benchmarkCaseStatus"] == "PASS" for result in group_results
                ),
                "failedCases": sum(
                    result["benchmarkCaseStatus"] == "FAIL" for result in group_results
                ),
                "cases": [_summary_case(result) for result in group_results],
            }
        )

    canonical_tasks = [
        _summary_case(result)
        for result in episode_results
        if result["caseGroup"] == "validFull"
    ]
    return {
        "schemaVersion": SUMMARY_SCHEMA_VERSION,
        "benchmarkId": BENCHMARK_ID,
        "benchmarkVersion": BENCHMARK_VERSION,
        "acceptanceId": ACCEPTANCE_ID,
        "overallStatus": overall_status,
        "caseCounts": {
            "total": len(episode_results),
            "passed": passed_cases,
            "failed": failed_cases,
        },
        "canonicalTasks": canonical_tasks,
        "fixtureGroups": group_summaries,
        "branchingStatus": "PASS" if branching_passed else "FAIL",
        "branchingEvidence": list(branching_evidence),
        "evidenceFormat": "JSONL append-only episodeEvidence and branchingEvidence records",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture",
        default=str(FIXTURE_PATH),
        help="M10 task fixture JSON path; defaults to the checked-in fixture.",
    )
    parser.add_argument(
        "--evidence-path",
        default=str(DEFAULT_EVIDENCE_PATH),
        help="Append-only JSONL evidence path.",
    )
    parser.add_argument(
        "--summary-path",
        help="Optional machine-readable summary JSON output path.",
    )
    args = parser.parse_args(argv)
    try:
        summary = run_formal_acceptance(args.fixture, args.evidence_path)
        if args.summary_path:
            _write_summary(args.summary_path, summary)
    except (OSError, TypeError, ValueError, KeyError) as error:
        print(
            json.dumps(
                {
                    "schemaVersion": SUMMARY_SCHEMA_VERSION,
                    "benchmarkId": BENCHMARK_ID,
                    "acceptanceId": ACCEPTANCE_ID,
                    "overallStatus": "BLOCKED",
                    "error": str(error),
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if summary["overallStatus"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
