"""Archive verification-gated M32 benchmark revision after the first live run."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from build_m32_benchmark import _canonical, _demo_cases, build_payload  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    archive = ROOT / "iterations" / "live-run-v1"
    manifest_path = archive / "archive_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit("first live-run archive manifest is required")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, metadata in manifest["files"].items():
        data = (archive / relative).read_bytes()
        if _sha256(data) != metadata["sha256"]:
            raise SystemExit("archived v1 file hash mismatch: " + relative)

    benchmark_path = ROOT / "text_scene_sequence_benchmark.json"
    lock_path = ROOT / "text_scene_sequence_benchmark_lock.json"
    cases_path = ROOT / "text_scene_demo_cases.json"
    revision_path = ROOT / "benchmark_revision_log.jsonl"
    previous_benchmark = benchmark_path.read_bytes()
    previous_lock = lock_path.read_bytes()
    if _sha256(previous_benchmark) != manifest["files"]["text_scene_sequence_benchmark.json"]["sha256"]:
        raise SystemExit("current benchmark does not match archived v1")
    if _sha256(previous_lock) != manifest["files"]["text_scene_sequence_benchmark_lock.json"]["sha256"]:
        raise SystemExit("current lock does not match archived v1")

    old_lock = json.loads(previous_lock.decode("utf-8"))
    payload = build_payload()
    digest = _sha256(_canonical(payload))
    lock = {
        "benchmark_id": payload["benchmark_id"],
        "canonical_sha256": digest,
        "previous_benchmark_sha256": old_lock["canonical_sha256"],
        "previous_lock_sha256": _sha256(previous_lock),
        "revision_reason": payload["benchmark_revision_reason"],
        "scene_count": payload["scene_count"],
        "trajectory_count": payload["trajectory_count"],
        "decision_point_count": payload["decision_point_count"],
        "locked_at_utc": payload["frozen_at_utc"],
        "ground_truth_authored_before_live_model_calls": True,
        "target_labels_or_selection_histories_changed": False,
        "scene_text_revised_after_live_run_v1": True,
        "live_run_v1_archive": str(archive.relative_to(ROOT)).replace("\\", "/"),
        "live_run_v1_archive_manifest_sha256": _sha256(manifest_path.read_bytes()),
    }
    revision = {
        "revision": 3,
        "status": "FROZEN_AFTER_ARCHIVING_LIVE_RUN_V1",
        "previous_benchmark_sha256": old_lock["canonical_sha256"],
        "previous_lock_sha256": _sha256(previous_lock),
        "archived_live_run": str(archive.relative_to(ROOT)).replace("\\", "/"),
        "reason": payload["benchmark_revision_reason"],
        "scene_text_changed": True,
        "target_labels_or_selection_histories_changed": False,
        "live_calls_before_revision": manifest["record_count"],
        "revised_benchmark_sha256": digest,
        "revised_at_utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }

    # The exact prior benchmark, lock, cases, results and summaries are hash-checked
    # in iterations/live-run-v1 before these task-owned canonical inputs are revised.
    _write_json(benchmark_path, payload)
    _write_json(lock_path, lock)
    _write_json(cases_path, _demo_cases(payload))
    with revision_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(revision, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"status": "FROZEN", "benchmark_sha256": digest,
                      "archived_v1_manifest_verified": True,
                      "decision_points": payload["decision_point_count"],
                      "target_labels_or_selection_histories_changed": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
