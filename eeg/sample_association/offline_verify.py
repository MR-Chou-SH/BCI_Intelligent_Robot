"""Independent M5.3 recomputation from raw Quest-PC and ND8 evidence files."""

import argparse
import json
from pathlib import Path

from eeg.sample_association.models import EegPacketMetadata, PacketContinuityRecord
from integration.synchronization.clock_sync import AffineClockMapper

from .runtime import AssociationCoordinator


class MemoryLog:
    def __init__(self): self.records = []
    def append(self, record): self.records.append(record)


def read_jsonl(path):
    values, errors = [], []
    if not path.exists(): return values, ["missing:" + str(path.name)]
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try: values.append(json.loads(line))
        except json.JSONDecodeError: errors.append("malformed:{}:{}".format(path.name, number))
    return values, errors


def mapper_for_event(sync_records, connection_id, event_receive_ns):
    mapper = AffineClockMapper()
    latest = None
    for record in sync_records:
        if record.get("recordType") != "clock_sync_sample" or record.get("connectionId") != connection_id:
            continue
        if record.get("pcResultReceiveMonotonicNs", 0) > event_receive_ns:
            continue
        if not record.get("sampleAcceptedForAffineFit"):
            continue
        raw = record.get("rawSample", {})
        try:
            mapper.add((float(raw["q1QuestMonotonicSeconds"]) + float(raw["q4QuestMonotonicSeconds"])) / 2.0,
                       (int(raw["p2PcReceiveMonotonicNs"]) + int(raw["p3PcSendMonotonicNs"])) / 2e9)
            latest = int(record["pcResultReceiveMonotonicNs"])
        except (KeyError, TypeError, ValueError):
            continue
    return mapper, latest


def verify_session(session):
    session = Path(session)
    metadata_records, errors = read_jsonl(session / "packet-metadata.jsonl")
    events, event_errors = read_jsonl(session / "pc-stimulus-events.jsonl")
    sync, sync_errors = read_jsonl(session / "pc-synchronization.jsonl")
    live, live_errors = read_jsonl(session / "derived-association.jsonl")
    errors += event_errors + sync_errors + live_errors
    derived = MemoryLog(); gates = MemoryLog(); coordinator = AssociationCoordinator(derived, gates)
    for record in metadata_records:
        try:
            packet = EegPacketMetadata(**record["packet"])
            raw_continuity = record["continuity"]
            continuity = PacketContinuityRecord(raw_continuity.get("packet_sequence"), raw_continuity["cumulative_first_sample_index"],
                                                raw_continuity["status"], tuple(raw_continuity.get("issues", [])))
            coordinator.ingest_packet(packet, continuity)
        except (KeyError, TypeError, ValueError) as error:
            errors.append("invalid_packet_metadata:" + str(error))
    ready_times = [r[0].pc_receive_monotonic_ns for r in coordinator.packets if r[2].association_ready]
    if ready_times:
        coordinator.gate.ready_pc_monotonic_ns = min(ready_times)
    for event in events:
        if event.get("recordType") != "stimulus_event_received": continue
        quest = event.get("originalQuestEvent", {})
        mapper, latest = mapper_for_event(sync, event.get("connectionId"), event.get("pcReceiveMonotonicNs", 0))
        estimate = mapper.map(quest.get("questMonotonicSeconds", -1)) if isinstance(quest.get("questMonotonicSeconds"), (int, float)) else None
        replay = dict(event)
        replay["estimatedPcEventMonotonicNs"] = int(estimate * 1e9) if estimate is not None else None
        replay["clockSync"] = {"status": "ready" if estimate is not None else "unavailable", "acceptedSampleCount": mapper.sample_count,
                                "affineResidualRmsSeconds": mapper.residual_rms_seconds(), "latestAcceptedPcMonotonicNs": latest,
                                "clockIsSoftwareOnly": True}
        coordinator.ingest_event(replay)
    coordinator.finalize()
    comparable_live = {(r.get("sessionId"), r.get("trialId"), r.get("stimulusSequence")): r for r in live}
    mismatches = []
    for record in derived.records:
        key = (record.get("sessionId"), record.get("trialId"), record.get("stimulusSequence"))
        previous = comparable_live.get(key)
        if previous is None or previous.get("associationValid") != record.get("associationValid") or previous.get("associatedPacketSequence") != record.get("associatedPacketSequence") or previous.get("estimatedSampleOffset") != record.get("estimatedSampleOffset"):
            mismatches.append(key)
    stimulus_records = [record for record in derived.records
                        if record.get("stimulusEventType") in ("stimulus_started_software", "stimulus_stopped_software")]
    valid_stimulus_records = [record for record in stimulus_records if record.get("associationValid")]
    complete_valid_stimulus_association = bool(stimulus_records) and len(valid_stimulus_records) == len(stimulus_records)
    return {"recordType": "m5_3_offline_association_verification", "session": str(session),
            "rawEvidenceErrors": errors, "packetMetadataCount": len(metadata_records), "pcEventCount": len(events),
            "offlineAssociationCount": len(derived.records), "liveAssociationCount": len(live),
            "stimulusAssociationCount": len(stimulus_records),
            "validStimulusAssociationCount": len(valid_stimulus_records),
            "completeValidStimulusAssociation": complete_valid_stimulus_association,
            "liveOfflineMismatchKeys": mismatches,
            "passed": not errors and not mismatches and complete_valid_stimulus_association,
            "hardwareTimingVerified": False, "physicalOpticalTimingVerified": False}, derived.records


def main():
    parser = argparse.ArgumentParser(description="Independent M5.3 raw-evidence association verification")
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output-name", default="offline-association-verification.json")
    args = parser.parse_args()
    summary, records = verify_session(args.session)
    output = args.session / args.output_name
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2); stream.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
