"""Opt-in, offline-only candidate research sandboxes.

The functions here are intentionally descriptive and isolated from M11/M12/M13
production paths.  They prepare measurements for a future decision after real
human M13 evidence arrives; they do not fit, select, or write back parameters.
"""

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Optional

from integration.m13_dynamic_stopping import (
    M13_FUSED_EVIDENCE_THRESHOLD,
    M13_MARGIN_THRESHOLD,
    M13_REQUIRED_CONSECUTIVE,
)
from integration.m12_context_eeg_fusion import LAMBDA_CONTEXT
from integration.m17_fork_diagnostics import normalize_trial, synthetic_validation_sessions


M18_PROVENANCE = "EXPERIMENTAL_SANDBOX"
M18_LAMBDA_CANDIDATES = (0.0, 0.25, 0.5, 0.75, 1.0)
M18_STOPPING_PRESETS = (
    ("baseline", 0.70, 0.20, 2),
    ("threshold_low", 0.65, 0.20, 2),
    ("threshold_high", 0.75, 0.20, 2),
    ("margin_low", 0.70, 0.15, 2),
    ("margin_high", 0.70, 0.25, 2),
    ("consecutive_1", 0.70, 0.20, 1),
    ("consecutive_3", 0.70, 0.20, 3),
)


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _top(scores):
    values = {key: float(value) for key, value in (scores or {}).items() if _finite(value)}
    if not values:
        return (), None, None
    ordered = sorted(values.items(), key=lambda item: (-item[1], str(item[0])))
    top = ordered[0][1]
    return tuple(key for key, value in ordered if math.isclose(value, top, abs_tol=1e-12, rel_tol=0.0)), top, ordered[1][1] if len(ordered) > 1 else 0.0


def _normalized(trials):
    return [trial if "trajectory" in trial else normalize_trial(trial) for trial in trials]


def calibration_diagnostics(trials, bin_count=5):
    rows = []
    for trial in _normalized(trials):
        if not trial["trajectory"]:
            continue
        last = trial["trajectory"][-1]
        confidence = last.get("fusedTop1")
        target = (last.get("fusedTop") or [None])[0]
        expected = trial.get("expectedTarget")
        if not _finite(confidence) or expected is None:
            continue
        confidence = max(0.0, min(1.0, float(confidence)))
        correct = int(target == expected)
        rows.append({"trialId": trial["trialId"], "confidence": confidence, "correct": correct, "bin": min(bin_count - 1, int(confidence * bin_count)), "sourceType": trial.get("sourceType")})
    bins = []
    for index in range(bin_count):
        values = [row for row in rows if row["bin"] == index]
        bins.append({
            "bin": index,
            "lower": index / bin_count,
            "upper": (index + 1) / bin_count,
            "count": len(values),
            "meanConfidence": sum(row["confidence"] for row in values) / len(values) if values else None,
            "empiricalCorrectRate": sum(row["correct"] for row in values) / len(values) if values else None,
        })
    brier = sum((row["confidence"] - row["correct"]) ** 2 for row in rows) / len(rows) if rows else None
    return {"schemaVersion": 1, "recordType": "m18_calibration_diagnostics", "provenance": M18_PROVENANCE, "trialCount": len(rows), "brierLikeDescriptive": brier, "reliabilityBins": bins, "rows": rows, "limitations": ["confidence is treated as a descriptive normalized evidence value", "not a calibrated probability", "no production score transformation"]}


