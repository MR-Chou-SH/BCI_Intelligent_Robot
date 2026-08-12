# ADR-001: Use a Single Modular Repository

Date: 2026-08-13

Status: Accepted

## Context

The project consists of multiple closely related subsystems:

- VR/SSVEP stimulation
- computer vision
- EEG acquisition and decoding
- robotic-arm control
- system integration
- research experiments

These modules will eventually operate as one end-to-end system.

Developing them as unrelated repositories would increase integration difficulty and make it harder to share project context, interfaces, documentation and experiment history.

## Decision

Use one main Git repository:

`BCI_Intelligent_Robot`

with independent module directories:

- `vr_stimulus/`
- `vision/`
- `eeg/`
- `robot_arm/`
- `integration/`
- `experiments/`

Legacy SDKs and previous projects are stored separately under:

`reference/`

and treated as read-only reference implementations.

## Consequences

Benefits:

- one source of truth for system architecture;
- easier integration;
- shared documentation;
- unified Git history;
- Codex can inspect relationships across modules.

Risks:

- repository may become large;
- module boundaries must be maintained;
- large datasets and generated files must not be committed carelessly.

## Alternatives Considered

### Separate repository for every subsystem

Rejected for the initial stage because the modules are tightly coupled and the team is still defining interfaces.

This decision can be revisited if individual modules later become independently reusable projects.