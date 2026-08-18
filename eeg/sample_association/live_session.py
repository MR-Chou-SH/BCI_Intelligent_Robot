"""One explicit M5.3 Quest-PC-ND8 recording session (no decoding)."""

import argparse
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from integration.synchronization.trigger_server import TriggerServer

from .jsonl import AppendOnlyJsonl
from .runtime import AssociationCoordinator


def make_session(root):
    session = Path(root) / ("m5_3-association-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8]))
    session.mkdir(parents=True, exist_ok=False)
    return session


async def run_session(args):
    session = make_session(args.data_root)
    gate_log = AppendOnlyJsonl(session / "nd8-association-gate.jsonl")
    coordinator = AssociationCoordinator(session / "derived-association.jsonl", gate_log)
    adapter = Nd8SerialAdapter(args.com, metadata_log=AppendOnlyJsonl(session / "packet-metadata.jsonl"),
                               raw_packet_log=AppendOnlyJsonl(session / "raw-eeg-packets.jsonl"),
                               packet_observer=coordinator.ingest_packet)
    endpoint = TriggerServer(session, event_observer=coordinator.ingest_event)
    manifest = {"recordType": "m5_3_association_session", "createdUtc": datetime.now(timezone.utc).isoformat(),
                "comPort": args.com, "samplingRateHz": 1000, "rawEegFile": "raw-eeg-packets.jsonl",
                "packetMetadataFile": "packet-metadata.jsonl", "questPcEventsFile": "pc-stimulus-events.jsonl",
                "questPcSyncFile": "pc-synchronization.jsonl", "derivedAssociationFile": "derived-association.jsonl",
                "hardwareTimingVerified": False, "physicalOpticalTimingVerified": False}
    with (session / "session-manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    adapter.open_port()
    adapter.start_streaming()
    server = await asyncio.start_server(endpoint.handle_connection, args.host, args.port, limit=1_048_577)
    print("M5.3 session={} ND8 streaming; wait for nd8-association-gate.jsonl association_ready before launching a trial.".format(session), flush=True)
    try:
        async with server:
            await server.serve_forever()
    finally:
        coordinator.finalize()
        if adapter.state.value == "streaming":
            adapter.stop()
        adapter.close()


def main():
    parser = argparse.ArgumentParser(description="M5.3 live Quest-PC-ND8 association session")
    parser.add_argument("--com", required=True)
    parser.add_argument("--data-root", required=True, type=Path,
                        help="external experiment-data directory; never a repository path")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=11000, type=int)
    args = parser.parse_args()
    if args.com.upper() != "COM11":
        parser.error("M5.3 live session is restricted to the verified COM11 configuration")
    try:
        asyncio.run(run_session(args))
    except KeyboardInterrupt:
        print("M5.3 session stopped by user", flush=True)


if __name__ == "__main__":
    main()
