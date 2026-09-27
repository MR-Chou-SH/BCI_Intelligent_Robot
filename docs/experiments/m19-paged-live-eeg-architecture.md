# M19 Paged Live EEG Selection — Architecture and Manual Handoff

## Status and evidence boundary

**Implementation status:** code and software acceptance complete; Unity EditMode and Quest visual acceptance remain manual. M16 `PagedQueueV1` is the current UI baseline. M13.10 Showcase is retained only for legacy regression checks.

The run used synthetic input, immutable historical EEG replay, injected fake live-source tests, and local loopback TCP tests. It did not open a COM port, operate ND8, build/run Quest, or operate a physical robot. Software timestamps and stimulus epochs are not evidence of physical optical timing.

## Architecture

```text
Quest / Unity — M16 PagedQueueV1
  gaze Trigger Band (0.8 s dwell, one activation per enter)
       │ frozen pageId + pageEpoch + active slot/TargetId map
       ▼
existing M8 newline-delimited JSON TCP transport
       │ eeg_decode_request / explicit eeg_decode_ack
       ▼
PC M19 orchestrator
  M19TrialRegistry → selected EEG backend → class-only result
       │ classIndex 0/1/2; no TargetId resolution on PC
       ▼
Quest validates the live trial and frozen page epoch,
resolves class to its frozen TargetId, then updates the global queue
       │ Submit: exact user order, chunked internally in groups ≤ 3
       ▼
existing M9 batch dispatcher → virtual logical block IDs → MuJoCo adapter
       │
       └── M13.6 execution telemetry is associated with selection IDs
```

Quest owns page navigation, page epoch, the frozen mapping, and `TargetId` resolution. The PC validates the request snapshot and sends only a class decision; it never sends a resolved `TargetId` back. Selected targets stay blue and static across page changes. Undo restores only the last global selection, without compacting pages. Navigation, Undo, Submit, dynamic candidate append, and session reset terminate a pending trial before changing state; late responses are rejected by selection/page/epoch identity.

## State and wire contract

The PC registry permits one active M19 trial. A request must use contract `m19_decode_request_v1`, protocol version 1, a unique `trialId` and `selectionId`, page identity, and one to three active slots. Class indices map to 7.2, 9, and 12 Hz at slots 0, 1, and 2. Inactive slots are omitted from the active mapping and a decoder result for an inactive slot is rejected.

```text
BROWSE
  └─ trigger + frozen snapshot → REQUEST_SENT
       ├─ rejected ACK → BROWSE (no decode)
       └─ accepted ACK → WAITING_FOR_DECODER
            ├─ accepted class + matching page epoch → QUEUE_UPDATED → BROWSE
            ├─ no decision / invalid or inactive class → fail closed → BROWSE
            └─ abort, page change, or stale identity → invalidate → BROWSE
```

The shared Unity/Python fixture is `m7_unity6000/Assets/Resources/BCI/M19/m19_decode_request_v1.json`. Results contain `classIndex`, trial/page/epoch identity, and decision status; they do not contain `TargetId`. An explicit ACK follows each validly identified request. Malformed requests with a selection identity receive a rejected ACK and do not reach the EEG backend.

## EEG backends and timing

- **Synthetic:** deterministic class 0/1/2 and no-decision outcomes; it supports complete queue and transport tests without device access.
- **Historical:** reads the archived M13.7 raw packet/session files immutably and runs the current NumPy FBCCA extraction with the existing 0.5 s onset guard and 1.5 s evidence window. The current request’s active slots determine resolution; historical labels or old color mappings do not. This is source-swap software evidence, not new human accuracy evidence.
- **Live ND8 software wiring:** reuses the M13.7 `HumanSessionRecorder`, `GoldenPacketPipeline`, `LiveND8Source`, and channel-admission path. Construction/open is gated by explicit `--confirm-live-human`; injected fake-source tests verify the gate and close path. This run did not confirm or open a real device.

