from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = (REPO_ROOT / "research_analysis" / "m32_interactive_text_context_demo_20261003"
               / "attempt-01" / "run_m32_benchmark.py")
SPEC = importlib.util.spec_from_file_location("m32_run_m32_benchmark", RUNNER_PATH)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RecordingEngine:
    def __init__(self):
        self.calls = []

    def predict_next_target(self, *, scene, selection_history, remaining_candidates, current_task_state):
        self.calls.append({
            "scene": scene,
            "history": list(selection_history),
            "candidates": list(remaining_candidates),
            "task": dict(current_task_state),
        })
        return {"status": "context_off", "candidate_ids": list(remaining_candidates)}


class M32BenchmarkRunnerCandidateTests(unittest.TestCase):
    def test_context_runner_passes_all_parsed_candidates_not_only_scorer_labels(self):
        scene = {
            "objects": [
                {"id": "parsed_a", "source_mention": "苹果", "selectable": True},
                {"id": "parsed_b", "source_mention": "水果刀", "selectable": True},
                {"id": "parsed_extra", "source_mention": "盘子", "selectable": True},
                {"id": "room", "source_mention": "厨房", "selectable": False},
            ],
            "candidate_order": ["parsed_a", "parsed_b", "parsed_extra", "room"],
        }
        point = {
            "point_id": "case-r02", "selection_round": 2,
            "history_object_ids": ["apple"], "candidate_object_ids": ["knife"],
        }
        engine = RecordingEngine()

        record = RUNNER._record_context(
            client=None,
            engine=engine,
            parsed_scene=scene,
            benchmark_scene={"scene_id": "test-scene", "family": "test"},
            trajectory={"trajectory_id": "test-trajectory"},
            point=point,
            mapping={"apple": "parsed_a", "knife": "parsed_b"},
        )

        self.assertEqual(record["run_status"], "completed")
        self.assertEqual(record["scorer_expected_candidate_ids"], ["parsed_b"])
        self.assertEqual(record["actual_candidate_ids"], ["parsed_b", "parsed_extra"])
        self.assertEqual(engine.calls[0]["candidates"], ["parsed_b", "parsed_extra"])
        self.assertNotIn("room", engine.calls[0]["candidates"])


if __name__ == "__main__":
    unittest.main()
