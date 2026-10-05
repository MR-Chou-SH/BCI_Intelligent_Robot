# M37 Prep Final Report

Date: 2026-10-05. Tonight's checks are acquisition engineering evidence. Floating-electrode ND8 data were not evaluated for EEG quality or decoder accuracy. No formal session was started.

| Requirement | Result |
|---|---|
| Quest Research runtime | Historical PASS at 16:43 and during the 17:40 Quest-connected batch. Latest fresh launch displayed the Research UI and connected to PC TCP 11001, but did not send `m19_research_ready`; current deployed package is the older 16:34 APK. |
| Unity Editor / permanent USB or ADB | Editor and USB/ADB are not required at runtime. Wireless ADB remains available for deployment/diagnostics. A new APK is required to deploy the latest source fixes; both batch builds were blocked by Unity Licensing Client startup failures. |
| ND8 live stream | PASS on COM11: preflight captured 15 contiguous packets (200 samples × 8 channels); PC-only 24-trial batch had 1725 sequential packets, contiguous indexes, and 0 gaps; Quest-connected 24-trial batch also captured contiguous packets with 0 gaps. |
| M37 preflight | Historical PASS at 16:43. The later fresh-launch retry did not receive `m19_research_ready`, so the current deployed setup is NOT READY until the fixed APK is rebuilt, installed, and preflight passes again. |
| Simulated trigger | PASS: all 24 corrected live trials used `M37TrialCoordinator.accept_trigger` (`pc_simulated_same_handler`); duplicate trigger was rejected |
| Quest gaze offer smoke attempt | NOT AN ACCEPTANCE GATE TONIGHT: two offers were displayed, but no manual gaze action was confirmed and no event arrived; both partial attempts are preserved as technical-invalid |
| Physical laser trigger | NOT TESTED, as specified |
| Post-trigger preparation | PASS in live run: 1.503–1.519 s |
| Stimulus onset to software offset | PC-only run passed the 20 ms software-deadline check (4.001–4.016 s). In the prior Quest-connected batch, PC receipt of the stop acknowledgement was 4.098–4.245 s; this includes network delay and is not a measurement of Quest-local stimulus duration. Optical timing was not measured. |
| Sample anchor / epoch | PASS in prior Quest-connected batch: 24 anchors and 24 readable 8×4500 epochs with 500 pre + 4000 post samples. |
| Rest / next cue | PASS within 50 ms scheduling tolerance: 6.012–6.046 s |
| All target slots | PASS: 8 trials each at 7.2/9/12 Hz; audio coding is 660/880/1320 Hz |
| Stop/restart / port release | PASS for completed runs: multiple sessions reopened COM11; a post-timeout stuck process tree was stopped after validating its partial files, then COM11 reopened for a 3-second probe and closed successfully |
| Live disconnect injection | NOT TESTED; targeted software fault injection passed without fabricated samples |
| Hardware streams after testing | CLOSED; the latest ready retry closed COM11; TCP 11001 is free; the Quest Research process was force-stopped after diagnostics. |
| Formal data root | Prepared with frozen 6×63 schedule; session_001 does not exist |

The first 30-trial live batch preceded a timing correction and is retained in _dummy; audit measured onset-to-offset 4.025–4.235 s and 12.011–12.064 s from prior TRIAL_COMPLETE to next cue. The PC-only timing implementation then passed a deterministic regression, a 2-trial live retest, and a balanced 24-trial live batch. A separate Quest-connected 24-trial batch completed all captures through the shared simulated-trigger handler, but used the pre-update Quest APK and PC stop acknowledgement; its stop receipt intervals (4.098–4.245 s) must not be presented as Quest-local visual-stop timing. Across the hardware batches, 80 dummy trials were attempted: 30 pre-fix, 2 + 24 PC-only corrected, and 24 Quest-connected. Raw data remain on D:.

After that connected batch, the PC coordinator was updated to prefer the Quest-local `m19_research_stimulus_stopped` timestamp and retain a bounded legacy stop-ACK fallback. The Quest transport was also updated to publish `m19_research_ready` after each successful TCP connection, avoiding reliance on scene/socket startup order. Three focused Python tests and Python compilation pass. The two Unity 6000.0.66f2 build attempts could not reach the build entry: logs show repeated `LicenseClient-zsh21 refused`, Licensing Client reconnect failures, and `com.unity.editor.headless was not found`; neither attempt produced an APK. The installed Quest package remains SHA-256 `99f5926276b2c291b684347f5ffa607411cea713c6752cfed5a9462ef29606af` from 16:34.

A fresh launch of that installed APK displayed the expected three-target Research UI. The Quest TCP client connected to PC port 11001, but sent no application bytes during the bounded raw probe and the Python probe received no `m19_research_ready`. A three-trial live rehearsal therefore stopped before its first trial, closed COM11, and preserved any raw packets only under the new D: `_dummy` path. No formal session was created. The source fix is not yet deployed or verified on Quest; see `quest_rebuild_and_ready_retest.json`.

## Required completion fields

