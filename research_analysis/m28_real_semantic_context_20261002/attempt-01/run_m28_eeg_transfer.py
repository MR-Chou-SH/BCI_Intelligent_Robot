"""Transfer measured M28 semantic Context quality into frozen M25 EEG replay.

This is explicitly a quality-transfer simulation. It does not claim causal
semantic labels for the randomized historical EEG trials and never modifies
M25/M23/M24 inputs or outputs.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
M25_RUN = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01/run_m25_analysis.py"
M25_MANIFEST = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01/INPUT_MANIFEST.json"
M25_SELECTION = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01/shared_threshold_outer_fold_selection.json"
ENGINE_RESULTS = OUT / "semantic_context_engine_results.jsonl"
TRANSFER_SPEC = OUT / "eeg_transfer_spec.md"
PER_TRIAL = OUT / "eeg_transfer_per_trial.csv"
SUMMARY_CSV = OUT / "eeg_transfer_summary.csv"
FRONTIER_CSV = OUT / "required_quality_frontier.csv"
ARRIVAL_CSV = OUT / "context_arrival_feasibility.csv"
VALIDATION = OUT / "eeg_transfer_validation.json"

TRANSFER_SEED_COUNT = 100
FRONTIER_SEED_COUNT = 24
TRANSFER_BASE_SEED = 281002
OPS = ("FAST", "MEDIUM", "CONSERVATIVE")
SCENARIOS = ("measured_api_latency", "precomputed_before_eeg", "available_after_0.2s", "available_after_0.4s", "available_after_0.6s", "available_after_0.8s")
FIXED_ARRIVALS = {"precomputed_before_eeg": 0.0, "available_after_0.2s": 0.2, "available_after_0.4s": 0.4, "available_after_0.6s": 0.6, "available_after_0.8s": 0.8}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("refusing to write empty CSV: " + path.name)
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _ci_by_seed(values: list[float]) -> tuple[float | None, float | None]:
    return _percentile(values, 0.025), _percentile(values, 0.975)


def _derive_seed(label: str) -> int:
    return int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:8], "big")


def _load_m25():
    sys.path.insert(0, str(ROOT))
    import importlib.util
    spec = importlib.util.spec_from_file_location("m25_readonly_transfer", M25_RUN)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load frozen M25 replay helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_active_context_rows() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = _jsonl(ENGINE_RESULTS)
    metadata = next((row["model"] for row in rows if row.get("record_type") == "run_metadata"), None)
    primary = [row for row in rows if row.get("call_kind") == "primary"]
    if metadata is None or len(primary) != 40 or len({row["case_id"] for row in primary}) != 40:
        raise ValueError("M28 primary semantic results are incomplete")
    active = [row for row in primary if row["result"].get("coverage_decision") == "active"]
    if not active:
        raise ValueError("M28 has no active semantic Context rows to transfer")
    for row in active:
        candidates = row["candidate_ids"]
        result = row["result"]
        if len(candidates) != 3 or len(result.get("context_prior", [])) != 3:
            raise ValueError("the M25 class bridge requires exactly three candidates and prior entries")
        total = sum(float(value) for value in result["context_prior"])
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("M28 pseudo-prior does not sum to one")
    return active, metadata


def _mapped_empirical_context(row: dict[str, Any], true_index: int, rng: random.Random, classes: tuple[str, ...]) -> dict[str, Any]:
    """Map one semantic case to EEG classes after engine evaluation.

    Correctly predicted semantic top candidates are aligned with the EEG trial
    true class solely as simulation machinery. Any observed wrong active output
    retains its benchmark candidate-offset error under a seeded permutation.
    """
    candidates = row["candidate_ids"]
    result = row["result"]
    evaluation = row["evaluation_only"]
    ranking = list(result["candidate_ranking"])
    top_id = ranking[0]
    accepted = set(evaluation.get("expected_target_ids", []))
    correct = top_id in accepted
    top_candidate_index = candidates.index(top_id)
    class_for_candidate: dict[str, int] = {}
    if correct:
        class_for_candidate[top_id] = true_index
    else:
        expected_id = next((object_id for object_id in candidates if object_id in accepted), None)
        if expected_id is None:
            raise ValueError("active semantic case has no evaluable expected target")
        expected_index = candidates.index(expected_id)
        offset = (top_candidate_index - expected_index) % 3
        if offset == 0:
            offset = 1
        class_for_candidate[top_id] = (true_index + offset) % 3
    remaining_candidates = [item for item in candidates if item not in class_for_candidate]
    remaining_classes = [index for index in range(3) if index not in class_for_candidate.values()]
    rng.shuffle(remaining_classes)
    class_for_candidate.update(zip(remaining_candidates, remaining_classes))
    candidate_prior = {candidate: float(value) for candidate, value in zip(candidates, result["context_prior"])}
    prior = [0.0, 0.0, 0.0]
    for candidate, class_index in class_for_candidate.items():
        prior[class_index] = candidate_prior[candidate]
    if not math.isclose(sum(prior), 1.0, abs_tol=1e-9):
        raise AssertionError("mapped semantic prior failed to preserve total mass")
    return {
        "prior": prior,
        "context_top": class_for_candidate[top_id],
        "semantic_case_id": row["case_id"],
        "semantic_top_correct": correct,
        "api_latency_seconds": float(row["api_latency_ms"]) / 1000.0,
        "class_for_candidate": class_for_candidate,
        "mapped_top_class": classes[class_for_candidate[top_id]],
    }


def _simulate_full_prior(m25, trial: dict[str, Any], grid: str, baseline: dict[str, Any], prior: list[float],
                         available_from: float, top_threshold: float, margin_threshold: float,
                         minimum_evidence: float, stability: float = 0.40) -> dict[str, Any]:
    """M25 frozen gate/fusion/stability replay with the empirical full prior vector."""
    baseline_stop = float(baseline["stopTimeSeconds"])
    candidate: int | None = None
    stable_start: float | None = None
    authorized_count = 0
    examined = 0
    prior_top = max(range(3), key=lambda index: (prior[index], -index))
    prior_mass = float(prior[prior_top])
    for point in trial["pointsByGrid"][grid]:
        t = float(point["effectiveEvidenceTimeSeconds"])
        if t > baseline_stop + 1e-9:
            break
        examined += 1
        raw_top, _, _, eeg = m25.raw_top_margin(point)
        if t < available_from - 1e-9 or prior_mass < 0.70 - 1e-12 or prior_top != raw_top:
            candidate = None
            stable_start = None
            continue
        authorized_count += 1
        fused = m25.fuse(eeg, prior)
        order = sorted(range(3), key=lambda index: (-fused[index], index))
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
                    selected = raw_top
                    wrong = selected != int(trial["trueClassIndex"])
                    return {
                        "stop": t, "selectedIndex": selected, "contextApplied": True,
                        "authorizedPointCount": authorized_count,
                        "authorizedPointRate": authorized_count / max(examined, 1),
                        "wrongEarlyStop": bool(wrong),
                        "contextCausedError": bool(wrong and baseline["correct"]),
                        "fallbackReason": "",
                    }
                return {
                    "stop": baseline_stop, "selectedIndex": int(baseline["selectedClassIndex"]),
                    "contextApplied": False, "authorizedPointCount": authorized_count,
                    "authorizedPointRate": authorized_count / max(examined, 1),
                    "wrongEarlyStop": False, "contextCausedError": False,
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
        "fallbackReason": "no_authorized_stable_crossing",
    }


def _baseline_and_shared(m25, trials, folds, baseline_by):
    selection = json.loads(M25_SELECTION.read_text(encoding="utf-8"))
    if selection.get("selectionUsesHeldOutSession") is not False:
        raise ValueError("M25 threshold selection was not frozen independently of held-out sessions")
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    baselines: dict[tuple[str, str, str], dict[str, Any]] = {}
    matches = 0
    for session in m25.SESSIONS:
        fold = folds[session]
        for op in OPS:
            selected[(session, op)] = selection["folds"][f"{m25.FROZEN_BRANCH}|{session}|{op}"]["selectedSharedContextParameters"]
        for trial in (item for item in trials if item["session"] == session):
            for op in OPS:
                config = m25.op_config(fold, op)
                baseline = m25.raw_stop(trial, "0.10", float(config["topThreshold"]), float(config["marginThreshold"]), 0.50, m25.STABILITY_PRIMARY)
                stored = baseline_by[(op, session, trial["trialId"])]
                if not math.isclose(float(baseline["stopTimeSeconds"]), float(stored["stopTimeSeconds"]), abs_tol=1e-9) or baseline["selectedClass"] != stored["selectedClass"]:
                    raise AssertionError(f"M23 baseline reproduction failed for {session}/{op}/{trial['trialId']}")
                baselines[(op, session, trial["trialId"])] = baseline
                matches += 1
    if matches != 264:
        raise AssertionError("expected 88 trials × 3 frozen EEG operating points")
    return selected, baselines, matches


def _run_measured_transfer(m25, trials, selected, baselines, active_rows, *, coverage: float,
                           availability_scenario: str, availability_fixed: float | None,
                           seed: int) -> list[dict[str, Any]]:
    # Reuse identical seeded Context assignments across arrival scenarios so
    # differences isolate availability timing rather than sample composition.
    rng = random.Random(_derive_seed(f"M28-measured|{seed}"))
    rows: list[dict[str, Any]] = []
    for trial in trials:
        assignment = None
        if rng.random() < coverage:
            semantic_row = rng.choice(active_rows)
            assignment = _mapped_empirical_context(semantic_row, int(trial["trueClassIndex"]), rng, tuple(m25.CLASSES))
        for op in OPS:
            baseline = baselines[(op, trial["session"], trial["trialId"])]
            parameters = selected[(trial["session"], op)]
            if parameters is None:
                raise ValueError(f"M25 has no selected deployable thresholds for {trial['session']}/{op}")
            available_from = (
                assignment["api_latency_seconds"] if availability_fixed is None and assignment is not None
                else (availability_fixed or 0.0)
            )
            if assignment is None:
                result = {
                    "stop": float(baseline["stopTimeSeconds"]), "selectedIndex": int(baseline["selectedClassIndex"]),
                    "contextApplied": False, "authorizedPointCount": 0, "wrongEarlyStop": False,
                    "contextCausedError": False, "fallbackReason": "semantic_context_abstained",
                }
            else:
                result = _simulate_full_prior(
                    m25, trial, "0.10", baseline, assignment["prior"], available_from,
                    float(parameters["topThreshold"]), float(parameters["marginThreshold"]),
                    float(parameters["minimumEvidenceSeconds"]), float(parameters["stabilitySeconds"]),
                )
            true_index = int(trial["trueClassIndex"])
            rows.append({
                "recordType": "SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
                "scenario": availability_scenario, "seed": seed, "session": trial["session"],
                "trialId": trial["trialId"], "operatingPoint": op,
                "trueClass": m25.CLASSES[true_index], "baselineStopSeconds": baseline["stopTimeSeconds"],
                "contextStopSeconds": result["stop"], "pairedGainSeconds": float(baseline["stopTimeSeconds"]) - float(result["stop"]),
                "baselineCorrect": baseline["selectedClassIndex"] == true_index,
                "contextCorrect": result["selectedIndex"] == true_index,
                "selectedClass": m25.CLASSES[result["selectedIndex"]],
                "semanticAssignmentActive": assignment is not None,
                "contextApplied": result["contextApplied"], "authorizedPointCount": result.get("authorizedPointCount", 0),
                "wrongEarlyStop": result["wrongEarlyStop"], "contextCausedError": result["contextCausedError"],
                "contextAvailableFromSeconds": available_from if assignment else None,
                "semanticCaseId": assignment["semantic_case_id"] if assignment else "",
                "semanticTopCorrect": assignment["semantic_top_correct"] if assignment else None,
                "semanticTopMass": max(assignment["prior"]) if assignment else None,
                "semanticPrior": json.dumps(assignment["prior"], separators=(",", ":")) if assignment else "",
                "mappedContextTopClass": assignment["mapped_top_class"] if assignment else "",
                "sharedThresholdTop": parameters["topThreshold"], "sharedThresholdMargin": parameters["marginThreshold"],
                "sharedMinimumEvidence": parameters["minimumEvidenceSeconds"], "stabilitySeconds": parameters["stabilitySeconds"],
                "fallbackReason": result.get("fallbackReason", ""),
            })
    return rows


def _summarize_transfer(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["scenario"], row["operatingPoint"], int(row["seed"]))].append(row)
    per_seed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for (scenario, op, seed), items in groups.items():
        baseline_accuracy = statistics.mean(row["baselineCorrect"] for row in items)
        per_seed[(scenario, op)].append({
            "seed": seed, "mean_gain": statistics.mean(float(row["pairedGainSeconds"]) for row in items),
            "median_gain": statistics.median(float(row["pairedGainSeconds"]) for row in items),
            "positive_gain_mean": statistics.mean(float(row["pairedGainSeconds"]) for row in items if row["contextApplied"]) if any(row["contextApplied"] for row in items) else 0.0,
            "baseline_accuracy": baseline_accuracy,
            "context_accuracy": statistics.mean(row["contextCorrect"] for row in items),
            "wrong_early_stops": sum(bool(row["wrongEarlyStop"]) for row in items),
            "context_caused_errors": sum(bool(row["contextCausedError"]) for row in items),
            "assignment_rate": statistics.mean(bool(row["semanticAssignmentActive"]) for row in items),
            "applied_rate": statistics.mean(bool(row["contextApplied"]) for row in items),
            "authorized_trial_rate": statistics.mean(int(row["authorizedPointCount"]) > 0 for row in items),
        })
    summaries = []
    for (scenario, op), values in sorted(per_seed.items()):
        gains = [item["mean_gain"] for item in values]
        conditional = [item["positive_gain_mean"] for item in values]
        conditional_lo, conditional_hi = _ci_by_seed(conditional)
        context_applied_rows = [
            row for row in rows
            if row["scenario"] == scenario and row["operatingPoint"] == op and row["contextApplied"]
        ]
        context_active_gains = [float(row["pairedGainSeconds"]) for row in context_applied_rows]
        lo, hi = _ci_by_seed(gains)
        summaries.append({
            "scenario": scenario, "operatingPoint": op, "seed_count": len(values), "trial_count": 88,
            "mean_all_trial_gain_seconds": statistics.mean(gains), "median_all_trial_gain_seconds": statistics.median([float(row["pairedGainSeconds"]) for row in rows if row["scenario"] == scenario and row["operatingPoint"] == op]),
            "seed_interval_95_lower": lo, "seed_interval_95_upper": hi,
            "mean_gain_when_context_applied_seconds": statistics.mean(conditional),
            "context_active_seed_interval_95_lower": conditional_lo,
            "context_active_seed_interval_95_upper": conditional_hi,
            "mean_context_active_gain_seconds": statistics.mean(context_active_gains) if context_active_gains else 0.0,
            "median_context_active_gain_seconds": statistics.median(context_active_gains) if context_active_gains else 0.0,
            "context_active_trial_seed_rows": len(context_active_gains),
            "context_active_gain_meets_0_40s": bool(context_active_gains and statistics.mean(context_active_gains) >= 0.40),
            "context_active_gain_meets_0_50s": bool(context_active_gains and statistics.mean(context_active_gains) >= 0.50),
            "mean_baseline_accuracy": statistics.mean(item["baseline_accuracy"] for item in values),
            "mean_context_accuracy": statistics.mean(item["context_accuracy"] for item in values),
            "accuracy_loss_vs_paired_baseline": statistics.mean(item["context_accuracy"] - item["baseline_accuracy"] for item in values),
            "wrong_early_stop_count": sum(item["wrong_early_stops"] for item in values),
            "context_caused_error_count": sum(item["context_caused_errors"] for item in values),
            "mean_semantic_assignment_rate": statistics.mean(item["assignment_rate"] for item in values),
            "mean_context_applied_rate": statistics.mean(item["applied_rate"] for item in values),
            "mean_context_authorized_trial_rate": statistics.mean(item["authorized_trial_rate"] for item in values),
            "target_0_30_supported": statistics.mean(gains) >= 0.30 and sum(item["wrong_early_stops"] for item in values) == 0 and statistics.mean(item["context_accuracy"] - item["baseline_accuracy"] for item in values) >= -1e-12,
            "target_0_40_supported": statistics.mean(gains) >= 0.40 and sum(item["wrong_early_stops"] for item in values) == 0 and statistics.mean(item["context_accuracy"] - item["baseline_accuracy"] for item in values) >= -1e-12,
            "target_0_50_supported": statistics.mean(gains) >= 0.50 and sum(item["wrong_early_stops"] for item in values) == 0 and statistics.mean(item["context_accuracy"] - item["baseline_accuracy"] for item in values) >= -1e-12,
        })
    return summaries


def rebuild_saved_summary() -> list[dict[str, Any]]:
    """Recompute only derived summary fields from the immutable per-trial CSV."""
    rows: list[dict[str, Any]] = []
    with PER_TRIAL.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = dict(raw)
            for key in ("baselineCorrect", "contextCorrect", "semanticAssignmentActive", "contextApplied", "wrongEarlyStop", "contextCausedError", "semanticTopCorrect"):
                value = row.get(key)
                row[key] = value == "True" if value in {"True", "False"} else None
            for key in ("seed", "authorizedPointCount"):
                row[key] = int(row[key])
            for key in ("baselineStopSeconds", "contextStopSeconds", "pairedGainSeconds", "contextAvailableFromSeconds", "semanticTopMass", "sharedThresholdTop", "sharedThresholdMargin", "sharedMinimumEvidence", "stabilitySeconds"):
                if row.get(key) not in (None, ""):
                    row[key] = float(row[key])
            rows.append(row)
    summary = _summarize_transfer(rows)
    _write_csv(SUMMARY_CSV, summary)
    _rebuild_arrival_feasibility(rows)
    if VALIDATION.is_file():
        validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        validation["measuredApiLatencyScenario"] = [
            _summary_validation_row(item) for item in summary if item["scenario"] == "measured_api_latency"
        ]
        validation["precomputedBeforeEegScenario"] = [
            _summary_validation_row(item) for item in summary if item["scenario"] == "precomputed_before_eeg"
        ]
        validation["arrivalFeasibilityRebuiltFromPerTrial"] = True
        VALIDATION.write_text(json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _summary_validation_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "scenario": item["scenario"],
        "operatingPoint": item["operatingPoint"],
        "seed_count": int(item["seed_count"]),
        "trial_count": int(item["trial_count"]),
        "mean_all_trial_gain_seconds": float(item["mean_all_trial_gain_seconds"]),
        "median_all_trial_gain_seconds": float(item["median_all_trial_gain_seconds"]),
        "mean_gain_when_context_applied_seconds": float(item["mean_context_active_gain_seconds"]),
        "context_active_trial_seed_rows": int(item["context_active_trial_seed_rows"]),
        "context_active_seed_interval_95_lower": float(item["context_active_seed_interval_95_lower"]),
        "context_active_seed_interval_95_upper": float(item["context_active_seed_interval_95_upper"]),
        "mean_baseline_accuracy": float(item["mean_baseline_accuracy"]),
        "mean_context_accuracy": float(item["mean_context_accuracy"]),
        "accuracy_loss_vs_paired_baseline": float(item["accuracy_loss_vs_paired_baseline"]),
        "wrong_early_stop_count": int(item["wrong_early_stop_count"]),
        "context_caused_error_count": int(item["context_caused_error_count"]),
        "mean_context_applied_rate": float(item["mean_context_applied_rate"]),
        "target_0_40_supported": bool(item["target_0_40_supported"]),
        "target_0_50_supported": bool(item["target_0_50_supported"]),
    }


def _rebuild_arrival_feasibility(rows: list[dict[str, Any]]) -> None:
    active_latencies = [
        float(item["api_latency_ms"]) / 1000.0
        for item in _jsonl(ENGINE_RESULTS)
        if item.get("record_type") == "case_result"
        and item.get("call_kind") == "primary"
        and item.get("result", {}).get("status") == "informative"
        and item.get("api_latency_ms") is not None
    ]
    api_p50 = _percentile(active_latencies, 0.50)
    api_p90 = _percentile(active_latencies, 0.90)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["scenario"]), str(row["operatingPoint"]))].append(row)
    output: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for op in OPS:
            items = groups.get((scenario, op), [])
            if not items:
                continue
            gains = [float(row["pairedGainSeconds"]) for row in items]
            applied = [row for row in items if row["contextApplied"]]
            arrivals = [
                float(row["contextAvailableFromSeconds"])
                for row in items if row.get("contextAvailableFromSeconds") not in (None, "")
            ]
            baseline = [float(row["baselineStopSeconds"]) for row in items]
            output.append({
                "scenario": scenario,
                "operatingPoint": op,
                "trial_count": len(items),
                "context_availability_recorded_rate": len(arrivals) / len(items),
                "context_available_mean_seconds": statistics.mean(arrivals) if arrivals else None,
                "context_available_median_seconds": statistics.median(arrivals) if arrivals else None,
                "context_available_p90_seconds": _percentile(arrivals, 0.90),
                "semantic_api_active_latency_p50_seconds": api_p50,
                "semantic_api_active_latency_p90_seconds": api_p90,
                "baseline_mean_stop_seconds": statistics.mean(baseline),
                "baseline_median_stop_seconds": statistics.median(baseline),
                "mean_all_trial_gain_seconds": statistics.mean(gains),
                "median_all_trial_gain_seconds": statistics.median(gains),
                "mean_context_active_gain_seconds": statistics.mean(float(row["pairedGainSeconds"]) for row in applied) if applied else 0.0,
                "context_applied_rate": len(applied) / len(items),
                "context_accuracy": statistics.mean(bool(row["contextCorrect"]) for row in items),
                "baseline_accuracy": statistics.mean(bool(row["baselineCorrect"]) for row in items),
                "wrong_early_stop_count": sum(bool(row["wrongEarlyStop"]) for row in items),
                "context_caused_error_count": sum(bool(row["contextCausedError"]) for row in items),
                "context_arrives_before_mean_eeg_stop": bool(arrivals) and statistics.mean(arrivals) < statistics.mean(baseline),
                "interpretation": "Transfer-simulation gain by availability scenario; measured API latency is sampled from the active semantic-call distribution.",
            })
    _write_csv(ARRIVAL_CSV, output)


def _frontier(m25, trials, selected, baselines) -> list[dict[str, Any]]:
    precisions = (0.80, 0.90, 0.95, 1.00)
    coverages = (0.25, 0.475, 0.75, 1.00)
    top_masses = (0.60, 0.70, 0.80, 0.90)
    arrivals = (0.0, 0.2, 0.4, 0.6, 0.8)
    rows: list[dict[str, Any]] = []
    combination_index = 0
    combination_count = len(precisions) * len(coverages) * len(top_masses) * len(arrivals) * len(OPS)
    for precision in precisions:
        for coverage in coverages:
            for top_mass in top_masses:
                for arrival in arrivals:
                    for op in OPS:
                        combination_index += 1
                        seed_gains: list[float] = []
                        seed_accuracy_deltas: list[float] = []
                        wrong_total = 0
                        context_caused_total = 0
                        applied_total = 0
                        trial_total = 0
                        for seed_offset in range(FRONTIER_SEED_COUNT):
                            seed = TRANSFER_BASE_SEED + 10000 + seed_offset
                            rng = random.Random(_derive_seed(f"frontier|{precision}|{coverage}|{top_mass}|{arrival}|{op}|{seed}"))
                            gains: list[float] = []
                            base_correct = 0
                            context_correct = 0
                            wrong = 0
                            caused = 0
                            applied = 0
                            for trial in trials:
                                true_index = int(trial["trueClassIndex"])
                                baseline = baselines[(op, trial["session"], trial["trialId"])]
                                base_correct += int(baseline["selectedClassIndex"] == true_index)
                                active = rng.random() < coverage
                                if not active:
                                    result = {"stop": baseline["stopTimeSeconds"], "selectedIndex": baseline["selectedClassIndex"], "contextApplied": False, "wrongEarlyStop": False, "contextCausedError": False}
                                else:
                                    if rng.random() < precision:
                                        context_top = true_index
                                    else:
                                        context_top = rng.choice([index for index in range(3) if index != true_index])
                                    prior = [(1.0 - top_mass) / 2.0] * 3
                                    prior[context_top] = top_mass
                                    params = selected[(trial["session"], op)]
                                    result = _simulate_full_prior(m25, trial, "0.10", baseline, prior, arrival,
                                                                  float(params["topThreshold"]), float(params["marginThreshold"]),
                                                                  float(params["minimumEvidenceSeconds"]), float(params["stabilitySeconds"]))
                                gains.append(float(baseline["stopTimeSeconds"]) - float(result["stop"]))
                                context_correct += int(result["selectedIndex"] == true_index)
                                wrong += int(result["wrongEarlyStop"])
                                caused += int(result["contextCausedError"])
                                applied += int(result["contextApplied"])
                            seed_gains.append(statistics.mean(gains))
                            seed_accuracy_deltas.append(context_correct / len(trials) - base_correct / len(trials))
                            wrong_total += wrong
                            context_caused_total += caused
                            applied_total += applied
                            trial_total += len(trials)
                        mean_gain = statistics.mean(seed_gains)
                        zero_errors = wrong_total == 0
                        no_accuracy_loss = statistics.mean(seed_accuracy_deltas) >= -1e-12
                        row = {
                            "evidence_type": "synthetic_required_quality_frontier_not_engine_result",
                            "precision_assumption": precision, "coverage_assumption": coverage,
                            "prior_top_mass": top_mass, "availability_seconds": arrival,
                            "operatingPoint": op, "seed_count": FRONTIER_SEED_COUNT,
                            "mean_all_trial_gain_seconds": mean_gain,
                            "seed_interval_95_lower": _percentile(seed_gains, 0.025),
                            "seed_interval_95_upper": _percentile(seed_gains, 0.975),
                            "mean_accuracy_delta_vs_eeg_only": statistics.mean(seed_accuracy_deltas),
                            "wrong_early_stop_count": wrong_total, "context_caused_error_count": context_caused_total,
                            "context_applied_rate": applied_total / trial_total,
                            "zero_wrong_no_accuracy_loss": zero_errors and no_accuracy_loss,
                        }
                        for target in (0.30, 0.40, 0.50):
                            row[f"supports_{target:.2f}s"] = mean_gain >= target and zero_errors and no_accuracy_loss
                        rows.append(row)
                        if combination_index % 20 == 0:
                            print("FRONTIER combinations {}/{}".format(combination_index, combination_count), flush=True)
    return rows


def run() -> dict[str, Any]:
    if any(path.exists() for path in (PER_TRIAL, SUMMARY_CSV, FRONTIER_CSV, ARRIVAL_CSV, VALIDATION)):
        raise FileExistsError("M28 transfer outputs already exist; refusing to overwrite prior analysis")
    TRANSFER_SPEC.write_text(
        """# M28 EEG transfer specification

