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

Status after the user-frozen 2026-09-15 campaign: **M12 CONTEXT × EEG FUSION BASELINE = SOFTWARE / REPLAY PASS**. The contract reuses the M11 `ContextPrior`, the existing three-class M6/M8 SSVEP seam and explicit slot/TargetId/logical-ID mapping. It treats the existing FBCCA score vector as finite nonnegative `EEGEvidenceScore`, projects the global context prior onto the three active candidates, applies fixed `lambda = 0.5` softening toward uniform, prepares scores with `epsilon = 1e-12`, and normalizes the product as fused evidence. Context may bias but cannot veto an active candidate. The replay acceptance covers uniform/agreement/conflict/override/ambiguity/projection/mass-zero/invalid evidence/determinism and boundary checks. M12 is software/replay-only; no FBCCA rewrite, SSVEP redesign, dynamic stopping, Quest, ND8, real EEG or robot execution is in scope. Stop before M13.

### M13 — Dynamic Stopping

Study reduction of the time required for one EEG selection.

### M14 — Adaptive Shared Autonomy

Study reduction of the number of EEG interactions required to complete a task.

### M15 — Experimental Evaluation / Paper

Consolidate benchmark protocol, comparisons, results, limitations, and publication materials.

## Scope and change control

Use `docs/status/PROJECT_STATUS.md` for the current milestone and its acceptance state. This roadmap records the user-approved research sequence; do not reopen M1–M8 acceptance work or change the sequence without an explicit GPT/user decision.

Do not switch to, merge, or modify `feature/add-robotArm-simulation` as part of unrelated work. Integrate it only in a separately scoped, authorized task.
