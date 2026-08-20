"""ND8-only packet-stream stability check; no Quest, plan, or decoder."""
import argparse, json, time
from pathlib import Path
import numpy as np
from .nd8_serial_adapter import Nd8SerialAdapter
from eeg.sample_association.jsonl import AppendOnlyJsonl
from eeg.decoder.formal_online import channel_admission

def main():
 p=argparse.ArgumentParser(); p.add_argument('--com',required=True); p.add_argument('--seconds',type=float,default=120); p.add_argument('--output',type=Path,required=True); a=p.parse_args()
 packets=[]; statuses=[]; adapter=Nd8SerialAdapter(a.com,metadata_log=AppendOnlyJsonl(a.output.with_suffix('.metadata.jsonl')),raw_packet_log=AppendOnlyJsonl(a.output.with_suffix('.raw.jsonl')),live_packet_observer=lambda packet,status:(packets.append(np.asarray(packet.samples,float).copy()),statuses.append(status.status)))
 started=time.monotonic(); error=None
 try:
  adapter.open_port(); adapter.start_streaming()
  while time.monotonic()-started<a.seconds: time.sleep(.1)
 except Exception as exc: error=str(exc)
 finally:
  if adapter.state.value=='streaming': adapter.stop()
  adapter.close()
 values=np.concatenate(packets,axis=1) if packets else np.empty((8,0)); report={'durationSeconds':time.monotonic()-started,'packetCount':len(packets),'callbackErrors':adapter.callback_errors,'runtimeError':error,'continuityStatuses':statuses,'sampleRateHz':1000,'channelAdmission':channel_admission(values,statuses) if values.shape[1] else None}
 report['verdict']='PASS' if not error and not adapter.callback_errors and report['durationSeconds'] >= a.seconds and len(packets)>0 and all(x in ('continuous','anomaly') for x in statuses) else 'FAIL'
 a.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report),flush=True)
if __name__=='__main__': main()
