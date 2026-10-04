# M36 — Large-Scale Safe Context Intervention for SSVEP Decoding

## PROJECT

Repository:

`C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`

This is a software-only long-run research milestone.

M36 follows M34 and M35. The EEG-only decoder is now considered sufficiently strong for the current stage. The primary problem is no longer “find a different EEG decoder”, but:

> **How can semantic Context intervene in a much larger fraction of SSVEP decisions while preserving EEG-dominated safety under both congruent and incongruent Context?**

The central goal is to move from the current regime:

- Context is useful when it applies;
- applied-trial acceleration can be ~175–200 ms;
- but safe application coverage is very low;
- and session shift can still produce Context-induced wrong early stops;

toward:

> **high-coverage, context-assisted early stopping with little or no loss of EEG accuracy, using as much of the existing 118-trial dataset as scientifically possible.**

## 1. Prior evidence from M34/M35

Treat the following as prior evidence, not as new held-out evidence.

Frozen EEG-only stack:
- three-band FBCCA;
- main 5-channel cohort `[2,3,4,5,7]`;
- S7 common usable subset `[2,4,7]`;
- 3 harmonics;
- dynamic stopping baseline `E200_M175_S2`;
- minimum EEG evidence 0.20 s;
- relative margin threshold 0.175;
- two consecutive stable EEG-top updates;
- maximum evidence 1.00 s.

M35 found:
- EEG-only mean evidence: B2 ≈ 0.412 s, S7 ≈ 0.497 s;
- Oracle congruent Context mean acceleration: B2 ≈ 190 ms, S7 ≈ 212 ms;
- applied-trial Context gain can already approach ~175–214 ms;
- the main failure is low safe application coverage and cross-session robustness;
- A/B1-selected four-update rule achieved zero Context-induced wrong early stops in development but applied to only ~6.8% of trials at the strongest Context setting;
- S7 post-hoc replay still produced Context-induced wrong early stops;
- Neutral Context reproduced EEG-only exactly;
- the dangerous failure pattern is a temporarily wrong short-window EEG top that looks stable, agrees with wrong Context, triggers early stop, but would have corrected later.

M36 must directly attack that failure mode.

## 2. Primary research question

Can we construct a **context-aware early-stopping reliability gate** that:

1. allows correct/congruent Context to intervene on a substantially larger fraction of trials;
2. preserves approximately the ~175–200 ms acceleration available when Context is useful;
3. strongly suppresses Context-induced wrong early stops under incongruent Context;
4. generalizes better across acquisition sessions / stress conditions;
5. never lets Context directly replace the raw EEG winner?

The intended mechanism remains:

`EEG = classifier`

`Context = accelerator`

Context may change **when** we stop, not **which class** EEG emits.

## 3. Primary M36 targets

These are optimization targets, not values to manufacture.

### Target A — Safe application coverage
Increase Context application rate from M35’s conservative ~6.8% toward:
- first useful milestone: >=20%
- strong milestone: >=30%
- aspirational milestone: >=50%

Do not force these percentages if unsupported.

### Target B — Applied-trial acceleration
Retain approximately `150–250 ms` mean paired gain among Context-applied congruent trials, with ~200 ms as the desired center.

### Target C — Overall acceleration
Maximize whole-cohort mean gain while maintaining safety. A useful result should substantially exceed M35’s ~12 ms overall development gain.

### Target D — Accuracy / safety
Aim for:
- no meaningful accuracy loss relative to EEG-only;
- Context-induced wrong early stops as close to 0 as possible;
- high intervention precision: `P(Context-assisted early stop is correct | Context intervened)`.

The ~95% accuracy level is desirable where supported, but do not manufacture it.

## 4. Use as much of the 118-trial dataset as possible

The user explicitly wants M36 to use the available data broadly instead of validating on only ~30 trials.

Known usable historical waveform trials:
- A: 30
- B1: 29
- B2: 29
- S7: 30
- total: 118

### Critical rule
Do **NOT** simply merge all 118 trials, train on all of them, and evaluate on the same samples.

