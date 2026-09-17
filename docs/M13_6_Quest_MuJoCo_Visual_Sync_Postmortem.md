# M13.6 Quest–MuJoCo Visual Sync Postmortem

## Final status

**M13.6 Quest Visual Acceptance = PASS**

The final human Quest acceptance confirmed the viewpoint, InstructionText,
SSVEP slots and labels, startup-size continuity, robot synchronization, no-air-
grasp behavior, four-block manipulation and stacking, and execution-time
suppression of candidate indicators and flashing SSVEP presentation.

This is a Quest visual/integration acceptance. It is not an ND8 or live-human
SSVEP acceptance.

## Authority chain

```text
MuJoCo authoritative robot and object state
  -> robot q1-q7 / gripper / authoritative block poses
  -> M13.6 visual telemetry
  -> Unity / Quest presentation mirror
```

MuJoCo owns physical simulation state, trajectory execution, contacts and
object poses. Unity/Quest owns the visual mirror, presentation lifecycle and
user-facing rendering. Unity must not invent a second robot or block state to
make the visual stream look plausible.

## Key symptoms, root causes and principles

| Symptom | Root cause | Final principle/solution |
|---|---|---|
| Robot appeared at a fixed spatial offset | A historical presentation anchor/workspace placement was being treated as authoritative | Derive the robot visual anchor from the same MuJoCo/table frame used by telemetry; do not add a second manual presentation anchor |
| Quest showed an apparent air grasp | MuJoCo and Quest used inconsistent coordinate/anchor conventions | Use one unified MuJoCo-to-Quest frame for robot, gripper and blocks; preserve authoritative body poses |
| Physical and Quest block dimensions differed | Physical MuJoCo geometry is approximately 80 mm while Quest presentation uses a canonical 56 mm visual cube | Keep physical geometry and visual geometry distinct, but use one canonical Quest visual size from startup through telemetry |
| Blocks shrank at Phase 2/3 | Startup used an older approximately 85 mm visual size | Create startup blocks with the same M13.6 canonical visual size used by the receiver |
| Receiver could mishandle sequence restart/stale frames | TCP/UDP reconnects and sender restarts can reset or repeat sequence context | Track sequence state explicitly, reject stale/duplicate frames, handle reconnect/restart boundaries, and keep latest-state-wins behavior |
| Camera Rig edits did not reliably change the user/workspace side | Camera Rig serialized pose is not the runtime presentation authority | `Camera.main` pose is read after XR tracking; `M9WorkspaceRoot` is then placed from that runtime pose |
| Workspace flip made text/targets face the wrong way | Presentation objects retained a standalone 180-degree correction after the root presentation change | Keep the WorkspaceRoot yaw correction separate; use one final, explicit face correction for dynamic Quad/TextMesh orientation |
| SSVEP/candidate visuals remained visible during execution | The independent visual demo sent telemetry but did not complete the formal M8 presentation lifecycle | Route execution presentation through the existing binding lifecycle: selection open shows; execution hides; next selection restores |

## Final architecture lessons

1. MuJoCo state is the source of truth for robot and blocks.
2. Telemetry is a state mirror, not a second planner or animation authority.
3. Camera-relative workspace placement must use the runtime `Camera.main` pose,
   not a guessed serialized Camera Rig placement.
4. Presentation corrections must be explicit and single-purpose. A second
   180-degree correction is a regression, not a fix.
5. Presentation hide/show must reuse the selection lifecycle so visual state
   cannot diverge from selection state.

## Evidence boundary

- Quest visual human acceptance: PASS.
- MuJoCo-to-Quest visual synchronization: PASS for the accepted demo flows.
- ND8/COM11 operation in this closeout: not part of M13.6 visual acceptance.
- Live human EEG and M13 active SSVEP acceptance: still a later handoff.
