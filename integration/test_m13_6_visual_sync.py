import json
import importlib.util
import inspect
import math
import socket
import threading
import unittest
from pathlib import Path

from integration.m13_6_visual_sync import (
    BlockWorldState,
    LatestStateBuffer,
    MujocoToQuestTransform,
    M13_6_MAPPING_VERSION,
    M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST,
    M13_6_VISUAL_BLOCK_EDGE_MUJOCO,
    M13_6_VISUAL_BLOCK_EDGE_QUEST,
    M13_6_VISUAL_SEQUENTIAL_PLACE_TARGETS,
    m13_6_quest_catalog_anchor_positions_mujoco,
    m13_6_robot_base_orientation_fixture,
    MujocoStateSampler,
    RecordingStateSink,
    RobotWorldStateFrame,
    run_mujoco_sequential_stream,
    run_mujoco_pick_place_stream,
    TcpLatestStateSender,
    m13_6_gripper_block_relative_pose,
)


def frame(sequence):
    return RobotWorldStateFrame(
        sequence=sequence,
        simulation_time_seconds=sequence * 0.002,
        joint_positions_radians=(0.0, 0.1, 0.2, -1.0, 0.4, 1.0, -0.5),
        left_finger_position_meters=0.01,
        right_finger_position_meters=-0.01,
        gripper_opening_meters=0.02,
        robot_base_position_mujoco=(0.0, 0.4, 0.75),
        robot_base_quaternion_mujoco_wxyz=(1.0, 0.0, 0.0, 0.0),
        blocks=tuple(BlockWorldState("block_sim_{:02d}".format(index), (0.1 * index, 0.0, 0.84), (1.0, 0.0, 0.0, 0.0)) for index in range(1, 5)),
    )


