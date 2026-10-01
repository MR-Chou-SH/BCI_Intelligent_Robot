"""Regression coverage for one frozen randomized M20 Quest/MuJoCo layout."""

from __future__ import annotations

import copy
import unittest

from integration.m16_paged_queue import Candidate, PagedSelectionQueue
from integration.m20_assistive_scene_contract import (
    DEFAULT_SPEC_PATH,
    EXPECTED_CANDIDATE_ORDER,
    M9_GRASP_REACHABLE_X_BOUNDS_METERS,
    M9_GRASP_REACHABLE_Y_BOUNDS_METERS,
    load_spec,
    spatial_candidate_order,
    validate_spec,
)
from integration.m20_scene_layout_snapshot import (
    M20SceneSnapshotRegistry,
    RANDOMIZED_OBJECT_IDS,
    _apply_positions_to_spec,
    apply_scene_layout_snapshot,
    create_scene_layout_snapshot,
    parse_scene_layout_snapshot,
    serialize_scene_layout_snapshot,
)


class M20SceneLayoutSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.canonical, _ = load_spec(DEFAULT_SPEC_PATH)

    @staticmethod
    def _entity_map(spec):
        return {item["semanticId"]: item for item in spec["entities"]}

    def test_twenty_seed_layouts_are_valid_replayable_and_vary(self):
        layouts = []
        seeds = list(range(20261002, 20261022))
        for seed in seeds:
            scene_id = "m20-seed-{}".format(seed)
            snapshot, runtime_spec = create_scene_layout_snapshot(
                self.canonical,
                seed,
                scene_id=scene_id,
                created_utc="2026-10-02T00:00:00Z",
            )
            replay, replay_spec = create_scene_layout_snapshot(
                self.canonical,
                seed,
                scene_id=scene_id,
                created_utc="2026-10-02T00:00:00Z",
            )
            self.assertFalse(snapshot["fallbackUsed"], "seed {} unexpectedly used fallback".format(seed))
            self.assertEqual(serialize_scene_layout_snapshot(snapshot), serialize_scene_layout_snapshot(replay))
            self.assertEqual(tuple(runtime_spec["candidateOrderFarToNearLeftToRight"]), spatial_candidate_order(runtime_spec))
            self.assertEqual(runtime_spec["candidateOrderFarToNearLeftToRight"], replay_spec["candidateOrderFarToNearLeftToRight"])
            report = validate_spec(runtime_spec)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(runtime_spec["candidateOrderFarToNearLeftToRight"][-1], "assist_user_zone")
            self.assertEqual(report["slotMapping"], [
                {"slotIndex": 0, "frequencyHz": 7.2, "framesPerHalfCycle": 5},
                {"slotIndex": 1, "frequencyHz": 9.0, "framesPerHalfCycle": 4},
                {"slotIndex": 2, "frequencyHz": 12.0, "framesPerHalfCycle": 3},
            ])

            canonical = self._entity_map(self.canonical)
            randomized = self._entity_map(runtime_spec)
            self.assertEqual(randomized["assist_user_zone"]["positionMeters"], canonical["assist_user_zone"]["positionMeters"])
            for source_id in ("assist_medicine_box", "assist_phone"):
                position = randomized[source_id]["positionMeters"]
                self.assertGreaterEqual(position["x"], M9_GRASP_REACHABLE_X_BOUNDS_METERS[0])
                self.assertLessEqual(position["x"], M9_GRASP_REACHABLE_X_BOUNDS_METERS[1])
                self.assertGreaterEqual(position["y"], M9_GRASP_REACHABLE_Y_BOUNDS_METERS[0])
                self.assertLessEqual(position["y"], M9_GRASP_REACHABLE_Y_BOUNDS_METERS[1])
            for semantic_id in (
                "assist_medicine_box", "assist_storage_box", "assist_phone",
                "assist_button_switch", "assist_wireless_charger",
            ):
                self.assertNotEqual(randomized[semantic_id]["positionMeters"], canonical[semantic_id]["positionMeters"])
            layouts.append({key: tuple(randomized[key]["positionMeters"][axis] for axis in ("x", "y", "z"))
                for key in randomized if key in {
                    "assist_medicine_box", "assist_storage_box", "assist_phone",
                    "assist_button_switch", "assist_wireless_charger",
                }})

            roundtrip = apply_scene_layout_snapshot(self.canonical, parse_scene_layout_snapshot(
                serialize_scene_layout_snapshot(snapshot)
            ))
            self.assertEqual(
                {item["semanticId"]: item["positionMeters"] for item in runtime_spec["entities"]},
                {item["semanticId"]: item["positionMeters"] for item in roundtrip["entities"]},
            )
        self.assertEqual(len({serialize_scene_layout_snapshot(create_scene_layout_snapshot(
            self.canonical, seed, scene_id="m20-variation-{}".format(seed), created_utc="fixed"
        )[0]) for seed in seeds}), 20)
        for semantic_id in layouts[0]:
            self.assertGreater(len({layout[semantic_id] for layout in layouts}), 18)

    def test_randomized_order_pages_and_submit_never_rebuild_the_snapshot(self):
        snapshot, runtime_spec = create_scene_layout_snapshot(
            self.canonical, 8675309, scene_id="m20-frozen-session", created_utc="2026-10-02T00:00:00Z"
        )
        candidates = tuple(
            Candidate(item["logicalBlockId"], item["targetId"], item["displayLabel"])
            for item in runtime_spec["entities"]
            if item.get("selectable")
        )
        ordered_by_id = {item.target_id: item for item in candidates}
        queue_candidates = tuple(ordered_by_id[item] for item in runtime_spec["candidateOrderFarToNearLeftToRight"])
        queue = PagedSelectionQueue(queue_candidates)
        snapshot_before = serialize_scene_layout_snapshot(snapshot)
        self.assertEqual(2, queue.page_count)
        self.assertEqual((7.2, 9.0, 12.0), tuple(item.nominal_frequency_hz for item in queue.current_page.candidates))
        queue.select_slot(0)
        self.assertTrue(queue.navigate_next())
        queue.select_slot(0)
        self.assertTrue(queue.navigate_previous())
        submitted = queue.submit()
        self.assertTrue(submitted.accepted)
        self.assertEqual(2, len(submitted.plan.ordered_selections))
        self.assertEqual(snapshot_before, serialize_scene_layout_snapshot(snapshot))
        self.assertEqual(2, queue.page_count)

    def test_snapshot_rejects_identity_geometry_fixed_zone_and_stale_scene_mismatches(self):
        snapshot, _ = create_scene_layout_snapshot(
            self.canonical, 5102, scene_id="m20-active-session", created_utc="2026-10-02T00:00:00Z"
        )
        runtime = apply_scene_layout_snapshot(self.canonical, snapshot)
        registry = M20SceneSnapshotRegistry()
        batch = {
            "sceneId": snapshot["sceneId"],
            "sceneLayoutSnapshotJson": serialize_scene_layout_snapshot(snapshot),
            "selections": [{"targetId": "assist_phone", "predictedClassIndex": 0, "slotIndex": 0}],
        }
        accepted, accepted_spec = registry.accept_confirmed_batch(batch, self.canonical)
        self.assertEqual(accepted["sceneId"], registry.scene_id)
        self.assertEqual(runtime["candidateOrderFarToNearLeftToRight"], accepted_spec["candidateOrderFarToNearLeftToRight"])

        moved_snapshot, _ = create_scene_layout_snapshot(
            self.canonical, 5104, scene_id="m20-active-session", created_utc="2026-10-02T00:00:00Z"
        )
        with self.assertRaisesRegex(ValueError, "snapshot changed during a frozen"):
            registry.accept_confirmed_batch({**batch,
                "sceneLayoutSnapshotJson": serialize_scene_layout_snapshot(moved_snapshot),
            }, self.canonical)

        stale, _ = create_scene_layout_snapshot(
            self.canonical, 5103, scene_id="m20-stale-session", created_utc="2026-10-02T00:00:00Z"
        )
        with self.assertRaisesRegex(ValueError, "stale scene command"):
            registry.accept_confirmed_batch({**batch,
                "sceneId": stale["sceneId"],
                "sceneLayoutSnapshotJson": serialize_scene_layout_snapshot(stale),
            }, self.canonical)

        fixed_zone = copy.deepcopy(snapshot)
        zone = next(item for item in fixed_zone["objects"] if item["semanticId"] == "assist_user_zone")
        zone["positionMeters"]["x"] += 0.01
        with self.assertRaisesRegex(ValueError, "fixed USER ZONE"):
            apply_scene_layout_snapshot(self.canonical, fixed_zone)

        unreachable_source = copy.deepcopy(snapshot)
        medicine = next(item for item in unreachable_source["objects"] if item["semanticId"] == "assist_medicine_box")
        medicine["positionMeters"]["y"] = M9_GRASP_REACHABLE_Y_BOUNDS_METERS[1] + 0.01
        positions = {
            item["semanticId"]: tuple(item["positionMeters"][axis] for axis in ("x", "y", "z"))
            for item in unreachable_source["objects"]
            if item["semanticId"] in RANDOMIZED_OBJECT_IDS
        }
        unreachable_runtime = _apply_positions_to_spec(self.canonical, positions)
        unreachable_source["candidateOrderFarToNearLeftToRight"] = list(
            spatial_candidate_order(unreachable_runtime)
        )
        with self.assertRaisesRegex(ValueError, "M9 grasp-reachable layout envelope"):
            apply_scene_layout_snapshot(self.canonical, unreachable_source)

        duplicate = copy.deepcopy(snapshot)
        duplicate["objects"].append(copy.deepcopy(duplicate["objects"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate semanticId"):
            apply_scene_layout_snapshot(self.canonical, duplicate)

        missing = copy.deepcopy(snapshot)
        missing["objects"].pop()
        with self.assertRaisesRegex(ValueError, "missing semantic object poses"):
            apply_scene_layout_snapshot(self.canonical, missing)

        unknown = copy.deepcopy(snapshot)
        unknown["objects"][0]["semanticId"] = "old_obj_0"
        with self.assertRaisesRegex(ValueError, "unknown semanticId"):
            apply_scene_layout_snapshot(self.canonical, unknown)

        wrong_target = {**batch, "selections": [{"targetId": "old_target", "slotIndex": 0}]}
        with self.assertRaisesRegex(ValueError, "unknown TargetId"):
            M20SceneSnapshotRegistry().accept_confirmed_batch(wrong_target, self.canonical)

    def test_mujoco_replay_uses_snapshot_table_local_poses(self):
        from integration.m20_assistive_scene_contract import build_mujoco_scene, check_mujoco_scene
        import mujoco

        for seed in (190926, 190927, 190928):
            snapshot, runtime_spec = create_scene_layout_snapshot(
                self.canonical, seed, scene_id="m20-mujoco-{}".format(seed), created_utc="fixed"
            )
            model, data = build_mujoco_scene(apply_scene_layout_snapshot(self.canonical, snapshot))
            report = check_mujoco_scene(runtime_spec, model, data)
            self.assertEqual("PASS", report["status"])
            entities = self._entity_map(runtime_spec)
            for semantic_id in EXPECTED_CANDIDATE_ORDER:
                body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, semantic_id)
                self.assertGreaterEqual(body_id, 0)
                expected = entities[semantic_id]["positionMeters"]
                self.assertAlmostEqual(float(data.xpos[body_id][0]), expected["x"], places=6)
                self.assertAlmostEqual(float(data.xpos[body_id][1]), expected["y"], places=6)
                self.assertAlmostEqual(
                    float(data.xpos[body_id][2]),
                    runtime_spec["table"]["mujocoTopSurfaceWorldZMeters"] + expected["z"],
                    places=6,
                )


if __name__ == "__main__":
    unittest.main()
