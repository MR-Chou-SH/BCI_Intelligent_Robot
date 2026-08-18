# M5 Development and Experiment Log — Stimulus Timing / EEG Trigger Synchronization

Date: 2026-08-18
Status: Completed / PASS at the software/runtime and real end-to-end association evidence level
Branch: `feature/m5-stimulus-eeg-association`

## Scope and evidence boundary

M5 followed the verified M4 three-target, shared-frame, frame-driven Quest baseline. Its purpose was to connect a software stimulus event to the correct software time domain and then to an ND8 post-sync packet/sample estimate. M5 did not implement EEG classification, vision, robotic-arm control, scene understanding, physical optical measurement, or hardware-exact sample timing.

The final evidence chain is:

`Quest software stimulus event → Quest-PC software clock mapping → ND8 stable post-sync packet → software-derived EEG sample estimate`

The following remain explicitly unverified throughout this log: ND8 hardware timing, physical optical onset/phase, the physical display frequency, ND8 hardware sample-anchor semantics, and hardware-exact EEG sample timing.

## Why M5 was required after M4

M4 proved that three SSVEP targets could be rendered using one shared frame origin and independent integer frame periods. That is necessary for a stimulus, but it does not tell the EEG pipeline which samples correspond to the software start or stop event. Moving from Quest SSVEP to EEG sample association crosses several time domains:

1. Quest render-frame time and Quest monotonic time;
2. PC trigger-server monotonic time;
3. ND8 SDK timestamp time, which can change domain during startup;
4. PC receive time for ND8 packets;
5. an estimated packet-relative and global sample index.

M5 therefore treated raw Quest events, Quest-PC synchronization evidence, ND8 raw EEG, ND8 packet metadata, and derived association as separate append-only evidence streams.

## M5.0 — architecture audit and design

The legacy Drone2.1 system sent a `TIME:<timestamp>` stimulus marker and used an ND8 timestamp to select an EEG window. Its implementation treated the packet timestamp as a first-sample millisecond timestamp. That is useful historical context, but it is not sufficient protocol evidence for this project: the timestamp anchor was not independently verified, and the old system did not express startup domain transitions, continuity failures, or uncertainty as machine-readable evidence.

M5 therefore adopted the ADR-003 evidence hierarchy:

- a verified hardware sample counter would be strongest;
- a verified device-timestamp-to-PC mapping would be next;
- PC packet-receive time would be an explicitly lower-confidence fallback;
- every result would retain mapping method, quality, continuity, uncertainty, and hardware-timing flags.

The architecture also required network transport to remain separate from frame scheduling. A network event may be delayed or disconnected; it must not control the Quest stimulus frame algorithm.

## M5.1 — local stimulus event records

M5.1 introduced explicit trial semantics on an independent scene:

- `session_started`;
- `stimulus_started_software`;
- `stimulus_stopped_software`.

The scene used a frame-scheduled trial, a temporary all-black idle state, explicit stop behavior, and append-only Quest JSONL timing records. Start and stop were defined by Unity software/render scheduling. The event was not treated as a measured physical optical onset or offset.

Quest 3 acceptance passed: the scene launched in immersive OpenXR/MR, passthrough and three targets remained visible and world-fixed, the three flicker rates remained differentiated, stop returned to persistent black idle, and the trial logs were ordered, complete, and not truncated. The runtime reported approximately 72 Hz. This was a software/runtime observation, not an optical measurement.

M5.1 deliberately kept the network out of stimulus timing. A network failure must not change the frame-driven stimulus state.

## M5.2 — Quest-PC transport and clock mapping

M5.2 added a persistent TCP connection carrying newline-delimited JSON. It preserved the original Quest event, added PC receive timestamps, returned an explicit ACK, and recorded transport diagnostics separately.

Repeated four-timestamp samples used the standard Quest-PC exchange:

`q1 → p2 → p3 → q4`

Accepted samples produced an affine software mapping from Quest monotonic time to PC monotonic time. The raw samples and fit residual were retained. Disconnect, reconnect, active-trial server shutdown, and server restart were exercised; stimulus continuation remained independent of network availability.

