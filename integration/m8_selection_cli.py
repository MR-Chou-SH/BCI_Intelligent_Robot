"""M8 PC entry point for mock/replay and separately scoped M8.2b live ND8 runs."""
import argparse
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eeg.sample_association.jsonl import AppendOnlyJsonl
from integration.m8_live_nd8 import run_live_nd8
from integration.m8_selection_orchestration import M8SelectionOrchestrator, QuestSelectionTcpServer
from integration.m8_selection_transport.simulated_batch_consumer import consume_one_batch


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


def _record_final_batch_delivery(session_root, receipt):
    """Preserve one final single-trial batch receipt beside its live-ND8 evidence."""
    record = {
        "recordType": "m8_final_single_trial_batch_delivery",
        "status": "acknowledged",
        "payload": receipt.payload,
        "batch": receipt.batch,
        "downstreamAccepted": receipt.downstream_accepted,
        "ack": receipt.ack,
    }
    root = Path(session_root)
    (root / "m8-final-batch-delivery.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["finalBatchDelivery"] = {
        "status": "acknowledged",
        "batchId": receipt.batch["batchId"],
        "downstreamAccepted": receipt.downstream_accepted,
        "ackBatchId": receipt.ack["batchId"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description="M8 M6 final-decision to Quest selection orchestration")
    parser.add_argument("--mode", choices=("mock", "replay", "live-nd8"), default="mock")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=11001, type=int)
    parser.add_argument("--accept-timeout-seconds", default=30.0, type=float)
    parser.add_argument("--ack-timeout-seconds", default=5.0, type=float)
    parser.add_argument("--event-log", type=Path)
    parser.add_argument("--selection-id-prefix")
    parser.add_argument("--trial-id")
    parser.add_argument("--final-label", choices=("target_left", "target_center", "target_right"))
    parser.add_argument("--no-decision", action="store_true")
    parser.add_argument("--replay-final-decisions", type=Path)
    parser.add_argument("--com", choices=("COM11",))
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--session-prefix", default="m8_2b-live")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight-timeout-seconds", default=150.0, type=float)
    parser.add_argument("--packet-stall-seconds", default=2.0, type=float)
    parser.add_argument("--selection-plan", default="fixed", choices=("fixed", "free"),
                        help="fixed is the verified slot order; free forwards each decoder class without an expected class")
    parser.add_argument("--max-trials", default=3, type=int, choices=(1, 2, 3),
                        help="default frozen three-trial protocol; 1 or 2 enables a shortened demonstration")
    parser.add_argument("--batch-consumer-timeout-seconds", default=45.0, type=float,
                        help="single-trial only: wait on released TCP 11001 for the confirmed batch")
    parser.set_defaults(preparation_seconds=13.0, trial_window_seconds=4.0)
    args = parser.parse_args(argv)

    if args.mode == "live-nd8":
        if args.data_root is None:
            parser.error("live-nd8 requires --data-root under the external EEG study root")
        if args.com is None:
            parser.error("live-nd8 requires the verified --com COM11 configuration")
        if args.dry_run and args.preflight_only:
            parser.error("--dry-run and --preflight-only are mutually exclusive")
        exit_code, session_root = run_live_nd8(args)
        if (exit_code == 0 and (args.selection_plan == "free" or args.max_trials in (1, 2)) and
                not args.dry_run and not args.preflight_only):
            print("M8 final {}-trial {} run complete; TCP {} released; waiting for confirmed batch on {}:{}".format(
                args.max_trials, args.selection_plan, args.port, args.host, args.port), flush=True)
            try:
                receipt = consume_one_batch(args.host, args.port, args.batch_consumer_timeout_seconds)
            except (OSError, RuntimeError, TimeoutError, ValueError, json.JSONDecodeError) as error:
                print("M8 final batch consumer failed closed: {}".format(error), flush=True)
                return 2
            record = _record_final_batch_delivery(session_root, receipt)
            print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)
        return exit_code
    if (args.dry_run or args.preflight_only or args.com or args.data_root or
            args.selection_plan != "fixed" or args.max_trials != 3):
        parser.error("live-nd8-only arguments require --mode live-nd8")
    if args.event_log is None or not args.selection_id_prefix:
        parser.error("mock/replay mode requires --event-log and --selection-id-prefix")
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