QUEST RUNTIME CONNECTED: NOT PASS ON LATEST RETEST — historical ready-message pass exists; current installed package connected TCP but sent no ready event. The latest source fix needs a new APK.
UNITY EDITOR REQUIRED: NO for runtime; an Editor build is required to deploy the current source fixes.
PERMANENT USB/ADB REQUIRED: NO — wireless ADB was used for one-time install/diagnostics; runtime transport uses Wi-Fi/LAN TCP
ND8 CONNECTED: YES during earlier dummy/preflight runs; latest rehearsal opened and closed COM11 before any trial.
ND8 PORT: COM11
CONTINUOUS STREAM PASS: YES — prior COM11 batches had contiguous sample/packet indexes and 0 gaps.
SIMULATED TRIGGER PATH PASS: YES — 24 Quest-connected and 26 PC-only corrected trials entered `M37TrialCoordinator.accept_trigger`; duplicate rejected. Latest retry did not reach a trial.
PHYSICAL LASER TRIGGER TESTED: NO
1.5S PREP PASS: YES — measured 1.503–1.519 s in final 24
STIMULUS ONSET SAMPLE ANCHOR PASS: YES in previous live batches — Quest-connected 24/24 anchors; sample timestamp semantics remain unverified.
[-0.5,+4.0) EPOCH PASS: YES in previous live batches — 50 post-fix captures are readable 8×4500 epochs, 500 pre + 4000 post; latest retry stopped before trial 1.
EXPECTED SAMPLES/CHANNEL: 4500
DUMMY TRIALS COMPLETED: 80 across earlier batches (30 pre-fix, 2 + 24 PC-only corrected, 24 Quest-connected); latest compatibility retry completed 0.
DUMMY TRIALS TECHNICALLY VALID: 50 post-fix capture-complete trials; Quest-local stop timing was not telemetry-verified in its 24-trial batch. The 30 pre-fix trials remain preserved but excluded from timing compliance.
PACKET GAP COUNT: 0 in the completed 24-trial live batches and short ND8 probes.
DUPLICATE TRIGGER TEST: PASS — one duplicate rejected in each live-dummy run
STOP/RESTART TEST: PASS — COM11 was reopened across separate runs and closed afterward
DISCONNECT RECOVERY TEST: PASS in targeted software fault injection; physical disconnect not forced
DEVICE BATTERY/LOSS OBSERVED: no power loss seen; battery telemetry unavailable
HARDWARE STREAMS CLOSED AFTER TEST: YES — COM11 closed; Quest Research app stopped; TCP 11001 free.
FORMAL DATA ROOT READY: YES — schedule validated (6 sessions × 63); no session_001 created
QUEST GAZE OFFER SMOKE: INCOMPLETE — earlier two offers had zero confirmed manual trigger events. Latest fresh launch also failed the ready handshake. This is not evidence about the physical laser.
SIMULATED TRIGGER PATH PASS: YES — all 24 corrected live ND8 trials entered `M37TrialCoordinator.accept_trigger` through `pc_simulated_same_handler`; 24/24 completed the downstream pipeline
M37_PREFLIGHT_PASS: HISTORICAL PASS at 16:43; latest Quest-ready retest did not pass, so rerun after deploying the current source.
PHYSICAL LASER/TTL TRIGGER TESTED: NO — distinct external trigger route was not tested
NEXT REQUIRED CHECK: tomorrow morning, perform 3–5 dummy trials using the real laser trigger; confirm it enters the shared handler, IDs match, no duplicate is accepted, and 1.5 s prep/epoch timing pass before formal acquisition
POST-TIMEOUT CLOSE CHECK: the no-trigger attempt wrote `TRIAL_TECHNICAL_INVALID` with `fakeSamplesAdded=false`; its 627 raw/metadata packet lines and last JSON records were verified before stopping the hung process tree. A fresh 3-second COM11 reopen probe passed and closed.
TOMORROW PREFLIGHT COMMAND: after Unity licensing is restored and the dedicated APK rebuilt/installed, run `powershell -ExecutionPolicy Bypass -File scripts\m37_preflight.ps1 -ComPort COM11 -QuestIp 192.168.43.110`.
TOMORROW FORMAL START COMMAND: powershell -ExecutionPolicy Bypass -File scripts\m37_start_acquisition.ps1 -SessionNumber 1 -PhysicalTriggerPassed
TOMORROW OPERATOR CHECKLIST: M37_TOMORROW_OPERATOR_CHECKLIST.md
REMAINING BLOCKERS: Unity Licensing Client is unavailable, preventing the latest Research APK from being built; the currently installed APK failed to send `m19_research_ready` on fresh launch. Rebuild/install and rerun Quest preflight before the 3–5 physical-trigger trials. Physical laser remains intentionally untested tonight; no formal session was created. Floating-electrode data do not assess EEG quality or SSVEP accuracy.
FINAL COMMIT: see final Git checkpoint reported at delivery
REMOTE HASH VERIFIED: see final push verification reported at delivery

Raw continuous EEG, per-trial .npz epochs, and full packet metadata remain only under D:\EEG_Study\m37_prospective\_dummy / _preflight; none are copied into this repository bundle.
