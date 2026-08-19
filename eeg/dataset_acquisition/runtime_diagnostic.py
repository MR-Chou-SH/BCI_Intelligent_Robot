"""No-ND8 runtime diagnostic for the M6.1b Quest dataset controller."""

import argparse
import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from integration.synchronization.trigger_server import TriggerServer

from .plan import generate_trial_plan, write_ground_truth
from .session import PROTOCOL


def utc_now():
    return datetime.now(timezone.utc).isoformat()


async def run(args):
    started = time.monotonic()
    root = Path(args.data_root)
    diagnostic_id = "m6_1b-runtime-diagnostic-{}-{}".format(
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    session = root / diagnostic_id
    session.mkdir(parents=True, exist_ok=False)
    session_id = args.plan_session_id or diagnostic_id
    plan = generate_trial_plan(session_id, args.seed)
    write_ground_truth(session / "trial-ground-truth.jsonl", plan, PROTOCOL)
    summary = {
        "recordType": "m6_1b_runtime_diagnostic", "sessionId": diagnostic_id,
        "planSessionId": session_id,
        "createdUtc": utc_now(), "nd8Used": False, "diagnosticOnly": True,
        "plannedTrialCount": len(plan), "requiredCompletedTrials": args.trial_limit,
        "status": "waiting_for_quest",
    }
    completed_trial_ids = set()
    completion = asyncio.Event()

    def observe(record):
        event = record.get("originalQuestEvent", {})
        if (record.get("validationStatus") == "valid" and
                event.get("eventType") == "stimulus_stopped_software"):
            completed_trial_ids.add(event.get("trialId"))
            if len(completed_trial_ids) >= args.trial_limit:
                completion.set()

    endpoint = TriggerServer(
        session,
        event_observer=observe,
        dataset_plan={"sessionId": session_id, "protocol": PROTOCOL,
                      "trials": [item.to_dict() for item in plan]},
    )
    server = await asyncio.start_server(endpoint.handle_connection, args.host, args.port, limit=1_048_577)
    print("M6DIAG session={} waiting for Quest; no ND8 is active.".format(session), flush=True)
    try:
        async with server:
            await asyncio.wait_for(completion.wait(), timeout=args.timeout_seconds)
        summary["status"] = "passed"
    except asyncio.TimeoutError:
        summary["status"] = "timeout"
    finally:
        summary["finishedUtc"] = utc_now()
        summary["elapsedSeconds"] = time.monotonic() - started
        summary["completedTrialIds"] = sorted(completed_trial_ids)
        summary["completedTrialCount"] = len(completed_trial_ids)
        (session / "runtime-diagnostic-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("M6DIAG status={} completedTrials={} session={}".format(
            summary["status"], len(completed_trial_ids), session), flush=True)
    return 0 if summary["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description="M6.1b Quest runtime diagnostic without ND8")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--seed", default=61, type=int)
    parser.add_argument("--plan-session-id", help="send this existing formal session ID without writing into its directory")
    parser.add_argument("--trial-limit", default=3, type=int, choices=range(1, 4))
    parser.add_argument("--timeout-seconds", default=120.0, type=float)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=11000, type=int)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
