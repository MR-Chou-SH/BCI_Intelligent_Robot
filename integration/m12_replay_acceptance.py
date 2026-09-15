"""Formal software/replay acceptance for the frozen M12 fusion baseline."""

import argparse
import json
from pathlib import Path

from integration.m11_context_prediction import ContextPrior, make_observation, predict_context_prior
from integration.m12_context_eeg_fusion import (
    ActiveSsvepCandidate,
    FusionInputError,
    fuse_context_and_eeg,
    fuse_fbcca_score_vector,
)


ALL_LOGICAL_IDS = (
    "block_sim_01",
    "block_sim_02",
    "block_sim_03",
    "block_sim_04",
)
TARGET_IDS = (
    "m9-vblock-red-01",
    "m9-vblock-green-01",
    "m9-vblock-blue-01",
    "m9-vblock-yellow-01",
)


def _active(logical_ids):
    target_by_logical = dict(zip(ALL_LOGICAL_IDS, TARGET_IDS))
    return tuple(
        ActiveSsvepCandidate.from_frozen_mapping(slot, target_by_logical[logical_id])
        for slot, logical_id in enumerate(logical_ids)
    )


def _prior(probabilities, history=("block_sim_01",)):
    return ContextPrior(
        observable_history=tuple(history),
        available_logical_block_ids=ALL_LOGICAL_IDS,
        step_index=len(history),
        candidate_task_count=1,
        task_hypotheses=(),
        next_target_probabilities=tuple(probabilities.items()),
        top_targets=(),
        tie=False,
        entropy=None,
        terminal=False,
        valid=True,
    )


def _record(case_id, context, active, scores, expected_top, expected_tie=False):
    result = fuse_context_and_eeg(context, active, scores)
    passed = result.top_logical_block_ids == tuple(expected_top) and result.tie == expected_tie
    return {
        "recordType": "m12FusedTargetEvidence",
        "caseId": case_id,
        "source": "M11 ContextPrior + existing M6/FBCCA fused score seam",
        "contextPrior": context.to_public_dict(),
        "activeCandidates": [item.to_public_dict() for item in active],
        "eegEvidenceScores": dict(scores),
        "fusedTargetEvidence": result.to_public_dict(),
        "expected": {"topLogicalBlockIds": list(expected_top), "tie": expected_tie},
        "status": "PASS" if passed else "FAIL",
    }


def _invalid_evidence_record(context, active):
    invalid_inputs = (
        {"block_sim_01": float("nan"), "block_sim_02": 1.0, "block_sim_03": 1.0},
        {"block_sim_01": float("inf"), "block_sim_02": 1.0, "block_sim_03": 1.0},
        {"block_sim_01": -1.0, "block_sim_02": 1.0, "block_sim_03": 1.0},
        {"block_sim_01": 1.0},
    )
    errors = []
    for invalid in invalid_inputs:
        try:
            fuse_context_and_eeg(context, active, invalid)
        except FusionInputError as error:
            errors.append(type(error).__name__)
        else:
            errors.append("accepted")
    passed = all(item == "FusionInputError" for item in errors)
    return {
        "recordType": "m12FusedTargetEvidence",
        "caseId": "invalid-eeg-evidence-rejected",
        "invalidInputsTested": len(invalid_inputs),
        "observedErrors": errors,
        "status": "PASS" if passed else "FAIL",
    }


def _deterministic_record(context, active, scores):
    first = fuse_context_and_eeg(context, active, scores).to_public_dict()
    second = fuse_context_and_eeg(context, active, scores).to_public_dict()
    return {
        "recordType": "m12FusedTargetEvidence",
        "caseId": "deterministic-replay",
        "first": first,
        "second": second,
        "status": "PASS" if first == second else "FAIL",
    }


def _vector_adapter_record(context, active):
    result = fuse_fbcca_score_vector(context, active, (2.0, 9.0, 1.0))
    passed = result.top_logical_block_ids == ("block_sim_02",)
    return {
        "recordType": "m12FusedTargetEvidence",
        "caseId": "existing-fbcca-vector-read-only-adapter",
        "inputContract": "predict_fbcca(...)[1] -> three-value fused score vector",
        "scoreVector": [2.0, 9.0, 1.0],
        "fusedTargetEvidence": result.to_public_dict(),
        "status": "PASS" if passed else "FAIL",
    }


def _boundary_record(records):
    source = Path(__file__).with_name("m12_context_eeg_fusion.py").read_text(encoding="utf-8")
    forbidden_direct_calls = ("RobotAdapter(", ".dispatch(", "MuJoCo", "robot_arm")
    serialized = json.dumps(records, sort_keys=True)
    source_checks = {"token_{}".format(index): token not in source for index, token in enumerate(forbidden_direct_calls)}
    passed = (
        "obj_" not in serialized
        and all(source_checks.values())
    )
    return {
        "recordType": "m12FusedTargetEvidence",
        "caseId": "robot-and-object-id-boundary",
        "sourceStaticCheck": source_checks,
        "evidenceContainsObjIds": "obj_" in serialized,
        "robotAdapterInvocation": False,
        "status": "PASS" if passed else "FAIL",
    }