The result was a software clock bridge, not a real-time EEG trigger and not proof of physical optical onset.

## M5.3 — ND8 acquisition and timestamp evidence

### Vendor SDK environment

The Neurodance 7.3 SDK contains `core.pyd` compiled for the CPython 3.9 ABI. The working vendor environment was kept outside the repository:

`C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe`

It was verified as Windows x64 CPython 3.9.13 with `neuro-dance 7.3`, NumPy 2.0.2, and pyserial 3.5. The vendor wheel, native extension, and runtime were not copied into Git.

### First ND8 acquisition

Using COM11 at explicitly configured 1000 Hz, the SDK produced 8-channel × 200-sample packets with approximately 200 ms timestamp cadence. The first recording was constant across channels/samples while the SDK reported a not-ready dongle state. This established serial transport and callback cadence only. It did not establish physiological EEG, packet timestamp semantics, or stimulus association.

### Host MAC readiness

The adapter was changed to perform `host_mac_info()` after serial transport starts and wait for `host_mac_received()` before configuring the sampling rate and enabling EEG. This handshake prevents the adapter from treating serial availability as device readiness. The host MAC was retained only as a suffix in validation summaries.

### Real EEG and timestamp warnings

With the device correctly worn and four formal EEG electrodes connected, Python received changing values on part of the observed channels. The native SDK also printed repeated `timestamp not sync!!!` warnings. These warnings could not be assigned to individual packet IDs without modifying the native SDK, so the formal timeline used packet timestamp and continuity evidence rather than console text alone.

### 90-second SDK-to-PC mapping validation

The 90-second validation contained 449 packets. Around packet 50→51, the SDK timestamp changed from a non-Unix startup domain to Unix milliseconds. The transition was treated as a segment boundary; no fit was allowed to cross it.

The post-transition segment contained 398 packets and approximately 79.4 seconds. Its software mapping evidence included a PC receive UTC minus SDK timestamp median offset of approximately 311 ms, P95 approximately 353 ms, software-fit drift approximately -75 ppm, and approximately 20.1 ms RMS receive-time residual. The remaining 199/201 ms differences were treated as millisecond quantization/cadence variation. This was software-level mapping evidence only.

### Runtime post-sync association gate

The historical observation became a runtime gate with explicit states:

`pre_sync → transition → stable_unix_ms → association_ready`

and a separate `continuity_lost` invalid state.

The gate requires actual evidence, not packet 51 or a fixed sleep:

- Unix-millisecond SDK timestamp;
- continuous packet sequence and shape;
- no timestamp jump, rollback, or severe continuity issue;
- at least 10 packets;
- at least 1.8 seconds of stable timestamp span;
- cadence error no greater than 2 ms.

Events before the recorded ready time, during transition, or after continuity loss are invalid/unavailable and raw evidence remains preserved.

### ADR-003 and sample anchor

The raw evidence streams remain separate. The association output records packet sequence, SDK timestamp, PC mapping, timestamp segment, continuity, sample offset, global index estimate, residual, uncertainty, and timing flags.

The vendor demo describes the callback timestamp as “first point time”. The old Drone2.1 code also assumes a first-sample timestamp. Neither was promoted to a verified hardware protocol fact. The formal output therefore uses `software_derived_estimate`, with `hardwareTimingVerified=false` and `physicalOpticalTimingVerified=false`.

## First real Quest→EEG association and debugging

The first end-to-end trial was run only after the ND8 gate and Quest-PC mapping appeared ready. Quest events and ND8 continuity looked correct, but all four stimulus associations were invalid.

The root cause was a cross-module clock-domain error:

- ND8 local packet receive time used `monotonic_ns()`;
- the PC trigger server used `perf_counter_ns()`.

Both clocks are monotonic within their own process, but their origins are not interchangeable. The error was difficult to see because each subsystem independently appeared healthy: ND8 continuity was normal, Quest-PC mapping was available, and raw stimulus events were valid. It surfaced only when the two timing bridges were composed.

