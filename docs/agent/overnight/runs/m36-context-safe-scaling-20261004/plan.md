# M36 Context Safe Scaling — Run Plan

## Run identity

- Run ID: m36-context-safe-scaling-20261004
- Owner / requesting task: M36 autonomous software research run
- Created at (UTC): 2026-10-04T10:53:22Z
- Status: IN PROGRESS

## Objective

- Determine whether leakage-safe, grouped/nested out-of-fold reliability gates can raise Context intervention coverage while preserving EEG-dominated safety across the historical 118-trial corpus.
- Follow `M36_CONTEXT_SAFE_SCALING_PROTOCOL.md` as copied byte-for-byte to the M36 artifact directory.
- Complete Track 1 (A+B1+B2, 88 trials, five channels) and Track 2 (A+B1+B2+S7, 118 trials, common three channels); preserve EEG argmax as the only emitted class.
- Machine-verifiable success: every trial has out-of-fold results for each applicable track; no outer/inner trial leakage; no future EEG in features; gate hyperparameters and calibrators are nested within outer training; neutral and non-applied conditions exactly match frozen EEG-only; both wrong Context targets are evaluated; saved metrics recompute; source EEG hashes unchanged; report and required figures/audit pass.

## Repository baseline

- Repository / Git root: C:\Users\zsh21\Desktop\BCI_Intelligent_Robot
- Starting branch / HEAD: codex/m35-controlled-context-causality / 41c0f36c83cc0a4effde549f013c41f433dbfcc3
- Starting upstream: origin/codex/m35-controlled-context-causality
- Origin: https://github.com/MR-Chou-SH/BCI_Intelligent_Robot.git
- Starting working-tree state: 4,355 pre-existing status entries; zero staged entries. Exact status, tracked diff, staged list, and untracked list are preserved in `baseline_*.txt` / `baseline_git.json` in this run directory.
- Required checks before Git mutation: compare live status to `baseline_status.txt`; confirm branch and HEAD; inspect all exact staged paths; preserve every baseline entry.

## Scope and authorization

- Allowed reads: M34/M35 frozen code, manifests, reports, artifacts; the four raw EEG session directories named in M34 `data_manifest.json` on D:\EEG_Study (read-only); M36 protocol.
- Allowed writes: `research_analysis/m36_context_safe_scaling_20261004/attempt-01/**`; `docs/agent/overnight/runs/m36-context-safe-scaling-20261004/**`; one M36 status entry in `docs/status/PROJECT_STATUS.md` as required by AGENTS.md; new branch `codex/m36-context-safe-scaling`.
- Allowed Git actions: create/switch to the explicitly requested M36 branch; selectively stage M36 outputs and the M36 status entry only; commit and push this M36 checkpoint; verify local/upstream/remote hashes.
- Explicit exclusions: never edit raw EEG or M34/M35 frozen artifacts; no decoder retuning; no COM11, ND8, Quest, ADB/MQDH, live EEG, robot, MuJoCo, or VLA operation; no production runtime changes; no reset, clean, stash, force push, `git add .`, or `git add -A`.
- Frozen decisions: FBCCA-003/E200_M175_S2, 0.5 s nominal onset alignment, slot mapping 0/1/2 = 7.2/9/12 Hz, S7 common channel subset [2,4,7], EEG argmax is always the emitted class.
- Data roots: the exact four source directories and raw files referenced by M34 manifest. Raw hashes will be checked before and after.

## Grouping and validation design

- Audit unique recording IDs, source manifests, start/stop chronology, protocol and channel metadata. Keep each complete trial and all its windows in one fold.
- Primary outer groups use acquisition campaign grouping: A; M6.4 campaign containing B1+B2 (same experiment/date, separate recording IDs 28 minutes apart); S7 stress session. Also run unique-recording-session grouped OOF as a sensitivity analysis. B1 and B2 are not silently treated as independent participants.
- Track 1 uses A/B1/B2; Track 2 uses all four recordings. Where nested training has >=2 groups, inner validation leaves out whole groups. If only one training group remains, use chronological blocked trial folds with one-trial boundary embargo. Hyperparameters, scalers, calibration and operating thresholds use outer-train data only.
- Build causal trial-time features from current/past EEG only; future-derived correctness and final-consistency are training/evaluation labels only. Compare M35 and improved deterministic rules, NumPy L2 logistic reliability gate with nested calibration/risk selection, and a small tree only if an already-installed stable implementation is available.
- Evaluate congruent, both wrong-context classes, neutral, full q_top sweep, oracle ceilings, M35 baseline, and stored M33 secondary only; no semantic model calls.

