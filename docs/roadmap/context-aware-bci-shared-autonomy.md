# Context-aware BCI Shared Autonomy Roadmap

## Research direction

The research target is a Brain-AI Shared Autonomy Robot. EEG provides low-bandwidth intent evidence directly from the user. Task context, history, and scene state can support more autonomous decisions as confidence rises; uncertain decisions should return more authority to the user.

M1–M8 are completed engineering capabilities with their warnings and evidence boundaries preserved. The real-world Passthrough, Quest Camera, YOLO, StableTarget, EnvironmentRaycast, and SSVEP binding route remains available as a completed capability and future extension. It is not the current primary benchmark.

## System responsibilities

- **Quest 3 / Unity:** virtual tabletop, blocks, SSVEP presentation, user interaction, and visualization.
- **PC:** EEG decoding, selected-object mapping, later context model and intent fusion, task state, and shared-autonomy logic.
- **MuJoCo:** Franka FR3 + UMI gripper simulation, robot dynamics, IK, trajectory execution, Pick / Place, and execution feedback.

Use a shared logical block ID between Quest and MuJoCo, for example `block_red_01`, `block_green_01`, or `block_blue_01`. The EEG selection output identifies the next block; task logic chooses the operation from context.

## Milestones

### M9 — Virtual Manipulation Baseline

Establish a controlled virtual tabletop benchmark: Quest selection → logical block ID → PC interface → MuJoCo Franka FR3 Pick / Place → execution feedback.

Reuse the existing baseline on `feature/add-robotArm-simulation`, which provides Franka FR3, UMI gripper, geometric grasping, numerical IK, segmented trajectories, and Pick / Lift / Place. Do not rebuild those low-level capabilities or train an RL policy by default. This repository owns the BCI selection output, task/command interface, integration, execution status/feedback, and end-to-end experiments.

M9 excludes VLM, LLM, context AI, reinforcement learning, and dynamic stopping.

### M10 — Sequential Task Benchmark

Define reproducible House / Tower / Bridge tasks and record the task graph, history, completed steps, available blocks, and current state.

### M11 — Context-aware Intent Prediction

Study `P(Task | History)` and `P(NextTarget | Context)` using the sequential benchmark.

Status after the user-frozen 2026-09-15 campaign: **M11 CONTEXT-ONLY NEXT-TARGET PREDICTION BASELINE = SOFTWARE PASS**. The hidden-task predictor receives only completed logical-block history, the observable frozen four-ID candidate catalogue, and the derived step index. It uses a uniform House/Tower/Bridge hypothesis prior, prefix compatibility filtering, and probability-mass aggregation. M10 `remainingLogicalBlockIds` and `validNextLogicalBlockIds` remain evaluator/oracle truth and are not predictor inputs. Canonical branching, tie preservation, invalid/terminal behavior, deterministic replay, anti-leakage, probability normalization and `obj_N` isolation pass in the run evidence.

### M12 — Context Prior × EEG Evidence

Compare EEG-only, context-only, and context-plus-EEG selection.

Status after the user-frozen 2026-09-15 campaign: **M12 CONTEXT × EEG FUSION BASELINE = SOFTWARE / REPLAY PASS**. The contract reuses the M11 `ContextPrior`, the existing three-class M6/M8 SSVEP seam and explicit slot/TargetId/logical-ID mapping. It treats the existing FBCCA score vector as finite nonnegative `EEGEvidenceScore`, projects the global context prior onto the three active candidates, applies fixed `lambda = 0.5` softening toward uniform, prepares scores with `epsilon = 1e-12`, and normalizes the product as fused evidence. Context may bias but cannot veto an active candidate. The replay acceptance covers uniform/agreement/conflict/override/ambiguity/projection/mass-zero/invalid evidence/determinism and boundary checks. M12 is software/replay-only; no FBCCA rewrite, SSVEP redesign, dynamic stopping, Quest, ND8, real EEG or robot execution is in scope.

### M13 — Dynamic Stopping

Study reduction of the time required for one EEG selection.

Status after the 2026-09-15 software campaign: **M13 CONTEXT-AWARE DYNAMIC STOPPING = SOFTWARE / REPLAY PASS; REAL QUEST + ND8 ACCEPTANCE PENDING**. M13 consumes repeated M12 `FusedTargetEvidence` snapshots on the existing formal M6 window grid and uses frozen engineering defaults: fused top `>= 0.70`, top1-top2 margin `>= 0.20`, raw EEG top equal to fused top, and two consecutive eligible windows. A target change or ineligible window resets the confirmation count; the first eligible window never stops. Valid final evidence produces `full_window_fallback`; unresolved ties and invalid evidence produce explicit `no_decision`.

The 10-case synthetic trajectory acceptance, deterministic evidence output, thin opt-in M8 final-decision adapter and software readiness checks pass. Read-only replay of the existing M6.5b `numpy_fbcca` result fixture covered 89 trials using a predeclared synthetic M11 context overlay; it produced descriptive replay evidence only and is not a context-aware human experiment. No M6/M12 decoder or public M8 contract was modified, and M13 never bypasses Quest selection authority or the Robot Adapter. The campaign stopped at the real-device gate: live ND8 score trajectories, Quest frozen-selection ACKs and any physical timing/latency claim still require user-operated Quest + ND8 acceptance.

