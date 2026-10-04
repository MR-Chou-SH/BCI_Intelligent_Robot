# Project Status

Last updated: 2026-10-04

## Overall Phase

Current active milestone: **M19 — Paged Live EEG Selection**. The current Unity interaction baseline is M16 `PagedQueueV1`; M13.10 Showcase remains legacy/regression evidence only. The M19 implementation and software acceptance are complete, with Unity EditMode and Quest visual acceptance still requiring the already-open Editor and a human operator. No Quest Build & Run, COM/ND8 access, or physical-robot operation was performed for this milestone. See [M19 architecture and handoff](../experiments/m19-paged-live-eeg-architecture.md) and the [run evidence](../agent/overnight/runs/m19_paged_live_eeg_20260922T105007Z/handoff.md).

M1–M8 — Completed engineering capabilities; documented warnings and evidence boundaries remain part of the record.

Current software state: **M10 Sequential Task Benchmark COMPLETE / SOFTWARE + MUJOCO SIMULATION PASS; M11 CONTEXT PREDICTION BASELINE = SOFTWARE PASS; M12 CONTEXT × EEG FUSION BASELINE = SOFTWARE / REPLAY PASS; M13 CONTEXT-AWARE DYNAMIC STOPPING = SOFTWARE / REPLAY PASS; M13.5 LIVE READINESS & ROBUSTNESS = SOFTWARE PASS; M13.6 QUEST VISUAL HUMAN ACCEPTANCE = PASS / SEALED; M13.7 GOLDEN HUMAN SESSION = SOFTWARE / REPLAY + AUDIO + HISTORICAL HUMAN-EEG READINESS PASS; FINAL DEMO SOFTWARE READY — LIVE ND8 ONLY.** Real ND8/human M13 acceptance, physical timing and any physical-robot acceptance remain pending. The benchmark uses a controlled virtual tabletop and logical block IDs; the M9–M15 research sequence is in docs/roadmap/context-aware-bci-shared-autonomy.md.

M18 Unified Phase 2 + Phase 3 acquisition framework: **SOFTWARE READY FOR PHYSICAL SMOKE**. The frozen one-wear plan is Focused 18 + Natural 60 + Self-paced 24, True Idle 9 minutes, Passive Browse 12 minutes, and 12 embedded sham READY cues. Deterministic matrix/schedule, immutable prospective Context snapshots, fail-closed episode state machine, append-only per-block continuous raw-EEG layout, resume/abort safety, canonical Phase 2/3 exports, synthetic dry-run, a bounded `--confirm-live-human` physical-smoke runner, and software/hardware preflight reports are complete in `artifacts/m18_unified_acquisition_20260921T161522Z/`. The synthetic run passed matrix/causality/state/raw-continuity/export checks with 102 intentional episode records; no accuracy, TPR, FPR, Context benefit, human EEG, Quest, ND8, or physical timing claim is made. The next authorized action is operator-run physical smoke only after human Quest/ND8/electrode/cue checks; production decoder, thresholds, Context runtime, robot semantics, and Unity canonical target mapping were unchanged.

Pre-live operational state: **PRE-LIVE BASELINE FROZEN — READY FOR PHYSICAL VALIDATION.** The reproducibility snapshot, operator runbook, pre-live sentinel, evidence index, and isolated future-analysis/adaptive harness are recorded under `docs/agent/baselines/pre-live-nd8-demo-baseline-20260918/`, `docs/acceptance/`, and `docs/agent/overnight/runs/20260918-m13-pre-live-freeze-overnight/`. No M13.6 visual path or production Demo behavior was changed in this freeze; the only operator-boundary correction allows the existing M8 live smoke CLI to accept the explicitly discovered `COMx` rather than assuming COM11. Physical ND8/driver/packet/contact and human Quest/controller checks remain pending.

M30/M31/M32 software research track: **COMPLETE WITH REPORTED LIMITATIONS** as of 2026-10-03. The selectively pushed checkpoint and full evidence are recorded in [the final handoff](../agent/m30_m31_longrun_20261003/FINAL_HANDOFF.md). M30 remains exploratory historical EEG transfer simulation with no positive-Context condition passing the zero-wrong-stop safety gate; M31 remains software-only with weak ambiguity recognition and dispatch disabled; M32's CLI/runbook are ready for human review, which has not been performed. No Quest, ND8, COM11, live EEG, raw EEG modification, physical robot, or real VLA dispatch was used. This software research track does not change the separate M19 physical validation boundary.

M33 Semantic Context repair: **SOFTWARE CHANGES AND FOCUSED VALIDATION COMPLETE WITH HELD-OUT / REPEATABILITY LIMITATIONS** as of 2026-10-03. The shared M30/M32 engine reasons over ordered task state, degrades incompatible relations per candidate, and uses a train/dev-only high-precision Context gate. Its frozen gate achieved 95.24% train/dev active precision at 26.25% coverage; the one-shot held-out result was 94.12% at 36.96% coverage and was not retuned. The M25/M23 historical replay had zero wrong early stops and zero no-delay violations, but also zero Context-applied stops or measured gain. Raw model ranking/q repeatability remains variable; same-process exact-input results are memoized in memory for stable M32 Undo/Reset. See the [M33 final report](../../research_analysis/m33_task_state_context_fix_20261003/attempt-01/M33_FINAL_REPORT.md). M33 is analysis-only and does not change the M19 Quest/ND8 validation boundary.

M34 high-speed SSVEP / Context analysis: **SOFTWARE ANALYSIS COMPLETE WITH RELIABILITY AND TRANSFER LIMITATIONS** as of 2026-10-04. The 118 usable historical trials were split by session into A train, B1 dev, B2 primary heldout, and separate S7 stress heldout. The existing FBCCA baseline reproduced its recorded A/B1 results; a bounded FBCCA/eTRCA/TDCA comparison and DEV-only dynamic stop were frozen before one B2/S7 evaluation. FBCCA reached 89.7% at 0.40 s on B2 and 90.0% at 0.40/0.50 s on S7; no heldout reached 95%. A seeded historical Context transfer simulation accelerated some pairings into ≤0.30 s, but caused wrong early stops, so no safe Context acceleration claim is made. Further tuning on the already-used heldout sessions would invalidate the protocol; prospective EEG, real Context pairing, and physical timing evidence are required for stronger claims. Raw EEG remains external and read-only. See the [M34 final report](../../research_analysis/m34_high_speed_ssvep_context_20261004/attempt-01/M34_FINAL_REPORT.md). This analysis does not change M19 as the active operational milestone or authorize Quest/ND8/COM11 use.

The real-world Passthrough / Quest Camera / YOLO / StableTarget / EnvironmentRaycast route is a completed capability and future extension. It is preserved, but is not the current research benchmark and should not be revalidated by default.

M1–M8 limitations remain attached to their historical evidence: large head motion can cause StableTarget identity churn; local Undo does not automatically re-arm live EEG orchestration; M8.5 pending batches are process-memory only; high-frequency SSVEP can appear visually gray; physical optical and hardware-exact timing remain unverified; real EEG evidence is engineering evidence, not generalized accuracy.

Historical M8.5 records include a simulated reliability-acceptance note. It is retained as evidence context, not as the current milestone or an instruction to resume M8.5. Reopen it only through an explicit project decision.

## Completed M7 Capability / Future Extension

- `vr_stimulus/` remains the Unity 6000.5.8f1 M1–M6 legacy project. Its frame-driven SSVEP, trigger and communication code remains the source for selective future reuse.
- `m7_unity6000/` is the Unity 6000.0.66f2 M7+ application baseline. It is a tracked, non-nested-Git import of Meta `Unity-PassthroughCameraApiSamples` upstream commit `9105be64da8690b41154baf5629cb82dc2dbe4a7`, with MRUK / Meta Core 85.0.0.
- M7.5 official localization is `Completed / PASS` on Quest 3: `MultiObjectDetection → PassthroughCameraAccess.ViewportPointToRay → EnvironmentRaycastManager.Raycast → world marker`.
- M7 visual binding is now `Completed / PASS` on Quest 3: eligible detection → StableTarget → stable world anchor → at most three frame-driven SSVEP slots. Slot 0/1/2 use 7.2/9/12 Hz from shared frame origin with `framesPerHalfCycle = 5/4/3`.
- M7.4 self-developed RGB→Environment Depth UV remains preserved by historical checkpoint and is not the active route.
- The separate `BCI_Intelligent_Robot_unity6000_migration` worktree remains an experiment/reference only and is not merged wholesale.

M7 acceptance boundary:

- Allowlisted classes enter the BCI target pipeline; non-allowlisted background classes such as `person`, `dining table` and `chair` do not.
- Stable `TargetId`, temporary-missing retention, world-fixed anchor behavior and deterministic three-slot assignment were accepted on Quest 3.
- Known non-blockers are approximately 1–2 seconds of stale target retention when a static target moves quickly, and black stimuli appearing subjectively lighter than the legacy M6 scene. Neither is changed in this closeout.
- EEG transport and robot control remain outside this completed M7 boundary. M8.1 has now completed Quest 3 transport acceptance; M8.2a adds only PC-side M6 final-decision orchestration over the existing Quest transport and does not start ND8 hardware or robot control.

## M9 Milestone Record

### M9 — Virtual Manipulation Baseline

Status: **M9 SOFTWARE E2E COMPLETE / UNITY VISUAL + QUEST TRANSPORT / EEG-HARDWARE ACCEPTANCE PENDING**. The formal M8 TCP → M9 dispatcher → MuJoCo path and four-target headless physics run are verified; M9.10 adds only the final floor-text orientation correction on top of the M9.9 UI polish, while Unity/Quest visual re-acceptance remains manual.

