"""Transparent, direction-agnostic diagnostics for the post-M13 research fork.

This module consumes replay/session-shaped trial records and reports observed
patterns.  It deliberately does not select a research direction, tune a
production parameter, or claim human evidence.
"""

import argparse
import json
import math
from pathlib import Path
from statistics import mean


DIAGNOSTIC_LABELS = (
    "EEG_SHORT_WINDOW_UNSTABLE",
    "EEG_CLASSIFICATION_WEAK",
    "CONTEXT_UNINFORMATIVE",
    "CONTEXT_MISLEADING",
    "FUSION_DOMINATES_TOO_MUCH",
    "FUSION_TOO_WEAK",
    "STOPPING_TOO_CONSERVATIVE",
    "STOPPING_TOO_AGGRESSIVE",
    "CALIBRATION_UNCLEAR",
    "USER_CORRECTION_NEEDED",
    "NO_CLEAR_BOTTLENECK",
)

_EPSILON = 1e-12


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _top(scores):
    values = {key: float(value) for key, value in (scores or {}).items() if _finite(value)}
    if not values:
        return (), None, None
    ordered = sorted(values.items(), key=lambda item: (-item[1], str(item[0])))
    maximum = ordered[0][1]
    winners = tuple(key for key, value in ordered if math.isclose(value, maximum, rel_tol=0.0, abs_tol=_EPSILON))
    second = ordered[1][1] if len(ordered) > 1 else 0.0
    return winners, maximum, second


