import unittest
import inspect
from unittest.mock import patch

from integration.m13_6_usb_visual_acceptance import (
    QUEST_VISUAL_PORT,
    _run_mujoco,
    _parse_quest_log_lines,
    _synthetic_frame,
)
from integration.m13_6_visual_sync import (
    M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST,
    MujocoToQuestTransform,
)


class M136UsbVisualAcceptanceTests(unittest.TestCase):
    def test_run_mujoco_routes_phase_initial_positions_only_to_sequential_api(self):
        class FakeSender:
            sent_count = 0
            last_error = None

            def close(self):
                return None

        fake_initial = {"block_sim_01": [0.1, 0.2, 0.8]}
        with patch("integration.m13_6_usb_visual_acceptance.TcpLatestStateSender", return_value=FakeSender()), \
                patch("integration.m13_6_usb_visual_acceptance.run_mujoco_pick_place_stream", return_value={"nextSequence": 2}) as pick, \
                patch("integration.m13_6_usb_visual_acceptance.run_mujoco_sequential_stream", return_value={"nextSequence": 3}) as sequential:
            _run_mujoco("single", 21002, "block_sim_01", 30.0, 10, 1, initial_block_positions_mujoco=fake_initial)
            pick.assert_called_once()
            self.assertNotIn("initial_block_positions_mujoco", pick.call_args.kwargs)

            _run_mujoco("sequential", 21002, "block_sim_01", 30.0, 10, 1, initial_block_positions_mujoco=fake_initial)
            sequential.assert_called_once()
            self.assertEqual(sequential.call_args.kwargs["initial_block_positions_mujoco"], fake_initial)

    def test_run_mujoco_signature_exposes_visual_mode_and_forwards_it(self):
        self.assertIn("m13_6_visual_mode", inspect.signature(_run_mujoco).parameters)
        class FakeSender:
            sent_count = 0
            last_error = None

            def close(self):
                return None

        with patch("integration.m13_6_usb_visual_acceptance.TcpLatestStateSender", return_value=FakeSender()), \
                patch("integration.m13_6_usb_visual_acceptance.run_mujoco_pick_place_stream", return_value={"nextSequence": 2}) as pick, \
                patch("integration.m13_6_usb_visual_acceptance.run_mujoco_sequential_stream", return_value={"nextSequence": 3}) as sequential:
            _run_mujoco("single", 21002, "block_sim_01", 30.0, 10, 1, m13_6_visual_mode=False)
            _run_mujoco("sequential", 21002, "block_sim_01", 30.0, 10, 1, m13_6_visual_mode=False)
        self.assertFalse(pick.call_args.kwargs["m13_6_visual_mode"])
        self.assertFalse(sequential.call_args.kwargs["m13_6_visual_mode"])

    def test_synthetic_fixture_uses_frozen_public_identity_contract(self):
        frame = _synthetic_frame(7, 1.25)
        payload = frame.to_dict()
        self.assertEqual(payload["messageType"], "m13_6_robot_world_state")
        self.assertEqual(len(payload["jointPositionsRadians"]), 7)
        self.assertEqual(
            [block["logicalBlockId"] for block in payload["blocks"]],
            ["block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"],
        )
        self.assertNotIn("obj_", str(payload))

    def test_usb_visual_port_is_separate_from_m8_control(self):
        self.assertEqual(QUEST_VISUAL_PORT, 11002)
        with open("integration/m13_6_usb_visual_acceptance.py", encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('\"m8ControlPortUntouched\": 11001', source)
        self.assertIn('\"kind\": \"adb_tcp_forward\"', source)

    def test_synthetic_blocks_reuse_scene_table_layout(self):
        transform = MujocoToQuestTransform()
        positions = [
            transform.position(block.position_mujoco)
            for block in _synthetic_frame(7, 1.25).blocks
        ]
        self.assertEqual(
            [round(position[0], 3) for position in positions],
            [round(position[0], 3) for position in M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST],
        )
        self.assertEqual(
            [round(position[1], 3) for position in positions],
            [round(position[1], 3) for position in M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST],
        )
        self.assertEqual(
            [round(position[2], 3) for position in positions],
            [round(position[2], 3) for position in M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST],
        )


    def test_diagnostics_binding_state_is_runtime_binding_evidence(self):
        logs = _parse_quest_log_lines(
            [
                "I/Unity (1): M13_6_VISUAL diagnostics build=test bindings=True",
            ]
        )
        self.assertEqual(logs["sceneBindings"], [])
        self.assertEqual(logs["bindingEvidenceSource"], "diagnostics_bindings_true")
        self.assertTrue(logs["bindings"])

    def test_explicit_scene_binding_evidence_remains_supported(self):
        logs = _parse_quest_log_lines(
            [
                "I/Unity (1): M13_6_VISUAL scene_bindings world_root=World robot_root=Robot joints=9 gripper_left=True gripper_right=True blocks=4",
            ]
        )
        self.assertEqual(logs["bindingEvidenceSource"], "scene_bindings")
        self.assertTrue(logs["bindings"])

    def test_false_diagnostics_binding_state_does_not_pass(self):
        logs = _parse_quest_log_lines(
            [
                "I/Unity (1): M13_6_VISUAL diagnostics build=test bindings=False",
            ]
        )
        self.assertFalse(logs["bindings"])


if __name__ == "__main__":
    unittest.main()
