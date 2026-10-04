# M37-Prep — Night-Before Prospective EEG Acquisition Readiness

## 0. Mission

Repository:

`C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`

Tonight's task is to make tomorrow's prospective EEG acquisition as close as possible to:

> Wear ND8 → run preflight → pass channel-quality check → start session → collect data.

Tonight, both Quest 3 and ND8 may be connected for end-to-end software-link verification.

Important constraints tonight:

- Quest 3 USB-C port is occupied by charging.
- Do **not** require a permanent USB/ADB connection for runtime.
- Quest 3 should run its installed Unity app standalone.
- Quest ↔ PC runtime communication should use the existing network path over Wi-Fi/LAN.
- ND8 may be connected to the PC; electrodes may be floating in air.
- The user cannot manually operate the physical laser trigger tonight.
- Therefore physical trigger input cannot be validated tonight.
- Use a software-injected / auto-trigger path that enters the same downstream trigger handler/state-machine path as a real trigger.
- Tomorrow morning the only mandatory human hardware check should be a short physical-trigger preflight before formal collection.
- Tonight may validate device connectivity, timing, buffering, saving, trial state transitions, restart/recovery, and repeated dummy trials.
- Tonight must **not** claim EEG signal quality or SSVEP decoding accuracy because electrodes may be floating.

The goal is readiness, not scientific data collection.

---

# 1. Desired Formal Trial Flow

Implement and validate this exact formal timing/state model:

1. 6.0 s rest after the previous trial.
2. Target audio cue plays.
3. User identifies which SSVEP block to attend.
4. System enters `WAIT_TRIGGER`.
5. User normally performs the laser trigger.
6. Exactly one valid trigger is accepted.
7. After the accepted trigger, wait exactly 1.5 s preparation time.
8. At the end of the 1.5 s preparation:
   - SSVEP stimulus formally begins;
   - record authoritative software `stimulus_onset`;
   - associate `stimulus_onset` with ND8 continuous-stream sample index;
   - define this instant as EEG `t = 0`.
9. Keep the stimulus active for 4.0 s.
10. At `t = +4.0 s`, record `stimulus_off`.
11. Finalize the trial EEG epoch.
12. Enter 6.0 s rest.
13. Play the next target cue.

Canonical sequence:

`cue → trigger → 1.5 s preparation → stimulus_onset / EEG t=0 → 4.0 s SSVEP → 6.0 s rest → next cue`

The trigger itself is **not** EEG t=0.

---

# 2. Required EEG Epoch

For every valid trial save:

`[-0.5 s, +4.0 s)`

relative to `stimulus_onset`.

At 1000 Hz, this means exactly:

- 500 pre-onset samples
- 4000 post-onset samples
- total `4500 samples/channel`

Use half-open interval semantics if convenient.

Required architecture:

`ND8 continuous stream → ring buffer → stimulus_onset sample anchor → extract [-500, +4000)`

Do not start the ND8 stream only at stimulus onset.

The ND8 stream should already be running before the trial.

Ring buffer depth should be at least 2 s; 5 s or more is preferred.

---

# 3. Formal Data Root

Codex may create the D-drive directories automatically.

Preferred root:

`D:\EEG_Study\m37_prospective`

Suggested layout:

```text
D:\EEG_Study\m37_prospective\
    _preflight\
    _dummy\
    formal\
        session_001\
        session_002\
        ...
```

Rules:

- Tonight's floating-electrode / simulated-trigger data must go only under `_preflight` or `_dummy`.
- Never mix tonight's dummy data with tomorrow's formal data.
- Never overwrite older M6/M34/M35/M36 EEG data.
- If directories already exist, create safe timestamped children.
- Do not commit raw EEG to Git.

---

# 4. Quest Runtime Architecture

The formal acquisition path must **not depend on Unity Editor**.

Preferred runtime:

`Quest 3 standalone installed app`
↕
`Wi-Fi / LAN`
↕
`PC acquisition service`
↕
`ND8`

Unity Editor may be used tonight only if source-level debugging or rebuilding the Quest app is genuinely needed.

Do not keep Unity Editor in the formal runtime path if the Quest app can already run standalone.

Do not require a permanent USB connection.

The Quest's USB-C port is needed for charging.

---

# 5. Meta Quest Developer Hub / ADB Policy

MQDH/ADB is a development and diagnostics aid, not part of the formal acquisition critical path.

