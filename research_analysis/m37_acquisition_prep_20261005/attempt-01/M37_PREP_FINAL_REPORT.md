# M37 Prep Final Report

Preparation outcome: software path PASS; live Quest/ND8 readiness remains blocked or unverified. Synthetic outputs are rehearsal evidence only; no EEG quality or decoder claim is made.

| Requirement | Result |
|---|---|
| Quest runtime connected | NOT CONNECTED |
| Unity Editor required / permanent USB/ADB | NO / NO |
| ND8 connected and continuous stream stable | NOT TESTED (COM11 operation blocked) |
| Simulated trigger / duplicate trigger | PASS (shared handler; duplicate rejected) |
| Physical laser trigger | NOT TESTED |
| 1.5 s prep; onset anchor; 4500/channel epoch | PASS in synthetic accelerated rehearsal |
| All three mappings | PASS in software; physical display timing unverified |
| Hardware stop/restart and live disconnect recovery | NOT TESTED |
| Hardware streams left open | NO |
| Formal data root | D:\EEG_Study\m37_prospective\formal |

Software verification: 27 targeted Python tests passed; the generated Unity Editor C# project compiled with 0 errors (56 dependency/reference warnings); the 14-file artifact audit passed. Fake Quest TCP exercised offer, start/stop ACK and the shared simulated accepted-trigger handler. A fake ND8 adapter exercised immediate first-packet delivery, raw recorder wiring, stop/restart offsets, and append-only packet/sample indices.

Current-code 30-trial accelerated rehearsal: PASS, 10 trials per slot, 4500 samples/channel, 500 pre-onset + 4000 post-onset. Output: D:\EEG_Study\m37_prospective\_preflight\software_rehearsal_final_20261005T052451.

## Exact status fields

QUEST RUNTIME CONNECTED: NO — no m19_research_ready in 20 s
UNITY EDITOR REQUIRED: NO
PERMANENT USB/ADB REQUIRED: NO
ND8 CONNECTED: NOT TESTED — COM11 open/start was blocked by automatic approval review
ND8 PORT: COM11 (enumeration only)
CONTINUOUS STREAM PASS: synthetic rehearsal PASS; live ND8 NOT TESTED
SIMULATED TRIGGER PATH PASS: YES
PHYSICAL LASER TRIGGER TESTED: NO
1.5S PREP PASS: YES — synthetic timeline
STIMULUS ONSET SAMPLE ANCHOR PASS: YES — synthetic ring buffer
[-0.5,+4.0) EPOCH PASS: YES — 500 pre + 4000 post samples/channel in synthetic run
EXPECTED SAMPLES/CHANNEL: 4500
DUMMY TRIALS COMPLETED: 30 synthetic accelerated; 0 live hardware
DUMMY TRIALS TECHNICALLY VALID: 30 synthetic; live not tested
PACKET GAP COUNT: 0 synthetic
DUPLICATE TRIGGER TEST: PASS
STOP/RESTART TEST: PASS in synthetic resume test; live port release/reopen not tested
DISCONNECT RECOVERY TEST: PASS under software fault injection; real disconnect not tested
DEVICE BATTERY/LOSS OBSERVED: no telemetry; no loss event observed
HARDWARE STREAMS CLOSED AFTER TEST: YES (no live stream opened)
FORMAL DATA ROOT READY: formal root and 6×63 schedule are prepared; live-device gates remain
REAL LASER EVENT ROUTE: NOT CONFIRMED — current Quest Research route receives m19_research_trigger from dwell; no independent laser/TTL receiver was found
TOMORROW PREFLIGHT COMMAND: powershell -ExecutionPolicy Bypass -File scripts\m37_preflight.ps1
TOMORROW FORMAL START COMMAND: powershell -ExecutionPolicy Bypass -File scripts\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed
