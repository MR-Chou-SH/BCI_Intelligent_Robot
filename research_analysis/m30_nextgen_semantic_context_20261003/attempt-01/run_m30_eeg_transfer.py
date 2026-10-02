"""Transfer measured M30 semantic Context quality into frozen M25 EEG replay.

This is a seeded quality-transfer simulation, not prospective or causal human
EEG evidence. Semantic benchmark targets are used only after model evaluation
to align a measured three-candidate page with a historical EEG class.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
M25_RUN = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01/run_m25_analysis.py"
M25_MANIFEST = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01/INPUT_MANIFEST.json"
M25_SELECTION = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01/shared_threshold_outer_fold_selection.json"
M25_OPS = ("FAST", "MEDIUM", "CONSERVATIVE")
COVERAGE_OPS = ("C65", "C85", "C100")
LAMBDAS = (0.0, 0.5, 1.0, 1.5, 2.0)
PRIMARY_SEEDS = 100
TIMING_SEEDS = 30
BASE_SEED = 301003
TIMING_SCENARIOS = {
    "measured_api_latency": None,
    "available_after_0.2s": 0.2,
    "available_after_0.4s": 0.4,
}
TOL = 1e-9


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_csv_once(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path.name}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_m25():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("m25_readonly_m30_transfer", M25_RUN)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load frozen M25 replay helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_semantic_results() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, float]]:
    lock = json.loads((OUT / "semantic_context_sequence_benchmark_lock.json").read_text(encoding="utf-8"))
    metadata_rows = _read_jsonl(OUT / "semantic_context_results.jsonl")
    metadata = next((row for row in metadata_rows if row.get("recordType") == "m30_run_metadata"), None)
    rows = [row for row in metadata_rows
            if row.get("recordType") == "m30_semantic_context_result" and row.get("callKind") == "primary"]
    if metadata is None or metadata.get("benchmarkSha256") != lock.get("sha256"):
        raise ValueError("M30 semantic outputs do not match the frozen benchmark lock")
    if metadata.get("engineVersion") != "m30-context-precondition-guard-v1":
        raise ValueError("M30 transfer requires the audited precondition-guard engine run")
    if len(rows) != int(lock["decision_point_count"]) or len({(r["episodeId"], int(r["roundIndex"])) for r in rows}) != len(rows):
        raise ValueError("M30 semantic benchmark results are incomplete or duplicated")
    gate_doc = json.loads((OUT / "coverage_operating_points.json").read_text(encoding="utf-8"))
    if gate_doc.get("heldOutUsedForGateSelection") is not False:
        raise ValueError("M30 operating points were not calibrated independently of held-out rows")
    gates = {name: float(gate_doc["coverageOperatingPoints"][name]["confidenceThreshold"])
             for name in COVERAGE_OPS}
    return rows, lock, gates


def _confidence(row: dict[str, Any]) -> float | None:
    values = sorted((float(item["semantic_score"]) for item in row.get("candidateScores", [])
                     if isinstance(item, dict) and isinstance(item.get("semantic_score"), (int, float))), reverse=True)
    if len(values) < 2:
        return None
    return 0.5 * values[0] + 0.5 * (values[0] - values[1])


def _semantic_pools(rows: list[dict[str, Any]], gates: dict[str, float]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]]]:
    heldout = [row for row in rows if row.get("split") == "held_out"]
    if len(heldout) != 46:
        raise ValueError("expected 46 held-out semantic decision rows")
    pools: dict[str, list[dict[str, Any]]] = {}
    stats: dict[str, dict[str, Any]] = {}
    for op in COVERAGE_OPS:
        threshold = gates[op]
        active = []
        for row in heldout:
            confidence = _confidence(row)
            if row.get("predictedStatus") == "informative" and confidence is not None and confidence >= threshold:
                active.append(row)
        pools[op] = active
        eligible = [row for row in heldout if row.get("expectedStatus") == "informative"
                    and len(row.get("acceptableNextTargets") or []) == 1]
        on_eligible = sum(row in eligible for row in active)
        correct = sum(row.get("rankedTopCandidate") in (row.get("acceptableNextTargets") or [])
                      and row.get("expectedStatus") == "informative" for row in active)
        stats[op] = {
            "heldOutDecisionCount": len(heldout),
            "heldOutGateActiveCount": len(active),
            "heldOutGateActiveRateAll": len(active) / len(heldout),
            "heldOutEligibleInformativeCount": len(eligible),
            "heldOutGateActiveRateEligible": on_eligible / len(eligible) if eligible else None,
            "heldOutActivePrecision": correct / len(active) if active else None,
        }
    return pools, stats


def _map_semantic_page(row: dict[str, Any], true_index: int, rng: random.Random, classes: tuple[str, ...],
                       paginate_candidates, project_global_prior) -> dict[str, Any] | None:
    accepted = row.get("acceptableNextTargets") or []
    if row.get("expectedStatus") != "informative" or len(accepted) != 1:
        return None
    q_global = row.get("qGlobal") or []
    candidate_ids = [item.get("candidate_id") for item in q_global if isinstance(item, dict)]
    if len(candidate_ids) != len(q_global) or len(candidate_ids) < 3:
        return None
    target_id = accepted[0]
    if target_id not in candidate_ids:
        return None
    pages = paginate_candidates(candidate_ids, page_size=3)
    page_index = next((index for index, page in enumerate(pages) if target_id in page), None)
    if page_index is None or len(pages[page_index]) != 3:
        return None
    page_ids = pages[page_index]
    projection = project_global_prior(q_global, page_ids)
    prior_page = [float(value) for value in projection["q_page"]]
    if not math.isclose(sum(prior_page), 1.0, abs_tol=1e-9):
        raise AssertionError("page projection failed to preserve prior mass")
    target_position = page_ids.index(target_id)
    prior_top_position = max(range(3), key=lambda index: (prior_page[index], -index))
    offset = (prior_top_position - target_position) % 3
    correct_top = offset == 0
    class_for_candidate = {target_id: int(true_index)}
    if not correct_top:
        class_for_candidate[page_ids[prior_top_position]] = (int(true_index) + offset) % 3
    unused_candidates = [item for item in page_ids if item not in class_for_candidate]
    unused_classes = [index for index in range(3) if index not in class_for_candidate.values()]
    rng.shuffle(unused_classes)
    class_for_candidate.update(zip(unused_candidates, unused_classes))
    prior_eeg = [0.0, 0.0, 0.0]
    for candidate, value in zip(page_ids, prior_page):
        prior_eeg[class_for_candidate[candidate]] = value
    return {
        "prior": prior_eeg,
        "pageCandidateIds": page_ids,
        "qPage": prior_page,
        "pageIndex": page_index,
        "targetId": target_id,
        "pageTopId": page_ids[prior_top_position],
        "pageTopCorrect": correct_top,
        "semanticCaseId": row["episodeId"],
        "semanticRoundIndex": int(row["roundIndex"]),
        "sceneFamily": row["sceneFamily"],
        "apiLatencySeconds": float(row.get("apiLatencyMs") or 0.0) / 1000.0,
        "priorTopMass": max(prior_page),
    }


def _assignment_for_trial(m25, trial: dict[str, Any], pool: list[dict[str, Any]], gate_rate: float,
                          seed: int, coverage_op: str, paginate_candidates, project_global_prior) -> dict[str, Any]:
    rng = random.Random(int.from_bytes(hashlib.sha256(
        f"M30_assignment|{coverage_op}|{seed}|{trial['session']}|{trial['trialId']}".encode("utf-8")
    ).digest()[:8], "big"))
    if not pool or rng.random() >= gate_rate:
        return {"gateActive": False, "assignment": None, "mapFailure": False}
    semantic_row = rng.choice(pool)
    assignment = _map_semantic_page(semantic_row, int(trial["trueClassIndex"]), rng,
                                    tuple(m25.CLASSES), paginate_candidates, project_global_prior)
    return {"gateActive": True, "assignment": assignment, "mapFailure": assignment is None,
            "semanticRow": semantic_row}


def _fuse_lambda(eeg: list[float], prior: list[float], lambda_ctx: float) -> list[float]:
    if lambda_ctx == 0.0:
        return [float(value) for value in eeg]
    raw = [max(0.0, float(eeg[index])) * max(1e-15, float(prior[index])) ** lambda_ctx for index in range(3)]
    total = sum(raw)
    return [float(value) for value in eeg] if total <= 0.0 else [value / total for value in raw]


def _simulate(m25, trial: dict[str, Any], baseline: dict[str, Any], assignment: dict[str, Any] | None,
              availability_seconds: float, lambda_ctx: float, parameters: dict[str, Any]) -> dict[str, Any]:
    baseline_stop = float(baseline["stopTimeSeconds"])
    baseline_class = int(baseline["selectedClassIndex"])
    if assignment is None or lambda_ctx == 0.0:
        return {"stop": baseline_stop, "selectedIndex": baseline_class, "contextApplied": False,
                "authorizedPointCount": 0, "examinedPointCount": 0, "wrongEarlyStop": False,
                "contextCausedError": False, "fallbackReason": "no_aligned_context" if assignment is None else "lambda_zero_exact_eeg_only"}
    prior = assignment["prior"]
    prior_top = max(range(3), key=lambda index: (prior[index], -index))
    prior_mass = float(prior[prior_top])
    candidate: int | None = None
    stable_start: float | None = None
    authorized = 0
    examined = 0
    for point in trial["pointsByGrid"]["0.10"]:
        at = float(point["effectiveEvidenceTimeSeconds"])
        if at > baseline_stop + TOL:
            break
        examined += 1
        raw_top, _, _, eeg = m25.raw_top_margin(point)
        if at < availability_seconds - TOL or prior_mass < 0.70 - TOL or prior_top != raw_top:
            candidate = None
            stable_start = None
            continue
        authorized += 1
        fused = _fuse_lambda(eeg, prior, lambda_ctx)
        order = sorted(range(3), key=lambda index: (-fused[index], index))
        fused_top, fused_second = order[:2]
        eligible = (
            at >= float(parameters["minimumEvidenceSeconds"]) - TOL
            and fused_top == raw_top
            and fused[raw_top] >= float(parameters["topThreshold"]) - TOL
            and fused[raw_top] - fused[fused_second] >= float(parameters["marginThreshold"]) - TOL
        )
        if eligible:
            if candidate != raw_top:
                candidate = raw_top
                stable_start = at
            if stable_start is not None and at - stable_start >= float(parameters["stabilitySeconds"]) - TOL:
                if at < baseline_stop - TOL:
                    wrong = raw_top != int(trial["trueClassIndex"])
                    return {"stop": at, "selectedIndex": raw_top, "contextApplied": True,
                            "authorizedPointCount": authorized, "examinedPointCount": examined,
                            "wrongEarlyStop": bool(wrong),
                            "contextCausedError": bool(wrong and baseline["correct"]), "fallbackReason": ""}
                break
        else:
            candidate = None
            stable_start = None
    return {"stop": baseline_stop, "selectedIndex": baseline_class, "contextApplied": False,
            "authorizedPointCount": authorized, "examinedPointCount": examined, "wrongEarlyStop": False,
            "contextCausedError": False, "fallbackReason": "paired_baseline_cap_or_no_stable_crossing"}


def _new_accumulator() -> dict[str, Any]:
    return {"n": 0, "gainSum": 0.0, "gains": [], "activeGains": [], "baselineCorrect": 0,
            "contextCorrect": 0, "gateActive": 0, "assignmentActive": 0, "mapFailure": 0,
            "contextApplied": 0, "wrongEarlyStop": 0, "contextCausedError": 0, "noDelay": 0,
            "accelerated": 0, "unchanged": 0, "delayed": 0, "authorizedRateSum": 0.0,
            "authorizedRateN": 0, "semanticTopCorrect": 0, "semanticTopN": 0, "priorTopMassSum": 0.0}


def _add_metrics(acc: dict[str, Any], row: dict[str, Any]) -> None:
    gain = float(row["pairedGainSeconds"])
    acc["n"] += 1
    acc["gainSum"] += gain
    acc["gains"].append(gain)
    acc["baselineCorrect"] += int(bool(row["baselineCorrect"]))
    acc["contextCorrect"] += int(bool(row["contextCorrect"]))
    acc["gateActive"] += int(bool(row["semanticGateActive"]))
    acc["assignmentActive"] += int(bool(row["semanticAssignmentActive"]))
    acc["mapFailure"] += int(bool(row["alignmentFallback"]))
    acc["contextApplied"] += int(bool(row["contextApplied"]))
    acc["wrongEarlyStop"] += int(bool(row["wrongEarlyStop"]))
    acc["contextCausedError"] += int(bool(row["contextCausedError"]))
    acc["noDelay"] += int(float(row["contextStopSeconds"]) <= float(row["baselineStopSeconds"]) + TOL)
    acc["accelerated"] += int(gain > TOL)
    acc["unchanged"] += int(abs(gain) <= TOL)
    acc["delayed"] += int(gain < -TOL)
    if row["contextApplied"]:
        acc["activeGains"].append(gain)
    if row["semanticAssignmentActive"]:
        acc["authorizedRateSum"] += float(row["authorizedPointRate"] or 0.0)
        acc["authorizedRateN"] += 1
        acc["semanticTopCorrect"] += int(bool(row["semanticPageTopCorrect"]))
        acc["semanticTopN"] += 1
        acc["priorTopMassSum"] += float(row["semanticPagePriorTopMass"] or 0.0)


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _summary_row(key: tuple[str, str, float, str], acc: dict[str, Any], *, seed_means: list[float],
                 active_seed_means: list[float], expected_active_rate: float,
                 semantic_stats: dict[str, Any]) -> dict[str, Any]:
    scenario, coverage_op, lambda_ctx, eeg_op = key
    n = acc["n"]
    active_n = len(acc["activeGains"])
    baseline_accuracy = acc["baselineCorrect"] / n if n else None
    context_accuracy = acc["contextCorrect"] / n if n else None
    accuracy_delta = (context_accuracy - baseline_accuracy) if n else None
    active_mean = statistics.mean(acc["activeGains"]) if active_n else None
    active_median = statistics.median(acc["activeGains"]) if active_n else None
    reach_04 = active_mean is not None and active_mean >= 0.40
    reach_05 = active_mean is not None and active_mean >= 0.50
    safe = (accuracy_delta is not None and accuracy_delta >= -TOL and acc["wrongEarlyStop"] == 0
            and acc["contextCausedError"] == 0 and acc["delayed"] == 0 and acc["noDelay"] == n)
    return {
        "scenario": scenario, "coverageOperatingPoint": coverage_op, "lambdaCtx": lambda_ctx,
        "operatingPoint": eeg_op, "seedCount": len(seed_means), "trialRows": n,
        "heldOutGateActiveRate": semantic_stats["heldOutGateActiveRateAll"],
        "heldOutEligibleParticipation": semantic_stats["heldOutGateActiveRateEligible"],
        "heldOutActivePrecision": semantic_stats["heldOutActivePrecision"],
        "mappedAssignmentRate": acc["assignmentActive"] / n if n else None,
        "alignmentFallbackRate": acc["mapFailure"] / n if n else None,
        "meanContextAuthorizedPointRateWhenAssigned": acc["authorizedRateSum"] / acc["authorizedRateN"] if acc["authorizedRateN"] else None,
        "meanSemanticPageTopCorrectWhenAssigned": acc["semanticTopCorrect"] / acc["semanticTopN"] if acc["semanticTopN"] else None,
        "meanSemanticPagePriorTopMassWhenAssigned": acc["priorTopMassSum"] / acc["semanticTopN"] if acc["semanticTopN"] else None,
        "contextAppliedRate": acc["contextApplied"] / n if n else None,
        "acceleratedTrialCount": acc["accelerated"], "unchangedTrialCount": acc["unchanged"],
        "delayedTrialCount": acc["delayed"], "noDelayViolationCount": n - acc["noDelay"],
        "wrongEarlyStopCount": acc["wrongEarlyStop"], "contextCausedErrorCount": acc["contextCausedError"],
        "baselineAccuracy": baseline_accuracy, "contextAccuracy": context_accuracy,
        "accuracyDeltaVsPairedBaseline": accuracy_delta,
        "meanAllTrialGainSeconds": acc["gainSum"] / n if n else None,
        "medianAllTrialGainSeconds": statistics.median(acc["gains"]) if acc["gains"] else None,
        "meanContextActiveGainSeconds": active_mean,
        "medianContextActiveGainSeconds": active_median,
        "activeGainMeanAcrossSeedsLower95": _quantile(active_seed_means, 0.025),
        "activeGainMeanAcrossSeedsUpper95": _quantile(active_seed_means, 0.975),
        "allTrialGainMeanAcrossSeedsLower95": _quantile(seed_means, 0.025),
        "allTrialGainMeanAcrossSeedsUpper95": _quantile(seed_means, 0.975),
        "reaches0_4sContextActiveGain": bool(reach_04), "reaches0_5sContextActiveGain": bool(reach_05),
        "supportedInTransferSimulation": bool(safe and reach_04),
        "evidenceLabel": "NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
    }


def _record_trial(writer: csv.DictWriter, row: dict[str, Any], aggregates: dict[Any, dict[str, Any]],
                  by_seed: dict[Any, dict[str, Any]], subgroup: dict[Any, dict[str, Any]]) -> None:
    writer.writerow(row)
    key = (row["scenario"], row["coverageOperatingPoint"], float(row["lambdaCtx"]), row["operatingPoint"])
    _add_metrics(aggregates.setdefault(key, _new_accumulator()), row)
    seed_key = key + (int(row["seed"]),)
    _add_metrics(by_seed.setdefault(seed_key, _new_accumulator()), row)
    for kind, value in (("session", row["session"]), ("sceneFamily", row["semanticSceneFamily"] or "NO_ALIGNED_CONTEXT")):
        subgroup.setdefault((key, kind, value), _new_accumulator())
        _add_metrics(subgroup[(key, kind, value)], row)


def _run_replay(m25, trials: list[dict[str, Any]], baselines: dict[tuple[str, str, str], dict[str, Any]],
                selected: dict[tuple[str, str], dict[str, Any]], pools: dict[str, list[dict[str, Any]]],
                semantic_stats: dict[str, dict[str, Any]], paginate_candidates, project_global_prior) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    output = OUT / "eeg_transfer_per_trial.csv"
    if output.exists():
        raise FileExistsError(f"refusing to overwrite transfer output: {output.name}")
    aggregate: dict[Any, dict[str, Any]] = {}
    by_seed: dict[Any, dict[str, Any]] = {}
    subgroup: dict[Any, dict[str, Any]] = {}
    rows_written = 0
    with output.open("x", encoding="utf-8-sig", newline="") as handle:
        writer: csv.DictWriter | None = None

        def emit(coverage_op: str, scenario: str, lambda_ctx: float, seed: int,
                 trial: dict[str, Any], picked: dict[str, Any]) -> None:
            nonlocal writer, rows_written
            assignment = picked["assignment"]
            baseline = baselines[(M25_OPS[0], trial["session"], trial["trialId"])]
            # Each operating point has its own paired baseline and frozen thresholds.
            for eeg_op in M25_OPS:
                baseline = baselines[(eeg_op, trial["session"], trial["trialId"])]
                parameters = selected[(trial["session"], eeg_op)]
                if parameters is None:
                    raise ValueError(f"M25 has no frozen shared parameters for {trial['session']}/{eeg_op}")
                available = 0.0
                if scenario == "measured_api_latency" and assignment is not None:
                    available = float(assignment["apiLatencySeconds"])
                elif scenario.startswith("available_after_"):
                    available = float(scenario.removeprefix("available_after_").removesuffix("s"))
                result = _simulate(m25, trial, baseline, assignment, available, lambda_ctx, parameters)
                baseline_stop = float(baseline["stopTimeSeconds"])
                stop = float(result["stop"])
                true_index = int(trial["trueClassIndex"])
                if stop > baseline_stop + TOL:
                    raise AssertionError("M30 Context transfer delayed its paired EEG-only stop")
                row = {
                    "recordType": "NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
                    "conditionType": "primary_matrix" if scenario == "precomputed_before_eeg" else "timing_sensitivity",
                    "scenario": scenario, "coverageOperatingPoint": coverage_op, "lambdaCtx": lambda_ctx,
                    "operatingPoint": eeg_op, "seed": seed, "session": trial["session"],
                    "trialId": trial["trialId"], "trueClass": m25.CLASSES[true_index],
                    "baselineStopSeconds": baseline_stop, "contextStopSeconds": stop,
                    "pairedGainSeconds": baseline_stop - stop,
                    "baselineCorrect": int(int(baseline["selectedClassIndex"]) == true_index),
                    "contextCorrect": int(int(result["selectedIndex"]) == true_index),
                    "selectedClass": m25.CLASSES[int(result["selectedIndex"])],
                    "semanticGateActive": int(bool(picked["gateActive"])),
                    "semanticAssignmentActive": int(assignment is not None),
                    "alignmentFallback": int(bool(picked["mapFailure"])),
                    "contextApplied": int(bool(result["contextApplied"])),
                    "authorizedPointCount": int(result["authorizedPointCount"]),
                    "examinedPointCount": int(result["examinedPointCount"]),
                    "authorizedPointRate": (float(result["authorizedPointCount"]) / max(int(result["examinedPointCount"]), 1)
                                             if assignment is not None else 0.0),
                    "wrongEarlyStop": int(bool(result["wrongEarlyStop"])),
                    "contextCausedError": int(bool(result["contextCausedError"])),
                    "contextAvailableFromSeconds": available if assignment is not None else "",
                    "semanticCaseId": assignment["semanticCaseId"] if assignment else "",
                    "semanticRoundIndex": assignment["semanticRoundIndex"] if assignment else "",
                    "semanticSceneFamily": assignment["sceneFamily"] if assignment else "",
                    "semanticPageIndex": assignment["pageIndex"] if assignment else "",
                    "semanticPageCandidates": json.dumps(assignment["pageCandidateIds"], ensure_ascii=False) if assignment else "",
                    "semanticQPage": json.dumps(assignment["qPage"], separators=(",", ":")) if assignment else "",
                    "semanticPageTopCorrect": int(assignment["pageTopCorrect"]) if assignment else "",
                    "semanticPagePriorTopMass": assignment["priorTopMass"] if assignment else "",
                    "sharedThresholdTop": parameters["topThreshold"],
                    "sharedThresholdMargin": parameters["marginThreshold"],
                    "sharedMinimumEvidenceSeconds": parameters["minimumEvidenceSeconds"],
                    "stabilitySeconds": parameters["stabilitySeconds"],
                    "fallbackReason": result["fallbackReason"],
                }
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(row), extrasaction="ignore")
                    writer.writeheader()
                _record_trial(writer, row, aggregate, by_seed, subgroup)
                rows_written += 1

        for coverage_op in COVERAGE_OPS:
            pool = pools[coverage_op]
            gate_rate = len(pool) / 46.0
            for seed_index in range(PRIMARY_SEEDS):
                seed = BASE_SEED + seed_index
                picked_by_trial = {
                    (trial["session"], trial["trialId"]): _assignment_for_trial(
                        m25, trial, pool, gate_rate, seed, coverage_op, paginate_candidates, project_global_prior
                    ) for trial in trials
                }
                for trial in trials:
                    picked = picked_by_trial[(trial["session"], trial["trialId"])]
                    for lambda_ctx in LAMBDAS:
                        emit(coverage_op, "precomputed_before_eeg", lambda_ctx, seed, trial, picked)
                if (seed_index + 1) % 10 == 0:
                    print(f"M30 primary transfer {coverage_op}: seeds {seed_index+1}/{PRIMARY_SEEDS}", flush=True)
            for coverage_op_scenario, fixed in TIMING_SCENARIOS.items():
                for seed_index in range(TIMING_SEEDS):
                    seed = BASE_SEED + seed_index
                    picked_by_trial = {
                        (trial["session"], trial["trialId"]): _assignment_for_trial(
                            m25, trial, pool, gate_rate, seed, coverage_op, paginate_candidates, project_global_prior
                        ) for trial in trials
                    }
                    for trial in trials:
                        picked = picked_by_trial[(trial["session"], trial["trialId"])]
                        # Timing sensitivity is predeclared at the nominal lambda=1.0.
                        emit(coverage_op, coverage_op_scenario, 1.0, seed, trial, picked)
                    if (seed_index + 1) % 10 == 0:
                        print(f"M30 timing {coverage_op}/{coverage_op_scenario}: seeds {seed_index+1}/{TIMING_SEEDS}", flush=True)
    if rows_written != (len(trials) * M25_OPS.__len__() * (
            len(COVERAGE_OPS) * PRIMARY_SEEDS * len(LAMBDAS)
            + len(COVERAGE_OPS) * TIMING_SEEDS * len(TIMING_SCENARIOS))):
        raise AssertionError(f"unexpected M30 transfer row count: {rows_written}")

    seed_metrics: dict[Any, dict[str, float]] = {}
    for seed_key, acc in by_seed.items():
        seed_metrics[seed_key[:-1]] = seed_metrics.get(seed_key[:-1], [])
        mean_gain = acc["gainSum"] / acc["n"]
        active_mean = statistics.mean(acc["activeGains"]) if acc["activeGains"] else None
        seed_metrics[seed_key[:-1]].append({"all": mean_gain, "active": active_mean})
    summaries = []
    for key, acc in sorted(aggregate.items(), key=lambda item: item[0]):
        seed_rows = seed_metrics[key]
        all_seed_means = [row["all"] for row in seed_rows]
        active_seed_means = [row["active"] for row in seed_rows if row["active"] is not None]
        summaries.append(_summary_row(key, acc, seed_means=all_seed_means,
                                      active_seed_means=active_seed_means,
                                      expected_active_rate=0.0,
                                      semantic_stats=semantic_stats[key[1]]))
    _write_csv_once(OUT / "eeg_transfer_summary.csv", summaries)

    subgroup_rows = []
    for (key, group_kind, group_value), acc in sorted(subgroup.items(), key=lambda item: (item[0][0], item[0][1], item[0][2])):
        subgroup_rows.append({
            "scenario": key[0], "coverageOperatingPoint": key[1], "lambdaCtx": key[2],
            "operatingPoint": key[3], "groupKind": group_kind, "groupValue": group_value,
            "trialRows": acc["n"], "baselineAccuracy": acc["baselineCorrect"] / acc["n"] if acc["n"] else None,
            "contextAccuracy": acc["contextCorrect"] / acc["n"] if acc["n"] else None,
            "accuracyDelta": (acc["contextCorrect"] - acc["baselineCorrect"]) / acc["n"] if acc["n"] else None,
            "meanAllTrialGainSeconds": acc["gainSum"] / acc["n"] if acc["n"] else None,
            "meanContextActiveGainSeconds": statistics.mean(acc["activeGains"]) if acc["activeGains"] else None,
            "semanticAssignmentRate": acc["assignmentActive"] / acc["n"] if acc["n"] else None,
            "contextAppliedRate": acc["contextApplied"] / acc["n"] if acc["n"] else None,
            "wrongEarlyStopCount": acc["wrongEarlyStop"], "noDelayViolationCount": acc["n"] - acc["noDelay"],
            "evidenceLabel": "NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
        })
    _write_csv_once(OUT / "eeg_transfer_by_session.csv", [row for row in subgroup_rows if row["groupKind"] == "session"])
    _write_csv_once(OUT / "eeg_transfer_by_scene_family.csv", [row for row in subgroup_rows if row["groupKind"] == "sceneFamily"])
    return summaries, subgroup_rows, semantic_stats


def _m28_comparison(summaries: list[dict[str, Any]], semantic_stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    old_sem = json.loads((ROOT / "research_analysis/m28_real_semantic_context_20261002/attempt-01/semantic_context_benchmark_summary.json").read_text(encoding="utf-8"))
    old_transfer_path = ROOT / "research_analysis/m28_real_semantic_context_20261002/attempt-01/eeg_transfer_summary.csv"
    with old_transfer_path.open("r", encoding="utf-8-sig", newline="") as handle:
        old_transfer = list(csv.DictReader(handle))
    old_precomputed = {row["operatingPoint"]: row for row in old_transfer if row["scenario"] == "precomputed_before_eeg"}
    m30_coverage_rows = {}
    with (OUT / "coverage_precision_summary.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["split"] == "held_out":
                m30_coverage_rows[row["coverageOperatingPoint"]] = row
    comparisons = []
    for coverage_op in COVERAGE_OPS:
        for eeg_op in M25_OPS:
            current = next(row for row in summaries if row["scenario"] == "precomputed_before_eeg"
                           and row["coverageOperatingPoint"] == coverage_op and float(row["lambdaCtx"]) == 1.0
                           and row["operatingPoint"] == eeg_op)
            previous = old_precomputed.get(eeg_op, {})
            sem = m30_coverage_rows[coverage_op]
            comparisons.append({
                "eegOperatingPoint": eeg_op, "m30CoverageOperatingPoint": coverage_op,
                "M28Architecture": "static/pre-tag-heavy curated semantic cases",
                "M28SemanticCaseCount": old_sem.get("case_count"),
                "M28HeldOutTop1": old_sem.get("scene_split_summary", {}).get("held_out", {}).get("top1_correct", {}).get("rate"),
                "M28OverallActivePrecision": old_sem.get("active_context_precision", {}).get("rate"),
                "M28ActiveCoverageAll": old_sem.get("active_context_coverage", {}).get("rate"),
                "M28AmbiguityAccuracy": old_sem.get("ambiguity_accuracy", {}).get("rate"),
                "M28InvalidRejection": old_sem.get("invalid_case_rejection", {}).get("rate"),
                "M28PrecomputedContextActiveGainSeconds": float(previous.get("mean_context_active_gain_seconds") or 0.0),
                "M28PrecomputedAllTrialGainSeconds": float(previous.get("mean_all_trial_gain_seconds") or 0.0),
                "M28WrongEarlyStopCount": int(float(previous.get("wrong_early_stop_count") or 0)),
                "M30Architecture": "sequential, relational, variable-dimensional q_global + deterministic 3-choice page projection",
                "M30HeldOutDecisionCount": semantic_stats[coverage_op]["heldOutDecisionCount"],
                "M30HeldOutTop1": json.loads((OUT / "semantic_context_summary.json").read_text(encoding="utf-8"))["splitMetrics"]["held_out"]["informativeTop1Accuracy"],
                "M30HeldOutActivePrecision": float(sem["activeContextPrecision"]),
                "M30HeldOutOverallParticipation": float(sem["overallParticipation"]),
                "M30HeldOutEligibleParticipation": float(sem["eligibleParticipation"]),
                "M30HeldOutAmbiguityAccuracy": float(sem["ambiguityHandlingAccuracy"]),
                "M30HeldOutInvalidRejection": float(sem["invalidRejectionRate"]),
                "M30PrecomputedLambda1ContextActiveGainSeconds": current["meanContextActiveGainSeconds"],
                "M30PrecomputedLambda1AllTrialGainSeconds": current["meanAllTrialGainSeconds"],
                "M30PrecomputedLambda1WrongEarlyStopCount": current["wrongEarlyStopCount"],
                "M30EvidenceLabel": "NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
                "ComparisonWarning": "M28 and M30 use different curated benchmark designs; transfer is randomized-trial simulation, not prospective semantic EEG.",
            })
    _write_csv_once(OUT / "m28_vs_m30_comparison.csv", comparisons)
    return comparisons


def _validate_m25_unchanged(m25_manifest_before: str, source_entries: list[dict[str, Any]]) -> bool:
    if _sha256(M25_MANIFEST) != m25_manifest_before:
        return False
    return all((ROOT / entry["path"]).is_file()
               and (ROOT / entry["path"]).stat().st_size == int(entry["sizeBytes"])
               and _sha256(ROOT / entry["path"]) == entry["sha256"] for entry in source_entries)


def run() -> dict[str, Any]:
    required_outputs = ("eeg_transfer_per_trial.csv", "eeg_transfer_summary.csv", "coverage_lambda_eeg_matrix.csv",
                        "m28_vs_m30_comparison.csv", "eeg_transfer_by_session.csv", "eeg_transfer_by_scene_family.csv",
                        "eeg_transfer_spec.md", "eeg_transfer_validation.json")
    existing = [name for name in required_outputs if (OUT / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing M30 EEG transfer outputs: {existing}")
    sys.path.insert(0, str(ROOT))
    from integration.semantic_context_sequence import paginate_candidates, project_global_prior

    before_manifest_hash = _sha256(M25_MANIFEST)
    m25 = _load_m25()
    trials, manifest, folds, baseline_by, _assignment_by, _m24_rows, _m24_by = m25.load_inputs()
    cohort = m25.validate_cohort(trials, manifest)
    selection = json.loads(M25_SELECTION.read_text(encoding="utf-8"))
    if selection.get("selectionUsesHeldOutSession") is not False:
        raise ValueError("M25 operating-point selection used a held-out session")
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    baselines: dict[tuple[str, str, str], dict[str, Any]] = {}
    baseline_matches = 0
    for session in m25.SESSIONS:
        fold = folds[session]
        for eeg_op in M25_OPS:
            selected[(session, eeg_op)] = selection["folds"][f"{m25.FROZEN_BRANCH}|{session}|{eeg_op}"]["selectedSharedContextParameters"]
        for trial in (item for item in trials if item["session"] == session):
            for eeg_op in M25_OPS:
                config = m25.op_config(fold, eeg_op)
                baseline = m25.raw_stop(trial, "0.10", float(config["topThreshold"]),
                                        float(config["marginThreshold"]), 0.50, m25.STABILITY_PRIMARY)
                stored = baseline_by[(eeg_op, session, trial["trialId"])]
                if (not math.isclose(float(baseline["stopTimeSeconds"]), float(stored["stopTimeSeconds"]), abs_tol=1e-9)
                        or baseline["selectedClass"] != stored["selectedClass"]):
                    raise AssertionError(f"frozen M25 baseline mismatch: {session}/{eeg_op}/{trial['trialId']}")
                baselines[(eeg_op, session, trial["trialId"])] = baseline
                baseline_matches += 1
    if baseline_matches != 264:
        raise AssertionError("expected 88 trials × 3 M25 operating points")

    semantic_rows, benchmark_lock, gates = _load_semantic_results()
    pools, semantic_stats = _semantic_pools(semantic_rows, gates)
    _run_replay(m25, trials, baselines, selected, pools, semantic_stats, paginate_candidates, project_global_prior)
    with (OUT / "eeg_transfer_summary.csv").open("r", encoding="utf-8-sig", newline="") as handle:
        summaries = list(csv.DictReader(handle))
    matrix = [row for row in summaries if row["scenario"] == "precomputed_before_eeg"]
    _write_csv_once(OUT / "coverage_lambda_eeg_matrix.csv", matrix)
    comparisons = _m28_comparison(summaries, semantic_stats)
    (OUT / "eeg_transfer_spec.md").open("x", encoding="utf-8").write(
        """# M30 EEG transfer specification

