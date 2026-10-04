"""One-shot final M34 evaluation on frozen B2/S7 held-out sessions."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import random
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT))

import numpy as np  # noqa: E402
from integration.semantic_context_sequence import paginate_candidates, project_global_prior  # noqa: E402
from run_dynamic_stop_search import metrics as classify_metrics, qtile  # noqa: E402


M33 = ROOT / "research_analysis/m33_task_state_context_fix_20261003/attempt-01"
M33_GATE_PATH = M33 / "train_dev_gate.json"
M33_HELDOUT_PATH = M33 / "held_out_results.jsonl"
M30_TRANSFER = ROOT / "research_analysis/m30_nextgen_semantic_context_20261003/attempt-01/run_m30_eeg_transfer.py"
MANIFEST = None
PROTOCOL = json.loads((OUT / "frozen_protocol.json").read_text(encoding="utf-8"))
SELECTED = json.loads((OUT / "selected_eeg_backbones.json").read_text(encoding="utf-8"))
FREEZE = json.loads((OUT / "full_pipeline_freeze.json").read_text(encoding="utf-8"))
WINDOWS = [float(value) for value in PROTOCOL["fixedEvidenceWindowsSeconds"]]
GUARD = float(PROTOCOL["dynamicStopping"]["guardSeconds"])
FREQUENCIES = (7.2, 9.0, 12.0)
SEED_START = 34004
SEED_COUNT = int(PROTOCOL["contextIntegration"]["b1Tuning"]["assignmentSeeds"])
TOL = 1e-12


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def import_script(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load M34 helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_active_m33_heldout(gate_threshold):
    rows = [row for row in read_jsonl(M33_HELDOUT_PATH) if row.get("record_type") == "m33_semantic_result"]
    if len(rows) != 46:
        raise ValueError(f"expected 46 frozen M33 held-out rows, found {len(rows)}")
    active = [row for row in rows if row.get("predicted_status") == "informative"
              and row.get("eligible_informative") is True
              and float(row.get("gate_features", {}).get("conservative_confidence", -1.0)) >= gate_threshold]
    return rows, [{
        "expectedStatus": row["expected_status"],
        "acceptableNextTargets": row["acceptable_next_targets"],
        "qGlobal": row["q_global"],
        "episodeId": row["episode_id"],
        "roundIndex": int(row["round_index"]),
        "sceneFamily": row.get("scene_family", ""),
        "apiLatencyMs": float(row.get("provenance", {}).get("api_latency_ms", 0.0) or 0.0),
    } for row in active]


def manifest_sessions():
    return {row["sessionKey"]: row for row in MANIFEST["sessions"]}


def usable_trials(session_key):
    return [row for row in MANIFEST["trials"] if row["sessionKey"] == session_key and row["usable"]]


def load_raw(session_key, helper):
    return helper.load_raw(session_key)


def centered(values):
    values = np.asarray(values, dtype=np.float64)
    return values - np.mean(values, axis=-1, keepdims=True)


def make_training_data(raw_by_session, helper, window, channels):
    trials = [row for row in MANIFEST["trials"]
              if row["sessionKey"] in ("A", "B1") and row["usable"]]
    epochs = []
    labels = []
    for trial in trials:
        epoch = helper.make_raw_epoch(raw_by_session[trial["sessionKey"]], trial, GUARD, window, tuple(range(8)))
        epochs.append(centered(epoch[list(channels)]))
        labels.append(int(trial["slotIndex"]))
    return np.asarray(epochs), np.asarray(labels, dtype=int)


def fit_selected_models(raw_by_session, helper, window, channels):
    fitted = {}
    fit_ms = {}
    train, labels = make_training_data(raw_by_session, helper, window, channels)
    et_config = SELECTED["selected"]["eTRCA"]["config"]
    et_models = []
    start = time.perf_counter_ns()
    if et_config["filterVariant"] == "three-band":
        for band in helper.BANDS:
            values = np.asarray([helper.filtered(epoch, band) for epoch in train])
            et_models.append(helper.fit_etrcca(values, labels, component_count=int(et_config["components"]), relative_ridge=1e-6))
    else:
        et_models.append(helper.fit_etrcca(train, labels, component_count=int(et_config["components"]), relative_ridge=1e-6))
    fitted["eTRCA"] = et_models
    fit_ms["eTRCA"] = (time.perf_counter_ns() - start) / 1e6

    td_config = SELECTED["selected"]["TDCA"]["config"]
    td_models = []
    start = time.perf_counter_ns()
    td_models.append(helper.fit_tdca(
        train,
        labels,
        frequencies_hz=FREQUENCIES,
        sampling_rate_hz=1000.0,
        harmonic_count=int(td_config["harmonics"]),
        delay_count=int(td_config["delayCopies"]) - 1,
        delay_step_samples=int(td_config["delayStepSamples"]),
        component_count=int(td_config["components"]),
        relative_ridge=float(td_config["relativeSwRidge"]),
    ))
    fitted["TDCA"] = td_models
    fit_ms["TDCA"] = (time.perf_counter_ns() - start) / 1e6
    return fitted, fit_ms


def score_decoder(decoder, raw_epoch, channels, models, helper, config):
    if decoder == "FBCCA":
        fb_config = helper.fbcca_config(config["filterVariant"])
        return helper.predict_fbcca(
            raw_epoch[list(channels)], FREQUENCIES, int(config["harmonics"]), 1000.0, config=fb_config
        )[1]
    if decoder == "eTRCA":
        if config["filterVariant"] == "three-band":
            parts = [helper.predict_etrcca(model, helper.filtered(raw_epoch[list(channels)], band))
                     for model, band in zip(models, helper.BANDS)]
            return helper.FILTER_WEIGHTS @ np.asarray(parts)
        return helper.predict_etrcca(models[0], centered(raw_epoch[list(channels)]))
    if decoder == "TDCA":
        return helper.predict_tdca(models[0], centered(raw_epoch[list(channels)]))
    raise ValueError(decoder)


def itr_bits_per_selection(accuracy, class_count=3):
    p = float(accuracy)
    if p <= 0.0:
        return math.log2(class_count) + math.log2(1.0 / (class_count - 1))
    if p >= 1.0:
        return math.log2(class_count)
    return math.log2(class_count) + p * math.log2(p) + (1.0 - p) * math.log2((1.0 - p) / (class_count - 1))


def summarize_scores(rows):
    true = [int(row["trueSlotIndex"]) for row in rows]
    predicted = [int(row["predictedSlotIndex"]) for row in rows]
    summary = classify_metrics(true, predicted)
    correct = summary["accuracy"]
    decision_times = [float(row["nominalLoggedOnsetRelativeDecisionSeconds"]) for row in rows]
    mean_time = sum(decision_times) / len(decision_times) if decision_times else 0.0
    bits = itr_bits_per_selection(correct)
    summary.update({
        "meanEvidenceSeconds": sum(float(row["evidenceSeconds"]) for row in rows) / len(rows) if rows else None,
        "nominalLoggedOnsetRelativeDecisionTimeSeconds": mean_time,
        "meanComputeMs": sum(float(row["computeMs"]) for row in rows) / len(rows) if rows else None,
        "p90ComputeMs": qtile([float(row["computeMs"]) for row in rows], 0.9) if rows else None,
        "decisionOnlyItrBitsPerMin": bits * 60.0 / mean_time if mean_time > 0 else None,
        "m6Like9sCycleItrBitsPerMin": bits * 60.0 / 9.0,
        "itrBitsPerSelection": bits,
        "itrFormula": "B = log2(3)+p*log2(p)+(1-p)*log2((1-p)/2); ITR = B*60/T. Decision-only T=nominal logged-onset-relative time; M6-like T=9 s cue+rest+fixed stimulus+post-rest cycle.",
    })
    return summary


def dynamic_decision(points, policy):
    windows = WINDOWS
    for index, window in enumerate(windows):
        if window < float(policy["minimumEvidenceSeconds"]) - TOL:
            continue
        point = points[window]
        first = index - int(policy["requiredConsecutiveTopUpdates"]) + 1
        if first < 0:
            continue
        stable = all(points[windows[j]]["top"] == point["top"] for j in range(first, index + 1))
        if stable and point["relativeMargin"] + TOL >= float(policy["minimumRelativeMargin"]):
            return point
    return points[windows[-1]]


def make_context_assignment(helper, session_key, trial_id, true_slot, pool, gate_rate, seed):
    digest = hashlib.sha256(f"M34_M33_assignment|{seed}|{session_key}|{trial_id}".encode("utf-8")).digest()
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


def context_simulation(eeg_trial, baseline, assignment_info, policy, measured_api=False):
    assignment = assignment_info["assignment"]
    if assignment is None:
        return {"stop": baseline["evidenceSeconds"], "selected": baseline["predictedSlotIndex"],
                "applied": False, "authorized": False, "wrongEarly": False,
                "fallback": "semantic_gate_or_alignment_fallback"}
    semantic = assignment_info["semanticRow"]
    available_at = float(semantic["apiLatencyMs"]) / 1000.0 if measured_api else 0.0
    context_top = max(range(3), key=lambda index: (float(assignment["prior"][index]), -index))
    authorized_count = 0
    for index, window in enumerate(WINDOWS):
        if window > float(baseline["evidenceSeconds"]) + TOL:
            break
        point = eeg_trial["points"][window]
        first = index - int(policy["requiredConsecutiveTopUpdates"]) + 1
        stable = first >= 0 and all(eeg_trial["points"][WINDOWS[j]]["top"] == point["top"]
                                    for j in range(first, index + 1))
        authorized = (
            window + TOL >= available_at
            and float(assignment["priorTopMass"]) + TOL >= float(FREEZE["minimumProjectedPageTopMass"])
            and float(assignment["globalPageMass"]) + TOL >= float(FREEZE["minimumGlobalPageMass"])
            and context_top == point["top"]
            and window + TOL >= float(policy["minimumEvidenceSeconds"])
            and point["relativeMargin"] + TOL >= float(policy["minimumEegRelativeMargin"])
            and stable
        )
        if authorized:
            authorized_count += 1
        if authorized and window < float(baseline["evidenceSeconds"]) - TOL:
            return {"stop": window, "selected": point["top"], "applied": True,
                    "authorized": True, "authorizedPointCount": authorized_count,
                    "wrongEarly": point["top"] != eeg_trial["true"], "fallback": ""}
    return {"stop": float(baseline["evidenceSeconds"]), "selected": int(baseline["predictedSlotIndex"]),
            "applied": False, "authorized": authorized_count > 0, "authorizedPointCount": authorized_count, "wrongEarly": False,
            "fallback": "paired_eeg_dynamic_stop_or_no_safe_early_agreement"}


def write_csv(path: Path, rows):
    if not rows:
        raise ValueError(f"refusing to write empty output: {path.name}")
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        normalized = [{key: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                       if isinstance(value, (dict, list, tuple)) else value
                       for key, value in row.items()} for row in rows]
        writer.writerows(normalized)


def run_final():
    if FREEZE.get("status") != "FROZEN_FOR_HELDOUT" or FREEZE.get("heldoutPredictionsOrMetricsEvaluated") is not False:
        raise ValueError("full pipeline freeze is absent or held-out has already been marked loaded")
    marker = OUT / "heldout_attempt_started.json"
    result_path = OUT / "heldout_results.json"
    parser = argparse.ArgumentParser()
    parser.add_argument("--technical-retry", action="store_true")
    args = parser.parse_args()
    if result_path.exists():
        raise RuntimeError("official held-out result already exists; refusing a second final evaluation")
    if marker.exists() and not args.technical_retry:
        raise RuntimeError("a held-out attempt already started; inspect its state before retrying")
    marker_doc = {
        "recordType": "m34_heldout_attempt_started",
        "startedAtUtc": utc_now(),
        "sessions": ["B2", "S7"],
        "pipelineFreezeSha256": sha256(OUT / "full_pipeline_freeze.json"),
        "protocolSha256": sha256(OUT / "frozen_protocol.json"),
        "dataManifestSha256": sha256(OUT / "data_manifest.json"),
        "attemptNumber": 2 if marker.exists() else 1,
        "technicalRetryAuthorized": bool(args.technical_retry),
        "heldoutMetricsBeforeStart": False,
    }
    if marker.exists():
        previous = json.loads(marker.read_text(encoding="utf-8"))
        marker_doc["previousAttempt"] = previous
    marker.write_text(json.dumps(marker_doc, indent=2) + "\n", encoding="utf-8")

    global MANIFEST
    MANIFEST = json.loads((OUT / "data_manifest.json").read_text(encoding="utf-8"))
    m33_gate = json.loads(M33_GATE_PATH.read_text(encoding="utf-8"))
    expected_gate_hash = FREEZE.get("semanticGate", {}).get("sha256")
    if expected_gate_hash != sha256(M33_GATE_PATH) or m33_gate.get("held_out_used_for_gate_selection") is not False:
        raise ValueError("frozen M33 train/dev gate hash or held-out boundary does not match")
    heldout_context_source = FREEZE.get("m33HeldoutSemanticSource", {})
    if heldout_context_source.get("sha256") != sha256(M33_HELDOUT_PATH):
        raise ValueError("M33 held-out semantic source changed after M34 freeze")

    helper = import_script(OUT / "run_train_dev_search.py", "m34_final_helpers")
    decoder_lib = import_script(OUT / "m34_decoders.py", "m34_final_decoder_lib")
    m30_helper = import_script(M30_TRANSFER, "m30_readonly_transfer_helpers_m34_heldout")
    raw_by_session = {key: load_raw(key, helper) for key in ("A", "B1", "B2", "S7")}
    sessions = manifest_sessions()
    expected_hashes = {key: sessions[key]["rawFileSha256"] for key in ("A", "B1", "B2", "S7")}
    before_hashes = {}
    for session_key in ("A", "B1", "B2", "S7"):
        session = sessions[session_key]
        raw_name = "raw-eeg.jsonl" if session_key == "S7" else "raw-eeg-packets.jsonl"
        actual = sha256(Path(session["sourcePath"]) / raw_name)
        before_hashes[session_key] = actual
        if actual != expected_hashes[session_key]:
            raise AssertionError(f"raw EEG source hash differs from frozen manifest before run: {session_key}")
    fixed_rows = []
    trace_by_session = {}
    fit_by_session = {}

    for session_key in ("B2", "S7"):
        session_trials = usable_trials(session_key)
        if len(session_trials) != (29 if session_key == "B2" else 30):
            raise ValueError(f"unexpected held-out usable trial count for {session_key}")
        channels = [2, 3, 4, 5, 7] if session_key == "B2" else [2, 4, 7]
        trace_by_session[session_key] = {trial["trialId"]: {"true": int(trial["slotIndex"]), "points": {}} for trial in session_trials}
        fit_by_session[session_key] = {}
        for window in WINDOWS:
            models, fit_ms = fit_selected_models(raw_by_session, helper, window, channels)
            fit_by_session[session_key][window] = fit_ms
            per_decoder_window = defaultdict(list)
            for trial in session_trials:
                raw_epoch = helper.make_raw_epoch(raw_by_session[session_key], trial, GUARD, window, tuple(range(8)))
                for decoder in ("FBCCA", "eTRCA", "TDCA"):
                    config = SELECTED["selected"][decoder]["config"]
                    model_list = models.get(decoder, [])
                    start_ns = time.perf_counter_ns()
                    scores = np.asarray(score_decoder(decoder, raw_epoch, channels, model_list, helper, config), dtype=float)
                    compute_ms = (time.perf_counter_ns() - start_ns) / 1e6
                    prediction = int(np.argmax(scores))
                    row = {
                        "sessionKey": session_key,
                        "split": "heldout_primary" if session_key == "B2" else "heldout_stress",
                        "trialId": trial["trialId"],
                        "decoder": decoder,
                        "config": json.dumps(config, sort_keys=True, separators=(",", ":")),
                        "channelIndices": json.dumps(channels, separators=(",", ":")),
                        "trueSlotIndex": int(trial["slotIndex"]),
                        "frequencyHz": float(trial["frequencyHz"]),
                        "predictedSlotIndex": prediction,
                        "scoresBySlot": json.dumps(scores.tolist(), separators=(",", ":")),
                        "evidenceSeconds": window,
                        "onsetGuardSeconds": GUARD,
                        "nominalLoggedOnsetRelativeDecisionSeconds": GUARD + window,
                        "computeMs": compute_ms,
                        "modelFitMsThisWindow": float(fit_ms.get(decoder, 0.0)),
                        "correct": int(prediction == int(trial["slotIndex"])),
                        "sampleAnchorState": trial["sampleAnchorState"],
                        "mappingUncertaintySeconds": trial["mappingUncertaintySeconds"],
                    }
                    per_decoder_window[decoder].append(row)
                    fixed_rows.append(row)
                    if decoder == "FBCCA":
                        order = sorted(range(3), key=lambda index: (-scores[index], index))
                        top, second = order[:2]
                        margin = float(scores[top] - scores[second]) / max(abs(float(scores[top])), 1e-12)
                        trace_by_session[session_key][trial["trialId"]]["points"][window] = {
                            "window": window,
                            "scores": scores.tolist(),
                            "top": top,
                            "relativeMargin": margin,
                            "computeMs": compute_ms,
                        }
            for decoder, rows in per_decoder_window.items():
                pass

    write_csv(OUT / "heldout_fixed_per_trial.csv", fixed_rows)
    fixed_summary_rows = []
    for session_key in ("B2", "S7"):
        for decoder in ("FBCCA", "eTRCA", "TDCA"):
            for window in WINDOWS:
                rows = [row for row in fixed_rows if row["sessionKey"] == session_key
                        and row["decoder"] == decoder and abs(float(row["evidenceSeconds"]) - window) < TOL]
                fixed_summary_rows.append({
                    "sessionKey": session_key,
                    "decoder": decoder,
                    "evidenceSeconds": window,
                    "nominalLoggedOnsetRelativeDecisionSeconds": GUARD + window,
                    "selectedConfig": json.dumps(SELECTED["selected"][decoder]["config"], sort_keys=True, separators=(",", ":")),
                    "channelsUsed": json.dumps([2, 3, 4, 5, 7] if session_key == "B2" else [2, 4, 7]),
                    "modelFitMsThisWindow": float(rows[0]["modelFitMsThisWindow"]) if rows else None,
                    **summarize_scores(rows),
                })
    write_csv(OUT / "heldout_fixed_summary.csv", fixed_summary_rows)

    dynamic_policy = FREEZE["eegFreeze"]["dynamicStopping"]
    if not isinstance(dynamic_policy, dict):
        raise ValueError("frozen dynamic-stop policy missing")
    dynamic_rows_by_session = {}
    for session_key in ("B2", "S7"):
        selected_rows = []
        for trial in usable_trials(session_key):
            trial_id = trial["trialId"]
            trace = trace_by_session[session_key][trial_id]
            point = dynamic_decision(trace["points"], dynamic_policy)
            computed_ns = sum(trace["points"][window]["computeMs"] for window in WINDOWS if window <= point["window"] + TOL)
            selected_rows.append({
                "sessionKey": session_key,
                "trialId": trial_id,
                "trueSlotIndex": trace["true"],
                "predictedSlotIndex": point["top"],
                "correct": int(point["top"] == trace["true"]),
                "evidenceSeconds": point["window"],
                "nominalLoggedOnsetRelativeDecisionSeconds": GUARD + point["window"],
                "stopReason": "frozen_margin_stability" if point["window"] < WINDOWS[-1] else "frozen_or_max_window",
                "minimumEvidenceSeconds": dynamic_policy["minimumEvidenceSeconds"],
                "minimumRelativeMargin": dynamic_policy["minimumRelativeMargin"],
                "requiredConsecutiveTopUpdates": dynamic_policy["requiredConsecutiveTopUpdates"],
                "relativeMarginAtStop": point["relativeMargin"],
                "scoresBySlot": json.dumps(point["scores"], separators=(",", ":")),
                "cumulativeRecomputeLatencyMs": computed_ns,
                "computeMs": point["computeMs"],
                "nominalLoggedOnsetRelativeTimingOnly": True,
            })
        dynamic_rows_by_session[session_key] = selected_rows
    write_csv(OUT / "heldout_dynamic_per_trial.csv", dynamic_rows_by_session["B2"] + dynamic_rows_by_session["S7"])
    dynamic_summary = {}
    for session_key, rows in dynamic_rows_by_session.items():
        summary = summarize_scores([{
            "trueSlotIndex": row["trueSlotIndex"],
            "predictedSlotIndex": row["predictedSlotIndex"],
            "evidenceSeconds": row["evidenceSeconds"],
            "nominalLoggedOnsetRelativeDecisionSeconds": row["nominalLoggedOnsetRelativeDecisionSeconds"],
            "computeMs": row["computeMs"],
        } for row in rows])
        evidences = [float(row["evidenceSeconds"]) for row in rows]
        summary.update({
            "meanCumulativeRecomputeLatencyMs": sum(float(row["cumulativeRecomputeLatencyMs"]) for row in rows) / len(rows),
            "p90CumulativeRecomputeLatencyMs": qtile([float(row["cumulativeRecomputeLatencyMs"]) for row in rows], 0.9),
            "wrongEarlyStopCount": sum(not row["correct"] and float(row["evidenceSeconds"]) < 1.0 - TOL for row in rows),
            "maximumEvidenceFallbackCount": sum(abs(value - 1.0) < TOL for value in evidences),
            "stopFractionsAtOrBeforeEvidenceSeconds": {
                str(boundary): sum(value <= boundary + TOL for value in evidences) / len(evidences)
                for boundary in (0.2, 0.25, 0.3, 0.4, 0.5)
            },
            "policy": dynamic_policy,
        })
        dynamic_summary[session_key] = summary

    semantic_rows, active_pool = load_active_m33_heldout(float(m33_gate["threshold"]))
    helper = import_script(M30_TRANSFER, "m30_readonly_transfer_helpers_m34_context")
    context_all = []
    context_summary = {}
    for session_key in ("B2", "S7"):
        session_trials = usable_trials(session_key)
        baseline_by_id = {row["trialId"]: row for row in dynamic_rows_by_session[session_key]}
        pair_rows = []
        gate_rate = len(active_pool) / len(semantic_rows)
        for seed in range(SEED_START, SEED_START + SEED_COUNT):
            for trial in session_trials:
                trial_id = trial["trialId"]
                trace = trace_by_session[session_key][trial_id]
                baseline = baseline_by_id[trial_id]
                info = make_context_assignment(helper, session_key, trial_id, trace["true"], active_pool, gate_rate, seed)
                for scenario, measured_api in (("precomputed_before_eeg", False),
                                                ("measured_m33_api_latency_sensitivity", True)):
                    result = context_simulation(trace, baseline, info, FREEZE["contextPolicy"], measured_api=measured_api)
                    baseline_correct = int(int(baseline["predictedSlotIndex"]) == trace["true"])
                    row = {
                        "sessionKey": session_key,
                        "scenario": scenario,
                        "trialId": trial_id,
                        "assignmentSeed": seed,
                        "trueSlotIndex": trace["true"],
                        "baselinePredictedSlotIndex": baseline["predictedSlotIndex"],
                        "contextPredictedSlotIndex": result["selected"],
                        "baselineCorrect": baseline_correct,
                        "contextCorrect": int(result["selected"] == trace["true"]),
                        "baselineEvidenceSeconds": baseline["evidenceSeconds"],
                        "contextEvidenceSeconds": result["stop"],
                        "baselineStopSeconds": baseline["nominalLoggedOnsetRelativeDecisionSeconds"],
                        "contextStopSeconds": GUARD + result["stop"],
                        "pairedGainSeconds": float(baseline["evidenceSeconds"]) - result["stop"],
                        "semanticGateActive": int(info["gateActive"]),
                        "semanticAssignmentActive": int(info["assignment"] is not None),
                        "alignmentFallback": int(info["mappingFailure"]),
                        "semanticCaseId": info["semanticRow"].get("episodeId") if info["semanticRow"] else "",
                        "semanticRoundIndex": info["semanticRow"].get("roundIndex") if info["semanticRow"] else "",
                        "semanticSceneFamily": info["semanticRow"].get("sceneFamily") if info["semanticRow"] else "",
                        "pageCandidateIds": json.dumps(info["assignment"].get("pageCandidateIds") if info["assignment"] else []),
                        "qPage": json.dumps(info["assignment"].get("qPage") if info["assignment"] else []),
                        "pageGlobalMass": info["assignment"].get("globalPageMass") if info["assignment"] else None,
                        "pageOffMass": info["assignment"].get("pageOffMass") if info["assignment"] else None,
                        "pageConditionalTopMass": info["assignment"].get("priorTopMass") if info["assignment"] else None,
                        "contextAuthorized": int(result["authorized"]),
                        "contextAuthorizedPointCount": result.get("authorizedPointCount", 0),
                        "contextApplied": int(result["applied"]),
                        "wrongEarlyStop": int(result["wrongEarly"]),
                        "contextCausedError": int(result["wrongEarly"] and baseline_correct),
                        "contextAvailableAfterSeconds": (
                            float(info["semanticRow"]["apiLatencyMs"]) / 1000.0 if info["semanticRow"] else None
                        ),
                        "fallbackReason": result["fallback"],
                    }
                    pair_rows.append(row)
                    context_all.append(row)
        context_summary[session_key] = {}
        for scenario in ("precomputed_before_eeg", "measured_m33_api_latency_sensitivity"):
            rows = [row for row in pair_rows if row["scenario"] == scenario]
            n = len(rows)
            active = sum(int(row["semanticGateActive"]) for row in rows)
            assigned = sum(int(row["semanticAssignmentActive"]) for row in rows)
            authorized = sum(int(row["contextAuthorized"]) for row in rows)
            applied = sum(int(row["contextApplied"]) for row in rows)
            baseline_correct = sum(int(row["baselineCorrect"]) for row in rows)
            context_correct = sum(int(row["contextCorrect"]) for row in rows)
            stop_times = [float(row["contextStopSeconds"]) for row in rows]
            context_summary[session_key][scenario] = {
                "trialSeedPairs": n,
                "baselineAccuracy": baseline_correct / n,
                "contextAccuracy": context_correct / n,
                "accuracyDelta": (context_correct - baseline_correct) / n,
                "meanBaselineDecisionSeconds": sum(float(row["baselineStopSeconds"]) for row in rows) / n,
                "meanContextDecisionSeconds": sum(stop_times) / n,
                "medianContextDecisionSeconds": qtile(stop_times, 0.5),
                "p90ContextDecisionSeconds": qtile(stop_times, 0.9),
                "semanticGateActiveRate": active / n,
                "mappedAssignmentRate": assigned / n,
                "authorizationRate": authorized / n,
                "authorizedPointCount": sum(int(row["contextAuthorizedPointCount"]) for row in rows),
                "applicationRate": applied / n,
                "meanAppliedGainSeconds": (sum(float(row["pairedGainSeconds"]) for row in rows if row["contextApplied"]) / applied) if applied else None,
                "stopFractionsAtOrBeforeEvidenceSeconds": {
                    str(boundary): sum(float(row["contextEvidenceSeconds"]) <= boundary + TOL for row in rows) / n
                    for boundary in (0.2, 0.25, 0.3, 0.4, 0.5)
                },
                "wrongEarlyStopCount": sum(int(row["wrongEarlyStop"]) for row in rows),
                "contextCausedErrorCount": sum(int(row["contextCausedError"]) for row in rows),
                "noDelayViolationCount": sum(float(row["contextStopSeconds"]) > float(row["baselineStopSeconds"]) + TOL for row in rows),
                "alignmentFallbackCount": sum(int(row["alignmentFallback"]) for row in rows),
                "ambiguousOrOffRowsInM33Source": sum(row.get("predicted_status") in ("ambiguous", "context_off", "invalid")
                                                      for row in semantic_rows),
                "evidenceLabel": "seeded historical transfer simulation; not prospective EEG/context pairing",
            }
    write_csv(OUT / "context_heldout_per_trial_seed.csv", context_all)
    (OUT / "heldout_context_summary.json").write_text(json.dumps({
        "status": "PASS",
        "M33GateThreshold": float(m33_gate["threshold"]),
        "M33SemanticHeldoutRows": len(semantic_rows),
        "M33SemanticActivePoolRows": len(active_pool),
        "M33SemanticHeldoutActiveRate": len(active_pool) / len(semantic_rows),
        "M33SelectedContextGateHash": sha256(M33_GATE_PATH),
        "ContextPolicy": FREEZE["contextPolicy"],
        "ContextPairing": "A frozen M33 semantic result is randomly assigned to each EEG trial/seed. Only the post-hoc expected-target label maps its 3-item page to the true EEG slot. Context sees its own original ordered history and candidate set only.",
        "LeakageBoundary": "B2/S7 labels were audited into the frozen manifest before search, but no held-out predictions, scores, or outcomes were used for model or threshold selection. M33 expected-target labels are used only after semantic prediction to align synthetic pages to EEG slots, never as model input.",
        "ContextResultsBySessionAndAvailability": context_summary,
        "sessionCounts": {session: len(usable_trials(session)) for session in ("B2", "S7")},
    }, indent=2) + "\n", encoding="utf-8")

    # Raw sources are re-hashed after decoding and must remain byte-identical.
    after_hashes = {}
    for session_key in ("A", "B1", "B2", "S7"):
        session = sessions[session_key]
        raw_name = "raw-eeg.jsonl" if session_key == "S7" else "raw-eeg-packets.jsonl"
        actual = sha256(Path(session["sourcePath"]) / raw_name)
        after_hashes[session_key] = actual
        if actual != expected_hashes[session_key]:
            raise AssertionError(f"raw EEG source hash changed during M34: {session_key}")

    all_summaries = {
        "fixed": fixed_summary_rows,
        "dynamic": dynamic_summary,
        "context": context_summary,
        "modelFitTimesMsBySessionAndEvidence": fit_by_session,
        "rawSourceHashesAfter": after_hashes,
        "rawSourceHashesBefore": before_hashes,
        "rawSourceHashesMatchManifest": after_hashes == expected_hashes,
        "protocolSha256": sha256(OUT / "frozen_protocol.json"),
        "pipelineFreezeSha256": sha256(OUT / "full_pipeline_freeze.json"),
        "startedAtUtc": marker_doc["startedAtUtc"],
        "finishedAtUtc": utc_now(),
        "frequencyMapping": {"slot0": 7.2, "slot1": 9.0, "slot2": 12.0},
        "timingBoundary": "All decision times are nominal logged-onset-relative using the software event-to-sample estimate; physical optical onset and hardware anchor are unverified.",
    }
    (OUT / "heldout_results.json").write_text(json.dumps(all_summaries, indent=2) + "\n", encoding="utf-8")
    (OUT / "heldout_attempt_complete.json").write_text(json.dumps({
        "recordType": "m34_heldout_attempt_complete",
        "completedAtUtc": utc_now(),
        "status": "PASS" if all_summaries["rawSourceHashesMatchManifest"] else "FAIL",
        "sessions": ["B2", "S7"],
        "fixedTrialRows": len(fixed_rows),
        "contextTrialSeedScenarioRows": len(context_all),
        "heldoutRetryUsed": bool(marker_doc["technicalRetryAuthorized"]),
    }, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "fixedSummaryRows": len(fixed_summary_rows),
        "dynamic": dynamic_summary,
        "context": context_summary,
        "rawSourceHashesMatchManifest": all_summaries["rawSourceHashesMatchManifest"],
    }, indent=2))


if __name__ == "__main__":
    try:
        run_final()
    except Exception as error:
        failure_path = OUT / "heldout_attempt_failure.json"
        failure_path.write_text(json.dumps({
            "failedAtUtc": utc_now(),
            "errorType": type(error).__name__,
            "error": str(error),
            "note": "A technical retry may rerun this frozen evaluation only after a code-only repair; do not alter the frozen parameters.",
        }, indent=2) + "\n", encoding="utf-8")
        raise
