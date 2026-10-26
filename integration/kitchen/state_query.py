"""Read-only object/full-state query CLI; never resets, steps or moves robots."""
import argparse
import json
from pathlib import Path

from .planner_client import PlannerClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11031)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--object-type")
    parser.add_argument("--snapshot", action="store_true", help="Full replay snapshot instead of selected object states")
    parser.add_argument("--output", type=Path, help="Optional new JSON file; never overwritten")
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error("Output must be a fresh file")
    if args.snapshot and (args.ids or args.object_type):
        parser.error("Full snapshot cannot be combined with object filters")
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    with PlannerClient(args.host, args.port) as client:
        reply = client.call("get_replay_snapshot" if args.snapshot else "get_object_states",
                            {} if args.snapshot else {"ids": args.ids, "object_type": args.object_type})
    if not reply.get("ok"):
        raise RuntimeError(reply.get("error"))
    result = json.dumps(reply["result"], ensure_ascii=False, allow_nan=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(result + "\n")
    print(result)


if __name__ == "__main__":
    main()
