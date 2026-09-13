# AGENTS.md

## 1. Project Role

This repository is a long-term research project for a:

Meta Quest 3 + EEG/SSVEP + Computer Vision + Scene Understanding + Robotic Arm

brain-computer interface intelligent robotic system.

Before making meaningful changes, read:

1. `project_context.md`
2. `docs/status/PROJECT_STATUS.md`
3. the nearest applicable `AGENTS.md`

---

## 2. User Background

The user is an undergraduate student and is still learning:

- Unity / XR development
- EEG signal processing
- Git and GitHub
- software engineering
- large-project organization

Therefore:

- explain important engineering decisions in Chinese;
- do not assume the user understands project structure or framework conventions;
- explain why a change is required, not only what to type;
- prefer simple and maintainable solutions over unnecessarily complex architectures.

---

## 3. Default Working Mode

Default behavior:

1. Inspect before modifying.
2. For non-trivial changes, briefly state the proposed approach.
3. Make the smallest change that solves the current task.
4. Verify the change where possible.
5. Summarize exactly what changed.

Do not rewrite large parts of the project unless required.

---

## 4. Safety Rules

Do NOT:

- delete original research data;
- modify files under `reference/` unless explicitly requested;
- run unknown `.exe`, `.dll`, installers, or legacy binaries without explicit permission;
- install or upgrade system dependencies without explicit permission;
- upgrade Unity, Meta XR SDK, OpenXR packages, Python versions, or major dependencies without explicit permission;
- overwrite raw EEG datasets;
- invent hardware behavior that has not been verified on a real device.

`reference/` should be treated as read-only source material by default.

---

## 5. Module Boundaries

Main modules:

- `vr_stimulus/`
- `vision/`
- `eeg/`
- `robot_arm/`
- `integration/`
- `experiments/`

Keep modules loosely coupled.

### VR stimulus

Responsible for:

- Quest 3 XR environment
- Passthrough
- SSVEP visual stimuli
- target position
- timing and synchronization

### Vision

Responsible for:

- camera acquisition
- object detection
- object localization
- output of detected-object information

Vision code should not directly implement EEG decoding or robotic-arm logic.

### EEG

Responsible for:

- ND8 acquisition
- preprocessing
- SSVEP classification
- decoder evaluation

### Robot arm

For the M9 virtual manipulation baseline, reuse the Franka FR3 + UMI gripper + MuJoCo baseline on the existing branch feature/add-robotArm-simulation. This repository owns the BCI-to-task/command interface, integration, execution status/feedback, and end-to-end experiments; do not rebuild the simulator, IK, trajectories, or low-level controller by default. Do not switch to, merge, or modify that branch unless a task explicitly authorizes the integration.

### Integration

Responsible for connecting module outputs and inputs.

Avoid making one module depend directly on implementation details of another.

---

## 6. SSVEP-Specific Rules

SSVEP timing is experiment-critical.

When working on visual stimulation:

- do not implement stimulation using naive long-delay timers alone;
- prefer render-frame-aware stimulus scheduling;
- distinguish requested frequency from verified physical display frequency;
- record refresh rate where possible;
- record frame/timing information needed for later validation;
- report dropped-frame risks;
- remind the user that real display timing must eventually be verified experimentally.

Do not claim a stimulus is physically accurate only because software parameters say so.

---

## 7. EEG-Specific Rules

When working with EEG:

- preserve raw data;
- document sampling rate and channel layout;
- record preprocessing parameters;
- avoid train/test data leakage;
- keep baseline algorithms reproducible;
- distinguish confirmed implementation details from inference.

Existing Neurodance / Drone2.1 code is a reference implementation, not automatically production code.

---

## 8. Git Rules

Never infer the working baseline from a branch name or older documentation. Before Git mutations, inspect the repository root, current branch, HEAD, upstream, and working-tree state. Preserve the current branch unless the task explicitly asks for a branch change. Do not switch, merge, rebase, reset, clean, or push without explicit task authorization.

For isolated feature work, prefer branches such as:

- `feature/vr-stimulus`
- `feature/vision`
- `feature/eeg-decoder`
- `feature/robot-control`
- `feature/integration`

Do not create unnecessary branches for tiny documentation corrections during initial setup.

Before committing:

- inspect `git diff`;
- avoid committing generated files;
- avoid committing secrets or credentials;
- avoid committing large generated Unity caches.

Use concise conventional-style commit messages where practical.

Examples:

- `chore: initialize project structure`
- `feat(vr): add fixed flicker target`
- `feat(eeg): add FBCCA baseline`
- `docs: update project status`
- `fix(vr): correct stimulus timing logic`

---

## 9. Documentation Rules

Update documentation when project state actually changes.

### Update `project_context.md` when:

- project goals change;
- major architecture changes;
- hardware platform changes;
- major technical direction changes.

Do not update it for every small bug fix.

### Update `README.md` when:

- setup instructions change;
- project structure changes significantly;
- another developer needs new instructions to run the project.

### Update `docs/status/PROJECT_STATUS.md` when:

- a milestone starts;
- a milestone finishes;
- current priorities change;
- a module changes from planned to active/completed.

### Add a development log when:

- a meaningful experiment or milestone is completed;
- a difficult problem is solved;
- information will be useful for later reports or papers.

### Add an ADR under `docs/decisions/` when:

- selecting an engine/framework;
- choosing an important algorithm;
- changing architecture;
- making a difficult-to-reverse technical decision.

---

## 10. Definition of Done

A coding task is not complete merely because code was written.

A task should have:

1. implementation;
2. reasonable verification;
3. clear instructions for any real-device verification still needed;
4. no unexplained errors;
5. documentation update when the project state changed.

For hardware/XR/EEG tasks, explicitly distinguish:

- code-level verification;
- simulator/editor verification;
- real-device verification.

---

## 11. Project Phase and Source of Truth

M1–M8 are completed engineering capabilities with their documented warnings and evidence boundaries preserved. The real-world Passthrough / Quest Camera / YOLO / StableTarget / EnvironmentRaycast route remains available as a completed capability and future extension; it is not the current research benchmark.

The current research direction and M9–M15 sequence are recorded in docs/roadmap/context-aware-bci-shared-autonomy.md. PROJECT_STATUS.md is the source of truth for which milestone is current. Do not copy a milestone snapshot into this file or resume an older M8/M8.5 task based on historical status text.

For M9, EEG supplies the next logical block selection. Quest, PC, and MuJoCo responsibilities and the logical block-ID boundary are defined in the roadmap. Reuse the existing Franka FR3 + UMI + MuJoCo baseline rather than rebuilding low-level robot capabilities.

## 12. Long-Running / Overnight Work

For an explicitly long-running or overnight task, follow docs/agent/overnight/POLICY.md and use its plan, run-state, worklog, and handoff formats. The policy requires a repository/branch/HEAD/working-tree baseline, machine-verifiable success criteria, checkpoints, bounded retries, and explicit blocker states.

Do not push, reset, clean, delete user data, or operate real Quest, ND8, or a physical robot unless that specific plan explicitly authorizes the action. Preserve GPT/user-frozen research direction; use local engineering judgment for routine implementation details and escalate architecture, research-goal, or frozen-interface changes.