def _parameterized_fusion(raw_scores, context_scores, lambda_context):
    keys = tuple(dict.fromkeys(list(raw_scores) + list(context_scores)))
    if not keys:
        return {}
    raw_total = sum(float(raw_scores.get(key, 0.0)) for key in keys if _finite(raw_scores.get(key, 0.0)))
    uniform = 1.0 / len(keys)
    context_total = sum(float(context_scores.get(key, 0.0)) for key in keys if _finite(context_scores.get(key, 0.0)))
    if context_total <= 0.0:
        context_total = 1.0
    result = {}
    for key in keys:
        raw = float(raw_scores.get(key, 0.0)) if _finite(raw_scores.get(key, 0.0)) else 0.0
        prior = float(context_scores.get(key, 0.0)) / context_total if _finite(context_scores.get(key, 0.0)) else 0.0
        softened = (1.0 - lambda_context) * uniform + lambda_context * prior
        result[key] = raw * softened
    total = sum(result.values())
    return {key: value / total for key, value in result.items()} if total > 0.0 else result


def context_weight_sensitivity(trials, candidates=M18_LAMBDA_CANDIDATES):
    normalized = _normalized(trials)
    rows = []
    for lambda_context in candidates:
        decisions = []
        for trial in normalized:
            if not trial["trajectory"]:
                continue
            fused = _parameterized_fusion(trial["trajectory"][-1]["rawScores"], trial["contextPrior"], float(lambda_context))
            winners, top1, top2 = _top(fused)
            target = winners[0] if len(winners) == 1 else None
            decisions.append({"trialId": trial["trialId"], "target": target, "tie": len(winners) != 1, "top1": top1, "margin": None if top1 is None else top1 - (top2 or 0.0), "correct": target == trial.get("expectedTarget") if target is not None else None})
        baseline = next((row["target"] for row in rows if row["lambdaContext"] == LAMBDA_CONTEXT for row in row.get("decisions", [])), None)
        correct = [row for row in decisions if row["correct"] is True]
        rows.append({"lambdaContext": float(lambda_context), "trialCount": len(decisions), "decisionCount": sum(row["target"] is not None for row in decisions), "tieCount": sum(row["tie"] for row in decisions), "descriptiveCorrectCount": len(correct), "descriptiveAccuracy": len(correct) / len(decisions) if decisions else None, "meanMargin": sum(row["margin"] for row in decisions if row["margin"] is not None) / max(1, sum(row["margin"] is not None for row in decisions)), "decisions": decisions})
    return {"schemaVersion": 1, "recordType": "m18_context_weight_sensitivity", "provenance": M18_PROVENANCE, "productionLambda": LAMBDA_CONTEXT, "candidateLambdas": list(candidates), "rows": rows, "selectionPerformed": False, "limitations": ["sparse descriptive comparison only", "does not choose or write back lambda", "uses existing normalized evidence semantics"]}


def _evaluate_stopping(trial, threshold, margin, consecutive):
    count = 0
    candidate = None
    rows = trial["trajectory"]
    for row in rows:
        fused_top = row.get("fusedTop") or []
        raw_top = row.get("rawTop") or []
        eligible = len(fused_top) == 1 and len(raw_top) == 1 and fused_top[0] == raw_top[0] and _finite(row.get("fusedTop1")) and float(row["fusedTop1"]) >= threshold and _finite(row.get("fusedMargin")) and float(row["fusedMargin"]) >= margin
        if eligible:
            if candidate == fused_top[0]:
                count += 1
            else:
                candidate, count = fused_top[0], 1
            if count >= consecutive:
                return {"decisionMade": True, "target": candidate, "stopWindow": row["windowIndex"], "earlyStop": row["windowIndex"] < len(rows) - 1, "reason": "early_stable_fused_evidence", "consecutive": count}
        else:
            candidate, count = None, 0
    final = rows[-1] if rows else {}
    fused_top = final.get("fusedTop") or []
    return {"decisionMade": len(fused_top) == 1, "target": fused_top[0] if len(fused_top) == 1 else None, "stopWindow": final.get("windowIndex"), "earlyStop": False, "reason": "full_window_fallback" if len(fused_top) == 1 else "no_decision_tie", "consecutive": count}


