# M20 Scene Layout Contract

## Authority and lifecycle

`m7_unity6000/Assets/Resources/BCI/M20/assistive_desk_scene.json` remains the canonical template. On each M20 application initialization, Unity creates one version 1 `SceneLayoutSnapshot` with a session `sceneId`, a non-negative random seed, a timestamp, the complete table-local object poses and affordances, and the M13.6 coordinate adapter. A cold app launch creates a fresh seed. `-m20-layout-seed <integer>` supplies a deterministic developer/test seed.

Unity freezes the generated runtime spec before it creates the M16 catalog and presentation. Page navigation, selection, Undo, and Submit reuse the same scene. The confirmed M8 batch carries `sceneId` and the serialized snapshot in the existing batch envelope. The PC validates the complete snapshot before acknowledging the batch, then builds MuJoCo from that validated spec. It does not independently randomize the scene or read old hard-coded source poses.

## Stable identities and visual behavior

The six selectable targets retain their canonical TargetIds and logical block IDs: medicine, storage, phone, button, charger, and USER ZONE. The five non-zone task objects are translated; USER ZONE, table, robot, and UI-only roots stay fixed. Normal orientation, geometry, labels, page size, black/white stimulus presentation, Submit semantics, TCP message type, and slot assignment remain unchanged.

The candidate order is recomputed from the frozen positions using the existing far-to-near Y rows and left-to-right X order, with the existing 0.02 m row grouping. Slot 0/1/2 remain 7.2/9/12 Hz. M16 pagination and the existing binding consume the resulting catalog order.

## Table frame and validation

The snapshot frame is centered on the tabletop top surface, in meters: +X is user visual right, +Y is away from the user toward the robot, and +Z is up. Unity uses the existing adapter `(-x, z, -y)`. MuJoCo uses `(x, y, tableTopWorldZ + z)` with tabletop top at 0.75 m. The pose CSV predeclares a 5 mm comparison tolerance.

Randomized placements are checked for table bounds, at least 25 mm safe edge clearance, 20 mm AABB clearance from other objects and USER ZONE, and the existing 85 mm robot-base keep-out plus object radius and clearance. Medicine and phone source centers are further restricted to X −0.24…+0.24 m and Y 0…0.14 m. This conservative envelope follows probes of the existing `GripperGraspPlanner`; the 20-seed audit verifies actual IK reachability in that envelope. It does not claim a universal collision-free reachability proof for every possible simulator change.

Layout generation is bounded to 96 whole-layout attempts and 256 candidate attempts per object. A fixed, validated fallback is explicit in the snapshot and startup log. If validation or fallback validation fails, generation throws rather than accepting a partial layout.

## Fail-closed PC boundary

The PC rejects missing or invalid snapshots, unknown/duplicate semantic identities, mismatched TargetIds/slot identity, changed poses for a scene that is already frozen, stale scene IDs in an active registry, and object poses inconsistent with the template. Scene validation finishes before the existing consumer accepts the batch and sends `batch_ack`. The accepted snapshot determines source identity, current grasp pose, placement pose, and MuJoCo body locations.

## Evidence boundary

Python validation covers 20 deterministic layouts, exact same-seed replay, different-seed variation, bounds/overlap, fixed USER ZONE, spatial order, paging/Submit freeze, M9 IK, compiled MuJoCo body-pose parity, and the M16/PC/MuJoCo synthetic chain. Three randomized MuJoCo seeds cover medicine and phone grasp/place/release and both phone destinations. A local TCP loopback uses the production M20 PC listener and verifies matching ACK plus the exact snapshot-driven execution.

The live Quest application build, installation, three cold launches, visual labels/patches, page/Submit exercise, and visual cast were not run because the available UI control surface could not operate the already open Unity Editor. Editor EditMode tests were not executed. The generated Unity runtime and Editor C# assemblies did compile successfully with the project-generated Unity 6000.0.66f2 MSBuild projects. No physical robot, ND8, COM port, or raw EEG data was used.
