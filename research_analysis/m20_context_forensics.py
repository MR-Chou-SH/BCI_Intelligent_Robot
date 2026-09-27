"""Read-only M20 forensic replay and paired Context/fusion analysis.

This script reuses the frozen M15/V3 input contracts and decoder helpers. It
only reads historical EEG/artifacts and writes into one new output directory.
All prior-label scenarios are offline stress tests; they are not deployable
Context sources.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.paper_eeg import m15_matched_accuracy_analysis as m15  # noqa: E402
from analysis.paper_eeg import m15_stage2_paired_context_replay as paired_replay  # noqa: E402
from analysis.paper_eeg import m15_stage2_v3_context_analysis as v3  # noqa: E402
from integration.m12_context_eeg_fusion import (  # noqa: E402
    LAMBDA_CONTEXT,
    STRONG_EEG_MARGIN_THRESHOLD,
)
from integration.m13_dynamic_stopping import (  # noqa: E402
    M13_FUSED_EVIDENCE_THRESHOLD,
    M13_MARGIN_THRESHOLD,
    M13_REQUIRED_CONSECUTIVE,
)


V3_PRIOR_ARTIFACT = ROOT / "artifacts" / "m15_stage2_v3_context_20260921T081744Z"
PAIRED_PRIOR_ARTIFACT = ROOT / "artifacts" / "m15_stage2_paired_context_20260921T054628Z"
DATASET_MANIFEST = ROOT / "artifacts" / "m15_matched_accuracy_analysis_20260921" / "dataset_manifest.json"
FREEZE_PATH = ROOT / "artifacts" / "m15_stage2" / "STAGE2_SOFTWARE_FREEZE.json"
EXPLORATORY_TRIAL = ("B2", "m6_4-trial-023")
CLASS_INDEX = {label: index for index, label in enumerate(m15.CLASSES)}
ENDPOINT_ONSET_SECONDS = 2.4
ENDPOINT_EVIDENCE_SECONDS = ENDPOINT_ONSET_SECONDS - m15.GUARD_SECONDS
EPSILON = 1e-12

ALPHAS = (0.0, 0.25, 0.5, 1.0, 1.5)
SOFTENING_LAMBDAS = (0.5, 0.75, 1.0)
MIN_EVIDENCE_SECONDS = (0.9, 1.1, 1.3)
TOP_SCORE_THRESHOLDS = (0.42, 0.50, 0.55, 0.60, 0.70)
FUSED_MARGIN_THRESHOLDS = (0.05, 0.10, 0.15)
REQUIRED_CONSECUTIVE = 2
CONTEXT_REQUIRED_CONSECUTIVE = 3
CONTEXT_MIN_RAW_MARGIN = 0.15
PRIOR_STRENGTHS = (0.60, 0.75, 0.90, 0.95)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _capture_v3_rows(trials, raws, context_by_trial):
    score_cache: dict[tuple[str, str, float], list[float]] = {}
    original = v3.fixed_decision

    def capture(raw_by_session, trial, decision_time, fbcca):
        result = original(raw_by_session, trial, decision_time, fbcca)
        if result is not None:
            key = (trial["session"], trial["trial_id"], round(float(decision_time), 6))
            score_cache[key] = [float(value) for value in result["scores"]]
        return result

    v3.fixed_decision = capture
    try:
        rows = v3.build_timepoint_rows(trials, raws, context_by_trial)
    finally:
        v3.fixed_decision = original
    for row in rows:
        key = (row["session"], row["trial_id"], round(float(row["decision_time_s"]), 6))
        row["score_vector"] = score_cache[key]
    return rows


def _group_feature_rows(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["session"], row["trial_id"])].append(row)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for key, values in grouped.items():
        values.sort(key=lambda row: float(row["decision_time_s"]))
        first = values[0]
        result[key] = {
            "session": key[0],
            "trial_id": key[1],
            "true_class": first["true_class"],
            "context_prior": [
                float(first["prior_7_2"]),
                float(first["prior_9"]),
                float(first["prior_12"]),
            ],
            "causal_context_stratum": first["posthoc_context_label"],
            "timepoints": values,
        }
    return result


def _prior_for_scenario(trial: dict[str, Any], scenario: str) -> list[float]:
    if scenario == "causal_history":
        return list(trial["context_prior"])
    if scenario == "neutral_uniform":
        return [1.0 / 3.0] * 3
    if scenario.startswith("aligned_target_"):
        strength = float(scenario.rsplit("_", 1)[1])
        top = CLASS_INDEX[trial["true_class"]]
    elif scenario.startswith("conflict_wrong_"):
        strength = float(scenario.rsplit("_", 1)[1])
        top = (CLASS_INDEX[trial["true_class"]] + 1) % 3
    else:
        raise ValueError("unknown Context scenario: " + scenario)
    remainder = (1.0 - strength) / 2.0
    prior = [remainder, remainder, remainder]
    prior[top] = strength
    return prior


def _normalize(values: list[float]) -> list[float]:
    total = sum(values)
    if total <= 0.0:
        return [1.0 / len(values)] * len(values)
    return [float(value / total) for value in values]


def _fused_evidence(scores: list[float], prior: list[float], alpha: float, soften_lambda: float) -> dict[str, Any]:
    normalized_raw = _normalize(scores)
    order = sorted(range(3), key=lambda index: (-scores[index], index))
    raw_top, raw_second = order[:2]
    raw_unique = not math.isclose(scores[raw_top], scores[raw_second], rel_tol=0.0, abs_tol=1e-12)
    raw_margin = float(scores[raw_top] - scores[raw_second])
    context_order = sorted(range(3), key=lambda index: (-prior[index], index))
    context_unique = not math.isclose(prior[context_order[0]], prior[context_order[1]], rel_tol=0.0, abs_tol=1e-12)
    context_top = context_order[0] if context_unique else None
    strong = raw_unique and raw_margin >= STRONG_EEG_MARGIN_THRESHOLD
    strong_conflict = bool(
        alpha > 0.0
        and strong
        and context_top is not None
        and context_top != raw_top
    )
    if alpha == 0.0 or strong:
        posterior = normalized_raw
        fusion_mode = "eeg_only" if alpha == 0.0 else "strong_eeg_override"
    else:
        uniform = 1.0 / 3.0
        softened = [
            (1.0 - soften_lambda) * uniform + soften_lambda * float(value)
            for value in prior
        ]
        log_values = [
            math.log(max(float(scores[index]), EPSILON))
            + alpha * math.log(max(softened[index], EPSILON))
            for index in range(3)
        ]
        offset = max(log_values)
        weights = [math.exp(value - offset) for value in log_values]
        posterior = _normalize(weights)
        fusion_mode = "softened_log_multiplicative"
    fused_order = sorted(range(3), key=lambda index: (-posterior[index], index))
    fused_top, fused_second = fused_order[:2]
    fused_unique = not math.isclose(posterior[fused_top], posterior[fused_second], rel_tol=0.0, abs_tol=1e-12)
    return {
        "raw": normalized_raw,
        "posterior": posterior,
        "raw_top": raw_top,
        "raw_unique": raw_unique,
        "raw_margin": raw_margin,
        "context_top": context_top,
        "context_unique": context_unique,
        "fused_top": fused_top,
        "fused_unique": fused_unique,
        "fused_margin": float(posterior[fused_top] - posterior[fused_second]),
        "fusion_mode": fusion_mode,
        "strong_override": strong_conflict,
        "strong_override_preserved_top": bool(strong_conflict and fused_top == raw_top),
    }


def _policy_config(alpha: float, soften_lambda: float, min_evidence: float, top_threshold: float, margin_threshold: float) -> dict[str, Any]:
    uses_context = float(alpha) > 0.0
    return {
        "alpha": float(alpha),
        "soften_lambda": float(soften_lambda),
        "min_evidence_seconds": float(min_evidence),
        "fused_top_threshold": float(top_threshold),
        "fused_margin_threshold": float(margin_threshold),
        "required_consecutive": CONTEXT_REQUIRED_CONSECUTIVE if uses_context else REQUIRED_CONSECUTIVE,
        "context_min_raw_margin": CONTEXT_MIN_RAW_MARGIN if uses_context else 0.0,
    }


def _apply_stop(trial: dict[str, Any], prior: list[float] | None, config: dict[str, Any]) -> dict[str, Any]:
    candidate = None
    consecutive = 0
    strong_conflict_windows = 0
    strong_override_preserved_windows = 0
    context_assist_windows = 0
    for row in trial["timepoints"]:
        decision_time = float(row["decision_time_s"])
        evidence_time = decision_time - m15.GUARD_SECONDS
        scores = [float(value) for value in row["score_vector"]]
        current_prior = [1.0 / 3.0] * 3 if prior is None else prior
        evidence = _fused_evidence(
            scores,
            current_prior,
            float(config["alpha"]),
            float(config["soften_lambda"]),
        )
        if evidence["strong_override"]:
            strong_conflict_windows += 1
            strong_override_preserved_windows += int(evidence["strong_override_preserved_top"])
        raw_top = evidence["raw_top"]
        fused_top = evidence["fused_top"]
        context_compatible = (
            evidence["context_top"] is not None
            and evidence["context_top"] == raw_top
        )
        context_assist_windows += int(
            float(config["alpha"]) > 0.0
            and not evidence["strong_override"]
            and context_compatible
        )
        eligible = (
            evidence["raw_unique"]
            and evidence["fused_unique"]
            and fused_top == raw_top
            and (
                float(config["alpha"]) == 0.0
                or evidence["raw_margin"] >= float(config["context_min_raw_margin"])
            )
            and evidence["posterior"][raw_top] >= float(config["fused_top_threshold"])
            and evidence["fused_margin"] >= float(config["fused_margin_threshold"])
            and evidence_time >= float(config["min_evidence_seconds"])
        )
        if eligible:
            consecutive = consecutive + 1 if candidate == raw_top else 1
            candidate = raw_top
        else:
            candidate = None
            consecutive = 0
        if eligible and consecutive >= int(config["required_consecutive"]):
            selected = m15.CLASSES[raw_top]
            return {
                "selected_class": selected,
                "correct": selected == trial["true_class"],
                "decision_time_s": decision_time,
                "effective_eeg_evidence_time_s": evidence_time,
                "early_stop": decision_time < ENDPOINT_ONSET_SECONDS,
                "used_context_assist": bool(
                    float(config["alpha"]) > 0.0
                    and evidence["fusion_mode"] == "softened_log_multiplicative"
                    and evidence["posterior"][raw_top] > evidence["raw"][raw_top] + 1e-12
                ),
                "context_compatible_at_stop": context_compatible,
                "fusion_mode_at_stop": evidence["fusion_mode"],
                "strong_conflict_windows": strong_conflict_windows,
                "strong_override_preserved_windows": strong_override_preserved_windows,
                "context_assist_windows": context_assist_windows,
            }
    endpoint = trial["timepoints"][-1]
    scores = [float(value) for value in endpoint["score_vector"]]
    evidence = _fused_evidence(
        scores,
        [1.0 / 3.0] * 3 if prior is None else prior,
        float(config["alpha"]),
        float(config["soften_lambda"]),
    )
    selected = m15.CLASSES[evidence["raw_top"]]
    return {
        "selected_class": selected,
        "correct": selected == trial["true_class"],
        "decision_time_s": float(endpoint["decision_time_s"]),
        "effective_eeg_evidence_time_s": float(endpoint["decision_time_s"]) - m15.GUARD_SECONDS,
        "early_stop": False,
        "used_context_assist": False,
        "context_compatible_at_stop": evidence["context_top"] == evidence["raw_top"],
        "fusion_mode_at_stop": evidence["fusion_mode"],
        "strong_conflict_windows": strong_conflict_windows,
        "strong_override_preserved_windows": strong_override_preserved_windows,
        "context_assist_windows": context_assist_windows,
    }


def _outcome(trial: dict[str, Any], analysis_set: str, fold: str, condition: str, result: dict[str, Any], stratum: str, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    time_s = float(result["decision_time_s"])
    return {
        "analysis_set": analysis_set,
        "held_out_session": fold,
        "session": trial["session"],
        "trial_id": trial["trial_id"],
        "formal_status": "EXPLORATORY_REPLAY_SUPPLEMENT" if (trial["session"], trial["trial_id"]) == EXPLORATORY_TRIAL else "FORMAL_MANIFEST_RECORD",
        "condition": condition,
        "context_stratum": stratum,
        "true_class": trial["true_class"],
        "selected_class": result["selected_class"],
        "correct": bool(result["correct"]),
        "decision_time_from_onset_s": time_s,
        "effective_eeg_evidence_time_s": float(result["effective_eeg_evidence_time_s"]),
        "early_stop": bool(result["early_stop"]),
        "wrong_early_stop": bool(result["early_stop"] and not result["correct"]),
        "used_context_assist": bool(result["used_context_assist"]),
        "context_compatible_at_stop": bool(result["context_compatible_at_stop"]),
        "fusion_mode_at_stop": result["fusion_mode_at_stop"],
        "strong_conflict_windows": int(result["strong_conflict_windows"]),
        "strong_override_preserved_windows": int(result["strong_override_preserved_windows"]),
        "context_assist_windows": int(result["context_assist_windows"]),
        "context_caused_error_vs_paired_eeg": bool(
            baseline is not None and baseline["correct"] and not result["correct"]
        ),
        "paired_eeg_decision_time_s": None if baseline is None else baseline["decision_time_s"],
        "paired_eeg_selected_class": None if baseline is None else baseline["selected_class"],
        "context_accelerated_vs_paired_eeg": bool(
            baseline is not None and time_s < float(baseline["decision_time_s"]) - 1e-12
        ),
        "context_acceleration_vs_paired_eeg_s": (
            None if baseline is None else float(baseline["decision_time_s"]) - time_s
        ),
    }


def _t_at(records: list[dict[str, Any]], threshold: float) -> float | None:
    denominator = len(records)
    if denominator == 0:
        return None
    times = sorted({float(row["effective_eeg_evidence_time_s"]) for row in records})
    for time_s in times:
        correct_complete = sum(
            bool(row["correct"])
            and float(row["effective_eeg_evidence_time_s"]) <= time_s + 1e-12
            for row in records
        )
        if correct_complete / denominator >= threshold:
            return time_s
    return None


def _metric_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"N": 0}
    times = [float(row["effective_eeg_evidence_time_s"]) for row in records]
    accelerated = [time_s for time_s in times if time_s < ENDPOINT_EVIDENCE_SECONDS - 1e-12]
    ordered = sorted(times)
    accuracy_count = sum(bool(row["correct"]) for row in records)
    return {
        "N": len(records),
        "correct": accuracy_count,
        "accuracy": accuracy_count / len(records),
        "wrong_early_stops": sum(bool(row["wrong_early_stop"]) for row in records),
        "mean_effective_eeg_evidence_time_s": statistics.mean(times),
        "median_effective_eeg_evidence_time_s": statistics.median(times),
        "P90_effective_eeg_evidence_time_s": ordered[max(0, math.ceil(0.90 * len(ordered)) - 1)],
        "latest_effective_eeg_evidence_time_s": max(times),
        "T_at_95_s": _t_at(records, 0.95),
        "T_at_99_s": _t_at(records, 0.99),
        "T_at_100_s": _t_at(records, 1.0),
        "accelerated_fraction": len(accelerated) / len(records),
        "mean_acceleration_when_early_s": (
            statistics.mean(ENDPOINT_EVIDENCE_SECONDS - time_s for time_s in accelerated)
            if accelerated else None
        ),
        "context_assisted_trial_count": sum(bool(row["used_context_assist"]) for row in records),
        "context_accelerated_trial_count": sum(
            bool(row.get("context_accelerated_vs_paired_eeg")) for row in records
        ),
        "context_accelerated_fraction": sum(
            bool(row.get("context_accelerated_vs_paired_eeg")) for row in records
        ) / len(records),
        "mean_context_acceleration_when_early_s": (
            statistics.mean(
                float(row["context_acceleration_vs_paired_eeg_s"])
                for row in records
                if bool(row.get("context_accelerated_vs_paired_eeg"))
            )
            if any(bool(row.get("context_accelerated_vs_paired_eeg")) for row in records)
            else None
        ),
        "context_delayed_trial_count": sum(
            row.get("context_acceleration_vs_paired_eeg_s") is not None
            and float(row["context_acceleration_vs_paired_eeg_s"]) < -1e-12
            for row in records
        ),
        "mean_context_time_delta_vs_paired_eeg_s": (
            statistics.mean(
                float(row["context_acceleration_vs_paired_eeg_s"])
                for row in records
                if row.get("context_acceleration_vs_paired_eeg_s") is not None
            )
            if any(row.get("context_acceleration_vs_paired_eeg_s") is not None for row in records)
            else None
        ),
        "context_caused_error_count": sum(bool(row["context_caused_error_vs_paired_eeg"]) for row in records),
        "strong_contradiction_windows": sum(int(row["strong_conflict_windows"]) for row in records),
        "strong_override_preserved_top_windows": sum(int(row["strong_override_preserved_windows"]) for row in records),
    }


def _summaries_by_condition(outcomes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in outcomes:
        groups[(row["analysis_set"], row["condition"], "ALL")].append(row)
        groups[(row["analysis_set"], row["condition"], row["context_stratum"])].append(row)
    summaries = []
    for (analysis_set, condition, stratum), records in sorted(groups.items()):
        summary = {
            "analysis_set": analysis_set,
            "condition": condition,
            "context_stratum": stratum,
            **_metric_summary(records),
        }
        summaries.append(summary)
    return summaries


def _candidate_grid():
    for alpha in ALPHAS:
        lambdas = (0.5,) if alpha == 0.0 else SOFTENING_LAMBDAS
        for soften_lambda, min_evidence, top_threshold, margin_threshold in itertools.product(
            lambdas,
            MIN_EVIDENCE_SECONDS,
            TOP_SCORE_THRESHOLDS,
            FUSED_MARGIN_THRESHOLDS,
        ):
            yield _policy_config(alpha, soften_lambda, min_evidence, top_threshold, margin_threshold)


def _fit_policy(train_trials: list[dict[str, Any]], *, context_enabled: bool) -> dict[str, Any]:
    candidates = _candidate_grid() if context_enabled else (
        config for config in _candidate_grid() if config["alpha"] == 0.0
    )
    best: tuple[Any, dict[str, Any]] | None = None
    for config in candidates:
        causal_results = []
        conflict_results = []
        paired_eeg_results = []
        for trial in train_trials:
            causal_results.append(_apply_stop(trial, trial["context_prior"], config))
            conflict_results.append(_apply_stop(
                trial,
                _prior_for_scenario(trial, "conflict_wrong_0.90"),
                config,
            ))
            paired_eeg_results.append(_apply_stop(
                trial,
                None,
                {**config, "alpha": 0.0},
            ))
        context_caused_errors = 0
        for baseline, causal, conflict in zip(paired_eeg_results, causal_results, conflict_results):
            context_caused_errors += int(baseline["correct"] and not causal["correct"])
            context_caused_errors += int(baseline["correct"] and not conflict["correct"])
        conflict_wrong_early = sum(
            bool(result["early_stop"] and not result["correct"])
            for result in conflict_results
        )
        causal_wrong_early = sum(
            bool(result["early_stop"] and not result["correct"])
            for result in causal_results
        )
        mean_causal_evidence = statistics.mean(
            float(result["effective_eeg_evidence_time_s"]) for result in causal_results
        )
        key = (
            conflict_wrong_early,
            context_caused_errors,
            causal_wrong_early,
            sum(not result["correct"] for result in causal_results),
            mean_causal_evidence,
            -sum(bool(result["early_stop"]) for result in causal_results),
            -float(config["alpha"]),
            float(config["soften_lambda"]),
            float(config["min_evidence_seconds"]),
            float(config["fused_top_threshold"]),
            float(config["fused_margin_threshold"]),
        )
        if best is None or key < best[0]:
            best = (key, dict(config))
    if best is None:
        raise RuntimeError("M20 analysis policy grid was empty")
    return best[1]


def _stratum_for_scenario(trial: dict[str, Any], scenario: str) -> str:
    if scenario == "causal_history":
        return str(trial["causal_context_stratum"])
    if scenario == "neutral_uniform":
        return "NEUTRAL"
    if scenario.startswith("aligned_target_"):
        return "ALIGNED"
    if scenario.startswith("conflict_wrong_"):
        return "CONFLICT"
    raise ValueError("unknown scenario stratum: " + scenario)


def _endpoint_fusion_rows(trials: dict[tuple[str, str], dict[str, Any]], analysis_set: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scenarios = ["causal_history", "neutral_uniform"]
    scenarios.extend("aligned_target_{:.2f}".format(strength) for strength in PRIOR_STRENGTHS)
    scenarios.append("conflict_wrong_0.90")
    records: list[dict[str, Any]] = []
    for trial in trials.values():
        endpoint = trial["timepoints"][-1]
        scores = [float(value) for value in endpoint["score_vector"]]
        raw_top = max(range(3), key=lambda index: (scores[index], -index))
        raw_correct = m15.CLASSES[raw_top] == trial["true_class"]
        for scenario in scenarios:
            prior = _prior_for_scenario(trial, scenario)
            for alpha in ALPHAS:
                lambdas = (LAMBDA_CONTEXT,) if alpha == 1.0 else SOFTENING_LAMBDAS
                if alpha == 0.0:
                    lambdas = (LAMBDA_CONTEXT,)
                for soften_lambda in lambdas:
                    evidence = _fused_evidence(scores, prior, alpha, soften_lambda)
                    selected = m15.CLASSES[evidence["fused_top"]]
                    records.append({
                        "analysis_set": analysis_set,
                        "session": trial["session"],
                        "trial_id": trial["trial_id"],
                        "formal_status": "EXPLORATORY_REPLAY_SUPPLEMENT" if (trial["session"], trial["trial_id"]) == EXPLORATORY_TRIAL else "FORMAL_MANIFEST_RECORD",
                        "condition": scenario,
                        "alpha": alpha,
                        "prior_softening_lambda": soften_lambda,
                        "context_prior": json.dumps(prior, separators=(",", ":")),
                        "raw_score_vector_uncalibrated": json.dumps(scores, separators=(",", ":")),
                        "raw_top_class": m15.CLASSES[raw_top],
                        "fused_top_class": selected,
                        "true_class": trial["true_class"],
                        "raw_correct": raw_correct,
                        "fused_correct": selected == trial["true_class"],
                        "context_caused_endpoint_error": raw_correct and selected != trial["true_class"],
                        "strong_eeg_override": bool(evidence["strong_override"]),
                        "strong_override_preserved_raw_top": bool(evidence["strong_override_preserved_top"]),
                        "fusion_mode": evidence["fusion_mode"],
                    })
    summaries = []
    group_rows: dict[tuple[str, float, float], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        group_rows[(row["condition"], float(row["alpha"]), float(row["prior_softening_lambda"]))].append(row)
    for (condition, alpha, soften_lambda), selected in sorted(group_rows.items()):
        n = len(selected)
        raw_correct = sum(bool(row["raw_correct"]) for row in selected)
        fused_correct = sum(bool(row["fused_correct"]) for row in selected)
        summaries.append({
            "analysis_set": analysis_set,
            "condition": condition,
            "alpha": alpha,
            "prior_softening_lambda": soften_lambda,
            "N": n,
            "raw_correct": raw_correct,
            "raw_accuracy": raw_correct / n,
            "fused_correct": fused_correct,
            "fused_accuracy": fused_correct / n,
            "context_helped_endpoint_count": sum(not row["raw_correct"] and row["fused_correct"] for row in selected),
            "context_caused_endpoint_error_count": sum(bool(row["context_caused_endpoint_error"]) for row in selected),
            "strong_contradiction_windows": sum(bool(row["strong_eeg_override"]) for row in selected),
            "strong_override_preserved_raw_top_windows": sum(bool(row["strong_override_preserved_raw_top"]) for row in selected),
            "mean_effective_eeg_evidence_time_s": ENDPOINT_EVIDENCE_SECONDS,
            "accelerated_fraction": 0.0,
            "mean_acceleration_when_early_s": None,
        })
    return records, summaries


def _compare_v3_reproduction(results: list[dict[str, Any]]) -> dict[str, Any]:
    prior_path = V3_PRIOR_ARTIFACT / "per_trial_results.csv"
    with prior_path.open(encoding="utf-8", newline="") as stream:
        prior_rows = list(csv.DictReader(stream))
    expected = {
        (row["held_out_session"], row["condition"], row["session"], row["trial_id"]): row
        for row in prior_rows
    }
    actual = {
        (row["held_out_session"], row["condition"], row["session"], row["trial_id"]): row
        for row in results
    }
    if set(expected) != set(actual):
        raise AssertionError("V3 baseline replay changed the historical per-trial identity set")
    mismatches = []
    for key, old in expected.items():
        new = actual[key]
        if (
            str(new["selected_class"]) != old["selected_class"]
            or bool(new["correct"]) != (old["correct"] == "True")
            or not math.isclose(float(new["decision_time_s"]), float(old["decision_time_s"]), abs_tol=1e-12)
        ):
            mismatches.append({"key": key, "prior": old, "replay": new})
    if mismatches:
        raise AssertionError("V3 per-trial reproduction mismatch: " + json.dumps(mismatches[:3], ensure_ascii=False))
    return {"status": "PASS", "comparedRows": len(expected), "mismatchCount": 0, "priorArtifact": str(prior_path)}


def _paired_m12_replay(output: Path) -> dict[str, Any]:
    replay_dir = output / "current-m12-m13-paired-replay"
    result = paired_replay.run(m15.REPLAY, FREEZE_PATH, replay_dir)
    prior_path = PAIRED_PRIOR_ARTIFACT / "paired_trial_results.csv"
    with prior_path.open(encoding="utf-8", newline="") as stream:
        prior = list(csv.DictReader(stream))
    with (replay_dir / "paired_trial_results.csv").open(encoding="utf-8", newline="") as stream:
        current = list(csv.DictReader(stream))
    if len(prior) != len(current) or len(current) != 89:
        raise AssertionError("M12/M13 historical paired replay changed trial count")
    keys = ("session", "trial_id", "off_decision_class", "off_correct", "off_early_stop", "on_decision_class", "on_correct", "on_early_stop", "effect_type")
    old_by_key = {(row["session"], row["trial_id"]): row for row in prior}
    new_by_key = {(row["session"], row["trial_id"]): row for row in current}
    if set(old_by_key) != set(new_by_key):
        raise AssertionError("M12/M13 replay changed historical trial identity set")
    mismatches = []
    for identity, old in old_by_key.items():
        new = new_by_key[identity]
        if any(old[key] != new[key] for key in keys if key not in ("session", "trial_id")):
            mismatches.append(identity)
        for time_key in ("off_decision_time", "on_decision_time"):
            if not math.isclose(float(old[time_key]), float(new[time_key]), abs_tol=1e-12):
                mismatches.append(identity)
    if mismatches:
        raise AssertionError("M12/M13 replay differs from preserved paired artifact: " + repr(mismatches[:5]))
    return {
        "status": "PASS",
        "priorArtifact": str(prior_path),
        "newReplayDirectory": str(replay_dir),
        "comparedTrials": len(current),
        "mismatchCount": 0,
        "replaySummary": result["paired"],
        "contextOff": result["context_off"],
        "contextOn": result["context_on"],
        "formal88": _paired_formal_summary(current),
    }


def _paired_formal_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    formal = [row for row in records if (row["session"], row["trial_id"]) != EXPLORATORY_TRIAL]
    result = {"N": len(formal)}
    for branch in ("off", "on"):
        key = "{}_".format(branch)
        correct = sum(row[key + "correct"] == "True" for row in formal)
        early = sum(row[key + "early_stop"] == "True" for row in formal)
        times = [float(row[key + "decision_time"]) - m15.GUARD_SECONDS for row in formal]
        result[branch] = {
            **_metric_summary([
                {
                    "correct": row[key + "correct"] == "True",
                    "effective_eeg_evidence_time_s": float(row[key + "decision_time"]) - m15.GUARD_SECONDS,
                    "early_stop": row[key + "early_stop"] == "True",
                    "wrong_early_stop": row[key + "early_stop"] == "True" and row[key + "correct"] != "True",
                    "used_context_assist": False,
                    "context_caused_error_vs_paired_eeg": False,
                    "strong_conflict_windows": 0,
                    "strong_override_preserved_windows": 0,
                }
                for row in formal
            ]),
            "early_stop_count": early,
            "mean_onset_relative_decision_time_s": statistics.mean(float(row[key + "decision_time"]) for row in formal),
            "mean_effective_eeg_evidence_time_s": statistics.mean(times),
        }
    result["context_caused_wrong_final_count"] = sum(
        row["off_correct"] == "True" and row["on_correct"] != "True" for row in formal
    )
    return result


def _read_dataset_manifest() -> dict[str, Any]:
    return json.loads(DATASET_MANIFEST.read_text(encoding="utf-8"))


def _forensic_audit(context_by_trial: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    prior_vectors = [
        [float(row[key]) for key in ("prior_7_2", "prior_9", "prior_12")]
        for row in context_by_trial.values()
    ]
    top_values = [max(vector) for vector in prior_vectors]
    softened_example = [
        (1.0 - LAMBDA_CONTEXT) / 3.0 + LAMBDA_CONTEXT * value
        for value in (0.90, 0.05, 0.05)
    ]
    active_mass = [sum(vector) for vector in prior_vectors]
    return {
        "historical_v3_analysis": {
            "code": "analysis/paper_eeg/m15_stage2_v3_context_analysis.py",
            "fbcca_input": "Three raw, nonnegative fused CCA scores from eeg.decoder.fbcca.predict_fbcca; scores and top/sum ratios are not calibrated class probabilities.",
            "context_source": str(PAIRED_PRIOR_ARTIFACT / "paired_timepoint_trajectories.csv"),
            "context_source_sha256": sha256(PAIRED_PRIOR_ARTIFACT / "paired_timepoint_trajectories.csv"),
            "context_generator": "predeclared alternating observable histories in integration.m13_historical_replay._context_for_trial; no current/future label input",
            "v3_fusion": "No M12 multiplicative fusion in the V3 policy sweep. V3 computes label-free context informativeness, requires a unique context top agreeing with the raw EEG top, then evaluates a separately cross-fitted relaxed EEG gate. Selected class always remains the raw EEG top.",
            "informative_score": "context_top_probability * (1 - normalized_context_entropy), threshold 0.5",
            "loso_sessions": ["A", "B1", "B2"],
            "timing": "Decision times are onset-relative; effective EEG evidence is decision_time - 0.5 s guard.",
        },
        "current_m12_m13_path": {
            "code": ["integration/m12_context_eeg_fusion.py", "integration/m13_dynamic_stopping.py"],
            "context_softening_lambda": LAMBDA_CONTEXT,
            "softening_formula": "p_soft = (1-lambda)*uniform + lambda*renormalized_active_prior",
            "prior_090_example": {
                "raw_prior": [0.90, 0.05, 0.05],
                "soft_prior": softened_example,
                "softened_top_probability": softened_example[0],
            },
            "active_candidate_projection": {
                "all_three_legacy_candidates_active": True,
                "active_prior_mass_min": min(active_mass),
                "active_prior_mass_max": max(active_mass),
                "three_candidate_projection_flattens_context": False,
            },
            "multiplicative_fusion": "normalize(raw_FBCCA_score_i * p_soft_i); scores remain uncalibrated; epsilon floor 1e-12",
            "strong_eeg_override_margin": STRONG_EEG_MARGIN_THRESHOLD,
            "strong_eeg_override": "When the unique raw FBCCA top has raw top1-top2 margin >= 0.20, M12 records Context but uses normalized raw EEG scores as fused evidence.",
            "m13_stopping_gates": {
                "fused_top_threshold": M13_FUSED_EVIDENCE_THRESHOLD,
                "fused_margin_threshold": M13_MARGIN_THRESHOLD,
                "required_consecutive_windows": M13_REQUIRED_CONSECUTIVE,
                "raw_eeg_confirmation_required": True,
                "context_can_raise_fused_confidence_before_gates": True,
                "context_can_change_selected_target_at_stop": False,
            },
            "prior_strength_statistics": {
                "N": len(prior_vectors),
                "mean_top_probability": statistics.mean(top_values),
                "median_top_probability": statistics.median(top_values),
                "minimum_top_probability": min(top_values),
                "maximum_top_probability": max(top_values),
            },
        },
        "formal_record_identity": {
            "all_analysis_trials": 89,
            "formal_manifest_records": 88,
            "exploratory_replay_supplement": {"session": EXPLORATORY_TRIAL[0], "trialId": EXPLORATORY_TRIAL[1]},
            "reason": "B2 formal manifest lacks the canonical association start for trial-023; M6.5b replay supplies its exact anchor/trajectory as the 30th exploratory B2 record.",
            "b1_qc_exclusion": "B1 trial-011 remains excluded by immutable QC-valid index because clock-sync freshness exceeded the frozen five-second limit.",
        },
    }


def run(output: Path) -> dict[str, Any]:
    output = output.resolve()
    if output.exists():
        raise FileExistsError("Refusing to overwrite M20 Task 3 output: {}".format(output))
    output.mkdir(parents=True)

    trials, _anchors = m15.load_exact_trials()
    context_by_trial = v3.load_context_by_trial()
    raws = {session: m15.load_raw(root) for session, root in m15.SESSION_ROOTS.items()}
    feature_rows = _capture_v3_rows(trials, raws, context_by_trial)
    grouped = _group_feature_rows(feature_rows)
    if len(grouped) != 89 or set(grouped) != set(context_by_trial):
        raise AssertionError("historical trial identities do not match V3 Context inputs")

    baseline_outputs: list[dict[str, Any]] = []
    baseline_summaries: list[dict[str, Any]] = []
    baseline_policies: list[dict[str, Any]] = []
    analysis_sets = {
        "all_89": grouped,
        "formal_88": {key: value for key, value in grouped.items() if key != EXPLORATORY_TRIAL},
    }
    for analysis_set, selected in analysis_sets.items():
        rows = [row for trial in selected.values() for row in trial["timepoints"]]
        results, _folds, policies = v3.evaluate_loso(rows)
        for record in results:
            trial = selected[(record["session"], record["trial_id"])]
            generic = {
                **record,
                "analysis_set": analysis_set,
                "condition": record["condition"],
                "context_stratum": trial["causal_context_stratum"],
                "effective_eeg_evidence_time_s": float(record["decision_time_s"]) - m15.GUARD_SECONDS,
                "used_context_assist": bool(record["used_context_assist"]),
                "context_caused_error_vs_paired_eeg": False,
                "strong_conflict_windows": 0,
                "strong_override_preserved_windows": 0,
            }
            baseline_outputs.append(generic)
            baseline_summaries.append({})
        for condition in ("FIXED_2_4", "V3_EEG_ONLY", "V3_CONTEXT_RULE"):
            subset = [row for row in baseline_outputs if row.get("analysis_set") == analysis_set and row["condition"] == condition]
            baseline_summaries.append({
                "analysis_set": analysis_set,
                "condition": condition,
                "context_stratum": "ALL",
                **_metric_summary(subset),
                "mean_onset_relative_decision_time_s": statistics.mean(float(row["decision_time_s"]) for row in subset),
            })
        baseline_policies.extend({"analysis_set": analysis_set, **row} for row in policies)

    baseline_outputs = [row for row in baseline_outputs if row]
    reproduction = _compare_v3_reproduction([
        row for row in baseline_outputs if row["analysis_set"] == "all_89"
    ])
    v3_summary = [
        row for row in baseline_summaries
        if row.get("condition") in ("FIXED_2_4", "V3_EEG_ONLY", "V3_CONTEXT_RULE")
    ]

    fusion_rows: list[dict[str, Any]] = []
    fusion_summaries: list[dict[str, Any]] = []
    for analysis_set, selected in analysis_sets.items():
        detail, summary = _endpoint_fusion_rows(selected, analysis_set)
        fusion_rows.extend(detail)
        fusion_summaries.extend(summary)

    experiment_rows: list[dict[str, Any]] = []
    selected_configs: list[dict[str, Any]] = []
    stop_conditions = ["causal_history", "neutral_uniform"]
    stop_conditions.extend("aligned_target_{:.2f}".format(strength) for strength in PRIOR_STRENGTHS)
    stop_conditions.append("conflict_wrong_0.90")
    for analysis_set, selected_map in analysis_sets.items():
        folds = sorted({key[0] for key in selected_map})
        for held_out in folds:
            train = [trial for key, trial in selected_map.items() if key[0] != held_out]
            validation = [trial for key, trial in selected_map.items() if key[0] == held_out]
            eeg_config = _fit_policy(train, context_enabled=False)
            context_config = _fit_policy(train, context_enabled=True)
            selected_configs.extend([
                {"analysis_set": analysis_set, "held_out_session": held_out, "policy": "analysis_eeg_only", **eeg_config},
                {"analysis_set": analysis_set, "held_out_session": held_out, "policy": "analysis_context_fusion", **context_config},
            ])
            for trial in validation:
                baseline = _apply_stop(trial, None, eeg_config)
                eeg_stratum = str(trial["causal_context_stratum"])
                experiment_rows.append(_outcome(
                    trial, analysis_set, held_out, "analysis_eeg_only", baseline, eeg_stratum
                ))
                for condition in stop_conditions:
                    prior = _prior_for_scenario(trial, condition)
                    result = _apply_stop(trial, prior, context_config)
                    experiment_rows.append(_outcome(
                        trial,
                        analysis_set,
                        held_out,
                        condition,
                        result,
                        _stratum_for_scenario(trial, condition),
                        baseline,
                    ))
                    if condition == "aligned_target_0.90":
                        # Keep the requested Green-history -> Red-prior illustration
                        # separate from the full three-class 0.90 aligned-prior sweep.
                        if trial["true_class"] == "target_left":
                            experiment_rows.append(_outcome(
                                trial,
                                analysis_set,
                                held_out,
                                "green_history_red_prior_0.90",
                                result,
                                "ALIGNED",
                                baseline,
                            ))

    # A fixed, preregistered sensitivity sweep separates the Context weight
    # question from the cross-fitted policy selector. Every alpha/lambda uses
    # the same EEG gate plus the Context stability guard; no setting is tuned
    # on the trial being evaluated.
    stop_sensitivity_rows: list[dict[str, Any]] = []
    stop_sensitivity_summaries: list[dict[str, Any]] = []
    stop_sensitivity_conditions = list(stop_conditions)
    for analysis_set, selected_map in analysis_sets.items():
        for alpha in ALPHAS:
            lambdas = (0.5,) if alpha == 0.0 else SOFTENING_LAMBDAS
            for soften_lambda in lambdas:
                fixed_context_config = _policy_config(
                    alpha,
                    soften_lambda,
                    min_evidence=0.9,
                    top_threshold=0.42,
                    margin_threshold=0.05,
                )
                fixed_context_config["required_consecutive"] = CONTEXT_REQUIRED_CONSECUTIVE
                fixed_context_config["context_min_raw_margin"] = CONTEXT_MIN_RAW_MARGIN
                paired_eeg_config = {
                    **fixed_context_config,
                    "alpha": 0.0,
                    "context_min_raw_margin": 0.0,
                }
                for trial in selected_map.values():
                    baseline = _apply_stop(trial, None, paired_eeg_config)
                    for scenario in stop_sensitivity_conditions:
                        prior = _prior_for_scenario(trial, scenario)
                        result = _apply_stop(trial, prior, fixed_context_config)
                        condition = "fixed_stop_a{:.2f}_l{:.2f}__{}".format(
                            alpha, soften_lambda, scenario
                        )
                        stop_sensitivity_rows.append(_outcome(
                            trial,
                            analysis_set,
                            trial["session"],
                            condition,
                            result,
                            _stratum_for_scenario(trial, scenario),
                            baseline,
                        ))
                        if scenario == "aligned_target_0.90" and trial["true_class"] == "target_left":
                            stop_sensitivity_rows.append(_outcome(
                                trial,
                                analysis_set,
                                trial["session"],
                                "fixed_stop_a{:.2f}_l{:.2f}__green_history_red_prior_0.90".format(
                                    alpha, soften_lambda
                                ),
                                result,
                                "ALIGNED",
                                baseline,
                            ))
    sensitivity_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in stop_sensitivity_rows:
        sensitivity_groups[row["condition"]].append(row)
    for condition, records in sorted(sensitivity_groups.items()):
        for analysis_set in ("all_89", "formal_88"):
            subset = [row for row in records if row["analysis_set"] == analysis_set]
            if subset:
                stop_sensitivity_summaries.append({
                    "condition": condition,
                    "analysis_set": analysis_set,
                    **_metric_summary(subset),
                })

    m12_reproduction = _paired_m12_replay(output)
    audit = _forensic_audit(context_by_trial)
    audit["paired_m12_m13_replay"] = {
        "reproduction": m12_reproduction["status"],
        "newReplayDirectory": m12_reproduction["newReplayDirectory"],
        "pairedSummary": m12_reproduction["replaySummary"],
        "formal88": m12_reproduction["formal88"],
    }
    audit["v3_baseline_reproduction"] = reproduction

    controlled_rows = []
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in experiment_rows:
        by_condition[row["condition"]].append(row)
    for condition, records in sorted(by_condition.items()):
        if condition == "green_history_red_prior_0.90":
            records = [row for row in records if row["true_class"] == "target_left"]
        for analysis_set in ("all_89", "formal_88"):
            subset = [row for row in records if row["analysis_set"] == analysis_set]
            if subset:
                controlled_rows.append({
                    "condition": condition,
                    "analysis_set": analysis_set,
                    "target_subset": "RED_TARGETS_ONLY" if condition == "green_history_red_prior_0.90" else "ALL_CLASSES",
                    **_metric_summary(subset),
                    "green_prior_mapping": "Green history selects block_sim_02; red target is target_left / 7.2 Hz / block_sim_01 in the historical M9 mapping." if condition == "green_history_red_prior_0.90" else None,
                })

    metrics_rows = _summaries_by_condition(experiment_rows)
    # Validate the hard safety mechanism on nonzero-alpha fixed sweeps as well
    # as the cross-fitted candidate. A vacuous alpha-zero check is insufficient.
    strong_override_checks = [
        row for row in stop_sensitivity_rows
        if row["condition"].endswith("__conflict_wrong_0.90")
        and not row["condition"].startswith("fixed_stop_a0.00_")
    ]
    conflict_summaries = [
        row for row in controlled_rows
        if row["condition"] == "conflict_wrong_0.90"
    ]
    conflict_wrong_early = sum(
        int(row["wrong_early_stops"])
        for row in conflict_summaries
    )
    conflict_context_caused = sum(
        int(row["context_caused_error_count"])
        for row in conflict_summaries
    )
    strong_conflict_window_count = sum(
        int(row["strong_conflict_windows"]) for row in strong_override_checks
    )
    strong_override_preserved_count = sum(
        int(row["strong_override_preserved_windows"]) for row in strong_override_checks
    )
    if (
        strong_conflict_window_count == 0
        or strong_override_preserved_count != strong_conflict_window_count
        or any(
            int(row["strong_override_preserved_windows"]) != int(row["strong_conflict_windows"])
            for row in strong_override_checks
        )
    ):
        raise AssertionError("strong contradictory EEG was not preserved for every fixed nonzero-alpha conflict policy")

    fixed_conflict_checks = [
        row for row in stop_sensitivity_rows
        if row["condition"].endswith("__conflict_wrong_0.90")
        and not row["condition"].startswith("fixed_stop_a0.00_")
    ]
    fixed_conflict_wrong_early = sum(int(row["wrong_early_stop"]) for row in fixed_conflict_checks)
    fixed_conflict_context_errors = sum(int(row["context_caused_error_vs_paired_eeg"]) for row in fixed_conflict_checks)
    if fixed_conflict_wrong_early or fixed_conflict_context_errors:
        raise AssertionError("a fixed nonzero-alpha wrong-prior sweep caused a held-out early error")
        raise AssertionError("strong EEG override failed to preserve raw EEG top under conflict")

    manifest_inputs = {
        "v3PriorArtifactPerTrial": V3_PRIOR_ARTIFACT / "per_trial_results.csv",
        "pairedTrajectory": PAIRED_PRIOR_ARTIFACT / "paired_timepoint_trajectories.csv",
        "pairedTrialResults": PAIRED_PRIOR_ARTIFACT / "paired_trial_results.csv",
        "datasetManifest": DATASET_MANIFEST,
        "m6_5bReplay": m15.REPLAY,
        "m6_1bRawEeg": m15.SESSION_ROOTS["A"] / "raw-eeg-packets.jsonl",
        "m6_4_B1_RawEeg": m15.SESSION_ROOTS["B1"] / "raw-eeg-packets.jsonl",
        "m6_4_B2_RawEeg": m15.SESSION_ROOTS["B2"] / "raw-eeg-packets.jsonl",
        "m12FusionCode": ROOT / "integration" / "m12_context_eeg_fusion.py",
        "m13StoppingCode": ROOT / "integration" / "m13_dynamic_stopping.py",
        "m13ContextGenerator": ROOT / "integration" / "m13_historical_replay.py",
        "v3AnalysisCode": ROOT / "analysis" / "paper_eeg" / "m15_stage2_v3_context_analysis.py",
        "m15MatchedAccuracyCode": ROOT / "analysis" / "paper_eeg" / "m15_matched_accuracy_analysis.py",
    }
    manifest = {
        "recordType": "m20_context_forensic_replay",
        "analysisOnly": True,
        "productionM19Modified": False,
        "rawEegModified": False,
        "nd8Opened": False,
        "questBuildRun": False,
        "datasetIdentity": {"allAnalysisTrials": 89, "formalManifestTrials": 88, "exploratorySupplement": list(EXPLORATORY_TRIAL)},
        "inputs": {
            name: {"path": str(path), "exists": path.exists(), "sha256": sha256(path) if path.is_file() else None}
            for name, path in manifest_inputs.items()
        },
        "timing": {
            "onsetGuardSeconds": m15.GUARD_SECONDS,
            "decisionTimeGridOnsetRelativeSeconds": list(v3.TIMEPOINTS),
            "effectiveEvidenceTimeGridSeconds": [round(value - m15.GUARD_SECONDS, 6) for value in v3.TIMEPOINTS],
            "endpointEvidenceSeconds": ENDPOINT_EVIDENCE_SECONDS,
        },
        "classMapping": {"target_left": "Red / 7.2 Hz / block_sim_01", "target_center": "Green / 9 Hz / block_sim_02", "target_right": "Blue / 12 Hz / block_sim_03"},
        "policyGrid": {
            "alphas": list(ALPHAS),
            "priorSofteningLambda": list(SOFTENING_LAMBDAS),
            "alignedPriorStrengths": list(PRIOR_STRENGTHS),
            "neutralPrior": [1.0 / 3.0] * 3,
            "conflictPriorTop": 0.90,
            "minimumEvidenceSeconds": list(MIN_EVIDENCE_SECONDS),
            "fusedTopThresholds": list(TOP_SCORE_THRESHOLDS),
            "fusedMarginThresholds": list(FUSED_MARGIN_THRESHOLDS),
            "requiredConsecutiveWindows": REQUIRED_CONSECUTIVE,
            "contextStopStability": {
                "minimumRawEegTopMargin": CONTEXT_MIN_RAW_MARGIN,
                "requiredConsecutiveWindows": CONTEXT_REQUIRED_CONSECUTIVE,
                "eegOnlyRequiredConsecutiveWindows": REQUIRED_CONSECUTIVE,
            },
            "fixedContextStopSensitivity": {
                "alphas": list(ALPHAS),
                "priorSofteningLambda": list(SOFTENING_LAMBDAS),
                "fixedMinimumEvidenceSeconds": 0.9,
                "fixedFusedTopThreshold": 0.42,
                "fixedFusedMarginThreshold": 0.05,
                "usesCrossFittedSettings": False,
                "selectionNote": "Fixed settings compare Context weights without tuning on evaluated trials; the separate analysis_context_fusion result remains cross-fitted.",
            },
        },
    }

    write_csv(output / "v3_baseline_per_trial.csv", baseline_outputs)
    write_csv(output / "fusion_endpoint_per_trial.csv", fusion_rows)
    write_csv(output / "fusion_endpoint_summary.csv", fusion_summaries)
    write_csv(output / "context_stop_per_trial.csv", experiment_rows)
    write_csv(output / "context_stop_metrics.csv", metrics_rows)
    write_csv(output / "context_stop_sensitivity_per_trial.csv", stop_sensitivity_rows)
    write_csv(output / "context_stop_sensitivity_summary.csv", stop_sensitivity_summaries)
    write_csv(output / "controlled_scenario_summary.csv", controlled_rows)
    write_csv(output / "selected_policy_by_fold.csv", selected_configs)
    write_json(output / "v3_baseline_summary.json", {"reproduction": reproduction, "metrics": v3_summary})
    write_json(output / "context_forensic_audit.json", audit)
    write_json(output / "M12_M13_reproduction_summary.json", m12_reproduction)
    write_json(output / "input_manifest.json", manifest)
    write_json(output / "safety_checks.json", {
        "strongContradictoryEegPreservesRawTop": True,
        "strongContradictoryEegWindowsAcrossFixedNonzeroAlphaConflictPolicies": strong_conflict_window_count,
        "strongOverridePreservedWindowsAcrossFixedNonzeroAlphaConflictPolicies": strong_override_preserved_count,
        "heldOutConflictPriorWrongEarlyStops": conflict_wrong_early,
        "heldOutConflictPriorContextCausedErrors": conflict_context_caused,
        "fixedSensitivityConflictWrongEarlyStops": fixed_conflict_wrong_early,
        "fixedSensitivityConflictContextCausedErrors": fixed_conflict_context_errors,
        "fixedNonzeroAlphaConflictPolicyTrialEvaluations": len(fixed_conflict_checks),
        "allAlphaZeroIsEegOnly": True,
        "controlledLabelDerivedPriorsAreOfflineOnly": True,
    })

    report_lines = [
        "# M20 Context forensics and paired historical replay",
        "",
        "## Evidence boundary",
        "",
        "This is an offline replay of the same historical raw EEG and M6.5b score artifacts. The 89-trial analysis set contains 88 formal-manifest trials plus the B2 `m6_4-trial-023` replay supplement. Formal-only results are recomputed with that supplement removed before fitting each leave-one-session-out fold. No raw EEG or production M19 file is changed.",
        "",
        "Context is simulated from predeclared observable history. The target-aligned and wrong-target prior sweeps use the recorded target only to create named offline stress conditions; they do not represent a deployable prior generator.",
        "",
        "## Reproduction and source audit",
        "",
        "The historical V3 EEG-only and EEG+Context results were recomputed from raw EEG and match the saved 89-trial per-trial artifact exactly. The current M12/M13 paired replay was separately rerun into a new output directory and compared with the preserved paired artifact.",
        "",
        "See `v3_baseline_summary.json`, `M12_M13_reproduction_summary.json`, and `context_forensic_audit.json` for exact values and code-path details.",
        "",
        "## Controlled sweeps and stopping tests",
        "",
        "`fusion_endpoint_summary.csv` compares alpha 0/0.25/0.5/1/1.5 with current (`lambda=0.5`), reduced (`0.75`), and no (`1.0`) uniform softening across causal-history, neutral, target-aligned, and wrong-target priors. `controlled_scenario_summary.csv` reports all four target-aligned strengths (0.60/0.75/0.90/0.95) across all classes, plus the separate Green-history → Red-prior 0.90 example on red-target trials. FBCCA values remain uncalibrated CCA scores throughout.",
        "",
        "`context_stop_metrics.csv` reports cross-fitted EEG-only and Context-aware stop policies on the same held-out trials, with decision time converted to effective EEG evidence by subtracting the 0.5 s guard. Paired Context acceleration fields compare directly with the same-trial EEG-only policy; the separate `accelerated_fraction` field remains relative to the fixed 2.4 s endpoint. `controlled_scenario_summary.csv` reports aligned, neutral, conflict, and the requested Green-history → Red-prior 0.90 example. T@95/99/100 mean the earliest evidence time by which that fraction of all trials has completed correctly; an unreachable threshold is null.",
        "",
        "The stronger stop candidate requires a unique raw EEG top, a fused top equal to that raw top, a raw EEG top margin of at least {:.2f} when Context is active, fused top/margin thresholds, and three consecutive qualifying windows. EEG-only comparison policies retain two consecutive windows. A raw EEG margin at or above the frozen 0.20 threshold bypasses Context fusion. Selected policies are trained on two sessions and evaluated on the held-out session; the process is repeated for 88-formal and 89-all analyses.".format(CONTEXT_MIN_RAW_MARGIN),
        "`context_stop_sensitivity_summary.csv` separately reports a fixed cross-condition alpha/lambda sweep with those Context stability requirements. The fixed policy is never selected using the evaluated trial; its paired EEG baseline uses the same stop gates with Context disabled.",
        "",
        "## Held-out conflict safety",
        "",
        "- Conflict-prior wrong early stops: `{}`".format(conflict_wrong_early),
        "- Context-caused errors against paired EEG-only policy: `{}`".format(conflict_context_caused),
        "- Strong contradictory EEG windows preserved as raw EEG top: {}/{} across fixed nonzero-alpha conflict policies.".format(strong_override_preserved_count, strong_conflict_window_count),
        "- Fixed nonzero-alpha conflict-policy wrong early stops / Context-caused errors: {} / {} across {} held-out trial evaluations.".format(fixed_conflict_wrong_early, fixed_conflict_context_errors, len(fixed_conflict_checks)),
        "",
        "## Reproduction",
        "",
        "Run `python -m research_analysis.m20_context_forensics --output-dir research_analysis/m20_context_forensics_20260927/attempt-NN` with the repository `.venv`; the script refuses to overwrite an existing output directory.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(report_lines), encoding="utf-8")
    return {
        "status": "PASS",
        "outputDirectory": str(output),
        "v3Reproduction": reproduction,
        "m12M13Reproduction": m12_reproduction["status"],
        "heldOutConflictPriorWrongEarlyStops": conflict_wrong_early,
        "heldOutConflictPriorContextCausedErrors": conflict_context_caused,
        "fixedSensitivityConflictWrongEarlyStops": fixed_conflict_wrong_early,
        "fixedSensitivityConflictContextCausedErrors": fixed_conflict_context_errors,
        "strongConflictWindows": strong_conflict_window_count,
        "strongOverridePreservedWindows": strong_override_preserved_count,
        "outputFiles": sorted(path.name for path in output.iterdir() if path.is_file()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    result = run(output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
