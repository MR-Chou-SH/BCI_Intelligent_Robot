"""Read-only verifier for the frozen M30 sequential semantic benchmark."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from integration.semantic_intelligence import SemanticSceneCore
from integration.semantic_context_relations import relation_compatibility


OUT = Path(__file__).resolve().parent
BENCHMARK = OUT / "semantic_context_sequence_benchmark_v2.json"
LOCK = OUT / "semantic_context_sequence_benchmark_lock.json"
EXPECTED_FAMILIES = {
    "desktop_office": 7,
    "storage_organization": 7,
    "household_cleaning": 3,
    "tools_simple_manipulation": 3,
    "kitchen_food_preparation": 3,
    "assistive_handover": 3,
    "state_dependent": 4,
    "ambiguous_adversarial": 3,
}
EXPECTED_SPLITS = {"train": 14, "dev": 6, "held_out": 13}
FORBIDDEN_MODEL_KEYS = {
    "evaluation_only", "acceptable_next_targets", "acceptable_relations_by_target",
    "expected_target", "expected_target_ids", "expected_relation", "true_label",
    "future_selection", "future_eeg", "answer_key", "gold_label", "gold_target",
    "ground_truth", "label_source", "trajectory_object_ids_evaluation_only",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk_keys(value: Any, path: str = "model_input") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in FORBIDDEN_MODEL_KEYS:
                found.append(f"{path}.{key}")
            found.extend(_walk_keys(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_keys(child, f"{path}[{index}]"))
    return found


def validate() -> dict[str, Any]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    benchmark_bytes = BENCHMARK.read_bytes()
    benchmark_hash = hashlib.sha256(benchmark_bytes).hexdigest()
    if benchmark_hash != lock.get("sha256"):
        raise AssertionError("frozen benchmark digest does not match its lock")
    data = json.loads(benchmark_bytes.decode("utf-8"))
    if data.get("schema_version") != "m30-semantic-context-sequence-benchmark-v2":
        raise AssertionError("unexpected M30 benchmark schema")
    if data.get("episode_count") != 33 or lock.get("episode_count") != 33:
        raise AssertionError("M30 benchmark must have 33 episodes")
    if data.get("decision_point_count") != 126 or lock.get("decision_point_count") != 126:
        raise AssertionError("M30 benchmark must have 126 decision points")

    family_counts: dict[str, int] = {}
    split_counts: dict[str, int] = {}
    split_decision_counts: dict[str, int] = {}
    family_split: dict[str, str] = {}
    episode_ids: set[str] = set()
    decision_count = 0
    scene_core_checks = 0
    state_rows: list[dict[str, Any]] = []
    saw_completed = False
    saw_tool_absent = False
    for episode in data.get("episodes", []):
        episode_id = episode["episode_id"]
        family = episode["scene_family"]
        split = episode["split"]
        if episode_id in episode_ids:
            raise AssertionError(f"duplicate episode id: {episode_id}")
        episode_ids.add(episode_id)
        if family in family_split and family_split[family] != split:
            raise AssertionError(f"scene family crosses splits: {family}")
        family_split[family] = split
        family_counts[family] = family_counts.get(family, 0) + 1
        split_counts[split] = split_counts.get(split, 0) + 1
        points = episode.get("decision_points", [])
        split_decision_counts[split] = split_decision_counts.get(split, 0) + len(points)
        if not 1 <= len(points) <= 4:
            raise AssertionError(f"unexpected round count in {episode_id}")
        path = episode.get("trajectory_object_ids_evaluation_only", [])
        if len(path) != len(points) + 1:
            raise AssertionError(f"evaluation trajectory length mismatch in {episode_id}")
        for index, point in enumerate(points):
            decision_count += 1
            model_input = point.get("model_input")
            labels = point.get("evaluation_only")
            if not isinstance(model_input, dict) or not isinstance(labels, dict):
                raise AssertionError("model_input and evaluation_only must be separate objects")
            forbidden = _walk_keys(model_input)
            if forbidden:
                raise AssertionError("label leakage keys: " + ", ".join(forbidden))
            if set(model_input) != {"scene", "selection_history", "current_task_state", "candidate_next_object_ids"}:
                raise AssertionError(f"unexpected model input fields in {episode_id}")
            scene = model_input["scene"]
            objects = scene.get("objects", [])
            if len(objects) != 8:
                raise AssertionError(f"scene must have eight objects: {episode_id}")
            if any(not isinstance(obj.get("object_type"), str) or not obj["object_type"].strip()
                   or not isinstance(obj.get("affordance_tags"), list)
                   or not all(isinstance(tag, str) and tag.strip() for tag in obj["affordance_tags"])
                   for obj in objects):
                raise AssertionError(f"object_type or affordance_tags schema mismatch in {episode_id}")
            selectable = [obj["id"] for obj in objects if obj.get("selectable") is True]
            history = model_input["selection_history"]
            candidates = model_input["candidate_next_object_ids"]
            try:
                SemanticSceneCore(scene, selection_history=history,
                                  current_task_state=model_input["current_task_state"])
            except Exception as error:
                raise AssertionError(f"production scene-core rejected model input in {episode_id}: {type(error).__name__}") from None
            scene_core_checks += 1
            if history != path[:index + 1] or len(history) != len(set(history)):
                raise AssertionError(f"history is not a unique observed prefix in {episode_id}")
            expected_remaining = [item for item in selectable if item not in set(history)]
            if candidates != expected_remaining:
                raise AssertionError(f"remaining candidate list mismatch in {episode_id}")
            accepted = labels.get("acceptable_next_targets", [])
            if not set(accepted).issubset(candidates):
                raise AssertionError(f"evaluation target is not a candidate in {episode_id}")
            expected_status = labels.get("expected_status")
            if expected_status == "ambiguous" and len(set(accepted)) < 2:
                raise AssertionError(f"ambiguous label needs at least two distinct targets in {episode_id}")
            if expected_status == "informative" and len(set(accepted)) != 1:
                raise AssertionError(f"informative label needs exactly one target in {episode_id}")
            if set(labels.get("acceptable_relations_by_target", {})) != set(accepted):
                raise AssertionError(f"relation labels do not align with targets in {episode_id}")
            by_id = {obj["id"]: obj for obj in objects}
            history_sources = [by_id[item_id] for item_id in history]
            for target_id, relation_types in labels["acceptable_relations_by_target"].items():
                for relation_type in relation_types:
                    if not any(relation_compatibility(relation_type, source, by_id[target_id])[0]
                               for source in history_sources):
                        raise AssertionError(
                            f"incompatible gold relation {relation_type} in {episode_id} "
                            f"round {point['round_index']} target {target_id}"
                        )
            if labels.get("expected_status") not in {"informative", "ambiguous", "invalid", "context_off"}:
                raise AssertionError(f"unexpected expected status in {episode_id}")
            state_rows.extend(obj.get("current_state", {}) for obj in objects)
            if model_input["current_task_state"].get("phase") == "complete":
                saw_completed = True
            if model_input["current_task_state"].get("required_tool_available") is False:
                saw_tool_absent = True

    if family_counts != EXPECTED_FAMILIES or split_counts != EXPECTED_SPLITS:
        raise AssertionError("family or split counts differ from the frozen design")
    if decision_count != 126 or not saw_completed or not saw_tool_absent:
        raise AssertionError("decision count or required task-state cases are missing")
    if split_decision_counts != {"train": 56, "dev": 24, "held_out": 46}:
        raise AssertionError(f"unexpected split decision counts: {split_decision_counts}")
    serialized_states = json.dumps(state_rows, ensure_ascii=False).casefold()
    for required in ('"lid": "open"', '"lid": "closed"', '"availability": "available"',
                     '"availability": "unavailable"', '"status": "blocked"',
                     '"occupancy": "free"', '"occupancy": "occupied"', '"full": true'):
        if required not in serialized_states:
            raise AssertionError("missing required explicit state: " + required)

    if lock.get("expected_answers_generated_independently") is not True:
        raise AssertionError("benchmark lock does not assert independent labels")
    if lock.get("evaluation_started_before_freeze") is not False:
        raise AssertionError("evaluation must begin after the frozen lock")
    return {
        "status": "PASS",
        "benchmarkId": data["benchmark_id"],
        "sha256": benchmark_hash,
        "episodeCount": len(episode_ids),
        "decisionPointCount": decision_count,
        "productionSceneCoreValidationCount": scene_core_checks,
        "familyEpisodeCounts": family_counts,
        "splitEpisodeCounts": split_counts,
        "splitDecisionPointCounts": split_decision_counts,
        "splitByFamily": family_split,
        "modelInputLeakageKeyCount": 0,
        "evaluationStartedBeforeFreeze": False,
        "completedStatePresent": saw_completed,
        "toolAbsentStatePresent": saw_tool_absent,
    }


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False, indent=2))
