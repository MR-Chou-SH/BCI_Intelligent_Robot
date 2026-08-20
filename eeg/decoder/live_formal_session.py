"""M6.7 direct 30-trial formal session with a pre-plan 60 s quality gate."""
import argparse, asyncio, json, subprocess, time, uuid
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
from eeg.acquisition.nd8_serial_adapter import Nd8SerialAdapter
from eeg.dataset_acquisition.session import PROTOCOL, _quest_sync_ready
from eeg.sample_association.jsonl import AppendOnlyJsonl
from eeg.sample_association.runtime import AssociationCoordinator
from integration.synchronization.trigger_server import TriggerServer
from .formal_online import channel_admission, synthetic_warmup, generate_online_plan, technical_validity, evaluate_formal, early_technical_checkpoint
from .live_diagnostic import prepare_manifest
from .live_online import LiveOnlineController
from .pseudo_online import DecoderBackend

def commit():
    try: return subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip()
    except Exception: return "unavailable"

async def run(args):
    sid="m6_7-formal-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),uuid.uuid4().hex[:8]); root=Path(args.data_root)/sid
    plan=generate_online_plan(sid,args.seed,"formal_online"); plan["protocol"]=dict(PROTOCOL)
    manifest=prepare_manifest(root,sid,commit(),[]); manifest.update({"mode":"formal_online","randomSeed":args.seed,"plannedTrialCount":30,"status":"preflight","groundTruthLeakage":False})
    (root/"formal-plan.json").write_text(json.dumps(plan,indent=2)+"\n",encoding="utf-8")
    events, results_log, predictions, decisions, session_events=(AppendOnlyJsonl(root/"quest-events.jsonl"),AppendOnlyJsonl(root/"trial-results.jsonl"),AppendOnlyJsonl(root/"predictions.jsonl"),AppendOnlyJsonl(root/"decisions.jsonl"),AppendOnlyJsonl(root/"session-events.jsonl"))
    packets=[]; continuity=[]; shapes=[]; results=[]; errors=[]; controller=None; checkpoint=None; selected=[]; abort_requested=False
    def association(record):
        nonlocal controller
        if controller is None or not record.get("associationValid"): return
        if record.get("stimulusEventType")=="stimulus_started_software":
            ok=controller.start_trial(record); session_events.append({"recordType":"trial_start","trialId":record.get("trialId"),"accepted":ok})
        elif record.get("stimulusEventType")=="stimulus_stopped_software":
            result=controller.stop_trial()
            if result is not None:
                result.update({"startAssociationValid":True,"stopAssociationValid":True}); results.append(result); results_log.append(result)
    coordinator=AssociationCoordinator(root/"associations.jsonl",AppendOnlyJsonl(root/"continuity-gate.jsonl"),association_observer=association)
    def event(record): events.append(record); coordinator.ingest_event(record)
    def live(packet,status):
        nonlocal abort_requested
        values=np.asarray(packet.samples,dtype=float); shapes.append(tuple(values.shape))
        if sum(x.shape[1] for x in packets)<60000: packets.append(values.copy()); continuity.append(status.status)
        if controller is not None:
            if status.status not in ("continuous","anomaly") or not np.isfinite(values[selected]).all() or any(np.mean(np.isclose(np.abs(values[c]),375000,atol=1.0))>=.95 for c in selected):
                abort_requested=True; errors.append("frozen_channel_or_continuity_failure")
                return
            try: controller.ingest_packet(packet.to_metadata(),status,values)
            except Exception as exc: errors.append("decoder_exception:{}".format(exc))
    adapter=Nd8SerialAdapter(args.com,metadata_log=AppendOnlyJsonl(root/"packet-metadata.jsonl"),raw_packet_log=AppendOnlyJsonl(root/"raw-eeg.jsonl"),packet_observer=coordinator.ingest_packet,live_packet_observer=live)
    endpoint=TriggerServer(root,event_observer=event); server=None; status="incomplete"; start=time.monotonic()
    try:
        adapter.open_port(); adapter.start_streaming(); server=await asyncio.start_server(endpoint.handle_connection,args.host,args.port,limit=1048577)
        deadline=time.monotonic()+args.preflight_timeout_seconds
        while time.monotonic()<deadline:
            sample_count=sum(x.shape[1] for x in packets); good=bool(shapes) and all(s[0]==8 and s[1]>0 for s in shapes[-5:])
            if sample_count>=60000 and good and endpoint.writers and _quest_sync_ready(endpoint) and coordinator.gate.ready_pc_monotonic_ns is not None: break
            await asyncio.sleep(.05)
        else: raise RuntimeError("formal preflight timeout")
        admission=channel_admission(np.concatenate(packets,axis=1)[:,:60000],continuity,commit(),datetime.now(timezone.utc).isoformat())
        (root/"channel-admission.json").write_text(json.dumps(admission,indent=2)+"\n",encoding="utf-8")
        if admission["verdict"]!="READY": raise RuntimeError("channel admission failed")
        selected=admission["selectedChannels"]; warmup=synthetic_warmup(selected); (root/"synthetic-warmup.json").write_text(json.dumps(warmup,indent=2)+"\n",encoding="utf-8")
        manifest.update({"status":"preflight_passed","selectedChannels":selected,"channelAdmission":admission,"syntheticWarmup":warmup,"questTcpActive":True,"ackActive":True,"acceptedSyncFresh":True,"associationGateReady":True,"evidenceSinksWritable":True})
        (root/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
        controller=LiveOnlineController(DecoderBackend("numpy_fbcca"),selected,predictions,decisions)
        print("FORMAL READY session={} selected={} seed={}; broadcasting 30 trials".format(root,selected,args.seed),flush=True); await endpoint.broadcast_dataset_plan(plan)
        deadline=time.monotonic()+args.session_timeout_seconds
        while time.monotonic()<deadline and len(results)<30 and not errors:
            if len(results)==3 and checkpoint is None:
                checkpoint=early_technical_checkpoint(results); (root/"early-technical-checkpoint.json").write_text(json.dumps(checkpoint,indent=2)+"\n",encoding="utf-8")
                if checkpoint["verdict"]!="CONTINUE": errors.append("early_technical_checkpoint_failed")
            await asyncio.sleep(.05)
        if errors:
            await endpoint.abort_formal_session(sid,";".join(errors)); raise RuntimeError(";".join(errors))
        if len(results)!=30: raise RuntimeError("expected 30 results, got {}".format(len(results)))
        status="completed"
    except Exception as exc: errors.append(str(exc)); print("formal error: {}".format(exc),flush=True)
    finally:
        coordinator.finalize()
        if server: server.close(); await server.wait_closed()
        if adapter.state.value=="streaming": adapter.stop()
        adapter.close()
        validity=[technical_validity(x) for x in results]; evaluation=evaluate_formal(plan["trials"],results)
        (root/"technical-validity.json").write_text(json.dumps(validity,indent=2)+"\n",encoding="utf-8"); (root/"posthoc-evaluation.json").write_text(json.dumps(evaluation,indent=2)+"\n",encoding="utf-8")
        manifest.update({"status":status,"runtimeErrors":errors,"elapsedSeconds":time.monotonic()-start,"callbackErrors":adapter.callback_errors,"observedPacketCount":len(adapter.timeline.packets)})
        (root/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    return 0 if status=="completed" else 1
def main():
    p=argparse.ArgumentParser(); p.add_argument("--com",required=True,choices=["COM11"]); p.add_argument("--data-root",type=Path,required=True); p.add_argument("--seed",type=int,required=True); p.add_argument("--preflight-timeout-seconds",type=float,default=150); p.add_argument("--session-timeout-seconds",type=float,default=600); p.add_argument("--host",default="0.0.0.0"); p.add_argument("--port",type=int,default=11000); raise SystemExit(asyncio.run(run(p.parse_args())))
if __name__=="__main__": main()