Instead, use **out-of-fold / grouped retrospective validation** so that:

> **Every usable trial contributes to the M36 analysis, but no trial is used to fit/tune the rule that is evaluated on that same trial.**

B2 and S7 were already consumed in M34/M35. Therefore M36 is a **retrospective cross-validated algorithm-development study**, not a fresh held-out confirmation.

Do not call any M36 result “new independent held-out proof”. A future prospective dataset is still required for a final paper-grade independent claim.

## 5. Audit session/group structure before cross-validation

Re-read the M34 manifest and acquisition provenance.

Determine whether A, B1, B2, and S7 represent:
- four genuinely independent acquisition sessions;
- three acquisition sessions with B1/B2 from the same source session;
- or another grouping.

Do not assume session independence from names alone.

Create `m36_grouping_audit.json` containing acquisition source, recording/session identifier, chronology if recoverable, channel set, protocol/state differences, grouping decision, and rationale.

If B1 and B2 come from the same acquisition session, keep them in the same outer group for session-level generalization analysis.

## 6. Two complementary data tracks

Because S7 has only the common channels `[2,4,7]`, use two explicit tracks.

### Track 1 — Main 5-channel cohort
Use A+B1+B2 = 88 trials with `[2,3,4,5,7]`.

Purpose:
- strongest within-primary-cohort Context-gating development;
- maximum signal quality;
- cross-block/session validation inside the main cohort.

### Track 2 — Universal cross-session cohort
Use A+B1+B2+S7 = 118 trials using only common channels `[2,4,7]`.

Purpose:
- maximize trial usage;
- evaluate robustness to S7 stress/session shift;
- test whether a Context gate can generalize across all available acquisition conditions.

Do not silently compare 5-channel and 3-channel results as if they are the same model.

If an alternative representation safely uses all channels via per-session feature normalization without leakage, it may be explored secondarily, but the common-3-channel track must remain a clear robust baseline.

## 7. Outer validation: every trial gets an out-of-fold prediction

Construct a grouped outer cross-validation procedure.

Preferred hierarchy:
1. Leave-one-acquisition-session/group-out where possible.
2. If too few independent groups exist, supplement with blocked/grouped trial folds preserving acquisition chronology and preventing adjacent leakage.
3. Never split time windows from the same EEG trial across folds.

Each outer test trial must be scored by a gate trained/tuned without that trial and, where possible, without that acquisition group.

Save `outer_fold_assignments.csv` with trial/session/group, outer fold, training groups, validation groups, test group, channel track, and overlap audit.

Goal: obtain one out-of-fold M36 decision for every usable trial in the relevant track.

## 8. Nested model selection

For any learned gate or threshold:
- outer TEST fold must never select hyperparameters;
- use only outer TRAIN data;
- inside outer TRAIN, use nested grouped validation to choose thresholds/hyperparameters;
- then refit on full outer TRAIN;
- evaluate once on outer TEST.

Do not optimize directly on all 118 and then report performance on the same 118.

## 9. Keep EEG classification frozen

M36 is not a new EEG-decoder search.

Do not replace FBCCA unless a reproducibility bug is discovered.

For each trial/time window reproduce the frozen raw EEG score trajectory:

`S(t) = [s0(t), s1(t), s2(t)]`

The emitted class at a Context-assisted stop must always be:

`argmax S(t)`

Context must not directly rerank the class.

Do not use `argmax(EEG + lambda * Context)` as the main method. It may appear only as an explicitly labelled unsafe/ablation baseline.

## 10. Build a better early-EEG reliability representation

M35 showed that margin + short-term top stability alone are insufficient under session shift.

M36 should estimate:

> **Is the CURRENT raw EEG top likely to remain correct if we waited longer?**

This is a selective-prediction / reliability-estimation problem.

Build trial-timepoint features using only information available up to time `t`.

Candidate features should include:

### Current score geometry
- raw top score
- second score
- third score
- absolute top-second margin
- relative margin
- top/second ratio
- normalized score entropy
- score concentration

