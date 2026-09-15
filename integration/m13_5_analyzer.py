"""Descriptive analyzer for M13.5 structured session JSONL."""

import argparse
import json
from pathlib import Path
from statistics import mean, median

from integration.m13_5_logging import ensure_no_forbidden_identity, read_jsonl


def _average(values):
    return mean(values) if values else None


def _median(values):
    return median(values) if values else None


def _percent(count, total):
    return count / total if total else 0.0


def analyze_records(records, source_paths=()):
    ensure_no_forbidden_identity(records)
    by_session = {}
    for record in records:
        by_session.setdefault(record.get("sessionId"), []).append(record)
    trials = []
    duplicate_suppression = 0
    invalid_evidence = 0
    stale_rejections = 0
    acceptance_parse_errors = []
    session_modes = {}
    for session_id, session_records in by_session.items():
        mode = next((item.get("runtimeMode") for item in session_records if item.get("eventType") == "session_started"), "unknown")
        session_modes[session_id] = mode
        trial_ids = [item.get("trialId") for item in session_records if item.get("eventType") == "trial_started"]
        for trial_id in trial_ids:
            trial_records = [item for item in session_records if item.get("trialId") == trial_id]
            closed = next((item for item in trial_records if item.get("eventType") == "trial_closed"), None)
            submission_events = [item for item in trial_records if item.get("eventType") == "final_submission"]
            duplicate_suppression += sum(1 for item in trial_records if item.get("eventType") in ("duplicate_suppressed", "duplicate_finalize_suppressed"))
            invalid_evidence += sum(1 for item in trial_records if item.get("eventType") == "window_rejected" and item.get("reasonCode") in ("invalid_evidence", "malformed_evidence"))
            stale_rejections += sum(1 for item in trial_records if item.get("eventType") == "window_rejected" and item.get("reasonCode") in ("stale_trial_or_selection", "out_of_order_or_missing_window", "duplicate_window", "target_snapshot_mismatch"))
            if closed is None:
                acceptance_parse_errors.append("trial {} has no trial_closed event".format(trial_id))
                continue
            m13_made = closed.get("m13DecisionMade")
            m13_reason = closed.get("m13StopReason")
            m13_time = closed.get("m13EffectiveAcquisitionSeconds")
            m13_window = closed.get("m13StopWindow")
            baseline_target = closed.get("baselineFinalLogicalBlockId")
            m13_target = closed.get("m13FinalLogicalBlockId")
            m12_target = closed.get("m12FullWindowLogicalBlockId")
            m13_windows = closed.get("m13EvaluatedWindows")
            baseline_windows = closed.get("baselineEvaluatedWindows", closed.get("evaluatedWindowCount"))
            trials.append({
                "sessionId": session_id,
                "trialId": trial_id,
                "mode": mode,
                "m13DecisionMade": m13_made,
                "m13EarlyStop": bool(closed.get("m13EarlyStop")),
                "m13StopReason": m13_reason,
                "m13StopWindow": m13_window,
                "m13EffectiveAcquisitionSeconds": m13_time,
                "baselineFinalLogicalBlockId": baseline_target,
                "m13FinalLogicalBlockId": m13_target,
                "m12FullWindowLogicalBlockId": m12_target,
                "baselineWindows": baseline_windows,
                "m13Windows": m13_windows,
                "windowsSaved": max(0, baseline_windows - m13_windows) if isinstance(baseline_windows, int) and isinstance(m13_windows, int) else 0,
                "submissionCount": len(submission_events),
                "runtimeFaults": closed.get("runtimeFaults", []),
                "mappingValid": bool(closed.get("mappingValid")),
            })

    valid_trials = [item for item in trials if not item["runtimeFaults"] and item["mappingValid"]]
    invalid_trials = [item for item in trials if item not in valid_trials]
    m13_trials = [item for item in trials if item["mode"] != "baseline"]
    made_trials = [item for item in m13_trials if item["m13DecisionMade"]]
    early_trials = [item for item in m13_trials if item["m13EarlyStop"]]
    fallback_trials = [item for item in m13_trials if item["m13StopReason"] == "full_window_fallback"]
    no_decision_trials = [item for item in m13_trials if item["m13DecisionMade"] is False]
    decision_times = [item["m13EffectiveAcquisitionSeconds"] for item in made_trials if isinstance(item["m13EffectiveAcquisitionSeconds"], (int, float))]
    decision_windows = [item["m13StopWindow"] for item in made_trials if isinstance(item["m13StopWindow"], int)]
    m12_agreement = [item for item in made_trials if item["m12FullWindowLogicalBlockId"] is not None and item["m13FinalLogicalBlockId"] == item["m12FullWindowLogicalBlockId"]]
    baseline_agreement = [item for item in made_trials if item["baselineFinalLogicalBlockId"] is not None and item["m13FinalLogicalBlockId"] == item["baselineFinalLogicalBlockId"]]
    reasons = {}
    for item in m13_trials:
        reason = item["m13StopReason"] or "none"
        reasons[reason] = reasons.get(reason, 0) + 1
    shadow_trials = [item for item in trials if item["mode"] == "shadow"]
    shadow_early = [item for item in shadow_trials if item["m13EarlyStop"]]
    shadow_saved = [item["windowsSaved"] for item in shadow_early]
    summary = {
        "schemaVersion": 1,
        "recordType": "m13_5_session_analysis",
        "sourcePaths": [str(path) for path in source_paths],
        "sessionCount": len(by_session),
        "sessionModes": session_modes,
        "totalTrials": len(trials),
        "validTrials": len(valid_trials),
        "invalidTrials": len(invalid_trials),
        "m13Trials": len(m13_trials),
        "earlyStopCount": len(early_trials),
        "earlyStopRate": _percent(len(early_trials), len(m13_trials)),
        "fullWindowFallbackCount": len(fallback_trials),
        "fullWindowFallbackRate": _percent(len(fallback_trials), len(m13_trials)),
        "noDecisionCount": len(no_decision_trials),
        "meanStopWindow": _average(decision_windows),
        "medianStopWindow": _median(decision_windows),
        "meanEffectiveAcquisitionSeconds": _average(decision_times),
        "medianEffectiveAcquisitionSeconds": _median(decision_times),
        "windowsSaved": sum(item["windowsSaved"] for item in early_trials),
        "meanWindowsSaved": _average([item["windowsSaved"] for item in early_trials]),
        "m13VsM12FullWindowAgreementCount": len(m12_agreement),
        "m13VsM12FullWindowAgreementRate": _percent(len(m12_agreement), len(made_trials)),
        "m13VsBaselineFinalAgreementCount": len(baseline_agreement),
        "m13VsBaselineFinalAgreementRate": _percent(len(baseline_agreement), len(made_trials)),
        "stopReasonDistribution": reasons,
        "duplicateSuppressionCount": duplicate_suppression,
        "invalidEvidenceCount": invalid_evidence,
        "staleOrRejectedWindowCount": stale_rejections,
        "shadowMetrics": {
            "shadowTrialCount": len(shadow_trials),
            "hypotheticalEarlyStopCount": len(shadow_early),
            "hypotheticalDurationSavedSeconds": sum(item["windowsSaved"] * 0.2 for item in shadow_early),
            "hypotheticalWindowsSaved": sum(item["windowsSaved"] for item in shadow_early),
            "shadowTargetVsBaselineAgreementCount": sum(1 for item in shadow_trials if item["m13FinalLogicalBlockId"] == item["baselineFinalLogicalBlockId"]),
            "shadowTargetVsM12AgreementCount": sum(1 for item in shadow_trials if item["m13FinalLogicalBlockId"] == item["m12FullWindowLogicalBlockId"]),
            "shadowDisagreementCases": [item["trialId"] for item in shadow_trials if item["m13FinalLogicalBlockId"] != item["baselineFinalLogicalBlockId"]],
            "reasonsShadowDidNotStop": {reason: count for reason, count in reasons.items() if reason not in ("early_stable_fused_evidence",)},
        },
        "parseErrors": acceptance_parse_errors,
        "limitations": ["descriptive engineering metrics only", "no significance, cognitive-load, generalized-accuracy or latency claim", "ground truth is not required for this analyzer"],
    }
    return summary, trials