Tonight:

- If MQDH can see the Quest wirelessly, it may be used for diagnostics/log inspection.
- If MQDH cannot see the Quest without USB, do not block the acquisition task solely on that.
- Do not require ADB to stay connected during formal runtime.
- Do not repeatedly attempt ADB if the headset is charging and no USB data link is available.
- If a rebuild/install is absolutely required and wireless deployment is unavailable, report a blocker requiring a short temporary USB connection tomorrow or when the user is available.
- Do not power-cycle or modify Quest firmware.

The formal pass criterion is:

> Quest app can run standalone and communicate with the PC over the existing network protocol.

---

# 6. Existing Project Transport

Inspect and reuse existing project communication code before adding new paths.

Known historical ports:

- selection / EEG: `11001`
- telemetry: `11002`
- IPC: `12021`

Do not change these unless the repository already contains a newer authoritative configuration.

Verify:

- PC server binds correctly;
- Quest connects;
- reconnect behavior is safe;
- trial IDs stay synchronized;
- stimulus-start/stimulus-stop events are transmitted reliably;
- no old stale socket causes the next run to fail.

---

# 7. ND8 Connection

Known historical device:

- Neurodance ND8
- 1000 Hz
- historically `COM11`

Use the known Python environment:

`C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe`

Workflow:

1. Check the existing configured COM port.
2. If COM11 is unavailable, enumerate serial devices safely.
3. Use existing Neurodance descriptors/config if available.
4. Do not blindly probe unrelated serial ports.
5. Do not modify ND8 firmware.
6. Do not use floating-electrode data to tune thresholds.
7. Verify sample counter continuity, packet continuity, sample rate, and raw channel shape.

Tonight's ND8 test is for transport/data integrity only.

---

# 8. Fixed SSVEP Mapping

Preserve:

- slot 0 = 7.2 Hz
- slot 1 = 9.0 Hz
- slot 2 = 12.0 Hz

Never map by color.

The audio cue may use:

- high/middle/low pitch
- one/two/three beeps

but the canonical stored labels are always:

- `target_slot`
- `target_frequency_hz`

If an explicit cue mapping already exists, preserve it.

If it does not, place it in one editable config file rather than hard-coding it across multiple scripts.

---

# 9. Trigger Architecture Tonight

The user cannot perform the physical laser trigger tonight.

Therefore implement a **simulated trigger / auto-trigger test mode**.

Critical requirement:

The simulated trigger must enter as close as possible to the real accepted-trigger path.

Preferred order:

## Preferred: Quest-side auto-trigger

If the existing Quest app can safely support a debug command:

`AUTO_TRIGGER(slot=X)`

then:

PC sends debug command
→ Quest enters the same post-trigger logic
→ Quest sends the same downstream trigger/stimulus events as the physical path.

This tests more of the real chain.

## Fallback: PC-side injected trigger

If Quest-side injection would require risky/redundant app changes:

inject a trigger into the PC-side acquisition state machine at the same handler used after a valid trigger event.

Do not fork a separate fake acquisition pipeline.

The downstream behavior must be shared with real trigger processing.

---

# 10. Trigger Result Reporting

Tonight must distinguish:

`SIMULATED_TRIGGER_PATH = PASS/FAIL`

from:

`PHYSICAL_LASER_TRIGGER = NOT_TESTED`

Do **not** report `LASER TRIGGER PASS` tonight.

Tomorrow morning requires a short physical-trigger preflight.

---

# 11. Dummy Auto-Trigger Timing

For autonomous testing tonight:

- play/emit the normal cue;
- wait a configurable dummy reaction delay, e.g. ~1 s;
- inject the simulated trigger;
- then execute the formal 1.5 s prep;
- start SSVEP;
- collect/finalize the full epoch;
- rest 6 s;
- proceed.

The dummy reaction delay is **test-only** and is not part of tomorrow's formal trial protocol.

---

# 12. Trial State Machine

Use an explicit state machine, at minimum:

- `IDLE`
- `REST`
- `CUE`
- `WAIT_TRIGGER`
- `PREP_1P5`
- `STIMULUS`
- `FINALIZE`
- `COMPLETE`
- `ERROR_RECOVERABLE`
- `ERROR_FATAL`

Rules:

- Trigger accepted only in `WAIT_TRIGGER`.
- Duplicate triggers cannot start a duplicate trial.
- Trigger during `PREP_1P5` or `STIMULUS` is ignored and logged.
- Trial ID allocated once.
- Trial state transition timestamps are logged.
- Abort/restart must not overwrite prior finalized trials.

---

# 13. Timing / Event Contract

Record at least:

- `cue_onset_pc`
- cue ID / target slot / frequency
- accepted trigger PC timestamp
- Quest trigger timestamp if available
- prep start
- intended stimulus-onset deadline
- actual `stimulus_started_software`
- PC receive timestamp
- ND8 global sample index associated with stimulus onset
- clock mapping/freshness/uncertainty if existing infrastructure provides it
- `stimulus_off`
- trial finalization time

Do not call the software onset a verified physical optical onset.

No photodiode validation is required tonight.

---

# 14. Ring Buffer and Epoch Finalization

At `stimulus_onset`:

- capture/associate the ND8 onset sample index;
- ensure at least 500 prior samples are already available;
- continue streaming through +4.0 s;
- extract exactly `[-500,+4000)` samples;
- finalize the trial only after the complete post-onset segment exists.

Do not block the ND8 receive loop on slow disk I/O.

Prefer existing producer/consumer or asynchronous buffering patterns.

---

# 15. Raw Channels

Save all ND8 raw channels provided by the device.

Do not save only the 3-channel or 5-channel analysis subset.

Analysis channel subsets can be derived later.

Per-trial metadata must state:

- sample rate
- channel IDs/order
- shape
- sample count
- packet continuity
- missing sample count
- dropout flag

---

# 16. Technical Validity

A trial can be marked technical-invalid for reasons such as:

- ND8 disconnect
- Quest event loss
- invalid/missing onset anchor
- incomplete 4500-sample epoch
- packet discontinuity beyond the frozen tolerance
- manual abort
- trial state corruption

Do not mark a trial invalid because its EEG decoder output is wrong.

Classifier performance must never influence technical validity.

---

# 17. Session Manifest / Crash Safety

Maintain:

- append-only event log where practical;
- independent per-trial finalization;
- session manifest;
- config snapshot;
- schedule hash;
- raw file references;
- file hashes;
- device descriptors;
- disconnect/recovery events.

A crash on trial N must not corrupt finalized trials 1..N-1.

Partial/incomplete trials must remain identifiable as partial.

Never silently replace a partial trial with another trial under the same ID.

---

# 18. Tomorrow Formal Schedule Support

Prepare the software to support multiple sessions.

Default planned scale should be configurable around:

- 6 sessions
- 63 valid trials/session
- 21 trials/class/session
- 3 balanced classes

But do not hard-code 378 into acquisition logic.

Provide a schedule generator that:

- balances 7.2/9/12 Hz;
- randomizes order;
- avoids pathological long same-target runs;
- freezes and saves the schedule before acquisition;
- supports technical-invalid replacement trials at the end without deleting the original invalid record.

---

# 19. Preflight Wrapper

Create a simple one-command preflight, preferably a `.ps1` wrapper consistent with repository conventions.

Examples:

- `m37_preflight.ps1`
- `m37_start_acquisition.ps1`

Tomorrow the user should not need to remember several Python commands.

Preflight should check:

1. Python environment exists.
2. D-drive data root writable.
3. Quest transport reachable.
4. Required TCP ports usable.
5. ND8 device/COM available.
6. ND8 stream starts.
7. Sample counter advances.
8. Ring buffer fills.
9. event/manifest logging writable.
10. cue/frequency mapping loaded.
11. session ID is safe/non-colliding.
12. disk space is sufficient.
13. stale locks/handles from a previous crash are handled.
14. acquisition config is frozen.

Output exactly one clear state:

`READY FOR FORMAL ACQUISITION`

or a concise blocker list.

---

# 20. Tonight Dummy Tests

Run hardware-linked dummy tests using floating electrodes and simulated trigger.

## Test A — Single complete trial

Verify:

- cue
- simulated trigger
- 1.5 s prep
- stimulus onset
- onset sample anchor
- +4 s recording
- 4500 samples/channel
- 500 pre-onset samples
- stimulus off
- finalize
- 6 s rest

## Test B — All three targets

At least one dummy trial for:

- slot 0 / 7.2 Hz
- slot 1 / 9 Hz
- slot 2 / 12 Hz

## Test C — Repeated stability

Run approximately 20–30 dummy trials if Quest and ND8 remain available.