The first formal M9 run is on `feature/m9-virtual-manipulation`. The read-only snapshot `a42a0876350f6c94747ce93fa640807b14b18bfd` was inspected and its minimum runtime subset was copied into `robot_arm/`, with every source file and all 44 XML-referenced mesh blobs verified against that commit. `integration/m9_mujoco_execution.py` resolves logical IDs using the baseline's MuJoCo BODY type and calls its existing `GripperGraspPlanner.run_headless()`; it returns structured request/selection provenance, timestamps, baseline result and failure reason without exposing `obj_N` or MuJoCo IDs to the caller. Unit tests exercise this wrapper through an injected fake runtime; separately, the real no-file headless smoke has run with CPython 3.12.14, MuJoCo 3.12.0 and NumPy 2.5.3, returning `place_ok` for `block_sim_01`. The tested dependency set is recorded in `robot_arm/requirements-m9-smoke.txt`; the project-local `.venv/` is ignored and the configured Python 3.9.13 verifier environment remains unchanged. This smoke starts from a synthetic confirmed selection and verifies the software path only; Quest/EEG/hardware were not accessed.

The second formal run (`m9-virtual-block-selection-20260914T064215Z`) adds the M9 virtual-selection software path in `m7_unity6000/`: an explicit four-block catalog, runtime tabletop bootstrap, separate virtual-candidate input at the existing `BciSsvepTargetBinding` seam, and reuse of the frozen M8 selection/group/batch lifecycle. The four stable `m9-vblock-*` TargetIds map explicitly to the existing default logical IDs `block_sim_01`–`block_sim_04`; the Unity-independent contract test resolves them through the current logical-ID scene registry. The software-default verifier includes this test. Unity 6000.0.66f2 startup was blocked before project compilation by the absence of a valid Editor license (return code 198); the dedicated scene's YAML/build settings passed static checks, but Editor import, compile, EditMode tests, Quest visibility, physical SSVEP timing and real EEG remain unverified. The C# EditMode integration test is present but was not run. See the [run handoff](../agent/overnight/runs/m9-virtual-block-selection-20260914T064215Z/handoff.md) for the exact evidence and human acceptance steps.

The later Unity GUI rerun reported by the user passed EditMode 74/74, including M8 regression and M9 virtual-selection tests. M9.1–M9.4 are visual follow-ups; they do not change the M9 selection contract or start M10.

### M9.1 — Virtual Manipulation Scene Polish & Franka Visual Integration

Status: **SOFTWARE COMPLETE / QUEST VISUAL ACCEPTANCE REQUIRED**.

The dedicated `M9VirtualManipulation` scene now disables its `[BuildingBlock] Passthrough` root and OVRManager Insight Passthrough flag, with an opaque neutral virtual background. Its bootstrap places the workspace 1.45 m along the initial HMD's horizontal forward direction and 0.55 m below the initial head position. The catalog-sized tabletop is rebuilt from a light-oak top, apron and four supports; a camera-facing instruction panel sits above it. A static visual FR3 + UMI hierarchy reuses the fixed `a42a0876350f6c94747ce93fa640807b14b18bfd` snapshot: 28 FR3 OBJ parts and six converted UMI visual meshes, with both upstream license texts retained. No collision meshes, robot physics, IK, planner or transport are added to Unity. Existing four block identities, three SSVEP slots, M8 selection boundary and MuJoCo adapter are unchanged. M7 passthrough and its real-target scene remain intact.

The Unity-independent M9 visual contract checks are included in `software-default-v1`. Quest framing/material readability, real-device rendering/performance and final fully-virtual visibility still require manual Quest acceptance. M10 has not started.

### M9.2 — Quest Spatial / Rendering Repair

Status: **SOFTWARE COMPLETE / QUEST RE-ACCEPTANCE REQUIRED**.

The M9 bootstrap now waits for a tracked XR head device and two stable camera-world-pose frames before placing the workspace. A single unit-scale `M9WorkspaceRoot` uses the horizontal HMD forward vector at 1.45 m; the table, blocks, static Franka + UMI model, instruction panel and neutral-room geometry use local coordinates under that root. The table remains 0.55 m below the sampled head height. The non-XR Editor path has a clearly logged eye-height fallback; Android does not use it and times out visibly if a tracked pose is unavailable. This fixes the prior startup-order path where the camera could still be at its serialized identity pose, putting the workspace below the virtual floor.

Instruction text is now a workspace sibling to the panel, placed just off its user-facing surface, independently scaled, oriented from the text toward the sampled head position and horizontally flipped using the existing M8 TextMesh convention. The M9-only background is brighter blue-gray with flat ambient fill and a no-shadow directional key light. Runtime primitive materials now prefer the project's Built-in Render Pipeline `Standard` shader, with `Unlit/Color` fallback. A one-shot `M9_SPATIAL startup` log reports HMD/root/table/red-block/Franka/instruction transforms, camera culling and clip settings, plus renderer activity/layer/clip/shader support summaries.

Software-default M9 visual contract tests verify the delayed tracked-pose bootstrap, root hierarchy, direct workspace parenting for instruction text, explicit facing/mirror correction, built-in shader choice and startup render diagnostics. The existing block TargetIds, logical-ID mapping, three SSVEP frequencies, M8 selection/batch lifecycle, M9 Robot Adapter, visual Franka assets and M7 scene are unchanged. No Quest/ADB/ND8/EEG hardware was accessed. Manual Quest re-acceptance must confirm that all four blocks, the complete table and FR3/UMI are visible, the text reads normally, the virtual room is bright enough and the existing SSVEP presentation remains correct. M10 has not started.

### M9.3 — Franka Visual Polish & Dual-Arm Layout Preparation

Status: **SOFTWARE COMPLETE / QUEST VISUAL RE-ACCEPTANCE REQUIRED**.

The FR3 body and joint transforms remain matched to the fixed MuJoCo snapshot; no guessed per-link offsets or filler geometry were added. Mesh assembly now keeps the MJCF geometry pose on a dedicated child frame instead of overwriting the Unity-imported model root, preserving the imported hierarchy and its transforms. The entire Franka + UMI visual assembly is scaled to 0.7 at `FrankaRoot`; individual link/joint proportions and the named joint hierarchy remain unchanged.

The workspace has symmetric unit-scale `LeftRobotAnchor` and `RightRobotAnchor` layout points at `(-0.24, 0.006, 0.25)` m and `(+0.24, 0.006, 0.25)` m. Only one visual is created under the left anchor; the right anchor is empty. The z position keeps the base near the table rear while accounting for the shorter static reach after scaling. No dual-arm execution, planner, or control path is present. M9 block identities, 3-slot SSVEP mapping, M8 frozen selection boundary, M9 Robot Adapter, and M7 scene are unchanged.

The M9 visual contract and `software-default-v1` pass, and `git diff --check` is clean. The repaired mesh continuity, tabletop clearance, arm-to-block spacing, and future anchor presentation still require manual Unity/Quest visual re-acceptance. No Quest, ADB, ND8, EEG hardware or physical robot was accessed. M10 has not started.

### M9.4 — Franka Seam Audit & HUD Cleanup

Status: **SOFTWARE COMPLETE / QUEST VISUAL RE-ACCEPTANCE FAILED ON FRANKA SEAM**.

The teal-gray occluder was the Cube created by the old instruction-panel bootstrap. That Cube and its renderer/material are removed; the two-line instruction now uses a positive-scale TextMesh lying 1.5 cm above the virtual floor, 0.62 m toward the user from the workspace center, with a 10° user-facing tilt. The TextMesh local +Z front face points up from the floor and its local +Y top edge points toward the HMD, so glyphs are not mirrored. The reviewed 1.45 m workspace distance, 0.55 m table drop, Franka 0.7 scale and robot anchors are unchanged.

The fixed-qpos audit found no first body/joint divergence. A MuJoCo 3.12 forward pass compared to the factory's FR3 hierarchy gives a maximum position delta of `4.84e-16 m` and orientation delta of `4.19e-6°` over link0–link7 and the UMI base. The MuJoCo compiler's geom frames for link3–link5 are nonidentity, but its compiled vertices contain the inverse transform: all seven seam-adjacent compiled visual meshes reproduce their source OBJ vertices within `1.9e-8 m`. Applying those compiled geom transforms to the raw OBJ in Unity would double-transform the geometry. At the fixed pose, `link4_0` and `link5_0` have a source-mesh clearance of `2.05 mm`, or `1.44 mm` at the reviewed 0.7 scale. No per-link offset or filler geometry was added. A one-shot `M9_FRANKA_XFORM` startup diagnostic now records normalized and world link poses, joint anchors, Unity imported mesh-root poses and renderer bounds, so Unity can confirm whether its actual imported roots add any transform.

The numerical MuJoCo audit, M9 visual contract, `software-default-v1`, and `git diff --check` pass. The Quest-visible seam still requires manual re-acceptance; if it remains larger than the measured source-mesh clearance, inspect the startup imported-root and bounds values before considering any asset-level change. No Quest, ADB, ND8, EEG hardware or physical robot was accessed. M10 has not started.

### M9.5 — Runtime Franka Visual Audit & Presentation Repair

Status: **SOFTWARE COMPLETE / QUEST VISUAL ACCEPTANCE FAILED**.

The follow-up audit moved from theoretical FK to the Unity objects that are actually imported and instantiated. Unity 6000.0.66f2 `Library/Artifacts` was inspected with its own `binary2text` tool: all 34 required FR3/UMI ModelPrefabs have identity imported root/child transforms, one enabled MeshRenderer, active GameObjects, a non-null MeshFilter mesh and nonzero vertices. The factory's live chain remains `FrankaRoot/link0/joint1/link1/.../joint7/link7/UMI`; every mesh is instantiated as `body/_GeometryFrame/ImportedRoot/renderer`, and no later reparent or world-pose overwrite exists. Together with the M9.4 old-pose 1.44 mm surface-clearance result, this rules out a centimeter-scale imported-root offset, missing mesh or disabled renderer. The Quest image was exposing the old pose's strongly folded joint4/wrist silhouette and self-occlusion rather than a broken runtime parent chain.

