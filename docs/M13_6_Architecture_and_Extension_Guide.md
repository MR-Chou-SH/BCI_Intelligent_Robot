# M13.6 Architecture and Extension Guide

M13.6 is sealed after Quest visual acceptance. Future work must preserve the
authority boundaries below instead of reopening the accepted spatial-sync
polish.

## Adding a new robot action

Use the normal path:

```text
task / planner
  -> MuJoCo trajectory
  -> MuJoCo robot motion
  -> q1-q7 / gripper / block telemetry
  -> Quest automatic visual update
```

If the telemetry contract remains unchanged, a new action should not require
hand-authored Unity motion. Do not create a Quest-only action path, duplicate
IK, or infer block identity from presentation objects.

## Adding a new block arrangement

MuJoCo/task state remains authoritative. The current Quest runtime still has an
initial visual layout before the first authoritative telemetry frame. This is
accepted for M13.6 and must not be refactored now.

If block arrangements change frequently in a future milestone, the safer
follow-up design is:

```text
wait for first authoritative block telemetry
  -> create/place Quest blocks from that frame
```

That would remove the need to maintain two initial layouts. It is a future
refactor, not part of the M13.6 closeout.

## Adding a second robot

Do not simply duplicate the current Unity robot. The intended order is:

1. Add Robot 2 to MuJoCo.
2. Redesign Robot 1 and Robot 2 base poses together.
3. Extend telemetry to identify multiple robots.
4. Add the second Quest visual robot.
5. Let task/execution state specify robot identity.

The current first robot is intentionally placed at the middle of the table
edge. Do not move it in isolation; revisit base placement only when Robot 2 is
actually introduced.

## Presentation rules

- Do not use Camera Rig serialization as the user/workspace authority.
- Keep `Camera.main -> M9WorkspaceRoot` runtime placement intact.
- Keep `MujocoToQuestTransform` and telemetry identity mapping intact.
- Keep SSVEP slots, labels and markers presentation-only; never derive
  `TargetId` or `logicalBlockId` from their visual order.
- Keep execution hide/show connected to the formal selection lifecycle.
- Do not add another arbitrary 180-degree correction without checking the
  existing final face correction.

## What this guide does not authorize

This guide does not authorize changes to MuJoCo physics, planner/IK,
q1-q7 semantics, block telemetry identity, TCP/UDP contracts, M13 stopping,
ND8 acquisition, or live human experiments.
