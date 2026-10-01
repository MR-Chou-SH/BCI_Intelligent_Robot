from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import research_analysis.m23_high_information_context as m23


RUN_ID = "m25-context-evidence-latency-20261002"
OP_NAMES = ("FAST", "MEDIUM", "CONSERVATIVE")
SESSIONS = ("A", "B1", "B2")
STRENGTHS = (1.0 / 3.0, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99, 1.00)
SURFACE_STRENGTHS = (0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99, 1.00)
PRECISIONS = (1.00, 0.95, 0.90, 0.85, 0.80)
PRIMARY_COVERAGE = 1.00
FIXED_TIMES = (0.50, 0.70, 0.90, 1.10, 1.30, 1.50)
AVAILABILITY_TIMES = (0.0, 0.2, 0.4, 0.6, 0.8)
TOP_GRID = (0.40, 0.45, 0.50, 0.55)
MARGIN_GRID = (0.01, 0.05, 0.10, 0.15, 0.20)
MIN_EVIDENCE_GRID = (0.50, 0.70, 0.90)
STABILITY_PRIMARY = 0.40
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 251002
FROZEN_BRANCH = "FROZEN_GATE_SHARED_THRESHOLD"
DECOUPLED_BRANCH = "NONDEPLOYABLE_DECOUPLED_STRENGTH_GATE"
BRANCHES = (FROZEN_BRANCH, DECOUPLED_BRANCH)
M21_DIR = ROOT / "research_analysis/m21_context_algorithm_search_20260928/attempt-01"
M23_DIR = ROOT / "research_analysis/m23_high_information_context_20260929/attempt-01"
M24_DIR = ROOT / "research_analysis/m24_context_strength_response_20260929/attempt-01"
MANIFEST_PATH = OUT / "INPUT_MANIFEST.json"
CLASSES = tuple(m23.m21.CLASSES)
STRENGTH_LABELS = {s: ("0.333333" if abs(s - 1.0 / 3.0) < 1e-10 else f"{s:.2f}") for s in STRENGTHS}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path.name}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes")


def strength_label(value: float) -> str:
    return STRENGTH_LABELS.get(value, f"{value:.6f}")


def time_key(value: float) -> str:
    return f"{value:.2f}"


def normalized(point: dict[str, Any]) -> list[float]:
    return [float(x) for x in m23.normalized_scores(point)]


def raw_top_margin(point: dict[str, Any]) -> tuple[int, int, float, list[float]]:
    p = normalized(point)
    order = sorted(range(3), key=lambda i: (-p[i], i))
    return order[0], order[1], p[order[0]] - p[order[1]], p


def prior_vector(top: int, strength: float) -> list[float]:
    return [float(x) for x in m23.symmetric_prior(top, strength)]


def fuse(eeg: list[float], prior: list[float]) -> list[float]:
    raw = [max(0.0, float(eeg[i])) * max(0.0, float(prior[i])) for i in range(3)]
    total = sum(raw)
    if total <= 0:
        return list(eeg)
    return [x / total for x in raw]


def vector_metrics(vector: list[float], true_index: int) -> dict[str, float]:
    order = sorted(range(3), key=lambda i: (-vector[i], i))
    top, second = order[:2]
    entropy = -sum(x * math.log(max(x, 1e-15)) for x in vector if x > 0) / math.log(3.0)
    return {
        "topIndex": top,
        "topEvidence": float(vector[top]),
        "secondIndex": second,
        "secondEvidence": float(vector[second]),
        "margin": float(vector[top] - vector[second]),
        "normalizedEntropy": float(entropy),
        "trueClassEvidence": float(vector[true_index]),
    }


def metric_vector_change(vector: list[float], base: list[float]) -> float:
    return float(sum(abs(float(a) - float(b)) for a, b in zip(vector, base)))


def onehot_fraction(vector: list[float], eeg: list[float], top: int) -> float | None:
    onehot = [1.0 if i == top else 0.0 for i in range(3)]
    denom = metric_vector_change(onehot, eeg)
    if denom <= 1e-12:
        return None
    return metric_vector_change(vector, eeg) / denom


def exact_point(trial: dict[str, Any], grid: str, at: float) -> dict[str, Any]:
    points = trial["pointsByGrid"][grid]
    result = min(points, key=lambda p: abs(float(p["effectiveEvidenceTimeSeconds"]) - at))
    if not math.isclose(float(result["effectiveEvidenceTimeSeconds"]), at, abs_tol=1e-8):
        raise AssertionError(f"grid {grid} has no point at {at}")
    return result


def fold_map(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["heldOutSession"]: item for item in params["outerFolds"]}


def op_config(fold: dict[str, Any], name: str) -> dict[str, Any]:
    return next(x for x in fold["operatingPoints"] if x["operatingPoint"] == name)


def make_baseline(point: dict[str, Any], top: int, second: int, correct: bool, stop_time: float,
                  stop_index: int, early: bool, reason: str, top_threshold: float,
                  margin_threshold: float) -> dict[str, Any]:
    return {
        "selectedClassIndex": int(top),
        "selectedClass": CLASSES[int(top)],
        "rawTopIndexAtStop": int(top),
        "rawTopClassAtStop": CLASSES[int(top)],
        "stopTimeSeconds": float(stop_time),
        "onsetRelativeDecisionTimeSeconds": float(point["onsetRelativeDecisionTimeSeconds"]),
        "earlyStop": bool(early),
        "stopReason": reason,
        "correct": bool(correct),
        "stopStepIndex": int(stop_index),
        "rawMarginAtStop": float(point["rawAbsoluteMargin"]),
        "probabilityTopAtStop": float(normalized(point)[top]),
        "topThreshold": float(top_threshold),
        "marginThreshold": float(margin_threshold),
    }


def raw_stop(trial: dict[str, Any], grid: str, top_threshold: float,
             margin_threshold: float, minimum_evidence: float = 0.50,
             stability: float = STABILITY_PRIMARY) -> dict[str, Any]:
    points = trial["pointsByGrid"][grid]
    candidate: int | None = None
    stable_start: float | None = None
    for point in points:
        t = float(point["effectiveEvidenceTimeSeconds"])
        top, _, margin, eeg = raw_top_margin(point)
        eligible = (
            t >= minimum_evidence - 1e-9
            and eeg[top] >= top_threshold - 1e-12
            and margin >= margin_threshold - 1e-12
        )
        if eligible:
            if candidate != top:
                candidate = top
                stable_start = t
            if stable_start is not None and t - stable_start >= stability - 1e-8:
                return make_baseline(point, top, 0, top == int(trial["trueClassIndex"]), t,
                                     int(point["stepIndex"]), True,
                                     "adaptive_probability_and_stability", top_threshold, margin_threshold)
        else:
            candidate = None
            stable_start = None
    point = points[-1]
    top, second, _, _ = raw_top_margin(point)
    return make_baseline(point, top, second, top == int(trial["trueClassIndex"]),
                         float(point["effectiveEvidenceTimeSeconds"]), int(point["stepIndex"]),
                         False, "raw_eeg_full_window_fallback", top_threshold, margin_threshold)


def simulate_context(
    trial: dict[str, Any],
    grid: str,
    baseline: dict[str, Any],
    strength: float,
    context_top: int | None,
    assignment_active: bool,
    branch: str,
    top_threshold: float,
    margin_threshold: float,
    minimum_evidence: float,
    stability: float = STABILITY_PRIMARY,
    available_from: float = 0.0,
) -> dict[str, Any]:
    baseline_stop = float(baseline["stopTimeSeconds"])
    if not assignment_active or context_top is None:
        return {
            "stop": baseline_stop, "selectedIndex": int(baseline["selectedClassIndex"]),
            "contextApplied": False, "authorizedPointCount": 0,
            "authorizedPointRate": 0.0, "wrongEarlyStop": False,
            "contextCausedError": False, "stableStart": None,
            "selectedStepIndex": int(baseline["stopStepIndex"]),
            "fallbackReason": "assignment_unavailable",
        }
    prior = prior_vector(context_top, strength)
    prior_top = max(range(3), key=lambda i: (prior[i], -i))
    prior_mass = prior[prior_top]
    candidate: int | None = None
    stable_start: float | None = None
    authorized_count = 0
    examined = 0
    for point in trial["pointsByGrid"][grid]:
        t = float(point["effectiveEvidenceTimeSeconds"])
        if t > baseline_stop + 1e-9:
            break
        examined += 1
        raw_top, _, _, eeg = raw_top_margin(point)
        is_available = t >= available_from - 1e-9
        agrees = int(context_top) == raw_top
        if branch == FROZEN_BRANCH:
            authorized = is_available and agrees and prior_mass >= 0.70 - 1e-12
        elif branch == DECOUPLED_BRANCH:
            authorized = is_available and agrees
        else:
            raise ValueError(f"unknown context branch {branch}")
        if not authorized:
            candidate = None
            stable_start = None
            continue
        authorized_count += 1
        fused = fuse(eeg, prior)
        order = sorted(range(3), key=lambda i: (-fused[i], i))
        fused_top, fused_second = order[:2]
        eligible = (
            t >= minimum_evidence - 1e-9
            and fused_top == raw_top
            and fused[raw_top] >= top_threshold - 1e-12
            and fused[raw_top] - fused[fused_second] >= margin_threshold - 1e-12
        )
        if eligible:
            if candidate != raw_top:
                candidate = raw_top
                stable_start = t
            if stable_start is not None and t - stable_start >= stability - 1e-8:
                if t < baseline_stop - 1e-9:
                    selected = int(raw_top)
                    wrong = selected != int(trial["trueClassIndex"])
                    return {
                        "stop": t, "selectedIndex": selected, "contextApplied": True,
                        "authorizedPointCount": authorized_count,
                        "authorizedPointRate": authorized_count / max(examined, 1),
                        "wrongEarlyStop": bool(wrong),
                        "contextCausedError": bool(wrong and baseline["correct"]),
                        "stableStart": stable_start, "selectedStepIndex": int(point["stepIndex"]),
                        "fallbackReason": "",
                    }
                return {
                    "stop": baseline_stop, "selectedIndex": int(baseline["selectedClassIndex"]),
                    "contextApplied": False, "authorizedPointCount": authorized_count,
                    "authorizedPointRate": authorized_count / max(examined, 1),
                    "wrongEarlyStop": False, "contextCausedError": False,
                    "stableStart": stable_start, "selectedStepIndex": int(baseline["stopStepIndex"]),
                    "fallbackReason": "paired_baseline_cap",
                }
        else:
            candidate = None
            stable_start = None
    return {
        "stop": baseline_stop, "selectedIndex": int(baseline["selectedClassIndex"]),
        "contextApplied": False, "authorizedPointCount": authorized_count,
        "authorizedPointRate": authorized_count / max(examined, 1),
        "wrongEarlyStop": False, "contextCausedError": False,
        "stableStart": None, "selectedStepIndex": int(baseline["stopStepIndex"]),
        "fallbackReason": "no_authorized_stable_crossing",
    }


def stable_point_features(trial: dict[str, Any], at: float, grid: str = "0.10") -> dict[str, float | int]:
    points = [p for p in trial["pointsByGrid"][grid]
              if float(p["effectiveEvidenceTimeSeconds"]) <= at + 1e-9]
    current = exact_point(trial, grid, at)
    top, _, margin, eeg = raw_top_margin(current)
    entropy = vector_metrics(eeg, int(trial["trueClassIndex"]))["normalizedEntropy"]
    lower = at - 0.40 - 1e-9
    recent = [p for p in points if float(p["effectiveEvidenceTimeSeconds"]) >= lower]
    recent_tops = [raw_top_margin(p)[0] for p in recent]
    flips = sum(a != b for a, b in zip(recent_tops, recent_tops[1:]))
    same_duration = 0.0
    for p in reversed(points):
        if raw_top_margin(p)[0] != top:
            break
        same_duration = at - float(p["effectiveEvidenceTimeSeconds"])
    return {
        "rawTopIndex": top,
        "rawMargin": margin,
        "normalizedTopProbability": eeg[top],
        "normalizedEntropy": entropy,
        "flipsWithin040": flips,
        "sameTopDurationSeconds": same_duration,
    }


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=np.float64), q))


def bootstrap_ci(values: list[float], identity: str) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    key = hashlib.sha256(f"{BOOTSTRAP_SEED}|{identity}".encode("utf-8")).digest()
    seed = int.from_bytes(key[:8], "big")
    data = np.asarray(values, dtype=np.float64)
    rng = np.random.default_rng(seed)
    sample_index = rng.integers(0, len(data), size=(BOOTSTRAP_N, len(data)))
    means = data[sample_index].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def rankdata(values: list[float]) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=np.float64)
    i = 0
    while i < len(values):
        j = i + 1
        while j < len(values) and values[order[j]] == values[order[i]]:
            j += 1
        ranks[order[i:j]] = (i + j - 1) / 2.0 + 1.0
        i = j
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    rx, ry = rankdata(x), rankdata(y)
    if float(np.std(rx)) <= 1e-12 or float(np.std(ry)) <= 1e-12:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def load_inputs() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, dict[str, Any]],
                            dict[tuple[str, str, str], dict[str, Any]],
                            dict[tuple[float, float, str, str], dict[str, Any]],
                            list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    mismatches = []
    for entry in manifest["inputs"]:
        source = ROOT / entry["path"]
        if not source.is_file():
            mismatches.append({"path": entry["path"], "error": "missing"})
        elif source.stat().st_size != int(entry["sizeBytes"]) or sha256_file(source) != entry["sha256"]:
            mismatches.append({"path": entry["path"], "error": "fingerprint_mismatch"})
    if mismatches:
        raise AssertionError(f"frozen input fingerprint mismatch: {mismatches[:3]}")
    trials = m23.load_formal_trials()
    params = json.loads((M23_DIR / "eeg_operating_point_parameters.json").read_text(encoding="utf-8"))
    folds = fold_map(params)
    baseline_rows = read_csv(M23_DIR / "eeg_operating_point_per_trial.csv")
    baseline_by = {(x["operatingPoint"], x["session"], x["trialId"]): x for x in baseline_rows}
    assignments = read_csv(M23_DIR / "context_active_trial_assignments.csv")
    assignment_by: dict[tuple[float, float, str, str], dict[str, Any]] = {}
    for row in assignments:
        key = (float(row["requestedPrecision"]), float(row["requestedCoverage"]), row["session"], row["trialId"])
        assignment_by[key] = row
    m24_rows = read_csv(M24_DIR / "strength_response_per_trial.csv")
    m24_by: dict[tuple[float, float, float, str, str, str], dict[str, Any]] = {}
    for row in m24_rows:
        key = (float(row["contextStrength"]), float(row["requestedPredictionPrecision"]),
               float(row["requestedCoverage"]), row["operatingPoint"], row["session"], row["trialId"])
        m24_by[key] = row
    return trials, manifest, folds, baseline_by, assignment_by, m24_rows, list(m24_by.values())