Label: `SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION`. Historical EEG trials are randomized and do not carry natural semantic-scene target labels. Correct engine predictions are aligned to each trial's true class only after engine evaluation as explicit simulation machinery. Observed incorrect active outputs retain their candidate-offset error under seeded class mapping.

The source is the frozen 40-case M28 semantic benchmark. Primary transfer samples the empirical 19/40 active rate, full uncalibrated three-class pseudo-prior vectors, and observed API latencies with seeded assignments independent of EEG evidence. A separate precomputed-before-trial sensitivity sets availability to 0 s; fixed arrival sensitivities are 0.2/0.4/0.6/0.8 s. All M25 priors still pass its prior-top mass ≥0.70, raw-top agreement, frozen shared threshold, 0.40 s stability, and paired EEG-only cap. No M25/M23/M24 historical output is modified.

The required-quality frontier is a separate synthetic sensitivity surface. It varies assumed active precision, coverage, prior top mass, and availability while retaining the exact frozen M25 stopping parameters. It is not a measured property of the semantic engine.
""",
        encoding="utf-8",
    )
    before_manifest_hash = _sha256(M25_MANIFEST)
    m25 = _load_m25()
    trials, manifest, folds, baseline_by, _, _, _ = m25.load_inputs()
    cohort = m25.validate_cohort(trials, manifest)
    selected, baselines, baseline_match_count = _baseline_and_shared(m25, trials, folds, baseline_by)
    active_rows, model_metadata = _load_active_context_rows()
    semantic_all = _jsonl(ENGINE_RESULTS)
    primary = [row for row in semantic_all if row.get("call_kind") == "primary"]
    coverage = len(active_rows) / len(primary)
    empirical_precision = sum(
        row["result"].get("top_candidate") in row["evaluation_only"].get("expected_target_ids", []) for row in active_rows
    ) / len(active_rows)
    all_prior_tops = [max(row["result"]["context_prior"]) for row in active_rows]
    actual_latencies = [float(row["api_latency_ms"]) / 1000.0 for row in active_rows]
    transfer_rows: list[dict[str, Any]] = []
    for seed_offset in range(TRANSFER_SEED_COUNT):
        seed = TRANSFER_BASE_SEED + seed_offset
        for scenario in SCENARIOS:
            transfer_rows.extend(_run_measured_transfer(
                m25, trials, selected, baselines, active_rows, coverage=coverage,
                availability_scenario=scenario, availability_fixed=FIXED_ARRIVALS.get(scenario), seed=seed,
            ))
        if (seed_offset + 1) % 10 == 0:
            print("TRANSFER seeds {}/{}".format(seed_offset + 1, TRANSFER_SEED_COUNT), flush=True)
    _write_csv(PER_TRIAL, transfer_rows)
    transfer_summary = _summarize_transfer(transfer_rows)
    _write_csv(SUMMARY_CSV, transfer_summary)

    frontier_rows = _frontier(m25, trials, selected, baselines)
    _write_csv(FRONTIER_CSV, frontier_rows)
    frontier_targets = {}
    for target in (0.30, 0.40, 0.50):
        field = f"supports_{target:.2f}s"
        valid = [row for row in frontier_rows if row[field]]
        frontier_targets[str(target)] = {
            "supported_combinations": len(valid),
            "minimum_precision_assumption": min((row["precision_assumption"] for row in valid), default=None),
            "minimum_coverage_assumption": min((row["coverage_assumption"] for row in valid), default=None),
            "minimum_arrival_seconds": min((row["availability_seconds"] for row in valid), default=None),
            "combinations": valid[:30],
        }

    arrival_rows = []
    baseline_stops_by_op = {
        op: [float(row["stopTimeSeconds"]) for (row_op, _, _), row in baselines.items() if row_op == op]
        for op in OPS
    }
    for op in OPS:
        base = baseline_stops_by_op[op]
        for arrival in (0.0, 0.2, 0.4, 0.6, 0.8, _percentile(actual_latencies, 0.5) or 0.0, _percentile(actual_latencies, 0.9) or 0.0):
            arrival_rows.append({
                "operatingPoint": op, "availability_seconds": arrival,
                "baseline_mean_stop_seconds": statistics.mean(base),
                "baseline_median_stop_seconds": statistics.median(base),
                "active_semantic_api_latency_median_seconds": _percentile(actual_latencies, 0.5),
                "active_semantic_api_latency_p90_seconds": _percentile(actual_latencies, 0.9),
                "contexts_arrive_before_mean_eeg_stop": arrival < statistics.mean(base),
                "interpretation": "API call started at stimulus onset; precompute scenario is 0 s before EEG accumulation",
            })
    _write_csv(ARRIVAL_CSV, arrival_rows)

    after_manifest_hash = _sha256(M25_MANIFEST)
    if before_manifest_hash != after_manifest_hash:
        raise AssertionError("M25 input manifest changed during read-only transfer analysis")
    # load_inputs has independently verified every component fingerprint. Recheck
    # those same source files after the replay before writing final validation.
    source_hashes_unchanged = all(
        (ROOT / entry["path"]).is_file()
        and (ROOT / entry["path"]).stat().st_size == int(entry["sizeBytes"])
        and _sha256(ROOT / entry["path"]) == entry["sha256"]
        for entry in manifest["inputs"]
    )
    if not source_hashes_unchanged:
        raise AssertionError("one or more frozen M25 source files changed during replay")
    measured_rows = [row for row in transfer_summary if row["scenario"] == "measured_api_latency"]
    precomputed_rows = [row for row in transfer_summary if row["scenario"] == "precomputed_before_eeg"]
    result = {
        "recordType": "SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "m25ManifestSha256Before": before_manifest_hash, "m25ManifestSha256After": after_manifest_hash,
        "m25SourceHashesUnchanged": source_hashes_unchanged, "m25InputManifestVerified": True,
        "cohort": cohort, "baselineFoldMatchN": baseline_match_count,
        "semanticBenchmarkSha256": json.loads((OUT / "semantic_context_benchmark_lock.json").read_text(encoding="utf-8"))["sha256"],
        "semanticModel": model_metadata, "semanticCaseCount": len(primary), "semanticActiveCount": len(active_rows),
        "measuredActiveCoverage": coverage, "measuredActivePrecision": empirical_precision,
        "measuredActiveWrongCount": sum(row["result"].get("top_candidate") not in row["evaluation_only"].get("expected_target_ids", []) for row in active_rows),
        "measuredActivePriorTopMass": {
            "n": len(all_prior_tops), "mean": statistics.mean(all_prior_tops),
            "median": statistics.median(all_prior_tops), "p10": _percentile(all_prior_tops, 0.1), "p90": _percentile(all_prior_tops, 0.9),
        },
        "measuredActiveApiLatencySeconds": {"n": len(actual_latencies), "mean": statistics.mean(actual_latencies), "median": _percentile(actual_latencies, 0.5), "p90": _percentile(actual_latencies, 0.9)},
        "transferSeedCount": TRANSFER_SEED_COUNT,
        "measuredApiLatencyScenario": measured_rows,
        "precomputedBeforeEegScenario": precomputed_rows,
        "requiredQualityFrontierTargetSummary": frontier_targets,
        "interpretation": "Correct target mapping is an artificial transfer assumption after semantic calls; no causal prospective Context performance is claimed.",
    }
    VALIDATION.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    report = run()
    print(json.dumps({
        "baselineFoldMatchN": report["baselineFoldMatchN"],
        "measuredActiveCoverage": report["measuredActiveCoverage"],
        "measuredActivePrecision": report["measuredActivePrecision"],
        "measuredApiLatencyMedianSeconds": report["measuredActiveApiLatencySeconds"]["median"],
        "measuredApiLatencyP90Seconds": report["measuredActiveApiLatencySeconds"]["p90"],
        "measuredScenario": report["measuredApiLatencyScenario"],
        "precomputedScenario": report["precomputedBeforeEegScenario"],
        "frontier": report["requiredQualityFrontierTargetSummary"],
    }, ensure_ascii=False, indent=2))
