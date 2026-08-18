"""CLI for deterministic M6.1a raw-evidence analysis."""

import argparse
from pathlib import Path

from .analysis import analyze_session


def main():
    parser = argparse.ArgumentParser(description="Analyze one M6.1a ND8 raw-evidence session (no classifier)")
    parser.add_argument("--session", required=True, type=Path)
    args = parser.parse_args()
    summary = analyze_session(args.session)
    print(Path(args.session) / "analysis" / "signal-quality-summary.json")
    print("overallRecommendation={}".format(summary["overallRecommendation"]))


if __name__ == "__main__":
    main()
