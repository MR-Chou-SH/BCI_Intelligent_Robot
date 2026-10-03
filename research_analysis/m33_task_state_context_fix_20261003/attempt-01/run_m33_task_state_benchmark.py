"""M33 train/dev gate, M32 sequence diagnostics, one-shot held-out, and M25 replay.

No raw EEG waveform or hardware is used. The M30 benchmark labels are accessed
only by the scorer after each model response; held-out calls are guarded by a
gate-freeze record and a one-shot start marker.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from integration.semantic_context_engine import SemanticContextEngine
from integration.semantic_context_sequence import (
    M30_CONTEXT_ENGINE_VERSION,
    M30_CONTEXT_PROMPT_VERSION,
    build_live_sequence_context_engine,
    prior_diagnostics,
)

OUT = Path(__file__).resolve().parent
M30 = ROOT / "research_analysis/m30_nextgen_semantic_context_20261003/attempt-01"
M32 = ROOT / "research_analysis/m32_interactive_text_context_demo_20261003/attempt-01"
M25 = ROOT / "research_analysis/m25_context_evidence_latency_20261002/attempt-01"
BENCHMARK = M30 / "semantic_context_sequence_benchmark_v2.json"
LOCK = M30 / "semantic_context_sequence_benchmark_lock.json"
TRAIN_DEV = OUT / "train_dev_results.jsonl"
REPEATS = OUT / "dev_repeatability.jsonl"
M32_CASES = OUT / "m32_order_regressions_v2.json"
GATE = OUT / "train_dev_gate.json"
HELDOUT = OUT / "held_out_results.jsonl"
HELDOUT_START = OUT / "held_out_attempt_started.json"
HELDOUT_SUMMARY = OUT / "held_out_summary.json"
TRANSFER = OUT / "m25_transfer_summary.json"
PRECISION_FLOOR = 0.95
MIN_ACTIVE_SUPPORT = 5


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _append(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()


def _benchmark() -> tuple[dict[str, Any], dict[str, Any]]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if _sha(BENCHMARK) != lock["sha256"]:
        raise ValueError("M30 benchmark differs from its frozen lock")
    return json.loads(BENCHMARK.read_text(encoding="utf-8")), lock


def _points(split: str) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    benchmark, _ = _benchmark()
    return [
        (episode, point)
        for episode in benchmark["episodes"] if episode["split"] == split
        for point in episode["decision_points"]
    ]


def _predict(engine: SemanticContextEngine, model_input: dict[str, Any]) -> dict[str, Any]:
    # evaluation_only is intentionally not passed to the model API.
    return engine.predict_next_target(
        scene=model_input["scene"],
        selection_history=list(model_input["selection_history"]),
        remaining_candidates=list(model_input["candidate_next_object_ids"]),
        current_task_state=dict(model_input.get("current_task_state") or {}),
    )


def _top_row(result: dict[str, Any]) -> dict[str, Any]:
    ranking = result.get("candidate_ranking") or []
    top_id = ranking[0] if ranking else None
    return next((row for row in result.get("candidate_scores", [])
                 if isinstance(row, dict) and row.get("candidate_id") == top_id), {})


def _gate_features(result: dict[str, Any]) -> dict[str, float]:
    rows = result.get("candidate_scores", [])
    scores = sorted((float(row.get("final_semantic_score", row.get("semantic_score", 0.0)))
                     for row in rows if isinstance(row, dict)), reverse=True)
    margin = scores[0] - scores[1] if len(scores) > 1 else 0.0
    q = result.get("context_prior", [])
    entropy = prior_diagnostics([float(value) for value in q]).get("normalized_entropy")
    top = _top_row(result)
    state = result.get("task_state") or {}
    components = [
        float(state.get("confidence", 0.0)),
        float(state.get("stability", 0.0)),
        float(top.get("candidate_task_continuation_score", 0.0)),
        1.0 - float(top.get("task_switch_penalty", 1.0)),
        min(1.0, margin / 0.20),
        max(0.0, 1.0 - float(entropy if entropy is not None else 1.0)),
    ]
    return {
        "task_state_confidence": components[0],
        "task_state_stability": components[1],
        "top_continuation": components[2],
        "top_task_continuity": components[3],
        "scaled_final_score_margin": components[4],
        "one_minus_normalized_entropy": components[5],
        "conservative_confidence": min(components),
    }


def _score(episode: dict[str, Any], point: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    labels = point["evaluation_only"]
    top_id = (result.get("candidate_ranking") or [None])[0]
    acceptable = labels.get("acceptable_next_targets", [])
    state = result.get("task_state") or {}
    return {
        "record_type": "m33_semantic_result",
        "episode_id": episode["episode_id"],
        "round_index": int(point["round_index"]),
        "split": episode["split"],
        "scene_family": episode["scene_family"],
        "selection_history": point["model_input"]["selection_history"],
        "candidate_ids": point["model_input"]["candidate_next_object_ids"],
        "expected_status": labels["expected_status"],
        "acceptable_next_targets": acceptable,
        "predicted_status": result.get("status"),
        "reason_code": result.get("reason_code"),
        "top_candidate": top_id,
        "top1_correct": top_id in acceptable if acceptable else False,
        "task_state": state,
        "gate_features": _gate_features(result),
        "eligible_informative": bool(result.get("eligible_informative")),
        "candidate_ranking": result.get("candidate_ranking", []),
        "q_global": result.get("q_global", []),
        "context_prior": result.get("context_prior", []),
        "candidate_scores": result.get("candidate_scores", []),
        "provenance": result.get("provenance", {}),
    }


def _run_train_dev() -> dict[str, Any]:
    benchmark, lock = _benchmark()
    client, model_id, _, _ = build_live_sequence_context_engine()
    engine = SemanticContextEngine(client, model_id, base_url=client.base_url, max_correction_retries=1)
    existing = _read_jsonl(TRAIN_DEV)
    metadata = next((row for row in existing if row.get("record_type") == "m33_run_metadata"), None)
    if existing and (not metadata or metadata.get("benchmark_sha256") != lock["sha256"]
                     or metadata.get("prompt_version") != M30_CONTEXT_PROMPT_VERSION
                     or metadata.get("engine_version") != M30_CONTEXT_ENGINE_VERSION
                     or metadata.get("model_id") != model_id):
        raise ValueError("existing M33 train/dev run does not match this frozen prompt/model/benchmark")
    if not existing:
        _append(TRAIN_DEV, {
            "record_type": "m33_run_metadata", "created_at_utc": _utc(),
            "benchmark_sha256": lock["sha256"], "model_id": model_id,
            "prompt_version": M30_CONTEXT_PROMPT_VERSION, "engine_version": M30_CONTEXT_ENGINE_VERSION,
            "evaluation_labels_sent_to_model": False, "api_key_persisted": False,
        })
    completed = {(row["episode_id"], int(row["round_index"])) for row in existing
                 if row.get("record_type") == "m33_semantic_result"}
    pending = [(episode, point) for split in ("train", "dev")
               for episode, point in _points(split)
               if (episode["episode_id"], int(point["round_index"])) not in completed]
    for index, (episode, point) in enumerate(pending, 1):
        result = _predict(engine, point["model_input"])
        record = _score(episode, point, result)
        record["recorded_at_utc"] = _utc()
        _append(TRAIN_DEV, record)
        if index % 10 == 0 or index == len(pending):
            print("train/dev {}/{} complete".format(index, len(pending)), flush=True)
    rows = [row for row in _read_jsonl(TRAIN_DEV) if row.get("record_type") == "m33_semantic_result"]
    expected = sum(len([point for point in episode["decision_points"] if episode["split"] in {"train", "dev"}])
                   for episode in benchmark["episodes"])
    if len(rows) != expected:
        raise ValueError("train/dev result count mismatch: {} != {}".format(len(rows), expected))
    return {"status": "PASS", "completed": len(rows), "train": sum(r["split"] == "train" for r in rows),
            "dev": sum(r["split"] == "dev" for r in rows), "model_id": model_id}


def _repeat_dev(repeat_count: int = 6) -> dict[str, Any]:
    if REPEATS.exists():
        return {"status": "already_recorded", "count": len(_read_jsonl(REPEATS))}
    client, model_id, _, _ = build_live_sequence_context_engine()
    engine = SemanticContextEngine(client, model_id, base_url=client.base_url, max_correction_retries=1)
    result_rows = [row for row in _read_jsonl(TRAIN_DEV)
                   if row.get("record_type") == "m33_semantic_result" and row.get("split") == "dev"]
    result_rows.sort(key=lambda row: (row["scene_family"], row["episode_id"], row["round_index"]))
    points = {(episode["episode_id"], int(point["round_index"])): (episode, point)
              for episode, point in _points("dev")}
    # Fixed coverage across dev episodes, preferring both early and later history states.
    selected = []
    seen_episodes: set[str] = set()
    ordered = sorted(result_rows, key=lambda row: (row["round_index"] not in {1, 2}, row["scene_family"], row["episode_id"]))
    for row in ordered:
        if row["episode_id"] not in seen_episodes:
            selected.append(row)
            seen_episodes.add(row["episode_id"])
        if len(selected) >= repeat_count:
            break
    comparisons = []
    for row in selected:
        episode, point = points[(row["episode_id"], int(row["round_index"]))]
        repeated = _predict(engine, point["model_input"])
        primary_q = [float(item["q"]) for item in row["q_global"]]
        repeat_q = [float(item["q"]) for item in repeated.get("q_global", [])]
        q_l1 = sum(abs(a - b) for a, b in zip(primary_q, repeat_q)) if len(primary_q) == len(repeat_q) else None
        record = {
            "record_type": "m33_repeatability", "episode_id": episode["episode_id"],
            "round_index": int(point["round_index"]), "status_primary": row["predicted_status"],
            "status_repeat": repeated.get("status"), "top_primary": row["top_candidate"],
            "top_repeat": (repeated.get("candidate_ranking") or [None])[0],
            "ranking_exact": row["candidate_ranking"] == repeated.get("candidate_ranking"),
            "q_l1": q_l1, "task_state_primary": row["task_state"],
            "task_state_repeat": repeated.get("task_state"),
        }
        comparisons.append(record)
        _append(REPEATS, record)
    return {
        "status": "PASS" if comparisons else "FAIL", "repeat_count": len(comparisons),
        "top1_agreement": sum(r["top_primary"] == r["top_repeat"] for r in comparisons) / len(comparisons) if comparisons else None,
        "ranking_exact_rate": sum(r["ranking_exact"] for r in comparisons) / len(comparisons) if comparisons else None,
        "mean_q_l1": statistics.mean(r["q_l1"] for r in comparisons if r["q_l1"] is not None) if comparisons else None,
    }


def _tune_gate() -> dict[str, Any]:
    if GATE.exists():
        return json.loads(GATE.read_text(encoding="utf-8"))
    rows = [row for row in _read_jsonl(TRAIN_DEV) if row.get("record_type") == "m33_semantic_result"]
    if len(rows) != 80 or {row["split"] for row in rows} != {"train", "dev"}:
        raise ValueError("gate requires the complete frozen M30 train+dev predictions only")
    eligible = [row for row in rows if row["predicted_status"] == "informative" and row["eligible_informative"]]
    thresholds = sorted({0.0, *(float(row["gate_features"]["conservative_confidence"]) for row in eligible)})
    candidates = []
    for threshold in thresholds:
        active = [row for row in eligible if row["gate_features"]["conservative_confidence"] >= threshold]
        if len(active) < MIN_ACTIVE_SUPPORT:
            continue
        correct = sum(bool(row["top1_correct"]) for row in active)
        precision = correct / len(active)
        if precision >= PRECISION_FLOOR:
            candidates.append((len(active), threshold, precision, correct))
    if not candidates:
        gate = {
            "record_type": "m33_train_dev_gate", "status": "NO_FEASIBLE_OPERATING_POINT",
            "precision_floor": PRECISION_FLOOR, "minimum_active_support": MIN_ACTIVE_SUPPORT,
            "threshold": None, "train_dev_active_count": 0,
            "reason": "No train/dev threshold achieved the predeclared precision floor.",
            "held_out_used_for_gate_selection": False,
        }
    else:
        active_n, threshold, precision, correct = max(candidates, key=lambda item: (item[0], item[1]))
        gate = {
            "record_type": "m33_train_dev_gate", "status": "FROZEN",
            "created_at_utc": _utc(), "benchmark_sha256": json.loads(LOCK.read_text(encoding="utf-8"))["sha256"],
            "prompt_version": M30_CONTEXT_PROMPT_VERSION, "engine_version": M30_CONTEXT_ENGINE_VERSION,
            "precision_floor": PRECISION_FLOOR, "minimum_active_support": MIN_ACTIVE_SUPPORT,
            "threshold": threshold, "train_dev_active_count": active_n,
            "train_dev_correct_count": correct, "train_dev_precision": precision,
            "train_dev_coverage": active_n / len(rows),
            "train_dev_all_count": len(rows), "train_dev_eligible_count": len(eligible),
            "feature_formula": "min(task_state_confidence, task_state_stability, top_continuation, 1-top_task_switch_penalty, min(1, final_score_margin/0.20), 1-normalized_entropy)",
            "held_out_used_for_gate_selection": False,
            "training_episode_round_keys_sha256": hashlib.sha256("\n".join(sorted(
                "{}:{}".format(r["episode_id"], r["round_index"]) for r in rows
            )).encode("utf-8")).hexdigest(),
        }
    with GATE.open("x", encoding="utf-8") as stream:
        json.dump(gate, stream, ensure_ascii=False, indent=2)
    gate["gate_sha256"] = _sha(GATE)
    return gate


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labeled = [row for row in rows if row.get("acceptable_next_targets")]
    eligible = [row for row in rows if row.get("predicted_status") == "informative" and row.get("eligible_informative")]
    active_correct = sum(bool(row.get("top1_correct")) for row in eligible)
    labeled_informative = [row for row in labeled if row.get("predicted_status") == "informative"]
    expected_ambiguous = [row for row in rows if row.get("expected_status") == "ambiguous"]
    return {
        "count": len(rows),
        "labeled_target_count": len(labeled),
        "top1_accuracy_labeled": sum(bool(row.get("top1_correct")) for row in labeled) / len(labeled) if labeled else None,
        "informative_top1_accuracy": sum(bool(row.get("top1_correct")) for row in labeled_informative) / len(labeled_informative) if labeled_informative else None,
        "engine_eligible_count": len(eligible),
        "engine_eligible_coverage": len(eligible) / len(rows) if rows else None,
        "engine_eligible_precision": active_correct / len(eligible) if eligible else None,
        "predicted_status_counts": dict(Counter(row.get("predicted_status") for row in rows)),
        "expected_ambiguous_count": len(expected_ambiguous),
        "expected_ambiguous_recognized": sum(row.get("predicted_status") == "ambiguous" for row in expected_ambiguous),
        "false_informative_on_expected_ambiguous": sum(row.get("predicted_status") == "informative" and row.get("eligible_informative") for row in expected_ambiguous),
        "candidate_row_downgrade_count": sum(
            len([warning for d in row.get("provenance", {}).get("diagnostics", [])
                 for warning in d.get("candidate_row_downgrades", [])]) for row in rows
        ),
        "fallback_context_off_count": sum(row.get("predicted_status") == "context_off" for row in rows),
    }


def _gate_frozen_before_heldout() -> bool:
    if not GATE.is_file():
        return False
    if not HELDOUT_START.exists():
        return True
    try:
        gate = json.loads(GATE.read_text(encoding="utf-8"))
        marker = json.loads(HELDOUT_START.read_text(encoding="utf-8"))
        gate_time = datetime.fromisoformat(str(gate["created_at_utc"]).replace("Z", "+00:00"))
        start_time = datetime.fromisoformat(str(marker["started_at_utc"]).replace("Z", "+00:00"))
        return gate.get("status") == "FROZEN" and gate_time <= start_time
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _m32_order_regressions() -> dict[str, Any]:
    if M32_CASES.exists():
        return json.loads(M32_CASES.read_text(encoding="utf-8"))
    benchmark_path = M32 / "text_scene_sequence_benchmark.json"
    result_path = M32 / "text_scene_sequence_results.jsonl"
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    parse_records = [row for row in _read_jsonl(result_path) if row.get("record_type") == "scene_parse" and row.get("run_status") == "parsed"]
    parsed_scenes = {row["scene_id"]: row["parsed_scene"] for row in parse_records}
    client, model_id, _, _ = build_live_sequence_context_engine()
    engine = SemanticContextEngine(client, model_id, base_url=client.base_url, max_correction_retries=1)
    trajectories: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for scene_doc in benchmark["scenes"]:
        parsed = parsed_scenes.get(scene_doc["scene_id"])
        if not parsed:
            continue
        for trajectory in scene_doc["trajectories"]:
            trajectories[trajectory["trajectory_id"]] = (scene_doc, trajectory)

    def _runtime_ids(scene_doc: dict[str, Any], parsed: dict[str, Any], human_ids: list[str]) -> list[str]:
        benchmark_types = {obj["object_id"]: obj.get("object_type") for obj in scene_doc.get("objects", [])}
        runtime_types = {obj.get("object_type"): obj["id"] for obj in parsed.get("objects", [])
                         if obj.get("selectable", False)}
        mapped = []
        for human_id in human_ids:
            object_type = benchmark_types.get(human_id)
            runtime_id = runtime_types.get(object_type) or runtime_types.get(human_id)
            if runtime_id is None and isinstance(object_type, str) and "wireless" in object_type.casefold():
                wireless_matches = [(runtime_type, object_id) for runtime_type, object_id in runtime_types.items()
                                    if isinstance(runtime_type, str) and "wireless" in runtime_type.casefold()
                                    and any(token in runtime_type.casefold() for token in ("charg", "dock", "stand"))]
                if len(wireless_matches) == 1:
                    runtime_id = wireless_matches[0][1]
            if runtime_id is None:
                raise ValueError("M32 object {} ({}) could not be mapped to its accepted parser scene".format(human_id, object_type))
            mapped.append(runtime_id)
        return mapped
    pairs = [
        ("A_kitchen_apple_knife", "kitchen-apple-first", "kitchen-knife-first"),
        ("B_electronics_phone_charger", "electronics-desk-phone-first", "electronics-desk-charger-first"),
    ]
    manual_rows = []
    pair_summaries = []
    for pair_name, forward_id, reverse_id in pairs:
        pair_values = []
        for trajectory_id in (forward_id, reverse_id):
            scene_doc, trajectory = trajectories[trajectory_id]
            parsed = parsed_scenes[scene_doc["scene_id"]]
            point = next(point for point in trajectory["decision_points"] if int(point["selection_round"]) == 2)
            history = _runtime_ids(scene_doc, parsed, point["history_object_ids"])
            candidates = [obj["id"] for obj in parsed["objects"] if obj.get("selectable", False) and obj["id"] not in set(history)]
            result = _predict(engine, {
                "scene": parsed, "selection_history": history,
                "candidate_next_object_ids": candidates,
                "current_task_state": {"task_context": "Infer the ongoing task from the complete ordered accepted history."},
            })
            row = {
                "case": pair_name, "trajectory_id": trajectory_id,
                "history_names": point["history_object_ids"], "candidate_ids": candidates,
                "task_state": result.get("task_state"), "status": result.get("status"),
                "eligible_informative": result.get("eligible_informative"),
                "ranking": result.get("candidate_ranking"), "q_global": result.get("q_global"),
                "reason_code": result.get("reason_code"),
                "diagnostics": result.get("provenance", {}).get("diagnostics", []),
            }
            manual_rows.append(row)
            pair_values.append(row)
        q_left = {entry["candidate_id"]: float(entry["q"]) for entry in pair_values[0]["q_global"]}
        q_right = {entry["candidate_id"]: float(entry["q"]) for entry in pair_values[1]["q_global"]}
        pair_summaries.append({
            "pair": pair_name, "same_candidate_ids": pair_values[0]["candidate_ids"] == pair_values[1]["candidate_ids"],
            "task_state_changed": pair_values[0]["task_state"] != pair_values[1]["task_state"],
            "ranking_changed": pair_values[0]["ranking"] != pair_values[1]["ranking"],
            "q_l1": sum(abs(q_left[key] - q_right[key]) for key in q_left if key in q_right),
        })
    # M32 mixed-ambiguous reverse-history case E; no expected target is sent.
    scene_doc, trajectory = trajectories["mixed-ambiguous-phone-first"]
    parsed = parsed_scenes[scene_doc["scene_id"]]
    point = next(point for point in trajectory["decision_points"] if int(point["selection_round"]) == 2)
    history = _runtime_ids(scene_doc, parsed, point["history_object_ids"])
    candidates = [obj["id"] for obj in parsed["objects"] if obj.get("selectable", False) and obj["id"] not in set(history)]
    mixed = _predict(engine, {"scene": parsed, "selection_history": history,
                             "candidate_next_object_ids": candidates,
                             "current_task_state": {"task_context": "Infer the ongoing task from the complete ordered accepted history."}})
    manual_rows.append({"case": "E_mixed_ambiguous", "history_names": point["history_object_ids"],
                        "candidate_ids": candidates, "task_state": mixed.get("task_state"),
                        "status": mixed.get("status"), "eligible_informative": mixed.get("eligible_informative"),
                        "ranking": mixed.get("candidate_ranking"), "q_global": mixed.get("q_global"),
                        "reason_code": mixed.get("reason_code"),
                        "diagnostics": mixed.get("provenance", {}).get("diagnostics", [])})
    result = {
        "record_type": "m33_m32_order_regression", "created_at_utc": _utc(),
        "m32_benchmark_sha256": _sha(benchmark_path), "cases": manual_rows,
        "reversed_order_pairs": pair_summaries,
        "all_pairs_preserve_same_candidates": all(item["same_candidate_ids"] for item in pair_summaries),
        "all_pairs_change_task_or_prior": all(item["task_state_changed"] or item["q_l1"] > 1e-6 for item in pair_summaries),
        "mixed_case_not_authorized": mixed.get("status") != "informative" or not mixed.get("eligible_informative"),
    }
    with M32_CASES.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    return result


def _run_heldout() -> dict[str, Any]:
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    if gate.get("status") != "FROZEN" or gate.get("held_out_used_for_gate_selection") is not False:
        raise ValueError("held-out run requires a frozen feasible train/dev gate")
    if HELDOUT_START.exists() or HELDOUT.exists() or HELDOUT_SUMMARY.exists():
        raise FileExistsError("M33 held-out is one-shot and has already started or completed")
    client, model_id, _, _ = build_live_sequence_context_engine()
    if model_id != next(row["model_id"] for row in _read_jsonl(TRAIN_DEV) if row.get("record_type") == "m33_run_metadata"):
        raise ValueError("held-out model differs from train/dev model")
    # Create an irreversible local marker before the first held-out request.
    with HELDOUT_START.open("x", encoding="utf-8") as stream:
        json.dump({"started_at_utc": _utc(), "gate_sha256": _sha(GATE),
                   "prompt_version": M30_CONTEXT_PROMPT_VERSION, "held_out_calls": 0}, stream, indent=2)
    engine = SemanticContextEngine(client, model_id, base_url=client.base_url, max_correction_retries=1)
    pending = _points("held_out")
    _append(HELDOUT, {"record_type": "m33_heldout_metadata", "created_at_utc": _utc(),
                      "gate_sha256": _sha(GATE), "gate_threshold": gate["threshold"],
                      "benchmark_sha256": json.loads(LOCK.read_text(encoding="utf-8"))["sha256"],
                      "prompt_version": M30_CONTEXT_PROMPT_VERSION, "engine_version": M30_CONTEXT_ENGINE_VERSION,
                      "labels_sent_to_model": False, "repeat_calls": 0})
    for index, (episode, point) in enumerate(pending, 1):
        result = _predict(engine, point["model_input"])
        record = _score(episode, point, result)
        record["recorded_at_utc"] = _utc()
        _append(HELDOUT, record)
        if index % 10 == 0 or index == len(pending):
            print("held-out {}/{} complete".format(index, len(pending)), flush=True)
    rows = [row for row in _read_jsonl(HELDOUT) if row.get("record_type") == "m33_semantic_result"]
    if len(rows) != 46:
        raise ValueError("held-out result count must be 46")
    threshold = float(gate["threshold"])
    active = [row for row in rows if row["predicted_status"] == "informative" and row["eligible_informative"]
              and row["gate_features"]["conservative_confidence"] >= threshold]
    active_correct = sum(bool(row["top1_correct"]) for row in active)
    summary = {
        "record_type": "m33_heldout_summary", "status": "PASS", "created_at_utc": _utc(),
        "benchmark_sha256": json.loads(LOCK.read_text(encoding="utf-8"))["sha256"],
        "gate_sha256": _sha(GATE), "gate_threshold": threshold,
        "held_out_calls": 46, "repeat_calls": 0, "held_out_used_for_gate_selection": False,
        "overall": _metrics(rows), "gate_active_count": len(active),
        "gate_active_coverage": len(active) / len(rows),
        "gate_active_correct": active_correct,
        "gate_active_precision": active_correct / len(active) if active else None,
        "by_scene_family": {},
    }
    for family in sorted({row["scene_family"] for row in rows}):
        summary["by_scene_family"][family] = _metrics([row for row in rows if row["scene_family"] == family])
    with HELDOUT_SUMMARY.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
    return summary


def _m25_transfer() -> dict[str, Any]:
    if TRANSFER.exists():
        return json.loads(TRANSFER.read_text(encoding="utf-8"))
    heldout = json.loads(HELDOUT_SUMMARY.read_text(encoding="utf-8"))
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    if gate.get("status") != "FROZEN" or heldout.get("gate_sha256") != _sha(GATE):
        raise ValueError("M25 replay requires the exact frozen gate and one-shot held-out summary")
    transfer_file = M30 / "run_m30_eeg_transfer.py"
    spec = importlib.util.spec_from_file_location("m30_readonly_transfer_helpers_m33", transfer_file)
    if spec is None or spec.loader is None:
        raise ImportError("M30 transfer helper cannot be loaded")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    m25 = helper._load_m25()
    manifest_path = M25 / "INPUT_MANIFEST.json"
    before_manifest = _sha(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_entries = manifest.get("inputs", [])
    before_sources = {entry["path"]: _sha(ROOT / entry["path"]) for entry in source_entries}
    trials, m25_manifest, folds, baseline_by, _assignment_by, _m24_rows, _m24_by = m25.load_inputs()
    cohort = m25.validate_cohort(trials, m25_manifest)
    selection_path = M25 / "shared_threshold_outer_fold_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("selectionUsesHeldOutSession") is not False or len(trials) != 88:
        raise ValueError("M25 frozen 88-trial cohort/selection validation failed")
    operating_points = ("FAST", "MEDIUM", "CONSERVATIVE")
    baselines: dict[tuple[str, str, str], dict[str, Any]] = {}
    configs: dict[tuple[str, str], dict[str, Any]] = {}
    for session in m25.SESSIONS:
        fold = folds[session]
        for op in operating_points:
            configs[(session, op)] = selection["folds"][f"{m25.FROZEN_BRANCH}|{session}|{op}"]["selectedSharedContextParameters"]
        for trial in (item for item in trials if item["session"] == session):
            for op in operating_points:
                config = m25.op_config(fold, op)
                baseline = m25.raw_stop(trial, "0.10", float(config["topThreshold"]),
                                        float(config["marginThreshold"]), 0.50, m25.STABILITY_PRIMARY)
                stored = baseline_by[(op, session, trial["trialId"])]
                if not math.isclose(float(baseline["stopTimeSeconds"]), float(stored["stopTimeSeconds"]), abs_tol=1e-9):
                    raise AssertionError("frozen M25 paired baseline differs")
                baselines[(op, session, trial["trialId"])] = baseline
    semantic_rows = [row for row in _read_jsonl(HELDOUT) if row.get("record_type") == "m33_semantic_result"]
    active = [row for row in semantic_rows if row["predicted_status"] == "informative" and row["eligible_informative"]
              and row["gate_features"]["conservative_confidence"] >= float(gate["threshold"])]
    transfer_rows = [{
        "expectedStatus": row["expected_status"],
        "acceptableNextTargets": row["acceptable_next_targets"],
        "qGlobal": row["q_global"],
        "episodeId": row["episode_id"],
        "roundIndex": row["round_index"],
        "sceneFamily": row["scene_family"],
        "apiLatencyMs": row.get("provenance", {}).get("api_latency_ms", 0.0),
    } for row in active]
    gate_rate = len(active) / len(semantic_rows) if semantic_rows else 0.0
    accum: dict[str, dict[str, Any]] = {op: {"baseline_correct": 0, "context_correct": 0,
        "gain": [], "active_gain": [], "assigned": 0, "applied": 0, "wrong_early_stops": 0,
        "context_caused_errors": 0, "no_delay_violations": 0, "mapping_failures": 0} for op in operating_points}
    for seed in range(100):
        for trial in trials:
            assignment_info = helper._assignment_for_trial(
                m25, trial, transfer_rows, gate_rate, seed + 33001, "M33_PRECISION",
                __import__("integration.semantic_context_sequence", fromlist=["paginate_candidates"]).paginate_candidates,
                __import__("integration.semantic_context_sequence", fromlist=["project_global_prior"]).project_global_prior,
            )
            assignment = assignment_info.get("assignment")
            for op in operating_points:
                baseline = baselines[(op, trial["session"], trial["trialId"])]
                base_correct = int(baseline["selectedClassIndex"] == int(trial["trueClassIndex"]))
                result = helper._simulate(m25, trial, baseline, assignment,
                                          float(assignment.get("apiLatencySeconds", 0.0)) if assignment else 0.0,
                                          1.0, configs[(trial["session"], op)])
                stats = accum[op]
                stats["baseline_correct"] += base_correct
                stats["context_correct"] += int(result["selectedIndex"] == int(trial["trueClassIndex"]))
                gain = float(baseline["stopTimeSeconds"]) - float(result["stop"])
                stats["gain"].append(gain)
                if result["contextApplied"]:
                    stats["active_gain"].append(gain)
                    stats["applied"] += 1
                stats["assigned"] += int(bool(assignment_info.get("gateActive")))
                stats["mapping_failures"] += int(bool(assignment_info.get("mapFailure")))
                stats["wrong_early_stops"] += int(bool(result["wrongEarlyStop"]))
                stats["context_caused_errors"] += int(bool(result["contextCausedError"]))
                stats["no_delay_violations"] += int(float(result["stop"]) > float(baseline["stopTimeSeconds"]) + 1e-9)
    total = 100 * len(trials)
    per_op = {}
    for op, stats in accum.items():
        per_op[op] = {
            "n_trial_seed_pairs": total,
            "baseline_accuracy": stats["baseline_correct"] / total,
            "context_accuracy": stats["context_correct"] / total,
            "accuracy_delta_pp": 100.0 * (stats["context_correct"] - stats["baseline_correct"]) / total,
            "mean_all_trial_gain_seconds": statistics.mean(stats["gain"]),
            "context_applied_count": stats["applied"],
            "mean_context_active_gain_seconds": statistics.mean(stats["active_gain"]) if stats["active_gain"] else None,
            "assignment_rate": stats["assigned"] / total,
            "application_rate": stats["applied"] / total,
            "mapping_failures": stats["mapping_failures"],
            "wrong_early_stops": stats["wrong_early_stops"],
            "context_caused_errors": stats["context_caused_errors"],
            "no_delay_violations": stats["no_delay_violations"],
        }
    after_sources = {entry["path"]: _sha(ROOT / entry["path"]) for entry in source_entries}
    if _sha(manifest_path) != before_manifest or before_sources != after_sources:
        raise AssertionError("M25/M23 frozen inputs changed during read-only replay")
    report = {
        "record_type": "m33_m25_transfer_simulation", "status": "PASS",
        "evidence_boundary": "historical EEG feature replay; no raw EEG waveform, hardware, Quest, ND8, or COM used",
        "m25_manifest_sha256_before": before_manifest, "m25_manifest_sha256_after": _sha(manifest_path),
        "m25_source_hashes_unchanged": True, "m25_cohort": cohort,
        "heldout_semantic_gate_sha256": _sha(GATE), "heldout_gate_rate": gate_rate,
        "heldout_active_semantic_rows": len(active), "deterministic_seed_count": 100,
        "lambda_ctx": 1.0, "paired_lambda_zero_baseline": "exact M25 frozen EEG stop",
        "m25_frozen_operating_points": per_op,
    }
    with TRANSFER.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def _preheldout() -> dict[str, Any]:
    run = _run_train_dev()
    repeat = _repeat_dev()
    manual = _m32_order_regressions()
    gate = _tune_gate()
    rows = [row for row in _read_jsonl(TRAIN_DEV) if row.get("record_type") == "m33_semantic_result"]
    software_checks = {
        "gate_meets_train_dev_precision_floor": gate.get("status") == "FROZEN" and gate.get("train_dev_precision", 0.0) >= PRECISION_FLOOR,
        "m32_reversed_pairs_keep_candidate_ids": manual.get("all_pairs_preserve_same_candidates", False),
        "m32_reversed_pairs_change_task_or_q": manual.get("all_pairs_change_task_or_prior", False),
        "m32_mixed_case_not_authorized": manual.get("mixed_case_not_authorized", False),
        "gate_was_frozen_before_first_heldout_call": _gate_frozen_before_heldout(),
    }
    report = {"status": "PASS" if all(software_checks.values()) else "FAIL",
              "train_dev_run": run, "train_dev_metrics": _metrics(rows),
              "dev_repeatability": repeat, "m32_order_regressions": manual, "gate": gate,
              "software_checks": software_checks,
              "held_out_started": HELDOUT_START.exists()}
    summary_path = OUT / "preheldout_summary.json"
    with summary_path.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("preheldout", "heldout", "transfer"))
    args = parser.parse_args()
    if args.phase == "preheldout":
        result = _preheldout()
    elif args.phase == "heldout":
        result = _run_heldout()
    else:
        result = _m25_transfer()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"PASS", "FROZEN", "already_recorded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