The user subsequently rechecked the newer, more extended pose and still observed two apparent mechanical breaks. That evidence supersedes the earlier folded-pose explanation; M9.5 did not pass Quest visual acceptance.

No parent, asset mapping, mesh, scale or anchor was changed. The fixed display qpos now comes from the repository's existing 6-DOF IK utility at a legal robot-local tabletop pre-grasp target, placing the UMI over the left/center work area with its tool axis down: `(0.084207, -1.087620, 1.221978, -2.349414, 0.992534, 1.681765, 0.822657)` rad. `FrankaRoot` remains uniformly scaled to 0.7. The floor instruction keeps its reviewed position, clearance and positive scale; its rotation receives a 180° local-Z in-plane correction so both line direction and glyph top face the user.

One-shot `M9_FRANKA_RUNTIME` diagnostics now report every actual Renderer path, parent chain, associated body, geometry/import root transforms, world transform, bounds, vertex count, mesh bounds, enabled/active/layer state and material/shader. A Unity EditMode test builds the real factory output and checks its articulated chain, all 34 imported roots/renderers/meshes, and the old folded pose without changing hierarchy. The Unity-independent visual contract, fixed/current transform audit, old-pose continuity audit and actual serialized ModelPrefab audit pass. Unity GUI and Quest must confirm the new silhouette and floor-text orientation; no batch Unity, Quest, ADB, ND8, EEG hardware or physical robot was operated by the software pass. M10 has not started.

### M9.6 — Franka Joint-Closure Diagnostics

Status: **SOFTWARE REFERENCE CHECKS PASS / UNITY EDITMODE AND QUEST JOINT-CLOSURE RE-ACCEPTANCE REQUIRED**.

The purple mid-arm seam maps to `fr3_link4` (`link4_0`, `link4_1`) ↔ `fr3_link5` (`link5_0`, `link5_1`, `link5_2`) at `fr3_joint5`. The red distal region is checked at both adjacent interfaces: `fr3_link6` (`link6_0`–`link6_7`) ↔ `fr3_link7` (`link7_0`–`link7_3`) at `fr3_joint7`, and `fr3_link7` / MJCF `attachment_site` ↔ `umi_umi_gripper_base` / `umi_base_link`. The UMI base is now parented beneath an explicit `fr3_attachment_site` transform at link7 local `(0, 0, 0.107)` m; its own local pose is identity, preserving the prior world pose while making the MJCF flange frame visible in the Unity hierarchy.

The focused MuJoCo attachment test checks the current, previous and third legal qposes. At every pose, joint5 and joint7 anchors coincide with their child-body origins, their expected parent and child frame rotations agree within `5.6e-6°`, and the flange site and UMI base coincide with the same rotational tolerance. At the current pose, the MuJoCo model-world positions are A `(-0.059926, 0.385208, 0.507047)` m, adjacent wrist joint7 `(-0.000062, 0.449708, 0.507040)` m, and link7 attachment/UMI `(-0.000077, 0.449710, 0.400040)` m. The position delta is zero at the source precision. Existing source meshes have a 2.05 mm opposing-face clearance between `link4_0` and `link5_0` (1.44 mm at 0.7 display scale); the sampled closest `link7_1` / `umi_base_link` surfaces are about 0.31 mm apart. These measurements do not justify a link offset, and no body/joint pose, display qpos, scale, anchor, mesh or asset changed.

The Unity factory now adds small Scene View-only green-parent/magenta-child XYZ frame gizmos for joint5, joint7 and the UMI attachment, plus one-shot Editor attachment logs. `M9FrankaAttachmentClosureTests` constructs the actual factory hierarchy and checks all three interfaces across the same three qposes with 1e-5 m / 0.02° tolerances. The current environment could not run Unity GUI tests because the Computer Use bridge failed initialization while Unity Editor was already open; no batch Unity launch was made. Thus the MuJoCo reference checks pass, but actual Unity EditMode closure and Quest visual re-acceptance remain required before marking the visual issue accepted. No Quest, ADB, ND8, EEG hardware or physical robot was accessed. M10 has not started.

The “M10 has not started” notes in M9.1–M9.6 above describe the state at those historical substages. Current state is recorded below: the conditional M10 Prep scaffold is complete, while M10 benchmark acceptance remains open.

### M9.7 — Software E2E Closeout

Status: **M9 SOFTWARE E2E COMPLETE / UNITY VISUAL + QUEST TRANSPORT / EEG-HARDWARE ACCEPTANCE PENDING**.

`integration/m9_batch_dispatch.py` composes the existing M8 `BatchConsumerReceipt` with the M9 `ConfirmedTargetBatch` validator, virtual TargetId catalog, logical-ID adapter, scene binding, and production MuJoCo execution adapter. The all-target runner uses localhost M8 TCP, validates each `batch_ack`, and executes four serial full pick-and-place requests, each returning `place_ok`. It then confirms same-`batchId` replay and cross-batch same-`selectionId` replay are acknowledged but suppressed, with exactly four total executions. Public results and ACKs contain no MuJoCo `obj_N` names or numeric IDs. Batch and selection dedup are process-local; a failed selection remains reserved rather than retrying the same ID, while a later distinct request remains executable.

`integration/m9_virtual_e2e.py` checks four exact TargetId/logical-ID pairs, success/provenance `place_ok`, ACK batch IDs, duplicate suppression, and simulator-ID privacy. The reused planner mutates model-level actuator force ranges and geom friction; `ExistingFr3UmiPickPlaceBackend` restores snapshots before and after each execution, including exceptions, so sequential requests do not compound those changes.

The existing repository-root `.venv` (CPython 3.12.14, MuJoCo 3.12.0) was found and used; no dependency was installed. `scripts/agent/m9-software-acceptance.ps1` now selects it for E2E, runs from the repository root, and temporarily scopes Git safe-directory configuration to the verifier child process. Final acceptance passed: dispatch tests **6/6**, four full physics runs **4/4 `place_ok`**, `software-default-v1` **15/15**, and `git diff --check` **PASS**. The profile's SciPy-dependent EEG decoder checks remain `NOT_ENABLED`. The outstanding Franka visual seam, Unity GUI/EditMode checks, Quest visuals/transport, EEG/ND8, and any physical robot remain separate manual/hardware gates.

### M9.8 — Franka Visual Geometry Reconstruction

Status: **SOFTWARE COMPLETE / UNITY VISUAL RE-ACCEPTANCE REQUIRED**.

The remaining Franka discontinuities were traced to a coordinate-space mismatch
in Unity's built-in OBJ importer. The imported mesh vertices mirror the source
right-handed OBJ X axis even though their ImportedRoot transforms are identity;
the body/joint hierarchy and MJCF visual geometry frames remain unchanged.
`M9FrankaVisualFactory` now applies one inverse `(-1, 1, 1)` scale on every
dedicated geometry frame, covering the full FR3 and UMI OBJ set without
per-link offsets, stretched meshes or filler primitives. Primitive UMI helper
boxes are unchanged, as are FrankaRoot scale `0.7`, anchors, qpos, attachments,
M8/M9 contracts, table, workspace placement and SSVEP presentation.

`integration/test_m9_franka_visual_geometry.py` decodes the actual Unity
serialized vertex buffers and compares corrected world vertices with the
compiled MuJoCo visual meshes for the reported link3/link4, link4/link5 and
link6/link7 seam regions at the M9.5 pose and a second legal pose. It also
checks the same conversion across all 28 FR3 imported mesh bounds. The focused
visual, transform, attachment and virtual-block software checks pass. Unity
EditMode and Quest visual seam acceptance remain manual; no Quest, ADB, ND8,
EEG hardware or physical robot was accessed, and M10 was not started.

### M9.9 — UI Cleanup & Block Scale Polish

Status: **M9.9 SOFTWARE COMPLETE / UNITY + QUEST RE-ACCEPTANCE REQUIRED**.

The floor instruction TextMesh now builds its readable orientation from the
actual world-space tilted floor normal and applies the required 180° in-plane
roll around that normal. Its existing floor clearance, workspace-local Z offset
and positive unit scale remain unchanged. The unrelated
`ReturnToStartScene` sample-navigation root (component plus child `Tooltip`
TextMesh) is disabled in `M9VirtualManipulation` and guarded by the M9
bootstrap; the shared sample prefab and original M7 scene remain intact.

The four virtual blocks remain the shared `0.085 m` cube. Optional resizing was
skipped because the current static visual presentation does not provide a
single validated usable UMI opening without coupling a cosmetic change to the
robot model. TargetIds, positions, colors, SSVEP slots, M8/M9 execution and
Franka geometry are unchanged. The updated visual contract and software
verifier remain passing; Unity/Quest visual re-acceptance is manual.

### M9.10 — Final Floor Text Orientation Fix

Status: **M9.10 FLOOR TEXT ORIENTATION SOFTWARE COMPLETE / QUEST TEXT RE-ACCEPTANCE REQUIRED**.

The M9 floor instruction now constructs its TextMesh rotation directly from
the known local `+Z` front and local `+Y` glyph-top axes. The glyph top is the
projected direction away from the sampled HMD, while the front is the tilted
floor normal; the previous in-plane 180-degree reversal was removed. The text
remains 1.5 cm above the virtual floor at the existing workspace-local
`-0.62 m` offset with positive unit scale. All other M9 visual and execution
contracts remain unchanged. The M9 visual contract, `software-default-v1`, and
`git diff --check` pass; Quest must re-confirm readable text manually.

### M9 Checkpoint & Real Quest-PC Acceptance Preparation

Status: **M9 SOFTWARE CHECKPOINT PREPARED / REAL QUEST-PC ACCEPTANCE REQUIRED**.

The PC-only acceptance bridge in `integration/m9_quest_pc_acceptance.py` reuses
the frozen M8 `selection_open`, `eeg_selection`, `selection_ack`,
`target_batch_confirmed` and `batch_ack` messages. It accepts a software-
simulated class index, lets Quest resolve the class through its frozen snapshot,
receives and acknowledges the confirmed batch on the reconnecting M8 listener,
then dispatches the logical block through the existing M9 FR3+UMI MuJoCo
adapter. `integration/m9_quest_pc_readiness.py` checks the selected Python/
MuJoCo environment, TCP 11001 availability, scene network configuration,
required files and the four exact virtual-block mappings without accessing
Unity, Quest, ND8, EEG or a physical robot. The operator procedure and expected
logs are in `docs/acceptance/m9-quest-pc-mujoco.md`.

This preparation adds no wire message, TargetId mapping, Unity scene/runtime
change or hardware claim. The real Quest APK, LAN transport, Quest-side batch
submission, and any physical-device acceptance remain manual gates. M10
benchmark acceptance remains open.

### M10 Prep — Deterministic Sequential Task Benchmark Scaffold

Status: **M10 PREP SOFTWARE SCAFFOLD COMPLETE; superseded by M10.1 formal software acceptance below**.

After the M9 software-feasible gates passed, `integration/m10_task_benchmark.py` added a pure deterministic task state machine over the frozen logical block IDs. The machine-readable fixture defines House `01→02→03→04`, Tower `01→03→02→04`, and Bridge `01→02→04→03`, along with valid full/partial and invalid transition examples. The runner emits ordered state transitions and a minimal `TaskContext`; wrong-order and unknown IDs terminate an episode as invalid without appending the rejected ID, and reset restores the initial state. A post-completion selection is rejected while preserving the completed state.

The Prep state-machine semantics remain frozen and are covered by the formal M10.1 test suite. The historical Prep-only report above is retained; its earlier `9/9` count is superseded by the current test result below.

### M10.1 — Formal Benchmark Acceptance & Semantics Freeze

Status: **M10.1 FORMAL SOFTWARE BENCHMARK = PASS**.

`integration/m10_benchmark_acceptance.py` reads the checked-in fixture and executes all seven declared cases: three canonical full sequences, one valid partial prefix, wrong-order, unknown-ID, and post-completion rejection. All **7/7 benchmark cases PASS**. Canonical House, Tower, and Bridge each finish `completed`; the partial case is `benchmarkCaseStatus=PASS` with `episodeOutcome=valid_incomplete`; wrong-order and unknown-ID are accepted negative cases with `episodeOutcome=invalid`; post-completion rejection passes while preserving `episodeOutcome=completed`.

The acceptance also emits **2/2 branching evidence records**. After `block_sim_01`, House/Bridge resolve to `block_sim_02` while Tower resolves to `block_sim_03`. After `block_sim_01 → block_sim_02`, House resolves to `block_sim_03` while Bridge resolves to `block_sim_04`. The summary and append-only JSONL evidence are deterministic and contain no simulator-internal IDs.

The targeted M10 suite passes **12/12** with the repository `.venv` CPython 3.12.14. The state-machine semantics were not redefined; only the explicit episode outcome and formal acceptance/evidence layer was added. M10.1 was software-only.

### M10.2 — Sequential MuJoCo E2E and M10 Closeout

Status: **M10 SEQUENTIAL TASK BENCHMARK = COMPLETE / SOFTWARE + MUJOCO SIMULATION PASS**.

`integration/m10_mujoco_sequential_e2e.py` composes one confirmed/frozen M8 selection at a time with the existing M9 TargetId→logical-ID conversion, M9 dispatcher, and reused FR3/UMI MuJoCo adapter. M10 preflight occurs before dispatch; only a successful `place_ok` execution commits the next M10 state. Adapter/backend failure leaves the sequence state unchanged, and a wrong-order preflight never calls the robot adapter.

The Stage A gate passed with House, Tower, and Bridge each completing four ordered steps, four dispatches, and four `place_ok` results. The representative House wrong-order case after `block_sim_01` retained the prefix, rejected `block_sim_03` before dispatch, and recorded zero robot executions for that rejected target. Public evidence has no `obj_N` simulator identifiers. Focused M10.2 tests pass **3/3**; the M10.1 plus existing M9 dispatcher/adapter regression passes **35/35**.

This closes M10 as a software and MuJoCo simulation benchmark. It does not claim Quest visual/transport acceptance, EEG/ND8 behavior, physical-robot execution, physical timing, or a human/research performance benefit. M11 investigation is the next authorized software step; its predictor contract must not consume M10 `remainingLogicalBlockIds` or `validNextLogicalBlockIds`, which remain evaluator/oracle truth.

### M11 — Context-only Next-Target Prediction Baseline

Status: **M11 CONTEXT PREDICTION BASELINE = SOFTWARE PASS**.

The user-frozen M11 contract defines a hidden-task setting over the House/Tower/Bridge task library. The predictor receives only `completedLogicalBlockHistory`, the frozen four-ID `availableLogicalBlockIds` catalogue, and derived `stepIndex`; M10 `remainingLogicalBlockIds` and `validNextLogicalBlockIds`, true task identity, EEG evidence, and fused output never cross the predictor boundary. It filters prefix-compatible hypotheses from a uniform prior and aggregates next-target probability mass.

The canonical empty, one-prefix, two-prefix, branch, tie, invalid-history, terminal, deterministic-replay, anti-leakage, probability-sum and logical-ID isolation cases pass. The focused M11 suite passes **12/12**, formal acceptance passes **11/11**, and the M10 regression passes **12/12**. Evidence is in `docs/agent/overnight/runs/m11-to-m12-campaign-20260915T103723Z/`.

This is a context prior baseline, not a task-recognition accuracy claim. It is software-only and does not access Quest, ND8, real EEG, or a physical robot. M12 now reuses this `ContextPrior` contract.

### M12 — Context Prior × EEG Evidence Fusion Baseline

Status: **M12 CONTEXT × EEG FUSION BASELINE = SOFTWARE / REPLAY PASS**.

M12 reuses the M11 `ContextPrior`, the explicit virtual `TargetId → logicalBlockId` mapping, and the existing three-class M6/FBCCA fused score vector. The score is retained as finite nonnegative `EEGEvidenceScore`, not a calibrated probability. The active three-slot set uses the frozen 7.2/9/12 Hz slots; global context is projected onto those candidates, softened toward uniform with fixed `lambda = 0.5`, and multiplied by `max(score, 1e-12)` before normalization. Context biases but cannot veto an active candidate.

Uniform, agreement, moderate conflict, strong EEG override, half-half ambiguity, active projection, zero active mass, epsilon preparation, invalid evidence, deterministic replay, existing-vector adaptation, active-candidate nonzero influence, `obj_N` isolation and no-robot-invocation checks all pass. The focused fusion suite passes **13/13** and formal replay acceptance passes **12/12**. The pure NumPy FBCCA seam returns three finite score values for each frozen synthetic frequency; the broader M8/M9/M10/M11/M12 regression passes **75/75**. The old decoder test module could not import because this environment lacks optional `scipy`; no dependency was installed or changed.

This is software/replay evidence only. No Quest, ND8/COM11, real EEG, physical robot, human study or M13 work was entered. See `docs/agent/overnight/runs/m11-to-m12-campaign-20260915T103723Z/` and the M12 development log.

### M13 — Context-aware Dynamic Stopping Baseline

Status: **M13 CONTEXT-AWARE DYNAMIC STOPPING = SOFTWARE / REPLAY PASS; REAL QUEST + ND8 ACCEPTANCE PENDING**.

M13 reuses `eeg/decoder/characterization.py::WINDOW_GRID_SECONDS` (`0.5, 1.0, 1.5, 2.0, 2.5, 3.0` seconds) and the existing M6.5b online timing semantics (`0.5 s` onset guard, `1.5 s` analysis, `0.2 s` step). It consumes repeated M12 fused-evidence snapshots and applies the transparent v1 policy: normalized fused top `>= 0.70`, top1-top2 margin `>= 0.20`, fused top must equal raw EEG top, and two consecutive eligible windows. These are engineering defaults, not calibrated or optimized thresholds.

The policy/trajectory suite passes **14/14**; synthetic acceptance passes **10/10** cases, including transient spike, target switch reset, context/EEG conflict, strong EEG override, ambiguous evidence, fallback, tie, invalid evidence, deterministic replay and uniform-context invariance. The read-only historical M6.5b result fixture `D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json` replayed **89 trials**: 20 early stops and 69 full-window fallbacks; this is historical recorded EEG plus deterministic synthetic context overlay, not a human context-aware experiment. Descriptive agreement with the M12 full-window target was 89/89; no optimization or performance threshold was applied.

The M13 adapter delegates only `decisionMade=true` results to the existing `M8SelectionOrchestrator.submit_final_decision` seam. Mock acceptance verifies exactly-once early submission, no-decision suppression and frozen slot/label identity; no wire protocol, Quest contract, M6/M12 decoder or Robot Adapter path changed. Readiness passed with Python 3.12.14, required imports/files, mapping checks and a local bind/close probe for TCP 11001. The related regression passed **70/70**; the old M6 pseudo-online suite remains dependency-limited at 7/8 because the existing environment lacks optional scipy for legacy FBCCA, while the pure NumPy FBCCA seam passes.

Evidence boundary: Quest operated = **NO**; ND8/COM11 operated = **NO**; new real EEG collected = **NO**; physical robot operated = **NO**. The next authorized action is the operator procedure in `docs/experiments/m13-real-quest-nd8-operator-procedure.md`, not M14 or learned/optimized stopping.

### M13.5 — Live Readiness & Robustness

