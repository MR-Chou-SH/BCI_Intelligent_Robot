# M37 Prep Final Report

Date: 2026-10-05. Tonight's checks are acquisition engineering evidence. Floating-electrode ND8 data were not evaluated for EEG quality or decoder accuracy. No formal session was started.

| Requirement | Result |
|---|---|
| Quest Research runtime | NOT CONNECTED: PC IP ping passed, but Quest did not publish m19_research_ready; APK install/launch is not confirmed |
| Unity Editor / permanent USB or ADB | NOT REQUIRED AT RUNTIME; standalone APK build succeeded, one-time install remains |
| ND8 live stream | PASS on COM11; 24-trial corrected run, 8 channels, 1000 Hz, 1725 contiguous packets, 0 gaps |
| Simulated trigger | PASS; 24 accepted via shared handler; duplicate rejected |
| Physical laser trigger | NOT TESTED, as specified |
| Post-trigger preparation | PASS in live run: 1.503–1.519 s |
| Stimulus onset to software offset | PASS within 20 ms scheduling tolerance: 4.001–4.016 s; optical timing not measured |
| Sample anchor / epoch | PASS: 24 anchors and 24 readable 8×4500 epochs with 500 pre + 4000 post samples |
| Rest / next cue | PASS within 50 ms scheduling tolerance: 6.012–6.046 s |
| All target slots | PASS: 8 trials each at 7.2/9/12 Hz; audio coding is 660/880/1320 Hz |
| Stop/restart / port release | PASS: separate live sessions reopened COM11; SDK close logged; COM11 remains enumerable |
| Live disconnect injection | NOT TESTED; targeted software fault injection passed without fabricated samples |
| Hardware streams after testing | CLOSED; Quest test traffic is inactive |
| Formal data root | Prepared with frozen 6×63 schedule; session_001 does not exist |

The first 30-trial live batch preceded a timing correction and is retained in _dummy; audit measured onset-to-offset 4.025–4.235 s and 12.011–12.064 s from prior TRIAL_COMPLETE to next cue. The implementation was corrected and verified by a deterministic regression, a 2-trial live retest, and a final balanced 24-trial live batch. The 30 pre-fix captures are excluded from corrected timing compliance counts; they were not deleted.

## Required completion fields

QUEST RUNTIME CONNECTED: NO — no m19_research_ready in the bounded preflight
UNITY EDITOR REQUIRED: NO
PERMANENT USB/ADB REQUIRED: NO — one-time APK deployment remains unresolved
ND8 CONNECTED: YES — live COM11 streams passed
ND8 PORT: COM11
CONTINUOUS STREAM PASS: YES — final live batch had 1725 packets; sequence/sample indices contiguous; 0 gaps
SIMULATED TRIGGER PATH PASS: YES — 24 accepted through M37TrialCoordinator.accept_trigger; duplicate rejected
PHYSICAL LASER TRIGGER TESTED: NO
1.5S PREP PASS: YES — measured 1.503–1.519 s in final 24
STIMULUS ONSET SAMPLE ANCHOR PASS: YES — anchor present on all 24; first-sample hardware timestamp semantics remain unverified
[-0.5,+4.0) EPOCH PASS: YES — all 24 epochs are 8×4500, 500 pre + 4000 post
EXPECTED SAMPLES/CHANNEL: 4500
DUMMY TRIALS COMPLETED: 56 total (30 pre-fix, 2 first post-fix, 24 final post-fix)
DUMMY TRIALS TECHNICALLY VALID: 26 post-fix (2 + 24); the 30 pre-fix captures are preserved but excluded from timing compliance
PACKET GAP COUNT: 0 in the three live dummy sessions and the short ND8 probes
DUPLICATE TRIGGER TEST: PASS — one duplicate rejected in each live-dummy run
STOP/RESTART TEST: PASS — COM11 was reopened across separate runs and closed afterward
DISCONNECT RECOVERY TEST: PASS in targeted software fault injection; physical disconnect not forced
DEVICE BATTERY/LOSS OBSERVED: no power loss seen; battery telemetry unavailable
HARDWARE STREAMS CLOSED AFTER TEST: YES
FORMAL DATA ROOT READY: YES — schedule validated (6 sessions × 63); no session_001 created
TOMORROW PREFLIGHT COMMAND: powershell -ExecutionPolicy Bypass -File scripts\m37_preflight.ps1 -ComPort COM11 -QuestIp 192.168.43.110
TOMORROW FORMAL START COMMAND: powershell -ExecutionPolicy Bypass -File scripts\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed
PHYSICAL TRIGGER MORNING CHECK: install/launch the Research APK, then run 3–5 real-trigger dummy trials and verify one shared trigger event per trial; do not start formal acquisition if the real input route fails
TOMORROW OPERATOR CHECKLIST: M37_TOMORROW_OPERATOR_CHECKLIST.md
REMAINING BLOCKERS: Quest APK is built but not confirmed installed/launched; no m19_research_ready; actual laser/TTL event route remains unverified
FINAL COMMIT: see final Git checkpoint reported at delivery
REMOTE HASH VERIFIED: see final push verification reported at delivery

Raw continuous EEG, per-trial .npz epochs, and full packet metadata remain only under D:\EEG_Study\m37_prospective\_dummy / _preflight; none are copied into this repository bundle.
