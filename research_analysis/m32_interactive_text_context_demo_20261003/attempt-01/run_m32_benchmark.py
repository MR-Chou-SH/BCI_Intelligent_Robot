"""Run the frozen M32 parser and sequential Context benchmark with DeepSeek."""

from __future__ import annotations

from collections import defaultdict
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import unicodedata

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.semantic_context_engine import SemanticContextEngine  # noqa: E402
from integration.semantic_context_demo_cli import CURRENT_TASK_STATE, remaining_selectable_candidates  # noqa: E402
from integration.semantic_intelligence import DeepSeekClientError, build_live_client  # noqa: E402
from integration.text_scene_parser import M32_SCENE_PARSER_VERSION, TextSceneParseError, TextSceneParser  # noqa: E402
from validate_m32_benchmark import validate as validate_benchmark  # noqa: E402


BENCHMARK_PATH = ROOT / "text_scene_sequence_benchmark.json"
RESULTS_PATH = ROOT / "text_scene_sequence_results.jsonl"
LOCK_PATH = ROOT / "text_scene_sequence_benchmark_lock.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _append(stream, record: dict[str, Any]) -> None:
    stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def _map_objects(benchmark_scene: dict[str, Any], parsed_scene: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    parsed_by_mention: dict[str, list[str]] = defaultdict(list)
    for item in parsed_scene.get("objects", []):
        if not item.get("selectable", False):
            continue
        mention = item.get("source_mention")
        if isinstance(mention, str):
            parsed_by_mention[_norm(mention)].append(item["id"])
    mapping: dict[str, str] = {}
    missing = []
    for expected in benchmark_scene["objects"]:
        ids = parsed_by_mention.get(_norm(expected["mention"]), [])
        if len(ids) == 1:
            mapping[expected["object_id"]] = ids[0]
        else:
            missing.append(expected["object_id"])
    return mapping, missing


def _record_context(
    *, client: Any, engine: SemanticContextEngine, parsed_scene: dict[str, Any], benchmark_scene: dict[str, Any],
    trajectory: dict[str, Any], point: dict[str, Any], mapping: dict[str, str], repeat_of: str | None = None,
) -> dict[str, Any]:
    missing_refs = [reference for reference in point["history_object_ids"] + point["candidate_object_ids"]
                    if reference not in mapping]
    base = {
        "record_type": "context" if repeat_of is None else "repeatability",
        "recorded_at_utc": _utc_now(),
        "scene_id": benchmark_scene["scene_id"],
        "family": benchmark_scene["family"],
        "trajectory_id": trajectory["trajectory_id"],
        "point_id": point["point_id"],
        "selection_round": point["selection_round"],
        "history_object_ids": list(point["history_object_ids"]),
        "candidate_object_ids": list(point["candidate_object_ids"]),
        "repeat_of_point_id": repeat_of,
    }
    if missing_refs:
        return {**base, "run_status": "scene_mapping_failed", "missing_object_ids": sorted(set(missing_refs))}
    history = [mapping[ref] for ref in point["history_object_ids"]]
    # Use the full parsed scene candidate set. Benchmark candidate IDs are
    # scorer-only and must not trim the model's view of remaining objects.
    candidates = remaining_selectable_candidates(parsed_scene, history)
    started = time.perf_counter()
    try:
        result = engine.predict_next_target(
            scene=parsed_scene,
            selection_history=history,
            remaining_candidates=candidates,
            current_task_state=dict(CURRENT_TASK_STATE),
        )
        error_type = None
    except (ValueError, DeepSeekClientError) as error:
        result = None
        error_type = type(error).__name__
    return {
        **base,
        "run_status": "completed" if result is not None else "context_error",
        "actual_history_ids": history,
        "actual_candidate_ids": candidates,
        "scorer_expected_candidate_ids": [mapping[ref] for ref in point["candidate_object_ids"]],
        "wall_time_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "context_result": result,
        "error_type": error_type,
    }


def run(
    *, results_path: Path = RESULTS_PATH, benchmark_path: Path = BENCHMARK_PATH,
    lock_path: Path = LOCK_PATH, cases_path: Path | None = None,
) -> dict[str, Any]:
    cases_path = cases_path or benchmark_path.parent / "text_scene_demo_cases.json"
    static = validate_benchmark(benchmark_path, lock_path, cases_path)
    if results_path.exists():
        raise FileExistsError("refusing to overwrite M32 result history")
    results_path.parent.mkdir(parents=True, exist_ok=True)
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    client, model_id, _, _ = build_live_client()
    base_url = os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    parser = TextSceneParser(client, model_id, max_correction_retries=1)
    engine = SemanticContextEngine(client, model_id, base_url=base_url, max_correction_retries=1)

    point_lookup = {
        point["point_id"]: (scene, trajectory, point)
        for scene in benchmark["scenes"] for trajectory in scene["trajectories"]
        for point in trajectory["decision_points"]
    }
    with results_path.open("x", encoding="utf-8", newline="\n") as stream:
        _append(stream, {
            "record_type": "run_started", "recorded_at_utc": _utc_now(),
            "benchmark_sha256": static["benchmark_sha256"],
            "model_id": model_id, "base_url": base_url,
            "parser_version": M32_SCENE_PARSER_VERSION,
            "context_engine_version": "m30-context-precondition-guard-v1",
            "planned_scene_parses": 6, "planned_context_points": 60,
            "planned_repeatability_calls": len(benchmark["repeatability_point_ids"]),
            "deepseek_key_persisted": False, "eeg_used": False, "quest_used": False,
        })
        parsed_by_scene: dict[str, tuple[dict[str, Any], dict[str, str], list[str]]] = {}
        for case in benchmark["scenes"]:
            started = time.perf_counter()
            try:
                # Evaluation-only object IDs, acceptable targets and labels are
                # deliberately not placed in this parser request.
                parsed = parser.parse(case["scene_text"])
                scene = parsed["scene"]
                mapping, missing = _map_objects(case, scene)
                status = "parsed"
                error_type = None
            except (TextSceneParseError, DeepSeekClientError, ValueError) as error:
                parsed = None
                scene = None
                mapping, missing = {}, [item["object_id"] for item in case["objects"]]
                status = "parse_error"
                error_type = type(error).__name__
            _append(stream, {
                "record_type": "scene_parse", "recorded_at_utc": _utc_now(),
                "scene_id": case["scene_id"], "family": case["family"],
                "run_status": status, "parsed_scene": scene,
                "parse_diagnostics": parsed["diagnostics"] if parsed else None,
                "provenance": parsed["provenance"] if parsed else None,
                "missing_object_ids": missing, "error_type": error_type,
                "wall_time_ms": round((time.perf_counter() - started) * 1000.0, 3),
            })
            if scene is not None:
                parsed_by_scene[case["scene_id"]] = (scene, mapping, missing)

        for case in benchmark["scenes"]:
            parsed_entry = parsed_by_scene.get(case["scene_id"])
            for trajectory in case["trajectories"]:
                for point in trajectory["decision_points"]:
                    if parsed_entry is None:
                        base = {
                            "record_type": "context", "recorded_at_utc": _utc_now(),
                            "scene_id": case["scene_id"], "family": case["family"],
                            "trajectory_id": trajectory["trajectory_id"], "point_id": point["point_id"],
                            "selection_round": point["selection_round"],
                            "history_object_ids": point["history_object_ids"],
                            "candidate_object_ids": point["candidate_object_ids"],
                            "run_status": "scene_parse_failed", "context_result": None,
                        }
                        _append(stream, base)
                        continue
                    scene, mapping, _ = parsed_entry
                    _append(stream, _record_context(
                        client=client, engine=engine, parsed_scene=scene, benchmark_scene=case,
                        trajectory=trajectory, point=point, mapping=mapping,
                    ))

        for point_id in benchmark["repeatability_point_ids"]:
            case, trajectory, point = point_lookup[point_id]
            parsed_entry = parsed_by_scene.get(case["scene_id"])
            if parsed_entry is None:
                _append(stream, {
                    "record_type": "repeatability", "recorded_at_utc": _utc_now(),
                    "scene_id": case["scene_id"], "family": case["family"],
                    "trajectory_id": trajectory["trajectory_id"], "point_id": point_id,
                    "repeat_of_point_id": point_id, "run_status": "scene_parse_failed",
                })
                continue
            scene, mapping, _ = parsed_entry
            _append(stream, _record_context(
                client=client, engine=engine, parsed_scene=scene, benchmark_scene=case,
                trajectory=trajectory, point=point, mapping=mapping, repeat_of=point_id,
            ))
        _append(stream, {
            "record_type": "run_finished", "recorded_at_utc": _utc_now(),
            "model_id": model_id, "benchmark_sha256": lock["canonical_sha256"],
            "api_key_persisted": False, "real_vla_dispatch": False,
            "eeg_used": False, "quest_used": False,
        })
    return {"status": "RUN_COMPLETE", "results_path": str(results_path), "model_id": model_id,
            "benchmark_sha256": lock["canonical_sha256"], "planned_context_points": 60,
            "repeatability_calls": len(benchmark["repeatability_point_ids"])}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--results", type=Path, default=RESULTS_PATH)
    cli.add_argument("--benchmark", type=Path, default=BENCHMARK_PATH)
    cli.add_argument("--lock", type=Path, default=LOCK_PATH)
    cli.add_argument("--cases", type=Path)
    try:
        args = cli.parse_args()
        print(json.dumps(run(results_path=args.results, benchmark_path=args.benchmark,
                             lock_path=args.lock, cases_path=args.cases), ensure_ascii=False, indent=2))
    except (DeepSeekClientError, FileExistsError, ValueError) as error:
        # Error details are intentionally reduced to the class name.
        print(json.dumps({"status": "BLOCKED_OR_INVALID", "error_type": type(error).__name__}, ensure_ascii=False))
        raise SystemExit(2)
