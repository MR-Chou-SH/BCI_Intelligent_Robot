"""Read-only consistency checks for the frozen M34 evaluation artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATA_FILES = {
    "A": "raw-eeg-packets.jsonl",
    "B1": "raw-eeg-packets.jsonl",
    "B2": "raw-eeg-packets.jsonl",
    "S7": "raw-eeg.jsonl",
}
EXPECTED_TRIALS = {"A": 30, "B1": 29, "B2": 29, "S7": 30}
WINDOWS = {0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00}
DECODERS = {"FBCCA", "eTRCA", "TDCA"}


def load_json(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def read_csv(name: str):
    with (ROOT / name).open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure(condition: bool, message: str, results: list[dict]):
    results.append({"check": message, "passed": bool(condition)})
    if not condition:
        raise RuntimeError(f"M34 audit failed: {message}")


def main() -> int:
    checks: list[dict] = []
    manifest = load_json("data_manifest.json")
    protocol = load_json("frozen_protocol.json")
    pipeline = load_json("full_pipeline_freeze.json")
    heldout = load_json("heldout_results.json")
    complete = load_json("heldout_attempt_complete.json")
    sessions = {item["sessionKey"]: item for item in manifest["sessions"]}

    ensure(manifest["totalUniqueUsableTrials"] == 118, "manifest contains 118 unique usable trials", checks)
    ensure({key: value["usableTrialCount"] for key, value in sessions.items()} == EXPECTED_TRIALS,
           "session usable-trial counts match the frozen cohort", checks)
    ensure(
        protocol["trialLevelSplit"]["primaryHeldOut"]["sessionKeys"] == ["B2"]
        and protocol["trialLevelSplit"]["stressHeldOut"]["sessionKeys"] == ["S7"],
        "heldout sessions match the frozen protocol",
        checks,
    )
    ensure(protocol["dataset"]["primaryFormalCohort"]["frequencyMappingHz"]
           == {"slot0": 7.2, "slot1": 9.0, "slot2": 12.0},
           "slot-to-frequency mapping is fixed at 7.2/9/12 Hz", checks)

    trial_keys = [
        (trial["sessionKey"], trial["trialId"])
        for trial in manifest["trials"]
        if trial.get("usable")
    ]
    ensure(len(trial_keys) == 118 and len(set(trial_keys)) == 118,
           "usable session/trial keys are unique", checks)
    expected_split = {"A": "train", "B1": "dev", "B2": "heldout_primary", "S7": "heldout_stress"}
    split_ok = all(
        trial.get("split") == expected_split[trial["sessionKey"]]
        for trial in manifest["trials"]
        if trial.get("usable")
    )
    ensure(split_ok, "every usable trial remains in its frozen session split", checks)
    mapping_ok = all(
        trial["frequencyHz"] == {0: 7.2, 1: 9.0, 2: 12.0}[trial["slotIndex"]]
        for trial in manifest["trials"]
        if trial.get("usable")
    )
    ensure(mapping_ok, "all trial labels follow the fixed slot/frequency mapping", checks)

    before = heldout["rawSourceHashesBefore"]
    after = heldout["rawSourceHashesAfter"]
    actual_hashes = {}
    for key, session in sessions.items():
        raw_path = Path(session["sourcePath"]) / DATA_FILES[key]
        actual_hashes[key] = sha256(raw_path)
        ensure(
            actual_hashes[key] == session["rawFileSha256"] == before[key] == after[key],
            f"read-only source hash unchanged for session {key}",
            checks,
        )
    ensure(heldout["rawSourceHashesMatchManifest"] is True,
           "official heldout hash record matches the data manifest", checks)

    fixed = read_csv("heldout_fixed_summary.csv")
    fixed_keys = {
        (row["sessionKey"], row["decoder"], round(float(row["evidenceSeconds"]), 2))
        for row in fixed
    }
    expected_fixed_keys = {
        (session, decoder, round(window, 2))
        for session in ("B2", "S7")
        for decoder in DECODERS
        for window in WINDOWS
    }
    ensure(len(fixed) == 54 and fixed_keys == expected_fixed_keys,
           "fixed heldout table has 54 session/decoder/window rows", checks)
    ensure(all(int(row["n"]) == EXPECTED_TRIALS[row["sessionKey"]] for row in fixed),
           "fixed heldout metrics use the expected trial denominator", checks)
    fixed_trials = read_csv("heldout_fixed_per_trial.csv")
    ensure(len(fixed_trials) == 1593,
           "fixed heldout table contains all 1,593 trial-level predictions", checks)

    dynamic = read_csv("heldout_dynamic_per_trial.csv")
    ensure(len(dynamic) == 59, "dynamic heldout table contains 29 B2 and 30 S7 trials", checks)
    ensure(
        all(
            float(row["evidenceSeconds"]) in WINDOWS
            and float(row["nominalLoggedOnsetRelativeDecisionSeconds"])
            == 0.5 + float(row["evidenceSeconds"])
            for row in dynamic
        ),
        "dynamic stop schedule and 0.5 s nominal onset guard are consistent",
        checks,
    )

    context = read_csv("context_heldout_per_trial_seed.csv")
    context_summary = load_json("heldout_context_summary.json")
    context_keys = {
        (row["sessionKey"], row["scenario"], row["trialId"], row["assignmentSeed"])
        for row in context
    }
    ensure(len(context) == 11800 and len(context_keys) == 11800,
           "Context heldout table has 11,800 unique seeded trial-scenario pairs", checks)
    scenario_counts = {
        (session, scenario): sum(row["sessionKey"] == session and row["scenario"] == scenario for row in context)
        for session in ("B2", "S7")
        for scenario in ("precomputed_before_eeg", "measured_m33_api_latency_sensitivity")
    }
    ensure(scenario_counts == {
        ("B2", "precomputed_before_eeg"): 2900,
        ("B2", "measured_m33_api_latency_sensitivity"): 2900,
        ("S7", "precomputed_before_eeg"): 3000,
        ("S7", "measured_m33_api_latency_sensitivity"): 3000,
    }, "Context simulation has 100 assignment seeds per real heldout trial", checks)
    ensure(all(row["wrongEarlyStop"] == "0" or row["contextApplied"] == "1" for row in context),
           "wrong early stops occur only on Context-applied rows", checks)
    no_delay = all(
        condition["noDelayViolationCount"] == 0
        for session in context_summary["ContextResultsBySessionAndAvailability"].values()
        for condition in session.values()
    )
    ensure(no_delay, "all Context scenarios preserve the no-delay fallback invariant", checks)
    fallback_rows = [row for row in context if row["contextApplied"] != "1"]
    exact_fallback = all(
        row["contextPredictedSlotIndex"] == row["baselinePredictedSlotIndex"]
        and row["contextStopSeconds"] == row["baselineStopSeconds"]
        and row["contextCorrect"] == row["baselineCorrect"]
        for row in fallback_rows
    )
    ensure(exact_fallback, "non-applied Context cases exactly preserve EEG-only output and timing", checks)
    applied = [row for row in context if row["contextApplied"] == "1"]
    ensure(
        all(row["contextAuthorized"] == "1"
            and float(row["contextStopSeconds"]) < float(row["baselineStopSeconds"])
            for row in applied),
        "applied Context decisions are authorized and strictly earlier than EEG-only stopping",
        checks,
    )
    context_runner = (ROOT / "run_heldout_once.py").read_text(encoding="utf-8")
    ensure("context_top == point[\"top\"]" in context_runner
           and '"selected": point["top"]' in context_runner,
           "Context gate requires current EEG top agreement and emits that EEG top", checks)

    ensure(complete["status"] == "PASS" and complete["heldoutRetryUsed"] is False,
           "official final heldout run completed once without retry", checks)
    ensure(complete["fixedTrialRows"] == len(fixed_trials) == 1593
           and complete["contextTrialSeedScenarioRows"] == len(context) == 11800,
           "completion marker row counts match the saved result tables", checks)
    ensure(pipeline["status"] == "FROZEN_FOR_HELDOUT" and pipeline["eegFreeze"]["heldoutPredictionsOrMetricsEvaluated"] is False,
           "full pipeline freeze predates heldout outcome use", checks)

    plots = sorted((ROOT / "plots").glob("*.svg"))
    ensure(len(plots) >= 5 and all("</svg>" in plot.read_text(encoding="utf-8") for plot in plots),
           "all M34 SVG plots are present and closed", checks)
    report = ROOT / "M34_FINAL_REPORT.md"
    ensure(report.exists() and "Self-review, evidence limits, and conclusion" in report.read_text(encoding="utf-8"),
           "final report is present with its evidence-boundary review", checks)

    audit = {
        "status": "PASS",
        "checkedAtLocalDate": "2026-10-04",
        "rawSourcesReadOnly": True,
        "rawSourceHashesCurrent": actual_hashes,
        "fixedHeldoutRows": len(fixed),
        "dynamicHeldoutRows": len(dynamic),
        "contextTrialSeedScenarioRows": len(context),
        "contextAppliedRows": len(applied),
        "contextWrongEarlyStopRows": sum(row["wrongEarlyStop"] == "1" for row in context),
        "checks": checks,
    }
    (ROOT / "final_audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    print(f"M34 artifact audit PASS: {len(checks)} checks; {len(fixed)} fixed rows, {len(dynamic)} dynamic rows, {len(context)} Context pairs.")
    print("Raw EEG sources were read only; current hashes match the frozen manifest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
