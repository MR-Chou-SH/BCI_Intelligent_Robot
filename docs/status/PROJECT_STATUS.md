# Project Status

Last updated: 2026-08-15

## Overall Phase

Single SSVEP Target Development

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

## Current Milestone

### M4 — Three SSVEP Targets

Status: Ready to Start

## Current Priority

On the verified single-target frame-driven SSVEP baseline, implement three world-fixed SSVEP targets at fixed coordinates with different stimulus frequencies.

## Known Open Questions

- Horizon OS application-name metadata for sideloaded development builds
- ND8 detailed hardware parameters
- Mechanical arm model and interface
- Vision inference architecture
- Final SSVEP target frequencies
