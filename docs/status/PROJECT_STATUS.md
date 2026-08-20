# Project Status

Last updated: 2026-08-20

## Overall Phase

M6 — ND8 EEG Decoder and Cross-Session Robustness Validation

## Completed Milestone

### M0 — Project Initialization

Status: Completed

Goal:

建立正式项目目录、Git版本控制、项目文档体系和参考资料结构，为后续Quest 3开发做准备。

## Completed Preparation

- Meta Quest 3 available
- International Unity installed
- Neurodance ND8 SDK/reference materials available
- Existing EEG-controlled drone demo available
- Initial analysis of drone demo data flow completed
- Overall project architecture discussed

## M0 Completion

- Formal repository created
- Standard directory structure created
- Project management Markdown baseline established
- Local Git repository initialized
- Legacy reference materials organized

## Not Started

### VR Stimulus

- Unity Quest project
- Quest deployment
- Passthrough
- fixed square
- flicker timing
- three-target stimulation
- EEG synchronization

### Vision

Not started.

### EEG

Reference implementation analyzed.

Formal EEG module not yet created.

### Robot Arm

Not started.

Hardware/control interface details still need to be documented.

### Integration

Not started.

## Completed Milestone

### M1 — Minimal Unity Quest Application

Status: Completed

M1 progress:

- M1.0 — Development Environment Audit: Completed
- M1.1 — Quest Android Environment Setup: Completed
- M1.2 — Create Minimal Unity Quest Project: Completed
- M1.3 — Minimal Quest XR Configuration: Completed

Verified Unity and OpenXR stack:

- Unity: 6000.5.8f1
- XR Plug-in Management: 4.7.0
- OpenXR: 1.17.1
- Meta Quest Support: Enabled
- Oculus Touch Controller Profile: Enabled
- Build target: Android / ARM64
- OpenXR Project Validation: 0 Issues at validation time

Verified development environment:

- Unity: 6000.5.8f1
- Android Platform Tools: 36.0.0
- Android NDK: 27.2.12479018 (r27c)
- OpenJDK: 17.0.18
- adb: 1.0.41
- Meta Quest 3 adb status: `device`

Physical Meta Quest 3 verification:

- APK Build: Successful
- Build And Run: Successful
- Unity application launched successfully on Meta Quest 3
- Minimal pure VR scene rendered successfully
- Cube and Plane were visible with basic lighting
- Head rotation correctly controlled the Unity camera view
- Cube remained fixed in virtual-world coordinates and did not move with the headset view

Scope:

- create the formal Unity project under `vr_stimulus/`;
- confirm and record the full Unity editor version;
- configure the minimum Android/Quest build environment;
- confirm the necessary XR foundation configuration;
- build and run a minimal application on the physical Meta Quest 3;
- record the important versions and configuration actually used.

M1 does not implement:

- SSVEP flickering;
- three-target stimulation;
- EEG connectivity;
- computer vision;
- robotic-arm control.

Definition of done:

- a formal minimal Unity project exists under `vr_stimulus/`;
- the exact Unity version and important XR configuration are recorded;
- the minimum Android/Quest build environment is configured;
- the minimal application successfully builds and runs on the physical Meta Quest 3;
- the verified build procedure is documented;
- the verified state is committed to Git.

## Completed Milestone

### M2 — Passthrough + Fixed Virtual Cube

Status: Completed

### M2.1 — Passthrough Baseline

Status: Completed

Implementation:

- Added `Assets/Scenes/M2_1_Passthrough.unity` as the Android startup scene.
- Preserved the existing XR Rig and Main Camera tracking configuration.
- Added `ARSession` and `ARCameraManager` for the Meta OpenXR passthrough baseline.
- Enabled the active Android OpenXR features required by M2.1: Meta Quest Support, Oculus Touch Controller Profile, Meta Quest Camera (Passthrough), Meta Quest Session, and Composition Layers Support.
- Used Unity 6000.5.8f1 with XR Plug-in Management 4.7.0, OpenXR 1.17.1, Unity OpenXR: Meta 2.5.1, AR Foundation 6.5.0, and Composition Layers 2.5.0.
- Kept the Android target on ARM64 with minimum API level 32.

Physical Meta Quest 3 verification:

- APK Build: Successful
- Build And Run / deployment: Successful
- Application entered immersive OpenXR mode rather than a Horizon OS 2D panel.
- Passthrough reality view rendered correctly and followed head rotation.
- Cube, Plane, and Skybox were not visible in the passthrough baseline.
- No unexpected camera or scene permission prompt appeared.
- Application remained stable without crashes or automatic exit.
- Functional Acceptance: PASS

Passthrough color A/B verification:

- Main Camera remained `Solid Color` with background alpha `0`.
- Changing only its background RGB from `(0.19215687, 0.3019608, 0.4745098)` to `(0, 0, 0)` removed the visible light-blue veil.
- Root cause: non-zero RGB values in the fully transparent camera clear color were still contributing to the final passthrough composition.
- The corrected application passthrough was visually equivalent to Horizon OS passthrough in the same environment.

Known non-blocking follow-up:

- Horizon OS may show “Application name unavailable” for the sideloaded running application.
- This does not block the validated immersive passthrough baseline and is intentionally deferred; no custom Manifest or Activity was added.

### M2.2 — Fixed Virtual Cube

Status: Completed

Implementation:

- Created `Assets/Scenes/M2_2_FixedCube.unity` from the verified M2.1 passthrough baseline.
- Retained the existing XRRig, AR Session, and ARCameraManager configuration.
- Kept the Main Camera clear mode on `Solid Color` with background RGBA `(0, 0, 0, 0)`.
- Enabled one static Cube as a scene-root object, outside the Camera and XRRig hierarchies.
- Set the Cube world position to `(0, 1.5, 3.0)` and scale to `(0.4, 0.4, 0.4)`.
- Enabled the existing Directional Light only for basic illumination of the virtual Cube.
- Did not add interaction, spatial anchors, Scene API, MRUK, or SSVEP behavior.

Physical Meta Quest 3 verification:

- APK Build and Build And Run: Successful
- Immersive OpenXR / MR and passthrough remained functional.
- Passthrough color remained correct without the previous light-blue veil.
- The virtual Cube was clearly visible in the passthrough view.
- The Cube remained fixed in Unity world coordinates and did not follow head rotation.
- Head and body movement produced the expected three-dimensional spatial relationship and parallax.
- Plane and Skybox were not visible.
- No unexpected permission request appeared.
- Application remained stable without crashes or automatic exit.
- Manual Quest 3 Acceptance: PASS
- Definition of Done: Quest 3 physical verification passed.

## Completed Milestone

### M3 — Single SSVEP Target

Status: Completed

M3 progress:

- M3.0 — Pre-implementation Audit & Design: Completed
- M3.1 — Static Single Target + Frame-driven Flicker: Completed
- M3.2 — Quest 3 Physical Visual Acceptance: Completed / PASS
- M3.3 — Refresh-rate / Frame-timing Logging and Verification: Completed / PASS

M3.1 implementation:

- Created `Assets/Scenes/M3_1_SingleSSVEP.unity` from the verified M2.2 baseline without modifying M1, M2.1, or M2.2 scenes.
- Reused the world-fixed Cube at position `(0, 1.5, 3.0)` and scale `(0.4, 0.4, 0.4)`.
- Added a Built-in Render Pipeline `Unlit/Color` opaque material so black/white states do not depend on scene lighting.
- Added a single-purpose `FrameDrivenStimulus` component with `framesPerHalfCycle = 3`.
- The stimulus state is derived from `Time.frameCount` in `LateUpdate`, not from a wall-clock toggle timer.
- Added runtime XR display refresh-rate observation, derived software-frequency reporting, transition diagnostics, and Unity-side frame-anomaly warnings.
- Android Build and Quest 3 deployment: Successful.
- Physical acceptance confirmed passthrough without the previous blue veil, visible black/white flicker, world-fixed placement, correct head/body parallax, scene cleanliness, and stable operation.
- Quest XR runtime reported `72.000 Hz`; `Application.targetFrameRate = -1` and `framesPerHalfCycle = 3` give a derived software stimulus frequency of `12.000 Hz`.
- No `SSVEP Unity-frame anomaly` was observed during the captured M3.2 log interval.
- The derived 12 Hz value has not been verified as a physical optical frequency using a photodiode or high-speed camera.
- M3.2 Physical Visual Acceptance: PASS.

M3.3 timing verification:

