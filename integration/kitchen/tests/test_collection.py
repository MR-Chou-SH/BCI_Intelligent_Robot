import json
from pathlib import Path
import tempfile
import unittest
import uuid

from integration.kitchen.collection import CASES, TaskCollection, build_plan, audit_record
from integration.kitchen.task_catalog import load_catalog
from robot_arm.kitchen.platform import KitchenPlatform


class LocalClient:
    """Same request dispatcher and evidence writer as TCP, no sockets."""
    def __init__(self, platform):
        self.platform = platform
        self.token = platform.token()

    def call(self, method, args=None, *, mutating=False):
        reply = self.platform.request(method, args, token=self.token if mutating else None,
            request_id=uuid.uuid4().hex, writer_id="collection-test-writer", connection_id="collection-test")
        self.token = reply["token"]
        return reply


class CollectionTests(unittest.TestCase):
    def test_exception_retains_incomplete_annotations_and_stops_controller(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0,
                                       initialization_profile="research", data_root=root / "physics", record_physics=True)
            try:
                with self.assertRaisesRegex(RuntimeError, "planner failed"):
                    with TaskCollection(LocalClient(platform), root / "collection", "L01") as collection:
                        collection.start_segment("L01:1")
                        raise RuntimeError("planner failed")
                result = json.loads((root / "collection/collection.json").read_text())
                self.assertEqual(result["record_integrity"], "INCOMPLETE")
                self.assertEqual(result["annotation"]["status"], "INCOMPLETE_ANNOTATION")
                self.assertFalse(result["training_export_approved"])
                self.assertTrue(platform.controller.stopped)
            finally:
                platform.close()

    def test_diagram_cases_and_frozen_tasks_have_valid_dependencies(self):
        catalogue = load_catalog()
        for name in list(CASES) + [x["id"] for x in catalogue["atomic"] + catalogue["composite"]]:
            plan = build_plan(name)
            seen = set()
            for node in plan["nodes"]:
                self.assertTrue(set(node["requires"]).issubset(seen))
                self.assertNotIn(node["id"], seen)
                seen.add(node["id"])
            self.assertFalse(plan["initial_state_verified"])
        heat = build_plan("heat_then_open")
        self.assertEqual([x["kind"] for x in heat["nodes"]], ["atomic", "event", "atomic"])
        both = build_plan("C10")["nodes"]
        self.assertEqual(set(both[-1]["requires"]), {"L06:1", "R04:2"})
        self.assertEqual([x["object_role"] for x in build_plan("C12")["nodes"] if x["task"] == "R06"],
                         ["old_meal", "new_meal"])

    def test_real_dynamics_multifile_sidecar_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0,
                                       initialization_profile="research", data_root=root / "physics", record_physics=True)
            try:
                collection = TaskCollection(LocalClient(platform), root / "collection", "L01")
                collection.start_segment("L01:1")
                for _ in range(2):
                    q = list(collection.call("observe")["accepted_target16"])
                    q[0] += .002
                    self.assertTrue(collection.call("check_motion", {"end_joint_targets": q})["pass"])
                    collection.call("submit_joint_targets", {"joint_targets": q}, mutating=True)
                    state = collection.call("step", {"n_substeps": 20}, mutating=True)
                    self.assertFalse(state["stopped"])
                collection.end_segment("L01:1")
                with self.assertRaises(ValueError):
                    collection.call("reset", {"seed": 1}, mutating=True)
                result = collection.finish()
                self.assertEqual(result["record_integrity"], "PASS")
                self.assertEqual(len(result["records"]), 2)
                self.assertEqual(sum(r["samples"] for r in result["records"]), 40)
                self.assertEqual(result["annotation"]["status"], "COMPLETE_ANNOTATION")
                # A tiny movement is NOT a successful fruit grasp.
                self.assertEqual(result["task_success"], "NOT_VERIFIED")
                self.assertFalse(result["training_export_approved"])
                record = Path(result["records"][0]["path"])
                with self.assertRaises(ValueError):
                    audit_record(record, "wrong-episode")
                damaged = root / "tampered.jsonl"
                rows = record.read_bytes().splitlines(keepends=True)
                row = json.loads(rows[1]); row["record"]["sim_tick"] += 1
                rows[1] = json.dumps(row).encode() + b"\n"
                damaged.write_bytes(b"".join(rows))
                with self.assertRaises(ValueError):
                    audit_record(damaged, collection.episode_id)
            finally:
                platform.close()


if __name__ == "__main__":
    unittest.main()
