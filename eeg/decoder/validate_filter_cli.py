"""Run M6.3b current-vs-legacy FBCCA filter validation."""
import argparse
import json
from .filter_realization import run_filter_realization_validation


def main():
    parser = argparse.ArgumentParser(description="M6.3b FBCCA filter realization validation")
    parser.add_argument("session")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_filter_realization_validation(args.session, args.output)
    print(json.dumps([{"windowSeconds": row["windowSeconds"], "decoder": row["decoder"],
                       "accuracy": row["accuracy"], "correct": row["correct"]}
                      for row in result["results"]]))


if __name__ == "__main__":
    main()