- Added an independent `SSVEPTimingDiagnostics` component with a 30-second measurement window; it observes timing without changing stimulus state.
- The 30.006-second Quest 3 run reported a stable XR display refresh rate of `72.000 Hz` with zero refresh-rate changes.
- Observed 2162 Unity frames with a mean interval of `13.927 ms`, an approximate mean rate of `71.803 FPS`, and one software-side long Unity frame.
- Unity frame-index gap count was `0`; this does not prove zero physical display dropped frames.
- XR present counter was unavailable on the current runtime.
- XR dropped counter was available and returned start `7`, end `14`, and raw delta `7`; this must not be interpreted as proof of seven physical display frames being dropped.
- Software timing verification does not equal physical optical frequency verification, and EEG-valid stimulus timing remains unverified.
- M3.3 Timing Verification: PASS.

## Completed Milestone

### M4 — Three SSVEP Targets

Status: Completed

M4 progress:

- M4.0 — Pre-implementation Audit, Frequency Design & Architecture: Completed
- M4.1 — Static Three-target Scene + Shared Frame-driven Stimulation: Completed
- M4.2 — Quest 3 Physical Acceptance: Completed / PASS
- M4.3 — Multi-target Timing Verification: Completed / PASS

M4.1 implementation:

- Created the independent `Assets/Scenes/M4_1_ThreeSSVEP.unity` scene from the verified M3 baseline without modifying the M3 scene.
- Added three Scene Root Cube targets at `(-0.8, 1.5, 3.0)`, `(0, 1.5, 3.0)`, and `(0.8, 1.5, 3.0)`, each using the shared `SSVEP_Unlit` material.
- Added one `MultiTargetStimulusController` with a single `commonStartFrame` and shared `globalStimulusFrame` for all targets.
- Configured `target_left`, `target_center`, and `target_right` with `framesPerHalfCycle` values `5`, `4`, and `3`; at 72 Hz these derive to software frequencies `7.2`, `9`, and `12 Hz`.
- All phase offsets are `0`; M4.1 remains frequency-coded only.
- Runtime refresh rate is observed read-only and is not forced; a non-72 Hz runtime produces a warning while stimulation continues with unchanged integer-frame parameters.
- Unity 6000.5.8f1 compile and Android Build succeeded; Quest 3 M4.2 physical acceptance passed.

M4.2 physical acceptance:

- Quest 3 Build And Run succeeded with passthrough, three-target visibility, left/center/right layout, flicker differentiation, world-fixed behavior, spatial parallax, scene cleanliness, stability, and basic visual tolerability all accepted.
- Runtime reported `72.000 Hz`, `Application.targetFrameRate = -1`, and one shared `commonStartFrame = 322` for all three targets.
- Runtime-derived software frequencies were `7.2`, `9`, and `12 Hz`; these are not physical optical measurements.

M4.3 implementation:

- Added one global `MultiTargetTimingDiagnostics` component with a 30-second measurement window.
- Added read-only controller snapshots for the common/global frame and per-target transition counts without changing the stimulus state algorithm.
- The diagnostics compare observed transition deltas with exact frame-index-derived expectations for each target and record refresh, Unity timing, frame gaps, and XR counters.
- Quest 3 runtime verification completed at a stable reported `72.000 Hz`; shared global-frame consistency passed with no Unity frame-index gaps.
- Exact observed/expected transition deltas matched for left `432/432`, center `540/540`, and right `720/720`.
- The dropped-frame API reported a raw delta of `7`; this runtime counter and the one long Unity frame are software/runtime diagnostics, not proof of physical optical frame loss.
- M4.3 verifies software/runtime scheduling only and does not replace physical optical frequency or phase measurement.

## Completed Milestone

### M5 — Stimulus Timing / EEG Trigger Synchronization

Status: Completed / PASS

On the verified three-target frame-driven SSVEP baseline, design and implement stimulus start/stop timing records and an EEG trigger synchronization interface.

Current substage:

- M5.0 — Existing EEG / Trigger Architecture Audit & Synchronization Design: Completed
- M5.1 — Unity Stimulus Event Model and Local Timing Records: Completed / PASS
- M5.2 — Quest-PC Trigger Transport and Clock Synchronization: Completed / PASS
- M5.3 — EEG Sample Association and Offline Trigger Alignment: Completed / PASS

M5.3 ND8 acquisition runtime prerequisite:

