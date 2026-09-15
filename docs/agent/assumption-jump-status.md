# Assumption-Jump Campaign Status

Last updated: 2026-09-15 (UTC)

This ledger separates evidence status from development assumptions. `ASSUMED_PASS`
is a planning label for software composition only; it is never a substitute for
real human EEG evidence or a performance claim.

| Item | Evidence status | Development status | Boundary |
|---|---|---|---|
| M13 real-human SSVEP acceptance | `PENDING` | `ASSUMED_PASS` for downstream software composition | Real hardware pre-EEG path passed with warnings; no human EEG was collected in this campaign. |
| M13 context-aware stopping policy | `SOFTWARE / REPLAY PASS` | `CONFIRMED` | Frozen 0.70 fused threshold, 0.20 margin, two consecutive windows, raw EEG confirmation. |
| M14 full sequential shared-autonomy closed loop | `SOFTWARE / MUJOCO / REPLAY PASS` | `CONFIRMED` | Synthetic EEG/context trajectories drive the existing M8/M9/MuJoCo seams; M10 commits only after `place_ok`. |
| M15 comparative benchmark and ablation framework | `SOFTWARE / REPLAY PASS` | `CONFIRMED` | Paired A/B/C conditions and descriptive historical replay; no tuning or statistical claim. |
| M16 experiment/dataset/reproducibility infrastructure | `SOFTWARE READY` | `CONFIRMED` | Manifest, deterministic schedule, stable session layout, resume-safe dry run, analyzer/report composition. |
| M13 real Quest + ND8 human acceptance | `PENDING` | `NOT ATTEMPTED` here | Requires user-operated Quest, ND8 electrodes and physiological EEG. |
| Sequential human shared-autonomy research result | `PENDING` | `NOT ATTEMPTED` | No accuracy, latency, early-stop benefit, cognitive-load or generalized performance claim is made. |

## Evidence vocabulary

- `OBSERVED`: directly measured or parsed from an authorized run.
- `SYNTHETIC`: deterministic fixture or generated trajectory.
- `HISTORICAL_REPLAY`: previously recorded EEG score trajectory replayed read-only.
- `REAL_HARDWARE_NON_PHYSIOLOGICAL`: hardware path exercised without human EEG.
- `REAL_HUMAN_EEG`: physiological human recording; not present in this ledger yet.
- `ASSUMED_PASS`: development-only assumption explicitly marked as such.

## Campaign endpoint

The M14→M16 software composition reached the first research fork. The next
choice depends on human observations that are not available from contract-level
software evidence. Do not turn the assumption into a milestone completion claim
or start a new research method automatically.
