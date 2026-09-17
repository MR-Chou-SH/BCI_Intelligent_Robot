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

## Visual alignment audit and source repair

The source audit matched the reported Quest alignment symptoms to concrete
paths. The receiver's old `ApplyFrame` moved `FrankaRoot` from raw MuJoCo base
telemetry every frame; with the current table-frame mapping this placed the
root below the Quest table. The audit also found that the synthetic block
fixture mapped four blocks into a narrow cluster instead of reusing the Quest
catalog's explicit table layout. A real four-planner MuJoCo replay additionally
showed that later free-body contacts could disturb an earlier placed block when
all four placements shared one box.

The repair is source-only and preserves M8 TCP `11001`, M13.6 UDP/TCP `11002`,
latest-state semantics, logical IDs and the existing Quest scene lifecycle:

- `M13_6VisualSyncReceiver` now keeps the scene robot anchor fixed and reports
  anchor drift, and applies block poses from a table-relative initial
  calibration rather than replacing the catalog placement with raw absolute
  coordinates;
- the synthetic stream inverts the same transform from the Quest catalog
  layout;
- the M13.6 four-block MuJoCo stream uses separated 2x2 placement centres,
  while the default `GripperGraspPlanner` placement remains unchanged.

Verification: C# textual sanity has balanced braces and no robot-root
assignment; core M13.6 stream/receiver and USB fixture tests are **15/15**;
M9/M10/M14 related tests are **31/31**; synthetic and MuJoCo transform
sanity reports are **PASS**; `git diff --check` is clean. The full discovered
M13.6 pattern is **19/20** only because the restricted test environment
denies local binds to port `11002` with Windows `10013`; no listener was
reported by the read-only port inspection. This is an environment probe
limitation, not a failure in the alignment code.

The user rebuilt and deployed the C# source repair; the post-build verifier
reported PASS with bindings and diagnostics. Quest visual alignment still
requires user-worn observation. The subsequent motion playback/sequence repair
is Python-only, so it does not require another Unity build. No ND8/COM11 or
EEG was operated, and no physical robot was used.

## Motion regression diagnosis and playback repair

The first post-alignment full demo produced correct static placement but no
visible motion. Artifact analysis showed the decisive chain: the demo sent
synthetic `0..449`, single and sequential continuations, but the Quest had
already accepted through `200089` from the post-build probe. Quest diagnostics
therefore held at `accepted=90`, `applied=90`, `lastSeq=200089`, while later
packets were stale/replaced. The sender q trajectory itself was not constant.

The demo now selects a monotonic sequence seed from retained Quest diagnostics
or a safe current-time seed, keeps the phase sequence continuous, exposes the
run record in each phase observation, and pauses before each human-started
phase. MuJoCo visual playback gained an explicit wall-clock pacing option; the
visual demo enables it while the existing fast software/replay runners remain
unchanged by default. A slow single-joint q1 diagnostic was added with fixed
other joints and a recorded excursion `0 -> +0.35 -> 0 -> -0.35 -> 0`.

Verification: the new visual-demo/sequence/joint tests plus core M13.6
stream/USB/transform tests are **18/18 PASS**; Python compilation and `git diff --check`
pass. A software realtime MuJoCo single replay produced 208 frames in 7.94 s
at 30 Hz and `place_ok`. The current unattended Quest restart showed the first
TCP packet reaching the receiver, but without a tracked/worn scene it did not
produce accepted/applied diagnostics; this is not claimed as human visual
acceptance. The next probe is intentionally only the single-joint command.

## Final visual polish: orientation and phase continuity

The reported “base is at the far side but the arm reaches behind it” symptom
was reproduced at the frame-contract level. MuJoCo reports an identity base
frame at `(0, 0.4, 0.75)` and the table-centre vector is `-Y`. The existing
MuJoCo-to-Unity basis maps `-Y` to Quest `+Z`; the Quest fixed rear anchor needs
the opposite direction. A M13.6-only `180°` table-frame yaw calibration was
added at the first receiver-side `FrankaRoot` resolution. Position remains the
existing fixed scene anchor, and no joint mapping or planner code changed.

The M13.6 sampler now has an opt-in phase anchor. Each visual phase captures
the raw MuJoCo block pose once and emits visual anchor plus the raw delta. The
demo keeps the prior phase's mapped terminal pose and raw terminal pose as
separate values: the former is the next visual anchor, while the latter is
the next MuJoCo initial state. This avoids applying one displacement twice. A
software rehearsal returned `place_ok` for the single stream and `4/4
place_ok` for the sequential stream; the Phase 2 → Phase 3 first-frame maximum
jump was `0.0` in the rebased frame coordinates.

