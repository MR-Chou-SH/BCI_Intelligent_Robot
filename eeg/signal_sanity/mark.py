"""Append a manual, software-side annotation to an existing M6.1a session."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter_ns

from eeg.sample_association.jsonl import AppendOnlyJsonl


def main():
    parser = argparse.ArgumentParser(description="Append a manual M6.1a observation marker")
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    if not (args.session / "session-manifest.json").is_file():
        parser.error("session-manifest.json is required; marker was not written")
    AppendOnlyJsonl(args.session / "manual-annotations.jsonl").append({
        "recordType": "m6_1a_manual_annotation", "label": args.label,
        "pcMonotonicNs": perf_counter_ns(), "createdUtc": datetime.now(timezone.utc).isoformat(),
        "timingMeaning": "manual operator annotation; not a hardware EEG or optical trigger",
    })
    print(args.session / "manual-annotations.jsonl")


if __name__ == "__main__":
    main()
