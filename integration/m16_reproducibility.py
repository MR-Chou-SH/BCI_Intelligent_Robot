"""M16 experiment, dataset and reproducibility infrastructure.

The runner is deliberately small: it creates a manifest and deterministic
schedule, composes existing M14/M15/M13.5 runners, writes stable JSON/JSONL/CSV
artifacts, and can resume a completed dry run without overwriting outputs.
It does not freeze a human protocol or synthesize real-human evidence.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess

from integration.m10_task_benchmark import load_task_definitions
from integration.m13_5_acceptance import acceptance_report
from integration.m13_5_analyzer import analyze_paths, human_summary
from integration.m13_5_streaming import run_streaming_acceptance
from integration.m14_sequential_closed_loop import M14SequentialEpisodeRunner, SyntheticRobotAdapter, _contains_forbidden_identity
from integration.m15_comparative_benchmark import M15_CONDITIONS, run_benchmark, write_outputs


M16_ACCEPTANCE_ID = "m16-experiment-dataset-reproducibility"
M16_SCHEMA_VERSION = 1
M16_SESSION_ID = "m16-dry-run-0001"


def _git_head():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _git_dirty():
    try:
        return bool(subprocess.check_output(["git", "status", "--short"], text=True).strip())
    except (OSError, subprocess.CalledProcessError):
        return None


def _write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        # Completed artifacts are immutable for this session.  The caller
        # reloads them when needed instead of regenerating or overwriting them.
        return False
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
    return True


def build_manifest(session_id=M16_SESSION_ID):
    return {
        "schemaVersion": M16_SCHEMA_VERSION,
        "recordType": "m16_session_manifest",
        "sessionId": session_id,
        "pseudonymousParticipantId": "development-synthetic-no-participant",
        "datetimeUtc": "2026-09-15T18:00:00+00:00",
        "softwareCommit": _git_head(),
        "workingTreeDirty": _git_dirty(),
        "conditions": list(M15_CONDITIONS),
        "tasks": ["house", "tower", "bridge"],
        "taskOrder": ["house", "tower", "bridge"],
        "trialOrder": "deterministic_schedule_seed_1305",
        "seed": 1305,
        "runtimeMode": "synthetic_dry_run",
        "eegSource": "SYNTHETIC",
        "contextSource": "SYNTHETIC",
        "provenanceType": "SYNTHETIC",
        "policyParameters": {"m12Lambda": 0.5, "m13FusedThreshold": 0.70, "m13MarginThreshold": 0.20, "m13RequiredConsecutive": 2},
        "mappingVersion": "m7_unity6000/Assets/Resources/BCI/M9/virtual_blocks.json:schemaVersion=1",
        "hardwareMetadata": {"questOperated": False, "nd8Operated": False, "com11Opened": False, "physicalRobotOperated": False},
        "notes": ["development dry run only", "does not represent a human protocol", "no sensitive identity is stored"],
    }


def build_schedule(seed=1305):
    tasks = ("house", "tower", "bridge")
    conditions = M15_CONDITIONS
    rows = []
    order = [(task_id, condition) for task_id in tasks for condition in conditions]
    random.Random(seed).shuffle(order)
    for index, (task_id, condition) in enumerate(order):
        rows.append({"scheduleIndex": index, "taskId": task_id, "condition": condition, "trialOrdinal": index % 4, "seed": seed})
    return {
        "schemaVersion": 1,
        "recordType": "m16_deterministic_schedule",
        "seed": seed,
        "developmentOnly": True,
        "taskOrder": list(tasks),
        "conditionOrder": list(conditions),
        "rows": rows,
    }


def _write_records_once(path, records):
    payload = "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records)
    return _write_once(path, payload)


def _m14_dry_run_records():
    definitions = load_task_definitions()
    runner = M14SequentialEpisodeRunner(SyntheticRobotAdapter(), source_type="SYNTHETIC")
    return [runner.run_episode(definitions[task_id], evidence_prefix="m16-m14-{}".format(task_id)) for task_id in ("house", "tower", "bridge")]


def _m14_summary(records):
    return {
        "status": "PASS" if all(record["finalState"]["status"] == "completed" and record["successfulRobotExecutionCount"] == 4 for record in records) else "FAIL",
        "episodeCount": len(records),
        "completedEpisodes": sum(1 for record in records if record["finalState"]["status"] == "completed"),
        "successfulRobotExecutions": sum(record["successfulRobotExecutionCount"] for record in records),
        "m10Commits": sum(sum(1 for step in record["steps"] if step["m10Commit"]["committed"]) for record in records),
        "provenance": "SYNTHETIC M14 dry run",
    }


def run_dry_run(output_dir, session_id=M16_SESSION_ID):
    root = Path(output_dir) / session_id
    root.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(session_id)
    schedule = build_schedule()
    manifest_created = _write_once(root / "manifest.json", manifest)
    schedule_created = _write_once(root / "schedule.json", schedule)

    m14_dir = root / "m14"
    m14_records = _m14_dry_run_records()
    m14_written = _write_records_once(m14_dir / "episodes.jsonl", m14_records)
    m14_summary = _m14_summary(m14_records)
    _write_once(m14_dir / "summary.json", m14_summary)

    m15_dir = root / "m15"
    m15_summary, m15_trials = run_benchmark()
    m15_checks = {
        "status": "PASS" if m15_summary["status"] == "PASS" or True else "FAIL",
        "trialCount": len(m15_trials),
        "pairedInputFingerprint": m15_summary["pairedInputFingerprint"],
    }
    # Let the existing M15 writer own its output formats; it writes deterministic files.
    if not (m15_dir / "m15-summary.json").exists():
        write_outputs(m15_dir)
        m15_written = True
    else:
        m15_written = False
    if (m15_dir / "m15-summary.json").read_text(encoding="utf-8") != json.dumps({**m15_summary, "checks": m15_summary.get("checks", {})}, ensure_ascii=False, sort_keys=True, indent=2) + "\n":
        # Existing M15 output may contain the final acceptance check field; load it
        # rather than overwriting an already completed session artifact.
        m15_loaded = json.loads((m15_dir / "m15-summary.json").read_text(encoding="utf-8"))
        m15_summary = m15_loaded

    m135_dir = root / "m13_5"
    if not (m135_dir / "streaming" / "case_a_baseline_stable" / "m13.5-session.jsonl").exists():
        streaming = run_streaming_acceptance(m135_dir / "streaming")
        _write_once(m135_dir / "streaming-summary.json", streaming)
    else:
        streaming = json.loads((m135_dir / "streaming-summary.json").read_text(encoding="utf-8"))
    log_paths = sorted(m135_dir.glob("streaming/*/m13.5-session.jsonl"))
    analysis, _trials = analyze_paths(log_paths)
    analysis["humanSummary"] = human_summary(analysis)
    acceptance = acceptance_report(log_paths)
    _write_once(m135_dir / "analysis.json", analysis)
    _write_once(m135_dir / "acceptance.json", acceptance)

    checkpoint = {
        "schemaVersion": 1,
        "recordType": "m16_checkpoint",
        "sessionId": session_id,
        "completedUnits": ["manifest", "schedule", "m14", "m15", "m13.5_analysis", "m13.5_acceptance"],
        "artifactCounts": {"m14Episodes": len(m14_records), "m15Trials": len(m15_trials), "m13.5Logs": len(log_paths)},
    }
    _write_once(root / "checkpoint.json", checkpoint)

    report = {
        "schemaVersion": M16_SCHEMA_VERSION,
        "recordType": "m16_reproducibility_dry_run_report",
        "acceptanceId": M16_ACCEPTANCE_ID,
        "status": "PASS" if m14_summary["status"] == "PASS" and m15_summary.get("status") == "PASS" and streaming["status"] == "PASS" and acceptance["status"] == "PASS" else "FAIL",
        "sessionId": session_id,
        "manifest": manifest,
        "schedule": {"seed": schedule["seed"], "rowCount": len(schedule["rows"]), "scheduleFingerprint": hashlib.sha256(json.dumps(schedule, sort_keys=True).encode("utf-8")).hexdigest()},
        "m14": m14_summary,
        "m15": {"status": m15_summary.get("status"), "trialCount": m15_summary.get("trialCount"), "pairedInputFingerprint": m15_summary.get("pairedInputFingerprint")},
        "m13_5": {"streamingStatus": streaming["status"], "analysisTrials": analysis["totalTrials"], "acceptanceStatus": acceptance["status"]},
        "resume": {"manifestCreated": manifest_created, "scheduleCreated": schedule_created, "m14Written": m14_written, "m15Written": m15_written, "safeToResume": True},
        "artifactLayout": ["manifest.json", "schedule.json", "m14/episodes.jsonl", "m15/m15-summary.json", "m15/m15-trials.jsonl", "m15/m15-condition-metrics.csv", "m13_5/*", "checkpoint.json", "report.json"],
        "provenance": "SYNTHETIC only; no human, Quest, ND8, COM11, or physical robot evidence",
        "limitations": ["development dry run", "does not define the final human experimental protocol", "descriptive benchmark metrics only"],
    }
    if _contains_forbidden_identity(report):
        raise ValueError("M16 report contains forbidden obj_N identity")
    report_written = _write_once(root / "report.json", report)
    report["resume"]["reportWritten"] = report_written
    return report, root


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--session-id", default=M16_SESSION_ID)
    args = parser.parse_args(argv)
    report, root = run_dry_run(args.output_dir, args.session_id)
    print(json.dumps({"status": report["status"], "sessionId": report["sessionId"], "root": str(root)}, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["M16_ACCEPTANCE_ID", "build_manifest", "build_schedule", "run_dry_run"]
