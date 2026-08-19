"""Independent structural verifier for M6.1b dataset evidence."""

import json
from collections import Counter
from pathlib import Path

from .plan import TARGETS


def _read_jsonl(path):
    records, errors = [], []
    if not path.exists():
        return records, ["missing_file:{}".format(path.name)]
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                errors.append("malformed_json:{}:{}".format(path.name, line_number))
    return records, errors


def _trial_events(events):
    grouped = {}
    for record in events:
        event = record.get("originalQuestEvent", record) if isinstance(record, dict) else {}
        trial_id = event.get("trialId")
        if trial_id:
            grouped.setdefault(trial_id, []).append(event.get("eventType"))
    return grouped


def verify_session(session):
    session = Path(session)
    errors = []
    warnings = []
    try:
        manifest = json.loads((session / "session-manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = {}
        errors.append("missing_or_malformed_session_manifest")
    truth, truth_errors = _read_jsonl(session / "trial-ground-truth.jsonl")
    events, event_errors = _read_jsonl(session / "pc-stimulus-events.jsonl")
    metadata, metadata_errors = _read_jsonl(session / "packet-metadata.jsonl")
    associations, association_errors = _read_jsonl(session / "derived-association.jsonl")
    raw_packets, raw_errors = _read_jsonl(session / "raw-eeg-packets.jsonl")
    gates, gate_errors = _read_jsonl(session / "nd8-association-gate.jsonl")
    errors.extend(truth_errors + event_errors + metadata_errors + association_errors + raw_errors + gate_errors)

    trial_ids = [item.get("trialId") for item in truth if isinstance(item, dict)]
    labels = [item.get("targetId") for item in truth if isinstance(item, dict)]
    counts = Counter(labels)
    expected = {target: 10 for target in TARGETS}
    if len(truth) != 30:
        errors.append("expected_30_ground_truth_trials")
    if len(set(trial_ids)) != len(trial_ids):
        errors.append("duplicate_trial_ids")
    if counts != Counter(expected):
        errors.append("class_balance_not_10_10_10")
    if any(label not in TARGETS for label in labels):
        errors.append("illegal_target_label")
    if not truth or not all(item.get("randomSeed") is not None for item in truth if isinstance(item, dict)):
        errors.append("ground_truth_seed_missing")
    if manifest.get("randomSeed") is None:
        errors.append("manifest_random_seed_missing")

    event_groups = _trial_events(events)
    association_groups = {}
    for record in associations:
        association_groups.setdefault(record.get("trialId"), []).append(record)
    complete_trial_ids = set()
    for trial_id in trial_ids:
        kinds = event_groups.get(trial_id, [])
        if kinds.count("stimulus_started_software") != 1 or kinds.count("stimulus_stopped_software") != 1:
            errors.append("trial_event_pair_invalid:{}".format(trial_id))
        elif kinds.index("stimulus_started_software") > kinds.index("stimulus_stopped_software"):
            errors.append("trial_event_order_invalid:{}".format(trial_id))
        records = association_groups.get(trial_id, [])
        association_types = {record.get("stimulusEventType") for record in records}
        if not {"stimulus_started_software", "stimulus_stopped_software"}.issubset(association_types):
            errors.append("trial_association_event_types_incomplete:{}".format(trial_id))
        valid = [record for record in records if record.get("associationValid") is True]
        if len(valid) < 2:
            errors.append("trial_association_incomplete:{}".format(trial_id))
        elif all(record.get("hardwareTimingVerified") is False and record.get("sampleIndexKind") == "software_derived_estimate" for record in valid):
            complete_trial_ids.add(trial_id)
        else:
            warnings.append("trial_timing_boundary_unexpected:{}".format(trial_id))

    if not (session / "raw-eeg-packets.jsonl").is_file():
        errors.append("raw_eeg_file_missing")
    if not metadata:
        errors.append("packet_metadata_missing_or_empty")
    if not (session / "nd8-association-gate.jsonl").is_file():
        errors.append("gate_evidence_file_missing")
    sequences = [record.get("packetSequence") for record in raw_packets]
    if any(not isinstance(sequence, int) for sequence in sequences):
        errors.append("raw_packet_sequence_missing")
    elif sequences and sequences != list(range(sequences[0], sequences[0] + len(sequences))):
        errors.append("raw_packet_chronology_invalid")
    if any(record.get("gate", {}).get("state") == "continuity_lost" for record in gates):
        errors.append("nd8_continuity_lost")
    status = "complete" if not errors and len(complete_trial_ids) == 30 else ("complete_with_warnings" if not errors else "incomplete")
    result = {"recordType": "m6_1b_dataset_completeness", "session": str(session), "status": status,
              "expectedTrialCount": 30, "groundTruthTrialCount": len(truth), "classCounts": dict(counts),
              "eventTrialCount": len(event_groups), "completeTrialCount": len(complete_trial_ids),
              "rawEvidencePresent": (session / "raw-eeg-packets.jsonl").is_file(),
              "packetMetadataCount": len(metadata), "rawPacketCount": len(raw_packets), "errors": errors, "warnings": warnings,
              "classificationPerformed": False,
              "timingEvidenceBoundary": {"hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                                          "sampleAnchor": "software-derived estimate; unverified hardware anchor"}}
    (session / "dataset-completeness.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result
