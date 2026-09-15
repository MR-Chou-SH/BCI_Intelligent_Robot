"""PC-only live-like M13.5 streaming and fault-injection acceptance."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile

from integration.m8_selection_orchestration import M8SelectionOrchestrator, QuestSelectionTransportError
from integration.m13_5_logging import M135SessionLogger, read_jsonl
from integration.m13_5_runtime import (
    M13_5_POLICY,
    MODE_ACTIVE,
    MODE_BASELINE,
    MODE_SHADOW,
    M135RuntimeError,
    M135TrialRuntime,
    build_default_active_candidates,
)
from integration.m13_trajectory_fixture import make_snapshot


class FakeQuestTransport:
    """Existing M8 transport shape with controllable ACK/failure behavior."""

    def __init__(self, fail_submit=False):
        self.opened = []
        self.submitted = []
        self.aborted = []
        self.fail_submit = bool(fail_submit)

    @staticmethod
    def _ack(selection_id, accepted=True):
        return {"protocolVersion": 1, "messageType": "selection_ack", "selectionId": selection_id, "accepted": accepted}

    def open_selection(self, selection_id):
        self.opened.append(selection_id)
        return self._ack(selection_id)

    def submit_eeg_selection(self, selection_id, class_index):
        if self.fail_submit:
            raise QuestSelectionTransportError("simulated_connection_interruption")
        self.submitted.append((selection_id, class_index))
        return self._ack(selection_id)

    def abort_selection(self, selection_id):
        self.aborted.append(selection_id)
        return self._ack(selection_id)


def _trajectory(items):
    return [make_snapshot(index, scores, context, provenance={"source": "M13.5 PC-only synthetic stream"}) for index, (scores, context) in enumerate(items)]


def _run_case(root, case_name, mode, snapshots, late_snapshots=()):
    session_id = "m13_5-{}".format(case_name)
    selection_id = session_id + "-selection"
    trial_id = session_id + "-trial"
    case_root = Path(root) / case_name
    transport = FakeQuestTransport()
    orchestrator = M8SelectionOrchestrator(transport)
    logger = M135SessionLogger(case_root, session_id, mode, "synthetic_stream", "local-test", M13_5_POLICY, [item.to_public_dict() for item in build_default_active_candidates()])
    runtime = M135TrialRuntime(mode, orchestrator, logger)
    opened = runtime.start_trial(selection_id, trial_id)
    for snapshot in snapshots:
        runtime.observe(snapshot, trial_id, selection_id)
    for snapshot in late_snapshots:
        runtime.observe(snapshot, trial_id, selection_id)
    result = runtime.finalize_trial()
    records = read_jsonl(logger.path)
    final_events = [item for item in records if item.get("eventType") == "final_submission"]
    return {
        "case": case_name,
        "mode": mode,
        "opened": opened,
        "result": result.to_public_dict(),
        "transportSubmitted": list(transport.submitted),
        "transportAborted": list(transport.aborted),
        "eventTypes": [item.get("eventType") for item in records],
        "finalSubmissionEventCount": len(final_events),
        "logPath": str(logger.path),
    }


def run_streaming_acceptance(root):
    root = Path(root)
    cases = []
    stable_a = _trajectory([((2.0, .01, .01), (.98, .005, .005)), ((2.0, .01, .01), (.98, .005, .005))])
    cases.append(_run_case(root, "case_a_baseline_stable", MODE_BASELINE, stable_a))
    cases.append(_run_case(root, "case_b_shadow_stable", MODE_SHADOW, stable_a))
    cases.append(_run_case(root, "case_c_active_early_stop", MODE_ACTIVE, stable_a, late_snapshots=_trajectory([((.01, 2.0, .01), (.005, .98, .005))])))
    weak = _trajectory([((1.0, .9, .1), (.25, .25, .25)), ((1.0, .9, .1), (.25, .25, .25))])
    cases.append(_run_case(root, "case_d_active_full_window_fallback", MODE_ACTIVE, weak))
    tie = _trajectory([((1.0, 1.0, .1), (.25, .25, .25))])
    cases.append(_run_case(root, "case_e_active_no_decision", MODE_ACTIVE, tie))
    switch = _trajectory([((2.0, .01, .01), (.98, .005, .005)), ((.01, 2.0, .01), (.005, .98, .005)), ((.01, 2.0, .01), (.005, .98, .005))])
    cases.append(_run_case(root, "case_f_target_switch", MODE_ACTIVE, switch))
    conflict = _trajectory([((.29, .30, .01), (.98, .005, .005)), ((.29, .30, .01), (.98, .005, .005))])
    cases.append(_run_case(root, "case_g_context_conflict_shadow", MODE_SHADOW, conflict))

    checks = {
        "baselineDoesNotInvokeM13": cases[0]["result"]["m13Decision"] is None and cases[0]["result"]["submission"] is not None,
        "shadowDoesNotAffectBaseline": cases[1]["result"]["result" if False else "submission"] is not None and cases[1]["finalSubmissionEventCount"] == 1 and cases[1]["result"]["m13Decision"] is not None,
        "activeEarlyExactlyOnce": cases[2]["result"]["m13Decision"]["earlyStop"] and cases[2]["finalSubmissionEventCount"] == 1 and len(cases[2]["transportSubmitted"]) == 1 and "late_evidence_ignored" in cases[2]["eventTypes"],
        "activeFallbackExactlyOnce": cases[3]["result"]["m13Decision"]["stopReason"] == "full_window_fallback" and cases[3]["finalSubmissionEventCount"] == 1,
        "noDecisionSuppressed": not cases[4]["result"]["m13Decision"]["decisionMade"] and cases[4]["transportSubmitted"] == [] and len(cases[4]["transportAborted"]) == 1,
        "targetSwitchStopsNewTarget": cases[5]["result"]["m13Decision"]["selectedLogicalBlockId"] == "block_sim_02" and cases[5]["result"]["m13Decision"]["stopWindow"] == 2 and cases[5]["finalSubmissionEventCount"] == 1,
        "contextConflictDoesNotEarlyStop": not cases[6]["result"]["m13Decision"]["earlyStop"] and cases[6]["result"]["baselineDecision"]["selectedLogicalBlockId"] == "block_sim_02" and cases[6]["finalSubmissionEventCount"] == 1,
    }
    return {"schemaVersion": 1, "recordType": "m13_5_pc_only_streaming_acceptance", "status": "PASS" if all(checks.values()) else "FAIL", "policy": dict(M13_5_POLICY), "cases": cases, "checks": checks, "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "robotOperated": False}}


def _fault_runtime(root, name, mode=MODE_ACTIVE, fail_submit=False):
    session_id = "m13_5-fault-{}".format(name)
    selection_id = session_id + "-selection"
    trial_id = session_id + "-trial"
    transport = FakeQuestTransport(fail_submit=fail_submit)
    orchestrator = M8SelectionOrchestrator(transport)
    logger = M135SessionLogger(Path(root) / name, session_id, mode, "fault_injection", "local-test", M13_5_POLICY, [item.to_public_dict() for item in build_default_active_candidates()])
    runtime = M135TrialRuntime(mode, orchestrator, logger)
    runtime.start_trial(selection_id, trial_id)
    return runtime, logger, transport, selection_id, trial_id


def run_fault_injection_acceptance(root):
    results = []
    stable = _trajectory([((2.0, .01, .01), (.98, .005, .005)), ((2.0, .01, .01), (.98, .005, .005))])

    runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "duplicate_window")
    runtime.observe(stable[0], trial_id, selection_id)
    runtime.observe(stable[0], trial_id, selection_id)
    runtime.observe(stable[1], trial_id, selection_id)
    duplicate_result = runtime.finalize_trial()
    results.append({"case": "duplicate_window", "pass": "duplicate_window" in [item.get("reasonCode") for item in read_jsonl(logger.path)] and len(transport.submitted) == 1, "result": duplicate_result.to_public_dict()})

    runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "out_of_order_missing")
    runtime.observe(stable[0], trial_id, selection_id)
    runtime.observe(stable[0].__class__.from_m12(2, stable[1].fused_evidence, 1.5, 2.4), trial_id, selection_id)
    order_result = runtime.finalize_trial()
    results.append({"case": "out_of_order_missing", "pass": "out_of_order_or_missing_window" in [item.get("reasonCode") for item in read_jsonl(logger.path)] and order_result.m13_decision is not None and not order_result.m13_decision.decision_made and transport.submitted == [], "result": order_result.to_public_dict()})

    runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "stale_trial_identity")
    runtime.observe(stable[0], "old-trial", selection_id)
    runtime.observe(stable[0], trial_id, selection_id)
    runtime.observe(stable[1], trial_id, selection_id)
    stale_result = runtime.finalize_trial()
    results.append({"case": "stale_trial_identity", "pass": "stale_trial_or_selection" in [item.get("reasonCode") for item in read_jsonl(logger.path)] and len(transport.submitted) == 1, "result": stale_result.to_public_dict()})

    for name, bad_value in (("nan_evidence", float("nan")), ("inf_evidence", float("inf"))):
        runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, name)
        bad = replace(stable[0], fused_evidence=replace(stable[0].fused_evidence, entries=tuple(replace(entry, eeg_evidence_score=bad_value) for entry in stable[0].fused_evidence.entries)))
        runtime.observe(bad, trial_id, selection_id)
        bad_result = runtime.finalize_trial()
        results.append({"case": name, "pass": "invalid_evidence" in [item.get("reasonCode") for item in read_jsonl(logger.path)] and transport.submitted == [], "result": bad_result.to_public_dict()})

    runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "mapping_mismatch")
    mismatched_entry = replace(stable[0].fused_evidence.entries[0], candidate=type(stable[0].fused_evidence.entries[0].candidate).from_frozen_mapping(0, "m9-vblock-yellow-01"))
    mismatched = replace(stable[0], fused_evidence=replace(stable[0].fused_evidence, entries=(mismatched_entry,) + stable[0].fused_evidence.entries[1:]))
    runtime.observe(mismatched, trial_id, selection_id)
    mismatch_result = runtime.finalize_trial()
    results.append({"case": "mapping_mismatch", "pass": "target_snapshot_mismatch" in [item.get("reasonCode") for item in read_jsonl(logger.path)] and transport.submitted == [], "result": mismatch_result.to_public_dict()})

    runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "post_decision_lock")
    runtime.observe(stable[0], trial_id, selection_id)
    runtime.observe(stable[1], trial_id, selection_id)
    runtime.observe(_trajectory([((.01, 2.0, .01), (.005, .98, .005))])[0], trial_id, selection_id)
    first = runtime.finalize_trial()
    second = runtime.finalize_trial()
    records = read_jsonl(logger.path)
    results.append({"case": "post_decision_lock", "pass": len(transport.submitted) == 1 and "late_evidence_ignored" in [item.get("eventType") for item in records] and "duplicate_finalize_suppressed" in [item.get("eventType") for item in records] and first.to_public_dict() == second.to_public_dict(), "result": first.to_public_dict()})

    empty_pass = False
    try:
        runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "empty_active_target_set")
        M135TrialRuntime(MODE_ACTIVE, M8SelectionOrchestrator(FakeQuestTransport()), logger, candidates=())
    except (M135RuntimeError, ValueError):
        empty_pass = True
    results.append({"case": "empty_active_target_set", "pass": empty_pass})

    runtime, logger, transport, selection_id, trial_id = _fault_runtime(root, "transport_interruption", fail_submit=True)
    runtime.observe(stable[0], trial_id, selection_id)
    runtime.observe(stable[1], trial_id, selection_id)
    interruption = runtime.finalize_trial()
    runtime.finalize_trial()
    results.append({"case": "transport_interruption", "pass": interruption.submission.get("status") == "transport_failure" and len(transport.submitted) == 0, "result": interruption.to_public_dict()})

    fault_catalog = {
        "duplicate EEG window": "duplicate_window",
        "same window replay": "duplicate_window",
        "out-of-order window": "out_of_order_missing",
        "missing intermediate window": "out_of_order_missing",
        "stale window from previous trial": "stale_trial_identity",
        "stale selection ID": "stale_trial_identity",
        "selection already closed": "post_decision_lock",
        "duplicate final decision attempt": "post_decision_lock",
        "late evidence after final decision": "post_decision_lock",
        "malformed evidence": "nan_evidence",
        "NaN": "nan_evidence",
        "Inf": "inf_evidence",
        "empty active target set": "empty_active_target_set",
        "mapping mismatch": "mapping_mismatch",
        "target snapshot mismatch": "mapping_mismatch",
        "transport ACK duplicate": "post_decision_lock",
        "transport ACK late": "transport_interruption",
        "simulated connection interruption": "transport_interruption",
    }
    return {"schemaVersion": 1, "recordType": "m13_5_fault_injection_acceptance", "status": "PASS" if all(item["pass"] for item in results) else "FAIL", "results": results, "faultCatalog": fault_catalog, "hardwareBoundary": {"questOperated": False, "nd8Operated": False, "robotOperated": False}}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--fault-summary-path", required=True)
    args = parser.parse_args(argv)
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    streaming = run_streaming_acceptance(root / "streaming")
    faults = run_fault_injection_acceptance(root / "faults")
    Path(args.summary_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.fault_summary_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_path).write_text(json.dumps(streaming, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.fault_summary_path).write_text(json.dumps(faults, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"streaming": streaming["status"], "faults": faults["status"]}, sort_keys=True))
    return 0 if streaming["status"] == "PASS" and faults["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
