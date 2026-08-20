"""Quest-only validation of the explicit M6.6b three-trial plan; no ND8 access."""
import argparse
import asyncio
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from eeg.sample_association.jsonl import AppendOnlyJsonl
from eeg.dataset_acquisition.session import PROTOCOL
from integration.synchronization.trigger_server import TriggerServer
from .live_diagnostic import generate_diagnostic_plan, prepare_manifest


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


async def run(args):
    session_id = "m6_6b-protocol-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    root = Path(args.data_root) / session_id
    manifest = prepare_manifest(root, session_id, git_commit(), [2, 3, 4, 5, 7])
    protocol = dict(PROTOCOL)
    protocol["breakAfterTrials"] = []
    protocol["breakSeconds"] = 0.0
    plan = generate_diagnostic_plan(session_id)
    plan["protocol"] = protocol
    (root / "diagnostic-plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    event_log = AppendOnlyJsonl(root / "quest-events.jsonl")
    completion, completed = asyncio.Event(), set()

    def observe(record):
        event_log.append(record)
        event = record.get("originalQuestEvent") or {}
        if record.get("validationStatus") == "valid" and event.get("eventType") == "stimulus_stopped_software":
            completed.add(event.get("trialId"))
            if len(completed) == 3:
                completion.set()

    endpoint = TriggerServer(root, event_observer=observe, dataset_plan=plan)
    server = await asyncio.start_server(endpoint.handle_connection, args.host, args.port, limit=1_048_577)
    started = time.monotonic()
    print("M6.6b Quest-only protocol validation listening; session={}".format(root), flush=True)
    try:
        async with server:
            await asyncio.wait_for(completion.wait(), timeout=args.timeout_seconds)
        status = "passed"
    except asyncio.TimeoutError:
        status = "timeout"
    finally:
        summary = {"recordType": "m6_6b_quest_protocol_diagnostic", "sessionId": session_id,
                   "createdUtc": utc_now(), "status": status, "nd8Used": False,
                   "planMode": "diagnostic_live", "plannedTrialCount": 3,
                   "completedTrialIds": sorted(completed), "completedTrialCount": len(completed),
                   "elapsedSeconds": time.monotonic() - started,
                   "groundTruthLeakage": False, "hardwareTimingVerified": False,
                   "physicalOpticalTimingVerified": False}
        (root / "protocol-validation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest["status"] = status
        manifest["nd8Used"] = False
        (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("M6.6b Quest-only protocol validation status={} completed={}/3".format(status, len(completed)), flush=True)
    return 0 if status == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description="M6.6b Quest-only three-trial protocol validation")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--timeout-seconds", default=90.0, type=float)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=11000, type=int)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