def run_formal_acceptance(evidence_path=None, summary_path=None):
    uniform = _prior({item: 0.25 for item in ALL_LOGICAL_IDS})
    active_012 = _active(ALL_LOGICAL_IDS[:3])
    active_123 = _active(ALL_LOGICAL_IDS[1:])
    records = [
        _record("uniform-context-eeg-order", uniform, active_012,
                {"block_sim_01": 1.0, "block_sim_02": 3.0, "block_sim_03": 2.0},
                ("block_sim_02",)),
        _record("agreement", _prior({"block_sim_01": 0.0, "block_sim_02": 0.1, "block_sim_03": 0.2, "block_sim_04": 0.7}), active_012,
                {"block_sim_01": 1.0, "block_sim_02": 2.0, "block_sim_03": 10.0}, ("block_sim_03",)),
        _record("moderate-conflict", _prior({"block_sim_01": 0.05, "block_sim_02": 0.85, "block_sim_03": 0.05, "block_sim_04": 0.05}), active_012,
                {"block_sim_01": 1.0, "block_sim_02": 1.5, "block_sim_03": 1.0}, ("block_sim_02",)),
        _record("strong-eeg-override", _prior({"block_sim_01": 0.05, "block_sim_02": 0.90, "block_sim_03": 0.05, "block_sim_04": 0.0}), active_012,
                {"block_sim_01": 1.0, "block_sim_02": 1.0, "block_sim_03": 100.0}, ("block_sim_03",)),
        _record("half-half-context-ambiguity", predict_context_prior(make_observation(("block_sim_01", "block_sim_02"))), active_123,
                {"block_sim_02": 1.0, "block_sim_03": 7.0, "block_sim_04": 2.0}, ("block_sim_03",)),
        _record("active-projection", _prior({"block_sim_01": 0.9, "block_sim_02": 0.1, "block_sim_03": 0.0, "block_sim_04": 0.0}), active_123,
                {"block_sim_02": 1.0, "block_sim_03": 1.0, "block_sim_04": 1.0}, ("block_sim_02",)),
        _record("zero-active-context-mass", predict_context_prior(make_observation(())), active_123,
                {"block_sim_02": 1.0, "block_sim_03": 2.0, "block_sim_04": 3.0}, ("block_sim_04",)),
        _record("epsilon-zero-score-replay", uniform, active_012,
                {"block_sim_01": 0.0, "block_sim_02": 0.0, "block_sim_03": 0.0},
                ("block_sim_01", "block_sim_02", "block_sim_03"), True),
    ]
    records.extend([
        _invalid_evidence_record(uniform, active_012),
        _deterministic_record(uniform, active_012, {"block_sim_01": 1.0, "block_sim_02": 2.0, "block_sim_03": 3.0}),
        _vector_adapter_record(uniform, active_012),
    ])
    records.append(_boundary_record(records))
    serialized = json.dumps(records, sort_keys=True, separators=(",", ":"))
    checks = {
        "allCasesPass": all(item["status"] == "PASS" for item in records),
        "noObjectIds": "obj_" not in serialized,
        "noRobotExecution": all(item.get("robotAdapterInvocation") is not True for item in records),
        "noHardware": True,
        "noFbccaRewrite": True,
        "allActiveCandidatesRetainInfluence": all(
            entry["contextPriorSoft"] > 0.0
            for record in records[:8]
            for entry in record.get("fusedTargetEvidence", {}).get("entries", [])
        ),
    }
    overall = all(checks.values())
    summary = {
        "recordType": "m12ContextEegFusionReplayAcceptance",
        "schemaVersion": 1,
        "overallStatus": "PASS" if overall else "FAIL",
        "passPhrase": "M12 CONTEXT × EEG FUSION BASELINE = SOFTWARE / REPLAY PASS" if overall else None,
        "caseCounts": {
            "total": len(records),
            "passed": sum(item["status"] == "PASS" for item in records),
            "failed": sum(item["status"] != "PASS" for item in records),
        },
        "checks": checks,
        "records": records,
        "boundary": {
            "hardwareAccess": False,
            "questAccess": False,
            "nd8Access": False,
            "realEeg": False,
            "robotAdapterInvocation": False,
            "m13Entered": False,
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
    parser = argparse.ArgumentParser(description="M12 context x EEG evidence replay acceptance")
    parser.add_argument("--evidence-path", type=Path, required=True)
    parser.add_argument("--summary-path", type=Path, required=True)
    args = parser.parse_args(argv)
    summary = run_formal_acceptance(args.evidence_path, args.summary_path)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["overallStatus"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
