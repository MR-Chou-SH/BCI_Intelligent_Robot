# Project Status

Last updated: 2026-08-15

## Overall Phase

Three SSVEP Target Development

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

## Current Priority

### M5 — Stimulus Timing / EEG Trigger Synchronization

Status: In Progress

On the verified three-target frame-driven SSVEP baseline, design and implement stimulus start/stop timing records and an EEG trigger synchronization interface.

Current substage:

- M5.0 — Existing EEG / Trigger Architecture Audit & Synchronization Design: Completed
- M5.1 — Unity Stimulus Event Model and Local Timing Records: Completed / PASS
- M5.2 — Quest-PC Trigger Transport and Clock Synchronization: Ready to Start

M5.1 introduces an independent scene with explicit idle/start/stimulating/stop trial semantics, a temporary all-black idle state, standardized software-side stimulus events, and append-only local timing records. Software event timestamps do not represent measured physical optical onset or offset.

M5.1 Quest 3 physical/runtime acceptance:

- Android Release Build, installation, immersive OpenXR/MR startup, passthrough, three-target visibility, world-fixed behavior, parallax, visibly different flicker rates, explicit stop, persistent black idle, and runtime stability: PASS.
- The validation trial started at `commonStartFrame = 322`, stopped at `globalStimulusFrame = 2160`, and recorded `lastActiveGlobalStimulusFrame = 2159` with `stopReason = configured_frame_limit`.
- The Quest-generated JSONL contained exactly the ordered `session_started`, `stimulus_started_software`, and `stimulus_stopped_software` events; all lines were complete and the final line was not truncated.
- Quest reported an available XR refresh rate of approximately `72.000 Hz`; this remains a runtime/software observation and is not a physical optical timing measurement.

Do not begin online EEG classification, vision, robotic-arm control, or scene understanding as part of M5 synchronization work.

## Known Open Questions

- Horizon OS application-name metadata for sideloaded development builds
- ND8 detailed hardware parameters
- Mechanical arm model and interface
- Vision inference architecture
- Final SSVEP target frequencies
