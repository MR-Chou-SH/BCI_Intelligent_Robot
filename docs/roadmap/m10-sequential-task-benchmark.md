# M10 — Deterministic Sequential Task Benchmark

Status: **M10 Sequential Task Benchmark = COMPLETE / SOFTWARE + MUJOCO SIMULATION PASS**.

## M10.2 sequential MuJoCo composition

`integration/m10_mujoco_sequential_e2e.py` composes one confirmed/frozen selection at a time through the existing M9 boundary:

`confirmed selection → TargetId → logical block ID → M10 preflight → existing M9 dispatcher → FR3/UMI MuJoCo result → M10 commit`

The preflight rejects a wrong-order logical ID before the dispatcher is called. A valid step is committed only after the M9 execution result is successful; a backend or adapter failure leaves the M10 state unchanged. The orchestration layer does not change M9 public interfaces, the M10.1 state machine, the M9 scene binding, or the reused low-level planner/controller.

The Stage A software gate passed on 2026-09-15:

- House, Tower, and Bridge each completed their four frozen steps with four M9 dispatches and four `place_ok` MuJoCo outcomes.
- The representative House `block_sim_01 → block_sim_03` wrong-order case retained the accepted prefix, rejected before dispatch, and recorded zero robot executions for the rejected target.
- Public evidence contained no `obj_N` simulator-internal names.
- The focused M10.2 tests passed 3/3; the M10.1/M9 targeted regression passed 35/35.

Evidence: `docs/agent/overnight/runs/m10-2-to-m11-campaign-20260915T092723Z/evidence/m10-2-sequential.jsonl` and `verification/m10-2-summary.json`.

## Purpose and boundary

M10 establishes a reproducible sequential-manipulation task model and deterministic episode benchmark. M10.1 adds fixture-driven formal acceptance, unambiguous case/outcome semantics, branching evidence, and append-style machine-readable episode evidence. It does not infer what a user intends.

**M10 = deterministic sequential task benchmark.**
**M11 = context-aware intent prediction.**

The task package is pure Python and does not depend on Unity, Quest, EEG, SSVEP, MuJoCo, simulator-internal IDs, robot kinematics, or hardware.

## Frozen task definitions

| Task | Ordered logical block IDs |
|---|---|
| House | `block_sim_01` → `block_sim_02` → `block_sim_03` → `block_sim_04` |
| Tower | `block_sim_01` → `block_sim_03` → `block_sim_02` → `block_sim_04` |
| Bridge | `block_sim_01` → `block_sim_02` → `block_sim_04` → `block_sim_03` |

All tasks share the first step. House and Bridge share the `block_sim_01`, `block_sim_02` prefix and then branch to `block_sim_03` versus `block_sim_04`. Tower branches at step 1 by choosing `block_sim_03` after `block_sim_01`. The machine-readable definitions and sample episodes are in `integration/fixtures/m10/task_definitions.json`.

## State and transition semantics

`TaskDefinition` stores `taskId`, `taskName`, and `orderedLogicalBlockIds`. `TaskState` stores `currentStep`, `completedSequence`, `remainingSequence`, and one of `not_started`, `in_progress`, `completed`, or `invalid`. `currentStep` is the zero-based index of the next expected item; it is 4 for a completed four-step task. Each accepted transition reports its own zero-based `stepIndex` before advancing.

An exact expected logical ID is accepted and advances the state. An unknown ID or known-but-out-of-order ID rejects the transition and makes the episode `invalid`; the attempted ID is not appended and the valid completed prefix/remaining suffix are preserved. Invalid episodes remain terminal until reset. A request after completion is rejected with `task_already_completed` while preserving the completed state. Reset returns the same deterministic `not_started` state for any task.

`TaskContext` exposes only `taskId`, `stepIndex`, completed IDs, remaining IDs, and `validNextLogicalBlockIds`. Before completion, the latter is the singleton expected next block; it is empty for completed or invalid state. No scores, probabilities, priors, confidence, EEG evidence, or predictions are computed.

The formal benchmark separates `benchmarkCaseStatus` (`PASS` / `FAIL`) from `episodeOutcome` (`completed`, `valid_incomplete`, or `invalid`). A declared negative case passes when the expected rejection occurs. A valid partial prefix is `benchmarkCaseStatus=PASS` with `episodeOutcome=valid_incomplete`. A post-completion rejection can pass while the final episode remains `completed`.

## Reproduction

From the repository root:

```powershell
python -B -m integration.m10_task_benchmark_runner --task house
python -B -m integration.m10_task_benchmark_runner --task tower
python -B -m integration.m10_task_benchmark_runner --task bridge
python -B -m integration.m10_task_benchmark_runner --task house --logical-block-ids block_sim_01,block_sim_03
python -B -m unittest integration.test_m10_task_benchmark -v
python -B -m integration.m10_benchmark_acceptance --evidence-path artifacts/m10/formal_acceptance_episodes.jsonl
```

The first three commands emit deterministic JSON progression ending in `episodeOutcome=completed`. The wrong-order replay emits the accepted red prefix followed by a `wrong_order` transition and exits nonzero because that standalone episode is invalid. The formal acceptance command executes all seven fixture cases, emits a deterministic summary, appends nine JSONL records (seven episode records and two branching records), and exits zero only when all expectations pass. The tests and acceptance use the Python standard library and run without MuJoCo or hardware.

## What this prep establishes

- Fixed House, Tower, and Bridge sequences over the four existing logical block IDs.
- Immutable task/state/context values and deterministic valid/invalid progression.
- Machine-readable full, partial, wrong-order, unknown-ID, and post-completion examples.
- A command-line episode runner and formal acceptance entry point with canonical, partial, negative, branching, deterministic-replay, and logical-ID-isolation checks.
- A stable summary with case counts and per-task results, plus append-only `episodeEvidence` / `branchingEvidence` JSONL records.

## M10/M11 boundary

`TaskContext.remainingLogicalBlockIds` and `TaskContext.validNextLogicalBlockIds` are authoritative evaluator/oracle fields for M10. They are not legal future M11 predictor inputs. M11 must not use these answer-bearing fields to fabricate prediction results.

M10.1 and M10.2 do not establish human task comprehension, task recognition, intent inference, context prediction, EEG/context fusion, dynamic stopping, or experimental performance. M10.2 is a software + MuJoCo simulation composition only; it does not claim Quest, EEG/ND8, physical robot, or real-world benefit acceptance. M11 may study context-aware intent prediction as a separately authorized milestone. M12+ work remains out of scope until its fusion contract is frozen.
