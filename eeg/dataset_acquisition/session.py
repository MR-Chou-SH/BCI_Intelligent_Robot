"""M6.1b continuous ND8/M5 evidence session launcher; no decoder."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.sample_association.jsonl import AppendOnlyJsonl
from eeg.sample_association.runtime import AssociationCoordinator
from integration.synchronization.trigger_server import TriggerServer

from .plan import GENERATOR_VERSION, generate_trial_plan, write_ground_truth


PROTOCOL = {"preparationSeconds": 13.0, "cueSeconds": 2.0, "preStimulusRestSeconds": 1.0,
            "stimulusSeconds": 4.0, "postStimulusRestSeconds": 2.0, "breakAfterTrials": [10, 20],
            "breakSeconds": 25.0, "rawWindow": "complete_formal_stimulation_context"}


def _git_runtime_evidence():
    repository = Path(__file__).resolve().parents[2]
    try:
        status = subprocess.check_output(["git", "status", "--porcelain=v1"], cwd=repository, text=True)
        changed_files = [line[3:] for line in status.splitlines() if len(line) >= 4]
        tracked_patch = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=repository)
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=repository, text=True).splitlines()
        dirty = bool(changed_files)
        return {
            "dirtyWorktree": dirty,
            "dirtyWorktreeChangedFiles": changed_files,
            "dirtyWorktreeUntrackedFiles": untracked,
            "trackedDiffSha256": hashlib.sha256(tracked_patch).hexdigest(),
            "runtimeEvidenceNote": (
                "dataset collected from dirty working tree containing validated M6 runtime fix"
                if dirty else "dataset collected from clean working tree"),
        }
    except (OSError, subprocess.CalledProcessError) as error:
        return {"dirtyWorktree": "unavailable", "runtimeEvidenceError": str(error)}


def make_session(root, seed):
    session_id = "m6_1b-dataset-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    session = Path(root) / session_id
    session.mkdir(parents=True, exist_ok=False)
    plan = generate_trial_plan(session_id, seed)
    write_ground_truth(session / "trial-ground-truth.jsonl", plan, PROTOCOL)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unavailable"
    manifest = {"recordType": "m6_1b_dataset_session", "sessionId": session_id,
                "createdUtc": datetime.now(timezone.utc).isoformat(), "experiment": "M6.1b",
                "gitCommit": commit, "randomSeed": int(seed), "generatorVersion": GENERATOR_VERSION,
                "trialCount": 30, "classBalance": {"target_left": 10, "target_center": 10, "target_right": 10},
                "nominalStimulusFrequenciesHz": {"target_left": 7.2, "target_center": 9.0, "target_right": 12.0},
                "protocol": PROTOCOL, "hardwareTimingVerified": False, "physicalOpticalTimingVerified": False,
                "sampleAnchor": "unverified", "status": "preparing", "rawEegFile": "raw-eeg-packets.jsonl",
                "packetMetadataFile": "packet-metadata.jsonl", "gateEvidenceFile": "nd8-association-gate.jsonl",
                "trialGroundTruthFile": "trial-ground-truth.jsonl"}
    manifest.update(_git_runtime_evidence())
    _write_manifest(session, manifest)
    return session, manifest, plan


def _write_manifest(session, manifest):
    (session / "session-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_prepared_session(session):
    session = Path(session)
    manifest = json.loads((session / "session-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "preparing":
        raise ValueError("prepared session must have status=preparing")
    with (session / "trial-ground-truth.jsonl").open(encoding="utf-8") as stream:
        plan = [json.loads(line) for line in stream if line.strip()]
    if manifest.get("sessionId") != session.name or len(plan) != 30:
        raise ValueError("prepared session manifest or ground truth is invalid")
    if any(item.get("sessionId") != manifest["sessionId"] for item in plan):
        raise ValueError("ground truth session IDs do not match manifest")
    return session, manifest, plan


def _quest_sync_ready(endpoint):
    return any(mapper.coefficients() is not None and
               endpoint.sync_state.get(connection_id, {}).get("latestAcceptedPcMonotonicNs") is not None
               for connection_id, mapper in endpoint.mappers.items())


async def run_session(args):
    if args.session is not None:
        session, manifest, plan = load_prepared_session(args.session)
    else:
        session, manifest, plan = make_session(args.data_root, args.seed)
    gate_log = AppendOnlyJsonl(session / "nd8-association-gate.jsonl")
    coordinator = AssociationCoordinator(session / "derived-association.jsonl", gate_log)
    adapter = Nd8SerialAdapter(args.com, metadata_log=AppendOnlyJsonl(session / "packet-metadata.jsonl"),
                               raw_packet_log=AppendOnlyJsonl(session / "raw-eeg-packets.jsonl"),
                               packet_observer=coordinator.ingest_packet)
    endpoint = TriggerServer(
        session,
        event_observer=coordinator.ingest_event,
        dataset_plan={"sessionId": manifest["sessionId"], "protocol": PROTOCOL,
                      "trials": [item.to_dict() if hasattr(item, "to_dict") else item for item in plan]},
    )
    event_log = AppendOnlyJsonl(session / "session-events.jsonl")
    started = time.monotonic()
    try:
        adapter.open_port()
        adapter.start_streaming()
        server = await asyncio.start_server(endpoint.handle_connection, args.host, args.port, limit=1_048_577)
        async with server:
            deadline = time.monotonic() + 60.0
            while (coordinator.gate.ready_pc_monotonic_ns is None or not _quest_sync_ready(endpoint)) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if coordinator.gate.ready_pc_monotonic_ns is None:
                raise RuntimeError("ND8 association_ready was not reached before dataset preparation")
            if not _quest_sync_ready(endpoint):
                raise RuntimeError("Quest-PC affine synchronization was not established before dataset preparation")
            event_log.append({"recordType": "dataset_preparation_started", "createdUtc": datetime.now(timezone.utc).isoformat(),
                              "preparationSeconds": PROTOCOL["preparationSeconds"], "nd8AssociationReady": True,
                              "questPcServerStartedBeforePreparation": True})
            manifest["nd8AssociationReadyBeforePreparation"] = True
            manifest["questPcServerStartedBeforePreparation"] = True
            manifest["formalPreparationSeconds"] = PROTOCOL["preparationSeconds"]
            _write_manifest(session, manifest)
            print("M6.1b session={} plan ready; ND8 association_ready. Start the approved Quest dataset controller during the 13 s preparation interval.".format(session), flush=True)
            await asyncio.sleep(PROTOCOL["preparationSeconds"])
            event_log.append({"recordType": "dataset_formal_acquisition_started", "createdUtc": datetime.now(timezone.utc).isoformat(),
                              "formalAnalysisExcludesPreparation": True})
            manifest["status"] = "acquiring"
            _write_manifest(session, manifest)
            print("M6.1b formal acquisition window open; ground truth is fixed in trial-ground-truth.jsonl.", flush=True)
            await server.serve_forever()
    except KeyboardInterrupt:
        manifest["status"] = "incomplete"
        manifest["abortReason"] = "user_interrupt"
    except Exception as error:
        manifest["status"] = "incomplete"
        manifest["abortReason"] = str(error)
        raise
    finally:
        coordinator.finalize()
        if adapter.state.value == "streaming":
            adapter.stop()
        adapter.close()
        manifest["elapsedSeconds"] = time.monotonic() - started
        manifest.setdefault("status", "incomplete")
        manifest["observedPacketCount"] = len(adapter.timeline.packets)
        manifest["callbackErrors"] = list(adapter.callback_errors)
        _write_manifest(session, manifest)
        print("M6.1b session finalized: {}".format(session), flush=True)


def main():
    parser = argparse.ArgumentParser(description="M6.1b continuous ND8/M5 dataset evidence session")
    parser.add_argument("--com")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--session", type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=11000, type=int)
    args = parser.parse_args()
    if args.prepare_only:
        if args.data_root is None or args.seed is None or args.session is not None:
            parser.error("--prepare-only requires --data-root and --seed, not --session")
        session, _, _ = make_session(args.data_root, args.seed)
        print(session, flush=True)
        return
    if args.com is None or args.com.upper() != "COM11":
        parser.error("M6.1b dataset session is restricted to the verified COM11 configuration")
    if args.session is None and (args.data_root is None or args.seed is None):
        parser.error("run requires --session or both --data-root and --seed")
    try:
        asyncio.run(run_session(args))
    except KeyboardInterrupt:
        print("M6.1b session stopped by user", flush=True)


if __name__ == "__main__":
    main()