- The vendor `neuro_dance` 7.3 SDK requires an isolated external Windows x64 CPython 3.9 environment because its `core.pyd` depends on the CPython 3.9 ABI.
- The external environment smoke test passed for `pyserial==3.5`, NumPy, `neuro_dance.core`, `neuro_dance.nd_device_process`, and the project acquisition adapter import. No serial port or device operation was performed.
- Vendor SDK files, native extensions, and the Python runtime remain outside the Git repository. Real ND8 packet/timestamp validation is still required before hardware-timed sample association.

M5.3 first serial acquisition validation (2026-08-18):

- The vendor SDK opened `COM11` and produced 72 callback packets during a 15-second, explicitly configured 1000 Hz run. Packet shape was consistently 8 channels × 200 samples; SDK timestamp deltas were exactly 200 ms and the metadata timeline reported no continuity anomalies.
- The complete raw recording was nevertheless a single constant value across every channel, sample, and packet while the SDK also reported that the dongle was not ready. This confirms serial transport and packet cadence only; it does not validate physiological EEG, packet first-sample semantics, or real stimulus-to-sample association.
- The raw and metadata session is preserved outside Git under the configured EEG study root. A user-performed dongle/host readiness check is required before another acquisition validation.

M5.3 second serial acquisition validation (2026-08-18):

- The adapter now performs the vendor `host_mac_info()` query after serial transport starts and waits for the documented `host_mac_received()` callback before it configures 1000 Hz or enables EEG. The callback was observed (only the final four MAC characters were retained); the initial SDK not-ready heartbeat occurred before that callback and did not block streaming.
- The 15-second run produced 74 packets of 8 channels × 200 samples, but raw data remained a single constant value. One SDK timestamp interval was 136 ms rather than the expected 200 ms; this is now recorded by the timeline as `timestamp_delta_mismatch`. Valid physiological EEG and real sample association remain unverified.

M5.3 third serial acquisition validation (2026-08-18, device correctly worn):

- The 15-second host-MAC-ready run again produced 74 packets of 8 channels × 200 samples. Packet timestamps were 200 ms apart except for a local 201/199/201 ms sequence; the prior 136 ms interval did not recur. PC receive timing remained approximately 200 ms with normal host-side jitter.
- Python received non-constant raw values on channels 1 and 4, while the remaining six channels were still constant at the observed placeholder value. The SDK console also emitted repeated `timestamp not sync` warnings. This demonstrates partial changing data flow but does not yet establish a valid full eight-channel EEG stream or hardware-timed sample association.

M5.3 90-second SDK-to-PC timestamp mapping validation (2026-08-18):

- The session contained 449 packets at 8 channels × 200 samples. Packet 50→51 changed from a non-epoch SDK timestamp domain to the Unix-millisecond domain, a severe discontinuity that must be treated as a segment boundary rather than silently fitted across.
- The following 398-packet / approximately 79.4-second segment had a PC receive UTC minus SDK timestamp median offset of approximately 311 ms (P95 approximately 353 ms), software-fit monotonic drift of approximately -75 ppm, and 20.1 ms RMS receive-time residual. No large timestamp jump recurred in that post-sync segment; only 199/201 ms millisecond quantization differences were observed.
- Repeated native `timestamp not sync` console warnings occurred during the session but cannot be assigned to packet IDs without modifying or hooking the vendor native SDK. The post-sync segment is software-level mapping evidence only. Any future stimulus-to-sample work must exclude/prevent use of the initial pre-sync timestamp segment; hardware/optical timing remains unverified.
- The initial pre-sync packets are not eligible for formal sample association. A trial may begin only after a stable Unix-millisecond post-sync segment has been observed and recorded; this is a software-level timestamp mapping gate only, not hardware timing or physical optical timing verification.

M5.3 software association pipeline (2026-08-18):

- Added one explicit end-to-end recording entry point that starts ND8 acquisition and the existing Quest-PC trigger server in the same external-data session, while preserving raw EEG, ND8 metadata, Quest events, clock-sync diagnostics, gate evidence, and derived associations as separate append-only files.
- The runtime post-sync gate rejects pre-sync/non-Unix timestamps, timestamp transitions, packet continuity breaks, and incompatible packet shape; it enters `association_ready` only after a contiguous Unix-ms segment contains at least 10 packets spanning at least 1.8 seconds with cadence within a 2 ms tolerance.
- Only events after that recorded gate time and with a recent, low-residual Quest-PC affine synchronization snapshot can produce an association. The output identifies the ND8 packet and a software-derived sample estimate, never a hardware-exact sample time.
- The vendor demo text labels its callback timestamp as a first-point time, but this remains unverified for hardware/sample-anchor semantics in this project. Derived records explicitly mark the anchor as unverified, and retain `hardwareTimingVerified=false` and `physicalOpticalTimingVerified=false`.
- An independent offline verifier recomputes Quest-PC mapping from saved four-timestamp evidence and replays ND8 metadata/gate logic before comparing packet/sample results with the live derived log. The subsequent real hardware session completed this association successfully; the remaining boundary is hardware/optical timing verification.

