from .models import EpochAssociationRecord


def associate_m5_event(pc_event_record, mapper, epoch_start_seconds=0.0, epoch_end_seconds=3.0):
    """Consume M5.2 PC log records without changing their Quest-originated payload."""
    if not isinstance(pc_event_record, dict):
        raise ValueError("PC event record must be an object")
    event = pc_event_record.get("originalQuestEvent")
    pc_time = pc_event_record.get("estimatedPcEventMonotonicNs")
    if not isinstance(event, dict) or not event.get("sessionId") or "sequence" not in event:
        raise ValueError("incomplete M5.2 event record")
    association = mapper.map_pc_event(pc_time) if isinstance(pc_time, int) else mapper.map_pc_event(-1)
    return EpochAssociationRecord(event["sessionId"], event.get("trialId", ""), event.get("eventType", ""),
                                  event["sequence"], pc_time if isinstance(pc_time, int) else -1,
                                  float(epoch_start_seconds), float(epoch_end_seconds), association, event)
