"""Run M6.2b fixed FBCCA baseline."""
import argparse
import json
from .fbcca_pipeline import run_fbcca_pipeline


def main():
    parser = argparse.ArgumentParser(description="M6.2b offline FBCCA baseline")
    parser.add_argument("session")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_fbcca_pipeline(args.session, args.output)
    print(json.dumps({"output": args.output, "accuracy": result["overall"]["accuracy"],
                      "correct": result["overall"]["correct"], "total": result["overall"]["total"]}))


if __name__ == "__main__":
    main()