### Temporal trajectory
- top identity over previous windows
- number of consecutive stable tops
- top score slope
- margin slope
- entropy slope
- top-vs-second divergence rate
- number of previous top flips
- time since last top flip
- whether margin is monotonically increasing
- whether top score is consistently increasing
- whether second-place score is decaying

### Multi-view EEG agreement
Where feasible, derive independent or semi-independent views:
- filter-band agreement
- harmonic agreement
- channel-subset agreement
- 3-channel vs 5-channel prediction agreement in the main cohort
- leave-one-channel-out consistency
- short/long-window score consistency
- frequency-template consistency

The idea is that a truly reliable early EEG top should be supported by several independent EEG views, not merely one large margin.

Do not use future EEG samples to compute a feature at time `t`. Future samples may only create the training/evaluation label.

## 11. Define early-reliability labels carefully

For each trial and candidate stopping time `t`, evaluate at least two labels.

### Label A — Ground-truth correctness
`safe(t) = current EEG top == true class`

### Label B — Eventual frozen EEG consistency
`safe_final(t) = current EEG top == frozen EEG-only final class`

Both are useful.

The operational gate should prioritize preserving the correct EEG outcome while also reporting baseline-preservation behavior.

Do not use labels as runtime inputs.

## 12. Compare rule-based and learned reliability gates

Evaluate a focused set.

### Baseline 1 — M35 rule
Reproduce the four-update / margin-floor rule.

### Baseline 2 — Improved interpretable deterministic rule
Examples:
- multi-view consensus
- rising margin requirement
- no recent top flips
- minimum agreement count across EEG views
- adaptive stability requirement depending on evidence duration

### Model 1 — Logistic regression
Use normalized trajectory features. Apply class weighting if needed.

### Model 2 — Small regularized tree/boosting model
Only if available and stable. Examples: shallow gradient boosting, random forest with strict regularization, HistGradientBoosting.

Avoid large opaque models.

### Model 3 — Calibrated selective classifier
Calibrate reliability probability with nested training only:
- Platt/logistic calibration
- isotonic only if sample count supports it

Optional:
- conformal/selective risk control
- empirical risk upper-confidence thresholding

Prefer methods that let us explicitly say: intervene only when estimated risk is below threshold.

## 13. Optimize risk-coverage, not only accuracy

For every candidate gate compute a risk-coverage curve.

Definitions:
- `coverage = Context-assisted applications / eligible congruent opportunities`
- `intervention precision = correct Context-assisted applications / all Context-assisted applications`
- `induced risk = Context-caused wrong early stops / Context-assisted applications`

Plot/report:
- coverage
- intervention precision
- induced risk
- mean gain among applications
- overall mean gain
- final accuracy

Primary question:

> How much Context coverage can we safely obtain before induced risk becomes unacceptable?

Do not optimize only for mean latency.

## 14. Controlled Context conditions

Continue M35’s controlled mechanistic setup.

For every EEG trial evaluate:

### EEG-only
Frozen baseline.

### Congruent Context
`Context target = EEG ground truth`

Use Context strength sweep:
`q_top = 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95`

This is oracle/mechanistic Context, not real semantic prediction.

### Incongruent Context
For every trial, test BOTH wrong classes with the same strength sweep.

### Neutral Context
Uniform/Context-off. Must reproduce EEG-only.

## 15. Context modifies authorization, not class identity

Preferred architecture:

`Context agreement + estimated EEG early reliability + Context confidence -> authorization probability / stopping decision`

Final emitted class is always `raw EEG top`.

## 16. Explore adaptive Context gain

Instead of one global alpha, allow Context effect to depend on reliability.

Conceptually:

`Context bonus = f(Context confidence) * g(EEG reliability)`

where:
- low EEG reliability -> Context bonus approaches zero;
- high EEG reliability + Context agreement -> allow earlier stop;
- Context disagreement -> do not accelerate;
- unstable EEG trajectory -> do not accelerate.

Explore bounded forms such as piecewise linear, logistic, reliability-binned, and risk-controlled thresholding.

Do not use future truth at runtime.

