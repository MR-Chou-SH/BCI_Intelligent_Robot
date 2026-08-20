"""M6.6b real-device three-trial smoke runner; never a formal online evaluation."""
import argparse
import asyncio
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.dataset_acquisition.session import PROTOCOL, _quest_sync_ready
from eeg.sample_association.jsonl import AppendOnlyJsonl
from eeg.sample_association.runtime import AssociationCoordinator
from integration.synchronization.trigger_server import TriggerServer
from .live_diagnostic import generate_diagnostic_plan, prepare_manifest
from .live_online import LiveOnlineController
from .pseudo_online import DecoderBackend


def utc_now(): return datetime.now(timezone.utc).isoformat()


def commit():
    try: return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError): return "unavailable"


async def run(args):
    session_id = "m6_6b-live-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    root = Path(args.data_root) / session_id
    manifest = prepare_manifest(root, session_id, commit(), [2, 3, 4, 5, 7])
    plan = generate_diagnostic_plan(session_id)
    protocol = dict(PROTOCOL); protocol["breakAfterTrials"] = []; protocol["breakSeconds"] = 0.0
    plan["protocol"] = protocol
    (root / "diagnostic-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    event_log, result_log = AppendOnlyJsonl(root / "quest-events.jsonl"), AppendOnlyJsonl(root / "trial-results.jsonl")
    predictions, decisions = AppendOnlyJsonl(root / "predictions.jsonl"), AppendOnlyJsonl(root / "decisions.jsonl")
    session_events = AppendOnlyJsonl(root / "session-events.jsonl")
    controller = LiveOnlineController(DecoderBackend("numpy_fbcca"), [2, 3, 4, 5, 7], predictions, decisions)
    results, packet_shapes, errors = [], [], []

    def association_observer(record):
        if not record.get("associationValid"): return
        kind = record.get("stimulusEventType")
        if kind == "stimulus_started_software":
            accepted = controller.start_trial(record)
            session_events.append({"recordType": "live_trial_start", "trialId": record.get("trialId"), "accepted": accepted})
        elif kind == "stimulus_stopped_software":
            result = controller.stop_trial()
            if result is not None:
                result["stopAssociationValid"] = True
                results.append(result); result_log.append(result)

    coordinator = AssociationCoordinator(root / "associations.jsonl", AppendOnlyJsonl(root / "continuity-gate.jsonl"),
                                         association_observer=association_observer)

    def observe_event(record):
        event_log.append(record)
        coordinator.ingest_event(record)

    def observe_live_packet(packet, continuity):
        values = np.asarray(packet.samples, dtype=float)
        packet_shapes.append(tuple(values.shape))
        try: controller.ingest_packet(packet.to_metadata(), continuity, values)
        except Exception as error:
            errors.append("live_controller: {}".format(error))

    adapter = Nd8SerialAdapter(args.com, metadata_log=AppendOnlyJsonl(root / "packet-metadata.jsonl"),
                               raw_packet_log=AppendOnlyJsonl(root / "raw-eeg.jsonl"),
                               packet_observer=coordinator.ingest_packet, live_packet_observer=observe_live_packet)
    endpoint = TriggerServer(root, event_observer=observe_event)
    server = None; started = time.monotonic()
    try:
        adapter.open_port(); adapter.start_streaming()
        server = await asyncio.start_server(endpoint.handle_connection, args.host, args.port, limit=1_048_577)
        deadline = time.monotonic() + args.preflight_timeout_seconds
        while time.monotonic() < deadline:
            packets_ok = bool(packet_shapes) and all(len(shape) == 2 and shape[0] == 8 and shape[1] > 0 for shape in packet_shapes[-5:])
            if (coordinator.gate.ready_pc_monotonic_ns is not None and _quest_sync_ready(endpoint)
                    and bool(endpoint.writers) and packets_ok):
                break
            await asyncio.sleep(0.05)
        else: raise RuntimeError("live preflight did not reach ND8/Quest/association readiness")
        manifest.update({"status": "preflight_passed", "nd8AssociationReady": True, "questSyncReady": True,
                         "packetShape": list(packet_shapes[-1]), "nd8Used": True})
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("M6.6b PREFLIGHT PASS session={}; sending exactly 3 diagnostic trials".format(root), flush=True)
        await endpoint.broadcast_dataset_plan(plan)
        deadline = time.monotonic() + args.session_timeout_seconds
        while len(results) < 3 and time.monotonic() < deadline and not errors:
            await asyncio.sleep(0.1)
        if errors: raise RuntimeError("; ".join(errors))
        if len(results) != 3: raise RuntimeError("expected three completed live trials, observed {}".format(len(results)))
        status = "completed"
    except Exception as error:
        status = "incomplete"; errors.append(str(error))
        print("M6.6b live smoke error: {}".format(error), flush=True)
    finally:
        coordinator.finalize()
        if server is not None: server.close(); await server.wait_closed()
        if adapter.state.value == "streaming": adapter.stop()
        adapter.close()
        summary = {"recordType": "m6_6b_live_smoke", "sessionId": session_id, "status": status,
                   "plannedTrialCount": 3, "completedTrialCount": len(results), "trialResults": results,
                   "packetShapeSamples": [list(shape) for shape in packet_shapes[:5]], "callbackErrors": adapter.callback_errors,
                   "runtimeErrors": errors, "elapsedSeconds": time.monotonic()-started, "groundTruthLeakage": False,
                   "hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                   "hardwareSampleAnchorVerified": False}
        (root / "live-smoke-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest.update({"status": status, "observedPacketCount": len(adapter.timeline.packets), "callbackErrors": adapter.callback_errors})
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if status == "completed" else 1


def main():
    parser = argparse.ArgumentParser(description="M6.6b real ND8 three-trial live smoke test")
    parser.add_argument("--com", required=True, choices=["COM11"])
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--preflight-timeout-seconds", default=60.0, type=float)
    parser.add_argument("--session-timeout-seconds", default=90.0, type=float)
    parser.add_argument("--host", default="0.0.0.0"); parser.add_argument("--port", default=11000, type=int)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__": main()