Status: **M13.5 LIVE READINESS & ROBUSTNESS = SOFTWARE PASS; M13 REAL QUEST + ND8 ACCEPTANCE = PENDING**.

M13.5 adds explicit safe-default `baseline`, non-interfering `shadow` and opt-in `active` PC runtime modes. It reuses M12 fused evidence, the real M13 policy and the existing M8 `submit_final_decision` seam. PC-only streaming acceptance passes for early stop, fallback, no-decision, shadow non-interference, target switch and context conflict. Fault injection passes duplicate/replayed windows, out-of-order/missing windows, stale trial/selection identity, closed-selection/late evidence, malformed/NaN/Inf evidence, empty target set, mapping mismatch, duplicate finalization and simulated transport interruption.

Structured append-only JSONL sessions, a descriptive analyzer and engineering acceptance reporter are complete. They check trial identity, state reset, monotonic windows, mode semantics, mapping provenance, ACK order, exactly-once submission, no fabricated no-decision and `obj_N` privacy without imposing research-performance thresholds. Readiness is `READY` with explicit warnings that Quest/ND8 were not checked. The environment has no scipy declaration; legacy FBCCA imports it, while the M13 default NumPy seam does not. No dependency was installed. A second compatible historical continuous fixture was not available, so the optional multi-dataset audit is deferred without blocking.

Use `docs/experiments/m13.5-live-readiness-operator-package.md` for the future user-controlled direct-`active` M13 classifier-acceptance session. Baseline and shadow remain diagnostic modes only; this is an engineering readiness closeout, not live-device or human-study evidence.

### M13.6 — MuJoCo ↔ Quest Real-Time Visual Sync

Status: **M13.6 REAL QUEST RECEIVER + MUJOCO STREAM = USB/ADB ENGINEERING PASS / HARDENED; M13.6 QUEST VISUAL HUMAN ACCEPTANCE = PASS / SEALED at m13.6-quest-visual-pass**.

The final Quest human acceptance confirmed the accepted viewpoint, instruction
text, SSVEP slots and labels, startup-size continuity, robot synchronization,
no-air-grasp behavior, four-block manipulation/stacking, and execution-time
suppression of candidate indicators and flashing presentation. This is a Quest
visual/integration acceptance, not an ND8 or live-human SSVEP acceptance.
The authority chain remains MuJoCo authoritative state -> telemetry -> Quest
presentation mirror. Do not reopen M13.6 visual polish without an explicit
regression.

The independent visual transport keeps UDP `11002`, TCP compatibility `11002`,
and M8 control TCP `11001` separate. The Quest receiver uses the mounted M9
scene, TCP `NODELAY`, persistent newline framing, latest-state replacement,
stale/duplicate sequence protection and main-thread transform application.
The PC side now includes an ADB USB runner, same-LAN TCP preflight, a return-to-
lab demo launcher and software-only transform sanity output.

The currently installed user-built APK was exercised over `tcp:21002 ->
Quest:11002`: 30 Hz and 60 Hz 90-second streams had no sender errors and the
Quest process/listener remained alive; a 600 Hz short stress wrote fewer
physical frames than producer calls, consistent with latest-state replacement.
MuJoCo single Pick/Lift/Place returned `place_ok` with 208 frames and the
four-block stream returned `4/4 place_ok` with 832 frames. A 10-minute mixed
30/60 Hz soak was completed in two 200-second phases after an app restart;
sender errors remained absent and the listener stayed alive, though the old
APK did not expose the new periodic receiver counters.

The earlier readiness notes about a pending Unity GUI Build And Run and zero-
byte screenshot are historical pre-acceptance evidence. The final human Quest
visual acceptance is recorded in the M13.6 postmortem and sealed by the tag
above. Software transform sanity passed for 180 synthetic frames and 208
MuJoCo frames. Relevant M13.6/M13.5/M14/M15/M16/M10 regression passed
**52/52**; M16 no-EEG report dry-run passed. No ND8/COM11, EEG or physical
robot was operated for this visual closeout.

Use docs/experiments/m13.6-mujoco-quest-visual-sync.md and
python -m integration.m13_6_visual_demo --transport usb --phase all --human
only for historical reproduction or an explicitly authorized regression. The
current M13.6 visual baseline is sealed and must not be polished as part of
M13.7.

### M13.7 — Golden Human Session Live Acquisition Launcher

Status: **M13.7 SOFTWARE / REPLAY + HISTORICAL HUMAN-EEG READINESS PASS; REAL ND8/QUEST HUMAN ACCEPTANCE PENDING**.

The new `integration/m13_7_live_launcher.py` reads the versioned protocol JSON
as authority and executes the complete generated order without hardcoding the
147 trial records. `LiveND8Source(COM11)` uses the existing explicit-lifecycle
adapter and persists each raw packet through `HumanSessionRecorder` before the
common `GoldenPacketPipeline`/existing NumPy FBCCA and M13.5 path. The same
packet/continuity contract is used by `RecordedND8Replay`; the old M8 6/9-stage
launcher remains unchanged.

AudioCueSystem provides an explicit PC `winsound` path and a silent
Null/Logging path with the fixed slot 0/1/2 = 7.2/9/12 Hz and 1/2/3 beep
contract, a guaranteed one-second pre-stimulus silence, analysis-window audio
exclusion, and monotonic/UTC event records. Default rehearsal, simulation and
silent preflight remain speaker-independent. Explicit audible rehearsal and
confirmed LIVE HUMAN use `PcToneAudioBackend`; PC backend initialization or
playback errors fail fast rather than falling back to Null. HumanSessionRecorder writes fsync-backed raw ND8 packets, packet
metadata, lifecycle events, decoder/context evidence, Quest events and
action/telemetry associations into a new manifest-scoped session directory.
The launcher freezes Git/protocol/configuration provenance, supports a
confirmation gate, durable block-boundary pause/resume and partial-session
preservation, and marks short runs as PREFLIGHT rather than Golden data.

The no-ND8 dry-run passed with 80 synthetic replay packets, all three slots,
aligned/neutral/conflict context, no-intent/no-decision, verifier PASS, replay
of the resulting session, and six complete synthetic House/Tower/Bridge
episodes with 24 individual action segments. Golden Protocol v1 is frozen in
docs/protocols/M13_7_Golden_Protocol_v1.json: 60 static calibration, 18
independent M13 active, 27 context, 9 no-intent, 24 closed-loop selections
and 9 controlled-artifact labels, plus a time-based baseline. Its projected
duration is **44.20 minutes** from the implemented formal timing configuration
(`preparationSeconds=6.0`; 2651.76 s projected).

Full no-hardware launcher simulation passed with `planned=147`,
`executed=147`, 148 raw packets, verifier PASS, and replay PASS. A separate
synthetic PREFLIGHT passed with four representative segments. Live wiring was
tested with a fake ND8 device and proved raw persistence before downstream
delivery. This is software/replay infrastructure evidence only. No real
ND8/COM11 was opened, no human EEG was collected, no existing EEG was
overwritten, and no new M13.6 visual behavior was changed. See
docs/experiments/m13_7_golden_human_session_protocol.md and the run handoff
under docs/agent/overnight/runs/. The PC audible routes are implemented and
covered by fake-backend tests; no real speaker rehearsal was run automatically,
and the first manual audible rehearsal failed perceptual acceptance because the
original tones were too short, too closely spaced and not pitch-distinct. The
cue parameters have now been refined and the manual audible rehearsal has
passed. The overnight historical-human readiness rehearsal then fed the
verbatim M6.1b raw values through the M13.7 recorder, onset/sample-anchor
pipeline, NumPy FBCCA, M13.5 runtime, replay and verifier: 30/30 trials,
verifier PASS, replay first-window agreement 30/30, and the one-command Golden
QA report PASS. This remains software/replay evidence; no COM11, ND8, Quest or
new human EEG was operated in that rehearsal. A separate operator-run partial
session is retained read-only after the pre-hotfix silence-boundary failure;
it is not a finalized Golden acceptance. A low-risk operator preflight is
available at `integration/m13_7_operator_preflight.py`; the future live session
still requires the external runtime, Quest, ND8 channel-quality and human
acceptance gates.

#### Final Context-Aware Building Demo deployment readiness (2026-09-18)

The formal operator entry point is
`integration/m13_context_aware_building_launcher.py`. It supports the existing
historical/replay source and the actual `LiveND8Source(COMx)` through the same
M13.7 packet/sample-anchor boundary and the same M11 → M12 → M13 → M8 → M9 →
MuJoCo downstream chain. Live mode is explicitly label-free: the building task
posterior is derived only from observable selection history, provisional/current
batch state and EEG evidence; historical ground truth is used only to choose a
matching replay trial in the test harness.

The final software gate passed: 132/132 cross-M8–M14 regression tests,
69/69 M13.7/M8/M9 timing and transport tests, all 12 task×batch-partition
scenarios (3+1, 2+2, 1+3, 1+1+1+1), undo/alternative selection, dynamic
candidate remapping/stale-slot rejection, and two bounded historical replay
soak scenarios. The external CPython 3.9.13 runtime passed the read-only
operator preflight with vendor SDK imports and COM enumeration; no COM port,
ND8, Quest, or physical robot was opened or operated in this run. Tomorrow's
remaining boundary is therefore actual COM selection/packet arrival, channel
quality, human EEG, and human Quest/controller validation. See
`docs/experiments/m13_context_aware_building_demo_operator.md`.

### M14–M16 — Assumption-Jump Software Campaign

Status: **M14 FULL SEQUENTIAL SHARED-AUTONOMY CLOSED LOOP = SOFTWARE / MUJOCO / REPLAY PASS; M15 COMPARATIVE BENCHMARK & ABLATION = SOFTWARE / REPLAY PASS; M16 EXPERIMENT / DATASET / REPRODUCIBILITY INFRASTRUCTURE = SOFTWARE READY. M13 REAL-HUMAN SSVEP EVIDENCE = PENDING; DOWNSTREAM HUMAN SEQUENTIAL ACCEPTANCE = NOT VALIDATED.**

