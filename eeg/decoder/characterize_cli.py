"""Run M6.3a fixed analysis-window characterization."""
import argparse
import json
from .characterization import run_characterization


def main():
    parser = argparse.ArgumentParser(description="M6.3a CCA/FBCCA window characterization")
    parser.add_argument("session")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_characterization(args.session, args.output)
    print(json.dumps([{"windowSeconds": row["windowSeconds"], "decoder": row["decoder"],
                       "accuracy": row["accuracy"], "correct": row["correct"]}
                      for row in result["results"]]))


if __name__ == "__main__":
    main()
