import unittest
from argparse import Namespace
from pathlib import Path
import tempfile
from unittest.mock import patch

from integration.m13_6_usb_visual_acceptance import _joint_diagnostic_q1, _joint_diagnostic_frame
from integration.m13_6_visual_demo import _choose_start_sequence, run


class M136VisualDemoTests(unittest.TestCase):
    def test_real_all_phase_entrypoint_crosses_mujoco_boundaries(self):
        class FakeSender:
            sent_count = 0
            last_error = None

            def send(self, frame):
                self.sent_count += 1

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as output_dir:
            args = Namespace(
                adb=None,
                transport="usb",
                forward_port=21002,
                phase="all",
                human=False,
                duration_seconds=0.0,
                rate_hz=30.0,
                start_sequence=1,
                joint_duration_seconds=0.0,
                logical_block_id="block_sim_01",
                max_steps=1,
                output=str(Path(output_dir) / "visual-demo.json"),
            )
            with patch("integration.m13_6_visual_demo._adb_path", return_value="adb"), \
                    patch("integration.m13_6_visual_demo._ensure_app", return_value=123), \
                    patch("integration.m13_6_visual_demo._forward", return_value=(True, [])), \
                    patch("integration.m13_6_visual_demo._quest_logs", return_value={"diagnostics": [], "frameAccepted": [], "allM136Lines": []}), \
                    patch("integration.m13_6_visual_demo._socket_state", return_value={}), \
                    patch("integration.m13_6_visual_demo.time.sleep"), \
                    patch("integration.m13_6_usb_visual_acceptance.TcpLatestStateSender", return_value=FakeSender()):
                status = run(args)
        self.assertEqual(status, 0)
    def test_default_sequence_seed_moves_past_existing_quest_sequence(self):
        logs = {
            "diagnostics": ["M13_6_VISUAL diagnostics lastSeq=200089"],
            "frameAccepted": [],
            "allM136Lines": [],
        }
        self.assertEqual(_choose_start_sequence(logs, requested=None, wall_clock_seed=1000), 200090)
        self.assertEqual(_choose_start_sequence(logs, requested=7, wall_clock_seed=1000), 7)

    def test_single_joint_diagnostic_has_visible_q1_excursion(self):
        values = [_joint_diagnostic_q1(value) for value in (0.0, 2.0, 5.0, 7.0, 8.0)]
        self.assertEqual(values[0], 0.0)
        self.assertEqual(values[-1], 0.0)
        self.assertGreater(max(values), 0.3)
        self.assertLess(min(values), -0.3)
        frame = _joint_diagnostic_frame(12, 2.0)
        self.assertAlmostEqual(frame.joint_positions_radians[0], 0.35)
        self.assertEqual(frame.joint_positions_radians[1:], (-0.25, 0.18, -1.7, 0.0, 1.65, 0.75))


if __name__ == "__main__":
    unittest.main()
