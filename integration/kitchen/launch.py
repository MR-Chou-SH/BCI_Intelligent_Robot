"""Native Python launcher for Linux/Windows; owns no hardware or planner."""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = ROOT / "experiments/kitchen_platform_v3"


def run_path(value):
    path = Path(value)
    path = path if path.is_absolute() else ROOT / path
    # Reject links before resolution, including an experiment-root link.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("Run paths must not contain symbolic links")
    path = path.resolve()
    if not path.is_relative_to(RUN_ROOT.resolve()) or path == RUN_ROOT.resolve():
        raise ValueError("Run paths must be inside this checkout's experiments/kitchen_platform_v3")
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=run_path)
    parser.add_argument("--ready-file", required=True, type=run_path)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--initialization-profile", choices=("fixed", "research"), default="research")
    parser.add_argument("--mode", choices=("manual", "realtime"), default="manual")
    parser.add_argument("--bind-host", default="127.0.0.1")
    parser.add_argument("--planner-port", type=int, default=11031)
    parser.add_argument("--world-port", type=int, default=11032)
    parser.add_argument("--run-seconds", type=float, default=0)
    parser.add_argument("--diagnostics",action="store_true",help="Opt in to large raw physics logs")
    args = parser.parse_args(argv)
    if args.data_root.exists() or args.ready_file.exists():
        parser.error("Use fresh data and ready paths; existing runs are preserved")
    if args.data_root == args.ready_file or args.data_root.is_relative_to(args.ready_file):
        parser.error("Data directory must not be the ready file or its child")
    if not 0 <= args.seed < 2**32:
        parser.error("seed must be uint32")
    if (not all(0 <= p <= 65535 for p in (args.planner_port, args.world_port)) or
            args.planner_port != 0 and args.planner_port == args.world_port):
        parser.error("Ports must be distinct or zero (automatic)")
    if not 0 <= args.run_seconds <= 3600:
        parser.error("run-seconds must be 0..3600")
    from integration.kitchen.platform_server import serve
    args.layout = "kitchen_v3_norice_candidate_013"
    serve(args)


if __name__ == "__main__":
    main()
