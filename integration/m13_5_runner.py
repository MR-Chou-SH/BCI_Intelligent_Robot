"""Explicit-mode PC-only runner for M13.5 smoke sessions.

It intentionally consumes deterministic synthetic M12 evidence.  A future
live bridge may feed the same ``M135TrialRuntime.observe`` API from the existing
ND8/M6 callback path after hardware authorization and operator preflight.
"""

import argparse
import json
from pathlib import Path

from integration.m13_5_runtime import MODE_ACTIVE, MODE_BASELINE, MODE_SHADOW
from integration.m13_5_streaming import _run_case, _trajectory


def _scenario(name):
    if name == "stable":
        return _trajectory([((2.0, .01, .01), (.98, .005, .005)), ((2.0, .01, .01), (.98, .005, .005))])
    if name == "fallback":
        return _trajectory([((1.0, .9, .1), (.25, .25, .25)), ((1.0, .9, .1), (.25, .25, .25))])
    if name == "no-decision":
        return _trajectory([((1.0, 1.0, .1), (.25, .25, .25))])
    raise ValueError("scenario must be stable, fallback, or no-decision")


def run_mode_smoke(output_dir, mode, scenario):
    case = _run_case(Path(output_dir), "mode_{}_{}".format(mode, scenario), mode, _scenario(scenario))
    return {"schemaVersion": 1, "recordType": "m13_5_explicit_mode_smoke", "status": "PASS" if case["opened"] and case["finalSubmissionEventCount"] == 1 else "FAIL", "mode": mode, "scenario": scenario, "case": case}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=(MODE_BASELINE, MODE_SHADOW, MODE_ACTIVE), required=True)
    parser.add_argument("--scenario", choices=("stable", "fallback", "no-decision"), required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary-path", required=True)
    args = parser.parse_args(argv)
    summary = run_mode_smoke(args.output_dir, args.mode, args.scenario)
    path = Path(args.summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": summary["status"], "mode": args.mode, "scenario": args.scenario, "summaryPath": str(path)}, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