M14 now composes the existing observable-history M11 prior, M12 fusion, frozen M13 dynamic stopping, M8 final-decision lifecycle, M9 TargetId/logical-ID dispatcher and reused FR3/UMI MuJoCo path. House, Tower and Bridge each completed four synthetic-evidence steps with four `place_ok` simulation executions and four M10 commits; no-decision, wrong-target, robot-failure and duplicate-final-decision cases fail closed. M15 supplies paired EEG-only, M12 full-window and M13 dynamic-stop conditions with descriptive JSON/JSONL/CSV output; the registered M6.5b fixture was replayed read-only as historical EEG plus synthetic context overlay. M16 supplies a pseudonymous manifest, deterministic development schedule, stable session layout, resume-safe checkpoint and reused M13.5 analyzer/acceptance composition.

This campaign used `ASSUMED_PASS` only as a clearly labeled development assumption for pending real-human M13 evidence. It does not claim human EEG accuracy, latency, early-stop benefit, cognitive-load change, generalized performance or real sequential shared-autonomy success. The first unavoidable research fork is recorded in `docs/roadmap/next-research-fork.md`; no new research direction was selected automatically.

M15 Stage 2 v2 runtime integration is now **SOFTWARE READY FOR LIVE SMOKE**. The explicit `dynamic_v2` M8 live-ND8 mode uses the frozen EEG-only raw-score gate (2.0 s minimum; margin `0.15`; normalized top `0.40`; ratio `1.05`; raw-top run `2`; 4.0 s raw-EEG fallback) through the existing NumPy FBCCA, rolling-buffer, selectionId, Quest transport and M9 mapping seams. Historical replay through the production policy is 89/89 development and 3/3 M6.6b external per-trial parity with zero wrong early stops. This remains software/replay evidence: no live ND8/Quest smoke, physical timing, hardware sample-anchor, or physical latency claim is made here; M13 v1 and baseline remain available and unchanged by default.

The 2026-09-18 software-only context-aware building-demo checkpoint adds a thin
`integration/m13_context_aware_building_demo.py` composition over those frozen
seams. It consumes read-only M6.1b human raw EEG through the existing NumPy
FBCCA boundary, records M11 observable-history priors and M12 fusion, preserves
M13 stopping and M8 ACK semantics, supports provisional 1–3 selection batches
with A submit/B undo, and dispatches canonical Bridge/Tower/House order through
the existing M9 FR3/UMI MuJoCo adapter. The versioned contract is
`docs/protocols/M13_Context_Aware_Building_Demo_v1.json`; all three tasks passed
3+1 batches, 12/12 headless `place_ok` executions, ordered `build_slot_0`–
`build_slot_3` assignment, context-vs-EEG dominance probes and invalid/stale
selection guards. This is software/replay/MuJoCo readiness only: no Quest,
ND8/COM11, new human EEG, or physical robot was operated, and the fourth block
uses a clearly tagged scripted candidate assignment because the historical M6
source has three EEG classes.

Build a controlled virtual tabletop manipulation baseline: Quest 3 presents virtual blocks and SSVEP interaction; PC performs EEG decoding and maps the selected block to a stable logical block ID; MuJoCo runs the Franka FR3 + UMI gripper simulation and returns execution feedback.

Use stable logical IDs between Quest selection and MuJoCo objects. The imported baseline scene has four generic, non-color-coded objects, so its default table uses neutral simulation IDs `block_sim_01`–`block_sim_04` mapped explicitly to `obj_0`–`obj_3`. The caller must map confirmed Quest TargetIds to the chosen logical IDs; the mapping does not assert a visual color or task meaning. Reuse the snapshot on `feature/add-robotArm-simulation` without checking out, merging, or modifying that branch.

M9 excludes VLM/LLM, context AI, reinforcement learning, and dynamic stopping. Do not reopen M1–M8 acceptance work as part of M9 by default.

## Historical M8 Milestone Record

### M8.5 — Reliable Batch Delivery

Historical status: Completed / PASS WITH WARNING; retain the recorded limitations below.

M8.5 keeps the existing newline-delimited JSON connection on TCP 11001 and leaves `ConfirmedTargetBatch` plus every M8.3 frozen selection fact unchanged. A submitted Quest batch stays process-lifetime pending until a matching PC `batch_ack` (`protocolVersion`, `messageType`, `batchId`) arrives. On a TCP reconnect, each still-pending batch is resent in original publish order. The PC consumer accepts a legal `batchId` downstream once per process but returns `batch_ack` for every duplicate delivery, allowing Quest retry to stop without duplicate downstream execution. Pending state is deliberately in-memory only: a Quest app process restart before ACK does not recover it. No robot command, database, message queue, or EEG change is added. The prior closeout note retained simulated reliability acceptance as an open evidence item; this note is historical and is not the current project gate.

### M8 Final Demonstration Orchestration

Status: Completed / PASS

The formal live entry point accepts `--max-trials 1`, `2`, or `3`, retaining the original default three-trial plan. After the final accepted terminal event, the PC CLI releases TCP `11001`, starts the existing batch consumer, and waits for `target_batch_confirmed` / `batch_ack`. This is historical M8 behavior; M9 must consume the frozen selection boundary rather than reopen M8 internals.

### M8 Free-Selection Orchestration

Status: Completed / PASS WITH WARNINGS

The free-selection plan removed the PC plan's post-hoc expected-class ordering while retaining Quest's frozen snapshot as the authority for slot/TargetId resolution. The accepted user intent boundary remains the frozen selection/batch result.

### M8.4 — Multi-Target Selection UX & Batch Confirmation

Status: Completed / PASS

M8.4 preserves the immutable single-target M8.3 contract and adds a Quest-owned outer group lifecycle for ViewLockedHud. The existing full HUD candidate pool remains deduplicated and camera-right ordered; a current group freezes up to three anchors to slot `0/1/2`, while remaining candidates receive gray indicators. A result accepted through M8.3 adds its frozen fact once to the current batch, changes that group target to blue/static (its slot no longer flickers or resolves), and keeps remaining slot identities/frequencies unchanged. Right-controller A submits one non-empty immutable `ConfirmedTargetBatch`; right-controller B undoes the most recent current-group membership. In batch mode the raw Meta detection-box presentation is hidden, so only StableTarget-backed BCI indicators own gray/green/blue/`✓` semantics; the old marker A/B commands yield to batch input. Submit aborts a pending local selection first, so delayed PC EEG decisions are stale. The batch is sent as one `target_batch_confirmed` newline-JSON message over the existing Quest→PC connection; no robot command is defined. Submitted targets remain blue with `✓`; unselected group targets are processed/skipped and the next remaining group is activated. Quest acceptance passed: two selected targets produced exactly one ordered `target_batch_confirmed`, then the group advanced normally.

## Completed Milestone

### M8.3 — EEG-selected TargetId closed-loop / downstream selection contract

Status: Completed / PASS

Quest remains the sole authority that resolves a PC-provided canonical class index through the `selection_open` frozen snapshot. On an accepted terminal decision, `BciSelectionCoordinator` publishes exactly one immutable `BciTargetSelectionResult` / `target_selected` C# event containing the selection ID, class index, slot, TargetId, semantic label, frozen stable-world position when available, software UTC, and `quest_frozen_selection_snapshot` provenance. Downstream consumers subscribe to this event and must not query the live slot binding again. Duplicate, stale, invalid, empty, inactive, no-decision, abort, and reconnect-delivered messages cannot publish a second or fabricated result. Quest acceptance verified one accepted class-1 result, duplicate rejection, abort suppression, and unknown-selection rejection on the rebuilt current-HEAD APK, with no M7/HUD regression or app crash. This is a software boundary only; it does not begin robot control or define a robot command schema.

### M8.2b — Live ND8 Final Decision → Quest Selection

Status: Completed / PASS WITH WARNINGS

The formal `integration/m8_selection_cli.py --mode live-nd8` command composed the external CPython 3.9 Neurodance runtime, `Nd8SerialAdapter(COM11)`, M6.7 channel admission, frozen NumPy FBCCA `LiveOnlineController`, first-2-Consecutive final-decision policy, and the Quest snapshot transport. Multiple real Quest + ND8 engineering sessions repeatedly validated the accepted chain `real ND8 → online FBCCA → final class → Quest → slot → TargetId → ACK`; a complete three-class ViewLockedHud + separation session reached 3/3. This is engineering evidence, not an accuracy benchmark or a latency claim.

The frozen engineering presentation baseline is `ViewLockedHud`: camera-local slots `(-0.32, 0.18, 0.85)`, `(0, 0.18, 0.85)`, `(0.32, 0.18, 0.85)` m, uniform `0.20 m` scale, left-to-right target assignment after conservative physical duplicate suppression, world-space leader lines and `1/2/3` markers, plus selection layout freeze while HUD Quads remain camera-local. Slots remain 7.2/9/12 Hz with shared frame origin and `5/4/3` half-cycle frames on black/white Unlit material. World-space方案 A remains a diagnostic/fallback path, not the default experiment direction.

Warnings remain: real-session variation and early first-2-Consecutive sensitivity exist; 12 Hz was initially less stable and later HUD sessions showed improved evidence without proving a presentation cause. Purple-background and eyelid observations are exploratory, ordered, and not causal evidence. Hardware-exact sample timing, physical optical timing, physical phase, hardware sample-anchor semantics, generalized accuracy, and end-to-end latency remain unverified.

## Completed Milestone

### M8.2a — M6 Final Decision → M8 Selection Transport

Status: Completed / PASS (software/replay + Quest 3 orchestration acceptance)

