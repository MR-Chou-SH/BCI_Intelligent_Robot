"""Archive and correct a static-label draft before any M32 live model calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from build_m32_benchmark import _canonical, build_payload, _demo_cases  # noqa: E402


def main() -> None:
    benchmark_path = ROOT / "text_scene_sequence_benchmark.json"
    lock_path = ROOT / "text_scene_sequence_benchmark_lock.json"
    cases_path = ROOT / "text_scene_demo_cases.json"
    previous = json.loads(benchmark_path.read_text(encoding="utf-8"))
    previous_lock_bytes = lock_path.read_bytes()
    previous_lock = json.loads(previous_lock_bytes.decode("utf-8"))
    previous_lock_digest = hashlib.sha256(previous_lock_bytes).hexdigest()
    previous_digest = hashlib.sha256(_canonical(previous)).hexdigest()
    archive = ROOT / "prevalidation" / "v1-invalid-selected-target-labels"
    archive.mkdir(parents=True, exist_ok=False)
    for source in (benchmark_path, lock_path, cases_path):
        destination = archive / source.name
        with source.open("rb") as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst)

    payload = build_payload()
    digest = hashlib.sha256(_canonical(payload)).hexdigest()
    lock = {
        "benchmark_id": payload["benchmark_id"],
        "canonical_sha256": digest,
        "previous_prevalidation_sha256": previous_digest,
        "revision_reason": payload["prevalidation_revision_reason"],
        "scene_count": payload["scene_count"],
        "trajectory_count": payload["trajectory_count"],
        "decision_point_count": payload["decision_point_count"],
        "locked_at_utc": payload["frozen_at_utc"],
        "ground_truth_authored_before_live_model_calls": True,
    }
    for path, value in ((benchmark_path, payload), (lock_path, lock), (cases_path, _demo_cases(payload))):
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    revision = {
        "revision": 2,
        "status": "PASS_BEFORE_LIVE_CALLS",
        "previous_benchmark_sha256": previous_digest,
        "previous_lock_sha256": previous_lock_digest,
        "archived_at": str(archive.relative_to(ROOT)).replace("\\", "/"),
        "reason": payload["prevalidation_revision_reason"],
        "invalid_labels_removed": [
            "study-office-book-first-r04: bookshelf was already selected",
            "study-office-shelf-first-r03: bookshelf was already selected",
            "electronics-desk-phone-first-r04: charger was already selected",
        ],
        "live_model_calls_before_revision": 0,
        "revised_benchmark_sha256": digest,
    }
    log_path = ROOT / "benchmark_revision_log.jsonl"
    with log_path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(revision, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"status": "PASS_BEFORE_LIVE_CALLS", **lock}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
