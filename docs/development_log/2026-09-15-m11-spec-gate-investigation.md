# M11 — Context-aware Intent Prediction Specification Gate

Date: 2026-09-15
Status: **M11 NOT STARTED / SPECIFICATION GATE BLOCKED**.

## Authoritative-source audit

The bounded investigation used the current project sources of truth:

- `docs/roadmap/context-aware-bci-shared-autonomy.md` names M11 as studying `P(Task | History)` and `P(NextTarget | Context)`, and names M12 as comparing EEG-only, context-only, and context-plus-EEG selection.
- `docs/roadmap/m10-sequential-task-benchmark.md` freezes the M10 task/state semantics and explicitly states that `TaskContext.remainingLogicalBlockIds` and `TaskContext.validNextLogicalBlockIds` are evaluator/oracle fields, not legal M11 predictor inputs.
- `integration/m10_task_benchmark.py` implements those fields as answer-bearing deterministic state context; it contains no predictor, probability, confidence, EEG evidence, or human-label contract.
- `docs/status/PROJECT_STATUS.md` records M10 closeout as software + MuJoCo simulation only and points to M11 as the next gate; the project context describes the research direction but does not add an M11 input/output contract.

## Findings

The current authoritative sources do not freeze:

1. whether task identity is visible to the predictor;
2. the exact observable input schema for history/context/scene state;
3. whether the target is task identity, next logical block, an action, or a structured joint output;
4. probability and confidence semantics, including whether outputs must be calibrated;
5. the ground-truth labeling protocol and split/episode boundaries;
6. the baseline hypothesis to implement first;
7. machine-verifiable acceptance metrics and required evidence format.

The M10 oracle fields are a fixed leakage prohibition, not a substitute for these choices. Feeding `remainingLogicalBlockIds` or `validNextLogicalBlockIds` into a predictor would fabricate a result by exposing the answer.

## Persisted options, without selecting one

The repository can support a later minimal transparent baseline after GPT/user freezes the contract. Plausible choices include:

- an empirical frequency/table baseline over an explicitly observable history;
- a deterministic finite-state/context baseline whose inputs exclude answer-bearing oracle fields;
- a separately specified probabilistic next-target baseline with declared calibration and held-out episode evaluation.

These are options only. This run does not choose task visibility, target definition, probability semantics, labels, or acceptance metrics; doing so would be a high-level research decision.

## M12 gate

M12 is not entered. Before context-plus-EEG fusion, the authoritative specification must also freeze the M11 output, EEG input/evidence semantics, fusion rule/math, calibration and normalization, output semantics, offline/replay acceptance, and dynamic-stopping scope. The current roadmap does not freeze them.

## Decision

**No M11 or M12 implementation is authorized by the current repository specification.** The safe next action is for GPT/user to freeze the missing M11 contract, after which a new bounded software task can implement and evaluate the smallest reproducible baseline. No hardware, Quest, ND8, physical robot, or research-benefit claim was made.