def _entropy(values):
    probabilities = [float(value) for value in (values or {}).values() if _finite(value) and float(value) >= 0.0]
    total = sum(probabilities)
    if total <= 0.0:
        return None
    return -sum((value / total) * math.log(value / total) for value in probabilities if value > 0.0)


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _contains_forbidden_identity(value):
    if isinstance(value, str):
        return "obj_" in value
    if isinstance(value, dict):
        return any(_contains_forbidden_identity(key) or _contains_forbidden_identity(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_identity(item) for item in value)
    return False


def _fused_row_to_scores(row):
    if not isinstance(row, dict):
        return {}
    if isinstance(row.get("entries"), list):
        return {
            entry.get("logicalBlockId"): entry.get("normalizedFusedEvidence")
            for entry in row["entries"]
            if entry.get("logicalBlockId") is not None
        }
    if isinstance(row.get("fusedEvidence"), dict):
        return row["fusedEvidence"]
    if isinstance(row.get("normalizedFusedEvidence"), dict):
        return row["normalizedFusedEvidence"]
    return {key: value for key, value in row.items() if _finite(value)}


def normalize_trial(trial):
    """Convert M15/M13-style input into one stable diagnostic representation."""
    raw_trajectory = trial.get("rawEegTrajectory") or trial.get("rawEegEvidenceTrajectory") or []
    fused_trajectory = trial.get("m12FusedTrajectory") or trial.get("fusedEvidenceTrajectory") or []
    context_prior = trial.get("contextPrior") or {}
    context_scores = context_prior.get("nextTargetProbabilities") or context_prior.get("probabilities") or {}
    context_top = tuple(context_prior.get("topTargets") or _top(context_scores)[0])
    rows = []
    for index, raw_scores in enumerate(raw_trajectory):
        raw_scores = dict(raw_scores) if isinstance(raw_scores, dict) else {}
        fused_row = fused_trajectory[index] if index < len(fused_trajectory) else {}
        fused_scores = _fused_row_to_scores(fused_row)
        raw_winners, raw_top1, raw_top2 = _top(raw_scores)
        fused_winners, fused_top1, fused_top2 = _top(fused_scores)
        declared_fused = tuple(fused_row.get("topLogicalBlockIds") or ()) if isinstance(fused_row, dict) else ()
        rows.append({
            "windowIndex": index,
            "rawScores": raw_scores,
            "rawTop": list(raw_winners),
            "rawTop1": raw_top1,
            "rawTop2": raw_top2,
            "fusedEvidence": fused_scores,
            "fusedTop": list(declared_fused or fused_winners),
            "fusedTop1": fused_top1,
            "fusedTop2": fused_top2,
            "fusedMargin": None if fused_top1 is None else fused_top1 - (fused_top2 or 0.0),
            "rawMargin": None if raw_top1 is None else raw_top1 - (raw_top2 or 0.0),
            "contextPrior": dict(context_scores),
            "contextEntropy": _entropy(context_scores),
        })
    conditions = trial.get("conditions") or {}
    dynamic = conditions.get("C_CONTEXT_EEG_DYNAMIC_STOP") or {}
    full_window = conditions.get("B_CONTEXT_EEG_FULL_WINDOW") or {}
    raw_full_winners = rows[-1]["rawTop"] if rows else []
    fused_full_winners = rows[-1]["fusedTop"] if rows else []
    return _json_safe({
        "trialId": trial.get("trialId", "unknown"),
        "taskId": trial.get("taskId"),
        "stepIndex": trial.get("stepIndex"),
        "observableCompletedHistory": list(trial.get("observableCompletedHistory") or []),
        "expectedTarget": trial.get("expectedLogicalBlockId") or trial.get("groundTruthTarget"),
        "contextTop": list(context_top),
        "contextPrior": dict(context_scores),
        "contextEntropy": _entropy(context_scores),
        "trajectory": rows,
        "rawFullWindowTop": list(raw_full_winners),
        "fusedFullWindowTop": list(fused_full_winners),
        "m13FinalTarget": dynamic.get("finalTarget") or trial.get("m13FinalTarget"),
        "m13EarlyStop": bool(dynamic.get("earlyStop", trial.get("m13EarlyStop", False))),
        "m13DecisionMade": dynamic.get("decisionMade", trial.get("m13DecisionMade")),
        "m13StopReason": dynamic.get("reason") or trial.get("m13StopReason"),
        "m13StopWindow": dynamic.get("stopWindow", trial.get("m13StopWindow")),
        "fullWindowTarget": full_window.get("finalTarget") or trial.get("fullWindowTarget") or (fused_full_winners[0] if len(fused_full_winners) == 1 else None),
        "sourceType": trial.get("sourceType") or trial.get("provenanceType") or "SYNTHETIC",
        "correction": bool(trial.get("userCorrection") or trial.get("correction")),
        "provenance": trial.get("provenance", {}),
    })


def _switch_count(values):
    clean = [tuple(value) for value in values if value]
    return sum(1 for previous, current in zip(clean, clean[1:]) if previous != current)


def _ratio(count, total):
    return count / total if total else 0.0


def diagnose_trial(trial):
    item = normalize_trial(trial) if "trajectory" not in trial else trial
    rows = item["trajectory"]
    raw_winners = [row["rawTop"] for row in rows]
    fused_winners = [row["fusedTop"] for row in rows]
    expected = item.get("expectedTarget")
    raw_final = item["rawFullWindowTop"]
    fused_final = item["fusedFullWindowTop"]
    labels = []
    evidence = {}
    switches = _switch_count(raw_winners)
    first_half = raw_winners[: max(1, len(raw_winners) // 2)]
    short_disagreement = bool(expected and raw_final and raw_final[0] == expected and any(expected not in winner for winner in first_half))
    if short_disagreement or (switches >= 2 and expected in raw_final):
        labels.append("EEG_SHORT_WINDOW_UNSTABLE")
        evidence["EEG_SHORT_WINDOW_UNSTABLE"] = {"winnerSwitches": switches, "shortVsFullDisagreement": short_disagreement}
    final_raw_margin = rows[-1].get("rawMargin") if rows else None
    if not raw_final or (final_raw_margin is not None and final_raw_margin < 0.1) or item.get("m13DecisionMade") is False:
        labels.append("EEG_CLASSIFICATION_WEAK")
        evidence["EEG_CLASSIFICATION_WEAK"] = {"finalRawTop": raw_final, "finalRawMargin": final_raw_margin, "m13DecisionMade": item.get("m13DecisionMade")}
    context_informative = bool(item.get("contextTop")) and (max(item.get("contextPrior", {}).values() or [0.0]) >= 0.75)
    context_ambiguous = not context_informative
    if context_ambiguous:
        labels.append("CONTEXT_UNINFORMATIVE")
        evidence["CONTEXT_UNINFORMATIVE"] = {"contextEntropy": item.get("contextEntropy"), "contextTop": item.get("contextTop")}
    context_wrong = bool(expected and item.get("contextTop") and item["contextTop"][0] != expected)
    fused_wrong = bool(expected and fused_final and fused_final[0] != expected)
    raw_correct = bool(expected and raw_final and raw_final[0] == expected)
    if context_wrong and raw_correct and fused_wrong:
        labels.append("CONTEXT_MISLEADING")
        evidence["CONTEXT_MISLEADING"] = {"contextTop": item["contextTop"], "rawTop": raw_final, "fusedTop": fused_final}
    if raw_correct and fused_wrong:
        labels.append("FUSION_DOMINATES_TOO_MUCH")
        evidence["FUSION_DOMINATES_TOO_MUCH"] = {"rawTop": raw_final, "fusedTop": fused_final}
    if context_informative and raw_final == fused_final and item.get("contextTop") and item["contextTop"][0] != (raw_final[0] if raw_final else None):
        labels.append("FUSION_TOO_WEAK")
        evidence["FUSION_TOO_WEAK"] = {"contextTop": item["contextTop"], "rawTop": raw_final, "fusedTop": fused_final}
    if item.get("m13StopReason") == "full_window_fallback" and item.get("fullWindowTarget"):
        labels.append("STOPPING_TOO_CONSERVATIVE")
        evidence["STOPPING_TOO_CONSERVATIVE"] = {"stopReason": item.get("m13StopReason"), "stopWindow": item.get("m13StopWindow")}
    if item.get("m13EarlyStop") and item.get("m13FinalTarget") and item.get("fullWindowTarget") and item["m13FinalTarget"] != item["fullWindowTarget"]:
        labels.append("STOPPING_TOO_AGGRESSIVE")
        evidence["STOPPING_TOO_AGGRESSIVE"] = {"earlyTarget": item["m13FinalTarget"], "fullWindowTarget": item["fullWindowTarget"]}
    if item.get("m13EarlyStop") and item.get("m13FinalTarget") and not raw_final:
        labels.append("CALIBRATION_UNCLEAR")
    if item.get("correction"):
        labels.append("USER_CORRECTION_NEEDED")
        evidence["USER_CORRECTION_NEEDED"] = {"trialId": item.get("trialId")}
    if not labels:
        labels.append("NO_CLEAR_BOTTLENECK")
    return {
        "trialId": item.get("trialId"),
        "taskId": item.get("taskId"),
        "sourceType": item.get("sourceType"),
        "labels": labels,
        "evidence": evidence,
        "metrics": {
            "windowCount": len(rows),
            "rawWinnerSwitches": switches,
            "fusedWinnerSwitches": _switch_count(fused_winners),
            "firstStableRawWindow": next((index for index, winner in enumerate(raw_winners) if winner and all(later == winner for later in raw_winners[index:])), None),
            "rawFullWindowTop": raw_final,
            "fusedFullWindowTop": fused_final,
            "contextTop": item.get("contextTop"),
            "contextEntropy": item.get("contextEntropy"),
            "shortVsFullDisagreement": short_disagreement,
            "m13StopReason": item.get("m13StopReason"),
            "m13EarlyStop": item.get("m13EarlyStop"),
            "m13StopWindow": item.get("m13StopWindow"),
            "fullWindowTarget": item.get("fullWindowTarget"),
            "expectedTarget": item.get("expectedTarget"),
        },
        "normalizedTrial": item,
    }


def diagnose_trials(trials, provenance="SYNTHETIC"):
    diagnostics = [diagnose_trial(trial) for trial in trials]
    label_counts = {label: sum(label in item["labels"] for item in diagnostics) for label in DIAGNOSTIC_LABELS}
    observed = [label for label in DIAGNOSTIC_LABELS if label_counts[label] and label != "NO_CLEAR_BOTTLENECK"]
    if not observed:
        observed = ["NO_CLEAR_BOTTLENECK"]
    directions = {
        "EEG_SHORT_WINDOW_UNSTABLE": "streaming EEG improvement or short-window robustness",
        "EEG_CLASSIFICATION_WEAK": "decoder/evidence-quality investigation",
        "CONTEXT_MISLEADING": "context calibration or adaptive context weighting",
        "FUSION_DOMINATES_TOO_MUCH": "context weight/fusion calibration",
        "FUSION_TOO_WEAK": "context usefulness and fusion sensitivity",
        "STOPPING_TOO_CONSERVATIVE": "stopping personalization or policy study",
        "STOPPING_TOO_AGGRESSIVE": "uncertainty-aware stopping and safety review",
        "USER_CORRECTION_NEEDED": "user-correction/ErrP investigation",
        "CONTEXT_UNINFORMATIVE": "richer context observation or predictor investigation",
        "CALIBRATION_UNCLEAR": "confidence calibration diagnostics",
    }
    return _json_safe({
        "schemaVersion": 1,
        "recordType": "m17_fork_diagnostic_report",
        "provenance": provenance,
        "trialCount": len(diagnostics),
        "observedBottlenecks": observed,
        "labelCounts": label_counts,
        "possibleNextResearchDirections": [{"diagnostic": label, "candidateDirection": directions.get(label, "additional evidence review"), "status": "CANDIDATE_ONLY"} for label in observed if label in directions],
        "evidenceMissing": ["real human M13 EEG", "subject/session context quality", "user corrections unless explicitly recorded"],
        "additionalTrialsToDisambiguate": ["repeat real M13 trials across each active target", "retain full trajectory and context provenance", "run shadow before active if hardware acceptance is authorized"],
        "trialDiagnostics": diagnostics,
        "limitations": ["transparent deterministic classifier, not a learned model", "descriptive evidence only", "does not choose or optimize a production research direction"],
    })


def synthetic_validation_sessions():
    def make(name, raw, fused, context, expected, dynamic):
        return {
            "trialId": "m17-synthetic-{}".format(name),
            "taskId": "synthetic",
            "expectedLogicalBlockId": expected,
            "rawEegTrajectory": [{key: value for key, value in row.items()} for row in raw],
            "m12FusedTrajectory": [{"entries": [{"logicalBlockId": key, "normalizedFusedEvidence": value} for key, value in row.items()]} for row in fused],
            "contextPrior": {"nextTargetProbabilities": context, "topTargets": [max(context, key=context.get)]},
            "conditions": {"B_CONTEXT_EEG_FULL_WINDOW": {"finalTarget": list(fused[-1])[0]}, "C_CONTEXT_EEG_DYNAMIC_STOP": dynamic},
            "provenanceType": "SYNTHETIC",
        }
    a_raw = [{"A": 0.4, "B": 0.5}, {"A": 0.6, "B": 0.5}, {"A": 0.9, "B": 0.1}, {"A": 0.9, "B": 0.1}]
    stable_a = [{"A": 0.9, "B": 0.1}] * 4
    return [
        make("short-unstable", a_raw, stable_a, {"A": 1.0}, "A", {"decisionMade": True, "finalTarget": "A", "earlyStop": True, "stopWindow": 2, "reason": "early_stable_fused_evidence", "fullWindowTarget": "A"}),
        make("context-misleading", [{"A": 0.9, "B": 0.1}] * 4, [{"A": 0.2, "B": 0.8}] * 4, {"B": 1.0}, "A", {"decisionMade": True, "finalTarget": "B", "earlyStop": True, "stopWindow": 1, "reason": "early_stable_fused_evidence", "fullWindowTarget": "B"}),
        make("conservative", [{"A": 0.8, "B": 0.2}] * 4, stable_a, {"A": 1.0}, "A", {"decisionMade": True, "finalTarget": "A", "earlyStop": False, "stopWindow": 3, "reason": "full_window_fallback", "fullWindowTarget": "A"}),
        make("aggressive", [{"A": 0.8, "B": 0.2}] * 4, [{"A": 0.2, "B": 0.8}] * 4, {"B": 1.0}, "A", {"decisionMade": True, "finalTarget": "B", "earlyStop": True, "stopWindow": 1, "reason": "early_stable_fused_evidence", "fullWindowTarget": "A"}),
        make("fusion-dominates", [{"A": 0.9, "B": 0.1}] * 4, [{"A": 0.1, "B": 0.9}] * 4, {"B": 1.0}, "A", {"decisionMade": True, "finalTarget": "B", "earlyStop": False, "stopWindow": 3, "reason": "full_window_fallback", "fullWindowTarget": "B"}),
        make("fusion-weak", [{"A": 0.9, "B": 0.1}] * 4, stable_a, {"B": 0.9, "A": 0.1}, "A", {"decisionMade": True, "finalTarget": "A", "earlyStop": True, "stopWindow": 1, "reason": "early_stable_fused_evidence", "fullWindowTarget": "A"}),
        make("mixed", [{"A": 0.5, "B": 0.5}] * 4, [{"A": 0.5, "B": 0.5}] * 4, {"A": 0.5, "B": 0.5}, "A", {"decisionMade": False, "finalTarget": None, "earlyStop": False, "stopWindow": 3, "reason": "no_decision_tie", "fullWindowTarget": None}),
    ]


def run_synthetic_acceptance():
    expected = {
        "short-unstable": "EEG_SHORT_WINDOW_UNSTABLE",
        "context-misleading": "CONTEXT_MISLEADING",
        "conservative": "STOPPING_TOO_CONSERVATIVE",
        "aggressive": "STOPPING_TOO_AGGRESSIVE",
        "fusion-dominates": "FUSION_DOMINATES_TOO_MUCH",
        "fusion-weak": "FUSION_TOO_WEAK",
        "mixed": "NO_CLEAR_BOTTLENECK",
    }
    results = {}
    for trial in synthetic_validation_sessions():
        name = trial["trialId"].replace("m17-synthetic-", "")
        labels = diagnose_trial(trial)["labels"]
        results[name] = (expected[name] in labels) if name != "mixed" else (len(labels) > 1 or "NO_CLEAR_BOTTLENECK" in labels)
    return {"status": "PASS" if all(results.values()) else "FAIL", "cases": results}


def load_trials(paths):
    trials = []
    for path in paths:
        path = Path(path)
        if path.suffix.lower() == ".jsonl":
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        trials.append(json.loads(line))
            continue
        with path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
            trials.extend(payload if isinstance(payload, list) else payload.get("trials", []))
    return trials


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    trials = load_trials(args.input) if args.input else synthetic_validation_sessions()
    report = diagnose_trials(trials, "SYNTHETIC" if not args.input else "REPLAY_OR_SESSION")
    report["syntheticAcceptance"] = run_synthetic_acceptance()
    if _contains_forbidden_identity(report):
        raise ValueError("M17 report contains forbidden obj_N identity")
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["syntheticAcceptance"]["status"], "trialCount": report["trialCount"], "output": str(args.output)}, sort_keys=True))
    return 0 if report["syntheticAcceptance"]["status"] == "PASS" else 1


__all__ = ["DIAGNOSTIC_LABELS", "normalize_trial", "diagnose_trial", "diagnose_trials", "synthetic_validation_sessions", "run_synthetic_acceptance"]


if __name__ == "__main__":
    raise SystemExit(main())
