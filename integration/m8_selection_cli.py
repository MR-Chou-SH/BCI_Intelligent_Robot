"""M8.2a PC entry point for replay/mock M6 final decisions.

The live-ND8 mode deliberately fails closed in M8.2a: it is reserved for the
separately scoped M8.2b source wiring and never opens an ND8 device here.
"""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eeg.sample_association.jsonl import AppendOnlyJsonl
from integration.m8_selection_orchestration import M8SelectionOrchestrator, QuestSelectionTcpServer


def _load_replay_records(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_records(orchestrator, records, selection_id_prefix):
    results = []
    for index, decision in enumerate(records):
        trial_id = decision.get("trialId")
        if not trial_id:
            raise ValueError("replay final-decision record is missing trialId")
        selection_id = "{}-{:03d}".format(selection_id_prefix, index)
        if not orchestrator.open_selection(selection_id, trial_id):
            results.append({"selectionId": selection_id, "trialId": trial_id, "status": "open_rejected"})
            continue
        results.append(orchestrator.submit_final_decision(decision))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="M8.2a M6 final-decision to Quest selection orchestration")
    parser.add_argument("--mode", choices=("mock", "replay", "live-nd8"), default="mock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=11001, type=int)
    parser.add_argument("--accept-timeout-seconds", default=30.0, type=float)
    parser.add_argument("--ack-timeout-seconds", default=5.0, type=float)
    parser.add_argument("--event-log", required=True, type=Path)
    parser.add_argument("--selection-id-prefix", required=True)
    parser.add_argument("--trial-id")
    parser.add_argument("--final-label", choices=("target_left", "target_center", "target_right"))
    parser.add_argument("--no-decision", action="store_true")
    parser.add_argument("--replay-final-decisions", type=Path)
    args = parser.parse_args(argv)

    if args.mode == "live-nd8":
        parser.error("live-nd8 is reserved for M8.2b; M8.2a must not open ND8 hardware")
    if args.mode == "mock" and (not args.trial_id or (not args.final_label and not args.no_decision)):
        parser.error("mock mode requires --trial-id and exactly one of --final-label or --no-decision")
    if args.mode == "mock" and args.final_label and args.no_decision:
        parser.error("--final-label and --no-decision are mutually exclusive")
    if args.mode == "replay" and args.replay_final_decisions is None:
        parser.error("replay mode requires --replay-final-decisions")

    event_log = AppendOnlyJsonl(args.event_log)
    with QuestSelectionTcpServer(args.host, args.port, args.accept_timeout_seconds, args.ack_timeout_seconds) as transport:
        print("M8.2a listener={} port={}".format(args.host, transport.port), flush=True)
        orchestrator = M8SelectionOrchestrator(transport, event_log.append)
        if args.mode == "mock":
            records = [{
                "trialId": args.trial_id,
                "decisionMade": not args.no_decision,
                "finalDecisionLabel": args.final_label,
                "stabilizer": "2-Consecutive",
                "reason": "mock_no_decision" if args.no_decision else "fixed_consecutive_run",
            }]
        else:
            records = _load_replay_records(args.replay_final_decisions)
        results = _run_records(orchestrator, records, args.selection_id_prefix)

    for result in results:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if all(item.get("status") in ("quest_accepted", "no_decision") for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
