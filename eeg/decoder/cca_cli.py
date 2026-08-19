"""Run the fixed M6.2a Standard CCA baseline."""

import argparse
import json

from .pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="M6.2a offline Standard CCA baseline")
    parser.add_argument("session")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_pipeline(args.session, args.output)
    print(json.dumps({"output": args.output, "accuracy": result["overall"]["accuracy"],
                      "correct": result["overall"]["correct"], "total": result["overall"]["total"]}))


if __name__ == "__main__":
    main()