Evidence label: `NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION`. The 88 historical M25 EEG trials were randomized and do not have prospective M30 scene/intent labels. No raw waveform was read. The M25 frozen feature/probability replay and its selected shared thresholds remain unchanged.

The semantic inputs are only M30 HELD-OUT predictions, with C65/C85/C100 confidence thresholds fit on TRAIN+DEV. The transfer uses the empirical held-out gate-active rate and samples only those measured active rows. To map a semantic q_global onto a three-class historical EEG trial, the simulation locates the unique independently authored target on its deterministic three-choice page, projects and renormalizes q_global to that page, then maps the target to the historical true class. A wrong page-top preserves its measured page-relative top-error offset under seeded class assignment. Active rows without one unique target or without a full three-choice target page are counted as alignment fallback; this alignment is simulation machinery unavailable to a live system.

Primary matrix: 100 deterministic seeds × 3 measured coverage gates × 5 lambda_ctx values × 3 frozen M25 operating points; Context is available before EEG. Timing sensitivity: 30 matched seeds at lambda_ctx=1.0 for measured API arrival and fixed 0.2/0.4 s arrivals. `lambda_ctx=0` is an exact paired EEG-only baseline. For lambda>0, Context can accelerate only when projected prior top mass is at least 0.70 and its top agrees with the instantaneous raw EEG top. M25 minimum evidence, selected shared top/margin thresholds, and stability duration are preserved. The stop is capped at the paired EEG-only stop; the emitted class remains the current raw EEG top.

