import tempfile
import unittest
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

from integration.m19_next_day_launcher import main


class M19NextDayLauncherTests(unittest.TestCase):
    def _run_demo(self, telemetry_host_argument=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_root = root / "demo-output"
            arguments = [
                "demo",
                "--com", "COM11",
                "--confirm-live-human",
                "--quest-host", "192.0.2.10",
                "--output-root", str(output_root),
            ]
            if telemetry_host_argument is not None:
                arguments.extend(("--telemetry-host", telemetry_host_argument))

            with patch("integration.m19_next_day_launcher.collect_preflight", return_value={"status": "PASS"}), \
                    patch("integration.m19_next_day_launcher.subprocess.run") as child_run, \
                    patch("integration.m13_8_final_runtime_integration.LocalJsonlIpcServer") as server_class, \
                    patch("integration.m19_mujoco_rpc.M19MujocoRpcEndpoint"), \
                    patch("integration.m19_paged_live_eeg_demo._create_mujoco_dispatcher", return_value=(object(), Mock())):
                server_class.return_value.address = ("127.0.0.1", 11003)
                child_run.return_value.returncode = 0
                self.assertEqual(main(arguments), 0)
                command = child_run.call_args.args[0]
                telemetry_index = command.index("--telemetry-host")
                return command[telemetry_index + 1]

    def test_demo_telemetry_defaults_to_quest_host(self):
        self.assertEqual(self._run_demo(), "192.0.2.10")

    def test_demo_telemetry_host_can_be_overridden(self):
        self.assertEqual(self._run_demo("192.0.2.20"), "192.0.2.20")

    def test_research_new_session_runs_preflight_before_serve(self):
        with tempfile.TemporaryDirectory() as temporary:
            session_root = Path(temporary) / "research-session"
            arguments = [
                "research", "--com", "COM11", "--confirm-live-human",
                "--session-root", str(session_root), "--session-id", "m19-phase2-test",
                "--seed", "190927",
            ]
            completed = subprocess.CompletedProcess([], 0)
            with patch("integration.m19_next_day_launcher.subprocess.run", side_effect=(completed, completed)) as child_run:
                self.assertEqual(main(arguments), 0)

            commands = [call.args[0] for call in child_run.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertEqual(commands[0][commands[0].index("preflight")], "preflight")
            self.assertIn("--confirm-live-human", commands[0])
            self.assertEqual(commands[1][commands[1].index("serve")], "serve")
            self.assertIn("--confirm-live-human", commands[1])
            self.assertEqual(commands[1][commands[1].index("--seed") + 1], "190927")

    def test_research_resume_uses_same_session_without_new_preflight_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            session_root = Path(temporary) / "research-session"
            arguments = [
                "research", "--com", "COM11", "--confirm-live-human",
                "--session-root", str(session_root), "--session-id", "m19-phase2-test",
                "--seed", "190927", "--resume",
            ]
            with patch("integration.m19_next_day_launcher.subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as child_run:
                self.assertEqual(main(arguments), 0)

            child_run.assert_called_once()
            command = child_run.call_args.args[0]
            self.assertEqual(command[command.index("serve")], "serve")
            self.assertIn("--resume", command)
            self.assertEqual(command[command.index("--session-root") + 1], str(session_root))


if __name__ == "__main__":
    unittest.main()