`demo_preview` may leave the current-page stimuli flickering while browsing. Trigger-to-decision duration in this mode is not physical SSVEP onset-to-decision latency. `research_strict` starts and ends one formal **software** stimulus epoch at trial boundaries and requires its software onset timestamp. Both modes preserve software timing metadata; neither establishes physical display timing. In the live software path, the sample anchor is the first packet received at or after the PC software trigger. Real display refresh, gaze-transfer time, packet timing, and EEG sample alignment still require experimental verification.

## M9 mapping and acceptance evidence

The explicit virtual catalog mapping is Yellow → `block_sim_04`, Blue → `block_sim_03`, Green → `block_sim_02`, and Red → `block_sim_01`. The free-order synthetic run selected and dispatched Blue → Red → Green, preserving the same order through the M9 dispatcher; Undo restored Yellow. The report associates six executing/completed telemetry records with the three committed selections.

Evidence is under `artifacts/m19_paged_live_eeg_20260922T105007Z/`:

- Synthetic queue, M9 dispatch, and telemetry acceptance: `software_acceptance/synthetic-e2e_attempt_2/synthetic-e2e.json`.
- Historical source swap: `software_acceptance/historical-e2e/historical-e2e.json`; the archived source SHA-256 before/after was identical.
- Python test logs: `test-logs/phase_d_posthardening_attempt2.log`, `phase_g_repo_verifier_tests_attempt1.log`, `phase_g_sync_attempt1.log`, `phase_h_m16_m9_dispatch_attempt1.log`, `phase_h_m13_m18_regression_attempt1.log`, `phase_h_m13_6_visual_sync_mujoco_attempt1.log`, and `phase_h_m13_10_legacy_all_mujoco_attempt1.log`.
- Final run summary and limitations: `final/final_summary.json` and `final/final_report.md`.

The CPython 3.9.13 regression run passed 123 M8/M9/M16/M19 and synthetic EEG tests, 5 M5 synchronization tests, and 105 M13/M18 tests. Seven MuJoCo-dependent cases in `test_m13_6_visual_sync` were skipped there, then all 19 cases in that module passed in the existing project `.venv`, which already contains MuJoCo. All 27 M13.10 legacy tests also passed in `.venv`. The PowerShell wrapper `scripts/agent/verify.ps1` was not executable under the machine’s current execution policy; its reviewed Python checks were invoked directly with the configured interpreter. Unity EditMode tests were not run because Unity is already open and no GUI/CLI action was authorized.

## Manual Unity and Quest checklist

Use the existing open Unity Editor; do not start another instance. In `m7_unity6000`, open the enabled `M9VirtualManipulation` scene if it is not already active. Run the EditMode tests listed below and record the result before any device validation.

1. Run `BciGazeDwellStateTests`, `BciPagedBrowsePresentationTests`, `BciPagedSelectionQueueTests`, and `BciPagedWorldSpaceGazeTests`; then run the full EditMode suite to verify legacy M8/M9 behavior.
2. Check dynamic pagination for 1–7 candidates, left-to-right slot assignment, and a final page containing one or two candidates. Confirm the Trigger Band is above the Page X/Y label.
3. Verify gaze under 0.8 s does nothing; at 0.8 s it emits one request; remaining on the band does not retrigger; leaving and re-entering permits a new trigger only after the trial is terminal.
4. Verify a selected target is blue/static on its original page and remains selected after Next/Previous. Verify Undo restores the last target without page compaction.
5. Start a pending trial, navigate away, and confirm abort precedes page-epoch change; deliver a late result and confirm it cannot select the same slot on the new page.
6. Submit a cross-page queue and verify the exact global order reaches the existing M9 batch path; verify an empty queue is a no-op and internal batches contain at most three items.
7. Only after Unity checks pass and separate authorization is given, perform the human Quest/ND8 preflight and first physical user-triggered smoke. Verify channel admission and raw-first recording, then test one trigger, one decision/no-decision, abort, and clean source close. Do not treat software timestamps as proof of optical timing.

No Quest build, COM/ND8 access, or physical operation is authorized or claimed by this run.
