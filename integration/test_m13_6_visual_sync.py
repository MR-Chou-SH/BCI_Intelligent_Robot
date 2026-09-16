import json
import importlib.util
import math
import unittest
from pathlib import Path

from integration.m13_6_visual_sync import (
    BlockWorldState,
    LatestStateBuffer,
    MujocoToQuestTransform,
    M13_6_MAPPING_VERSION,
    RecordingStateSink,
    RobotWorldStateFrame,
    run_mujoco_sequential_stream,
    run_mujoco_pick_place_stream,
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

    def test_stale_and_duplicate_frames_are_rejected(self):
        buffer = LatestStateBuffer()
        self.assertTrue(buffer.accept(frame(4)))
        self.assertFalse(buffer.accept(frame(4)))
        self.assertFalse(buffer.accept(frame(3)))
        self.assertTrue(buffer.accept(frame(5)))
        self.assertEqual(buffer.accepted_count, 2)
        self.assertEqual(buffer.duplicate_count, 1)
        self.assertEqual(buffer.stale_count, 1)

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
        self.assertIn("ConcurrentQueue<byte[]>", receiver)
        self.assertIn("TrySetJointValue", receiver)
        self.assertIn("M9VirtualBlockTarget", receiver)
        self.assertIn("StartsWith(\"obj_\"", receiver)
        self.assertIn("RuntimeInitializeOnLoadMethod", installer)

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
    def test_real_mujoco_four_step_stream_is_continuous(self):
        sink = RecordingStateSink()
        result = run_mujoco_sequential_stream(sink=sink, rate_hz=30.0, max_steps_per_block=9000)
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["result"] for item in result["planners"]], ["place_ok"] * 4)
        self.assertEqual(result["logicalBlockIds"], ["block_sim_01", "block_sim_02", "block_sim_03", "block_sim_04"])
        self.assertTrue(all(left.sequence < right.sequence for left, right in zip(sink.frames, sink.frames[1:])))
        self.assertFalse(result["publicIdentityLeak"])


if __name__ == "__main__":
    unittest.main()
