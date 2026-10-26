from pathlib import Path
import tempfile
import unittest

from integration.kitchen.launch import ROOT, run_path, main


class NativeLauncherTests(unittest.TestCase):
    def test_paths_are_checkout_relative_and_cannot_escape(self):
        expected = ROOT / "experiments/kitchen_platform_v3/example/data"
        self.assertEqual(run_path("experiments/kitchen_platform_v3/example/data"), expected)
        for value in ("/tmp/outside", "experiments/kitchen_platform_v3", "experiments/kitchen_platform_v3/../../../outside"):
            with self.assertRaises(ValueError):
                run_path(value)
        with tempfile.TemporaryDirectory() as temp:
            link = Path(temp) / "linked"
            link.symlink_to(ROOT / "experiments/kitchen_platform_v3", target_is_directory=True)
            with self.assertRaises(ValueError):
                run_path(link / "data")

    def test_invalid_arguments_never_launch_physics(self):
        from unittest.mock import patch
        base = ["--data-root", "experiments/kitchen_platform_v3/launcher_validation/data",
                "--ready-file", "experiments/kitchen_platform_v3/launcher_validation/ready.json"]
        with patch("integration.kitchen.platform_server.serve") as serve:
            for args in (["--seed", "-1"], ["--planner-port", "-1"],
                         ["--planner-port", "11031", "--world-port", "11031"], ["--run-seconds", "3601"]):
                with self.assertRaises(SystemExit), patch("sys.stderr"):
                    main(base + args)
            serve.assert_not_called()


if __name__ == "__main__":
    unittest.main()
