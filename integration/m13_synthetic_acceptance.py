"""Machine-readable M13 synthetic trajectory acceptance."""

import argparse
import json
from pathlib import Path

from integration.m13_dynamic_stopping import DynamicStoppingPolicy, run_dynamic_stopping
from integration.m13_trajectory_fixture import make_snapshot


def _trajectory(*items):
    return [make_snapshot(index, scores, context, provenance={"source": "synthetic M13 acceptance"}) for index, (scores, context) in enumerate(items)]


def _case_records():
    return [
        ("case_01_strong_stable_agreement", _trajectory(((2.0, .01, .01), (.98, .005, .005)), ((2.0, .01, .01), (.98, .005, .005))), lambda policy, decision, snapshots: decision.early_stop and decision.stop_window == 1 and decision.selected_logical_block_id == "block_sim_01"),
        ("case_02_single_window_transient_spike", _trajectory(((2.0, .01, .01), (.98, .005, .005)), ((1.0, 1.0, 1.0), (.25, .25, .25)), ((2.0, .01, .01), (.98, .005, .005))), lambda policy, decision, snapshots: not decision.early_stop),
        ("case_03_target_switch", _trajectory(((2.0, .01, .01), (.98, .005, .005)), ((.01, 2.0, .01), (.005, .98, .005)), ((.01, 2.0, .01), (.005, .98, .005))), lambda policy, decision, snapshots: decision.early_stop and decision.stop_window == 2 and decision.selected_logical_block_id == "block_sim_02"),
        ("case_04_context_eeg_conflict", _trajectory(((.29, .30, .01), (.98, .005, .005)), ((.29, .30, .01), (.98, .005, .005))), lambda policy, decision, snapshots: not decision.early_stop and all(not item.eeg_confirmation_pass for item in policy.evaluations)),
        ("case_05_strong_eeg_override", _trajectory(((.01, 2.0, .01), (.98, .005, .005)), ((.01, 2.0, .01), (.98, .005, .005))), lambda policy, decision, snapshots: decision.early_stop and decision.selected_logical_block_id == "block_sim_02"),
        ("case_06_ambiguous_fused_evidence", _trajectory(((1.0, .9, .1), (.25, .25, .25)), ((1.0, .9, .1), (.25, .25, .25))), lambda policy, decision, snapshots: not decision.early_stop and decision.stop_reason == "full_window_fallback"),
        ("case_07_never_confident_full_window_fallback", _trajectory(((1.0, .9, .1), (.25, .25, .25)), ((1.0, .9, .1), (.25, .25, .25)), ((1.0, .9, .1), (.25, .25, .25))), lambda policy, decision, snapshots: decision.decision_made and not decision.early_stop and decision.stop_reason == "full_window_fallback"),
        ("case_08_invalid_evidence", None, None),
        ("case_09_deterministic_replay", _trajectory(((2.0, .01, .01), (.98, .005, .005)), ((2.0, .01, .01), (.98, .005, .005))), None),
        ("case_10_uniform_context_no_advantage", _trajectory(((.1, 1.0, .05), (.25, .25, .25)), ((.1, 1.0, .05), (.25, .25, .25))), lambda policy, decision, snapshots: decision.early_stop and decision.selected_logical_block_id == "block_sim_02"),
    ]


def _run_one(name, snapshots, assertion):
    if name == "case_08_invalid_evidence":
        policy = DynamicStoppingPolicy()
        evaluation = policy.reject_invalid(0, 2.0)
        decision = policy.finalize()
        passed = evaluation.reason == "invalid_evidence" and not decision.decision_made and decision.stop_reason == "invalid_evidence"
        return {"case": name, "windows": [evaluation.to_public_dict()], "decision": decision.to_public_dict(), "pass": passed}

    policy, decision = run_dynamic_stopping(snapshots)
    if name == "case_09_deterministic_replay":
        _, repeated = run_dynamic_stopping(snapshots)
        passed = decision.to_public_dict() == repeated.to_public_dict()
    else:
        passed = bool(assertion(policy, decision, snapshots))
    final_snapshot = snapshots[-1]
    reference_top = final_snapshot.fused_evidence.top_logical_block_ids
    reference_target = reference_top[0] if len(reference_top) == 1 else None
    selected = decision.selected_logical_block_id
    return {
        "case": name,
        "windows": [item.to_public_dict() for item in policy.evaluations],
        "decision": decision.to_public_dict(),
        "stoppedEarly": decision.early_stop,
        "stopWindow": decision.stop_window,
        "finalTarget": selected,
        "referenceFullWindowTarget": reference_target,
        "agreesWithReference": selected is not None and reference_target == selected,
        "windowsSaved": 0 if not decision.early_stop else len(snapshots) - len(policy.evaluations),
        "pass": passed,
    }


def run_acceptance():
    results = [_run_one(name, snapshots, assertion) for name, snapshots, assertion in _case_records()]
    return {
        "schemaVersion": 1,
        "recordType": "m13_synthetic_dynamic_stopping_acceptance",
        "status": "PASS" if all(item["pass"] for item in results) else "FAIL",
        "policy": {"fusedThreshold": 0.70, "marginThreshold": 0.20, "requiredConsecutive": 2, "eegConfirmation": "fused top must equal raw EEG top", "windowScheduleSource": "eeg.decoder.characterization.WINDOW_GRID_SECONDS", "onlineTimingSource": "eeg.decoder.pseudo_online"},
        "cases": results,
        "evidenceBoundary": {"questOperated": False, "nd8Operated": False, "robotOperated": False, "newRealEegCollected": False},
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-path", required=True)
    parser.add_argument("--summary-path", required=True)
    args = parser.parse_args(argv)
    summary = run_acceptance()
    evidence_path = Path(args.evidence_path)
    summary_path = Path(args.summary_path)
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with evidence_path.open("w", encoding="utf-8", newline="\n") as stream:
        for case in summary["cases"]:
            stream.write(json.dumps(case, sort_keys=True, separators=(",", ":")) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "caseCount": len(summary["cases"]), "summaryPath": str(summary_path)}, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
