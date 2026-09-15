# First Unavoidable Research Fork

## Current completed software state

- M10 sequential House/Tower/Bridge semantics and existing FR3/UMI MuJoCo path
  are composed successfully.
- M11 consumes only observable completed history and produces a context prior.
- M12 fuses that prior with the existing three-class EEG evidence using the
  frozen `lambda = 0.5` contract.
- M13 uses the frozen formal M6 window schedule and the transparent stopping
  defaults: fused top `>= 0.70`, margin `>= 0.20`, two consecutive eligible
  windows, and raw-EEG/fused-top agreement.
- M14, M15 and M16 software/replay infrastructure is complete and deterministic
  at its documented synthetic/historical boundaries.

## What is still unknown

The M13 real-human SSVEP result is still `PENDING`. The completed pre-EEG
hardware path only proves bounded transport and selection plumbing; it does not
determine human EEG accuracy, M13 stop-time distribution, fallback/no-decision
rate, context/EEG conflicts, user corrections, or sequential task completion.

Knowing only that the software contract can pass therefore does **not** uniquely
determine the next research direction. In particular, it cannot distinguish
whether the limiting observation will be:

- insufficient or noisy EEG evidence, suggesting calibration or uncertainty
  handling;
- premature or late stopping, suggesting a later threshold/window study;
- useful but brittle context, suggesting richer or learned context;
- systematic context/EEG disagreement, suggesting personalized fusion or a
  later adaptive-`lambda` study;
- user corrections or agency concerns, suggesting confirmation/ErrP/passive
  monitoring work; or
- a separate task/robot bottleneck that makes EEG-side research premature.

These are candidate directions, not a selected M14/M17 method. No threshold,
lambda, learned model, or task predictor should be changed from the frozen
baseline based on replay appearance.

## Minimum observations needed for the fork

The first real human acceptance should preserve paired, identity-scoped records
for baseline, shadow and (only after shadow checks) active operation. At minimum,
the resulting session should make it possible to report:

- raw EEG score trajectory, M12 fused trajectory, evaluated windows and stop
  reason/time;
- human-confirmed intended target, corrections and any target conflict;
- early-stop, fallback and no-decision counts;
- A/B/C final-target agreement and descriptive correctness where a ground-truth
  task target is defined;
- sequential completed steps, rejected selections, robot execution outcomes and
  completion-time proxy; and
- duplicate, stale, ACK, trial-boundary and privacy engineering checks.

Only after these observations exist can the project decide whether the highest
value next study is calibration/thresholds, uncertainty stopping, context
modeling, personalized fusion, user-agency sensing, or an execution-side study.

## Stop decision

This is the first unavoidable research fork because the remaining choice is
empirical rather than contract-driven. The safe next action is a user-authorized
real M13 Quest + ND8 human SSVEP acceptance using the existing M13.5
baseline→shadow→active procedure. This document intentionally does not select a
new research milestone or method.
