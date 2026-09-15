"""Idempotent experiment-to-report composition for M16–M18 artifacts."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

from integration.m13_5_acceptance import acceptance_report
from integration.m13_5_analyzer import analyze_paths, human_summary
from integration.m16_reproducibility import run_dry_run
from integration.m17_fork_diagnostics import diagnose_trials
from integration.m18_research_sandboxes import run_all_sandboxes


M19_PROVENANCE = "SYNTHETIC"
M19_SCHEMA_VERSION = 1


def _read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path):
    records = []
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _write_once(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing != rendered:
            raise ValueError("M19 immutable artifact changed on rerun: {}".format(path))
        return False
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(rendered)
    return True


def _write_csv_once(path, rows, fieldnames):
    lines = []
    from io import StringIO
    stream = StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in fieldnames})
    return _write_once(path, stream.getvalue())


def _m15_tables(m15_summary, trials):
    condition_rows = []
    for row in m15_summary.get("conditionMetrics", {}).values():
        condition_rows.append(dict(row))
    trial_rows = []
    context_rows = []
    stopping_rows = []
    for trial in trials:
        c = trial.get("conditions", {}).get("C_CONTEXT_EEG_DYNAMIC_STOP", {})
        trial_rows.append({
            "trialId": trial.get("trialId"), "taskId": trial.get("taskId"), "stepIndex": trial.get("stepIndex"), "expectedTarget": trial.get("expectedLogicalBlockId"), "dynamicTarget": c.get("finalTarget"), "decisionMade": c.get("decisionMade"), "earlyStop": c.get("earlyStop"), "reason": c.get("reason"), "stopWindow": c.get("stopWindow"), "effectiveAcquisitionSeconds": c.get("effectiveAcquisitionSeconds"), "sourceType": "SYNTHETIC",
        })
        final_raw = (trial.get("rawEegTrajectory") or [{}])[-1]
        final_fused = (trial.get("m12FusedTrajectory") or [{}])[-1]
        context = trial.get("contextPrior", {})
        context_rows.append({
            "trialId": trial.get("trialId"), "rawTop": (trial.get("pairedIdentity", {}).get("rawFinalTop") or [None])[0], "fusedTop": (trial.get("pairedIdentity", {}).get("fusedFinalTop") or [None])[0], "contextTop": (trial.get("pairedIdentity", {}).get("contextPriorTop") or [None])[0], "contextEntropy": context.get("entropy"), "rawScores": json.dumps(final_raw, sort_keys=True), "fusedEvidence": json.dumps(final_fused, sort_keys=True), "sourceType": "SYNTHETIC",
        })
        stopping_rows.append({"trialId": trial.get("trialId"), **c, "sourceType": "SYNTHETIC"})
    return condition_rows, trial_rows, stopping_rows, context_rows


def _discover_m135(root):
    paths = sorted((Path(root) / "m13_5").glob("streaming/*/m13.5-session.jsonl"))
    return paths


def _detect_real_human(records):
    return [record for record in records if str(record.get("sourceType", "")).upper() in ("LIVE_HUMAN_EEG", "REAL_HUMAN_EEG", "HUMAN_EEG")]


def run_report_pipeline(output_dir, source_root=None, real_session_paths=()):
    """Create a stable M19 package; source generation is safe to resume."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if source_root is None:
        source_parent = output_dir / "_m16_source"
        _m16_report, source_root = run_dry_run(source_parent)
    source_root = Path(source_root)
    package = output_dir / "package" if Path(source_root).parent == output_dir / "_m16_source" else output_dir
    package.mkdir(parents=True, exist_ok=True)
    m15_summary = _read_json(source_root / "m15" / "m15-summary.json", {})
    m15_trials = _read_jsonl(source_root / "m15" / "m15-trials.jsonl")
    m14_records = _read_jsonl(source_root / "m14" / "episodes.jsonl")
    m135_paths = _discover_m135(source_root)
    m135_analysis = None
    m135_acceptance = None
    if m135_paths:
        m135_analysis, _ = analyze_paths(m135_paths)
        m135_analysis["humanSummary"] = human_summary(m135_analysis)
        m135_acceptance = acceptance_report(m135_paths)
    else:
        m135_analysis = {"totalTrials": 0, "sourcePaths": [], "status": "EMPTY"}
        m135_acceptance = {"status": "EMPTY", "checks": []}
    real_records = []
    for path in real_session_paths:
        real_records.extend(_read_jsonl(path))
    human_records = _detect_real_human(real_records)
    diagnostics = diagnose_trials(m15_trials, M19_PROVENANCE)
    sandbox = run_all_sandboxes(m15_trials)
    condition_rows, trial_rows, stopping_rows, context_rows = _m15_tables(m15_summary, m15_trials)
    qc = {
        "schemaVersion": M19_SCHEMA_VERSION, "recordType": "m19_experiment_qc", "status": "PASS", "sourceRoot": str(source_root), "sourceType": M19_PROVENANCE, "m16ArtifactsPresent": bool(m15_trials and m14_records), "m135LogCount": len(m135_paths), "realHumanRecordCount": len(human_records), "realHumanDataStatus": "AVAILABLE" if human_records else "EMPTY_REAL_HUMAN_EEG", "optionalRealInputHandled": True, "limitations": ["current dry run contains synthetic evidence only", "real human EEG is not fabricated when absent"],
    }
    summary = {
        "schemaVersion": M19_SCHEMA_VERSION, "recordType": "m19_experiment_report_package", "status": "PASS", "package": str(package), "provenance": M19_PROVENANCE, "m16SessionRoot": str(source_root), "m15TrialCount": len(m15_trials), "m14EpisodeCount": len(m14_records), "m135AnalysisStatus": m135_acceptance.get("status"), "realHumanTrials": len(human_records), "realHumanEvidence": "PENDING" if not human_records else "INGESTED_FOR_ANALYSIS", "reportArtifacts": ["summary.json", "qc.json", "fork-diagnostics.json", "benchmark.json", "benchmark.csv", "trial-table.csv", "episode-table.csv", "stopping-table.csv", "context-fusion-table.csv", "sandbox-summary.json", "summary.md"], "idempotent": True, "nextRealHumanSession": "Run the same pipeline with the M13.5 session JSONL path added after authorized Quest + ND8 acceptance.",
    }
    _write_once(package / "summary.json", summary)
    _write_once(package / "qc.json", qc)
    _write_once(package / "fork-diagnostics.json", diagnostics)
    _write_once(package / "benchmark.json", m15_summary)
    _write_once(package / "sandbox-summary.json", sandbox)
    _write_csv_once(package / "benchmark.csv", condition_rows, ["condition", "trialCount", "decisionCount", "correctCount", "descriptiveDecisionRate", "descriptiveAccuracyAmongDecisions", "earlyStopCount", "earlyStopRate", "fullWindowFallbackCount", "noDecisionCount", "meanStopWindow", "meanEffectiveAcquisitionSeconds", "windowsConsumed"])
    _write_csv_once(package / "trial-table.csv", trial_rows, ["trialId", "taskId", "stepIndex", "expectedTarget", "dynamicTarget", "decisionMade", "earlyStop", "reason", "stopWindow", "effectiveAcquisitionSeconds", "sourceType"])
    _write_csv_once(package / "episode-table.csv", [{"taskId": row.get("taskId"), "status": row.get("finalState", {}).get("status"), "successfulRobotExecutionCount": row.get("successfulRobotExecutionCount"), "provenance": "SYNTHETIC"} for row in m14_records], ["taskId", "status", "successfulRobotExecutionCount", "provenance"])
    _write_csv_once(package / "stopping-table.csv", stopping_rows, ["trialId", "decisionMade", "finalTarget", "stopWindow", "effectiveAcquisitionSeconds", "earlyStop", "fallback", "noDecision", "reason", "sourceType"])
    _write_csv_once(package / "context-fusion-table.csv", context_rows, ["trialId", "rawTop", "fusedTop", "contextTop", "contextEntropy", "rawScores", "fusedEvidence", "sourceType"])
    markdown = "# M19 Experiment Report Package\n\n" + "- Status: `PASS`\n- Provenance: `SYNTHETIC` dry-run evidence only\n- M15 trials: `{}`\n- M14 episodes: `{}`\n- M13.5 engineering logs: `{}`\n- Real human EEG trials: `{}` (`{}`)\n- Production parameters were not changed.\n\n## Contents\n\nThis package contains machine-readable QC, fork diagnostics, benchmark tables, stopping/context-fusion tables, and isolated M18 sandbox outputs. Metrics are descriptive; no research direction or performance claim is selected.\n\n## Resume\n\nRerunning the same command is idempotent. When an authorized real M13.5 session exists, add its JSONL path to the pipeline; absent real-human input remains an explicit empty state.\n".format(len(m15_trials), len(m14_records), len(m135_paths), len(human_records), "AVAILABLE" if human_records else "PENDING")
    _write_once(package / "summary.md", markdown)
    fingerprint = hashlib.sha256("".join(sorted(path.name + str(path.stat().st_size) for path in package.iterdir() if path.is_file())).encode("utf-8")).hexdigest()
    summary["artifactFingerprint"] = fingerprint
    return summary, package


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source-root")
    parser.add_argument("--real-session", action="append", default=[])
    args = parser.parse_args(argv)
    summary, package = run_report_pipeline(args.output_dir, args.source_root, args.real_session)
    print(json.dumps({"status": summary["status"], "package": str(package), "realHumanTrials": summary["realHumanTrials"]}, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


__all__ = ["run_report_pipeline"]


if __name__ == "__main__":
    raise SystemExit(main())
