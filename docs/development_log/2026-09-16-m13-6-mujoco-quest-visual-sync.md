# M13.6 MuJoCo ↔ Quest Visual Sync

## Audit

The M9 MuJoCo scene uses the existing `build_gripper_scene()` and
`GripperGraspPlanner`: timestep `0.002 s`, seven FR3 revolute joints, two UMI
slider joints, four free-joint blocks, and the existing logical-to-simulator
binding. Quest already creates the FR3/UMI hierarchy and four explicit
`M9VirtualBlockTarget` objects at runtime.

The reliable M8 selection path uses TCP `11001`; it is not reused for
high-rate visual telemetry. The M9 Quest table/block geometry is not identical
to the MuJoCo scene, so M13.6 uses one shared calibrated coordinate transform
and records the mismatch instead of changing the simulation.

## Implementation

- Added `integration/m13_6_visual_sync.py` with compact protocol, logical-only
  block frames, MuJoCo sampler, UDP sender, sequence gate, coordinate/quaternion
  conversion, single Pick/Lift/Place runner, and continuous four-step runner.
- Added Quest `M13_6VisualSyncReceiver` and runtime-only installer. The receiver
  performs background UDP receive, main-thread updates, interpolation, stale /
  duplicate rejection, last-good-state retention, FR3/UMI joint updates and
  logical block pose updates.
- Added PC readiness checks and operator documentation.

## Software evidence

- Protocol/coordinate/stale/static receiver/readiness tests: **8/8 PASS** in
  the project Python 3.12 environment; **6 executable PASS + 2 expected
  MuJoCo skips** in the Python 3.9 environment.
- One real MuJoCo Pick/Lift/Place stream: `place_ok`, 208 frames at configured
  30 Hz sampling cadence, no public simulator identity leakage.
- One continuous four-block MuJoCo stream: all four `place_ok`, 832 frames,
  strictly increasing sequence, no public simulator identity leakage.
- Direct M14/M15/M16 regression: **9/9 PASS**; repository
  `software-default-v1`: **15/15 PASS**, with the existing SciPy-dependent EEG
  decoder suites not enabled in that profile.

No Quest, ND8, COM11, new EEG, or physical robot was operated. Quest visual
alignment, Unity compile/build, subjective smoothness, and network latency
remain the next interactive gate.