## 17. Special attention to 0.20–0.30 s

Analyze separately:
- 0.20 s
- 0.25 s
- 0.30 s
- 0.35 s
- 0.40 s

Measure:
- early EEG accuracy
- reliability-gate precision
- eligible congruent Context count
- safe intervention coverage
- wrong-context risk
- paired latency gain

Identify which time point offers the best Context opportunity.

Do not assume 0.20 s is always preferable to 0.25 or 0.30 s.

## 18. Keep oracle ceiling visible

Recompute oracle upper bound using all applicable out-of-fold trials.

Report:
- unconstrained oracle
- one-update oracle
- two-update oracle
- margin-constrained oracle
- multi-view-consensus oracle if useful

Calculate:

`efficiency = achieved safe gain / oracle gain`

for each method.

## 19. Failure-case mining

For every Context-induced wrong early stop, save detailed trace:
- trial ID
- session/group
- true class
- wrong Context target
- Context strength
- stop time
- raw EEG top at each prior update
- score vectors
- margins
- entropy
- stability count
- multi-view agreement
- reliability probability
- baseline final class
- whether EEG-only was correct
- time at which EEG trajectory corrected itself

Cluster failure cases if possible.

Ask whether failures concentrate in a session, class, wrong-target direction, low-quality trial, channel disagreement, recent top flips, flat margin trajectory, or S7-specific shift.

Use this analysis to improve TRAIN-fold gates only. Never hard-code trial IDs.

## 20. Session-shift robustness

Explicitly quantify feature shift across session/groups:
- score-scale distribution
- margin distribution
- entropy
- flip frequency
- band agreement
- channel agreement
- evidence-time distribution

Where needed, use training-only normalization.

Possible robust approaches:
- percentile/rank features instead of raw score magnitudes
- per-trial normalized margins
- session-invariant ratios
- calibration using only training groups
- conservative worst-group threshold selection

Do not use test-group statistics in a way unavailable online.

## 21. Worst-group / robust threshold selection

Do not select the gate that merely maximizes average development gain.

Consider selecting thresholds using:

`maximize coverage/gain subject to safety constraints in every training-validation group`

Possible constraints:
- zero induced wrong stops in every inner validation group;
- or induced-risk upper confidence bound below a predeclared threshold;
- no meaningful paired accuracy loss in any group.

This is intended to improve S7-like transfer.

## 22. Safety operating points

Evaluate multiple operating points.

### SAFE-STRICT
- target induced wrong early stops: 0
- maximize coverage under that constraint

### SAFE-95
- intervention precision >=95%
- no significant final accuracy loss
- maximize gain/coverage

### SAFE-99
- intervention precision >=99% where sample size supports estimation
- maximize coverage

Do not claim 99% safety from tiny intervention counts without confidence intervals.

Use Wilson/exact/binomial intervals where appropriate.

## 23. Statistics

All comparisons are paired by EEG trial.

Report:
- paired mean latency gain
- median gain
- bootstrap 95% CI
- fraction accelerated
- fraction unchanged
- fraction harmed/delayed
- paired accuracy difference
- McNemar-style paired correctness where valid
- intervention precision CI
- induced-risk CI
- session/group-specific results

Because wrong Context creates two replay scenarios per trial, clearly distinguish real EEG trials from replay scenarios.

Do not treat replay scenarios as independent human trials.

## 24. Primary success table

Produce one central table with rows:
- EEG-only
- M35 conservative Context
- best M36 rule-based gate
- best M36 logistic gate
- best M36 tree/boosting gate if used
- oracle Context
- stored real M33 replay

Columns:
- real EEG trials
- Context replay scenarios
- accuracy
- application/coverage
- intervention precision
- induced wrong early stops
- mean evidence
- overall mean gain
- applied-trial mean gain
- <=300 ms fraction
- oracle-efficiency ratio

Separate Track 1 and Track 2.

## 25. Required figures

