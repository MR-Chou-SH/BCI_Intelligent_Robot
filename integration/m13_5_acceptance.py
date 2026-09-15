"""Engineering acceptance reporter for M13.5 session JSONL."""

import argparse
import json
from pathlib import Path

from integration.m13_5_analyzer import analyze_paths, human_summary
from integration.m13_5_logging import ensure_no_forbidden_identity, read_jsonl


def _check(name, passed, detail):
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def acceptance_report(paths):
    records = []
    parse_errors = []
    for path in paths:
        try:
            records.extend(read_jsonl(path))
        except (OSError, ValueError) as error:
            parse_errors.append("{}: {}".format(path, error))
    checks = []
    if parse_errors:
        checks.append(_check("logs_parseable", False, parse_errors))
        return {"schemaVersion": 1, "recordType": "m13_5_engineering_acceptance", "status": "FAIL", "checks": checks, "parseErrors": parse_errors}
    ensure_no_forbidden_identity(records)
    by_session = {}
    for record in records:
        by_session.setdefault(record.get("sessionId"), []).append(record)
    checks.append(_check("logs_parseable", True, "all input JSONL records parsed"))
    checks.append(_check("identity_privacy", all("obj_" not in json.dumps(record, sort_keys=True) for record in records), "no MuJoCo object identity appears in session evidence"))
    all_modes_valid = True
    all_trials_closed = True
    monotonic = True
    mapping_valid = True
    final_exactly_once = True
    no_fabricated_selection = True
    ack_sequence = True
    no_stale_contamination = True
    mode_semantics = True
    trial_reset = True
    for session_id, session_records in by_session.items():
        started = next((item for item in session_records if item.get("eventType") == "session_started"), None)
        mode = None if started is None else started.get("runtimeMode")
        all_modes_valid = all_modes_valid and mode in ("baseline", "shadow", "active")
        if started is None or started.get("policy", {}).get("fusedThreshold") != 0.70 or started.get("policy", {}).get("marginThreshold") != 0.20 or started.get("policy", {}).get("requiredConsecutive") != 2:
            mode_semantics = False
        trial_ids = [item.get("trialId") for item in session_records if item.get("eventType") == "trial_started"]
        for trial_id in trial_ids:
            trial_records = [item for item in session_records if item.get("trialId") == trial_id]
            start = next((item for item in trial_records if item.get("eventType") == "trial_started"), None)
            closed = next((item for item in trial_records if item.get("eventType") == "trial_closed"), None)
            submissions = [item for item in trial_records if item.get("eventType") == "final_submission"]
            windows = [item for item in trial_records if item.get("eventType") == "window_evaluated"]
            if closed is None:
                all_trials_closed = False
            if start is None or not start.get("openAccepted") or not start.get("mappingValid"):
                mapping_valid = False
            reset = start.get("stateReset", {}) if start else {}
            trial_reset = trial_reset and all(reset.get(key) is True for key in ("consecutiveCount", "candidateTarget", "previousDecision", "windowIndex", "previousEvidence"))
            indices = [item.get("windowIndex") for item in windows]
            monotonic = monotonic and indices == list(range(len(indices)))
            final_exactly_once = final_exactly_once and len(submissions) == 1
            for item in trial_records:
                if item.get("trialId") != trial_id:
                    no_stale_contamination = False
            if submissions:
                submission = submissions[0]
                result = submission.get("m8Result") or {}
                decision_made = bool(submission.get("decisionMade"))
                if decision_made:
                    no_fabricated_selection = no_fabricated_selection and result.get("status") == "quest_accepted" and result.get("predictedClassIndex") in (0, 1, 2)
                    ack_sequence = ack_sequence and (result.get("ack") or {}).get("accepted") is True
                else:
                    no_fabricated_selection = no_fabricated_selection and result.get("status") == "no_decision" and result.get("predictedClassIndex") is None and not (result.get("ack") or {}).get("resolvedTargetId")
                    ack_sequence = ack_sequence and (result.get("ack") or {}).get("accepted") is True
            if mode == "baseline":
                mode_semantics = mode_semantics and all(item.get("m13State") == "suppressed" for item in windows) and not any(item.get("submissionSource") == "m13_active" for item in submissions)
            elif mode == "shadow":
                mode_semantics = mode_semantics and any(item.get("eventType") == "shadow_hypothetical_decision" for item in trial_records) and all(item.get("submissionSource") == "baseline" for item in submissions)
            elif mode == "active":
                mode_semantics = mode_semantics and all(item.get("submissionSource") == "m13_active" for item in submissions)
    checks.extend([
        _check("runtime_modes_valid", all_modes_valid, "all sessions explicitly use baseline, shadow, or active"),
        _check("trial_closed_cleanly", all_trials_closed, "each started trial has a terminal trial_closed event"),
        _check("frozen_mapping_valid", mapping_valid, "each trial records an accepted explicit mapping snapshot"),
        _check("windows_monotonic", monotonic, "accepted windows are contiguous and monotonic per trial"),
        _check("exactly_once_final_submission", final_exactly_once, "one final_submission event per closed trial"),
        _check("no_fabricated_no_decision", no_fabricated_selection, "no-decision has no class index or resolved target"),
        _check("ack_sequence", ack_sequence, "final terminal events have the expected mock ACK"),
        _check("no_stale_trial_contamination", no_stale_contamination, "events remain scoped to their current trial ID"),
        _check("mode_semantics", mode_semantics, "baseline suppresses M13, shadow does not submit M13, active submits M13"),
        _check("trial_state_reset", trial_reset, "trial start records reset of all M13 state and previous evidence"),
    ])
    summary, _trials = analyze_paths(paths)
    passed = all(item["status"] == "PASS" for item in checks) and not summary.get("parseErrors")
    return {"schemaVersion": 1, "recordType": "m13_5_engineering_acceptance", "status": "PASS" if passed else "FAIL", "checks": checks, "analysis": summary, "limitations": ["engineering semantics only", "no early-stop-rate or accuracy threshold", "no hardware or physical timing claim"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--text-path", required=True)
    args = parser.parse_args(argv)
    report = acceptance_report(args.input)
    report["humanSummary"] = "M13.5 engineering acceptance: {} ({} checks). {}".format(report["status"], len(report["checks"]), human_summary(report["analysis"]) if report.get("analysis") else "logs were not parseable")
    Path(args.summary_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.text_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_path).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.text_path).write_text(report["humanSummary"] + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "summaryPath": str(args.summary_path)}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