M5.3 real Quest + ND8 end-to-end validation (2026-08-18):

- Successful external session: `m5_3-association-20260818T140324Z-aeec2e3e` under the external EEG study root. The raw EEG/session directory is intentionally not part of Git.
- The trial recorded ordered `stimulus_started_software` and `stimulus_stopped_software` events. The configured trial ran for `2160` frames at an approximately 72 Hz Quest runtime (approximately 30 seconds).
- Start association: ND8 packet `433`, estimated sample offset `94`, estimated global sample `86694`, `associationValid=true`.
- Stop association: ND8 packet `583`, estimated sample offset `96`, estimated global sample `116696`, `associationValid=true`.
- Quest-PC affine residual RMS was approximately `5.86 ms` at start and `5.99 ms` at stop. ND8 packet-to-PC software mapping residual was approximately `20.1 ms`; reported overall software uncertainty was approximately `20.6 ms`.
- ND8 remained in post-sync segment `4` with `association_ready` and continuous packet continuity; the session contained `1563` packet metadata records.
- Final offline verification: `rawEvidenceErrors=[]`, `liveOfflineMismatchKeys=[]`, `validStimulusAssociationCount=2`, `completeValidStimulusAssociation=true`, `passed=true`.
- Targeted M5 tests passed `32/32`.
- Evidence boundary remains explicit: the result verifies Quest software event → PC software clock → stable ND8 packet → software-derived sample estimate. `hardwareTimingVerified=false` and `physicalOpticalTimingVerified=false`; ND8 hardware timing, physical optical timing, physical phase, and hardware sample-anchor semantics remain unverified.

M5 completion boundary:

- M5 — Stimulus Timing / EEG Trigger Synchronization is completed at the software/runtime and real end-to-end association evidence level defined above.
- This completion does not claim hardware-exact EEG sample timing or physical optical timing, and does not include online EEG classification, vision, robotic-arm control, or scene understanding.

M5.1 introduces an independent scene with explicit idle/start/stimulating/stop trial semantics, a temporary all-black idle state, standardized software-side stimulus events, and append-only local timing records. Software event timestamps do not represent measured physical optical onset or offset.

M5.1 Quest 3 physical/runtime acceptance:

- Android Release Build, installation, immersive OpenXR/MR startup, passthrough, three-target visibility, world-fixed behavior, parallax, visibly different flicker rates, explicit stop, persistent black idle, and runtime stability: PASS.
- The validation trial started at `commonStartFrame = 322`, stopped at `globalStimulusFrame = 2160`, and recorded `lastActiveGlobalStimulusFrame = 2159` with `stopReason = configured_frame_limit`.
- The Quest-generated JSONL contained exactly the ordered `session_started`, `stimulus_started_software`, and `stimulus_stopped_software` events; all lines were complete and the final line was not truncated.
- Quest reported an available XR refresh rate of approximately `72.000 Hz`; this remains a runtime/software observation and is not a physical optical timing measurement.

M5.2 implementation:

- TCP newline-delimited JSON transport for the unchanged M5.1 stimulus event record;
- explicit PC ACK and append-only Quest/PC transport diagnostics;
- repeated four-timestamp Quest-PC monotonic clock samples with raw values retained;
- an independent M5.2 scene whose trial start remains frame-scheduled and independent of network availability.

M5.2 Quest 3 transport and synchronization acceptance:

- TCP Quest-PC transport, ordered event delivery, explicit ACKs, periodic four-timestamp clock synchronization, and append-only Quest/PC logs: PASS.
- Quest 3 visual layout, differentiated flicker, world-fixed behavior, active-trial PC server shutdown, non-fatal stimulus continuation, normal black Idle stop, server restart, and reconnect: PASS.
- Runtime refresh observation remained approximately 72 Hz; software timing records do not claim physical optical onset or EEG timing.

