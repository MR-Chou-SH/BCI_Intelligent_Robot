# Project Status

Last updated: 2026-08-15

## Overall Phase

VR Stimulus Development Preparation

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

## Current Milestone

### M2 — Passthrough + Fixed Virtual Cube

Status: In Progress

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

## Current Priority

Proceed to M2.2 — Fixed Virtual Cube while keeping SSVEP stimulation out of scope until the fixed-cube baseline is verified.

## Known Open Questions

- Horizon OS application-name metadata for sideloaded development builds
- ND8 detailed hardware parameters
- Mechanical arm model and interface
- Vision inference architecture
- Final SSVEP target frequencies
