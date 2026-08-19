"""CLI for M6.1b structural completeness verification."""

import argparse
from pathlib import Path

from .verifier import verify_session


def main():
    parser = argparse.ArgumentParser(description="Verify M6.1b dataset evidence structure; no classifier")
    parser.add_argument("--session", required=True, type=Path)
    args = parser.parse_args()
    result = verify_session(args.session)
    print("status={}".format(result["status"]))
    print(args.session / "dataset-completeness.json")
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