def analyze_paths(paths):
    all_records = []
    for path in paths:
        all_records.extend(read_jsonl(path))
    return analyze_records(all_records, paths)


def human_summary(summary):
    return "M13.5 analysis: {} trials across {} session(s); {} early stops, {} full-window fallbacks, {} no-decisions; duplicate suppressions={}; invalid evidence={}; M13/M12 agreement={:.3f}.".format(
        summary["totalTrials"], summary["sessionCount"], summary["earlyStopCount"], summary["fullWindowFallbackCount"], summary["noDecisionCount"], summary["duplicateSuppressionCount"], summary["invalidEvidenceCount"], summary["m13VsM12FullWindowAgreementRate"])


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--summary-path", required=True)
    parser.add_argument("--text-path", required=True)
    args = parser.parse_args(argv)
    summary, _trials = analyze_paths(args.input)
    summary["humanSummary"] = human_summary(summary)
    Path(args.summary_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.text_path).parent.mkdir(parents=True, exist_ok=True)
    Path(args.summary_path).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    Path(args.text_path).write_text(summary["humanSummary"] + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS" if not summary["parseErrors"] else "FAIL", "trials": summary["totalTrials"], "summaryPath": str(args.summary_path)}, sort_keys=True))
    return 0 if not summary["parseErrors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
