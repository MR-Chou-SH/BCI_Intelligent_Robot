"""Read-only audit and manifest builder for the frozen M34 EEG sources.

Raw EEG lives outside the repository and is opened only for hashing, packet
count/header inspection, and later read-only decoding.  This module never
writes to a source session.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


FREQUENCY_BY_CLASS = {
    "target_left": 7.2,
    "target_center": 9.0,
    "target_right": 12.0,
}

SESSIONS = [
    {
        "session_key": "A",
        "session_id": "m6_1b-dataset-20260819T090215Z-d70eaa0d",
        "path": Path(r"D:\EEG_Study\m6_1b\m6_1b-dataset-20260819T090215Z-d70eaa0d"),
        "manifest_file": "session-manifest.json",
        "raw_file": "raw-eeg-packets.jsonl",
        "association_file": "derived-association.jsonl",
        "label_file": "trial-ground-truth.jsonl",
        "excluded_trials": {},
        "split": "train",
        "cohort": "formal_m6",
        "prior_use": "M6 offline FBCCA/CCA, M6.5/6.6 replay, M21-M25 analysis via 250 Hz feature cache; no eTRCA/TDCA training found.",
        "historical_channels": [2, 3, 4, 5, 7],
    },
    {
        "session_key": "B1",
        "session_id": "m6_4-dataset-20260819T155147Z-386f95dd",
        "path": Path(r"D:\EEG_Study\m6_4\m6_4-dataset-20260819T155147Z-386f95dd"),
        "manifest_file": "session-manifest.json",
        "raw_file": "raw-eeg-packets.jsonl",
        "association_file": "derived-association.jsonl",
        "label_file": "trial-ground-truth.jsonl",
        "excluded_trials": {"m6_4-trial-011": "Frozen M6 QC: stale clock-sync freshness; excluded before decoding."},
        "split": "dev",
        "cohort": "formal_m6",
        "prior_use": "M6 exploratory cross-session FBCCA/CCA, M6.5/6.6 replay, M21-M25 analysis via 250 Hz feature cache; no eTRCA/TDCA training found.",
        "historical_channels": [2, 3, 4, 5, 7],
    },
    {
        "session_key": "B2",
        "session_id": "m6_4-dataset-20260819T161943Z-afaac607",
        "path": Path(r"D:\EEG_Study\m6_4\m6_4-dataset-20260819T161943Z-afaac607"),
        "manifest_file": "session-manifest.json",
        "raw_file": "raw-eeg-packets.jsonl",
        "association_file": "derived-association.jsonl",
        "label_file": "trial-ground-truth.jsonl",
        "excluded_trials": {"m6_4-trial-023": "Frozen M25 formal-88 boundary: replay-supplemented exploratory trial excluded."},
        "split": "heldout_primary",
        "cohort": "formal_m6",
        "prior_use": "M6 exploratory cross-session FBCCA/CCA, M6.5/6.6 replay, M21-M25 analysis via 250 Hz feature cache; no eTRCA/TDCA training found.",
        "historical_channels": [2, 3, 4, 5, 7],
    },
    {
        "session_key": "S7",
        "session_id": "m6_7-formal-20260820T160940Z-0ef360f6",
        "path": Path(r"D:\EEG_Study\m6_7\m6_7-formal-20260820T160940Z-0ef360f6"),
        "manifest_file": "manifest.json",
        "raw_file": "raw-eeg.jsonl",
        "association_file": "associations.jsonl",
        "label_file": "formal-plan.json",
        "excluded_trials": {},
        "split": "heldout_stress",
        "cohort": "m6_7_stress_online",
        "prior_use": "Prior real stress-online FBCCA engineering validation: 30/30 technical-valid and post-hoc correct at the historical long window; not used by M21-M25 raw-waveform analysis.",
        "historical_channels": [2, 4, 7],
    },
]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_raw(path: Path):
    packet_count = 0
    first_packet = None
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            packet_count += 1
            if packet_count == 1:
                first_packet = json.loads(line)
    if not first_packet:
        raise ValueError(f"empty raw EEG file: {path}")
    sample_count = int(first_packet["sampleCountPerChannel"])
    channel_count = int(first_packet["channelCount"])
    sample_rate_hz = float(first_packet.get("nominalSamplingRateHz") or first_packet.get("samplingRateHz"))
    return {
        "packetCount": packet_count,
        "sampleCountPerPacketPerChannel": sample_count,
        "rawSampleCountPerChannel": packet_count * sample_count,
        "channelCount": channel_count,
        "sampleRateHz": sample_rate_hz,
        "firstPacketSequence": first_packet.get("packetSequence"),
        "rawFileBytes": path.stat().st_size,
        "rawFileSha256": sha256(path),
    }


def session_labels(spec):
    label_path = spec["path"] / spec["label_file"]
    if spec["label_file"].endswith(".jsonl"):
        labels = list(read_jsonl(label_path))
    else:
        labels = read_json(label_path)["trials"]
    by_id = {row["trialId"]: row for row in labels}
    if len(by_id) != len(labels):
        raise ValueError(f"duplicate trial labels in {label_path}")
    return labels, by_id


def session_associations(spec):
    path = spec["path"] / spec["association_file"]
    records = list(read_jsonl(path))
    result = {}
    for record in records:
        result.setdefault(record["trialId"], {})[record["stimulusEventType"]] = record
    return records, result


def validate_session(spec):
    path = spec["path"]
    source_manifest = read_json(path / spec["manifest_file"])
    raw = inspect_raw(path / spec["raw_file"])
    labels, labels_by_id = session_labels(spec)
    association_rows, associations = session_associations(spec)
    valid_status = None
    technical_by_trial = {}
    if spec["session_key"] == "S7":
        technical = read_json(path / "technical-validity.json")
        technical_by_trial = {row["trialId"]: row["technicalStatus"] for row in technical}

    trial_rows = []
    excluded = []
    for trial in sorted(labels, key=lambda item: item["trialIndex"]):
        trial_id = trial["trialId"]
        start = associations.get(trial_id, {}).get("stimulus_started_software")
        stop = associations.get(trial_id, {}).get("stimulus_stopped_software")
        reason = spec["excluded_trials"].get(trial_id)
        if not start or not stop:
            reason = reason or "missing start/stop sample association"
        elif not start.get("associationValid") or not stop.get("associationValid"):
            reason = reason or "invalid software-derived sample association"
        elif start.get("timestampSegmentId") != stop.get("timestampSegmentId"):
            reason = reason or "start/stop crossed a timestamp segment"
        elif start.get("packetContinuityStatus") != "continuous":
            reason = reason or "start association not continuous"
        elif stop.get("packetContinuityStatus") not in ("continuous", "anomaly"):
            reason = reason or "stop association continuity rejected"
        if start and stop and start.get("estimatedGlobalSampleIndex") is not None and stop.get("estimatedGlobalSampleIndex") is not None:
            start_sample = int(start["estimatedGlobalSampleIndex"])
            stop_sample = int(stop["estimatedGlobalSampleIndex"])
        else:
            start_sample = stop_sample = None
        if start_sample is not None and stop_sample is not None:
            if start_sample < 0 or stop_sample > raw["rawSampleCountPerChannel"] or stop_sample <= start_sample:
                reason = reason or "sample range outside raw recording or non-positive"
            elif stop_sample - start_sample < int(1.5 * raw["sampleRateHz"]):
                reason = reason or "less than 1.5 s of post-onset EEG available"

        class_id = trial.get("targetId")
        frequency = float(trial.get("nominalFrequencyHz", -1.0))
        if class_id not in FREQUENCY_BY_CLASS or abs(frequency - FREQUENCY_BY_CLASS[class_id]) > 0.05:
            reason = reason or "class/frequency mapping does not match frozen slot mapping"
        if spec["session_key"] == "S7" and technical_by_trial.get(trial_id) != "technical_valid":
            reason = reason or f"technical validity is {technical_by_trial.get(trial_id)}"

        original = (start or {}).get("originalQuestEvent") or {}
        if original:
            if original.get("targetId") != class_id or abs(float(original.get("nominalFrequencyHz", -1.0)) - frequency) > 0.05:
                reason = reason or "label disagrees with source stimulus event"
        row = {
            "sessionKey": spec["session_key"],
            "sessionId": spec["session_id"],
            "cohort": spec["cohort"],
            "split": spec["split"],
            "trialId": trial_id,
            "trialIndex": trial["trialIndex"],
            "targetId": class_id,
            "frequencyHz": frequency,
            "slotIndex": [7.2, 9.0, 12.0].index(round(frequency, 1)),
            "channelCount": raw["channelCount"],
            "channelIndices": list(range(raw["channelCount"])),
            "sessionUsableChannels": spec["historical_channels"],
            "sampleRateHz": raw["sampleRateHz"],
            "rawSampleCountPerChannel": raw["rawSampleCountPerChannel"],
            "rawPacketCount": raw["packetCount"],
            "onsetDefinition": "Quest stimulus_started_software event mapped to an estimated ND8 global sample index; hardware sample anchor and physical optical onset are unverified.",
            "onsetGlobalSampleIndex": start_sample,
            "stopGlobalSampleIndex": stop_sample,
            "usableStartPcMonotonicNsEstimate": (start or {}).get("estimatedPcEventMonotonicNs"),
            "usableEndPcMonotonicNsEstimate": (stop or {}).get("estimatedPcEventMonotonicNs"),
            "sampleAnchorState": (start or {}).get("sampleAnchorState"),
            "mappingMethod": (start or {}).get("mappingMethod"),
            "mappingResidualSeconds": (start or {}).get("nd8MappingResidualSeconds"),
            "mappingUncertaintySeconds": (start or {}).get("nd8MappingUncertaintySeconds"),
            "preprocessingProvenance": "Raw 1000 Hz packet samples; historical M6 reference is per-channel de-meaned, no filter/no resample. M21-M25 used a separate 250 Hz feature cache, not this raw waveform.",
            "rawWaveformExists": True,
            "previousUse": spec["prior_use"],
            "qualityExclusion": reason or "",
            "usable": reason is None,
            "historicalTrialStatus": trial.get("trialStatus"),
        }
        if reason:
            excluded.append({"trialId": trial_id, "reason": reason})
        else:
            trial_rows.append(row)

    # Raw/source metadata is recorded once per session and referenced by each trial.
    session_summary = {
        "sessionKey": spec["session_key"],
        "sessionId": spec["session_id"],
        "sourcePath": str(path),
        "manifestFile": spec["manifest_file"],
        "sourceManifestStatus": source_manifest.get("status"),
        "sourceManifestMode": source_manifest.get("mode"),
        "sourceManifestExperiment": source_manifest.get("experiment"),
        "plannedTrialCount": len(labels),
        "usableTrialCount": len(trial_rows),
        "excludedTrialCount": len(excluded),
        "classCountsUsable": {
            class_id: sum(1 for row in trial_rows if row["targetId"] == class_id)
            for class_id in FREQUENCY_BY_CLASS
        },
        "frequenciesHz": [7.2, 9.0, 12.0],
        "channelCount": raw["channelCount"],
        "selectedHistoricalChannelIndices": spec["historical_channels"],
        "sampleRateHz": raw["sampleRateHz"],
        "rawSampleCountPerChannel": raw["rawSampleCountPerChannel"],
        "rawPacketCount": raw["packetCount"],
        "rawFileBytes": raw["rawFileBytes"],
        "rawFileSha256": raw["rawFileSha256"],
        "hardwareTimingVerified": bool(source_manifest.get("hardwareTimingVerified", False)),
        "physicalOpticalTimingVerified": bool(source_manifest.get("physicalOpticalTimingVerified", False)),
        "hardwareSampleAnchorVerified": bool(source_manifest.get("hardwareSampleAnchorVerified", False)),
        "previousUse": spec["prior_use"],
        "exclusions": excluded,
    }
    return session_summary, trial_rows


def main():
    output_dir = Path(__file__).resolve().parent
    session_summaries = []
    trial_rows = []
    for spec in SESSIONS:
        summary, rows = validate_session(spec)
        session_summaries.append(summary)
        trial_rows.extend(rows)

    if len(trial_rows) != 118:
        raise SystemExit(f"expected 118 frozen usable trials, found {len(trial_rows)}")
    if [session["usableTrialCount"] for session in session_summaries] != [30, 29, 29, 30]:
        raise SystemExit("frozen session usable counts changed; inspect before updating the protocol")

    manifest = {
        "schemaVersion": 1,
        "generatedBy": "audit_data.py",
        "rawDataReadOnly": True,
        "totalUniqueUsableTrials": len(trial_rows),
        "formal88Cohort": {
            "sessionKeys": ["A", "B1", "B2"],
            "trialCount": 88,
            "note": "A's 30 trials are already part of formal88; do not add them twice.",
        },
        "m6StressCohort": {
            "sessionKey": "S7",
            "trialCount": 30,
            "note": "Separate stress-online session; report separately from the primary formal M6 cohort.",
        },
        "subjectIdentity": "No participant ID is present in source manifests; same-subject status follows the project acquisition history and is not independently verifiable from these files.",
        "timingBoundary": "All event-to-sample times are software-derived estimates. Physical optical timing and hardware sample anchoring are unverified.",
        "sessions": session_summaries,
        "trials": trial_rows,
        "additionalCandidatesExcluded": [
            {
                "candidate": "M6.1a signal-sanity sessions",
                "reason": "Signal/channel diagnostics, not complete randomized three-class formal trial datasets with compatible per-trial onset labels."
            },
            {
                "candidate": "M6.6b live diagnostic sessions",
                "reason": "Engineering diagnostics only; source raw EEG payloads are empty/partial and not a complete waveform trial dataset."
            },
            {
                "candidate": "M6.7 earlier attempts m6_7-formal-20260820T154619Z-a2c03b9a and m6_7-formal-20260820T155314Z-edc04c96",
                "reason": "Incomplete stress-online attempts with channel/continuity failures and no complete technical-valid trial set."
            },
            {
                "candidate": "M6.4 B1 trial 011",
                "reason": "Frozen M6 QC exclusion due stale clock-sync freshness."
            },
            {
                "candidate": "M6.4 B2 trial 023",
                "reason": "Frozen M25 formal-88 boundary; replay-supplemented exploratory trial, excluded from primary formal cohort."
            },
        ],
        "compatibilityAssessment": {
            "sameFrequencyMapping": True,
            "sameNominalSamplingRate": True,
            "sameRawPacketShape": True,
            "sameNominalStimulusDurationSeconds": True,
            "sameSoftwareOnsetConvention": True,
            "sameChannelUsability": False,
            "sameTaskState": False,
            "samePhysicalTimingVerification": False,
            "decision": "A/B1/B2 form the primary 88-trial formal cohort. S7 contributes 30 distinct, technically valid stress-online trials, evaluated as a separate stress holdout with the common channels only; do not pool its accuracy with the primary cohort without an explicit stratified report."
        },
    }

    (output_dir / "data_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    columns = list(trial_rows[0])
    with (output_dir / "data_manifest.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in trial_rows:
            writer.writerow({key: json.dumps(value, separators=(",", ":")) if isinstance(value, (list, dict)) else value for key, value in row.items()})

    counts = {row["sessionKey"]: sum(1 for trial in trial_rows if trial["sessionKey"] == row["sessionKey"]) for row in trial_rows}
    print(json.dumps({"status": "PASS", "usableTrialCount": len(trial_rows), "sessionCounts": counts, "manifest": str(output_dir / "data_manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
