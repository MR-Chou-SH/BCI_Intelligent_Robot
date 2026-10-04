# M37 Acquisition Preparation Run Plan

## Run identity

- Run ID: `m37-prep-night-20261005`
- Owner / requesting GPT task: Codex continuation of the user's M37 goal contract
- Created at (UTC): 2026-10-04T20:14:40Z
- Planned duration / deadline: bounded overnight preparation; stop hardware work after 20–30 dummy trials or any external device blocker
- Status: SOFTWARE COMPLETE; LIVE DEVICE VALIDATION BLOCKED / NEEDS HUMAN

## Objective

Prepare and validate the existing Quest 3 + ND8 acquisition path for tomorrow using the M37 contract, with standalone Quest over Wi-Fi/LAN, an auto-trigger that reaches the same accepted-trigger handler, an already-running continuous ND8 stream, and exact 4500-sample/channel epochs relative to software stimulus onset.

Machine-verifiable completion requires: isolated formal/dummy roots; a reproducible 6s REST → cue → WAIT_TRIGGER → one accepted simulated trigger → 1.5s prep → stimulus onset/sample anchor → 4s stimulus → exact [-500,+4000) epoch → finalize → 6s REST path; all raw ND8 channels and continuity metadata retained on live hardware; 20–30 stable dummy trials if hardware remains available; slot/frequency and schedule checks; fault tests including duplicate trigger, stop/restart and bounded disconnect behavior; preflight/start wrappers and morning checklist; streams closed; selective M37-only commit and push with remote hash verification. Physical laser remains NOT_TESTED.

## Repository baseline

- Absolute repository path: `C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`
- Git root: `C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`
- Starting branch: `codex/m36-context-safe-scaling`
- Starting HEAD: `c4b74670b56de3ce800e0f410e6d58e69bea69cd`
- Upstream: `origin/codex/m36-context-safe-scaling`
- Origin: `https://github.com/MR-Chou-SH/BCI_Intelligent_Robot.git`
- Starting working-tree state: 4368 porcelain entries with all untracked entries included: 5 modified tracked files and 4363 existing untracked paths. The five tracked modifications are the two M36 overnight task2 evidence/report files and three M20 integration source/test files. Existing untracked work includes earlier project analysis, M20/M36 evidence, and artifacts; all are preserved and excluded from M37 staging.
- Required baseline check before edits: recheck branch, HEAD, remote, and tracked/untracked status; only paths created for M37 after this point may be staged.
- Authorized target branch: `codex/m37-acquisition-prep`, created from the recorded HEAD with the existing dirty worktree carried forward unchanged.

## Scope and authorization