## Steps and verification

| Step | Atomic result | Verification | Expected result | Attempts / timeout | State |
|---|---|---|---|---|---|
| 1 | Capture baseline, copy protocol, create M36 branch, audit acquisition groups and raw hashes | Baseline comparator; SHA-256; `verify_grouping.py` | Baseline preserved; 118 trials and grouping evidence accounted | 1; 30 min | COMPLETE |
| 2 | Reproduce frozen M34/M35 trajectories and build Track 1/2 causal feature/label tables | M36 runner `--stage features`; trajectory parity and feature-causality audit | 88 and 118 trials, all nine windows, no future-sample feature inputs | 3 evidence-based retries; 60 min | COMPLETE |
| 3 | Create grouped outer folds and nested inner folds | `verify_m36_artifacts.py --folds-only` | No trial/group overlap, every trial tested once per applicable scheme | 3; 60 min | COMPLETE |
| 4 | Reproduce M35 and evaluate deterministic multi-view/adaptive reliability rules | Nested runner + OOF audit | Parameters selected only inside outer train; score argmax remains class | 3; 90 min | COMPLETE (outer-group safety did not hold) |
| 5 | Fit regularized logistic and nested calibrated selective gates; test tree only if already available | Nested runner + model audit | Outer-test data never used in fit/calibration/threshold selection | 3; 120 min | COMPLETE (no cross-group safe gate found) |
| 6 | Risk-coverage and SAFE-STRICT/SAFE-95/SAFE-99 analysis under congruent and both incongruent targets | OOF metrics recomputation | Paired per-trial outcomes, group-specific risk and confidence intervals | 3; 90 min | COMPLETE (no safety target supported across OOF groups) |
| 7 | Failure mining, session-shift analysis, oracle efficiency, stored M33 secondary | Deterministic audit | Every induced failure traced; no real semantic calls; trial/scenario counts separated | 3; 90 min | COMPLETE |
| 8 | Required figures, central tables, final report, self-review | Artifact verifier / SVG parse / report audit | All 22 report questions, all 12 required figure types, claim boundaries | 3; 120 min | COMPLETE |
| 9 | Python compile, M36 audits, repository software-default profile | Bundled runtime; scripts/agent/verify.ps1 with run output directory | M36 checks PASS; repository profile has no enabled failures | 3; 120 min | COMPLETE |
| 10 | Selective Git checkpoint and remote verification | git diff --cached --check, exact path allowlist, commit/push, git ls-remote | Only M36 deliverables; local, upstream, and remote SHA match | 2; 45 min | IN PROGRESS |

## Runtime and recovery

- Available runtimes: local research venv CPython 3.9.13/NumPy 2.0.2; bundled CPython 3.12.14/NumPy 2.3.5. SciPy/sklearn are absent; do not install dependencies. Use NumPy/std-lib implementations; if tree library absent, record NOT_AVAILABLE and continue with rule/logistic models.
- Verification output: M36 artifact directory and this run's `verification/`.
- Checkpoint after grouping, features, folds, each model family, complete OOF, final audit, and Git.
- Each hypothesis gets at most three evidence-distinct retries; a test failure starts an investigate/fix/retest loop, not a blocker.
- On resume, read this plan/state/worklog/handoff, revalidate branch/HEAD/status against baseline, inspect existing artifacts before rerunning anything.

## Completion conditions

- Complete only when every M36 requirement has authoritative evidence, both data tracks use every eligible trial, nested grouped OOF and all gate families are audited, report/results/figures pass, no raw EEG changed, and Git remote SHA equals local HEAD.
- Block only for irrecoverable missing data/metadata or unavailable essential tooling after reasonable software alternatives are exhausted. Prospective EEG is a conclusion after exhausting software approaches, not an initial blocker.
- Hardware and human validation are NOT ATTEMPTED and are not needed for this software-only milestone.
- Handoff: `docs/agent/overnight/runs/m36-context-safe-scaling-20261004/handoff.md`.
