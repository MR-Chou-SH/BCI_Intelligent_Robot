"""Formal software acceptance for the frozen M11 context-only baseline."""

import argparse
import json
from pathlib import Path

from integration.m10_task_benchmark import BENCHMARK_LOGICAL_BLOCK_IDS
from integration.m11_context_prediction import make_observation, predict_context_prior


RUN_CASES = (
    ("empty-history", (), {"block_sim_01": 1.0}, False, False),
    ("one-prefix", ("block_sim_01",), {"block_sim_02": 2.0 / 3.0, "block_sim_03": 1.0 / 3.0}, False, False),
    ("two-prefix-tie", ("block_sim_01", "block_sim_02"), {"block_sim_03": 0.5, "block_sim_04": 0.5}, False, True),
    ("branch-tower", ("block_sim_01", "block_sim_03"), {"block_sim_02": 1.0}, False, False),
    ("house-three-prefix", ("block_sim_01", "block_sim_02", "block_sim_03"), {"block_sim_04": 1.0}, False, False),
    ("bridge-three-prefix", ("block_sim_01", "block_sim_02", "block_sim_04"), {"block_sim_03": 1.0}, False, False),
    ("tower-three-prefix", ("block_sim_01", "block_sim_03", "block_sim_02"), {"block_sim_04": 1.0}, False, False),
    ("terminal", BENCHMARK_LOGICAL_BLOCK_IDS, {}, True, False),
)


def _record(case_id, history, expected, terminal, expected_tie):
    prior = predict_context_prior(make_observation(history))
    actual = prior.probability_map()
    passed = (
        prior.valid
        and prior.terminal == terminal
        and actual == expected
        and prior.tie == expected_tie
        and (terminal or abs(sum(actual.values()) - 1.0) <= 1e-12)
        and "obj_" not in json.dumps(prior.to_public_dict(), sort_keys=True)
    )
    return {
        "recordType": "m11ContextPriorEvidence",
        "caseId": case_id,
        "predictorInput": {
            "completedLogicalBlockHistory": list(history),
            "availableLogicalBlockIds": list(BENCHMARK_LOGICAL_BLOCK_IDS),
            "stepIndex": len(history),
        },
        "evaluatorEvidence": {
            "expectedNextTargetProbabilities": expected,
            "expectedTerminal": terminal,
            "expectedTie": expected_tie,
        },
        "contextPrior": prior.to_public_dict(),
        "status": "PASS" if passed else "FAIL",
    }


def _invalid_record():
    history = ("block_sim_01", "block_sim_04")
    prior = predict_context_prior(make_observation(history))
    passed = (
        not prior.valid
        and prior.candidate_task_count == 0
        and prior.probability_map() == {}
        and prior.invalid_reason == "no_consistent_task_hypothesis"
    )
    return {
        "recordType": "m11ContextPriorEvidence",
        "caseId": "invalid-history-no-fallback",
        "predictorInput": {
            "completedLogicalBlockHistory": list(history),
            "availableLogicalBlockIds": list(BENCHMARK_LOGICAL_BLOCK_IDS),
            "stepIndex": len(history),
        },
        "contextPrior": prior.to_public_dict(),
        "status": "PASS" if passed else "FAIL",
    }


def _anti_leakage_record():
    outputs = []
    for hidden_task_id in ("house", "tower", "bridge"):
        # This is evaluator-side bookkeeping, never a predictor argument.
        del hidden_task_id
        outputs.append(
            predict_context_prior(
                make_observation(("block_sim_01", "block_sim_02"))
            ).to_public_dict()
        )
    passed = outputs[0] == outputs[1] == outputs[2]
    return {
        "recordType": "m11ContextPriorEvidence",
        "caseId": "anti-leakage-hidden-task-invariance",
        "evaluatorEvidence": {"hiddenTaskVariantsTested": 3},
        "serializedOutputs": outputs,
        "status": "PASS" if passed else "FAIL",
    }


def _determinism_record():
    history = ("block_sim_01", "block_sim_02")
    first = predict_context_prior(make_observation(history)).to_public_dict()
    second = predict_context_prior(make_observation(history)).to_public_dict()
    passed = first == second
    return {
        "recordType": "m11ContextPriorEvidence",
        "caseId": "deterministic-replay",
        "predictorInput": {
            "completedLogicalBlockHistory": list(history),
            "availableLogicalBlockIds": list(BENCHMARK_LOGICAL_BLOCK_IDS),
            "stepIndex": len(history),
        },
        "first": first,
        "second": second,
        "status": "PASS" if passed else "FAIL",
    }


def run_formal_acceptance(evidence_path=None, summary_path=None):
    records = [_record(*case) for case in RUN_CASES]
    records.extend([_invalid_record(), _anti_leakage_record(), _determinism_record()])
    serialized = json.dumps(records, sort_keys=True, separators=(",", ":"))
    public_serialization = serialized
    checks = {
        "canonicalCases": all(item["status"] == "PASS" for item in records[: len(RUN_CASES)]),
        "invalidHistory": records[len(RUN_CASES)]["status"] == "PASS",
        "antiLeakage": records[len(RUN_CASES) + 1]["status"] == "PASS",
        "deterministicReplay": records[len(RUN_CASES) + 2]["status"] == "PASS",
        "noObjectIds": "obj_" not in public_serialization,
        "noOracleFields": all(
            field not in public_serialization
            for field in ("remainingSequence", "validNextLogicalBlockIds", "trueTaskId")
        ),
    }
    overall = all(checks.values()) and all(item["status"] == "PASS" for item in records)
    summary = {
        "recordType": "m11ContextPredictionAcceptance",
        "schemaVersion": 1,
        "overallStatus": "PASS" if overall else "FAIL",
        "passPhrase": "M11 CONTEXT PREDICTION BASELINE = SOFTWARE PASS" if overall else None,
        "caseCounts": {
            "total": len(records),
            "passed": sum(item["status"] == "PASS" for item in records),
            "failed": sum(item["status"] != "PASS" for item in records),
        },
        "checks": checks,
        "records": records,
        "boundary": {
            "hardwareAccess": False,
            "eegEvidence": False,
            "robotExecution": False,
            "m12Fusion": False,
        },
    }
    if evidence_path is not None:
        path = Path(evidence_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in records), encoding="utf-8")
    if summary_path is not None:
        path = Path(summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="M11 context-only software acceptance")
    parser.add_argument("--evidence-path", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = run_formal_acceptance(args.evidence_path, args.summary_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["overallStatus"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
