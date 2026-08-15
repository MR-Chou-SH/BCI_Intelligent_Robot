# Project Status

Last updated: 2026-08-13

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

Status: Ready to Start

## Current Priority

Prepare M2 while keeping SSVEP stimulation out of scope until the passthrough and fixed-cube baseline is verified.

## Known Open Questions

- Exact Unity editor version to lock for all team members
- Meta XR SDK version
- OpenXR package version
- ND8 detailed hardware parameters
- Mechanical arm model and interface
- Vision inference architecture
- Final SSVEP target frequencies