class M136VisualSyncTests(unittest.TestCase):
    def test_protocol_roundtrip_is_small_and_private(self):
        original = frame(1)
        encoded = original.to_json_bytes()
        decoded = RobotWorldStateFrame.from_json_bytes(encoded)
        self.assertEqual(decoded, original)
        self.assertLess(len(encoded), 2500)
        self.assertNotIn("obj_", encoded.decode("utf-8"))
        self.assertEqual(decoded.mapping_version, M13_6_MAPPING_VERSION)

    def test_coordinate_and_quaternion_conversion(self):
        transform = MujocoToQuestTransform()
        self.assertEqual(transform.position((0.0, 0.0, 0.75)), (0.0, 0.0, 0.0))
        self.assertEqual(tuple(round(value, 6) for value in transform.position((0.1, -0.2, 0.85))), (0.07, 0.07, 0.14))
        quaternion = transform.orientation((1.0, 0.0, 0.0, 0.0))
        self.assertAlmostEqual(sum(value * value for value in quaternion), 1.0, places=6)
        self.assertAlmostEqual(abs(quaternion[0]), math.sqrt(0.5), places=6)

    def test_robot_base_orientation_fixture_proves_table_facing_yaw(self):
        fixture = m13_6_robot_base_orientation_fixture()
        self.assertEqual(fixture["mujocoTableCenterDirection"], (0.0, -1.0, 0.0))
        self.assertEqual(fixture["tableCenterDirectionQuest"], (0.0, 0.0, 0.7))
        self.assertEqual(fixture["yawOffsetDegrees"], 0.0)
        self.assertEqual(fixture["robotRootOrientationSource"], "MujocoToQuestTransform.orientation(robot_base_quaternion)")
        self.assertTrue(fixture["passes"])

    def test_unified_table_basis_preserves_semantic_directions(self):
        basis = MujocoToQuestTransform().basis_vectors()
        self.assertEqual(basis["mujoco_plus_x"], (0.7, 0.0, 0.0))
        self.assertEqual(basis["mujoco_plus_y"], (0.0, 0.0, -0.7))
        self.assertEqual(basis["mujoco_plus_z"], (0.0, 0.7, 0.0))

    def test_visual_sequential_fixture_is_m13_6_only(self):
        self.assertEqual(set(M13_6_VISUAL_SEQUENTIAL_PLACE_TARGETS), {
            "block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04",
        })

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_visual_fixture_is_uniform_catalog_sized_at_runtime_and_grasp_safe(self):
        import mujoco
        import sys
        sys.path.insert(0, "robot_arm")
        from utils.gripper_scene import build_m13_6_visual_scene, object_half_extents
        model, data = build_m13_6_visual_scene()
        mujoco.mj_forward(model, data)
        extents = [object_half_extents(model, "obj_{}".format(index)) for index in range(4)]
        self.assertTrue(all(extent == extents[0] for extent in extents))
        self.assertEqual(extents[0], (M13_6_VISUAL_BLOCK_EDGE_MUJOCO / 2.0,) * 3)
        self.assertAlmostEqual(M13_6_VISUAL_BLOCK_EDGE_QUEST, 0.056, places=12)
        self.assertLessEqual(M13_6_VISUAL_BLOCK_EDGE_MUJOCO, 0.086)
        sys.path.pop(0)

    def test_debug_viewer_is_opt_in_on_the_production_runners(self):
        self.assertFalse(inspect.signature(run_mujoco_pick_place_stream).parameters["viewer"].default)
        self.assertFalse(inspect.signature(run_mujoco_sequential_stream).parameters["viewer"].default)
        source = Path("integration/m13_6_visual_sync.py").read_text(encoding="utf-8")
        self.assertIn("mujoco.viewer.launch_passive(model, data)", source)
        self.assertIn("viewer.sync()", source)

    def test_stale_and_duplicate_frames_are_rejected(self):
        buffer = LatestStateBuffer()
        self.assertTrue(buffer.accept(frame(4)))
        self.assertFalse(buffer.accept(frame(4)))
        self.assertFalse(buffer.accept(frame(3)))
        self.assertTrue(buffer.accept(frame(5)))
        self.assertEqual(buffer.accepted_count, 2)
        self.assertEqual(buffer.duplicate_count, 1)
        self.assertEqual(buffer.stale_count, 1)

    def test_tcp_visual_sender_uses_persistent_nodelay_and_json_lines(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        received = {}

        def accept_one():
            connection, _ = server.accept()
            with connection:
                received["payload"] = connection.recv(4096)

        thread = threading.Thread(target=accept_one)
        thread.start()
        sender = TcpLatestStateSender("127.0.0.1", server.getsockname()[1])
        self.assertEqual(sender._socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY), 1)
        self.assertGreater(sender.send(frame(17)), 0)
        sender.close()
        thread.join(timeout=2.0)
        server.close()

        self.assertFalse(thread.is_alive())
        decoded = RobotWorldStateFrame.from_json_bytes(received["payload"].rstrip(b"\n"))
        self.assertEqual(decoded.sequence, 17)
        self.assertTrue(received["payload"].endswith(b"\n"))

    def test_malformed_frame_and_public_identity_are_rejected(self):
        with self.assertRaises(ValueError):
            BlockWorldState("obj_0", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))
        payload = frame(1).to_dict()
        payload["blocks"] = payload["blocks"][:3]
        with self.assertRaises(ValueError):
            RobotWorldStateFrame.from_dict(payload)

    def test_quest_receiver_static_contract_is_separate_from_control(self):
        receiver = Path("m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncReceiver.cs").read_text(encoding="utf-8")
        installer = Path("m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncAutoInstaller.cs").read_text(encoding="utf-8")
        self.assertIn("DefaultPort = 11002", receiver)
        self.assertNotIn("11001", receiver)
        self.assertIn("M13_6VisualTransportMode", receiver)
        self.assertIn("visualBlockSizeQuestMeters", receiver)
        self.assertIn("ApplyVisualBlockSize", receiver)
        self.assertIn("TcpListener", receiver)
        self.assertIn("NoDelay = true", receiver)
        self.assertIn("ReadLine()", receiver)
        self.assertIn("PublishLatestPacket", receiver)
        self.assertIn("TryTakeLatestPacket", receiver)
        self.assertIn("TrySetJointValue", receiver)
        self.assertIn("M9VirtualBlockTarget", receiver)
        self.assertIn("StartsWith(\"obj_\"", receiver)
        self.assertIn("BuildIdentity", receiver)
        self.assertIn("diagnostics build=", receiver)
        self.assertIn("RuntimeInitializeOnLoadMethod", installer)

    def test_receiver_keeps_scene_robot_anchor_fixed(self):
        receiver = Path("m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/DetectionManager/Scripts/M13_6VisualSyncReceiver.cs").read_text(encoding="utf-8")
        self.assertIn("m_tableRoot", receiver)
        self.assertIn("unified_table_frame", receiver)
        self.assertNotIn("m_robotRoot.position =", receiver)
        self.assertNotIn("m_robotRoot.rotation =", receiver)
        self.assertIn("unified_table_transform", receiver)
        self.assertNotIn("Quaternion.AngleAxis", receiver)

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_real_mujoco_pick_place_stream_is_monotonic_and_private(self):
        sink = RecordingStateSink()
        result = run_mujoco_pick_place_stream(sink=sink, rate_hz=30.0, max_steps=9000)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["planner"]["result"], "place_ok")
        self.assertGreater(len(sink.frames), 20)
        self.assertTrue(all(left.sequence < right.sequence for left, right in zip(sink.frames, sink.frames[1:])))
        self.assertFalse(result["publicIdentityLeak"])

        self.assertNotIn("obj_", json.dumps([frame.to_dict() for frame in sink.frames]))

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_visual_phase_rebase_starts_at_quest_catalog(self):
        import mujoco
        from robot_arm.utils.gripper_scene import build_gripper_scene

        model, data = build_gripper_scene()
        mujoco.mj_forward(model, data)
        sampler = MujocoStateSampler(
            mujoco,
            model,
            data,
            phase_anchor_positions_mujoco=m13_6_quest_catalog_anchor_positions_mujoco(),
        )
        first = sampler.frame(1, "startup")
        anchors = m13_6_quest_catalog_anchor_positions_mujoco()
        first_by_id = {block.logical_block_id: block for block in first.blocks}
        for logical_id, anchor in anchors.items():
            for actual, expected in zip(first_by_id[logical_id].position_mujoco, anchor):
                self.assertAlmostEqual(actual, expected, places=12)

        body_id = sampler.body_ids["block_sim_01"]
        initial = tuple(float(value) for value in data.xpos[body_id])
        data.xpos[body_id][:] = [initial[0] + 0.03, initial[1] - 0.02, initial[2] + 0.01]
        second = sampler.frame(2, "executing")
        second_by_id = {block.logical_block_id: block for block in second.blocks}
        expected = tuple(anchors["block_sim_01"][index] + (0.03, -0.02, 0.01)[index] for index in range(3))
        for actual, expected_value in zip(second_by_id["block_sim_01"].position_mujoco, expected):
            self.assertAlmostEqual(actual, expected_value, places=12)

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_real_mujoco_four_step_stream_is_continuous(self):
        sink = RecordingStateSink()
        result = run_mujoco_sequential_stream(sink=sink, rate_hz=30.0, max_steps_per_block=9000)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["result"] for item in result["planners"]], ["place_ok"] * 4)
        self.assertEqual(result["logicalBlockIds"], ["block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"])
        self.assertTrue(all(left.sequence < right.sequence for left, right in zip(sink.frames, sink.frames[1:])))
        self.assertFalse(result["publicIdentityLeak"])

        final_blocks = {block.logical_block_id: block for block in sink.frames[-1].blocks}
        self.assertTrue(all(block.position_mujoco[2] > 0.75 for block in final_blocks.values()))
        positions = [block.position_mujoco for block in final_blocks.values()]
        self.assertGreaterEqual(
            min(
                math.dist(left[:2], right[:2])
                for index, left in enumerate(positions)
                for right in positions[index + 1 :]
            ),
            0.08,
        )

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_visual_rebased_sequential_layout_is_compact_and_continuous(self):
        sink = RecordingStateSink()
        result = run_mujoco_sequential_stream(
            sink=sink,
            rate_hz=30.0,
            max_steps_per_block=9000,
            m13_6_visual_mode=True,
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["result"] for item in result["planners"]], ["place_ok"] * 4)
        metrics = result["stackMetrics"]
        self.assertTrue(metrics["stable"])
        for pair in metrics["stackPairs"].values():
            self.assertGreaterEqual(pair["contactCount"], 1)
            self.assertLess(pair["penetrationDepthMeters"], 0.005)
            self.assertLess(pair["horizontalErrorMeters"], 0.03)
            self.assertLess(abs(pair["verticalGapMeters"]), 0.005)

    def test_visual_mode_first_frame_uses_restored_horizontal_catalog_layout(self):
        self.assertEqual(
            M13_6_SYNTHETIC_BLOCK_LAYOUT_QUEST,
            ((-0.320, 0.0425, 0.0), (-0.105, 0.0425, 0.0),
             (0.105, 0.0425, 0.0), (0.320, 0.0425, 0.0)),
        )
        anchors = m13_6_quest_catalog_anchor_positions_mujoco()
        self.assertEqual(tuple(round(anchors[logical_id][1], 6) for logical_id in sorted(anchors)), (0.0, 0.0, 0.0, 0.0))
        self.assertEqual(tuple(round(anchors[logical_id][2], 6) for logical_id in sorted(anchors)), (0.810714, 0.810714, 0.810714, 0.810714))
        self.assertEqual(tuple(round(anchors[logical_id][0], 6) for logical_id in sorted(anchors)), (-0.457143, -0.15, 0.15, 0.457143))

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_actual_body_pose_and_gripper_relative_pose_are_reported(self):
        import mujoco
        import sys
        sys.path.insert(0, "robot_arm")
        from utils.gripper_scene import build_m13_6_visual_scene
        model, data = build_m13_6_visual_scene()
        mujoco.mj_forward(model, data)
        initial_source_positions = {
            logical_id: tuple(float(value) for value in data.xpos[body_id])
            for logical_id, body_id in {
                logical_id: int(mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, simulator_name))
                for logical_id, simulator_name in {
                    "block_sim_01": "obj_0", "block_sim_02": "obj_1",
                    "block_sim_03": "obj_2", "block_sim_04": "obj_3",
                }.items()
            }.items()
        }
        relative = m13_6_gripper_block_relative_pose(mujoco, model, data, "block_sim_01")
        self.assertEqual(len(relative["positionGripperFrameMujoco"]), 3)
        self.assertEqual(len(relative["quaternionGripperFrameMujocoWxyz"]), 4)
        sink = RecordingStateSink()
        result = run_mujoco_sequential_stream(sink=sink, rate_hz=30.0, max_steps_per_block=9000, m13_6_visual_mode=True)
        final = {block.logical_block_id: block.position_mujoco for block in sink.frames[-1].blocks}
        for logical_id, position in result["finalSourceBlockPositionsMujoco"].items():
            expected = tuple(
                result["phaseAnchorPositionsMujoco"][logical_id][axis]
                + position[axis] - initial_source_positions[logical_id][axis]
                for axis in range(3)
            )
            for actual, reported in zip(expected, final[logical_id]):
                self.assertAlmostEqual(actual, reported, places=12)
        sys.path.pop(0)

    @unittest.skipUnless(importlib.util.find_spec("mujoco") is not None, "MuJoCo is available only in the project 3.12 environment")
    def test_phase_transition_uses_raw_source_terminal_for_replay(self):
        first_sink = RecordingStateSink()
        first = run_mujoco_pick_place_stream(
            sink=first_sink,
            rate_hz=30.0,
            max_steps=9000,
            m13_6_visual_mode=True,
        )
        second_sink = RecordingStateSink()
        second = run_mujoco_sequential_stream(
            sink=second_sink,
            rate_hz=30.0,
            max_steps_per_block=9000,
            m13_6_visual_mode=True,
            phase_anchor_positions_mujoco=first["finalBlockPositionsMujoco"],
            initial_block_positions_mujoco={
                "block_sim_01": first["finalSourceBlockPositionsMujoco"]["block_sim_01"]
            },
        )
        self.assertEqual(first["planner"]["result"], "place_ok")
        self.assertEqual(second["status"], "completed")
        first_frame = {block.logical_block_id: block.position_mujoco for block in second_sink.frames[0].blocks}
        self.assertLess(math.dist(first_frame["block_sim_01"], first["finalBlockPositionsMujoco"]["block_sim_01"]), 1e-8)


if __name__ == "__main__":
    unittest.main()