Create at least:
1. Risk–coverage curve for Context intervention.
2. Context coverage vs overall latency gain.
3. Intervention precision vs coverage.
4. Applied-trial gain vs coverage.
5. <=300 ms fraction vs coverage.
6. Session/group-specific accuracy and induced-risk comparison.
7. Oracle ceiling vs achieved M36 gain.
8. Representative successful congruent acceleration trace.
9. Representative safe incongruent rejection trace.
10. Representative wrong-Context failure trace.
11. Reliability probability calibration plot.
12. Feature/session-shift summary if useful.

## 26. Real M33 Context: secondary evaluation

Only after controlled Context gating is complete:

Reuse stored M33 Context outputs from M34/M35 where compatible.

Do NOT call DeepSeek again merely to produce more favorable semantic outputs.

Feed stored real M33 Context through the new M36 gate.

Report:
- application rate
- overall gain
- applied-trial gain
- induced errors
- exact fallback
- gap to controlled congruent Context
- gap to oracle

This tells us whether the next bottleneck is semantic Context quality or EEG authorization.

## 27. Do not spend M36 on onset-guard optimization

M35 already showed guard materially changes sample alignment.

M36 primary timing metric is EEG evidence duration.

Do not make guard tuning a major branch of this milestone. Preserve historical alignment for comparability.

## 28. Artifact directory

Create:

`research_analysis/m36_context_safe_scaling_20261004/attempt-01/`

Save at minimum:
- `M36_PROTOCOL.md`
- `m36_grouping_audit.json`
- `outer_fold_assignments.csv`
- `data_track_summary.json`
- `eeg_trajectory_features.csv`
- `reliability_labels.csv`
- `nested_cv_search.csv`
- `oof_predictions.csv`
- `risk_coverage.csv`
- `controlled_context_results.csv`
- `congruent_results.csv`
- `incongruent_results.csv`
- `neutral_results.csv`
- `oracle_summary.json`
- `failure_cases.csv`
- `session_shift_analysis.csv`
- `real_m33_secondary_summary.json`
- `statistical_summary.json`
- `final_audit.json`
- `plots/`
- `M36_FINAL_REPORT.md`

Do not commit raw EEG.

Large generated feature tables may remain local if repository policy makes them inappropriate to commit; report path/hash.

## 29. Required automated checks

Add/check:
- all 118 usable trials accounted for
- every trial has valid grouping
- outer train/test trial IDs never overlap
- same trial windows never cross folds
- test labels never used as runtime features
- future EEG samples never used in runtime features
- Context does not rerank raw EEG winner
- Neutral Context exactly equals EEG-only
- non-applied Context exactly equals EEG-only
- both wrong Context targets evaluated for every applicable trial
- raw EEG hashes unchanged
- all OOF predictions correspond to models not trained on test trial/group
- real-trial counts are not confused with replay-scenario counts
- final report metrics recompute from saved tables
- remote Git hash verified

## 30. Autonomous Long-Run research loop

This is a Long Run task.

Do not stop after the first model or first report.

Cycle:
1. reproduce frozen EEG trajectories;
2. audit grouping;
3. build leakage-safe features;
4. build nested OOF evaluation;
5. reproduce M35 gate;
6. test improved deterministic gates;
7. test small learned reliability estimators;
8. analyze risk–coverage;
9. inspect failure clusters;
10. improve robustness using TRAIN-fold evidence only;
11. rerun nested OOF evaluation;
12. compare against oracle;
13. evaluate stored M33 secondarily;
14. self-review scientific validity;
15. write final report.

When a model performs poorly, diagnose why, record it, and move to the next justified method.

Do not ask the user to choose routine software-side next steps.

## 31. Stop conditions

Stop only when one of these is true.

### A. Goal reached
A clearly better safe Context gate is found and characterized across the available retrospective data.

### B. Software search exhausted
Reasonable rule-based and small learned reliability approaches have been evaluated under nested grouped validation and the risk–coverage ceiling is clear.

### C. New data required
Further progress depends on prospective EEG / new subjects / actual online Context history rather than software.

Do not stop merely because the first M36 rule fails.

## 32. Final report must answer