def make_m23_baseline(row: dict[str, str]) -> dict[str, Any]:
    return {
        "selectedClassIndex": int(row["selectedClassIndex"]),
        "selectedClass": row["selectedClass"],
        "rawTopIndexAtStop": int(row["rawTopIndexAtStop"]),
        "rawTopClassAtStop": row["rawTopClassAtStop"],
        "stopTimeSeconds": float(row["stopTimeSeconds"]),
        "onsetRelativeDecisionTimeSeconds": float(row["onsetRelativeDecisionTimeSeconds"]),
        "earlyStop": to_bool(row["earlyStop"]),
        "stopReason": row["stopReason"],
        "correct": to_bool(row["correct"]),
        "stopStepIndex": int(row["stopStepIndex"]),
        "rawMarginAtStop": float(row["rawMarginAtStop"]),
        "probabilityTopAtStop": float(row["probabilityTopAtStop"]),
    }


def assignment_for(assignment_by: dict[tuple[float, float, str, str], dict[str, Any]],
                   precision: float, coverage: float, trial: dict[str, Any]) -> dict[str, Any]:
    return assignment_by[(precision, coverage, trial["session"], trial["trialId"])]


def validate_cohort(trials: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    counts = dict(Counter(t["session"] for t in trials))
    keys = {(t["session"], t["trialId"]) for t in trials}
    checks = {
        "formalN88": len(trials) == 88,
        "sessionCountsA30B129B229": counts == {"A": 30, "B1": 29, "B2": 29},
        "b1Trial011Excluded": ("B1", "m6_4-trial-011") not in keys,
        "b2Trial023Excluded": ("B2", "m6_4-trial-023") not in keys,
        "sourceRate250HzProvenance": manifest["featureProvenance"]["analysisRateHz"] == 250,
        "operatingPointHashMatchesSidecar": manifest["frozenOperatingPoints"]["matchesFrozenSidecar"],
        "rawWaveformNotRead": manifest["rawEegWaveformRead"] is False,
    }
    if not all(checks.values()):
        raise AssertionError(f"cohort/provenance invariants failed: {checks}")
    for trial in trials:
        for grid, expected in (("0.10", 15), ("0.04", 36)):
            if len(trial["pointsByGrid"][grid]) != expected:
                raise AssertionError(f"{trial['key']} lacks complete {grid} grid")
        if not all(math.isfinite(float(p["effectiveEvidenceTimeSeconds"])) for p in trial["pointsByGrid"]["0.10"]):
            raise AssertionError("nonfinite evidence timestamp")
    return {"checks": checks, "formalN": len(trials), "sessionCounts": counts}


def continuous_evidence(trials: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail: list[dict[str, Any]] = []
    for trial in trials:
        true_index = int(trial["trueClassIndex"])
        true_class = CLASSES[true_index]
        for point in trial["pointsByGrid"]["0.10"]:
            t = float(point["effectiveEvidenceTimeSeconds"])
            raw_top, raw_second, raw_margin, eeg = raw_top_margin(point)
            off_metrics = vector_metrics(eeg, true_index)
            by_strength: dict[float, list[float]] = {}
            priors: dict[float, list[float]] = {}
            for strength in STRENGTHS:
                prior = prior_vector(true_index, strength)
                priors[strength] = prior
                by_strength[strength] = fuse(eeg, prior)
            onehot = by_strength[1.0]
            baseline_070 = vector_metrics(by_strength[0.70], true_index)
            onehot_denominator = metric_vector_change(onehot, eeg)
            for strength in STRENGTHS:
                vector = by_strength[strength]
                metrics = vector_metrics(vector, true_index)
                off = off_metrics
                frac = (metric_vector_change(vector, eeg) / onehot_denominator
                        if onehot_denominator > 1e-12 else None)
                metrics070 = vector_metrics(by_strength[0.70], true_index)
                row = {
                    "analysisBranch": "PURE_CONTEXT_STRENGTH_EVIDENCE_RESPONSE",
                    "analysisRole": "PREREGISTERED_FIXED_TIME" if any(math.isclose(t, x, abs_tol=1e-8) for x in FIXED_TIMES) else "SECONDARY_COMPLETE_010_TRAJECTORY",
                    "diagnosticOnly": True,
                    "authorizationStatus": "COUNTERFACTUAL_FUSION_DIAGNOSTIC_ONLY" if strength < 0.70 - 1e-12 else "STRENGTH_AT_OR_ABOVE_HISTORICAL_GATE_NOT_YET_POINT_AUTHORIZED",
                    "session": trial["session"], "trialId": trial["trialId"],
                    "trueClass": true_class, "trueClassIndex": true_index,
                    "targetFrequencyHz": float(trial["targetFrequencyHz"]),
                    "effectiveEvidenceTimeSeconds": t, "onsetRelativeDecisionTimeSeconds": float(point["onsetRelativeDecisionTimeSeconds"]),
                    "grid": "0.10", "isPreregisteredFixedTime": any(math.isclose(t, x, abs_tol=1e-8) for x in FIXED_TIMES),
                    "requestedStrength": strength, "strengthLabel": strength_label(strength),
                    "rawFbccaScores": json.dumps([float(x) for x in point["scores"]], separators=(",", ":")),
                    "normalizedEegEvidence": json.dumps(eeg, separators=(",", ":")),
                    "rawEegTopClass": CLASSES[raw_top], "rawEegSecondClass": CLASSES[raw_second],
                    "rawEegTopEvidence": off["topEvidence"], "rawEegMargin": raw_margin,
                    "contextTopClass": true_class, "contextTopEqualsRawEegTop": true_index == raw_top,
                    "constructedPrior": json.dumps(priors[strength], separators=(",", ":")),
                    "priorAfterM23ClipRenormalization": json.dumps(priors[strength], separators=(",", ":")),
                    "priorActuallyUsedForPureDiagnostic": json.dumps(priors[strength], separators=(",", ":")),
                    "fusedVector": json.dumps(vector, separators=(",", ":")),
                    "fusedTopClass": CLASSES[metrics["topIndex"]],
                    "fusedTopEvidence": metrics["topEvidence"],
                    "fusedTopMinusSecondMargin": metrics["margin"],
                    "fusedNormalizedEntropy": metrics["normalizedEntropy"],
                    "fusedTrueClassEvidence": metrics["trueClassEvidence"],
                    "deltaTopVsUniformOFF": metrics["topEvidence"] - off["topEvidence"],
                    "deltaMarginVsUniformOFF": metrics["margin"] - off["margin"],
                    "deltaEntropyVsUniformOFF": metrics["normalizedEntropy"] - off["normalizedEntropy"],
                    "deltaTrueClassEvidenceVsUniformOFF": metrics["trueClassEvidence"] - off["trueClassEvidence"],
                    "deltaTopVsStrength070": metrics["topEvidence"] - metrics070["topEvidence"],
                    "deltaMarginVsStrength070": metrics["margin"] - metrics070["margin"],
                    "deltaEntropyVsStrength070": metrics["normalizedEntropy"] - metrics070["normalizedEntropy"],
                    "deltaTrueClassEvidenceVsStrength070": metrics["trueClassEvidence"] - metrics070["trueClassEvidence"],
                    "fractionOfOneHotVectorChange": frac,
                    "L1DistanceFromEEGVector": metric_vector_change(vector, eeg),
                    "isOneHotEndpoint": strength >= 1.0 - 1e-12,
                }
                detail.append(row)
    aggregate: list[dict[str, Any]] = []
    scopes: list[tuple[str, str | None, str | None]] = [("OVERALL", None, None)]
    scopes += [("SESSION", s, None) for s in SESSIONS]
    scopes += [("OPERATING_POINT", None, op) for op in OP_NAMES]
    scopes += [(f"SESSION_OPERATING_POINT", s, op) for s in SESSIONS for op in OP_NAMES]
    for scope, session, op in scopes:
        selected_trials = [t for t in trials if session is None or t["session"] == session]
        for strength in STRENGTHS:
            for t in sorted({float(r["effectiveEvidenceTimeSeconds"]) for r in detail}):
                subset = [r for r in detail if r["requestedStrength"] == strength
                          and math.isclose(float(r["effectiveEvidenceTimeSeconds"]), t, abs_tol=1e-8)
                          and (session is None or r["session"] == session)]
                if not subset:
                    continue
                n = len(subset)
                if op is not None:
                    n = len(selected_trials)
                aggregate.append({
                    "scope": scope, "session": session or "", "operatingPoint": op or "",
                    "strength": strength, "strengthLabel": strength_label(strength),
                    "effectiveEvidenceTimeSeconds": t, "N": n,
                    "meanFusedTopEvidence": float(np.mean([r["fusedTopEvidence"] for r in subset])),
                    "meanFusedMargin": float(np.mean([r["fusedTopMinusSecondMargin"] for r in subset])),
                    "meanFusedNormalizedEntropy": float(np.mean([r["fusedNormalizedEntropy"] for r in subset])),
                    "meanFusedTrueClassEvidence": float(np.mean([r["fusedTrueClassEvidence"] for r in subset])),
                    "meanDeltaTopVsOFF": float(np.mean([r["deltaTopVsUniformOFF"] for r in subset])),
                    "meanDeltaMarginVsOFF": float(np.mean([r["deltaMarginVsUniformOFF"] for r in subset])),
                    "meanDeltaEntropyVsOFF": float(np.mean([r["deltaEntropyVsUniformOFF"] for r in subset])),
                    "meanDeltaTrueEvidenceVsOFF": float(np.mean([r["deltaTrueClassEvidenceVsUniformOFF"] for r in subset])),
                    "meanDeltaTopVs070": float(np.mean([r["deltaTopVsStrength070"] for r in subset])),
                    "meanDeltaMarginVs070": float(np.mean([r["deltaMarginVsStrength070"] for r in subset])),
                    "meanDeltaEntropyVs070": float(np.mean([r["deltaEntropyVsStrength070"] for r in subset])),
                    "meanDeltaTrueEvidenceVs070": float(np.mean([r["deltaTrueClassEvidenceVsStrength070"] for r in subset])),
                    "meanFractionOfOneHotVectorChange": float(np.mean([r["fractionOfOneHotVectorChange"] for r in subset
                                                                       if r["fractionOfOneHotVectorChange"] is not None])),
                    "counterfactualBelow070N": sum(r["requestedStrength"] < 0.70 - 1e-12 for r in subset),
                })
    return detail, aggregate


def authorized_evidence(
    trials: list[dict[str, Any]], folds: dict[str, dict[str, Any]],
    baseline_by: dict[tuple[str, str, str], dict[str, Any]],
    m24_values: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    detail: list[dict[str, Any]] = []
    stops: list[dict[str, Any]] = []
    m24_map = {(float(r["contextStrength"]), float(r["requestedPredictionPrecision"]),
                float(r["requestedCoverage"]), r["operatingPoint"], r["session"], r["trialId"]): r
               for r in m24_values}
    m23_rows = read_csv(M23_DIR / "paired_context_results_per_trial.csv")
    m23_map = {(r["conditionId"], r["operatingPoint"], r["session"], r["trialId"]): r for r in m23_rows}
    m24_mismatch = 0
    m23_mismatch = 0
    m23_source_comparison_rows = 0
    for trial in trials:
        session = trial["session"]
        true_index = int(trial["trueClassIndex"])
        for op in OP_NAMES:
            config = op_config(folds[session], op)
            baseline_row = baseline_by[(op, session, trial["trialId"])]
            baseline = make_m23_baseline(baseline_row)
            for strength in STRENGTHS[1:]:
                prior = prior_vector(true_index, strength)
                result = m23.context_stop_trial(
                    trial, baseline, float(config["topThreshold"]), float(config["marginThreshold"]),
                    prior, True,
                )
                m23_source_condition_id = (
                    "S4_ORACLE_ONE_HOT" if math.isclose(strength, 1.0, abs_tol=1e-10)
                    else f"SIM_S{strength:.2f}_P1.00_C1.00"
                )
                m23_source_row = m23_map.get((m23_source_condition_id, op, session, trial["trialId"]))
                m23_source_match = None
                if any(math.isclose(strength, s, abs_tol=1e-10) for s in (0.70, 0.90, 0.95, 0.98, 0.99, 1.00)):
                    m23_source_comparison_rows += 1
                    m23_source_match = bool(
                        m23_source_row is not None
                        and math.isclose(float(m23_source_row["contextStrength"]), strength, abs_tol=1e-10)
                        and math.isclose(float(m23_source_row["requestedPredictionPrecision"]), 1.0, abs_tol=1e-10)
                        and math.isclose(float(m23_source_row["requestedCoverage"]), 1.0, abs_tol=1e-10)
                        and math.isclose(float(m23_source_row["contextStopTimeSeconds"]), float(result["stopTimeSeconds"]), abs_tol=1e-9)
                        and int(m23_source_row["contextAuthorizedPointCount"]) == int(result.get("authorizedPointCount", 0))
                        and to_bool(m23_source_row["contextAppliedAtStop"]) == bool(result.get("contextAppliedAtStop", False))
                        and m23_source_row["contextSelectedClass"] == result["selectedClass"]
                    )
                    m23_mismatch += int(not m23_source_match)
                stop_row = {
                    "analysisBranch": "AUTHORIZED_CONTEXT_EVIDENCE_RESPONSE",
                    "session": session, "trialId": trial["trialId"], "operatingPoint": op,
                    "requestedStrength": strength, "strengthLabel": strength_label(strength),
                    "contextTopClass": CLASSES[true_index],
                    "priorTopMass": max(prior),
                    "authorizedPointCount": int(result.get("authorizedPointCount", 0)),
                    "contextAppliedAtStop": bool(result.get("contextAppliedAtStop", False)),
                    "pairedEegOnlyStopSeconds": baseline["stopTimeSeconds"],
                    "contextStopSeconds": float(result["stopTimeSeconds"]),
                    "contextGainSeconds": baseline["stopTimeSeconds"] - float(result["stopTimeSeconds"]),
                    "selectedClass": result["selectedClass"],
                    "correct": bool(result["correct"]),
                    "m23SourceConditionId": m23_source_condition_id if m23_source_match is not None else "",
                    "m23SourceComparisonApplicable": m23_source_match is not None,
                    "m23SourceExactMatch": m23_source_match,
                }
                stops.append(stop_row)
                m24_row = m24_map.get((strength, 1.0, 1.0, op, session, trial["trialId"]))
                if m24_row is not None:
                    if (not math.isclose(float(m24_row["contextStopTimeSeconds"]), float(result["stopTimeSeconds"]), abs_tol=1e-9)
                        or int(m24_row["contextAuthorizedPointCount"]) != int(result.get("authorizedPointCount", 0))
                        or to_bool(m24_row["contextAppliedAtStop"]) != bool(result.get("contextAppliedAtStop", False))):
                        m24_mismatch += 1
            for point in trial["pointsByGrid"]["0.10"]:
                t = float(point["effectiveEvidenceTimeSeconds"])
                raw_top, raw_second, raw_margin, eeg = raw_top_margin(point)
                for strength in STRENGTHS:
                    prior = prior_vector(true_index, strength)
                    prior_top = max(range(3), key=lambda i: (prior[i], -i))
                    authorized = prior_top == raw_top and prior[prior_top] >= 0.70 - 1e-12
                    actual = fuse(eeg, prior) if authorized else list(eeg)
                    metrics = vector_metrics(actual, true_index)
                    gate_minimum = t >= m23.MIN_EVIDENCE_SECONDS - 1e-9
                    top_pass = metrics["topIndex"] == raw_top and metrics["topEvidence"] >= float(config["topThreshold"]) - 1e-12
                    margin_pass = metrics["topIndex"] == raw_top and metrics["margin"] >= float(config["marginThreshold"]) - 1e-12
                    detail.append({
                        "analysisBranch": "AUTHORIZED_CONTEXT_EVIDENCE_RESPONSE",
                        "session": session, "trialId": trial["trialId"], "operatingPoint": op,
                        "effectiveEvidenceTimeSeconds": t,
                        "isPreregisteredFixedTime": any(math.isclose(t, x, abs_tol=1e-8) for x in FIXED_TIMES),
                        "requestedStrength": strength, "strengthLabel": strength_label(strength),
                        "contextTopClass": CLASSES[true_index], "rawEegTopClass": CLASSES[raw_top],
                        "rawEegMargin": raw_margin, "normalizedEegEvidence": json.dumps(eeg, separators=(",", ":")),
                        "constructedPrior": json.dumps(prior, separators=(",", ":")),
                        "authorizationGatePass": authorized,
                        "actualPriorUsedForFusion": json.dumps(prior, separators=(",", ":")) if authorized else "EEG_ONLY",
                        "actualDecisionEvidence": json.dumps(actual, separators=(",", ":")),
                        "actualFusedTopClass": CLASSES[metrics["topIndex"]],
                        "actualFusedTopEvidence": metrics["topEvidence"],
                        "actualFusedMargin": metrics["margin"],
                        "actualFusedNormalizedEntropy": metrics["normalizedEntropy"],
                        "actualFusedTrueClassEvidence": metrics["trueClassEvidence"],
                        "minimumEvidenceGatePass": gate_minimum,
                        "rawTopConfirmationGatePass": metrics["topIndex"] == raw_top,
                        "frozenTopThreshold": float(config["topThreshold"]),
                        "topThresholdGatePass": bool(top_pass),
                        "frozenMarginThreshold": float(config["marginThreshold"]),
                        "marginThresholdGatePass": bool(margin_pass),
                        "pointwiseStopEvidenceGatesPass": bool(gate_minimum and top_pass and margin_pass),
                        "counterfactualOnly": False,
                    })
    summary: list[dict[str, Any]] = []
    for op in OP_NAMES:
        for strength in STRENGTHS:
            for t in sorted({r["effectiveEvidenceTimeSeconds"] for r in detail}):
                rows = [r for r in detail if r["operatingPoint"] == op and r["requestedStrength"] == strength
                        and math.isclose(r["effectiveEvidenceTimeSeconds"], t, abs_tol=1e-8)]
                if not rows:
                    continue
                authorized_rows = [r for r in rows if r["authorizationGatePass"]]
                summary.append({
                    "analysisBranch": "AUTHORIZED_CONTEXT_EVIDENCE_RESPONSE",
                    "operatingPoint": op, "requestedStrength": strength, "strengthLabel": strength_label(strength),
                    "effectiveEvidenceTimeSeconds": t, "isPreregisteredFixedTime": any(math.isclose(t, x, abs_tol=1e-8) for x in FIXED_TIMES),
                    "N": len(rows), "authorizedN": len(authorized_rows),
                    "authorizedRate": len(authorized_rows) / len(rows),
                    "allM23PointwiseGatesPassN": sum(bool(r["pointwiseStopEvidenceGatesPass"]) for r in rows),
                    "authorizedPointwiseGatesPassN": sum(bool(r["pointwiseStopEvidenceGatesPass"]) for r in authorized_rows),
                    "meanActualEvidenceTop": float(np.mean([r["actualFusedTopEvidence"] for r in rows])),
                    "meanActualEvidenceMargin": float(np.mean([r["actualFusedMargin"] for r in rows])),
                    "meanActualNormalizedEntropy": float(np.mean([r["actualFusedNormalizedEntropy"] for r in rows])),
                    "meanActualTrueClassEvidence": float(np.mean([r["actualFusedTrueClassEvidence"] for r in rows])),
                    "meanAuthorizedTopEvidence": float(np.mean([r["actualFusedTopEvidence"] for r in authorized_rows])) if authorized_rows else None,
                    "meanAuthorizedMargin": float(np.mean([r["actualFusedMargin"] for r in authorized_rows])) if authorized_rows else None,
                })
    info = {
        "historicalHelperReplayRows": len(stops),
        "m23SourceComparisonRows": m23_source_comparison_rows,
        "m24ComparisonRows": len([1 for s in STRENGTHS[1:] for trial in trials for op in OP_NAMES
                                  if (s, 1.0, 1.0, op, trial["session"], trial["trialId"]) in m24_map]),
        "m23HelperMismatchN": m23_mismatch,
        "m24ExactReuseReplayMismatchN": m24_mismatch,
    }
    if m23_mismatch or m24_mismatch:
        raise AssertionError(f"historical M23/M24 replay mismatches: {info}")
    return detail, summary, info


def choose_thresholds(
    trials: list[dict[str, Any]], folds: dict[str, dict[str, Any]],
    assignment_by: dict[tuple[float, float, str, str], dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_rows: list[dict[str, Any]] = []
    selections: dict[str, Any] = {}
    for held_out in SESSIONS:
        fold = folds[held_out]
        train_trials = [t for t in trials if t["session"] != held_out]
        if {t["session"] for t in train_trials} != set(fold["trainingSessions"]):
            raise AssertionError(f"outer fold training sessions mismatch for {held_out}")
        for op in OP_NAMES:
            config = op_config(fold, op)
            baselines = {
                (t["session"], t["trialId"]): raw_stop(
                    t, "0.10", float(config["topThreshold"]), float(config["marginThreshold"]), 0.50, STABILITY_PRIMARY)
                for t in train_trials
            }
            baseline_correct = sum(int(b["correct"]) for b in baselines.values())
            baseline_n = len(train_trials)
            for branch in BRANCHES:
                local_rows: list[dict[str, Any]] = []
                for top_threshold in TOP_GRID:
                    for margin_threshold in MARGIN_GRID:
                        for minimum in MIN_EVIDENCE_GRID:
                            gains: list[float] = []
                            correct_n = 0
                            wrong_early_n = 0
                            no_delay_fail_n = 0
                            by_strength_correct = {s: 0 for s in STRENGTHS}
                            baseline_by_strength = {s: 0 for s in STRENGTHS}
                            for trial in train_trials:
                                key = (trial["session"], trial["trialId"])
                                base = baselines[key]
                                assignment = assignment_for(assignment_by, 1.0, 1.0, trial)
                                if not to_bool(assignment["activeAssigned"]) or not to_bool(assignment["contextPredictionCorrect"]):
                                    raise AssertionError("primary 1.00/1.00 assignment is not complete target aligned")
                                true_index = int(trial["trueClassIndex"])
                                for strength in STRENGTHS:
                                    result = simulate_context(
                                        trial, "0.10", base, strength, true_index, True, branch,
                                        top_threshold, margin_threshold, minimum, STABILITY_PRIMARY, 0.0,
                                    )
                                    gain = float(base["stopTimeSeconds"]) - float(result["stop"])
                                    if gain < -1e-9:
                                        no_delay_fail_n += 1
                                    gains.append(max(0.0, gain))
                                    is_correct = result["selectedIndex"] == true_index
                                    correct_n += int(is_correct)
                                    by_strength_correct[strength] += int(is_correct)
                                    baseline_by_strength[strength] += int(base["correct"])
                                    wrong_early_n += int(result["wrongEarlyStop"])
                            total_n = len(train_trials) * len(STRENGTHS)
                            accuracy = correct_n / total_n
                            baseline_accuracy = baseline_correct / baseline_n
                            per_strength_no_loss = all(
                                by_strength_correct[s] >= baseline_by_strength[s] * 1
                                for s in STRENGTHS
                            )
                            valid = accuracy >= baseline_accuracy - 1e-12 and per_strength_no_loss and wrong_early_n == 0 and no_delay_fail_n == 0
                            row = {
                                "heldOutSession": held_out,
                                "trainingSessions": "|".join(fold["trainingSessions"]),
                                "operatingPoint": op, "branch": branch,
                                "topThreshold": top_threshold, "marginThreshold": margin_threshold,
                                "minimumEvidenceSeconds": minimum, "stabilitySeconds": STABILITY_PRIMARY,
                                "candidateStrengthCount": len(STRENGTHS), "candidateStrengths": "|".join(strength_label(s) for s in STRENGTHS),
                                "trainingN": len(train_trials), "trainingTrialStrengthRows": total_n,
                                "pairedBaselineAccuracy": baseline_accuracy,
                                "candidateTrainingAccuracy": accuracy,
                                "candidateTrainingCorrect": correct_n,
                                "baselineTrainingCorrect": baseline_correct,
                                "trainingMeanPairedGainSeconds": float(np.mean(gains)),
                                "trainingMedianPairedGainSeconds": float(np.median(gains)),
                                "trainingWrongEarlyStopN": wrong_early_n,
                                "trainingNoDelayViolationN": no_delay_fail_n,
                                "perStrengthNoAccuracyLoss": per_strength_no_loss,
                                "validCandidate": valid,
                                "selectionObjective": "mean paired gain aggregated over all ten strengths and training trials",
                                "selected": False,
                            }
                            local_rows.append(row)
                valid_rows = [r for r in local_rows if r["validCandidate"]]
                selected_row = None
                if valid_rows:
                    selected_row = max(valid_rows, key=lambda r: (
                        r["trainingMeanPairedGainSeconds"], r["topThreshold"],
                        r["marginThreshold"], r["minimumEvidenceSeconds"],
                    ))
                    selected_row["selected"] = True
                candidate_rows.extend(local_rows)
                key = f"{branch}|{held_out}|{op}"
                selections[key] = {
                    "branch": branch,
                    "heldOutSession": held_out,
                    "trainingSessions": list(fold["trainingSessions"]),
                    "trainingN": len(train_trials),
                    "operatingPoint": op,
                    "frozenM23OuterFoldParameters": {
                        "topThreshold": float(config["topThreshold"]),
                        "marginThreshold": float(config["marginThreshold"]),
                        "minimumEvidenceSeconds": 0.50,
                        "stabilitySeconds": STABILITY_PRIMARY,
                    },
                    "selectedSharedContextParameters": ({
                        "topThreshold": selected_row["topThreshold"],
                        "marginThreshold": selected_row["marginThreshold"],
                        "minimumEvidenceSeconds": selected_row["minimumEvidenceSeconds"],
                        "stabilitySeconds": STABILITY_PRIMARY,
                    } if selected_row is not None else None),
                    "selectedTrainingMeanGainSeconds": (
                        selected_row["trainingMeanPairedGainSeconds"] if selected_row is not None else None
                    ),
                    "selectedTrainingAccuracy": (
                        selected_row["candidateTrainingAccuracy"] if selected_row is not None else None
                    ),
                    "trainingWrongEarlyStopN": (
                        selected_row["trainingWrongEarlyStopN"] if selected_row is not None else None
                    ),
                    "candidateCount": len(local_rows),
                    "validCandidateCount": len(valid_rows),
                    "selectionOrder": ["maximize_training_mean_gain_over_all_strengths", "higher_top_threshold",
                                       "higher_margin_threshold", "later_minimum_evidence"],
                    "heldOutSessionUsedDuringSelection": False,
                }
    return candidate_rows, selections


def evaluate_shared_latency(
    trials: list[dict[str, Any]], folds: dict[str, dict[str, Any]],
    selections: dict[str, Any], assignment_by: dict[tuple[float, float, str, str], dict[str, Any]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for held_out in SESSIONS:
        fold = folds[held_out]
        test_trials = [t for t in trials if t["session"] == held_out]
        for op in OP_NAMES:
            base_cfg = op_config(fold, op)
            for branch in BRANCHES:
                selected = selections[f"{branch}|{held_out}|{op}"]["selectedSharedContextParameters"]
                for trial in test_trials:
                    base = raw_stop(trial, "0.10", float(base_cfg["topThreshold"]),
                                    float(base_cfg["marginThreshold"]), 0.50, STABILITY_PRIMARY)
                    assignment = assignment_for(assignment_by, 1.0, 1.0, trial)
                    for strength in STRENGTHS:
                        if selected is None:
                            result = {
                                "stop": float(base["stopTimeSeconds"]),
                                "selectedIndex": int(base["selectedClassIndex"]),
                                "contextApplied": False, "authorizedPointCount": 0,
                                "authorizedPointRate": 0.0, "wrongEarlyStop": False,
                                "contextCausedError": False, "fallbackReason": "no_valid_shared_candidate",
                            }
                            context_top = int(trial["trueClassIndex"])
                            top_threshold = margin_threshold = minimum = None
                        else:
                            context_top = int(trial["trueClassIndex"])
                            top_threshold = float(selected["topThreshold"])
                            margin_threshold = float(selected["marginThreshold"])
                            minimum = float(selected["minimumEvidenceSeconds"])
                            result = simulate_context(
                                trial, "0.10", base, strength, context_top,
                                to_bool(assignment["activeAssigned"]), branch,
                                top_threshold, margin_threshold, minimum, STABILITY_PRIMARY, 0.0,
                            )
                        if float(result["stop"]) > float(base["stopTimeSeconds"]) + 1e-9:
                            raise AssertionError("Context delayed beyond paired EEG-only cap")
                        selected_index = int(result["selectedIndex"])
                        if result["contextApplied"] and selected_index != context_top:
                            raise AssertionError("Context reranked raw EEG class identity")
                        gain = max(0.0, float(base["stopTimeSeconds"]) - float(result["stop"]))
                        correct = selected_index == int(trial["trueClassIndex"])
                        rows.append({
                            "recordType": "HELD_OUT_TRIAL",
                            "branch": branch, "heldOutSession": held_out,
                            "trainingSessions": "|".join(fold["trainingSessions"]),
                            "session": trial["session"], "trialId": trial["trialId"],
                            "operatingPoint": op, "requestedStrength": strength,
                            "strengthLabel": strength_label(strength),
                            "contextTopClass": CLASSES[context_top],
                            "precision": 1.0, "coverage": 1.0,
                            "contextThresholdSelected": selected is not None,
                            "contextTopThreshold": top_threshold,
                            "contextMarginThreshold": margin_threshold,
                            "contextMinimumEvidenceSeconds": minimum,
                            "stabilitySeconds": STABILITY_PRIMARY,
                            "baselineTopThreshold": float(base_cfg["topThreshold"]),
                            "baselineMarginThreshold": float(base_cfg["marginThreshold"]),
                            "pairedEegOnlyStopSeconds": float(base["stopTimeSeconds"]),
                            "contextStopSeconds": float(result["stop"]),
                            "pairedGainSeconds": gain,
                            "contextAuthorizedPointCount": int(result["authorizedPointCount"]),
                            "authorizedBeforeBaseline": int(result["authorizedPointCount"]) > 0,
                            "contextAppliedAtStop": bool(result["contextApplied"]),
                            "baselineSelectedClass": base["selectedClass"],
                            "contextSelectedClass": CLASSES[selected_index],
                            "baselineCorrect": bool(base["correct"]),
                            "contextCorrect": bool(correct),
                            "wrongEarlyStop": bool(result["wrongEarlyStop"]),
                            "contextCausedError": bool(result["contextCausedError"]),
                            "noDelayInvariantPass": float(result["stop"]) <= float(base["stopTimeSeconds"]) + 1e-9,
                            "noRerankInvariantPass": not result["contextApplied"] or selected_index == context_top,
                            "assignmentSha256": assignment["assignmentSha256"],
                            "selectedThresholdTrainingOnly": selections[f"{branch}|{held_out}|{op}"]["heldOutSessionUsedDuringSelection"] is False,
                        })
    return rows


def summarize_latency(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    group_specs = [
        ("ALL_HELD_OUT", lambda r: (r["branch"], r["operatingPoint"], r["requestedStrength"]), ("branch", "operatingPoint", "requestedStrength")),
        ("SESSION", lambda r: (r["branch"], r["heldOutSession"], r["operatingPoint"], r["requestedStrength"]), ("branch", "heldOutSession", "operatingPoint", "requestedStrength")),
    ]
    for scope, key_fn, names in group_specs:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[key_fn(row)].append(row)
        for key, group in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0])):
            values = [float(r["pairedGainSeconds"]) for r in group]
            authorized = [r for r in group if r["authorizedBeforeBaseline"]]
            conditional = [float(r["pairedGainSeconds"]) for r in authorized]
            ci = bootstrap_ci(values, f"latency|{scope}|{key}|all")
            cci = bootstrap_ci(conditional, f"latency|{scope}|{key}|conditional")
            record = {"scope": scope, "N": len(group)}
            record.update({name: value for name, value in zip(names, key)})
            record.update({
                "meanPairedGainAllTrialsSeconds": float(np.mean(values)),
                "medianPairedGainAllTrialsSeconds": float(np.median(values)),
                "allTrialMeanGainBootstrap95Lower": ci[0], "allTrialMeanGainBootstrap95Upper": ci[1],
                "authorizedTrialN": len(authorized), "authorizedRate": len(authorized) / len(group),
                "conditionalMeanGainSeconds": float(np.mean(conditional)) if conditional else None,
                "conditionalMedianGainSeconds": float(np.median(conditional)) if conditional else None,
                "conditionalGainBootstrap95Lower": cci[0], "conditionalGainBootstrap95Upper": cci[1],
                "acceleratedFraction": sum(v > 1e-9 for v in values) / len(values),
                "fractionGainAtLeast010": sum(v >= 0.10 - 1e-9 for v in values) / len(values),
                "fractionGainAtLeast020": sum(v >= 0.20 - 1e-9 for v in values) / len(values),
                "fractionGainAtLeast030": sum(v >= 0.30 - 1e-9 for v in values) / len(values),
                "fractionGainAtLeast050": sum(v >= 0.50 - 1e-9 for v in values) / len(values),
                "baselineAccuracy": float(np.mean([r["baselineCorrect"] for r in group])),
                "contextAccuracy": float(np.mean([r["contextCorrect"] for r in group])),
                "wrongEarlyStopN": sum(bool(r["wrongEarlyStop"]) for r in group),
                "contextCausedErrorN": sum(bool(r["contextCausedError"]) for r in group),
                "meanBaselineStopSeconds": float(np.mean([r["pairedEegOnlyStopSeconds"] for r in group])),
                "meanContextStopSeconds": float(np.mean([r["contextStopSeconds"] for r in group])),
                "thresholdSelected": all(bool(r["contextThresholdSelected"]) for r in group),
            })
            result.append(record)
    return result


def summarize_scopes(detail: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for precision in PRECISIONS:
        for strength in SURFACE_STRENGTHS:
            for op in OP_NAMES:
                subset = [r for r in detail if r["precision"] == precision and r["strength"] == strength and r["operatingPoint"] == op]
                if not subset:
                    continue
                active = [r for r in subset if r["assignmentActive"]]
                authorized_trials = [r for r in subset if r["authorizedTrialN"] > 0]
                assignment_correct = [r for r in active if r["assignmentCorrect"]]
                authorized_correct = [r for r in authorized_trials if r["assignmentCorrect"]]
                gains = [r["gainSeconds"] for r in subset]
                ci = bootstrap_ci(gains, f"surface|{precision}|{strength}|{op}")
                row = {
                    "precision": precision, "coverage": PRIMARY_COVERAGE, "strength": strength,
                    "strengthLabel": strength_label(strength), "operatingPoint": op, "N": len(subset),
                    "assignedN": len(active), "assignedRate": len(active) / len(subset),
                    "rawAssignmentPrecision": len(assignment_correct) / len(active) if active else None,
                    "authorizedTrialN": len(authorized_trials), "authorizedRate": len(authorized_trials) / len(subset),
                    "authorizedPrecision": len(authorized_correct) / len(authorized_trials) if authorized_trials else None,
                    "meanAuthorizedPointRate": float(np.mean([r["authorizedPointRate"] for r in subset])),
                    "meanGainSeconds": float(np.mean(gains)), "gainBootstrap95Lower": ci[0], "gainBootstrap95Upper": ci[1],
                    "acceleratedFraction": sum(x > 1e-9 for x in gains) / len(gains),
                    "fractionGainAtLeast010": sum(x >= 0.10 - 1e-9 for x in gains) / len(gains),
                    "fractionGainAtLeast020": sum(x >= 0.20 - 1e-9 for x in gains) / len(gains),
                    "fractionGainAtLeast030": sum(x >= 0.30 - 1e-9 for x in gains) / len(gains),
                    "fractionGainAtLeast050": sum(x >= 0.50 - 1e-9 for x in gains) / len(gains),
                    "accuracy": float(np.mean([r["correct"] for r in subset])),
                    "baselineAccuracy": float(np.mean([r["baselineCorrect"] for r in subset])),
                    "wrongEarlyStopN": sum(bool(r["wrongEarlyStop"]) for r in subset),
                    "contextCausedErrorN": sum(bool(r["contextCausedError"]) for r in subset),
                    "meanFusedTopAt050": float(np.mean([r["meanFusedTopAt050"] for r in subset])),
                    "meanFusedTopAt090": float(np.mean([r["meanFusedTopAt090"] for r in subset])),
                    "meanFusedTopAt150": float(np.mean([r["meanFusedTopAt150"] for r in subset])),
                    "meanFusedMarginAt050": float(np.mean([r["meanFusedMarginAt050"] for r in subset])),
                    "meanFusedMarginAt090": float(np.mean([r["meanFusedMarginAt090"] for r in subset])),
                    "meanFusedMarginAt150": float(np.mean([r["meanFusedMarginAt150"] for r in subset])),
                }
                rows.append(row)
    return rows


def svg_line(path: Path, title: str, x_values: list[float], series: list[tuple[str, list[float | None]]],
             x_label: str, y_label: str) -> None:
    width, height = 960, 580
    left, right, top, bottom = 90, 260, 70, 105
    plot_w, plot_h = width - left - right, height - top - bottom
    usable = [float(v) for _, values in series for v in values if v is not None and math.isfinite(float(v))]
    if not usable:
        usable = [0.0, 1.0]
    ymin, ymax = min(usable), max(usable)
    if math.isclose(ymin, ymax, abs_tol=1e-12):
        ymin -= 0.05
        ymax += 0.05
    pad = (ymax - ymin) * 0.08
    ymin -= pad
    ymax += pad
    xmin, xmax = min(x_values), max(x_values)
    if math.isclose(xmin, xmax):
        xmax = xmin + 1.0
    def px(x: float) -> float:
        return left + (x - xmin) / (xmax - xmin) * plot_w
    def py(y: float) -> float:
        return top + (ymax - y) / (ymax - ymin) * plot_h
    colors = ("#1565c0", "#d84315", "#2e7d32", "#6a1b9a", "#00838f", "#c62828", "#5d4037", "#455a64")
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              f'<text x="{left}" y="35" font-size="20" font-family="Arial" font-weight="bold">{html.escape(title)}</text>']
    for i in range(6):
        y = ymin + (ymax - ymin) * i / 5
        yy = py(y)
        pieces.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left+plot_w}" y2="{yy:.1f}" stroke="#dddddd"/>')
        pieces.append(f'<text x="{left-12}" y="{yy+4:.1f}" text-anchor="end" font-size="12" font-family="Arial">{y:.3f}</text>')
    pieces += [f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>',
               f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#333"/>']
    for x in x_values:
        xx = px(x)
        pieces.append(f'<line x1="{xx:.1f}" y1="{top+plot_h}" x2="{xx:.1f}" y2="{top+plot_h+5}" stroke="#333"/>')
        pieces.append(f'<text x="{xx:.1f}" y="{top+plot_h+23}" text-anchor="middle" font-size="11" font-family="Arial">{x:.3f}</text>')
    pieces.append(f'<text x="{left+plot_w/2:.1f}" y="{height-28}" text-anchor="middle" font-size="14" font-family="Arial">{html.escape(x_label)}</text>')
    pieces.append(f'<text x="22" y="{top+plot_h/2:.1f}" transform="rotate(-90 22 {top+plot_h/2:.1f})" text-anchor="middle" font-size="14" font-family="Arial">{html.escape(y_label)}</text>')
    for index, (name, values) in enumerate(series):
        color = colors[index % len(colors)]
        coords = [(px(x_values[i]), py(float(y))) for i, y in enumerate(values) if y is not None and i < len(x_values)]
        if coords:
            points = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
            pieces.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.5"/>')
            for x, y in coords:
                pieces.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.8" fill="{color}"/>')
        ly = top + 20 + index * 24
        pieces.append(f'<line x1="{left+plot_w+24}" y1="{ly-4}" x2="{left+plot_w+46}" y2="{ly-4}" stroke="{color}" stroke-width="3"/>')
        pieces.append(f'<text x="{left+plot_w+54}" y="{ly}" font-size="12" font-family="Arial">{html.escape(name)}</text>')
    pieces.append("</svg>")
    path.write_text("\n".join(pieces), encoding="utf-8")


def svg_heatmap(path: Path, title: str, x_labels: list[str], y_labels: list[str],
                matrix: list[list[float | None]], value_label: str) -> None:
    width, height = 960, 520
    left, top = 190, 90
    cell_w = min(72, (width - left - 150) / max(1, len(x_labels)))
    cell_h = min(44, (height - top - 90) / max(1, len(y_labels)))
    values = [float(v) for row in matrix for v in row if v is not None and math.isfinite(float(v))]
    lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
    if math.isclose(lo, hi, abs_tol=1e-12):
        hi = lo + 1.0
    def color(v: float) -> str:
        q = min(1.0, max(0.0, (v - lo) / (hi - lo)))
        r = int(245 - 175 * q)
        g = int(248 - 110 * q)
        b = int(255 - 25 * q)
        return f"rgb({r},{g},{b})"
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
              '<rect width="100%" height="100%" fill="white"/>',
              f'<text x="{left}" y="36" font-size="20" font-family="Arial" font-weight="bold">{html.escape(title)}</text>']
    for j, label in enumerate(y_labels):
        y = top + j * cell_h
        pieces.append(f'<text x="{left-10}" y="{y+cell_h*0.65:.1f}" text-anchor="end" font-size="12" font-family="Arial">{html.escape(label)}</text>')
        for i, xlab in enumerate(x_labels):
            x = left + i * cell_w
            val = matrix[j][i] if i < len(matrix[j]) else None
            fill = "#eeeeee" if val is None else color(float(val))
            pieces.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell_w-2:.1f}" height="{cell_h-2:.1f}" fill="{fill}" stroke="white"/>')
            text = "NA" if val is None else f"{float(val):.3f}"
            pieces.append(f'<text x="{x+cell_w/2:.1f}" y="{y+cell_h*0.65:.1f}" text-anchor="middle" font-size="10" font-family="Arial">{text}</text>')
    for i, label in enumerate(x_labels):
        x = left + i * cell_w + cell_w / 2
        pieces.append(f'<text x="{x:.1f}" y="{top-12}" text-anchor="middle" font-size="10" font-family="Arial">{html.escape(label)}</text>')
    pieces.append(f'<text x="{left}" y="{top+len(y_labels)*cell_h+35}" font-size="12" font-family="Arial">{html.escape(value_label)} range {lo:.3f} to {hi:.3f}</text>')
    pieces.append("</svg>")
    path.write_text("\n".join(pieces), encoding="utf-8")


def svg_scatter(path: Path, title: str, points: list[tuple[float, float, str]], x_label: str, y_label: str) -> None:
    width, height = 900, 560
    left, top, plot_w, plot_h = 95, 75, 700, 380
    if not points:
        points = [(0.0, 0.0, "no data")]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if math.isclose(xmin, xmax): xmax = xmin + 1.0
    if math.isclose(ymin, ymax): ymax = ymin + 1.0
    def px(x: float) -> float: return left + (x-xmin)/(xmax-xmin)*plot_w
    def py(y: float) -> float: return top + (ymax-y)/(ymax-ymin)*plot_h
    colors = {"A":"#1565c0","B1":"#d84315","B2":"#2e7d32"}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{left}" y="35" font-size="20" font-family="Arial" font-weight="bold">{html.escape(title)}</text>',
             f'<line x1="{left}" y1="{top+plot_h}" x2="{left+plot_w}" y2="{top+plot_h}" stroke="#333"/>',
             f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>']
    for x,y,s in points:
        parts.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="4" fill="{colors.get(s,"#546e7a")}" opacity="0.68"/>')
    parts.append(f'<text x="{left+plot_w/2}" y="{height-36}" text-anchor="middle" font-size="14" font-family="Arial">{html.escape(x_label)}</text>')
    parts.append(f'<text x="22" y="{top+plot_h/2}" transform="rotate(-90 22 {top+plot_h/2})" text-anchor="middle" font-size="14" font-family="Arial">{html.escape(y_label)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def write_plot_bundle(
    continuous_summary: list[dict[str, Any]],
    latency_summary: list[dict[str, Any]],
    precision_summary: list[dict[str, Any]],
    arrival_summary: list[dict[str, Any]],
    sensitivity: list[dict[str, Any]],
    headroom_rows: list[dict[str, Any]],
) -> list[str]:
    plot_dir = OUT / "plots"
    plot_dir.mkdir(exist_ok=True)
    strengths_x = list(STRENGTHS)
    def lookup_cont(scope: str, session: str | None, op: str | None, time_value: float, field: str) -> list[float | None]:
        result = []
        for s in STRENGTHS:
            row = next((r for r in continuous_summary if r["scope"] == scope
                        and r["session"] == (session or "") and r["operatingPoint"] == (op or "")
                        and r["strength"] == s and math.isclose(r["effectiveEvidenceTimeSeconds"], time_value, abs_tol=1e-8)), None)
            result.append(float(row[field]) if row is not None and row[field] is not None else None)
        return result
    svg_line(plot_dir/"01_strength_fused_top.svg","Context strength to fused top evidence at 0.90 s",
             strengths_x,[("All trials",lookup_cont("OVERALL",None,None,0.90,"meanFusedTopEvidence"))],
             "Context prior strength","Mean fused top evidence")
    svg_line(plot_dir/"02_strength_fused_margin.svg","Context strength to fused top-second margin at 0.90 s",
             strengths_x,[("All trials",lookup_cont("OVERALL",None,None,0.90,"meanFusedMargin"))],
             "Context prior strength","Mean fused margin")
    svg_line(plot_dir/"03_strength_fused_entropy.svg","Context strength to normalized entropy at 0.90 s",
             strengths_x,[("All trials",lookup_cont("OVERALL",None,None,0.90,"meanFusedNormalizedEntropy"))],
             "Context prior strength","Mean normalized entropy")
    op_rows = [r for r in continuous_summary if r["scope"] == "OPERATING_POINT" and math.isclose(r["effectiveEvidenceTimeSeconds"],0.90,abs_tol=1e-8)]
    matrix = [[next((float(r["meanFusedTopEvidence"]) for r in op_rows if r["operatingPoint"]==op and r["strength"]==s),None) for s in STRENGTHS] for op in OP_NAMES]
    svg_heatmap(plot_dir/"04_strength_by_op_heatmap.svg","Strength by EEG operating point: fused top at 0.90 s",
                [strength_label(s) for s in STRENGTHS],list(OP_NAMES),matrix,"Mean fused top")
    lines = []
    for branch in BRANCHES:
        for op in OP_NAMES:
            values = []
            for s in STRENGTHS:
                row = next((r for r in latency_summary if r["scope"]=="ALL_HELD_OUT" and r["branch"]==branch
                            and r["operatingPoint"]==op and float(r["requestedStrength"])==s),None)
                values.append(float(row["meanPairedGainAllTrialsSeconds"]) if row else None)
            lines.append((f"{branch.split('_')[0]} {op}",values))
    svg_line(plot_dir/"05_shared_threshold_gain.svg","Shared-threshold paired latency gain",
             strengths_x,lines,"Context prior strength","EEG-only minus Context stop (s)")
    p_x = list(SURFACE_STRENGTHS)
    p_matrix = [[
        float(np.mean([r["meanGainSeconds"] for r in precision_summary if r["precision"]==p and r["strength"]==s]))
        if any(r["precision"]==p and r["strength"]==s for r in precision_summary) else None
        for s in p_x] for p in PRECISIONS]
    svg_heatmap(plot_dir/"06_strength_precision_gain_heatmap.svg","Strength × assignment precision: mean latency gain",
                [strength_label(s) for s in p_x],[f"{p:.2f}" for p in PRECISIONS],p_matrix,"Mean paired gain (s)")
    w_matrix = [[
        float(np.mean([r["wrongEarlyStopN"]/max(1,r["N"]) for r in precision_summary if r["precision"]==p and r["strength"]==s]))
        if any(r["precision"]==p and r["strength"]==s for r in precision_summary) else None
        for s in p_x] for p in PRECISIONS]
    svg_heatmap(plot_dir/"07_strength_precision_wrongstop_heatmap.svg","Strength × assignment precision: wrong early-stop rate",
                [strength_label(s) for s in p_x],[f"{p:.2f}" for p in PRECISIONS],w_matrix,"Wrong early stops / trials")
    arrival_x = list(AVAILABILITY_TIMES)
    arrival_series = []
    for op in OP_NAMES:
        vals=[]
        for a in arrival_x:
            group=[r for r in arrival_summary if r["scope"]=="ALL_HELD_OUT" and r["operatingPoint"]==op and math.isclose(r["contextAvailableAtSeconds"],a,abs_tol=1e-8)]
            vals.append(float(np.mean([r["meanGainSeconds"] for r in group])) if group else None)
        arrival_series.append((op,vals))
    svg_line(plot_dir/"08_context_arrival_gain.svg","Context arrival time versus paired latency gain",
             arrival_x,arrival_series,"Context available at (s)","Mean gain (s)")
    scatter=[(float(r["headroomSeconds"]),float(r["meanGainAcrossStrengthsSeconds"]),str(r["session"])) for r in headroom_rows if r["recordType"]=="TRIAL"]
    svg_scatter(plot_dir/"09_eeg_only_headroom_gain.svg","EEG-only latency headroom versus Context gain",
                scatter,"EEG-only stop minus 0.50 s (s)","Mean paired gain across strengths (s)")
    sens_lines=[]
    for grid in ("0.10","0.04"):
        vals=[]
        for s in STRENGTHS:
            group=[r for r in sensitivity if r["branch"]==FROZEN_BRANCH and r["grid"]==grid
                   and math.isclose(float(r["stabilitySeconds"]),0.40,abs_tol=1e-8)
                   and float(r["strength"])==s]
            vals.append(float(np.mean([r["meanGainSeconds"] for r in group])) if group else None)
        sens_lines.append((f"grid {grid}",vals))
    svg_line(plot_dir/"10_grid_resolution_comparison.svg","0.10 s primary versus 0.04 s sensitivity",
             strengths_x,sens_lines,"Context prior strength","Mean held-out paired gain (s)")
    stable_lines=[]
    for stability in (0.20,0.40,0.60):
        vals=[]
        for s in STRENGTHS:
            group=[r for r in sensitivity if r["branch"]==FROZEN_BRANCH and r["grid"]=="0.10"
                   and math.isclose(float(r["stabilitySeconds"]),stability,abs_tol=1e-8)
                   and float(r["strength"])==s]
            vals.append(float(np.mean([r["meanGainSeconds"] for r in group])) if group else None)
        stable_lines.append((f"stability {stability:.2f}",vals))
    svg_line(plot_dir/"11_stability_sensitivity.svg","Stability-duration sensitivity without refitting",
             strengths_x,stable_lines,"Context prior strength","Mean held-out paired gain (s)")
    session_lines=[]
    for session in SESSIONS:
        vals=[]
        for s in STRENGTHS:
            group=[r for r in continuous_summary if r["scope"]=="SESSION" and r["session"]==session
                   and r["strength"]==s and math.isclose(r["effectiveEvidenceTimeSeconds"],0.90,abs_tol=1e-8)]
            vals.append(float(np.mean([r["meanFusedTopEvidence"] for r in group])) if group else None)
        session_lines.append((f"session {session}",vals))
    svg_line(plot_dir/"12_per_session_strength_curves.svg","Per-session continuous evidence curve at 0.90 s",
             strengths_x,session_lines,"Context prior strength","Mean fused top evidence")
    return sorted(p.name for p in plot_dir.glob("*.svg"))


def main() -> int:
    started = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    trials, manifest, folds, baseline_by, assignment_by, m24_rows, m24_unique = load_inputs()
    cohort = validate_cohort(trials, manifest)
    trial_by = {(t["session"], t["trialId"]): t for t in trials}

    # Verify this fold-based application of M23 frozen thresholds reproduces the held-out M23 baselines.
    baseline_fold_match_n = 0
    for held_out in SESSIONS:
        fold = folds[held_out]
        for trial in [t for t in trials if t["session"] == held_out]:
            for op in OP_NAMES:
                cfg = op_config(fold, op)
                got = raw_stop(trial, "0.10", float(cfg["topThreshold"]), float(cfg["marginThreshold"]), 0.50, STABILITY_PRIMARY)
                stored = baseline_by[(op, held_out, trial["trialId"])]
                if (not math.isclose(float(got["stopTimeSeconds"]), float(stored["stopTimeSeconds"]), abs_tol=1e-9)
                    or got["selectedClass"] != stored["selectedClass"]):
                    raise AssertionError(f"M23 held-out baseline mismatch for {held_out}/{op}/{trial['trialId']}")
                baseline_fold_match_n += 1

    # Freeze the continuous evidence outputs, then perform training-only shared-threshold selection.
    continuous_rows, continuous_summary = continuous_evidence(trials)
    authorized_rows, authorized_summary, authorized_replay_info = authorized_evidence(trials, folds, baseline_by, m24_unique)
    candidate_rows, selections = choose_thresholds(trials, folds, assignment_by)
    write_csv(OUT/"continuous_evidence_per_trial.csv", continuous_rows)
    write_csv(OUT/"continuous_evidence_summary.csv", continuous_summary)
    write_csv(OUT/"authorized_evidence_per_trial.csv", authorized_rows)
    write_csv(OUT/"authorized_evidence_summary.csv", authorized_summary)
    write_csv(OUT/"authorized_stop_replay.csv", authorized_replay_info["m23HelperMismatchN"] == 0 and [
        r for r in authorized_rows[:0]
    ] or [r for r in []]) if False else None
    # The per-trial stop results are materialized independently for a direct audit.
    stop_replay_rows = []
    m24_map = {(float(r["contextStrength"]), float(r["requestedPredictionPrecision"]),
                float(r["requestedCoverage"]), r["operatingPoint"], r["session"], r["trialId"]): r for r in m24_unique}
    for trial in trials:
        baseline_session = trial["session"]
        true_index = int(trial["trueClassIndex"])
        for op in OP_NAMES:
            cfg = op_config(folds[baseline_session], op)
            base = make_m23_baseline(baseline_by[(op, baseline_session, trial["trialId"])])
            for strength in STRENGTHS[1:]:
                res = m23.context_stop_trial(trial, base, float(cfg["topThreshold"]), float(cfg["marginThreshold"]),
                                             prior_vector(true_index, strength), True)
                anchor = m24_map.get((strength, 1.0, 1.0, op, baseline_session, trial["trialId"]))
                stop_replay_rows.append({
                    "session": baseline_session, "trialId": trial["trialId"], "operatingPoint": op,
                    "strength": strength, "strengthLabel": strength_label(strength),
                    "pairedBaselineStopSeconds": base["stopTimeSeconds"],
                    "M23HelperStopSeconds": res["stopTimeSeconds"],
                    "M23AuthorizedPointCount": res.get("authorizedPointCount", 0),
                    "M23ContextAppliedAtStop": res.get("contextAppliedAtStop", False),
                    "M24StoredStopSeconds": float(anchor["contextStopTimeSeconds"]) if anchor else None,
                    "M24StoredAuthorizedPointCount": int(anchor["contextAuthorizedPointCount"]) if anchor else None,
                    "M24ExactMatch": bool(anchor and math.isclose(float(anchor["contextStopTimeSeconds"]), float(res["stopTimeSeconds"]), abs_tol=1e-9)
                                           and int(anchor["contextAuthorizedPointCount"]) == int(res.get("authorizedPointCount", 0))),
                })
    if any(not r["M24ExactMatch"] for r in stop_replay_rows):
        raise AssertionError("M25 historical exact M23/M24 helper replays did not match")
    write_csv(OUT/"authorized_stop_replay.csv", stop_replay_rows)

    # Candidate rows and selection metadata are written before evaluating the held-out sessions.
    write_csv(OUT/"shared_threshold_candidate_grid.csv", candidate_rows)
    selection_json = {
        "recordType": "m25_shared_threshold_outer_fold_selection",
        "analysisBranches": list(BRANCHES),
        "candidateGrid": {"topThreshold": list(TOP_GRID), "marginThreshold": list(MARGIN_GRID),
                          "minimumEvidenceSeconds": list(MIN_EVIDENCE_GRID), "stabilitySeconds": STABILITY_PRIMARY},
        "strengthGrid": [strength_label(s) for s in STRENGTHS],
        "selectionUsesHeldOutSession": False,
        "folds": selections,
    }
    (OUT/"shared_threshold_outer_fold_selection.json").write_text(json.dumps(selection_json, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")

    latency_rows = evaluate_shared_latency(trials, folds, selections, assignment_by)
    latency_summary = summarize_latency(latency_rows)
    write_csv(OUT/"shared_threshold_latency_per_trial.csv", latency_rows)
    write_csv(OUT/"shared_threshold_latency_summary.csv", latency_summary)

    # Lower-precision quality surface: frozen assignment labels/hashes, no refitting.
    precision_trial_rows: list[dict[str, Any]] = []
    for held_out in SESSIONS:
        fold = folds[held_out]
        test_trials = [t for t in trials if t["session"] == held_out]
        for op in OP_NAMES:
            base_cfg = op_config(fold, op)
            selected = selections[f"{FROZEN_BRANCH}|{held_out}|{op}"]["selectedSharedContextParameters"]
            for trial in test_trials:
                base = raw_stop(trial, "0.10", float(base_cfg["topThreshold"]), float(base_cfg["marginThreshold"]), 0.50, STABILITY_PRIMARY)
                for precision in PRECISIONS:
                    assignment = assignment_for(assignment_by, precision, 1.0, trial)
                    active = to_bool(assignment["activeAssigned"])
                    correct_prediction = to_bool(assignment["contextPredictionCorrect"]) if active else False
                    context_top = CLASSES.index(assignment["simulatedContextTopClass"]) if active and assignment["simulatedContextTopClass"] else None
                    for strength in SURFACE_STRENGTHS:
                        fixed = {}
                        auth_points = 0
                        for at in (0.50, 0.90, 1.50):
                            point = exact_point(trial, "0.10", at)
                            raw_top, _, _, eeg = raw_top_margin(point)
                            auth = False
                            actual = list(eeg)
                            if active and context_top is not None:
                                prior = prior_vector(context_top, strength)
                                prior_top = max(range(3), key=lambda i: (prior[i], -i))
                                auth = prior_top == raw_top and prior[prior_top] >= 0.70 - 1e-12
                                if auth:
                                    actual = fuse(eeg, prior)
                                    auth_points += 1
                            met = vector_metrics(actual, int(trial["trueClassIndex"]))
                            fixed[f"{int(at*100):03d}"] = (met, auth)
                        if selected is None:
                            result = {"stop": base["stopTimeSeconds"], "selectedIndex": base["selectedClassIndex"],
                                      "contextApplied": False, "authorizedPointCount": 0, "wrongEarlyStop": False,
                                      "contextCausedError": False}
                            threshold = None
                        else:
                            threshold = selected
                            result = simulate_context(
                                trial, "0.10", base, strength, context_top, active, FROZEN_BRANCH,
                                float(selected["topThreshold"]), float(selected["marginThreshold"]),
                                float(selected["minimumEvidenceSeconds"]), STABILITY_PRIMARY, 0.0,
                            )
                        selected_index = int(result["selectedIndex"])
                        gain = max(0.0, float(base["stopTimeSeconds"]) - float(result["stop"]))
                        precision_trial_rows.append({
                            "heldOutSession": held_out, "session": trial["session"], "trialId": trial["trialId"],
                            "operatingPoint": op, "precision": precision, "coverage": 1.0,
                            "strength": strength, "strengthLabel": strength_label(strength),
                            "assignmentActive": active, "assignmentCorrect": correct_prediction,
                            "assignedContextTopClass": CLASSES[context_top] if context_top is not None else "",
                            "assignmentSha256": assignment["assignmentSha256"],
                            "authorizedTrialN": int(result.get("authorizedPointCount", 0)),
                            "authorizedPointRate": float(result.get("authorizedPointRate", 0.0)),
                            "meanFusedTopAt050": fixed["050"][0]["topEvidence"],
                            "meanFusedTopAt090": fixed["090"][0]["topEvidence"],
                            "meanFusedTopAt150": fixed["150"][0]["topEvidence"],
                            "meanFusedMarginAt050": fixed["050"][0]["margin"],
                            "meanFusedMarginAt090": fixed["090"][0]["margin"],
                            "meanFusedMarginAt150": fixed["150"][0]["margin"],
                            "authorizedAt050": fixed["050"][1], "authorizedAt090": fixed["090"][1], "authorizedAt150": fixed["150"][1],
                            "pairedBaselineStopSeconds": float(base["stopTimeSeconds"]),
                            "contextStopSeconds": float(result["stop"]), "gainSeconds": gain,
                            "contextAppliedAtStop": bool(result["contextApplied"]),
                            "baselineCorrect": bool(base["correct"]), "correct": selected_index == int(trial["trueClassIndex"]),
                            "wrongEarlyStop": bool(result.get("wrongEarlyStop", False)),
                            "contextCausedError": bool(result.get("contextCausedError", False)),
                            "selectedThresholdFromTargetAlignedTrainOnly": threshold is not None,
                            "m23AssignmentSeed": assignment["assignmentSeed"],
                        })
    precision_summary = summarize_scopes(precision_trial_rows)
    write_csv(OUT/"strength_precision_surface.csv", precision_summary)
    write_csv(OUT/"strength_precision_surface_per_trial.csv", precision_trial_rows)

    # Wrong-Context stress uses a separate deterministic assignment and never enters threshold selection.
    wrong_rows: list[dict[str, Any]] = []
    for held_out in SESSIONS:
        fold = folds[held_out]
        for op in OP_NAMES:
            base_cfg = op_config(fold, op)
            selected = selections[f"{FROZEN_BRANCH}|{held_out}|{op}"]["selectedSharedContextParameters"]
            for trial in [t for t in trials if t["session"] == held_out]:
                true_index = int(trial["trueClassIndex"])
                token = hashlib.sha256(f"m25-wrong-context-v1|{trial['session']}|{trial['trialId']}".encode("utf-8")).digest()
                wrong_top = (true_index + 1 + (int.from_bytes(token[:4], "big") % 2)) % 3
                base = raw_stop(trial, "0.10", float(base_cfg["topThreshold"]), float(base_cfg["marginThreshold"]), 0.50, STABILITY_PRIMARY)
                for strength in (0.70, 0.80, 0.90, 0.95, 0.99, 1.00):
                    agreement = []
                    reject = 0
                    first_agreement_point = None
                    for point in trial["pointsByGrid"]["0.10"]:
                        if float(point["effectiveEvidenceTimeSeconds"]) > float(base["stopTimeSeconds"]) + 1e-9:
                            break
                        raw_top, _, _, _ = raw_top_margin(point)
                        if wrong_top == raw_top:
                            agreement.append(point)
                            if first_agreement_point is None:
                                first_agreement_point = point
                        else:
                            reject += 1
                    if selected is None:
                        result = {"stop": base["stopTimeSeconds"], "selectedIndex": base["selectedClassIndex"],
                                  "contextApplied": False, "authorizedPointCount": 0, "wrongEarlyStop": False}
                    else:
                        result = simulate_context(
                            trial, "0.10", base, strength, wrong_top, True, FROZEN_BRANCH,
                            float(selected["topThreshold"]), float(selected["marginThreshold"]),
                            float(selected["minimumEvidenceSeconds"]), STABILITY_PRIMARY, 0.0,
                        )
                    wrong_early = bool(float(result["stop"]) < float(base["stopTimeSeconds"]) - 1e-9
                                       and int(result["selectedIndex"]) != true_index)
                    point = result.get("selectedStepIndex")
                    failure_point = next((p for p in trial["pointsByGrid"]["0.10"] if int(p["stepIndex"]) == point), None) if wrong_early else first_agreement_point
                    if failure_point is None:
                        feature = {"rawMargin": None, "normalizedTopProbability": None, "normalizedEntropy": None,
                                   "flipsWithin040": None, "sameTopDurationSeconds": None}
                    else:
                        feature = stable_point_features(trial, float(failure_point["effectiveEvidenceTimeSeconds"]))
                    wrong_rows.append({
                        "recordType": "TRIAL",
                        "heldOutSession": held_out, "session": trial["session"], "trialId": trial["trialId"],
                        "operatingPoint": op, "strength": strength, "strengthLabel": strength_label(strength),
                        "trueClass": CLASSES[true_index], "wrongContextTopClass": CLASSES[wrong_top],
                        "wrongContextHashRule": "m25-wrong-context-v1 SHA256(session|trialId), offset 1 or 2 modulo 3",
                        "thresholdSelectionUsedWrongContext": False,
                        "examinedPointsBeforeBaseline": reject + len(agreement),
                        "rawTopDisagreementRejectedPoints": reject,
                        "wrongContextAgreedWithTemporarilyWrongRawTopPoints": len(agreement),
                        "hadAnyAgreement": bool(agreement),
                        "earliestAgreementSeconds": min((float(p["effectiveEvidenceTimeSeconds"]) for p in agreement), default=None),
                        "wrongEarlyStop": wrong_early,
                        "earliestFailureTimeSeconds": float(result["stop"]) if wrong_early else None,
                        "pairedBaselineStopSeconds": float(base["stopTimeSeconds"]),
                        "contextStopSeconds": float(result["stop"]),
                        "contextAuthorizedPointCount": int(result.get("authorizedPointCount", 0)),
                        "rawMarginAtFailureOrFirstAgreement": feature["rawMargin"],
                        "normalizedTopProbabilityAtFailureOrFirstAgreement": feature["normalizedTopProbability"],
                        "normalizedEntropyAtFailureOrFirstAgreement": feature["normalizedEntropy"],
                        "rawTopFlipsWithin040AtFailureOrFirstAgreement": feature["flipsWithin040"],
                        "sameTopDurationSecondsAtFailureOrFirstAgreement": feature["sameTopDurationSeconds"],
                    })
    # Descriptive feature/failure associations are appended as explicitly separate summary rows.
    for op in OP_NAMES:
        for strength in (0.70, 0.80, 0.90, 0.95, 0.99, 1.00):
            group = [r for r in wrong_rows if r["recordType"] == "TRIAL" and r["operatingPoint"] == op and r["strength"] == strength]
            usable = [r for r in group if r["rawMarginAtFailureOrFirstAgreement"] is not None]
            summary = {
                "recordType": "SUMMARY", "heldOutSession": "ALL", "session": "ALL", "trialId": "",
                "operatingPoint": op, "strength": strength, "strengthLabel": strength_label(strength),
                "trueClass": "", "wrongContextTopClass": "", "wrongContextHashRule": "",
                "thresholdSelectionUsedWrongContext": False,
                "examinedPointsBeforeBaseline": sum(int(r["examinedPointsBeforeBaseline"]) for r in group),
                "rawTopDisagreementRejectedPoints": sum(int(r["rawTopDisagreementRejectedPoints"]) for r in group),
                "wrongContextAgreedWithTemporarilyWrongRawTopPoints": sum(int(r["wrongContextAgreedWithTemporarilyWrongRawTopPoints"]) for r in group),
                "hadAnyAgreement": sum(bool(r["hadAnyAgreement"]) for r in group),
                "earliestAgreementSeconds": None,
                "wrongEarlyStop": sum(bool(r["wrongEarlyStop"]) for r in group),
                "earliestFailureTimeSeconds": min((float(r["earliestFailureTimeSeconds"]) for r in group if r["earliestFailureTimeSeconds"] is not None), default=None),
                "pairedBaselineStopSeconds": None, "contextStopSeconds": None, "contextAuthorizedPointCount": None,
                "rawMarginAtFailureOrFirstAgreement": None, "normalizedTopProbabilityAtFailureOrFirstAgreement": None,
                "normalizedEntropyAtFailureOrFirstAgreement": None,
                "rawTopFlipsWithin040AtFailureOrFirstAgreement": None,
                "sameTopDurationSecondsAtFailureOrFirstAgreement": None,
                "spearmanMarginWithWrongStop": spearman([float(r["rawMarginAtFailureOrFirstAgreement"]) for r in usable],
                                                        [float(bool(r["wrongEarlyStop"])) for r in usable]),
                "spearmanEntropyWithWrongStop": spearman([float(r["normalizedEntropyAtFailureOrFirstAgreement"]) for r in usable],
                                                         [float(bool(r["wrongEarlyStop"])) for r in usable]),
                "spearmanFlipsWithWrongStop": spearman([float(r["rawTopFlipsWithin040AtFailureOrFirstAgreement"]) for r in usable],
                                                       [float(bool(r["wrongEarlyStop"])) for r in usable]),
                "spearmanSameTopDurationWithWrongStop": spearman([float(r["sameTopDurationSecondsAtFailureOrFirstAgreement"]) for r in usable],
                                                                 [float(bool(r["wrongEarlyStop"])) for r in usable]),
            }
            wrong_rows.append(summary)
    write_csv(OUT/"wrong_context_safety.csv", wrong_rows)

    # Context availability timing uses the same frozen target-aligned threshold; no timing-specific refits.
    availability_trial_rows: list[dict[str, Any]] = []
    for held_out in SESSIONS:
        fold = folds[held_out]
        for op in OP_NAMES:
            base_cfg = op_config(fold, op)
            selected = selections[f"{FROZEN_BRANCH}|{held_out}|{op}"]["selectedSharedContextParameters"]
            for trial in [t for t in trials if t["session"] == held_out]:
                true_index = int(trial["trueClassIndex"])
                base = raw_stop(trial, "0.10", float(base_cfg["topThreshold"]), float(base_cfg["marginThreshold"]), 0.50, STABILITY_PRIMARY)
                for strength in STRENGTHS:
                    for available_at in AVAILABILITY_TIMES:
                        if selected is None:
                            result = {"stop":base["stopTimeSeconds"],"selectedIndex":base["selectedClassIndex"],
                                      "contextApplied":False,"authorizedPointCount":0,"wrongEarlyStop":False}
                            threshold = None
                        else:
                            threshold = selected
                            result = simulate_context(trial,"0.10",base,strength,true_index,True,FROZEN_BRANCH,
                                float(selected["topThreshold"]),float(selected["marginThreshold"]),
                                float(selected["minimumEvidenceSeconds"]),STABILITY_PRIMARY,available_at)
                        availability_trial_rows.append({
                            "recordType":"TRIAL","heldOutSession":held_out,"session":trial["session"],
                            "trialId":trial["trialId"],"operatingPoint":op,"strength":strength,
                            "strengthLabel":strength_label(strength),"contextAvailableAtSeconds":available_at,
                            "selectedThresholdFromPrimaryTrainOnly":threshold is not None,
                            "selectedTopThreshold":float(threshold["topThreshold"]) if threshold is not None else None,
                            "selectedMarginThreshold":float(threshold["marginThreshold"]) if threshold is not None else None,
                            "selectedMinimumEvidenceSeconds":float(threshold["minimumEvidenceSeconds"]) if threshold is not None else None,
                            "pairedBaselineStopSeconds":float(base["stopTimeSeconds"]),
                            "contextStopSeconds":float(result["stop"]),
                            "gainSeconds":max(0.0,float(base["stopTimeSeconds"])-float(result["stop"])),
                            "authorizedPointCount":int(result.get("authorizedPointCount",0)),
                            "contextAppliedAtStop":bool(result.get("contextApplied",False)),
                            "correct":int(result["selectedIndex"])==true_index,
                        })
    write_csv(OUT/"context_availability_timing_per_trial.csv", availability_trial_rows)
    availability_summary: list[dict[str, Any]] = []
    for scope in ("ALL_HELD_OUT","SESSION"):
        for op in OP_NAMES:
            for strength in STRENGTHS:
                for available_at in AVAILABILITY_TIMES:
                    if scope=="ALL_HELD_OUT":
                        group=[r for r in availability_trial_rows if r["operatingPoint"]==op and r["strength"]==strength
                               and math.isclose(r["contextAvailableAtSeconds"],available_at,abs_tol=1e-8)]
                    else:
                        group=[r for r in availability_trial_rows if r["operatingPoint"]==op and r["strength"]==strength
                               and math.isclose(r["contextAvailableAtSeconds"],available_at,abs_tol=1e-8)]
                    # Session rows are emitted separately below to keep the scope label explicit.
                    if scope=="SESSION":
                        for session in SESSIONS:
                            sub=[r for r in group if r["heldOutSession"]==session]
                            gains=[float(r["gainSeconds"]) for r in sub]
                            base_mean=float(np.mean([r["pairedBaselineStopSeconds"] for r in sub])) if sub else None
                            availability_summary.append({
                                "scope":"SESSION","session":session,"operatingPoint":op,"strength":strength,
                                "strengthLabel":strength_label(strength),"contextAvailableAtSeconds":available_at,
                                "N":len(sub),"meanGainSeconds":float(np.mean(gains)) if gains else None,
                                "medianGainSeconds":float(np.median(gains)) if gains else None,
                                "gainBootstrap95Lower":bootstrap_ci(gains,f"availability|{session}|{op}|{strength}|{available_at}")[0],
                                "gainBootstrap95Upper":bootstrap_ci(gains,f"availability|{session}|{op}|{strength}|{available_at}")[1],
                                "acceleratedFraction":sum(x>1e-9 for x in gains)/len(gains) if gains else None,
                                "meanBaselineStopSeconds":base_mean,
                                "meanContextStopSeconds":float(np.mean([r["contextStopSeconds"] for r in sub])) if sub else None,
                                "authorizedTrialRate":sum(int(r["authorizedPointCount"])>0 for r in sub)/len(sub) if sub else None,
                            })
                    else:
                        gains=[float(r["gainSeconds"]) for r in group]
                        availability_summary.append({
                            "scope":"ALL_HELD_OUT","session":"","operatingPoint":op,"strength":strength,
                            "strengthLabel":strength_label(strength),"contextAvailableAtSeconds":available_at,
                            "N":len(group),"meanGainSeconds":float(np.mean(gains)) if gains else None,
                            "medianGainSeconds":float(np.median(gains)) if gains else None,
                            "gainBootstrap95Lower":bootstrap_ci(gains,f"availability|ALL|{op}|{strength}|{available_at}")[0],
                            "gainBootstrap95Upper":bootstrap_ci(gains,f"availability|ALL|{op}|{strength}|{available_at}")[1],
                            "acceleratedFraction":sum(x>1e-9 for x in gains)/len(gains) if gains else None,
                            "meanBaselineStopSeconds":float(np.mean([r["pairedBaselineStopSeconds"] for r in group])),
                            "meanContextStopSeconds":float(np.mean([r["contextStopSeconds"] for r in group])),
                            "authorizedTrialRate":sum(int(r["authorizedPointCount"])>0 for r in group)/len(group),
                        })
    write_csv(OUT/"context_availability_timing.csv", availability_summary)

    # Descriptive headroom and ambiguity: only features observed at/before fixed early times.
    latency_frozen={(r["operatingPoint"],r["session"],r["trialId"],float(r["requestedStrength"])):r
                    for r in latency_rows if r["branch"]==FROZEN_BRANCH}
    headroom_rows: list[dict[str, Any]] = []
    for held_out in SESSIONS:
        fold=folds[held_out]
        for op in OP_NAMES:
            cfg=op_config(fold,op)
            for trial in [t for t in trials if t["session"]==held_out]:
                base=raw_stop(trial,"0.10",float(cfg["topThreshold"]),float(cfg["marginThreshold"]),0.50,STABILITY_PRIMARY)
                f05=stable_point_features(trial,0.50)
                f07=stable_point_features(trial,0.70)
                f09=stable_point_features(trial,0.90)
                selected=selections[f"{FROZEN_BRANCH}|{held_out}|{op}"]["selectedSharedContextParameters"]
                gains=[float(latency_frozen[(op,held_out,trial["trialId"],s)]["pairedGainSeconds"]) for s in STRENGTHS]
                gain_by={s:float(latency_frozen[(op,held_out,trial["trialId"],s)]["pairedGainSeconds"]) for s in STRENGTHS}
                top_threshold=float(selected["topThreshold"]) if selected else None
                margin_threshold=float(selected["marginThreshold"]) if selected else None
                point05=exact_point(trial,"0.10",0.50)
                point07=exact_point(trial,"0.10",0.70)
                slope=(float(point07["rawAbsoluteMargin"])-float(point05["rawAbsoluteMargin"])) / 0.20
                headroom_rows.append({
                    "recordType":"TRIAL","heldOutSession":held_out,"session":held_out,"trialId":trial["trialId"],
                    "operatingPoint":op,"baselineStopSeconds":base["stopTimeSeconds"],
                    "headroomSeconds":float(base["stopTimeSeconds"])-0.50,
                    "rawMarginAt050":f05["rawMargin"],"normalizedMarginAt050":f05["rawMargin"],
                    "normalizedTopProbabilityAt050":f05["normalizedTopProbability"],
                    "entropyAt050":f05["normalizedEntropy"],
                    "rawMarginAt070":f07["rawMargin"],"normalizedTopProbabilityAt070":f07["normalizedTopProbability"],
                    "entropyAt070":f07["normalizedEntropy"],
                    "rawMarginAt090":f09["rawMargin"],"normalizedTopProbabilityAt090":f09["normalizedTopProbability"],
                    "entropyAt090":f09["normalizedEntropy"],
                    "topFlipsWithin040At090":f09["flipsWithin040"],
                    "sameTopDurationAt090":f09["sameTopDurationSeconds"],
                    "rawMarginSlope050To070PerSecond":slope,
                    "distanceToSelectedTopThresholdAt050":f05["normalizedTopProbability"]-top_threshold if top_threshold is not None else None,
                    "distanceToSelectedMarginThresholdAt050":f05["rawMargin"]-margin_threshold if margin_threshold is not None else None,
                    "meanGainAcrossStrengthsSeconds":float(np.mean(gains)),
                    "gainStrength033":gain_by[1.0/3.0],"gainStrength070":gain_by[0.70],
                    "gainStrength095":gain_by[0.95],"gainStrength099":gain_by[0.99],
                    "gainStrength100":gain_by[1.0],
                    "usesOnlyFeaturesThrough090":True,
                })
    metric_fields=[
        ("headroomSeconds","EEG-only headroom"),
        ("rawMarginAt050","raw margin at 0.50 s"),
        ("normalizedTopProbabilityAt050","normalized top evidence at 0.50 s"),
        ("entropyAt050","normalized entropy at 0.50 s"),
        ("topFlipsWithin040At090","top flips in prior 0.40 s at 0.90 s"),
        ("sameTopDurationAt090","same-top duration at 0.90 s"),
        ("rawMarginSlope050To070PerSecond","raw-margin slope 0.50 to 0.70 s"),
        ("distanceToSelectedMarginThresholdAt050","distance to frozen selected margin threshold at 0.50 s"),
    ]
    for op in OP_NAMES:
        group=[r for r in headroom_rows if r["recordType"]=="TRIAL" and r["operatingPoint"]==op]
        gains=[float(r["meanGainAcrossStrengthsSeconds"]) for r in group]
        for field,label in metric_fields:
            pairs=[r for r in group if r[field] is not None]
            headroom_rows.append({
                "recordType":"CORRELATION_SUMMARY","heldOutSession":"ALL","session":"ALL","trialId":"",
                "operatingPoint":op,"feature":field,"featureLabel":label,"N":len(pairs),
                "spearmanWithMeanGainAcrossStrengths":spearman([float(r[field]) for r in pairs],
                                                                [float(r["meanGainAcrossStrengthsSeconds"]) for r in pairs]),
                "usesOnlyFeaturesThrough090":True,
            })
        values=sorted(group,key=lambda r:float(r["headroomSeconds"]))
        for q in range(4):
            subset=values[round(q*len(values)/4):round((q+1)*len(values)/4)]
            headroom_rows.append({
                "recordType":"HEADROOM_QUARTILE","heldOutSession":"ALL","session":"ALL","trialId":"",
                "operatingPoint":op,"feature":"headroomSeconds","featureLabel":"EEG-only headroom",
                "quartile":f"Q{q+1}","N":len(subset),
                "meanHeadroomSeconds":float(np.mean([r["headroomSeconds"] for r in subset])) if subset else None,
                "meanGainAcrossStrengthsSeconds":float(np.mean([r["meanGainAcrossStrengthsSeconds"] for r in subset])) if subset else None,
                "usesOnlyFeaturesThrough090":True,
            })
    write_csv(OUT/"headroom_ambiguity_analysis.csv", headroom_rows)

    # Resolution and stability sensitivities: same selected threshold, held-out only, no refitting.
    sensitivity_rows: list[dict[str, Any]] = []
    for held_out in SESSIONS:
        fold=folds[held_out]
        for op in OP_NAMES:
            base_cfg=op_config(fold,op)
            for branch in BRANCHES:
                selected=selections[f"{branch}|{held_out}|{op}"]["selectedSharedContextParameters"]
                for grid in ("0.10","0.04"):
                    for stability in (0.20,0.40,0.60):
                        per_trial: dict[tuple[str,str], list[dict[str, Any]]] = defaultdict(list)
                        for trial in [t for t in trials if t["session"]==held_out]:
                            base=raw_stop(trial,grid,float(base_cfg["topThreshold"]),float(base_cfg["marginThreshold"]),0.50,stability)
                            for strength in STRENGTHS:
                                if selected is None:
                                    res={"stop":base["stopTimeSeconds"],"selectedIndex":base["selectedClassIndex"],
                                         "contextApplied":False,"authorizedPointCount":0,"wrongEarlyStop":False}
                                    used=None
                                else:
                                    used=selected
                                    res=simulate_context(trial,grid,base,strength,int(trial["trueClassIndex"]),True,branch,
                                        float(selected["topThreshold"]),float(selected["marginThreshold"]),
                                        float(selected["minimumEvidenceSeconds"]),stability,0.0)
                                per_trial[(trial["session"],trial["trialId"])].append({
                                    "strength":strength,"base":float(base["stopTimeSeconds"]),
                                    "stop":float(res["stop"]),"gain":max(0.0,float(base["stopTimeSeconds"])-float(res["stop"])),
                                    "authorized":int(res.get("authorizedPointCount",0))>0,
                                    "contextApplied":bool(res.get("contextApplied",False)),
                                    "selectedThreshold":used is not None,
                                })
                        separation=[len({round(r["stop"],8) for r in vals})>1 for vals in per_trial.values()]
                        within_ranges=[max(r["stop"] for r in vals)-min(r["stop"] for r in vals) for vals in per_trial.values()]
                        by_strength={s:[] for s in STRENGTHS}
                        for vals in per_trial.values():
                            for r in vals: by_strength[r["strength"]].append(r)
                        for strength,vals in by_strength.items():
                            gains=[r["gain"] for r in vals]
                            ci=bootstrap_ci(gains,f"sensitivity|{held_out}|{op}|{branch}|{grid}|{stability}|{strength}")
                            sensitivity_rows.append({
                                "heldOutSession":held_out,"trainingSessions":"|".join(fold["trainingSessions"]),
                                "operatingPoint":op,"branch":branch,"grid":grid,"stabilitySeconds":stability,
                                "strength":strength,"strengthLabel":strength_label(strength),"N":len(vals),
                                "meanBaselineStopSeconds":float(np.mean([r["base"] for r in vals])),
                                "meanContextStopSeconds":float(np.mean([r["stop"] for r in vals])),
                                "meanGainSeconds":float(np.mean(gains)),"gainBootstrap95Lower":ci[0],"gainBootstrap95Upper":ci[1],
                                "acceleratedFraction":sum(x>1e-9 for x in gains)/len(gains),
                                "authorizedTrialRate":sum(r["authorized"] for r in vals)/len(vals),
                                "contextAppliedTrialRate":sum(r["contextApplied"] for r in vals)/len(vals),
                                "trialsWithAnyStrengthSeparation":sum(separation),
                                "strengthSeparationTrialFraction":sum(separation)/len(separation),
                                "meanWithinTrialStrengthStopRangeSeconds":float(np.mean(within_ranges)),
                                "thresholdRefitForSensitivity":False,
                                "selectedPrimaryThreshold":all(r["selectedThreshold"] for r in vals),
                            })
    write_csv(OUT/"resolution_stability_sensitivity.csv", sensitivity_rows)

    # Aggregate all-trial paired curves by session/OP and compute strength separation at trial level.
    sep_records=[]
    for branch in BRANCHES:
        for op in OP_NAMES:
            by_trial: dict[tuple[str,str], list[float]] = defaultdict(list)
            for r in latency_rows:
                if r["branch"]==branch and r["operatingPoint"]==op:
                    by_trial[(r["session"],r["trialId"])].append(float(r["contextStopSeconds"]))
            separated=sum(len({round(x,8) for x in v})>1 for v in by_trial.values())
            sep_records.append({"branch":branch,"operatingPoint":op,"N":len(by_trial),
                                "strengthSeparatedTrialN":separated,
                                "strengthSeparatedTrialFraction":separated/len(by_trial)})

    # Find M24 primary stop saturation mechanism through pre-registered grid and stability sensitivity.
    plot_names=write_plot_bundle(continuous_summary,latency_summary,precision_summary,
                                 availability_summary,sensitivity_rows,headroom_rows)

    # Final report summarizes result values after every branch above has completed.
    cont090=[r for r in continuous_summary if r["scope"]=="OVERALL" and math.isclose(r["effectiveEvidenceTimeSeconds"],0.90,abs_tol=1e-8)]
    top_range=max(r["meanFusedTopEvidence"] for r in cont090)-min(r["meanFusedTopEvidence"] for r in cont090)
    margin_range=max(r["meanFusedMargin"] for r in cont090)-min(r["meanFusedMargin"] for r in cont090)
    entropy_range=max(r["meanFusedNormalizedEntropy"] for r in cont090)-min(r["meanFusedNormalizedEntropy"] for r in cont090)
    true_range=max(r["meanFusedTrueClassEvidence"] for r in cont090)-min(r["meanFusedTrueClassEvidence"] for r in cont090)
    frozen_all=[r for r in latency_summary if r["scope"]=="ALL_HELD_OUT" and r["branch"]==FROZEN_BRANCH]
    decoupled_all=[r for r in latency_summary if r["scope"]=="ALL_HELD_OUT" and r["branch"]==DECOUPLED_BRANCH]
    frozen_differences=sum(x["strengthSeparatedTrialN"] for x in sep_records if x["branch"]==FROZEN_BRANCH)
    precision_overall={}
    for p in PRECISIONS:
        g=[r for r in precision_summary if r["precision"]==p]
        precision_overall[f"{p:.2f}"]={
            "meanGainSeconds":float(np.mean([r["meanGainSeconds"] for r in g])),
            "wrongEarlyStops":sum(int(r["wrongEarlyStopN"]) for r in g),
            "contextCausedErrors":sum(int(r["contextCausedErrorN"]) for r in g),
            "meanRawAssignmentPrecision":float(np.mean([r["rawAssignmentPrecision"] for r in g if r["rawAssignmentPrecision"] is not None])),
            "meanAuthorizedPrecision":float(np.mean([r["authorizedPrecision"] for r in g if r["authorizedPrecision"] is not None])),
        }
    arrival_overall=[]
    for a in AVAILABILITY_TIMES:
        g=[r for r in availability_summary if r["scope"]=="ALL_HELD_OUT" and math.isclose(r["contextAvailableAtSeconds"],a,abs_tol=1e-8)]
        arrival_overall.append({"availableAtSeconds":a,"meanGainSeconds":float(np.mean([r["meanGainSeconds"] for r in g]))})
    wrong_summary=[r for r in wrong_rows if r["recordType"]=="SUMMARY"]
    wrong_events=sum(int(r["wrongEarlyStop"]) for r in wrong_summary)
    assignment_reuse_pass = all(
        r["assignmentSha256"] == assignment_by[(float(r["precision"]), 1.0, r["session"], r["trialId"])]["assignmentSha256"]
        and int(float(r["m23AssignmentSeed"])) == int(float(assignment_by[(float(r["precision"]), 1.0, r["session"], r["trialId"])]["assignmentSeed"]))
        for r in precision_trial_rows
    )
    availability_threshold_pass = True
    for row in availability_trial_rows:
        selected = selections[f"{FROZEN_BRANCH}|{row['heldOutSession']}|{row['operatingPoint']}"]["selectedSharedContextParameters"]
        if bool(row["selectedThresholdFromPrimaryTrainOnly"]) != (selected is not None):
            availability_threshold_pass = False
            break
        if selected is not None and not all((
            math.isclose(float(row["selectedTopThreshold"]), float(selected["topThreshold"]), abs_tol=1e-12),
            math.isclose(float(row["selectedMarginThreshold"]), float(selected["marginThreshold"]), abs_tol=1e-12),
            math.isclose(float(row["selectedMinimumEvidenceSeconds"]), float(selected["minimumEvidenceSeconds"]), abs_tol=1e-12),
        )):
            availability_threshold_pass = False
            break
    expected_availability_n = {"A": 30, "B1": 29, "B2": 29}
    availability_summary_counts_pass = all(
        int(row["N"]) == (88 if row["scope"] == "ALL_HELD_OUT" else expected_availability_n[row["session"]])
        for row in availability_summary
    )
    sensitivity_primary=[r for r in sensitivity_rows if r["branch"]==FROZEN_BRANCH and r["grid"]=="0.10"
                         and math.isclose(r["stabilitySeconds"],0.40,abs_tol=1e-8)]
    sens_separated=max((float(r["strengthSeparationTrialFraction"]) for r in sensitivity_primary),default=0.0)
    report_lines=[
        "# M25 Context Evidence and Shared-Threshold Latency Study",
        "",
        "## Executive result",
        "",
        "This is an offline replay on the frozen M21 250 Hz feature cache. The 88 formal trials remain A=30, B1=29, B2=29. B1 trial-011 and exploratory B2 trial-023 remain excluded. No raw EEG waveform was decoded or modified; ND8/COM11/Quest were not accessed for Task 1.",
        "",
        "The pure target-aligned evidence branch shows strength-dependent numeric evidence at the same cached EEG timepoint. At 0.90 s the across-strength ranges in mean fused top, margin, normalized entropy and true-class evidence are respectively "
        f"{top_range:.4f}, {margin_range:.4f}, {entropy_range:.4f}, and {true_range:.4f}. Strengths below 0.70 are labeled COUNTERFACTUAL_FUSION_DIAGNOSTIC_ONLY and are never counted as authorized stopping behavior.",
        "",
        "The latency question is reported independently. M25 selected one Context threshold configuration per LOSO fold, operating point and mechanism branch from training sessions only; the same selected parameters were applied to every strength in the held-out session. See the tables and machine-readable selection audit for the exact configurations and held-out paired gains.",
        "",
        "## Provenance and frozen decisions",
        "",
        f"- Feature cache: {manifest['featureProvenance']['sourceArtifact']} SHA-256 {manifest['featureProvenance']['cachedFeatureTableSha256']}.",
        f"- Frozen M23 operating-point parameter SHA-256: {manifest['frozenOperatingPoints']['sha256']}. The fold's M23 EEG-only top/margin parameters are reused as fixed values; training and held-out trials within a fold use the same parameters. Held-out raw baselines reproduce the corresponding stored M23 baseline rows ({baseline_fold_match_n}/264 OP-trial checks).",
        "- No EEG-only parameter was recalibrated using Context outcomes. Candidate selection uses no held-out session.",
        "- Primary continuous strengths: 1/3, .50, .60, .70, .80, .90, .95, .98, .99, 1.00; fixed times .50/.70/.90/1.10/1.30/1.50 s; complete .10 s trajectory retained.",
        "",
        "## 1. Direct continuous evidence response",
        "",
        f"At 0.90 s, the strength grid spans {top_range:.4f} in fused top evidence, {margin_range:.4f} in fused top-second margin, {entropy_range:.4f} in normalized entropy, and {true_range:.4f} in true-class evidence. This is direct numerical evidence propagation under Context top=true target, precision=1.00 and coverage=1.00; it is not a deployed predictor or stopping policy.",
        "",
        "The 1/3 prior is uniform and leaves the EEG evidence unchanged. The one-hot endpoint is the exact top-class posterior. The one-hot change fraction is the L1 movement from normalized EEG toward the one-hot posterior divided by the full L1 distance to that endpoint. Curves are also reported per session and per frozen EEG operating point.",
        "",
        "## 2. Historical authorized response",
        "",
        f"The exact M23 helper replay covered {authorized_replay_info['historicalHelperReplayRows']} trial × OP × strength combinations and compared {authorized_replay_info['m24ComparisonRows']} matching M24 p=1/c=1 cells. M23 and M24 mismatch counts were both zero. This branch authorizes only when Context top agrees with current raw EEG top and prior top mass is at least .70; the per-point minimum/top/margin gates and .40 s stability are recorded separately. Unauthorized evidence is exact EEG-only fallback.",
        "",
        "## 3. Shared-threshold LOSO latency",
        "",
        "Threshold selection uses the corresponding M23 fold parameters, the compact preregistered candidate grid, all ten strengths, and training trials only. It rejects any candidate with lower training accuracy than the paired frozen baseline, any target-aligned wrong early stop, or any delay. The optimization and tie-break order are frozen in M25_EXPERIMENT_SPEC.md. The selected gate and per-fold parameters are recorded in shared_threshold_outer_fold_selection.json.",
        "",
        f"Across the frozen-gate test set, held-out strength-separated trial count summed over FAST/MEDIUM/CONSERVATIVE is {frozen_differences}; maximum per-fold/OP strength-separated fraction under primary .10/.40 is {sens_separated:.3f}. These are trial-level descriptive replay results, not population estimates.",
        "",
        "## 4. Precision and risk surface",
        "",
        "The precision surface reuses M23 seed-230929 assignment rows at coverage 1.00. It reports realized assignment precision, authorized precision, fixed-time fused evidence, latency gain, accuracy and wrong-stop outcomes. This is one frozen deterministic assignment realization, so observed risk must not be generalized as a population safety rate.",
    ]
    for p in PRECISIONS:
        info=precision_overall[f"{p:.2f}"]
        report_lines.append(f"- Requested precision {p:.2f}: mean gain {info['meanGainSeconds']:.3f} s; wrong early stops {info['wrongEarlyStops']}; Context-caused errors {info['contextCausedErrors']}; mean realized assignment precision {info['meanRawAssignmentPrecision']:.3f}; mean authorized precision {info['meanAuthorizedPrecision']:.3f}.")
    report_lines += [
        "",
        "## 5. Wrong-Context safety stress",
        "",
        f"The separate deterministic wrong-top stress was excluded from threshold selection. It recorded {wrong_events} wrong early-stop events in the held-out evaluation. CSV rows identify raw-top disagreements/rejections, points where wrong Context coincided with temporarily wrong EEG top, earliest failure time, raw margin, entropy, recent top flips and same-top duration. These results characterize this synthetic stress rule only.",
        "",
        "## 6. Context arrival timing",
        "",
        "Context availability was delayed to .0/.2/.4/.6/.8 s without changing content or thresholds. The mean held-out gains are:",
        "",
    ]
    for item in arrival_overall:
        report_lines.append(f"- available at {item['availableAtSeconds']:.1f} s: {item['meanGainSeconds']:.3f} s.")
    report_lines += [
        "",
        "## 7. Grid, stability and descriptive headroom",
        "",
        "The same selected thresholds were evaluated on cached .10 s and .04 s grids and .20/.40/.60 s stability windows without refitting. The sensitivity CSV includes mean stop/gain and per-trial strength separation. The headroom table uses only evidence available by 0.90 s, reports descriptive Spearman associations and quartile summaries, and is not used to select a policy.",
        "",
        "## Interpretation and scientific limits",
        "",
        "- A numeric strength response exists in the fused evidence, but a latency response is an empirical outcome of the shared gates and may remain saturated at the discrete stop-time level.",
        "- FROZEN_GATE_SHARED_THRESHOLD retains the historical .70 authorization boundary. NONDEPLOYABLE_DECOUPLED_STRENGTH_GATE is a mechanism-only sensitivity, not production behavior.",
        "- Target-aligned, deterministic Context is not a deployable semantic predictor. Lower-precision and wrong-Context simulations are controlled risk overlays.",
        "- The unit is the trial, but only three acquisition sessions are present. Trial bootstrap intervals do not establish population or cross-subject generalization.",
        "- Software grid time is effective EEG evidence time in cached features, not physical optical onset, exact hardware timing, online performance or Quest display timing.",
        "",
        "## Outputs and validation",
        "",
        f"The complete required artifact set and {len(plot_names)} SVG plots are listed in final_validation.json. Input/source fingerprints and M23/M24 helper agreement are checked. Final validation status: PASS only if all software invariants passed.",
        "",
    ]
    (OUT/"M25_FINAL_REPORT.md").write_text("\n".join(report_lines),encoding="utf-8")

    # Invariant validation is last and records the complete artifact inventory.
    expected = [
        "INPUT_MANIFEST.json","M25_EXPERIMENT_SPEC.md","continuous_evidence_per_trial.csv",
        "continuous_evidence_summary.csv","authorized_evidence_per_trial.csv","authorized_evidence_summary.csv",
        "authorized_stop_replay.csv","shared_threshold_candidate_grid.csv",
        "shared_threshold_outer_fold_selection.json","shared_threshold_latency_per_trial.csv",
        "shared_threshold_latency_summary.csv","strength_precision_surface.csv","strength_precision_surface_per_trial.csv",
        "wrong_context_safety.csv","context_availability_timing.csv","context_availability_timing_per_trial.csv",
        "headroom_ambiguity_analysis.csv","resolution_stability_sensitivity.csv",
        "M25_FINAL_REPORT.md",
    ]
    missing=[name for name in expected if not (OUT/name).is_file()]
    required_plot_names=[
        "01_strength_fused_top.svg","02_strength_fused_margin.svg","03_strength_fused_entropy.svg",
        "04_strength_by_op_heatmap.svg","05_shared_threshold_gain.svg","06_strength_precision_gain_heatmap.svg",
        "07_strength_precision_wrongstop_heatmap.svg","08_context_arrival_gain.svg","09_eeg_only_headroom_gain.svg",
        "10_grid_resolution_comparison.svg","11_stability_sensitivity.svg","12_per_session_strength_curves.svg",
    ]
    checks={
        "cohort88AndSessions":cohort["checks"],
        "m23HeldOutBaselineReproduced":baseline_fold_match_n==264,
        "continuousEvidenceExpectedRows":len(continuous_rows)==88*15*len(STRENGTHS),
        "continuousFixedTimeRows":sum(bool(r["isPreregisteredFixedTime"]) for r in continuous_rows)==88*len(FIXED_TIMES)*len(STRENGTHS),
        "allPureEvidenceVectorsNormalized":all(abs(sum(json.loads(r["fusedVector"]))-1.0)<1e-9 for r in continuous_rows),
        "sub070CounterfactualsExplicit":all(r["authorizationStatus"]=="COUNTERFACTUAL_FUSION_DIAGNOSTIC_ONLY" for r in continuous_rows if r["requestedStrength"]<0.70-1e-12),
        "authorizedM23M24ExactReplays":authorized_replay_info["m23SourceComparisonRows"]==88*3*6
            and authorized_replay_info["historicalHelperReplayRows"]==88*3*len(STRENGTHS[1:])
            and authorized_replay_info["m24ComparisonRows"]==88*3*len(STRENGTHS[1:])
            and authorized_replay_info["m23HelperMismatchN"]==0 and authorized_replay_info["m24ExactReuseReplayMismatchN"]==0,
        "allSharedLatencyNoDelay":all(bool(r["noDelayInvariantPass"]) for r in latency_rows),
        "allSharedLatencyNoRerank":all(bool(r["noRerankInvariantPass"]) for r in latency_rows),
        "foldSelectionExcludesHeldOut":all(not x["heldOutSessionUsedDuringSelection"] and x["heldOutSession"] not in x["trainingSessions"] for x in selections.values()),
        "candidateGridValidityAndAccuracyFloor":all((not r["selected"]) or (r["validCandidate"] and r["trainingWrongEarlyStopN"]==0 and r["trainingNoDelayViolationN"]==0) for r in candidate_rows),
        "selectedThresholdSharedAcrossAllStrengths":all(len({(r["contextTopThreshold"],r["contextMarginThreshold"],r["contextMinimumEvidenceSeconds"]) for r in latency_rows if r["branch"]==b and r["heldOutSession"]==h and r["operatingPoint"]==op})<=1 for b in BRANCHES for h in SESSIONS for op in OP_NAMES),
        "M23AssignmentsReused":assignment_reuse_pass,
        "sensitivityNoRefit":all(not r["thresholdRefitForSensitivity"] for r in sensitivity_rows),
        "wrongContextExcludedFromSelection":all(not r["thresholdSelectionUsedWrongContext"] for r in wrong_rows),
        "availabilityUsesPrimaryThreshold":availability_threshold_pass,
        "availabilitySummaryHasExpectedSessionCounts":availability_summary_counts_pass,
        "precisionSurfaceExpectedRows":len(precision_summary)==len(PRECISIONS)*len(SURFACE_STRENGTHS)*len(OP_NAMES)
            and len(precision_trial_rows)==len(PRECISIONS)*len(SURFACE_STRENGTHS)*88*len(OP_NAMES),
        "wrongContextExpectedRows":len([r for r in wrong_rows if r["recordType"]=="TRIAL"])==88*len(OP_NAMES)*6,
        "availabilityExpectedRows":len(availability_trial_rows)==88*len(OP_NAMES)*len(STRENGTHS)*len(AVAILABILITY_TIMES),
        "sensitivityExpectedRows":len(sensitivity_rows)==len(SESSIONS)*len(OP_NAMES)*len(BRANCHES)*2*3*len(STRENGTHS),
        "allRequiredArtifactsPresent":not missing,
        "allRequiredPlotsPresent":all((OUT/"plots"/name).is_file() for name in required_plot_names),
        "atLeast12Plots":len(list((OUT/"plots").glob("*.svg")))>=12,
        "inputHashesUnchanged":all((ROOT/x["path"]).is_file() and sha256_file(ROOT/x["path"])==x["sha256"] for x in manifest["inputs"]),
        "rawEegWaveformNotReadOrModified":manifest["rawEegWaveformRead"] is False and manifest["rawEegModified"] is False,
        "hardwareNotAccessed":manifest["nd8Accessed"] is False and manifest["com11Accessed"] is False and manifest["questUnityModified"] is False,
    }
    failures=[key for key,value in checks.items() if value is False or (isinstance(value,dict) and not all(value.values()))]
    final={
        "recordType":"m25_final_invariant_validation",
        "status":"PASS" if not failures else "FAIL",
        "runId":RUN_ID,
        "generatedAtUtc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "sourceHead":"f9cd64d114a7bec6822ec6ccb6d8e9727ef1a7be",
        "inputManifestSha256":sha256_file(MANIFEST_PATH),
        "checks":checks,"failedChecks":failures,"missingRequiredArtifacts":missing,
        "formalN":len(trials),"sessionCounts":cohort["sessionCounts"],
        "fixedEvidenceTimesSeconds":list(FIXED_TIMES),
        "strengths":[strength_label(s) for s in STRENGTHS],
        "primaryGrid":"0.10","sensitivityGrid":"0.04",
        "baselineFoldMatchN":baseline_fold_match_n,
        "continuousEvidenceRows":len(continuous_rows),
        "continuousPrimaryFixedRows":sum(bool(r["isPreregisteredFixedTime"]) for r in continuous_rows),
        "authorizedEvidenceRows":len(authorized_rows),
        "authorizedStopReplayRows":len(stop_replay_rows),
        "M23M24Replay":authorized_replay_info,
        "candidateGridRows":len(candidate_rows),
        "sharedLatencyHeldOutRows":len(latency_rows),
        "precisionSurfaceRows":len(precision_summary),
        "precisionSurfacePerTrialRows":len(precision_trial_rows),
        "wrongContextRows":len(wrong_rows),
        "contextAvailabilitySummaryRows":len(availability_summary),
        "resolutionStabilityRows":len(sensitivity_rows),
        "headroomRows":len(headroom_rows),
        "strengthSeparatedTrials":sep_records,
        "plots":plot_names,
        "runSeconds":time.time()-started,
        "noRawEegWaveformRead":True,
        "noHardwareAccess":True,
    }
    (OUT/"final_validation.json").write_text(json.dumps(final,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"status":final["status"],"failedChecks":failures,"n":len(trials),
                      "continuousRows":len(continuous_rows),"latencyRows":len(latency_rows),
                      "plots":len(plot_names),"outputDirectory":str(OUT)},indent=2))
    return 0 if final["status"]=="PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