Placement diagnosis separated source-layout mismatch from Quest asset metadata.
The M13.6 visual stream now uses an explicit visual-only placement fixture and
small placement region, while the default M9/M14 planner remains unchanged.
The final mapped Quest rehearsal positions formed two compact columns at about
`x=-0.158/-0.165` and `x=0.158/0.160`; within-column depth spread stayed below
`0.01 m`. The final Quest visual gate remains pending.

Verification after this source change: M13.6 focused tests **20/20 PASS**;
Python compilation **PASS**; M9/M10/M14 direct regression remains **23/23
PASS** in the preceding run; `git diff --check` **PASS**. Since
`M13_6VisualSyncReceiver.cs` changed, a fresh Unity Build And Run and post-build
verification are required before user observation. ND8/COM11/EEG were not
operated.

## Final root-cause repair: unified transform and physical stack validation

The remaining visual mismatch was traced to two independent frame mechanisms:
the Quest receiver's isolated robot yaw correction and the sampler's
per-block `anchor + (current - initial)` rebase. The former could mirror the
robot relative to the table; the latter assigned different artificial offsets
to blocks, so a shared physical stack could not be represented by one rigid
table transform. The old `place_ok` check also did not prove block-block
contact or stability.

The repair removes the independent robot yaw, applies the same table transform
to robot/gripper/blocks, and uses actual geometry-derived stack targets. It
adds release-time collision-category switching, bottom-before-top execution,
actual body-pose/contact/stability metrics, and a gripper-block relative-pose
invariant. Existing M9/M10 defaults and M8 selection presentation lifecycle
remain unchanged. The final visual sequential replay passed `4/4 place_ok`
with both physical stack pairs stable; the focused M13.6 suite passed after
updating the obsolete flat-layout assertion. Unity receiver and catalog source
changed, so `NO UNITY REBUILD REQUIRED` does not apply: a fresh Build And Run
is required before the human Quest gate. No ND8, COM11, EEG, network, or
physical robot operation was performed.

## Interactive hotfix: real visual-demo entrypoint signature contract

The first deployed run reached Phase 1, then failed at the Phase 2 boundary
because `visual_demo.single_phase()` passed `m13_6_visual_mode` to the USB
acceptance helper while `_run_mujoco()` had not declared that keyword. The
lower-level single and sequential MuJoCo runners already supported the option;
the missing declaration was isolated to the intermediate caller/callee seam.

The helper now declares the option and forwards its value explicitly to both
runners, with visual mode enabled by default for backward compatibility. A
new real-entrypoint smoke test drives `visual_demo.run()` through synthetic,
single, and sequential phases without Quest/ADB, while a signature contract
test checks the intermediate and lower-level APIs. This is Python-only;
`NO UNITY REBUILD REQUIRED`.

## MuJoCo-only observation viewer

To separate MuJoCo behavior from the pending Quest spatial gate, the existing
M13.6 runner gained an opt-in debug viewer. `--viewer` opens the passive
MuJoCo viewer on the runner's live `model/data`; every simulation step and
settling step calls `viewer.sync()`, and the final state remains visible until
the window is closed. `--dry-run` keeps the telemetry sink in memory. Single
and sequential commands therefore observe the actual M13.6 visual-mode
planner, initial scene, physical stack targets and collision behavior without
Quest, ADB, network, ND8 or Unity changes. Default headless behavior is
unchanged.

## Targeted spatial diagnosis: fixed robot-root translation

The MuJoCo-only single visual runner was replayed with the same M13.6 loader,
visual target and planner. Five phases were sampled: `HOME`, `APPROACH`,
`SETTLE`/grasp, `LIFT`, and `PLACE`/transport. The mapped MuJoCo gripper
position was compared with the current Unity hierarchy estimate formed from
the serialized `FrankaRoot` anchor and the source robot-base-to-gripper FK
displacement under the same `q1..q7`.

Every frame produced the same delta:
`MuJoCo - Unity = (0.240, -0.006, -0.530) m`, magnitude `0.581838 m`;
population standard deviation was effectively zero and the maximum residual
after subtracting the median was `1.1e-16 m`. This supports the fixed-root
translation hypothesis and does not support a Unity FK, joint-axis or rotation
error. The current serialized anchor is `(-0.24, 0.006, 0.25)` in table-local
coordinates, while the MuJoCo base maps to `(0, 0, -0.28)`.

