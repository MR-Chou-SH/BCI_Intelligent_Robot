"""Audit M30 benchmark, transfer, report, provenance, and plot deliverables."""

from __future__ import annotations

import csv
import gzip
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(OUT))


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run(*, persist: bool = True) -> dict[str, Any]:
    from validate_m30_benchmark import validate as validate_benchmark

    benchmark = validate_benchmark()
    result_rows: list[dict[str, Any]] = []
    with (OUT / "semantic_context_results.jsonl").open("r", encoding="utf-8") as handle:
        result_rows = [json.loads(line) for line in handle if line.strip()]
    metadata = next((row for row in result_rows if row.get("recordType") == "m30_run_metadata"), None)
    primary = [row for row in result_rows if row.get("recordType") == "m30_semantic_context_result"
               and row.get("callKind") == "primary"]
    repeat_rows = [json.loads(line) for line in (OUT / "semantic_context_repeatability.jsonl").open(encoding="utf-8")
                   if line.strip()]
    repeats = [row for row in repeat_rows if row.get("recordType") == "m30_repeatability_result"]
    gates = _json(OUT / "coverage_operating_points.json")
    transfer = _json(OUT / "eeg_transfer_validation.json")
    report = (OUT / "M30_FINAL_REPORT.md").read_text(encoding="utf-8")

    required_files = [
        "M30_EXPERIMENT_SPEC.md", "semantic_context_sequence_benchmark_v2.json",
        "semantic_context_sequence_benchmark_lock.json", "scene_family_manifest.json",
        "relational_affordance_schema.json", "semantic_context_results.jsonl",
        "semantic_context_summary.json", "semantic_context_per_round.csv",
        "semantic_context_per_scene_family.csv", "coverage_operating_points.json",
        "coverage_precision_summary.csv", "page_projection_results.csv",
        "context_latency_summary.json", "eeg_transfer_spec.md",
        "eeg_transfer_summary.csv", "coverage_lambda_eeg_matrix.csv", "m28_vs_m30_comparison.csv",
        "M30_FINAL_REPORT.md", "eeg_transfer_validation.json",
    ]
    missing_files = [name for name in required_files if not (OUT / name).is_file()]
    transfer_csv = OUT / "eeg_transfer_per_trial.csv"
    transfer_gzip = OUT / "eeg_transfer_per_trial.csv.gz"
    if not transfer_csv.is_file() and not transfer_gzip.is_file():
        missing_files.append("eeg_transfer_per_trial.csv[.gz]")
    plot_paths = sorted((OUT / "plots").glob("*.svg"))
    invalid_svgs = []
    for path in plot_paths:
        try:
            ET.parse(path)
        except ET.ParseError:
            invalid_svgs.append(path.name)

    transfer_trial_rows = 0
    primary_rows = 0
    timing_rows = 0
    no_delay_violations = 0
    wrong_early_stop_rows = 0
    lambda_zero_nonzero_gain = 0
    if transfer_gzip.is_file():
        transfer_handle = gzip.open(transfer_gzip, "rt", encoding="utf-8-sig", newline="")
    else:
        transfer_handle = transfer_csv.open("r", encoding="utf-8-sig", newline="")
    with transfer_handle as handle:
        for row in csv.DictReader(handle):
            transfer_trial_rows += 1
            if row["conditionType"] == "primary_matrix":
                primary_rows += 1
            else:
                timing_rows += 1
            if float(row["contextStopSeconds"]) > float(row["baselineStopSeconds"]) + 1e-9:
                no_delay_violations += 1
            wrong_early_stop_rows += int(row["wrongEarlyStop"])
            if float(row["lambdaCtx"]) == 0.0 and abs(float(row["pairedGainSeconds"])) > 1e-9:
                lambda_zero_nonzero_gain += 1

    expected_questions = [f"{number}. **" for number in range(1, 12)]
    report_questions_present = all(item in report for item in expected_questions)
    checks = {
        "benchmarkStaticValidation": benchmark["status"] == "PASS",
        "lockedBenchmarkDigestMatchesSemanticRun": metadata is not None and metadata.get("benchmarkSha256") == benchmark["sha256"],
        "promptVersionFrozen": metadata is not None and metadata.get("promptVersion") == "m30-sequential-relational-context-v4",
        "engineVersionRecorded": metadata is not None and metadata.get("engineVersion") == "m30-context-precondition-guard-v1",
        "semanticPointCount126": len(primary) == 126,
        "repeatabilityPointCount16": len(repeats) == 16,
        "trainDevOnlyGateSelection": gates.get("heldOutUsedForGateSelection") is False,
        "transferIntegrityStatus": transfer.get("status") == "PASS",
        "m25InputsUnchanged": transfer.get("m25FrozenInputFingerprintsUnchanged") is True,
        "m25BaselineReproductions264": transfer.get("m25BaselineReproductions") == 264,
        "primaryTransferRows396000": primary_rows == 396000,
        "timingTransferRows71280": timing_rows == 71280,
        "totalTransferRows467280": transfer_trial_rows == 467280,
        "noDelayViolations": no_delay_violations == 0,
        "lambdaZeroExactBaseline": lambda_zero_nonzero_gain == 0,
        "plotsAtLeast13": len(plot_paths) >= 13,
        "allPlotsAreValidSvg": not invalid_svgs,
        "allRequiredArtifactsPresent": not missing_files,
        "reportAnswersAll11Questions": report_questions_present,
        "apiKeyNotPersisted": metadata is not None and metadata.get("apiKeyPersisted") is False,
    }
    transfer_safety = {
        "wrongEarlyStopRows": wrong_early_stop_rows,
        "noDelayViolationRows": no_delay_violations,
        "positiveLambdaConditionsWithZeroWrongEarlyStops": sum(
            int(row["wrongEarlyStopCount"]) == 0 and float(row["lambdaCtx"]) > 0.0
            for row in csv.DictReader((OUT / "eeg_transfer_summary.csv").open(encoding="utf-8-sig", newline=""))
            if row["scenario"] == "precomputed_before_eeg"
        ),
        "supportedSafeContextConditions": sum(
            str(row["supportedInTransferSimulation"]).casefold() == "true"
            for row in csv.DictReader((OUT / "eeg_transfer_summary.csv").open(encoding="utf-8-sig", newline=""))
            if row["scenario"] == "precomputed_before_eeg"
        ),
        "interpretation": "A PASS here is artifact/replay integrity only; positive-lambda results do not pass the safety gate.",
    }
    validation = {
        "recordType": "m30_final_validation", "status": "PASS" if all(checks.values()) else "FAIL",
        "benchmarkSha256": benchmark["sha256"], "decisionPointCount": len(primary),
        "repeatabilityPointCount": len(repeats), "transferTrialRowCount": transfer_trial_rows,
        "plotCount": len(plot_paths), "missingFiles": missing_files, "invalidSvgs": invalid_svgs,
        "checks": checks, "transferSafety": transfer_safety,
        "boundary": {
            "prospectiveHumanEegEvidence": False, "rawEegModified": False,
            "questOrPhysicalHardwareUsed": False, "productionM19Modified": False,
            "apiKeyPersisted": False,
        },
    }
    if persist:
        output = OUT / "final_validation.json"
        with output.open("x", encoding="utf-8") as handle:
            json.dump(validation, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return validation


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