M8.2a connects only M6's existing `decisionMade=True` / `finalDecisionLabel` output through one PC-side canonical mapping to M8's `eeg_selection` message. Mock/replay and real `LiveOnlineController` final-record tests verify open-ACK gating, 0/1/2 mapping, one-shot submission, stale/abort/no-decision suppression, Quest rejection logging, newline JSON/ACK handling and a single-command mock CLI. Quest 3 orchestration acceptance then verified the complete mock path for class 0/1/2, plus no-decision and abort suppression, with the Quest snapshot remaining the authority for slot/TargetId resolution. Intermediate M6 predictions, real ND8 startup, timing/latency claims, and robot actions remain outside this completed substage.

M8.2b closeout is complete; subsequent work must consume its frozen Quest-owned selection boundary rather than reopen decoder tuning or hardware validation by default.

## Completed Milestone

### M8.1 — PC → Quest Simulated EEG Selection Transport

Status: Completed / PASS (Quest 3 acceptance)

M8.0 established the immutable `BciSelectionSnapshot` contract. M8.1 adds only a minimal simulated-PC selection transport: PC sends a unique selection ID and canonical class index; Quest captures/owns the matching snapshot and resolves the TargetId locally. Real ND8 decoding, robot actions, and additional clock-synchronization claims remain outside this milestone.

Quest 3 acceptance confirmed class 0 → slot 0 → `target-0001`, class 1 → slot 1 → `target-0007`, and class 2 → slot 2 → `target-0005`. Duplicate decisions were rejected as `DuplicateDecision`; unknown IDs as `UnknownSelectionId`. Android TCP handling was corrected so `remote_eof` exits the connected loop and reconnects, while Android idle `WouldBlock` is not treated as a connection failure. M7 StableTarget and three-slot SSVEP behavior showed no regression.

## Completed Milestone

### M7 — Vision-guided SSVEP Target Binding

Status: Completed / PASS

The formal M7 Unity application is `m7_unity6000/` on Unity `6000.0.66f2`. Quest 3 acceptance confirmed eligible detection, StableTarget state/ID continuity, stable world anchors, and no more than three world-space black/white SSVEP targets. The fixed mapping is slot 0 → 7.2 Hz, slot 1 → 9 Hz, and slot 2 → 12 Hz, implemented with the verified shared-frame-origin `5/4/3` frame-driven controller.

M7 does not include robot control, dynamic-object tracking optimization, or any revival of the M7.4 RGB→Environment Depth UV route. M8 now owns the completed EEG selection integration and active downstream result boundary.

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

## Historical M0 Startup Backlog (Superseded)

The startup checklist has been superseded by the M1–M8 completion records below. Its old “not started” labels are not current status.

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

## Completed Milestone

### M5 — Stimulus Timing / EEG Trigger Synchronization

Status: Completed / PASS

On the verified three-target frame-driven SSVEP baseline, design and implement stimulus start/stop timing records and an EEG trigger synchronization interface.

Current substage:

- M5.0 — Existing EEG / Trigger Architecture Audit & Synchronization Design: Completed
- M5.1 — Unity Stimulus Event Model and Local Timing Records: Completed / PASS
- M5.2 — Quest-PC Trigger Transport and Clock Synchronization: Completed / PASS
- M5.3 — EEG Sample Association and Offline Trigger Alignment: Completed / PASS

M5.3 ND8 acquisition runtime prerequisite:

- The vendor `neuro_dance` 7.3 SDK requires an isolated external Windows x64 CPython 3.9 environment because its `core.pyd` depends on the CPython 3.9 ABI.
- The external environment smoke test passed for `pyserial==3.5`, NumPy, `neuro_dance.core`, `neuro_dance.nd_device_process`, and the project acquisition adapter import. No serial port or device operation was performed.
- Vendor SDK files, native extensions, and the Python runtime remain outside the Git repository. Real ND8 packet/timestamp validation is still required before hardware-timed sample association.

M5.3 first serial acquisition validation (2026-08-18):

- The vendor SDK opened `COM11` and produced 72 callback packets during a 15-second, explicitly configured 1000 Hz run. Packet shape was consistently 8 channels × 200 samples; SDK timestamp deltas were exactly 200 ms and the metadata timeline reported no continuity anomalies.
- The complete raw recording was nevertheless a single constant value across every channel, sample, and packet while the SDK also reported that the dongle was not ready. This confirms serial transport and packet cadence only; it does not validate physiological EEG, packet first-sample semantics, or real stimulus-to-sample association.
- The raw and metadata session is preserved outside Git under the configured EEG study root. A user-performed dongle/host readiness check is required before another acquisition validation.

M5.3 second serial acquisition validation (2026-08-18):

- The adapter now performs the vendor `host_mac_info()` query after serial transport starts and waits for the documented `host_mac_received()` callback before it configures 1000 Hz or enables EEG. The callback was observed (only the final four MAC characters were retained); the initial SDK not-ready heartbeat occurred before that callback and did not block streaming.
- The 15-second run produced 74 packets of 8 channels × 200 samples, but raw data remained a single constant value. One SDK timestamp interval was 136 ms rather than the expected 200 ms; this is now recorded by the timeline as `timestamp_delta_mismatch`. Valid physiological EEG and real sample association remain unverified.

M5.3 third serial acquisition validation (2026-08-18, device correctly worn):

- The 15-second host-MAC-ready run again produced 74 packets of 8 channels × 200 samples. Packet timestamps were 200 ms apart except for a local 201/199/201 ms sequence; the prior 136 ms interval did not recur. PC receive timing remained approximately 200 ms with normal host-side jitter.
- Python received non-constant raw values on channels 1 and 4, while the remaining six channels were still constant at the observed placeholder value. The SDK console also emitted repeated `timestamp not sync` warnings. This demonstrates partial changing data flow but does not yet establish a valid full eight-channel EEG stream or hardware-timed sample association.

M5.3 90-second SDK-to-PC timestamp mapping validation (2026-08-18):

- The session contained 449 packets at 8 channels × 200 samples. Packet 50→51 changed from a non-epoch SDK timestamp domain to the Unix-millisecond domain, a severe discontinuity that must be treated as a segment boundary rather than silently fitted across.
- The following 398-packet / approximately 79.4-second segment had a PC receive UTC minus SDK timestamp median offset of approximately 311 ms (P95 approximately 353 ms), software-fit monotonic drift of approximately -75 ppm, and 20.1 ms RMS receive-time residual. No large timestamp jump recurred in that post-sync segment; only 199/201 ms millisecond quantization differences were observed.
- Repeated native `timestamp not sync` console warnings occurred during the session but cannot be assigned to packet IDs without modifying or hooking the vendor native SDK. The post-sync segment is software-level mapping evidence only. Any future stimulus-to-sample work must exclude/prevent use of the initial pre-sync timestamp segment; hardware/optical timing remains unverified.
- The initial pre-sync packets are not eligible for formal sample association. A trial may begin only after a stable Unix-millisecond post-sync segment has been observed and recorded; this is a software-level timestamp mapping gate only, not hardware timing or physical optical timing verification.

M5.3 software association pipeline (2026-08-18):

- Added one explicit end-to-end recording entry point that starts ND8 acquisition and the existing Quest-PC trigger server in the same external-data session, while preserving raw EEG, ND8 metadata, Quest events, clock-sync diagnostics, gate evidence, and derived associations as separate append-only files.
- The runtime post-sync gate rejects pre-sync/non-Unix timestamps, timestamp transitions, packet continuity breaks, and incompatible packet shape; it enters `association_ready` only after a contiguous Unix-ms segment contains at least 10 packets spanning at least 1.8 seconds with cadence within a 2 ms tolerance.
- Only events after that recorded gate time and with a recent, low-residual Quest-PC affine synchronization snapshot can produce an association. The output identifies the ND8 packet and a software-derived sample estimate, never a hardware-exact sample time.
- The vendor demo text labels its callback timestamp as a first-point time, but this remains unverified for hardware/sample-anchor semantics in this project. Derived records explicitly mark the anchor as unverified, and retain `hardwareTimingVerified=false` and `physicalOpticalTimingVerified=false`.
- An independent offline verifier recomputes Quest-PC mapping from saved four-timestamp evidence and replays ND8 metadata/gate logic before comparing packet/sample results with the live derived log. The subsequent real hardware session completed this association successfully; the remaining boundary is hardware/optical timing verification.

M5.3 real Quest + ND8 end-to-end validation (2026-08-18):

- Successful external session: `m5_3-association-20260818T140324Z-aeec2e3e` under the external EEG study root. The raw EEG/session directory is intentionally not part of Git.
- The trial recorded ordered `stimulus_started_software` and `stimulus_stopped_software` events. The configured trial ran for `2160` frames at an approximately 72 Hz Quest runtime (approximately 30 seconds).
- Start association: ND8 packet `433`, estimated sample offset `94`, estimated global sample `86694`, `associationValid=true`.
- Stop association: ND8 packet `583`, estimated sample offset `96`, estimated global sample `116696`, `associationValid=true`.
- Quest-PC affine residual RMS was approximately `5.86 ms` at start and `5.99 ms` at stop. ND8 packet-to-PC software mapping residual was approximately `20.1 ms`; reported overall software uncertainty was approximately `20.6 ms`.
- ND8 remained in post-sync segment `4` with `association_ready` and continuous packet continuity; the session contained `1563` packet metadata records.
- Final offline verification: `rawEvidenceErrors=[]`, `liveOfflineMismatchKeys=[]`, `validStimulusAssociationCount=2`, `completeValidStimulusAssociation=true`, `passed=true`.
- Targeted M5 tests passed `32/32`.
- Evidence boundary remains explicit: the result verifies Quest software event → PC software clock → stable ND8 packet → software-derived sample estimate. `hardwareTimingVerified=false` and `physicalOpticalTimingVerified=false`; ND8 hardware timing, physical optical timing, physical phase, and hardware sample-anchor semantics remain unverified.