The same read-only diagnostic found that MuJoCo's first mapped block positions
and the current Quest catalog agree to floating-point precision for all four
logical blocks, including all pairwise relative positions. Therefore the
source telemetry/catalog path does not create an additional startup collapse;
the observed compact appearance is the current compact catalog fixture itself
or a runtime visual observation that needs fresh Quest object evidence. No
Unity calibration, link-pose streaming, planner, rotation, axis or stack
physics change was made.

## Final two-bug fix: robot-base anchor and startup block layout

The fixed-root diagnosis was applied at its actual source. The old
`LeftRobotAnchor`/`RightRobotAnchor` placement `(-0.24, 0.006, 0.25)` was a
presentation-era far-side anchor. It is replaced by the existing unified
MuJoCo table-frame calculation: MuJoCo base `(0, 0.4, 0.75)`, table top
`z=0.75`, scale `0.70`, and the unchanged `Rx(-90°)` basis produce the
table-local anchor `(0, 0, -0.28)`. No rotation, axis sign, joint value,
planner, collision or transport behavior was changed.

The known-good HEAD catalog layout was restored for both the Unity catalog and
the M13.6 synthetic/visual fixture:
`(-0.320, 0.0425, 0.000)`, `(-0.105, 0.0425, 0.000)`,
`(0.105, 0.0425, 0.000)`, `(0.320, 0.0425, 0.000)`. Adjacent spacing is
`0.215 m`, with all four blocks on one row at the same height. Visual mode now
automatically uses the existing catalog phase anchors for its first telemetry
frame; the runtime anchor uses the grasp-safe cube center derived from the
catalog's horizontal layout, and the demo passes the previous visual terminal pose into the next phase,
so startup and Phase 2 → Phase 3 remain continuous without per-block offsets.

Verification after the repair: five-frame gripper alignment residual is at
floating-point noise (`max residual 1.39e-17 m`, median delta `(0, 0, 0)`),
startup-to-first-telemetry block jump is `0.0 m` for all four blocks, single
visual replay is `place_ok` with 222 frames, and sequential visual replay is
`4/4 place_ok` with 888 frames and stable physical stacks. Focused tests pass
`34/34`, visual-demo/transform/audit tests pass `8/8`, Python compilation and
`git diff --check` pass. Unity source changed, so a new Build And Run is
required before Quest visual observation; the Quest visual gate remains
pending. ND8, COM11, EEG, network and physical robot were not operated.

## Overnight Goal 1: physical/visual block identity hardening

The current M13.6 visual-mode MuJoCo scene was audited from its compiled
`model/data`, not from the legacy object configuration alone. The historical
default scene remains heterogeneous (`obj_0` box, `obj_1` box, `obj_2`
cylinder, `obj_3` box), so it is unchanged for M9/M10/M14. M13.6 now uses an
isolated four-body free-box fixture with frozen identity
`block_sim_01..04 -> obj_0..3`, equal 80 mm edges, equal density/mass, and
catalog-derived horizontal spacing.

The first literal conversion of the shared 85 mm Quest cube through scale
0.70 would have produced a 121.43 mm MuJoCo cube, which exceeds the measured
86 mm UMI opening and fails to lift. The grasp-safe M13.6 fixture therefore
uses an 80 mm physical cube, with runtime Quest size carried in each visual
frame as `visualBlockSizeQuestMeters = (0.056, 0.056, 0.056)`. The receiver
applies that opt-in size and table-supported center height only when an M13.6
frame provides it; the shared 85 mm catalog/default remains unchanged.

The block identity audit is persisted at
`artifacts/m13_6_block_identity_audit.json` and `.md`. It reports zero
runtime geometry residual, zero runtime initial-layout residual, grasp width
0.080 m <= configured 0.086 m opening, frozen identity, actual-body pose
telemetry, and unchanged production defaults. The spatial artifact now
distinguishes the 85 mm catalog startup projection from the 56 mm M13.6
runtime projection; the runtime first-frame residual is zero and horizontal
pairwise spacing remains 0.210/0.215 m.

Post-fix software replay: M13.6 single `place_ok`, M13.6 sequential `4/4
place_ok`, both red/green and blue/yellow stack metrics stable with
penetration below 0.00022 m and horizontal error below 0.0035 m. Focused
M13.6/USB/demo/transform tests pass `31/31`; M9 binding and pick/place
regression passes `17/17`; Python compilation passes. This remains software
evidence only. Because the receiver now consumes the new runtime-size field,
a fresh Unity Build And Run is required before the next Quest observation.
