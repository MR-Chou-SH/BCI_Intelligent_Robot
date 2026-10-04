"""DEV-only dynamic-stop tuning for the frozen M34 FBCCA backbone."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


OUT = Path(__file__).resolve().parent
PROTOCOL = json.loads((OUT / "frozen_protocol.json").read_text(encoding="utf-8"))
SELECTED = json.loads((OUT / "selected_eeg_backbones.json").read_text(encoding="utf-8"))
WINDOWS = [float(value) for value in PROTOCOL["fixedEvidenceWindowsSeconds"]]
GUARD = float(PROTOCOL["dynamicStopping"].get("guardSeconds", 0.5))
CONFIG_ID = SELECTED["selected"]["FBCCA"]["configId"]
DEV_SESSION = "B1"
TOL = 1e-12


def read_dev_scores():
    by_trial = {}
    with (OUT / "train_dev_search_per_trial.csv").open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["configId"] != CONFIG_ID or row["sessionKey"] != DEV_SESSION:
                continue
            trial = by_trial.setdefault(row["trialId"], {"true": int(row["trueSlotIndex"]), "points": {}})
            window = float(row["evidenceSeconds"])
            scores = [float(value) for value in json.loads(row["scoresBySlot"])]
            order = sorted(range(len(scores)), key=lambda index: (-scores[index], index))
            top, second = order[:2]
            margin = (scores[top] - scores[second]) / max(abs(scores[top]), 1e-12)
            trial["points"][window] = {
                "window": window,
                "scores": scores,
                "top": top,
                "relativeMargin": margin,
                "computeNs": int(row["computeNs"]),
            }
    if len(by_trial) != 29 or any(set(value["points"]) != set(WINDOWS) for value in by_trial.values()):
        raise ValueError("selected FBCCA B1 score traces are incomplete")
    return by_trial


def metrics(true_values, predicted_values):
    confusion = [[0, 0, 0] for _ in range(3)]
    for truth, prediction in zip(true_values, predicted_values):
        confusion[int(truth)][int(prediction)] += 1
    per_class = [confusion[index][index] / max(1, sum(confusion[index])) for index in range(3)]
    return {
        "n": len(true_values),
        "correct": sum(int(a == b) for a, b in zip(true_values, predicted_values)),
        "accuracy": sum(int(a == b) for a, b in zip(true_values, predicted_values)) / max(1, len(true_values)),
        "balancedAccuracy": sum(per_class) / 3.0,
        "perClassAccuracyBySlot": per_class,
        "confusionMatrixTrueSlotByPredictedSlot": confusion,
    }


def qtile(values, q):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] if low == high else ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def run_policy(trials, minimum_evidence, minimum_margin, stable_updates):
    rows = []
    for trial_id, trial in trials.items():
        points = trial["points"]
        picked = None
        for index, window in enumerate(WINDOWS):
            if window < minimum_evidence - TOL:
                continue
            point = points[window]
            if point["relativeMargin"] < minimum_margin - TOL:
                continue
            first = index - stable_updates + 1
            if first < 0:
                continue
            if not all(points[WINDOWS[j]]["top"] == point["top"] for j in range(first, index + 1)):
                continue
            picked = point
            break
        forced = picked is None
        if forced:
            picked = points[WINDOWS[-1]]
        rows.append({
            "sessionKey": DEV_SESSION,
            "trialId": trial_id,
            "trueSlotIndex": trial["true"],
            "predictedSlotIndex": picked["top"],
            "correct": int(picked["top"] == trial["true"]),
            "evidenceSeconds": picked["window"],
            "nominalLoggedOnsetRelativeDecisionSeconds": GUARD + picked["window"],
            "stopReason": "forced_max_window" if forced else "margin_and_stability",
            "relativeMarginAtStop": picked["relativeMargin"],
            "scoresBySlot": json.dumps(picked["scores"], separators=(",", ":")),
            "cumulativeRecomputeLatencyMs": sum(points[window]["computeNs"] for window in WINDOWS if window <= picked["window"] + TOL) / 1e6,
        })
    return rows


def summarize(rows):
    result = metrics([row["trueSlotIndex"] for row in rows], [row["predictedSlotIndex"] for row in rows])
    evidences = [float(row["evidenceSeconds"]) for row in rows]
    nominal = [float(row["nominalLoggedOnsetRelativeDecisionSeconds"]) for row in rows]
    compute = [float(row["cumulativeRecomputeLatencyMs"]) for row in rows]
    result.update({
        "meanEvidenceSeconds": sum(evidences) / len(evidences),
        "medianEvidenceSeconds": qtile(evidences, 0.5),
        "p90EvidenceSeconds": qtile(evidences, 0.9),
        "meanNominalLoggedOnsetRelativeDecisionSeconds": sum(nominal) / len(nominal),
        "medianNominalLoggedOnsetRelativeDecisionSeconds": qtile(nominal, 0.5),
        "p90NominalLoggedOnsetRelativeDecisionSeconds": qtile(nominal, 0.9),
        "meanCumulativeRecomputeLatencyMs": sum(compute) / len(compute),
        "p90CumulativeRecomputeLatencyMs": qtile(compute, 0.9),
        "stoppedAtOrBefore": {
            str(boundary): sum(value <= boundary + TOL for value in evidences)
            for boundary in (0.2, 0.25, 0.3, 0.4, 0.5)
        },
        "forcedToMaximumCount": sum(row["stopReason"] == "forced_max_window" for row in rows),
        "wrongEarlyStopCount": sum(not row["correct"] and row["evidenceSeconds"] < WINDOWS[-1] - TOL for row in rows),
    })
    return result


def main():
    trials = read_dev_scores()
    fixed = []
    for trial_id, trial in trials.items():
        point = trial["points"][0.5]
        fixed.append({
            "sessionKey": DEV_SESSION,
            "trialId": trial_id,
            "trueSlotIndex": trial["true"],
            "predictedSlotIndex": point["top"],
            "correct": int(point["top"] == trial["true"]),
            "evidenceSeconds": 0.5,
            "nominalLoggedOnsetRelativeDecisionSeconds": GUARD + 0.5,
            "stopReason": "fixed_050_baseline",
            "relativeMarginAtStop": point["relativeMargin"],
            "scoresBySlot": json.dumps(point["scores"], separators=(",", ":")),
            "cumulativeRecomputeLatencyMs": point["computeNs"] / 1e6,
        })
    fixed_metrics = summarize(fixed)
    candidates = []
    policy_rows = {}
    rule = PROTOCOL["dynamicStopping"]["b1Search"]
    for minimum_evidence in rule["minimumEvidenceSeconds"]:
        for minimum_margin in rule["minimumRelativeMargin"]:
            for stable_updates in rule["requiredConsecutiveTopUpdates"]:
                rows = run_policy(trials, float(minimum_evidence), float(minimum_margin), int(stable_updates))
                summary = summarize(rows)
                candidate_id = "E{:03d}_M{:03d}_S{}".format(
                    round(float(minimum_evidence) * 1000), round(float(minimum_margin) * 1000), int(stable_updates)
                )
                record = {
                    "candidateId": candidate_id,
                    "minimumEvidenceSeconds": float(minimum_evidence),
                    "minimumRelativeMargin": float(minimum_margin),
                    "requiredConsecutiveTopUpdates": int(stable_updates),
                    **summary,
                }
                candidates.append(record)
                policy_rows[candidate_id] = rows
    floor = fixed_metrics["balancedAccuracy"]
    feasible = [row for row in candidates if row["balancedAccuracy"] + TOL >= floor]
    if not feasible:
        selected = None
        selected_rows = fixed
        selected_metrics = fixed_metrics
        fallback = "No dynamic candidate matched the selected fixed 0.50 s B1 balanced accuracy; frozen policy is fixed 0.50 s."
    else:
        feasible.sort(key=lambda row: (
            -row["balancedAccuracy"],
            row["meanEvidenceSeconds"],
            -row["minimumRelativeMargin"],
            -row["requiredConsecutiveTopUpdates"],
        ))
        selected = feasible[0]
        selected_rows = policy_rows[selected["candidateId"]]
        selected_metrics = summarize(selected_rows)
        fallback = None
    with (OUT / "dynamic_stopping_candidates.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(candidates[0]))
        writer.writeheader()
        writer.writerows(candidates)
    with (OUT / "dynamic_stopping_selected_per_trial.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(selected_rows[0]))
        writer.writeheader()
        writer.writerows(selected_rows)
    summary = {
        "status": "PASS",
        "dataSplit": "B1 development only",
        "decoderConfigId": CONFIG_ID,
        "decoderConfig": SELECTED["selected"]["FBCCA"]["config"],
        "guardSeconds": GUARD,
        "scheduleSeconds": WINDOWS,
        "fixed050Baseline": fixed_metrics,
        "selectedPolicy": selected,
        "selectedDynamicMetrics": selected_metrics,
        "fallbackReason": fallback,
        "candidateCount": len(candidates),
        "selectionRule": rule["selectionRule"],
        "latencyNote": "Decision timing is nominal logged-onset-relative (0.5 s guard + evidence). Cumulative decode compute sums observed full-window calls at each schedule point, a conservative repeated-recompute estimate rather than an incremental streaming benchmark.",
        "leakageNote": "No B2 or S7 predictions/metrics were used. Candidate thresholds were searched on B1 only after the fixed EEG decoder was selected.",
    }
    (OUT / "dynamic_stopping_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    freeze = {
        "status": "FROZEN_FOR_HELDOUT",
        "frozenAfter": ["baseline reproduction", "bounded A/B1 decoder search", "B1-only dynamic-stop tuning"],
        "primaryEegBackbone": SELECTED["selected"]["FBCCA"],
        "onsetGuardSeconds": GUARD,
        "dynamicStopping": selected,
        "dynamicFallback": fallback,
        "heldoutSessions": ["B2", "S7"],
        "heldoutPredictionsOrMetricsEvaluated": False,
        "heldoutAuditBoundary": "B2/S7 labels are present only in the pre-tuning data-audit manifest; no held-out predictions, scores, or outcomes were used for selection.",
        "frozenAtLocalDate": "2026-10-04",
    }
    (OUT / "eeg_pipeline_freeze.json").write_text(json.dumps(freeze, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": selected, "fixed050": fixed_metrics, "dynamic": selected_metrics}, indent=2))


if __name__ == "__main__":
    main()
