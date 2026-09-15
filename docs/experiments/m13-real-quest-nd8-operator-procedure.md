# M13 Real Quest + ND8 Engineering Acceptance Procedure

This is a future operator procedure only. It was prepared by the software campaign and must not be executed automatically. It is an engineering acceptance, not a human-performance experiment and not a physical-timing calibration.

## Before starting

The operator confirms that the approved session plan, Quest scene/build, ND8 runtime and any controller/GUI are ready. Do not change the frozen three-class mapping: slot 0/1/2 remains 7.2/9/12 Hz, and Quest remains the authority for frozen `TargetId` resolution.

Codex/software responsibilities:

- run the PC-side readiness checker;
- start the explicitly selected M13 live/replay runner and existing M8 selection transport;
- record append-only logs for raw score snapshots, M12 fused snapshots, M13 decision state and M8 ACKs;
- parse the machine-readable summary and check exactly-once final submission.

User/operator responsibilities:

- put on and prepare the Quest headset and ND8 according to the existing approved procedure;
- perform any required GUI, controller and visual confirmation actions;
- confirm that the Quest visual scene and SSVEP presentation are usable;
- stop the session if wearing, signal, transport or visual conditions are unsafe or invalid.

## Software preflight

From the repository root, using the existing environment and without installing dependencies:

```powershell
.venv\Scripts\python.exe -B -m integration.m13_readiness --summary-path m13-readiness-summary.json
```

The checker only imports modules, validates required files/mappings, checks that TCP 11001 can be locally bound and released, and imports the ND8 adapter without instantiating it. It must report `overallStatus=PASS`. It does not open COM11, connect Quest or collect EEG.

Before a live run, the user explicitly starts the existing approved Quest/ND8 path and the M13 opt-in runner. The M13 mode must be explicit; the normal M6/M8 baseline remains the default. Record the run ID and software commit in the session manifest.

## Minimum live acceptance observations

For at least one controlled engineering trial, the log parser should show:

1. live ND8 score snapshots at the existing formal window/step semantics;
2. an M12 fused trajectory containing raw EEG score, context prior, softened context and normalized fused evidence;
3. M13 threshold, margin, raw-EEG-confirmation state, consecutive count, stop/no-stop and reason;
4. either `early_stable_fused_evidence` or an explicit `full_window_fallback` / `no_decision`;
5. one final class only when `decisionMade=true`;
6. Quest frozen selection ACK for the accepted final decision;
7. no duplicate final decision event.

The expected stop time is an algorithmic software window time. Do not call it physical display latency, hardware EEG latency or end-to-end latency without separate timing evidence.

## Abort and evidence rules

No-decision must suppress fabricated selection. A context-favored target that disagrees with the current raw EEG top must not early-stop. Preserve raw EEG and all derived logs; do not overwrite or tune thresholds after inspecting outcomes. Report live Quest/ND8 acceptance separately from the software/replay PASS.

This procedure does not authorize a physical robot action, a human research protocol, threshold optimization, learned stopping, adaptive lambda or M14.
