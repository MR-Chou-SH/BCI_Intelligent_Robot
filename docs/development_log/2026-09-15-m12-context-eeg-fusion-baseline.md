# 2026-09-15 — M12 Context Prior × EEG Evidence Fusion Baseline

## Result

M12 passed software/replay acceptance:

- 13/13 focused fusion tests;
- 12/12 replay-acceptance records;
- 75/75 relevant M8/M9/M10/M11/M12 regression tests;
- direct pure-NumPy FBCCA seam check: three frozen synthetic frequencies,
  three finite score values per input, predicted indices `0, 1, 2`.

The acceptance phrase is: `M12 CONTEXT × EEG FUSION BASELINE = SOFTWARE / REPLAY PASS`.

## Frozen fusion

M12 consumes the M11 `ContextPrior` and the existing `predict_fbcca()` fused
score vector. The three active slots remain 0 → 7.2 Hz, 1 → 9 Hz and 2 → 12
Hz. Candidate identity is resolved only through the checked-in explicit
`TargetId → logicalBlockId` virtual mapping.

The existing score is named `EEGEvidenceScore`; it is not treated as a
calibrated posterior. The global context prior is projected onto the active
set, uses a uniform active fallback when its mass is zero, and is softened with
fixed `lambda = 0.5`. Each score is prepared with `max(score, 1e-12)`, then
the context/evidence products are normalized. Consequently every active
candidate retains nonzero fused influence and context cannot veto user EEG
evidence.

## Evidence boundary

Uniform, agreement, moderate conflict, strong EEG override, half-half context
ambiguity, active projection, zero active mass, epsilon handling, invalid
evidence rejection, deterministic replay, existing-vector adaptation,
`obj_N` isolation and no-direct-robot-invocation checks passed. The old M6
decoder test module was not importable because the existing environment lacks
optional `scipy`; no dependency installation or algorithm modification was
performed. Quest, ND8/COM11, real EEG, physical robot, human study and M13
were not entered.
