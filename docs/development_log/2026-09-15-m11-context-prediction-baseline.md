# 2026-09-15 — M11 Context-only Next-target Prediction Baseline

## Result

M11 passed its software-only formal acceptance:

- 12/12 focused predictor tests;
- 11/11 acceptance records;
- 12/12 M10 task-benchmark regression tests.

The acceptance phrase is: `M11 CONTEXT PREDICTION BASELINE = SOFTWARE PASS`.

## Frozen boundary

The predictor is evaluated in a hidden-task setting over the existing House,
Tower and Bridge definitions. Its only inputs are the observable completed
logical-block history, the observable frozen four-block catalogue, and the
derived step index. It does not accept the true task ID, M10 remaining-sequence
or valid-next oracle fields, future steps, EEG evidence or M12 fused output.

The baseline starts with a uniform task hypothesis prior, filters hypotheses
whose task prefix is incompatible with the observed history, renormalizes, and
aggregates next-target probability mass. Equal mass is retained as a tie in
`topTargets`; no ID-order or random winner is selected. Invalid histories are
explicitly invalid and terminal histories expose no fifth target.

## Evidence boundary

Canonical probability distributions, invalid/terminal handling, probability
normalization, deterministic replay, hidden-task anti-leakage, and logical-ID
privacy passed in
`docs/agent/overnight/runs/m11-to-m12-campaign-20260915T103723Z/`.
This is a context-prior software baseline, not an accuracy or human-intent
claim. Quest, ND8, real EEG, physical robot and human study gates were not
accessed.