The fix aligned ND8 packet receive timing with the trigger server’s `perf_counter_ns()` domain:

`882d978 fix(eeg): align ND8 receive clock with trigger server`

The engineering lesson is:

> A monotonic clock is not automatically the same cross-module clock domain.

## Offline verifier validity and chronology fixes

The first failed session initially produced live/offline agreement even though every stimulus association was invalid. This exposed a validity bug: deterministic agreement is necessary, but not sufficient for a PASS. The verifier was changed to require a complete set of valid stimulus associations:

`1caecae fix(eeg): require valid offline stimulus associations`

The second real trial then produced valid live associations, but offline replay initially disagreed because it loaded future packet evidence before recomputing an earlier event. Offline verification must reconstruct gate state, timestamp segment, continuity, and association context in original PC receive order. The replay was corrected and a regression test was added:

`65714da fix(eeg): replay packet evidence chronologically offline`

## Second real Quest + ND8 validation

Successful session:

`D:\EEG_Study\m5_3\m5_3-association-20260818T140324Z-aeec2e3e`

The configured trial ran for 2160 frames at approximately 72 Hz, approximately 30 seconds. The events were ordered and both were valid.

| Event | ND8 packet | Estimated offset | Estimated global sample | Valid |
|---|---:|---:|---:|---|
| `stimulus_started_software` | 433 | 94 | 86694 | true |
| `stimulus_stopped_software` | 583 | 96 | 116696 | true |

Quest-PC affine residual RMS was approximately 5.86 ms at start and 5.99 ms at stop. ND8 packet-to-PC software mapping residual was approximately 20.1 ms and the reported overall software uncertainty was approximately 20.6 ms.

ND8 remained in post-sync segment 4 with `association_ready` and continuous continuity. The session contained 1563 packet metadata records.

Final offline verification reported:

- `rawEvidenceErrors: []`;
- `liveOfflineMismatchKeys: []`;
- `validStimulusAssociationCount: 2`;
- `completeValidStimulusAssociation: true`;
- `passed: true`.

The final targeted M5 test suite passed 32/32.

## Potential research / paper-relevant contributions

The following are engineering methods or candidate discussion points, not unverified claims of novelty. A literature comparison is required before calling any item a contribution:

1. explicit handling of an ND8 startup timestamp-domain transition;
2. a runtime post-sync association gate based on observed timestamp and continuity evidence;
3. a dual-clock bridge `Quest → PC ← ND8`;
4. evidence-aware packet/sample association with quality and uncertainty;
5. online derived association plus independent offline replay;
6. uncertainty-aware alignment rather than hidden precision;
7. separation of software, hardware, and optical timing evidence;
8. network-independent frame-scheduled XR stimulation.

## Engineering lessons

- Transport success is not signal validity.
- A timestamp existing does not mean its sample-anchor semantics are known.
- A monotonic clock is not automatically the same clock domain across modules.
- Live/offline agreement is not correctness unless validity is also required.
- Raw EEG and raw synchronization evidence must remain append-only and separate from derived results.
- Startup-unstable data must not silently enter a formal trial.
- Uncertainty must be recorded rather than hidden.
- Real-device failure is valuable evidence for finding cross-module architecture defects.

## Git checkpoints

- `3059ca2 feat(eeg): validate ND8 acquisition and timestamp mapping`
- `e27dfbc feat(eeg): add M5.3 stimulus association pipeline`
- `882d978 fix(eeg): align ND8 receive clock with trigger server`
- `1caecae fix(eeg): require valid offline stimulus associations`
- `65714da fix(eeg): replay packet evidence chronologically offline`

## M5 conclusion and limitations

M5 is completed at the software/runtime and real end-to-end association evidence level. It does not establish physical optical timing, physical optical phase, ND8 hardware timing, a verified hardware packet sample anchor, or hardware-exact EEG sample timing. Future M6 work may address online EEG classification; this log does not define its algorithm or architecture.