### M14 — Adaptive Shared Autonomy

Study reduction of the number of EEG interactions required to complete a task.

Status after the assumption-jump software campaign: **M14 FULL SEQUENTIAL SHARED-AUTONOMY CLOSED LOOP = SOFTWARE / MUJOCO / REPLAY PASS; REAL HUMAN SEQUENTIAL BCI PENDING**.

`integration/m14_sequential_closed_loop.py` composes observable-history M11
context, M12 fusion, M13 dynamic stopping, the frozen M8 final-decision seam,
the existing M9 TargetId/logical-ID dispatcher and the existing FR3/UMI MuJoCo
adapter. House, Tower and Bridge each complete four synthetic-evidence steps;
M10 advances only after a successful MuJoCo `place_ok`. No-decision, wrong-target,
robot-failure and duplicate-final-decision cases fail closed. This is software,
simulation and replay evidence, not a human sequential experiment.

### M15 — Experimental Evaluation / Paper

Consolidate benchmark protocol, comparisons, results, limitations, and publication materials.

Status after the same campaign: **M15 COMPARATIVE BENCHMARK & ABLATION FRAMEWORK = SOFTWARE / REPLAY PASS**.

`integration/m15_comparative_benchmark.py` provides paired EEG-only,
context+EEG full-window and context+EEG+M13 dynamic-stop conditions over the
same deterministic inputs, with machine-readable JSON/JSONL/CSV descriptive
metrics. The registered M6.5b historical fixture can be replayed read-only with
the existing synthetic context overlay. No parameter tuning, significance or
human-performance claim is produced.

### M16 — Experiment, Dataset & Reproducibility Infrastructure

Status after the same campaign: **M16 EXPERIMENT / DATASET / REPRODUCIBILITY INFRASTRUCTURE = SOFTWARE READY; REAL HUMAN DATASET PENDING**.

`integration/m16_reproducibility.py` creates a pseudonymous session manifest,
seeded development schedule, stable dataset layout, resume-safe checkpoint and
reuses the existing M13.5 analyzer/acceptance plus M15 benchmark. The dry run
passes without Quest, ND8, COM11, human EEG or physical robot. The final human
protocol remains intentionally unfrozen.

### M17–M20 — Fork-Parallel Preparation Tooling

Status after the 2026-09-16 fork-parallel campaign: **M17 FORK DIAGNOSTICS = SOFTWARE READY; M18 CANDIDATE SANDBOXES = SOFTWARE READY / NON-PRODUCTION; M19 EXPERIMENT-TO-REPORT PIPELINE = SOFTWARE PASS; M20 RELEASE / RECOVERY HARDENING = READY.** This is preparation infrastructure, not a selected post-M13 research method.

`integration/m17_fork_diagnostics.py` classifies observed synthetic/replay
patterns with transparent deterministic rules and reports candidate directions
without selecting one. `integration/m18_research_sandboxes.py` provides
offline-only calibration, sparse context-weight/stopping sensitivity,
uncertainty, adaptation and correction contracts; production defaults remain
frozen. `integration/m19_experiment_report.py` composes M16 artifacts into an
idempotent report package and preserves an explicit empty real-human state.
`integration/m20_release_readiness.py` audits imports/dependencies, recovery,
canonical commands and a one-command synthetic dry run. M13 real-human EEG and
the next formal research direction remain pending.

### M13.6 — MuJoCo ↔ Quest Real-Time Visualization Sync

Status after the 2026-09-16 software campaign: **M13.6 MUJOCO ↔ QUEST VISUAL
SYNC = SOFTWARE READY; REAL QUEST VISUAL ACCEPTANCE PENDING**.

`integration/m13_6_visual_sync.py` publishes the MuJoCo authoritative world as
an independent UDP latest-state-wins stream on port `11002`; the existing M8
selection/control TCP path on `11001` is unchanged. The stream carries the
seven FR3 joint values, UMI finger state, robot base pose and all four logical
block poses using the existing `block_sim_01..04` mapping. The Quest receiver
validates protocol/version/sequence/identity, interpolates the latest valid
state on the Unity main thread, and updates the existing M9 visual rig and
logical block targets. Public telemetry never exposes MuJoCo `obj_N` names.

The M13.6 protocol, coordinate transform, stale/duplicate rejection, static
Quest contract and real MuJoCo Pick/Lift/Place stream tests pass in the project
Python 3.12 environment. A four-block continuous MuJoCo stream completed all
four `place_ok` operations with monotonic sequence numbers. Unity source and
runtime auto-installation are statically verified; Unity build/device
verification and visual alignment still require a later user-operated Quest
session. This milestone does not use ND8, EEG, COM11 or the physical robot and
does not modify M11–M13.

## Scope and change control

Use `docs/status/PROJECT_STATUS.md` for the current milestone and its acceptance state. This roadmap records the user-approved research sequence; do not reopen M1–M8 acceptance work or change the sequence without an explicit GPT/user decision.

Do not switch to, merge, or modify `feature/add-robotArm-simulation` as part of unrelated work. Integrate it only in a separately scoped, authorized task.
