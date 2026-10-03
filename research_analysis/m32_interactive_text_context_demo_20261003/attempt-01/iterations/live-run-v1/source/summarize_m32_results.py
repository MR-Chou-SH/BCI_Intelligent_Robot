"""Score M32 live outputs against the locked, independently authored labels."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent
BENCH = ROOT / "text_scene_sequence_benchmark.json"
RESULTS = ROOT / "text_scene_sequence_results.jsonl"


def _rate(numerator: int, denominator: int) -> dict:
    return {"numerator": numerator, "denominator": denominator,
            "rate": round(numerator / denominator, 4) if denominator else None}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _write_json_exclusive(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _write_csv_exclusive(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _context_rows(result: dict | None) -> tuple[list[str], dict[str, dict], dict[str, float]]:
    if not isinstance(result, dict):
        return [], {}, {}
    ranking = result.get("candidate_ranking", [])
    scores = {row.get("candidate_id"): row for row in result.get("candidate_scores", [])
              if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)}
    q = {row.get("candidate_id"): float(row["q"]) for row in result.get("q_global", [])
         if isinstance(row, dict) and isinstance(row.get("candidate_id"), str)
         and isinstance(row.get("q"), (int, float))}
    return list(ranking), scores, q


def summarize() -> dict:
    benchmark = json.loads(BENCH.read_text(encoding="utf-8"))
    records = [json.loads(line) for line in RESULTS.read_text(encoding="utf-8").splitlines() if line.strip()]
    parses = {row["scene_id"]: row for row in records if row.get("record_type") == "scene_parse"}
    contexts = {row["point_id"]: row for row in records
                if row.get("record_type") == "context" and row.get("repeat_of_point_id") is None}
    repeats = [row for row in records if row.get("record_type") == "repeatability"]

    parser_per_scene = []
    parsed_scenes: dict[str, dict] = {}
    total_gold_objects = total_exact_objects = total_extra_objects = total_missing_objects = 0
    expected_colors = color_correct = color_missing_or_wrong = invented_colors = 0
    expected_states = state_correct = state_missing_or_wrong = invented_states = 0
    ungrounded_object_attempts = raw_object_predictions = 0
    parser_latencies = []
    object_map_by_scene: dict[str, dict[str, str]] = {}

    for case in benchmark["scenes"]:
        record = parses.get(case["scene_id"], {})
        parsed = record.get("parsed_scene") or {}
        parsed_objects = parsed.get("objects", [])
        parsed_by_mention = {item.get("source_mention"): item for item in parsed_objects if item.get("source_mention")}
        parsed_by_actual_id = {item.get("id"): item for item in parsed_objects}
        gold_by_mention = {obj["mention"]: obj for obj in case["objects"]}
        exact = sum(1 for mention in gold_by_mention if mention in parsed_by_mention)
        extra = sum(1 for mention in parsed_by_mention if mention not in gold_by_mention)
        missing = len(gold_by_mention) - exact
        total_gold_objects += len(gold_by_mention)
        total_exact_objects += exact
        total_extra_objects += extra
        total_missing_objects += missing

        mention_to_actual = {}
        for mention, expected in gold_by_mention.items():
            actual = parsed_by_mention.get(mention)
            if actual:
                mention_to_actual[expected["object_id"]] = actual["id"]

        for expected in case["objects"]:
            actual = parsed_by_mention.get(expected["mention"])
            expected_color = expected.get("explicit_color")
            expected_state = expected.get("explicit_state")
            if expected_color is not None:
                expected_colors += 1
                if actual and actual.get("color") == expected_color:
                    color_correct += 1
                else:
                    color_missing_or_wrong += 1
            elif actual and actual.get("color") is not None:
                invented_colors += 1
            if expected_state is not None:
                expected_states += 1
                if actual and actual.get("current_state") == expected_state:
                    state_correct += 1
                else:
                    state_missing_or_wrong += 1
            elif actual and actual.get("current_state"):
                invented_states += 1

        diagnostics = record.get("parse_diagnostics") or {}
        raw_object_predictions += int(diagnostics.get("raw_object_count", 0) or 0)
        ungrounded_object_attempts += int(diagnostics.get("rejected_object_count", 0) or 0)
        provenance = record.get("provenance") or {}
        if isinstance(provenance.get("api_latency_ms"), (int, float)):
            parser_latencies.append(float(provenance["api_latency_ms"]))
        parsed_scenes[case["scene_id"]] = parsed
        object_map_by_scene[case["scene_id"]] = mention_to_actual
        parser_per_scene.append({
            "scene_id": case["scene_id"], "family": case["family"],
            "run_status": record.get("run_status", "missing"),
            "expected_object_count": len(gold_by_mention), "parsed_object_count": len(parsed_objects),
            "exact_mentions": exact, "missing_mentions": missing, "extra_mentions": extra,
            "color_expected": sum(1 for item in case["objects"] if item.get("explicit_color") is not None),
            "state_expected": sum(1 for item in case["objects"] if item.get("explicit_state") is not None),
            "parser_latency_ms": provenance.get("api_latency_ms"),
        })

    context_records = []
    context_csv_rows = []
    status_counts: dict[str, int] = {}
    candidate_dimension_correct = q_normalized = selected_removal_correct = candidate_set_correct = 0
    top1_ok = topk_ok = evaluated_top = evaluated_contexts = 0
    ambiguity_expected = ambiguity_recognized = 0
    specific_relation_rows = valid_q_rows = context_failures = 0
    context_latencies = []
    total_tokens = 0
    points_by_id = {}
    for case in benchmark["scenes"]:
        parsed_scene = parsed_scenes.get(case["scene_id"], {})
        actual_by_id = {item.get("id"): item for item in parsed_scene.get("objects", [])}
        id_to_name = {item.get("id"): item.get("name_zh") or item.get("name") or item.get("id")
                      for item in parsed_scene.get("objects", [])}
        actual_to_benchmark = {actual_id: benchmark_id for benchmark_id, actual_id in
                               object_map_by_scene.get(case["scene_id"], {}).items()}
        gold_by_id = {item["object_id"]: item for item in case["objects"]}
        for trajectory in case["trajectories"]:
            for point in trajectory["decision_points"]:
                points_by_id[point["point_id"]] = (case, trajectory, point)
                record = contexts.get(point["point_id"], {})
                result = record.get("context_result") or {}
                ranking_actual, score_rows, q_by_actual = _context_rows(result)
                ranking = [actual_to_benchmark.get(item, "UNKNOWN:" + str(item)) for item in ranking_actual]
                candidate_ids_actual = list(result.get("candidate_ids", [])) if isinstance(result, dict) else []
                expected_actual = [object_map_by_scene.get(case["scene_id"], {}).get(ref) for ref in point["candidate_object_ids"]]
                expected_actual = [item for item in expected_actual if item]
                candidate_set_ok = set(candidate_ids_actual) == set(expected_actual)
                q_rows = result.get("q_global", []) if isinstance(result, dict) else []
                q_values = [float(row["q"]) for row in q_rows if isinstance(row, dict) and isinstance(row.get("q"), (int, float))]
                dimension_ok = result.get("q_dimension") == len(expected_actual) and len(q_rows) == len(expected_actual)
                q_sum = sum(q_values) if q_values else None
                normalized = q_sum is not None and math.isclose(q_sum, 1.0, abs_tol=1e-8)
                history_actual = [object_map_by_scene.get(case["scene_id"], {}).get(ref) for ref in point["history_object_ids"]]
                removed_ok = all(item not in candidate_ids_actual for item in history_actual if item)
                if record.get("run_status") != "completed" or not result:
                    context_failures += 1
                else:
                    evaluated_contexts += 1
                    status = str(result.get("status", "unknown"))
                    status_counts[status] = status_counts.get(status, 0) + 1
                    candidate_set_correct += int(candidate_set_ok)
                    candidate_dimension_correct += int(dimension_ok)
                    q_normalized += int(normalized)
                    selected_removal_correct += int(removed_ok)
                    valid_q_rows += int(dimension_ok and normalized)
                    for candidate_score in result.get("candidate_scores", []):
                        if isinstance(candidate_score, dict) and candidate_score.get("relation_type", "NONE") != "NONE":
                            specific_relation_rows += 1
                    if status == "ambiguous":
                        ambiguity_recognized += 1
                    if point.get("expected_ambiguity") is not None:
                        ambiguity_expected += 1
                    acceptable = set(point["acceptable_next_targets"])
                    if ranking:
                        evaluated_top += 1
                        top1_ok += int(ranking[0] in acceptable)
                        topk_ok += int(bool(set(ranking[:3]).intersection(acceptable)))
                    latency = (result.get("provenance") or {}).get("api_latency_ms")
                    if isinstance(latency, (int, float)):
                        context_latencies.append(float(latency))
                    usage = (result.get("provenance") or {}).get("usage") or {}
                    total_tokens += int(usage.get("total_tokens", 0) or 0)

                q_by_benchmark = {actual_to_benchmark.get(actual_id, actual_id): q
                                  for actual_id, q in q_by_actual.items()}
                top = ranking[0] if ranking else ""
                top_name = gold_by_id.get(top, {}).get("mention", id_to_name.get(ranking_actual[0], "") if ranking_actual else "")
                latency = (result.get("provenance") or {}).get("api_latency_ms") if isinstance(result, dict) else None
                context_csv_rows.append({
                    "scene_id": case["scene_id"], "family": case["family"],
                    "trajectory_id": trajectory["trajectory_id"], "point_id": point["point_id"],
                    "selection_round": point["selection_round"], "history_length": len(point["history_object_ids"]),
                    "candidate_count": len(point["candidate_object_ids"]), "context_status": result.get("status", "missing"),
                    "top_candidate_id": top, "top_candidate": top_name,
                    "top1_acceptable": int(bool(ranking) and ranking[0] in set(point["acceptable_next_targets"])),
                    "q_dimension": result.get("q_dimension", 0), "q_sum": q_sum,
                    "candidate_set_correct": int(candidate_set_ok), "selected_removal_correct": int(removed_ok),
                    "prior_top_mass": result.get("prior_top_mass"), "prior_margin": result.get("prior_margin"),
                    "normalized_entropy": result.get("normalized_entropy"), "api_latency_ms": latency,
                    "q_global_json": json.dumps(q_by_benchmark, ensure_ascii=False, separators=(",", ":")),
                    "ranking_json": json.dumps(ranking, ensure_ascii=False, separators=(",", ":")),
                })

    sensitivity_rows = []
    paired_groups: dict[str, list[tuple[dict, dict, dict]]] = {}
    for case, trajectory, point in points_by_id.values():
        pair_id = point.get("history_sensitivity_pair_id")
        if pair_id:
            paired_groups.setdefault(pair_id, []).append((case, trajectory, point))
    history_ranking_change = history_top_change = history_q_change = 0
    for pair_id, members in sorted(paired_groups.items()):
        (case_a, traj_a, point_a), (case_b, traj_b, point_b) = members
        record_a, record_b = contexts.get(point_a["point_id"], {}), contexts.get(point_b["point_id"], {})
        result_a, result_b = record_a.get("context_result") or {}, record_b.get("context_result") or {}
        map_a = {actual: benchmark_id for benchmark_id, actual in object_map_by_scene.get(case_a["scene_id"], {}).items()}
        map_b = {actual: benchmark_id for benchmark_id, actual in object_map_by_scene.get(case_b["scene_id"], {}).items()}
        rank_a = [map_a.get(item, str(item)) for item in result_a.get("candidate_ranking", [])]
        rank_b = [map_b.get(item, str(item)) for item in result_b.get("candidate_ranking", [])]
        q_a = {map_a.get(row["candidate_id"], row["candidate_id"]): float(row["q"])
               for row in result_a.get("q_global", []) if isinstance(row, dict) and isinstance(row.get("q"), (int, float))}
        q_b = {map_b.get(row["candidate_id"], row["candidate_id"]): float(row["q"])
               for row in result_b.get("q_global", []) if isinstance(row, dict) and isinstance(row.get("q"), (int, float))}
        same_candidates = set(point_a["candidate_object_ids"]) == set(point_b["candidate_object_ids"])
        ranking_changed = rank_a != rank_b
        top_changed = bool(rank_a and rank_b and rank_a[0] != rank_b[0])
        l1 = sum(abs(q_a.get(key, 0.0) - q_b.get(key, 0.0)) for key in set(q_a) | set(q_b))
        changed = l1 > 1e-6
        history_ranking_change += int(ranking_changed)
        history_top_change += int(top_changed)
        history_q_change += int(changed)
        sensitivity_rows.append({
            "pair_id": pair_id, "scene_id": case_a["scene_id"],
            "left_point_id": point_a["point_id"], "right_point_id": point_b["point_id"],
            "same_candidate_set": int(same_candidates),
            "left_history": json.dumps(point_a["history_object_ids"], ensure_ascii=False),
            "right_history": json.dumps(point_b["history_object_ids"], ensure_ascii=False),
            "left_ranking": json.dumps(rank_a, ensure_ascii=False),
            "right_ranking": json.dumps(rank_b, ensure_ascii=False),
            "ranking_changed": int(ranking_changed), "top_candidate_changed": int(top_changed),
            "q_l1_distance": round(l1, 6), "q_changed": int(changed),
        })

    base_by_point = contexts
    repeat_structural_exact = repeat_relation_exact = 0
    repeat_q_l1 = []
    repeat_rows = []
    for record in repeats:
        point_id = record.get("repeat_of_point_id")
        base = base_by_point.get(point_id, {}).get("context_result") or {}
        repeated = record.get("context_result") or {}
        base_ranking, base_scores, base_q = _context_rows(base)
        repeat_ranking, repeat_scores, repeat_q = _context_rows(repeated)
        base_relations = {key: row.get("relation_type") for key, row in base_scores.items()}
        repeat_relations = {key: row.get("relation_type") for key, row in repeat_scores.items()}
        structural = base.get("status") == repeated.get("status") and base_ranking == repeat_ranking
        relation_exact = base_relations == repeat_relations
        all_q_ids = set(base_q) | set(repeat_q)
        q_l1 = sum(abs(base_q.get(key, 0.0) - repeat_q.get(key, 0.0)) for key in all_q_ids)
        repeat_structural_exact += int(structural)
        repeat_relation_exact += int(relation_exact)
        repeat_q_l1.append(q_l1)
        repeat_rows.append({"point_id": point_id, "run_status": record.get("run_status"),
                            "structural_exact": int(structural), "relations_exact": int(relation_exact),
                            "q_l1_distance": round(q_l1, 6), "repeat_latency_ms": (repeated.get("provenance") or {}).get("api_latency_ms")})

    scene_summary = {
        "scene_count": len(benchmark["scenes"]),
        "scene_object_count_exact_7_or_8": sum(1 for row in parser_per_scene if row["parsed_object_count"] in {7, 8}),
        "object_mention_exact_match": _rate(total_exact_objects, total_gold_objects),
        "missing_gold_object_mentions": total_missing_objects,
        "extra_accepted_object_mentions": total_extra_objects,
        "raw_model_object_predictions": raw_object_predictions,
        "ungrounded_object_mentions_rejected": ungrounded_object_attempts,
        "explicit_color_correct": _rate(color_correct, expected_colors),
        "explicit_color_missing_or_wrong": color_missing_or_wrong,
        "unspecified_colors_invented": invented_colors,
        "explicit_state_correct": _rate(state_correct, expected_states),
        "explicit_state_missing_or_wrong": state_missing_or_wrong,
        "unspecified_states_invented": invented_states,
        "parser_latency_ms": {"mean": round(statistics.mean(parser_latencies), 3) if parser_latencies else None,
                               "median": round(statistics.median(parser_latencies), 3) if parser_latencies else None,
                               "p90": round(_percentile(parser_latencies, 0.90), 3) if parser_latencies else None},
        "per_scene": parser_per_scene,
    }
    context_summary = {
        "planned_decision_points": benchmark["decision_point_count"],
        "completed_decision_points": evaluated_contexts,
        "context_failures_or_missing": context_failures,
        "candidate_coverage": _rate(candidate_set_correct, evaluated_contexts),
        "selected_object_removal": _rate(selected_removal_correct, evaluated_contexts),
        "q_dimension_correct": _rate(candidate_dimension_correct, evaluated_contexts),
        "q_normalized": _rate(q_normalized, evaluated_contexts),
        "valid_candidate_and_q_rows": _rate(valid_q_rows, evaluated_contexts),
        "top1_acceptable_next_target": _rate(top1_ok, evaluated_top),
        "top3_accepts_an_independent_label": _rate(topk_ok, evaluated_top),
        "expected_ambiguity_points": ambiguity_expected,
        "model_ambiguous_status_count": ambiguity_recognized,
        "context_status_counts": status_counts,
        "specific_affordance_relation_rows": specific_relation_rows,
        "latency_ms": {"mean": round(statistics.mean(context_latencies), 3) if context_latencies else None,
                       "median": round(statistics.median(context_latencies), 3) if context_latencies else None,
                       "p90": round(_percentile(context_latencies, 0.90), 3) if context_latencies else None,
                       "max": round(max(context_latencies), 3) if context_latencies else None},
        "total_completion_tokens_reported": total_tokens,
        "history_sensitivity": {
            "same_candidate_set_pairs": len(sensitivity_rows),
            "ranking_changed": _rate(history_ranking_change, len(sensitivity_rows)),
            "top_candidate_changed": _rate(history_top_change, len(sensitivity_rows)),
            "q_changed": _rate(history_q_change, len(sensitivity_rows)),
            "pairs": sensitivity_rows,
        },
    }
    repeatability = {
        "repeat_calls": len(repeats),
        "structural_exact": _rate(repeat_structural_exact, len(repeats)),
        "relation_structure_exact": _rate(repeat_relation_exact, len(repeats)),
        "q_l1_distance_mean": round(statistics.mean(repeat_q_l1), 6) if repeat_q_l1 else None,
        "q_l1_distance_median": round(statistics.median(repeat_q_l1), 6) if repeat_q_l1 else None,
        "per_point": repeat_rows,
    }
    kitchen_walkthrough = []
    kitchen = next(scene for scene in benchmark["scenes"] if scene["scene_id"] == "kitchen")
    kitchen_path = next(path for path in kitchen["trajectories"] if path["trajectory_id"].endswith("apple-first"))
    kitchen_name = {item["object_id"]: item["mention"] for item in kitchen["objects"]}
    for point in kitchen_path["decision_points"][:3]:
        record = contexts.get(point["point_id"], {})
        result = record.get("context_result") or {}
        scene = parsed_scenes.get("kitchen", {})
        name_by_actual = {item["id"]: item.get("name_zh") or item.get("name") for item in scene.get("objects", [])}
        actual_to_ref = {actual: ref for ref, actual in object_map_by_scene.get("kitchen", {}).items()}
        q_rows = result.get("q_global", [])
        q_readable = [{"object": kitchen_name.get(actual_to_ref.get(row.get("candidate_id"), ""),
                                                   name_by_actual.get(row.get("candidate_id"), "unknown")),
                       "q": round(float(row["q"]), 6)}
                      for row in q_rows if isinstance(row, dict) and isinstance(row.get("q"), (int, float))]
        kitchen_walkthrough.append({
            "point_id": point["point_id"],
            "selection_history": [kitchen_name.get(item, item) for item in point["history_object_ids"]],
            "candidate_count": len(point["candidate_object_ids"]),
            "q_dimension": result.get("q_dimension"), "q_sum": round(sum(row["q"] for row in q_readable), 8),
            "status": result.get("status"), "q_global": q_readable,
        })

    summary = {
        "schema_version": "m32-sequential-context-summary-v1",
        "benchmark_id": benchmark["benchmark_id"],
        "benchmark_sha256": json.loads((ROOT / "text_scene_sequence_benchmark_lock.json").read_text(encoding="utf-8"))["canonical_sha256"],
        "run_model_id": next((row.get("model_id") for row in records if row.get("record_type") == "run_started"), None),
        "scene_parser": scene_summary,
        "context": context_summary,
        "repeatability": repeatability,
        "kitchen_three_round_walkthrough": kitchen_walkthrough,
        "boundaries": {"eeg_used": False, "quest_used": False, "image_input_implemented": False,
                       "real_vla_dispatch": False, "api_key_persisted": False},
    }

    _write_json_exclusive(ROOT / "scene_parser_summary.json", scene_summary)
    _write_json_exclusive(ROOT / "repeatability_summary.json", repeatability)
    _write_json_exclusive(ROOT / "history_sensitivity_summary.json", context_summary["history_sensitivity"])
    _write_json_exclusive(ROOT / "text_scene_sequence_summary.json", summary)
    _write_csv_exclusive(ROOT / "context_per_round.csv", [
        "scene_id", "family", "trajectory_id", "point_id", "selection_round", "history_length", "candidate_count",
        "context_status", "top_candidate_id", "top_candidate", "top1_acceptable", "q_dimension", "q_sum",
        "candidate_set_correct", "selected_removal_correct", "prior_top_mass", "prior_margin", "normalized_entropy",
        "api_latency_ms", "q_global_json", "ranking_json",
    ], context_csv_rows)
    _write_csv_exclusive(ROOT / "history_sensitivity_summary.csv", [
        "pair_id", "scene_id", "left_point_id", "right_point_id", "same_candidate_set", "left_history", "right_history",
        "left_ranking", "right_ranking", "ranking_changed", "top_candidate_changed", "q_l1_distance", "q_changed",
    ], sensitivity_rows)
    _write_csv_exclusive(ROOT / "scene_parser_per_scene.csv", [
        "scene_id", "family", "run_status", "expected_object_count", "parsed_object_count", "exact_mentions",
        "missing_mentions", "extra_mentions", "color_expected", "state_expected", "parser_latency_ms",
    ], parser_per_scene)
    return summary


if __name__ == "__main__":
    print(json.dumps(summarize(), ensure_ascii=False, indent=2))