This is not causal evidence, not a prospective human-task EEG test, and not deployment validation. Held-out semantic performance was already observed in earlier prompt iterations; results are exploratory. No gate or lambda is selected from held-out transfer outcomes.
""")

    after_ok = _validate_m25_unchanged(before_manifest_hash, manifest["inputs"])
    if not after_ok:
        raise AssertionError("a frozen M25 input changed during read-only transfer replay")
    summary_map = {(row["scenario"], row["coverageOperatingPoint"], float(row["lambdaCtx"]), row["operatingPoint"]): row
                   for row in summaries}
    no_delay = sum(int(float(row["noDelayViolationCount"])) for row in summaries)
    all_wrong = sum(int(float(row["wrongEarlyStopCount"])) for row in summaries)
    validation = {
        "recordType": "m30_eeg_transfer_validation", "status": "PASS",
        "benchmarkSha256": benchmark_lock["sha256"], "m30EngineVersion": "m30-context-precondition-guard-v1",
        "m25ManifestSha256Before": before_manifest_hash, "m25ManifestSha256After": _sha256(M25_MANIFEST),
        "m25FrozenInputFingerprintsUnchanged": after_ok, "m25Cohort": cohort,
        "m25BaselineReproductions": baseline_matches, "m25BaselineExpected": 264,
        "primarySeedCount": PRIMARY_SEEDS, "timingSensitivitySeedCount": TIMING_SEEDS,
        "coverageOperatingPoints": list(COVERAGE_OPS), "lambdaCtxValues": list(LAMBDAS),
        "eegOperatingPoints": list(M25_OPS), "primaryConditionCount": len(COVERAGE_OPS) * len(LAMBDAS) * len(M25_OPS),
        "primaryTrialRowCount": len(trials) * PRIMARY_SEEDS * len(COVERAGE_OPS) * len(LAMBDAS) * len(M25_OPS),
        "timingTrialRowCount": len(trials) * TIMING_SEEDS * len(COVERAGE_OPS) * len(TIMING_SCENARIOS) * len(M25_OPS),
        "noDelayViolationCount": no_delay, "wrongEarlyStopCount": all_wrong,
        "heldOutSemanticGateStats": semantic_stats,
        "evidenceLabel": "NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION",
        "prospectiveCausalEvidence": False,
        "allPrimaryConditionsPresent": len([row for row in summaries if row["scenario"] == "precomputed_before_eeg"]) == 45,
        "m28ComparisonRows": len(comparisons),
    }
    validation["status"] = "PASS" if after_ok and no_delay == 0 and validation["allPrimaryConditionsPresent"] else "FAIL"
    with (OUT / "eeg_transfer_validation.json").open("x", encoding="utf-8") as handle:
        json.dump(validation, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    if validation["status"] != "PASS":
        raise AssertionError("M30 EEG transfer validation failed")
    return validation


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
