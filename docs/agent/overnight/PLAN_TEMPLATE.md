# Overnight Run Plan

Copy this file for one run. Replace every prompt before starting work. The plan is an execution contract, not a project roadmap.

## Run identity

- Run ID:
- Owner / requesting GPT task:
- Created at (UTC):
- Planned duration / deadline:
- Status: NOT STARTED

## Objective

- One-sentence goal:
- Why this work is in scope now:
- Machine-verifiable success criteria:

## Repository baseline

- Absolute repository path:
- Git root:
- Branch:
- HEAD:
- Upstream (if available):
- Working-tree state and existing changes:
- Required baseline check before edits:

If any baseline field changes unexpectedly, stop and record BLOCKED.

## Scope and authorization

- Allowed paths:
- Allowed mutations:
- Explicit exclusions:
- Frozen research/design decisions to preserve:
- Hardware / GUI actions explicitly authorized (default: none):
- Git actions authorized (default: no push; no reset/clean):
- Data roots that may be read:
- Data/output paths that may be written:

## Steps and verification

- Allowed verification profile:
- Verification command:
- Verification output directory:
- Overall success criteria (profile result and required checks):

| Step | Atomic result | Exact validation command | Expected result | Attempts / timeout | State |
|---|---|---|---|---|---|
| 1 |  |  |  |  | NOT STARTED |

For every code change, define an automated check that can run without unapproved hardware or GUI interaction. Record when a step instead needs human validation.

## Runtime and recovery

- Exact executable/runtime and version:
- Required environment variables or inputs:
- Checkpoint cadence:
- Last-green verification must include command, exit code, source/HEAD, and input identity:
- Resume instructions:
- Blocker policy and maximum retries:

## Completion conditions

- Definition of complete:
- Conditions that mean BLOCKED:
- Conditions that mean NOT ATTEMPTED:
- Human validation required:
- Morning handoff path:

Do not start until the baseline is captured and the success criteria and allowed actions are concrete.