Verify:

- no trial-ID drift
- no packet corruption
- no stale buffers
- no duplicate file names
- no deadlocks
- no memory growth large enough to threaten tomorrow

## Test D — Duplicate trigger

Programmatically send a duplicate simulated trigger and verify only one trial starts.

## Test E — Stop / restart

Stop acquisition cleanly between trials and restart.

Verify:

- handles release cleanly
- new run does not overwrite previous dummy data
- Quest and PC reconnect safely
- ND8 stream restarts safely

---

# 21. Battery / Device Depletion Policy

Quest and ND8 are currently fully charged, but either may lose power overnight, especially ND8.

This must be handled safely.

## If ND8 disconnects mid-trial

- mark current trial incomplete/technical-invalid;
- do not fabricate missing samples;
- do not fill zeros and call it valid;
- stop creating valid EEG trials;
- attempt bounded reconnect;
- if unavailable, close the hardware test cleanly and report a blocker.

## If Quest disconnects mid-trial

- mark onset/event synchronization invalid for that trial;
- do not claim a valid timing anchor;
- keep already written data safe;
- attempt bounded reconnect;
- resume only from a clean new trial boundary.

## Bounded retry

Do not retry forever.

Use a limited retry/backoff strategy.

If a device is clearly dead/unavailable, stop hardware testing gracefully and continue with software-only audits.

---

# 22. Battery Preservation After Tests

Once the required connected-device validation and repeated dummy trials pass:

- stop ND8 test streaming;
- release the serial port;
- stop dummy acquisition;
- flush and close files;
- stop unnecessary Quest test traffic;
- leave hardware in an idle/safe state.

Do **not** keep ND8 and Quest actively streaming all night.

The Long Run may continue with software-only tests/audits after hardware validation.

Do not issue undocumented device power-off commands.

---

# 23. Physical Trigger Check Tomorrow

Generate a tiny tomorrow-morning physical-trigger checklist.

Before any formal EEG session:

1. Wear ND8.
2. Run normal preflight.
3. Launch Quest app.
4. Perform 3–5 dummy trials using the **real laser trigger**.
5. Confirm physical trigger enters the same handler/state path.
6. Confirm 1.5 s prep and epoch timing.
7. Confirm trial IDs match.
8. Confirm no duplicate trigger.
9. Only then start `formal/session_001`.

If physical trigger fails, formal acquisition must not start.

This should be the only unvalidated hardware interaction left from tonight.

---

# 24. No Scientific Tuning Tonight

Do not:

- retune FBCCA
- train eTRCA/TDCA
- tune M35/M36 Context gate
- evaluate SSVEP accuracy on floating-electrode data
- change Context thresholds based on dummy data
- modify historical raw EEG

Tonight is acquisition engineering only.

---

# 25. Optional Online Score Logging

If existing online FBCCA logging is already easy to reuse, it may be enabled as a secondary diagnostic.

But:

- raw EEG capture is mandatory;
- online score logging is optional;
- scoring failure must not stop raw capture;
- tomorrow formal trials should still record the full 4.0 s after onset;
- no Context-based real early termination is enabled yet.

---

# 26. Required Artifacts

Create:

`research_analysis/m37_acquisition_prep_20261005/attempt-01/`

At minimum produce:

- `M37_PREP_PROTOCOL.md`
- `device_discovery.json`
- `preflight_summary.json`
- `timing_validation.json`
- `dummy_trial_manifest.csv`
- `dummy_event_log.jsonl`
- `epoch_shape_audit.json`
- `trigger_simulation_validation.json`
- `disconnect_recovery_test.json`
- `battery_preservation_closeout.json`
- `formal_config_template.json`
- `formal_schedule_example.csv`
- `M37_TOMORROW_OPERATOR_CHECKLIST.md`
- `M37_PREP_FINAL_REPORT.md`

Use repository-native alternatives if clearly better.

---

# 27. Required Automated Checks

At minimum verify:

- dummy and formal roots separated
- slot↔frequency mapping fixed
- trial IDs unique
- state-machine transitions valid
- duplicate trigger rejected
- onset anchor exists
- epoch sample count = 4500/channel
- pre-onset samples = 500
- post-onset samples = 4000
- channel count/order recorded
- raw data files readable after write
- manifests reference existing files
- no overwrite after restart
- incomplete trials clearly marked
- no fake data inserted after disconnect
- hardware streams closed after validation
- preflight wrapper exits with correct status
- tomorrow checklist generated