1. Were all 118 usable trials included in the M36 retrospective analysis?
2. How were they grouped to avoid leakage?
3. What is the main 88-trial 5-channel result?
4. What is the universal 118-trial common-3-channel result?
5. What is the best safe Context application rate?
6. How much did that improve over M35’s ~6.8%?
7. What is intervention precision?
8. How many Context-induced wrong early stops remain?
9. What is overall mean evidence reduction?
10. What is applied-trial mean gain?
11. Is ~200 ms conditional gain preserved?
12. What fraction finishes <=300 ms?
13. Does performance generalize across acquisition groups?
14. Which reliability features mattered?
15. Does multi-view EEG agreement help distinguish temporary wrong tops?
16. Does a learned reliability gate outperform deterministic rules?
17. What is the safe risk–coverage frontier?
18. How close is M36 to the oracle ceiling?
19. How does stored real M33 Context perform through the M36 gate?
20. Is the dominant remaining bottleneck EEG reliability, Context semantic accuracy, session shift, or insufficient data?
21. Can we now support the mechanistic claim: “Congruent Context can intervene broadly and accelerate SSVEP decoding while incongruent Context is prevented from overriding reliable EEG”?
22. What must be collected prospectively next?

## 33. Completion summary

At completion print:

`TOTAL REAL EEG TRIALS USED:`

`TRACK 1 TRIALS:`

`TRACK 2 TRIALS:`

`BEST GATE:`

`BEST SAFE APPLICATION RATE:`

`M35 APPLICATION RATE BASELINE:`

`INTERVENTION PRECISION:`

`CONTEXT-INDUCED WRONG EARLY STOPS:`

`EEG-ONLY MEAN EVIDENCE:`

`M36 CONTEXT MEAN EVIDENCE:`

`OVERALL MEAN GAIN:`

`APPLIED-TRIAL MEAN GAIN:`

`<=300MS EEG-ONLY FRACTION:`

`<=300MS M36 FRACTION:`

`ORACLE MEAN GAIN:`

`ORACLE EFFICIENCY:`

`REAL M33 APPLICATION RATE THROUGH M36 GATE:`

`REAL M33 MEAN GAIN THROUGH M36 GATE:`

`CROSS-SESSION ROBUST: YES / PARTIAL / NO`

`MAIN REMAINING BOTTLENECK:`

`PROSPECTIVE DATA REQUIRED:`

`FINAL COMMIT:`

`REMOTE HASH VERIFIED:`

## 34. Scientific claim boundary

M36 may support a **retrospective, cross-validated mechanistic claim** if evidence is strong.

It must NOT be described as:
- fresh independent held-out validation;
- prospective online Context validation;
- population-level generalization;
- physical-latency validation.

The 118 trials are historical and were already used in earlier milestones.

The purpose of M36 is to use them efficiently and leakage-safely to build the strongest Context authorization mechanism possible before new prospective data collection.

## 35. Software / hardware boundary

Software only.

Do NOT:
- open COM11
- use ND8
- use Quest
- use ADB / MQDH
- collect new EEG
- control robot
- dispatch VLA
- modify raw EEG files
- alter unrelated project files

No user GUI interaction should be needed unless a genuine irrecoverable metadata blocker is found.

## 36. Git discipline

Create branch:

`codex/m36-context-safe-scaling`

Do NOT use:
- `git reset --hard`
- `git clean`
- force push
- destructive stash
- `git add .`
- `git add -A`

Preserve all unrelated dirty/untracked files.

Selectively stage M36 files only.

Run relevant compilation/tests/audit.

Commit and push.

Verify local HEAD, upstream, and remote branch hashes match.

## 37. Priority order

1. No leakage / scientific validity
2. Use as much of the 118-trial dataset as possible
3. Cross-session robustness
4. Prevent wrong-Context early stops
5. Increase safe Context coverage
6. Preserve ~200 ms applied-trial acceleration
7. Increase overall latency gain
8. Maintain EEG accuracy
9. Interpretability / reproducibility
10. Engineering cleanup

Do not optimize for attractive numbers at the expense of validity.