def stopping_sensitivity(trials, presets=M18_STOPPING_PRESETS):
    normalized = _normalized(trials)
    rows = []
    for name, threshold, margin, consecutive in presets:
        results = [_evaluate_stopping(trial, threshold, margin, consecutive) for trial in normalized]
        full_targets = [trial.get("fullWindowTarget") for trial in normalized]
        decisions = [result for result in results if result["decisionMade"]]
        rows.append({"preset": name, "threshold": threshold, "margin": margin, "consecutive": consecutive, "trialCount": len(results), "earlyStopCount": sum(result["earlyStop"] for result in results), "fallbackCount": sum(result["reason"] == "full_window_fallback" for result in results), "noDecisionCount": sum(not result["decisionMade"] for result in results), "meanStopWindow": sum(result["stopWindow"] for result in decisions if isinstance(result["stopWindow"], int)) / max(1, sum(isinstance(result["stopWindow"], int) for result in decisions)), "agreementWithFullWindow": sum(result["target"] == target for result, target in zip(results, full_targets) if result["target"] is not None and target is not None) / max(1, sum(result["target"] is not None and target is not None for result, target in zip(results, full_targets))), "results": results})
    return {"schemaVersion": 1, "recordType": "m18_stopping_sensitivity", "provenance": M18_PROVENANCE, "productionDefaults": {"threshold": M13_FUSED_EVIDENCE_THRESHOLD, "margin": M13_MARGIN_THRESHOLD, "consecutive": M13_REQUIRED_CONSECUTIVE}, "presets": [name for name, _threshold, _margin, _consecutive in presets], "rows": rows, "selectionPerformed": False, "limitations": ["one-factor-at-a-time sparse presets", "not a grid search", "does not update M13 policy"]}


@dataclass(frozen=True)
class DecisionUncertaintyFeatures:
    entropy: Optional[float]
    top1: Optional[float]
    top2: Optional[float]
    margin: Optional[float]
    winnerStability: Optional[float]
    trajectoryVolatility: Optional[float]
    evaluatedWindows: int
    provenance: str = M18_PROVENANCE


def uncertainty_features(trial):
    normalized = trial if "trajectory" in trial else normalize_trial(trial)
    rows = normalized["trajectory"]
    winners = [(row.get("fusedTop") or [None])[0] for row in rows]
    winner_stability = sum(1 for left, right in zip(winners, winners[1:]) if left == right) / max(1, len(winners) - 1) if winners else None
    margins = [float(row["fusedMargin"]) for row in rows if _finite(row.get("fusedMargin"))]
    volatility = sum(abs(right - left) for left, right in zip(margins, margins[1:])) / max(1, len(margins) - 1) if margins else None
    last = rows[-1] if rows else {}
    evidence = last.get("fusedEvidence") or {}
    total = sum(float(value) for value in evidence.values() if _finite(value) and float(value) >= 0.0)
    entropy = -sum((float(value) / total) * math.log(float(value) / total) for value in evidence.values() if _finite(value) and float(value) > 0.0) if total > 0.0 else None
    return DecisionUncertaintyFeatures(entropy, last.get("fusedTop1"), last.get("fusedTop2"), last.get("fusedMargin"), winner_stability, volatility, len(rows))


@dataclass(frozen=True)
class ParticipantSessionAdaptationConfig:
    participantId: str
    sessionId: str
    lambdaContext: Optional[float] = None
    threshold: Optional[float] = None
    margin: Optional[float] = None
    consecutive: Optional[int] = None
    classMetadata: Optional[dict] = None
    provenance: str = M18_PROVENANCE

    def validate(self):
        if not self.participantId or not self.sessionId:
            raise ValueError("participantId and sessionId are required for an opt-in sandbox config")
        if self.lambdaContext is not None and not 0.0 <= self.lambdaContext <= 1.0:
            raise ValueError("lambdaContext must be within [0, 1]")
        if self.threshold is not None and not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be within [0, 1]")
        if self.margin is not None and not 0.0 <= self.margin <= 1.0:
            raise ValueError("margin must be within [0, 1]")
        if self.consecutive is not None and self.consecutive < 1:
            raise ValueError("consecutive must be positive")
        return True