- Allowed paths: new M37-specific files under `integration/`, `scripts/`, `research_analysis/m37_acquisition_prep_20261005/attempt-01/`, and `docs/agent/overnight/runs/m37-prep-night-20261005/`; edits to existing acquisition files only when required for the stated M37 flow.
- Allowed mutations: minimal M37 acquisition code/tests/wrappers/config, append-only M37 run evidence, dedicated D-drive `_preflight` / `_dummy` / `formal` directory creation, selective staging, commit, and push to `origin/codex/m37-acquisition-prep` as explicitly authorized by M37.
- Explicit exclusions: raw EEG/dummy EEG in Git; any M6/M19 historical data; Unity Editor/build/Quest installation; physical laser trigger; decoder/science tuning; other M20/M36 dirty paths; any reset/clean/stash/discard/force-push.
- Frozen research decisions: slots 0/1/2 remain 7.2/9/12 Hz; physical trigger is not tested tonight; no classifier accuracy or EEG quality claims from floating electrodes; raw first, no fabricated samples, trialwise crash safety, 6s rest, 1.5s preparation, 4s stimulus, [-0.5,+4.0) extraction.
- Hardware / GUI actions explicitly authorized: safe serial enumeration; open only the configured ND8 port COM11 after software preflight; bounded live transport/sample-continuity checks with floating electrodes; use the existing standalone Quest runtime over Wi-Fi/LAN if reachable; inject test-only simulated triggers through the same PC accepted-trigger handler; close Quest/ND8 traffic/streams immediately after tests. No physical trigger, firmware, USB/ADB requirement, Unity Editor, or Quest build.
- Git actions authorized: create/use `codex/m37-acquisition-prep`; stage only named M37 files; commit and push this branch; verify remote hash. Never alter Git history.
- Data roots that may be read: existing configuration and source metadata; D-drive free space and serial-port enumeration only. No historical raw EEG is opened for modification.
- Data/output paths that may be written: `D:\EEG_Study\m37_prospective\_preflight\<timestamp>`, `D:\EEG_Study\m37_prospective\_dummy\<timestamp>`, and `D:\EEG_Study\m37_prospective\formal\` (template only); repository M37 artifact folder contains no raw EEG.

## Steps and verification

- Allowed verification profile: `m37-acquisition-contract-v1` using the exact targeted commands below and M37 audit outputs. Do not run the repository-wide overnight profile unless the scoped M37 tests expose a relevant integration failure.
- Verification output directory: `research_analysis/m37_acquisition_prep_20261005/attempt-01/verification/`
- Overall success: all software contract checks pass; if hardware is reachable, 20–30 real transport dummy trials are technically valid and all devices are closed. If a physical device is unavailable, complete all software work and mark that hardware item NEEDS HUMAN VALIDATION / BLOCKED with evidence, never claim PASS.

| Step | Atomic result | Exact validation command | Expected result | Attempts / timeout | State |
|---|---|---|---|---|---|
| 1 | Inventory Quest/PC/ND8 source, event/state contracts, runtime and safe discovery; create configuration/schedule | targeted py_compile plus M37/M8 suites | Source and device discovery recorded; no live device opened | 1 pass | DONE |
| 2 | Implement state machine, ring buffer, exact pre/post epoch, Quest transport bridge, wrappers and crash-safe metadata | py_compile, M37/M8 tests, generated Unity Editor project compile | 27 targeted Python tests pass; C# project compiles with 0 errors | 2 repair loops; final pass | DONE |
| 3 | Run synthetic 30-trial rehearsal, all slots, duplicate trigger, invalid/disconnect, restart and no-overwrite | python -m integration.m37_acquisition_prep rehearse --trials 30 --seed 37005 | Current-code rehearsal PASS, 30/30, 10/slot, 4500/channel, 500 pre + 4000 post; no EEG quality/decoder claim | 1 final run, 3.7 s | DONE |
| 4 | Check bounded Quest network path and live ND8 transport/continuity if authorized | probe-quest once; COM11 probe-nd8 rejected by automatic approval review | Quest did not connect in 20 s; ND8 remained unopened. Fake Quest TCP and fake ND8 streaming/resume paths PASS. Physical laser NOT_TESTED. | Quest 1 attempt; ND8 0 port opens after review rejection | BLOCKED / NEEDS HUMAN |
| 5 | Final shape/timing/trigger/disconnect audit, shutdown closeout, artifacts and tomorrow commands | python -m integration.m37_acquisition_prep audit --artifact-dir research_analysis/m37_acquisition_prep_20261005/attempt-01 | 14 required artifacts present; all software checks PASS; no raw/epoch arrays in repository artifact bundle; no hardware streams left open | 1 pass | DONE |
| 6 | Selectively stage M37 files, review staged diff/secrets/sizes, commit and push authorized branch | git diff --cached --check; git diff --cached --stat; git diff --cached --name-only; git commit; git push -u origin HEAD | Only M37-specific files staged; push without force; verify remote hash equals local HEAD | PASS; code checkpoint `ad5885a` pushed and remote hash verified | DONE |
## Runtime and recovery

- Exact executable/runtime and version: `C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe`, CPython 3.9.13.
- Inputs: M37 config snapshot; slot mapping 0→7.2, 1→9, 2→12; precommitted configurable formal schedule; Quest selection transport TCP/11001, telemetry TCP/11002 if needed, IPC TCP/12021 only when current path requires it; ND8 COM11.
- Checkpoint cadence: append WORKLOG and update state at baseline, implementation, each verification, hardware start/stop, final audit and Git checkpoint.
- Last-green verification: exact command, exit code, source HEAD, diff/status fingerprint, result path.
- Resume instructions: read this plan, `state.json`, and append-only `worklog.jsonl`; recheck HEAD/branch/status and preserve the five dirty tracked files and every pre-existing untracked path.
- Blocker policy: two bounded attempts per external connection. Stop hardware after disconnection, do not fabricate samples, preserve completed data, close handles, and continue independent software work. Only user interaction required by physical device authorization/app absence is escalated.

## Completion conditions

- Definition of complete: required M37 artifacts exist, relevant software checks pass, all available hardware tests are safely closed out, exact tomorrow commands/checklist are delivered, M37-only commit is pushed and remote hash verified.
- Conditions that mean BLOCKED: hardware unavailable after bounded attempts, a human authorization dialog, Quest app absent with only USB install possible, or GitHub auth denied after all software and staging work is complete.
- Conditions that mean NOT ATTEMPTED: physical laser trigger and any EEG quality/classifier evaluation.
- Human validation required: tomorrow's 3–5 real laser-trigger smoke trials before formal/session_001.
- External boundary evidence: Quest TCP probe had no peer and no m19_research_ready; COM11 open/start was rejected by automatic approval review, so the serial device remained unopened. Continue with software work and Git checkpoint; request explicit COM11 authorization only after every independent software task is complete.
- Morning handoff path: `docs/agent/overnight/runs/m37-prep-night-20261005/handoff.md`; user checklist `research_analysis/m37_acquisition_prep_20261005/attempt-01/M37_TOMORROW_OPERATOR_CHECKLIST.md`.