---

# 28. Final Report Must Answer

1. Did Quest connect over the available runtime path?
2. Was Unity Editor required?
3. Was permanent USB/ADB required?
4. Did ND8 connect?
5. Which COM/device was used?
6. Did continuous ND8 streaming remain stable?
7. Did the simulated-trigger path enter the same downstream acquisition handler?
8. Was physical laser trigger tested? Expected answer tonight: NO.
9. Was the 1.5 s prep interval verified?
10. Was stimulus onset anchored to ND8 sample index?
11. Were epochs exactly 4500 samples/channel?
12. Were exactly 500 samples pre-onset?
13. Were all available raw channels preserved?
14. Did all three frequency mappings pass?
15. How many dummy trials completed?
16. How many were technically valid?
17. Were packet gaps seen?
18. Was duplicate-trigger rejection verified?
19. Was stop/restart verified?
20. Was disconnect-safe behavior verified?
21. Did any device lose power?
22. Were streams shut down after validation?
23. What exact command does the user run tomorrow?
24. What exact steps are required for the 3–5 physical-trigger preflight?
25. Are any blockers left before formal acquisition?

---

# 29. Long-Run Autonomous Rule

This is a Long Run preparation task.

Proceed autonomously:

1. inspect existing Quest/PC/ND8 acquisition code;
2. identify the smallest safe reuse path;
3. implement missing timing/ring-buffer/state-machine/metadata pieces;
4. create the D-drive data structure;
5. create preflight and formal-start wrappers;
6. validate software-only logic;
7. connect to Quest/ND8 if available;
8. run simulated-trigger end-to-end dummy trials;
9. diagnose/fix software failures;
10. rerun;
11. complete bounded repeated-trial tests;
12. test stop/restart;
13. test safe disconnect handling if feasible without risky hardware manipulation;
14. stop/release hardware streams;
15. finish software-only audits;
16. generate tomorrow operator checklist;
17. write final report;
18. commit/push authorized files.

Do not stop after one successful trial.

Do not wait for the user to manually trigger tonight.

Do not ask the user to choose routine implementation details.

Only request human intervention if there is a genuine irrecoverable blocker such as:

- headset authorization requiring a physical confirmation;
- Quest app absent and installation requires USB;
- ND8 physically disconnected/off;
- OS permission dialog requiring user action.

---

# 30. Git Discipline

Create or use an appropriate branch:

`codex/m37-acquisition-prep`

Preserve unrelated dirty/untracked files.

Never use:

- `git reset --hard`
- `git clean`
- force push
- destructive stash
- `git add .`
- `git add -A`

Selectively stage M37-prep files only.

Do not commit raw/dummy EEG.

Run relevant compile/tests/audit.

Commit and push.

Verify local/upstream/remote hashes.

---

# 31. Completion Summary

At completion print exactly:

`QUEST RUNTIME CONNECTED:`

`UNITY EDITOR REQUIRED:`

`PERMANENT USB/ADB REQUIRED:`

`ND8 CONNECTED:`

`ND8 PORT:`

`CONTINUOUS STREAM PASS:`

`SIMULATED TRIGGER PATH PASS:`

`PHYSICAL LASER TRIGGER TESTED: NO`

`1.5S PREP PASS:`

`STIMULUS ONSET SAMPLE ANCHOR PASS:`

`[-0.5,+4.0) EPOCH PASS:`

`EXPECTED SAMPLES/CHANNEL: 4500`

`DUMMY TRIALS COMPLETED:`

`DUMMY TRIALS TECHNICALLY VALID:`

`PACKET GAP COUNT:`

`DUPLICATE TRIGGER TEST:`

`STOP/RESTART TEST:`

`DISCONNECT RECOVERY TEST:`

`DEVICE BATTERY/LOSS OBSERVED:`

`HARDWARE STREAMS CLOSED AFTER TEST:`

`FORMAL DATA ROOT READY:`

`TOMORROW PREFLIGHT COMMAND:`

`TOMORROW FORMAL START COMMAND:`

`PHYSICAL TRIGGER MORNING CHECK:`

`TOMORROW OPERATOR CHECKLIST:`

`REMAINING BLOCKERS:`

`FINAL COMMIT:`

`REMOTE HASH VERIFIED:`
