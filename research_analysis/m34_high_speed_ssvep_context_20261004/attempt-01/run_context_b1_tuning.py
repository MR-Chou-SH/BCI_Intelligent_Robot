"""Tune conservative M33 authorization on M34 Dev B1 only.

Historical semantic/EEG pairing is a seeded transfer simulation. Semantic
answer labels are used only to align synthetic page positions to EEG slots;
they are never passed to the Context model or EEG decoder.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from integration.semantic_context_sequence import paginate_candidates, project_global_prior  # noqa: E402


OUT = Path(__file__).resolve().parent
M33 = ROOT / "research_analysis/m33_task_state_context_fix_20261003/attempt-01"
M33_GATE_PATH = M33 / "train_dev_gate.json"
M33_RESULTS_PATH = M33 / "train_dev_results.jsonl"
M30_TRANSFER = ROOT / "research_analysis/m30_nextgen_semantic_context_20261003/attempt-01/run_m30_eeg_transfer.py"
PROTOCOL = json.loads((OUT / "frozen_protocol.json").read_text(encoding="utf-8"))
CONFIG = PROTOCOL["contextIntegration"]["b1Tuning"]
WINDOWS = [float(value) for value in PROTOCOL["fixedEvidenceWindowsSeconds"]]
BASE_SEED = 34004
TOL = 1e-12


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_transfer_helper():
    spec = importlib.util.spec_from_file_location("m30_readonly_transfer_helpers_m34", M30_TRANSFER)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load the existing M30/M33 semantic page-alignment helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def semantic_gate_rows():
    gate = json.loads(M33_GATE_PATH.read_text(encoding="utf-8"))
    threshold = float(gate["threshold"])
    rows = [row for row in read_jsonl(M33_RESULTS_PATH) if row.get("record_type") == "m33_semantic_result"]
    active = [row for row in rows if row.get("split") in ("train", "dev")
              and row.get("predicted_status") == "informative"
              and row.get("eligible_informative") is True
              and float(row.get("gate_features", {}).get("conservative_confidence", -1.0)) >= threshold]
    if gate.get("held_out_used_for_gate_selection") is not False or not rows or not active:
        raise ValueError("M33 train/dev gate is not a valid frozen input")
    pool = [{
        "expectedStatus": row["expected_status"],
        "acceptableNextTargets": row["acceptable_next_targets"],
        "qGlobal": row["q_global"],
        "episodeId": row["episode_id"],
        "roundIndex": int(row["round_index"]),
        "sceneFamily": row.get("scene_family", ""),
        "apiLatencyMs": float(row.get("provenance", {}).get("api_latency_ms", 0.0) or 0.0),
        "semanticGateFeature": float(row["gate_features"]["conservative_confidence"]),
    } for row in active]
    return pool, len(rows), gate


def load_b1_eeg():
    selected = json.loads((OUT / "selected_eeg_backbones.json").read_text(encoding="utf-8"))
    config_id = selected["selected"]["FBCCA"]["configId"]
    traces = {}
    with (OUT / "train_dev_search_per_trial.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["configId"] != config_id or row["sessionKey"] != "B1":
                continue
            trial = traces.setdefault(row["trialId"], {"true": int(row["trueSlotIndex"]), "points": {}})
            window = float(row["evidenceSeconds"])
            scores = [float(value) for value in json.loads(row["scoresBySlot"])]
            order = sorted(range(3), key=lambda index: (-scores[index], index))
            top, second = order[:2]
            margin = (scores[top] - scores[second]) / max(abs(scores[top]), 1e-12)
            trial["points"][window] = {"top": top, "margin": margin, "scores": scores}
    if len(traces) != 29 or any(set(row["points"]) != set(WINDOWS) for row in traces.values()):
        raise ValueError("B1 EEG traces are incomplete")
    baseline = {}
    with (OUT / "dynamic_stopping_selected_per_trial.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["sessionKey"] != "B1":
                raise ValueError("dynamic baseline contains a non-B1 row")
            baseline[row["trialId"]] = {
                "stop": float(row["evidenceSeconds"]),
                "predicted": int(row["predictedSlotIndex"]),
            }
    if set(baseline) != set(traces):
        raise ValueError("B1 dynamic baseline and score traces differ")
    return traces, baseline


def make_assignment(helper, trial_id, true_slot, pool, gate_rate, seed):
    digest = hashlib.sha256(f"M34_M33_assignment|{seed}|B1|{trial_id}".encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(digest[:8], "big"))
    if rng.random() >= gate_rate:
        return {"gateActive": False, "assignment": None, "mappingFailure": False, "semanticRow": None}
    semantic = pool[rng.randrange(len(pool))]
    assignment = helper._map_semantic_page(
        semantic, true_slot, rng, ("target_left", "target_center", "target_right"),
        paginate_candidates, project_global_prior,
    )
    if assignment is None:
        return {"gateActive": True, "assignment": None, "mappingFailure": True, "semanticRow": semantic}
    q_map = {item["candidate_id"]: float(item["q"]) for item in semantic["qGlobal"]}
    page_mass = sum(q_map[candidate] for candidate in assignment["pageCandidateIds"])
    assignment["globalPageMass"] = page_mass
    assignment["pageOffMass"] = max(0.0, 1.0 - page_mass)
    return {"gateActive": True, "assignment": assignment, "mappingFailure": False, "semanticRow": semantic}


def stable_top(points, windows, current_index, required_updates):
    first = current_index - required_updates + 1
    if first < 0:
        return False
    current = points[windows[current_index]]["top"]
    return all(points[windows[index]]["top"] == current for index in range(first, current_index + 1))


def simulate(trial_id, eeg, baseline, info, policy, *, measured_api=False):
    assignment = info["assignment"]
    if assignment is None:
        return {"stop": baseline["stop"], "selected": baseline["predicted"], "applied": False,
                "authorizedPoints": 0, "wrongEarly": False, "contextCausedError": False,
                "fallback": "mapping_or_semantic_gate" if info["gateActive"] else "semantic_gate_inactive"}
    semantic = info["semanticRow"]
    available_at = float(semantic["apiLatencyMs"]) / 1000.0 if measured_api else 0.0
    prior = assignment["prior"]
    context_top = max(range(3), key=lambda index: (float(prior[index]), -index))
    page_top_mass = float(assignment["priorTopMass"])
    page_mass = float(assignment["globalPageMass"])
    authorized = 0
    for index, window in enumerate(WINDOWS):
        if window > baseline["stop"] + TOL:
            break
        point = eeg["points"][window]
        eeg_gate = (
            window + TOL >= float(policy["minimumEvidenceSeconds"])
            and point["margin"] + TOL >= float(policy["minimumEegRelativeMargin"])
            and stable_top(eeg["points"], WINDOWS, index, int(policy["requiredConsecutiveTopUpdates"]))
        )
        gates = (
            window + TOL >= available_at
            and page_top_mass + TOL >= float(CONFIG["minimumProjectedPageTopMass"])
            and page_mass + TOL >= float(CONFIG["minimumGlobalPageMass"])
            and context_top == point["top"]
            and eeg_gate
        )
        if gates:
            authorized += 1
            if window < baseline["stop"] - TOL:
                wrong = point["top"] != eeg["true"]
                return {"stop": window, "selected": point["top"], "applied": True,
                        "authorizedPoints": authorized, "wrongEarly": bool(wrong),
                        "contextCausedError": bool(wrong and baseline["predicted"] == eeg["true"]),
                        "fallback": ""}
    return {"stop": baseline["stop"], "selected": baseline["predicted"], "applied": False,
            "authorizedPoints": authorized, "wrongEarly": False, "contextCausedError": False,
            "fallback": "paired_eeg_dynamic_stop_or_no_safe_early_agreement"}


def summarize(rows):
    n = len(rows)
    gains = [float(row["baselineStopSeconds"]) - float(row["contextStopSeconds"]) for row in rows]
    times = [float(row["contextStopSeconds"]) for row in rows]
    baseline_correct = sum(int(row["baselineCorrect"]) for row in rows)
    context_correct = sum(int(row["contextCorrect"]) for row in rows)
    active = sum(int(row["semanticGateActive"]) for row in rows)
    assigned = sum(int(row["semanticAssignmentActive"]) for row in rows)
    applied = sum(int(row["contextApplied"]) for row in rows)
    sort_times = sorted(times)
    def quantile(q):
        position = (len(sort_times) - 1) * q
        low, high = int(position // 1), int(-(-position // 1))
        return sort_times[low] if low == high else sort_times[low] + (sort_times[high] - sort_times[low]) * (position - low)
    return {
        "trialSeedPairs": n,
        "baselineCorrect": baseline_correct,
        "contextCorrect": context_correct,
        "baselineAccuracy": baseline_correct / n if n else 0.0,
        "contextAccuracy": context_correct / n if n else 0.0,
        "accuracyDelta": (context_correct - baseline_correct) / n if n else 0.0,
        "meanBaselineStopSeconds": sum(float(row["baselineStopSeconds"]) for row in rows) / n if n else 0.0,
        "meanContextStopSeconds": sum(times) / n if n else 0.0,
        "medianContextStopSeconds": quantile(0.5) if n else None,
        "p90ContextStopSeconds": quantile(0.9) if n else None,
        "meanAllTrialGainSeconds": sum(gains) / n if n else 0.0,
        "contextActiveAssignments": assigned,
        "semanticGateActiveAssignments": active,
        "semanticGateActiveRate": active / n if n else 0.0,
        "contextApplicationCount": applied,
        "contextApplicationRate": applied / n if n else 0.0,
        "meanAppliedGainSeconds": (sum(float(row["pairedGainSeconds"]) for row in rows if row["contextApplied"]) / applied) if applied else None,
        "noDelayViolationCount": sum(float(row["contextStopSeconds"]) > float(row["baselineStopSeconds"]) + TOL for row in rows),
        "wrongEarlyStopCount": sum(int(row["wrongEarlyStop"]) for row in rows),
        "contextCausedErrorCount": sum(int(row["contextCausedError"]) for row in rows),
        "stopFractionsAtOrBeforeEvidenceSeconds": {
            str(boundary): sum(float(row["contextStopSeconds"]) <= boundary + TOL for row in rows) / n if n else 0.0
            for boundary in (0.2, 0.25, 0.3, 0.4, 0.5)
        },
    }


def main():
    helper = load_transfer_helper()
    pool, semantic_count, gate = semantic_gate_rows()
    eeg, baseline = load_b1_eeg()
    gate_rate = len(pool) / semantic_count
    seed_count = int(CONFIG["assignmentSeeds"])
    assignments = {}
    mapping_failures = 0
    active_assignments = 0
    for seed in range(BASE_SEED, BASE_SEED + seed_count):
        for trial_id, trial in eeg.items():
            info = make_assignment(helper, trial_id, trial["true"], pool, gate_rate, seed)
            assignments[(trial_id, seed)] = info
            active_assignments += int(info["gateActive"])
            mapping_failures += int(info["mappingFailure"])
    candidate_rows = []
    selected_pair_rows = {}
    for minimum_evidence in CONFIG["minimumEvidenceSeconds"]:
        for minimum_margin in CONFIG["minimumEegRelativeMargin"]:
            for stable_updates in CONFIG["requiredConsecutiveTopUpdates"]:
                policy = {
                    "minimumEvidenceSeconds": float(minimum_evidence),
                    "minimumEegRelativeMargin": float(minimum_margin),
                    "requiredConsecutiveTopUpdates": int(stable_updates),
                }
                pair_rows = []
                for seed in range(BASE_SEED, BASE_SEED + seed_count):
                    for trial_id, trial in eeg.items():
                        base = baseline[trial_id]
                        result = simulate(trial_id, trial, base, assignments[(trial_id, seed)], policy)
                        pair_rows.append({
                            "sessionKey": "B1",
                            "trialId": trial_id,
                            "assignmentSeed": seed,
                            "trueSlotIndex": trial["true"],
                            "baselinePredictedSlotIndex": base["predicted"],
                            "contextPredictedSlotIndex": result["selected"],
                            "baselineCorrect": int(base["predicted"] == trial["true"]),
                            "contextCorrect": int(result["selected"] == trial["true"]),
                            "baselineStopSeconds": base["stop"],
                            "contextStopSeconds": result["stop"],
                            "pairedGainSeconds": base["stop"] - result["stop"],
                            "semanticGateActive": int(assignments[(trial_id, seed)]["gateActive"]),
                            "semanticAssignmentActive": int(assignments[(trial_id, seed)]["assignment"] is not None),
                            "alignmentFallback": int(assignments[(trial_id, seed)]["mappingFailure"]),
                            "pageGlobalMass": assignments[(trial_id, seed)]["assignment"].get("globalPageMass") if assignments[(trial_id, seed)]["assignment"] else None,
                            "pageOffMass": assignments[(trial_id, seed)]["assignment"].get("pageOffMass") if assignments[(trial_id, seed)]["assignment"] else None,
                            "pageConditionalTopMass": assignments[(trial_id, seed)]["assignment"].get("priorTopMass") if assignments[(trial_id, seed)]["assignment"] else None,
                            "authorizedPointCount": result["authorizedPoints"],
                            "contextApplied": int(result["applied"]),
                            "wrongEarlyStop": int(result["wrongEarly"]),
                            "contextCausedError": int(result["contextCausedError"]),
                            "fallbackReason": result["fallback"],
                        })
                summary = summarize(pair_rows)
                candidate_id = "E{:03d}_M{:03d}_S{}".format(
                    round(float(minimum_evidence) * 1000), round(float(minimum_margin) * 1000), int(stable_updates)
                )
                candidate_rows.append({
                    "candidateId": candidate_id,
                    **policy,
                    **summary,
                    "mappingFailureCount": mapping_failures,
                    "sourceGateThreshold": float(gate["threshold"]),
                    "measuredSemanticGateRate": gate_rate,
                    "selectionEligible": bool(summary["contextApplicationCount"] > 0
                        and summary["accuracyDelta"] >= -TOL
                        and summary["wrongEarlyStopCount"] == 0
                        and summary["noDelayViolationCount"] == 0),
                })
                selected_pair_rows[candidate_id] = pair_rows
    feasible = [row for row in candidate_rows if row["selectionEligible"]]
    selected = None
    if feasible:
        feasible.sort(key=lambda row: (
            -row["meanAllTrialGainSeconds"],
            -row["contextApplicationCount"],
            -row["minimumEegRelativeMargin"],
            -row["requiredConsecutiveTopUpdates"],
        ))
        selected = feasible[0]
        selected_pairs = selected_pair_rows[selected["candidateId"]]
        selected_policy = {key: selected[key] for key in (
            "minimumEvidenceSeconds", "minimumEegRelativeMargin", "requiredConsecutiveTopUpdates"
        )}
        enabled = True
    else:
        selected_pairs = []
        selected_policy = None
        enabled = False
    with (OUT / "context_dev_candidates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(candidate_rows[0]))
        writer.writeheader()
        writer.writerows(candidate_rows)
    if selected_pairs:
        with (OUT / "context_dev_selected_per_pair.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(selected_pairs[0]))
            writer.writeheader()
            writer.writerows(selected_pairs)
    latency_rows = []
    if enabled:
        for seed in range(BASE_SEED, BASE_SEED + seed_count):
            for trial_id, trial in eeg.items():
                base = baseline[trial_id]
                result = simulate(trial_id, trial, base, assignments[(trial_id, seed)], selected_policy, measured_api=True)
                latency_rows.append({
                    "sessionKey": "B1", "trialId": trial_id, "assignmentSeed": seed,
                    "contextAvailableAfterSeconds": (
                        float(assignments[(trial_id, seed)]["semanticRow"]["apiLatencyMs"]) / 1000.0
                        if assignments[(trial_id, seed)]["semanticRow"] else None
                    ),
                    "baselineStopSeconds": base["stop"], "contextStopSeconds": result["stop"],
                    "pairedGainSeconds": base["stop"] - result["stop"],
                    "contextApplied": int(result["applied"]), "wrongEarlyStop": int(result["wrongEarly"]),
                })
        with (OUT / "context_dev_api_latency_sensitivity_per_pair.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(latency_rows[0]))
            writer.writeheader()
            writer.writerows(latency_rows)
    summary = {
        "status": "PASS",
        "dataSplit": "M34 Dev B1 only",
        "eegBackboneFreeze": json.loads((OUT / "eeg_pipeline_freeze.json").read_text(encoding="utf-8")),
        "m33TrainDevGate": {
            "path": str(M33_GATE_PATH.relative_to(ROOT)),
            "sha256": sha256(M33_GATE_PATH),
            "trainDevResultsSha256": sha256(M33_RESULTS_PATH),
            "threshold": float(gate["threshold"]),
            "semanticOutputRows": semantic_count,
            "activePoolRows": len(pool),
            "measuredGateRate": gate_rate,
        },
        "assignmentSeedCount": seed_count,
        "trialSeedPairsPerCandidate": seed_count * len(eeg),
        "mappingFailureCountAcrossAssignments": mapping_failures,
        "selectedPolicy": selected_policy,
        "selectedCandidate": selected,
        "contextEnabled": enabled,
        "selectedMetricsPrecomputedContext": selected.get("contextApplicationCount") if selected else 0,
        "measuredApiLatencySensitivity": {
            "contextApplicationCount": sum(int(row["contextApplied"]) for row in latency_rows),
            "meanPairedGainSeconds": sum(float(row["pairedGainSeconds"]) for row in latency_rows) / max(1, len(latency_rows)),
            "trialSeedPairs": len(latency_rows),
        } if latency_rows else None,
        "selectionRule": CONFIG["selectionRule"],
        "alignmentDisclosure": CONFIG["alignmentDisclosure"],
        "pageMassRule": "Require both normalized projected page top mass >= 0.70 and pre-renormalization global probability mass on the mapped page >= 0.70; otherwise exact EEG-only fallback.",
        "leakageNote": "B2/S7 labels are present only in the frozen audit manifest; no held-out predictions or scores were generated. M33 expected-target labels are used only after semantic prediction to map a simulated three-choice page to an EEG true slot, never as model input.",
    }
    (OUT / "context_dev_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if enabled:
        freeze = {
            "status": "FROZEN_FOR_HELDOUT",
            "eegFreeze": json.loads((OUT / "eeg_pipeline_freeze.json").read_text(encoding="utf-8")),
            "contextEnabled": True,
            "contextPolicy": selected_policy,
            "semanticGate": summary["m33TrainDevGate"],
            "m33HeldoutSemanticSource": {
                "path": "research_analysis/m33_task_state_context_fix_20261003/attempt-01/held_out_results.jsonl",
                "sha256": sha256(M33 / "held_out_results.jsonl"),
                "use": "One-shot transfer simulation after this freeze; never used to select M34 parameters.",
            },
            "minimumProjectedPageTopMass": float(CONFIG["minimumProjectedPageTopMass"]),
            "minimumGlobalPageMass": float(CONFIG["minimumGlobalPageMass"]),
            "availabilityAtRunTimeAssumption": "The prior is precomputed from the prior selection history before the next gaze trial; actual M33 API latency is reported separately as sensitivity.",
            "assignmentSeedCountForEvaluation": seed_count,
            "heldoutSessions": ["B2", "S7"],
            "heldoutPredictionsOrMetricsEvaluated": False,
        }
    else:
        freeze = {
            "status": "FROZEN_FOR_HELDOUT",
            "eegFreeze": json.loads((OUT / "eeg_pipeline_freeze.json").read_text(encoding="utf-8")),
            "contextEnabled": False,
            "contextPolicy": None,
            "fallback": "No candidate produced a safe earlier stop on Dev B1; use exact EEG-only dynamic stop.",
            "heldoutSessions": ["B2", "S7"],
            "heldoutPredictionsOrMetricsEvaluated": False,
        }
    (OUT / "full_pipeline_freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "selectedPolicy": selected_policy,
        "selectedCandidate": selected,
        "contextEnabled": enabled,
        "apiSensitivity": summary["measuredApiLatencySensitivity"],
        "activeAssignments": active_assignments,
        "mappingFailures": mapping_failures,
    }, indent=2))


if __name__ == "__main__":
    main()