M5.2 does not provide EEG sample association or online EEG processing.

Do not begin online EEG classification, vision, robotic-arm control, or scene understanding as part of M5 synchronization work.

## Current Priority

### M6 — ND8 EEG Online Classification

Status: In Progress

Current substage:

- M6.0 — Existing EEG / Decoder Architecture Audit & Experimental Design: Completed
- M6.1a — ND8 Signal & Channel Sanity Validation: Completed / PASS WITH WARNINGS
- M6.1b — Controlled Three-Class Offline Dataset Acquisition: Completed / PASS WITH WARNINGS
- M6.2a — Standard CCA Baseline: Completed / PASS WITH WARNINGS
- M6.2b — Legacy-informed FBCCA Baseline: Completed / PASS WITH WARNINGS
- M6.2 — Offline CCA / FBCCA Baselines: Completed / PASS WITH WARNINGS
- M6.3a — Window-Length Characterization: Completed / PASS WITH WARNINGS
- M6.3b — FBCCA Filter Realization Validation: Completed / PASS WITH WARNINGS
- M6.3 — Offline Characterization: Completed / PASS WITH WARNINGS
- M6.4 — Cross-Session Generalization / Robustness Exploration: Completed / PASS WITH WARNINGS
- M6.5a — Pseudo-Online Decoder Infrastructure: In Progress

M6.1b Session A is the complete 30/30 QC-valid baseline dataset; its acquisition verifier records `classificationPerformed=false`. M6.2a/2b completed fixed Standard CCA and FBCCA baselines, and M6.3 completed fixed window/filter-realization characterization, all as within-session evidence. M6.4 has now performed independent-session acquisition and read-only association replay. B1 has 29/30 fixed QC-valid trials (LEFT/CENTER/RIGHT = 10/9/10); trial 011 remains invalid because its clock-sync freshness exceeded the frozen five-second limit. B2's original formal runtime/manifest status remains `incomplete`, but corrected read-only replay yields 30/30 QC-valid trials (10/10/10). That B2 result is post-hoc exploratory replay evidence, not formal dataset completeness.

Frozen exploratory decoding (CH2/3/4/5/7; 1000 Hz; 0.5 s onset guard; demean-only; 7.2/9/12 Hz; three harmonics) shows a session effect. At 1.5 s, Standard CCA / NumPy FBCCA / legacy-style FBCCA are A 30/30 / 30/30 / 30/30, B1 26/29 / 28/29 / 27/29, and B2 29/30 / 29/30 / 30/30. At 1.0 s they are A 29/30 / 30/30 / 28/30, B1 26/29 / 28/29 / 26/29, and B2 29/30 / 28/30 / 27/30. These are exploratory cross-session observations, not generalized, cross-subject, online, or final-system accuracy.

M6.4 PASS means association robustness audit, real failure-mode fixes, historical replay, and fixed-subset exploratory evaluation are complete. It does not mean formal prospective cross-session generalization is proven. The fixed QC-valid subsets were created before decoding and are not outcome-selected. Association replay retains the software-derived boundary: hardware timing, physical optical timing, nominal stimulus-frequency optical verification, ND8 hardware sample anchor, and hardware-exact EEG sample timing are unverified. No immediate fourth EEG acquisition is planned.

M6.5a validates a replay-only pseudo-online software architecture: historical packet → rolling buffer → event → eligibility → frozen window → decoder → prediction. Its frozen first-decision policy is 0.5 s guard + 1.5 s analysis (2.0 s algorithmic wait), CH2/3/4/5/7 and the existing three CCA/FBCCA backends. In A/B1/B2 it reproduced the 1.5 s offline prediction for every fixed QC-valid trial: A 30/30, B1 29/29, B2 30/30 equivalence for each backend. Classification results remain A 30/30 / 30/30 / 30/30, B1 26/29 / 28/29 / 27/29, B2 29/30 / 29/30 / 30/30 (Standard / NumPy FBCCA / legacy-style FBCCA). Compute time is recorded separately from algorithmic wait and packetization. M6.5a must not use future packets, cannot establish true online performance or end-to-end latency, and does not authorize real ND8 online acquisition.

## Known Open Questions

- Horizon OS application-name metadata for sideloaded development builds
- ND8 detailed hardware parameters
- Mechanical arm model and interface
- Vision inference architecture
- Final SSVEP target frequencies
