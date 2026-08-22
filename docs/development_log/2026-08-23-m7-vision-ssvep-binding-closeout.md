# M7 Vision-guided SSVEP Target Binding Closeout

Date: 2026-08-23

## Result

M7 visual → SSVEP binding completed / PASS on Quest 3 in `m7_unity6000/` using Unity `6000.0.66f2`.

The accepted runtime chain is:

`eligible detection → StableTarget → stable world anchor → at most three SSVEP slots`

The world localization component continues to use the previously verified Meta chain:

`PassthroughCameraAccess.ViewportPointToRay → EnvironmentRaycastManager.Raycast → hitInfo.point`

## Implemented boundary

- An allowlist filters BCI candidates before `StableTargetManager`; the initial classes are `cup`, `bottle`, `book`, `mouse`, `cell phone`, and `keyboard`.
- Stable `TargetId` and `Active / TemporarilyMissing / Lost` state drive anchor and slot lifetime.
- Stable world anchors retain their position through short detection loss and are assigned deterministically to at most three slots.
- Slot 0/1/2 map to 7.2/9/12 Hz using the verified shared frame origin and `framesPerHalfCycle = 5/4/3` frame-driven controller.
- The final Quad orientation faces the Quest camera with the verified Unity Quad normal compensation; labels remain separately camera-facing.

## Quest 3 acceptance

- Eligible objects were detected, localized, and displayed with black/white world-space flicker targets.
- `person`, `dining table`, `chair`, and other non-allowlisted classes did not enter the BCI target pipeline.
- Passthrough, YOLO, StableTarget, anchor retention, slot assignment, label orientation, and final APK launch were accepted.

Known non-blockers:

- A fast-moving static object can retain an old target for approximately 1–2 seconds.
- The black stimulus may look subjectively lighter than the legacy M6 scene; it was not changed because flicker behavior remained normal.

## Evidence boundary and next stage

This closeout does not include EEG selection, EEG transport, `SSVEP slot ↔ EEG class ↔ real-world TargetId` integration, dynamic tracking optimization, robot control, or the paused M7.4 RGB→Environment Depth UV route. The next scoped milestone is the minimal SSVEP slot / EEG class / stable `TargetId` interface.