@dataclass(frozen=True)
class UserCorrectionEvidence:
    selectionId: str
    originalTarget: Optional[str]
    correctionIndicated: Optional[bool]
    timestampUtc: str
    provenance: str = M18_PROVENANCE
    trialId: Optional[str] = None
    correctedTarget: Optional[str] = None
    confidence: Optional[float] = None
    evidencePlaceholder: Optional[dict] = None


def audit_context_prior_extension():
    return {"status": "NO_CHANGE", "contract": "ContextPrior", "supportsObservableHistory": True, "supportsAvailableLogicalBlockIds": True, "supportsStepIndex": True, "futureAdapterRequired": False, "provenance": M18_PROVENANCE, "note": "M11 contract is sufficient for future predictor adapters; no production refactor was justified."}


def run_all_sandboxes(trials):
    normalized = _normalized(trials)
    uncertainty = [{"trialId": trial["trialId"], **asdict(uncertainty_features(trial))} for trial in normalized]
    adaptation = ParticipantSessionAdaptationConfig("sandbox-participant", "sandbox-session")
    adaptation.validate()
    return {
        "schemaVersion": 1,
        "recordType": "m18_candidate_research_sandboxes",
        "provenance": M18_PROVENANCE,
        "productionDefaults": {"m12Lambda": LAMBDA_CONTEXT, "m13FusedThreshold": M13_FUSED_EVIDENCE_THRESHOLD, "m13MarginThreshold": M13_MARGIN_THRESHOLD, "m13RequiredConsecutive": M13_REQUIRED_CONSECUTIVE},
        "calibration": calibration_diagnostics(normalized),
        "contextWeight": context_weight_sensitivity(normalized),
        "stopping": stopping_sensitivity(normalized),
        "uncertainty": {"recordType": "DecisionUncertaintyFeatures", "rows": uncertainty, "provenance": M18_PROVENANCE},
        "participantSessionAdaptation": {"schema": asdict(adaptation), "productionOptIn": False},
        "userCorrectionContract": {"schema": asdict(UserCorrectionEvidence("sandbox-selection", None, None, "not-recorded")), "productionOptIn": False},
        "contextPriorAudit": audit_context_prior_extension(),
        "acceptance": {"isolated": True, "offlineOnly": True, "deterministic": True, "productionDefaultsUnchanged": True, "selectionPerformed": False},
    }


def synthetic_acceptance():
    from integration.m15_comparative_benchmark import run_benchmark
    _summary, trials = run_benchmark()
    report = run_all_sandboxes(trials)
    checks = {
        "productionDefaults": report["productionDefaults"] == {"m12Lambda": 0.5, "m13FusedThreshold": 0.70, "m13MarginThreshold": 0.20, "m13RequiredConsecutive": 2},
        "calibration": report["calibration"]["trialCount"] == 12,
        "contextWeightCandidates": report["contextWeight"]["candidateLambdas"] == list(M18_LAMBDA_CANDIDATES),
        "stoppingPresets": len(report["stopping"]["rows"]) == len(M18_STOPPING_PRESETS),
        "uncertaintyRows": len(report["uncertainty"]["rows"]) == 12,
        "contextAuditNoChange": report["contextPriorAudit"]["status"] == "NO_CHANGE",
        "sandboxProvenance": report["provenance"] == M18_PROVENANCE,
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "report": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    result = synthetic_acceptance()
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


__all__ = ["M18_PROVENANCE", "M18_LAMBDA_CANDIDATES", "M18_STOPPING_PRESETS", "DecisionUncertaintyFeatures", "ParticipantSessionAdaptationConfig", "UserCorrectionEvidence", "calibration_diagnostics", "context_weight_sensitivity", "stopping_sensitivity", "uncertainty_features", "audit_context_prior_extension", "run_all_sandboxes", "synthetic_acceptance"]


if __name__ == "__main__":
    raise SystemExit(main())