M5 completion boundary:

- M5 — Stimulus Timing / EEG Trigger Synchronization is completed at the software/runtime and real end-to-end association evidence level defined above.
- This completion does not claim hardware-exact EEG sample timing or physical optical timing, and does not include online EEG classification, vision, robotic-arm control, or scene understanding.

M5.1 introduces an independent scene with explicit idle/start/stimulating/stop trial semantics, a temporary all-black idle state, standardized software-side stimulus events, and append-only local timing records. Software event timestamps do not represent measured physical optical onset or offset.

M5.1 Quest 3 physical/runtime acceptance:

- Android Release Build, installation, immersive OpenXR/MR startup, passthrough, three-target visibility, world-fixed behavior, parallax, visibly different flicker rates, explicit stop, persistent black idle, and runtime stability: PASS.
- The validation trial started at `commonStartFrame = 322`, stopped at `globalStimulusFrame = 2160`, and recorded `lastActiveGlobalStimulusFrame = 2159` with `stopReason = configured_frame_limit`.
- The Quest-generated JSONL contained exactly the ordered `session_started`, `stimulus_started_software`, and `stimulus_stopped_software` events; all lines were complete and the final line was not truncated.
- Quest reported an available XR refresh rate of approximately `72.000 Hz`; this remains a runtime/software observation and is not a physical optical timing measurement.

M5.2 implementation:

- TCP newline-delimited JSON transport for the unchanged M5.1 stimulus event record;
- explicit PC ACK and append-only Quest/PC transport diagnostics;
- repeated four-timestamp Quest-PC monotonic clock samples with raw values retained;
- an independent M5.2 scene whose trial start remains frame-scheduled and independent of network availability.

M5.2 Quest 3 transport and synchronization acceptance:

- TCP Quest-PC transport, ordered event delivery, explicit ACKs, periodic four-timestamp clock synchronization, and append-only Quest/PC logs: PASS.
- Quest 3 visual layout, differentiated flicker, world-fixed behavior, active-trial PC server shutdown, non-fatal stimulus continuation, normal black Idle stop, server restart, and reconnect: PASS.
- Runtime refresh observation remained approximately 72 Hz; software timing records do not claim physical optical onset or EEG timing.

M5.2 does not provide EEG sample association or online EEG processing.

Do not begin online EEG classification, vision, robotic-arm control, or scene understanding as part of M5 synchronization work.

## Historical Milestone Record

### M6 — ND8 EEG Online Classification

Status: Completed / PASS WITH WARNINGS

Current substage:

- M6.0 — Existing EEG / Decoder Architecture Audit & Experimental Design: Completed
- M6.1a — ND8 Signal & Channel Sanity Validation: Completed / PASS WITH WARNINGS
- M6.1b — Controlled Three-Class Offline Dataset Acquisition: Completed / PASS WITH WARNINGS
- M6.2a — Standard CCA Baseline: Completed / PASS WITH WARNINGS
- M6.2b — Legacy-informed FBCCA Baseline: Completed / PASS WITH WARNINGS
- M6.2 — Offline CCA / FBCCA Baselines: Completed / PASS WITH WARNINGS
- M6.3a — Window-Length Characterization: Completed / PASS WITH WARNINGS
- M6.3b — FBCCA Filter Realization Validation: Completed / PASS WITH WARNINGS
- M6.3 — Offline Characterization: Completed / PASS WITH WARNINGS
- M6.4 — Cross-Session Generalization / Robustness Exploration: Completed / PASS WITH WARNINGS
- M6.5a — Pseudo-Online Decoder Infrastructure: Completed / PASS WITH WARNINGS
- M6.5b — Continuous / Stabilized Pseudo-Online Decoding: Completed / PASS WITH WARNINGS
- M6.6a — Live-Source Integration: Completed / PASS WITH WARNINGS
- M6.6b — Real ND8 Online Diagnostic Smoke Test: Completed / PASS WITH WARNINGS
- M6.7 — Stress Online Validation: Completed / PASS WITH WARNINGS

M6.1b Session A is the complete 30/30 QC-valid baseline dataset; its acquisition verifier records `classificationPerformed=false`. M6.2a/2b completed fixed Standard CCA and FBCCA baselines, and M6.3 completed fixed window/filter-realization characterization, all as within-session evidence. M6.4 has now performed independent-session acquisition and read-only association replay. B1 has 29/30 fixed QC-valid trials (LEFT/CENTER/RIGHT = 10/9/10); trial 011 remains invalid because its clock-sync freshness exceeded the frozen five-second limit. B2's original formal runtime/manifest status remains `incomplete`, but corrected read-only replay yields 30/30 QC-valid trials (10/10/10). That B2 result is post-hoc exploratory replay evidence, not formal dataset completeness.

Frozen exploratory decoding (CH2/3/4/5/7; 1000 Hz; 0.5 s onset guard; demean-only; 7.2/9/12 Hz; three harmonics) shows a session effect. At 1.5 s, Standard CCA / NumPy FBCCA / legacy-style FBCCA are A 30/30 / 30/30 / 30/30, B1 26/29 / 28/29 / 27/29, and B2 29/30 / 29/30 / 30/30. At 1.0 s they are A 29/30 / 30/30 / 28/30, B1 26/29 / 28/29 / 26/29, and B2 29/30 / 28/30 / 27/30. These are exploratory cross-session observations, not generalized, cross-subject, online, or final-system accuracy.

M6.4 PASS means association robustness audit, real failure-mode fixes, historical replay, and fixed-subset exploratory evaluation are complete. It does not mean formal prospective cross-session generalization is proven. The fixed QC-valid subsets were created before decoding and are not outcome-selected. Association replay retains the software-derived boundary: hardware timing, physical optical timing, nominal stimulus-frequency optical verification, ND8 hardware sample anchor, and hardware-exact EEG sample timing are unverified. No immediate fourth EEG acquisition is planned.

M6.5a validates a replay-only pseudo-online software architecture: historical packet → rolling buffer → event → eligibility → frozen window → decoder → prediction. Its frozen first-decision policy is 0.5 s guard + 1.5 s analysis (2.0 s algorithmic wait), CH2/3/4/5/7 and the existing three CCA/FBCCA backends. In A/B1/B2 it reproduced the 1.5 s offline prediction for every fixed QC-valid trial: A 30/30, B1 29/29, B2 30/30 equivalence for each backend. Classification results remain A 30/30 / 30/30 / 30/30, B1 26/29 / 28/29 / 27/29, B2 29/30 / 29/30 / 30/30 (Standard / NumPy FBCCA / legacy-style FBCCA). Compute time is recorded separately from algorithmic wait and packetization. M6.5a must not use future packets, cannot establish true online performance or end-to-end latency, and does not authorize real ND8 online acquisition.

M6.5b adds fixed 0.2 s continuous replay and First / 2-Consecutive / 3-Consecutive stabilization. NumPy FBCCA characterization retains full decision coverage on A/B1/B2. First and 2-Consecutive give A 30/30, B1 28/29, B2 29/30 at 2.0/2.2 s; 3-Consecutive gives A 30/30, B1 29/29, B2 30/30 with a typical 2.4 s decision. This is exploratory engineering characterization, not policy optimization or real online validation.

M6.6a live-source integration is `Completed / PASS WITH WARNINGS`. The callback-style controller is numerically equivalent to M6.5b across all 89 fixed historical trials (A 30/30, B1 29/29, B2 30/30 agreement; zero mismatch), diagnostic-live evidence orchestration is prepared, and deterministic stop-during-compute plus new-trial-isolation concurrency gates pass. This establishes software readiness only for a separately approved short diagnostic hardware smoke test; true online accuracy, end-to-end latency, hardware timing, optical timing, and hardware sample anchoring remain unverified.

M6.6b is `Completed / PASS WITH WARNINGS`. Real session `m6_6b-live-20260820T143946Z-ac87baf2` executed Quest stimulus → software-derived association → live ND8 packets → rolling NumPy FBCCA → 2-Consecutive. It produced three valid, unique live decisions (LEFT/CENTER/RIGHT once each; 3/3 post-hoc correct), with first prediction at 2.0 s and logical final decision at 2.2 s. This is a three-trial engineering diagnostic, not formal online accuracy. One physical electrode detached during wearing; the session therefore had four effective EEG channels, and the near-rail channel is recorded only as an unusable/disconnected-channel candidate without claiming an exact electrode-to-SDK mapping or a validated four-channel configuration. Hardware sample anchor, physical optical timing, hardware-exact timing, physical end-to-end latency, generalized performance, and robot integration remain unverified.

M6.7 is `Completed / PASS WITH WARNINGS` as a non-ideal-condition `stress_online` engineering validation. Session `m6_7-formal-20260820T160940Z-0ef360f6` completed 30 randomized trials (10/10/10) with frozen CH2/CH4/CH7 (3/5 admission), 30/30 technical-valid trials, 30/30 decisions, 30/30 post-hoc correct, and 0 no-decisions. This is stress evidence, not primary formal-online accuracy. Two earlier M6.7 incomplete preflight sessions caused by real ND8 disconnects remain preserved as incomplete evidence; a separate ND8-only 120 s stability check then passed (594 packets, 593 continuous and one startup anomaly, no callback/runtime error). The fixed decoder chain is now sufficiently exercised to proceed to a separately scoped BCI decision → robot command interface integration, while hardware/optical timing, three-channel equivalence, generalized performance, and physical end-to-end latency remain unverified. The 3/5 channel admission remains an engineering rule, not a three-channel sufficiency or validation claim.

## Known Open Questions

- Horizon OS application-name metadata for sideloaded development builds
- ND8 detailed hardware parameters
- Mechanical arm model and interface
- Vision inference architecture
- Final SSVEP target frequencies
