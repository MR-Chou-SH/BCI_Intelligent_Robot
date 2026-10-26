import json
import hashlib
import math
from pathlib import Path
import tempfile
import shutil
import unittest
import h5py

import mujoco
import numpy as np

from integration.kitchen.tests.test_collection import LocalClient
from integration.kitchen.replay import inspect_trajectory, verify, read_compact
from robot_arm.kitchen.platform import KitchenPlatform
from robot_arm.kitchen.research_initialization import audit_object_separation, _free_address


class StateReplayTests(unittest.TestCase):
    def test_default_recording_is_small_and_has_no_diagnostic_copies(self):
        with tempfile.TemporaryDirectory() as temp:
            platform=KitchenPlatform(layout="kitchen_v3_norice_candidate_013",seed=0,data_root=Path(temp))
            try:
                client=LocalClient(platform)
                info=client.call("start_state_recording",mutating=True)["result"]
                client.call("step",{"n_substeps":1000},mutating=True)
                client.call("stop_state_recording",mutating=True)
                self.assertLess(Path(info["path"]).stat().st_size,256*1024)
                self.assertFalse(list(Path(temp).rglob("*.jsonl")))
                self.assertFalse(list(Path(temp).rglob("initializations")))
                self.assertEqual(verify(info["path"])["status"],"CONTROL_RESIMULATION_PASS")
                data=read_compact(info["path"])
                objects=client.call("get_object_states",{"detail":"key"})["result"]["objects"]
                for index,obj in enumerate(objects):
                    np.testing.assert_allclose(data["states/object_pose"][-1,index],
                        [*obj["position_world_m"],*obj["quaternion_world_wxyz"]],atol=1e-12)
            finally:platform.close()

    def test_portable_support_types_keep_bounded_nonrobot_gate(self):
        platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0)
        try:
            contract = {"arm": "right", "target": "burger_container", "phase": "acquire",
                        "payload_ids": ["burger_container", "burger"],
                        "supports": {"burger_container": ["v3_counter_right", "microwave_bottom"],
                                     "burger": ["burger_plate"]}}
            self.assertEqual(platform.contact.validate_contract(contract)["target"], "burger_container")
            contract["supports"]["burger_container"] = ["floor"]
            with self.assertRaisesRegex(ValueError, "INVALID_BOUNDED_NONROBOT_SUPPORT"):
                platform.contact.validate_contract(contract)
            contract["supports"]["burger_container"] = ["burger_plate"]
            with self.assertRaisesRegex(ValueError, "INVALID_BOUNDED_NONROBOT_SUPPORT"):
                platform.contact.validate_contract(contract)
        finally:
            platform.close()

    def test_state_query_is_read_only_finite_same_tick_and_filters(self):
        platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0,
                                   initialization_profile="research")
        try:
            client = LocalClient(platform)
            spec = mujoco.mjtState.mjSTATE_INTEGRATION
            before = np.empty(mujoco.mj_stateSize(platform.scene.model, spec))
            mujoco.mj_getState(platform.scene.model, platform.scene.data, before, spec)
            result = client.call("get_object_states", {"ids": ["apple", "microwave_door", "burger"]})["result"]
            after = np.empty_like(before)
            mujoco.mj_getState(platform.scene.model, platform.scene.data, after, spec)
            np.testing.assert_array_equal(before, after)
            self.assertEqual(result["sim_tick"], 0)
            items = {item["id"]: item for item in result["objects"]}
            self.assertEqual(set(items), {"apple", "microwave_door", "burger"})
            self.assertEqual(items["microwave_door"]["joint_position_unit"], "rad")
            self.assertEqual(items["microwave_door"]["door_state"], "open")
            self.assertAlmostEqual(items["microwave_door"]["joint_position"], math.radians(100))
            self.assertEqual(items["burger"]["carrier_id"], "burger_container")
            self.assertFalse(items["burger"]["meal_group_in_microwave"])
            self.assertFalse(items["burger"]["meal_group_heated"])
            self.assertEqual(items["apple"]["linear_velocity_world_m_s"], [0., 0., 0.])
            self.assertFalse(result["images_included"])
            self.assertTrue(json.dumps(result, allow_nan=False))
            self.assertFalse(client.call("get_object_states", {"ids": ["rice"]})["ok"])
            self.assertFalse(client.call("get_object_states", {"ids": ["missing"]})["ok"])
        finally:
            platform.close()

    def test_wait_actions_nonzero_initial_tick_final_partial_frame_and_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=1,
                                       initialization_profile="research", data_root=Path(temp))
            try:
                client = LocalClient(platform)
                client.call("step", {"n_substeps": 20}, mutating=True)
                info = client.call("start_state_recording", mutating=True)["result"]
                # Recording must retain the actual t=20 settled state, not the reset state.
                initial = client.call("get_replay_snapshot")["result"]
                self.assertEqual(initial["snapshot"]["sim_tick"], 20)
                self.assertFalse(client.call("start_state_recording", mutating=True)["ok"])
                self.assertFalse(client.call("reset", {"seed": 0}, mutating=True)["ok"])
                client.call("step", {"n_substeps": 40}, mutating=True)
                q = client.call("observe")["result"]["accepted_target16"]
                q[0] += .002
                self.assertTrue(client.call("submit_joint_targets", {"joint_targets": q}, mutating=True)["ok"])
                client.call("step", {"n_substeps": 20}, mutating=True)
                client.call("step", {"n_substeps": 7}, mutating=True)
                client.call("hold", mutating=True)
                stopped = client.call("stop_state_recording", mutating=True)["result"]
                self.assertFalse(stopped["active"])
                self.assertTrue(stopped["sealed"])
                report = inspect_trajectory(info["path"])
                self.assertEqual(report["steps"], 67)
                self.assertEqual(report["start_tick"], 20)
                self.assertEqual(report["end_tick"], 87)
                self.assertEqual(verify(info["path"])["status"], "CONTROL_RESIMULATION_PASS")
                data=read_compact(info["path"])
                np.testing.assert_array_equal(data["initial/integration_state"], initial["snapshot"]["integration_state"])
                self.assertEqual(data["metadata"]["control_identity"]["control_hash"],data["metadata"]["identity"]["control_hash"])
                self.assertEqual(data["actions/ctrl"].shape,(67,16))
                self.assertEqual(data["states/tick"].tolist(),[20,40,60,80,87])
                self.assertFalse(list(Path(temp).rglob("physics.*")))
                self.assertFalse(list(Path(temp).rglob("sources")))
                truncated = Path(temp) / "truncated.h5"
                truncated.write_bytes(Path(info["path"]).read_bytes()[:200])
                with self.assertRaises(ValueError):
                    inspect_trajectory(truncated)
                altered = Path(temp) / "changed_controls.h5"
                shutil.copyfile(info["path"],altered)
                with h5py.File(altered,"r+") as stream:
                    stream["actions/ctrl"][0,0]+=.1
                self.assertEqual(inspect_trajectory(altered)["status"], "SEALED_STATE_TRAJECTORY_PASS")
                self.assertEqual(verify(altered)["status"], "CONTROL_RESIMULATION_DIVERGED")
                with h5py.File(altered,"r+") as stream:
                    stream.attrs["sealed"]=False
                with self.assertRaisesRegex(ValueError,"Unsealed"):
                    inspect_trajectory(altered)
            finally:
                platform.close()

    def test_object_pair_audit_detects_forced_overlap_without_touching_live_data(self):
        platform = KitchenPlatform(layout="kitchen_v3_norice_candidate_013", seed=0,
                                   initialization_profile="research")
        try:
            scene = platform.scene
            self.assertTrue(audit_object_separation(scene)["pass"])
            data = mujoco.MjData(scene.model)
            mujoco.mj_copyData(data, scene.model, scene.data)
            apple, _ = _free_address(scene, "apple")
            tomato, _ = _free_address(scene, "tomato")
            data.qpos[tomato:tomato + 3] = data.qpos[apple:apple + 3]
            mujoco.mj_forward(scene.model, data)
            result = audit_object_separation(scene, data=data)
            self.assertFalse(result["pass"])
            self.assertTrue(any(set(row["objects"]) == {"apple", "tomato"} for row in result["errors"]))
            self.assertTrue(audit_object_separation(scene)["pass"])
        finally:
            platform.close()


if __name__ == "__main__":
    unittest.main()
