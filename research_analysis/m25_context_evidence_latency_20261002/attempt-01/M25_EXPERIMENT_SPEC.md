# M25 Experiment Specification

Version: 1.0
Frozen before M25 held-out evaluation: 2026-10-02 UTC
Analysis branch labels:
- PURE_CONTEXT_STRENGTH_EVIDENCE_RESPONSE
- AUTHORIZED_CONTEXT_EVIDENCE_RESPONSE
- FROZEN_GATE_SHARED_THRESHOLD
- NONDEPLOYABLE_DECOUPLED_STRENGTH_GATE

## Question and claim boundary

Separate numeric evidence propagation from stopping-time behavior. The pure evidence branch is diagnostic and is not a deployed stopping rule. Any target-aligned, wrong-context, or decoupled simulation is offline mechanism evidence; it is not a semantic predictor, population-generalization result, or deployment authorization.

## Frozen source and cohort

Use the exact M21 cached 250 Hz FBCCA feature table and M23 frozen adaptive EEG-only operating points. Formal cohort N=88: A=30, B1=29, B2=29. Preserve exclusion B1 m6_4-trial-011 and exploratory exclusion B2 m6_4-trial-023. Class order is target_left, target_center, target_right; nominal slot mapping remains 7.2, 9, 12 Hz. No raw waveform is decoded or modified. Do not recalibrate EEG-only thresholds from Context outcomes.

Primary EEG operating points: FAST, MEDIUM, CONSERVATIVE, with each session evaluated under its frozen M23 outer leave-one-session-out baseline.

## 1. Continuous evidence response

Primary fixed mechanism: Context top equals the true target, requested precision=1.00, assigned coverage=1.00. Use identical cached raw score vectors for all prior strengths. For prior top class k and strength s, prior[k]=s and each other class=(1-s)/2. Fuse by the exact M23/M24 operation: normalize nonnegative EEG scores, multiply elementwise by the prior, normalize the product. Use this operation continuously even below prior mass .70 and label those rows COUNTERFACTUAL_FUSION_DIAGNOSTIC_ONLY. They are never counted as authorized or as a stopping result.

Strengths: 1/3, .50, .60, .70, .80, .90, .95, .98, .99, 1.00.
Pre-registered fixed evidence times: .50, .70, .90, 1.10, 1.30, 1.50 s. Retain all .10 s points (0.50–1.90 s) as secondary trajectory evidence.

For every trial/time/strength record raw FBCCA scores, normalized EEG evidence, raw top and margin, Context prior/top, fused vector/top/margin/normalized entropy/true-class evidence, changes from uniform/OFF and .70, and one-hot-change fraction. Define fraction of one-hot change as L1(fused(s)-EEG)/L1(one_hot_context-EEG), with zero denominator represented as null. Summaries cover full cohort, each session, each EEG OP, and session×OP.

## 2. Authorized evidence response

Reproduce the M23/M24 pointwise authorization separately. Authorize only when Context top equals current raw EEG top and prior top mass >=.70. On authorized points, apply the frozen elementwise fusion; on unauthorized points, preserve EEG evidence exactly. Also record the M23 stopping evidence gates (minimum evidence, raw-top confirmation, frozen top/margin threshold) separately. Do not combine counterfactual fusion rows with this branch.

## 3. Shared-threshold LOSO latency

Outer folds:
- hold out A, train B1+B2
- hold out B1, train A+B2
- hold out B2, train A+B1

For each outer fold, select the matching frozen M23 fold parameter set (whose EEG-only calibration used exactly the complementary training sessions) and apply that same set to the fold's training and held-out trials. Recompute the paired raw EEG stop from cached features with those fixed parameters; on the held-out session it must exactly reproduce M23's stored baseline. Do not use per-trial baseline rows from other M23 folds while fitting M25 thresholds, because those rows may have been calibrated with this M25 held-out session.

Within each fold and EEG OP, calibrate one shared configuration for all ten strengths. Selected class is always raw EEG top; Context cannot rerank; require Context top/raw-top agreement; preserve no-delay EEG-only cap and .40 s primary stability. Compare:
- FROZEN_GATE_SHARED_THRESHOLD: historical prior-top mass >=.70.
- NONDEPLOYABLE_DECOUPLED_STRENGTH_GATE: mechanism-only assignment-available + Context/raw-top agreement; prior sharpness does not decide authorization. This branch never modifies production behavior.

Pre-registered compact candidate grid, common to all strengths and folds:
- top probability threshold: .40, .45, .50, .55
- top-minus-second margin threshold: .01, .05, .10, .15, .20
- minimum evidence: .50, .70, .90 s
- stability: .40 s primary

A candidate is valid only if training accuracy is not lower than the paired frozen EEG-only baseline and there are zero Context-caused wrong early stops on target-aligned training cases. Among valid candidates, maximize mean paired latency reduction across the entire ten-strength grid and all training trials. Ties: higher top threshold, then higher margin, then later minimum evidence. If no candidate is valid, report no selected Context rule and use exact paired EEG-only fallback. Apply the frozen configuration to the held-out session without refitting.

In the M25 threshold-calibrated policy branches, only points satisfying that branch's authorization predicate accrue Context stopping stability; a gate failure or Context unavailability resets the stable run. If no authorized stable crossing occurs before the paired EEG-only stop, return the exact paired baseline result. This avoids a newly calibrated Context threshold manufacturing an early stop when Context is absent or unauthorized. Section 2 remains the separate exact historical M23/M24 path.

Report paired gain (EEG-only stop minus Context stop), conditional mean/median over trials with any authorized Context before baseline, all-trial mean, trial-level bootstrap 95% CI (2,000 deterministic resamples), accelerated fraction and fractions with gain >=.10/.20/.30/.50 s, accuracy, wrong early stops, Context-caused errors, authorized rate, per-session curves. Trial is the resampling unit; three sessions do not establish population generalization.

## 4. Grid and stability sensitivity

Use the primary selected shared thresholds without refitting:
- decision grids .10 s primary and .04 s sensitivity, using the exact cached M21 grids;
- stability .20, .40 primary, and .60 s.
Retain the same no-delay cap, raw-top rule, and minimum evidence selected for that fold/OP. Report whether strengths separate in stop time and the distribution of stop-time differences, identifying grid quantization, stability, common threshold crossings, minimum evidence, and EEG-only cap.

## 5. Context quality × strength surface

Coverage fixed at 1.00. Reuse exact M23 deterministic assignment rows and hashes for precision 1.00, .95, .90, .85, .80. Strengths: .50, .60, .70, .80, .90, .95, .99, 1.00. Do not regenerate assignments or select thresholds from this surface. Use the already selected FROZEN_GATE_SHARED_THRESHOLD per fold/OP.

Report fixed-time evidence, latency, assignment/authorized rates, realized assignment precision, accuracy, wrong early stops, Context-caused errors, and gain-risk tradeoffs. Label every stochastic assignment realization as the single frozen deterministic M23 realization; do not generalize safety from it.

## 6. Wrong-context safety stress

Use strengths .70/.80/.90/.95/.99/1.00 and one deterministic wrong Context top per trial generated by a separate fixed hash rule. Do not use this stress condition in threshold selection. Apply the selected frozen-gate thresholds. Measure raw-top disagreement/rejection, agreement with temporarily wrong EEG top, wrong early stops and earliest failure time; associate failures with raw margin, normalized entropy, top flips and same-top duration. This is a safety stress, not a predictor estimate.

## 7. Context arrival timing

For true-target Context, simulate availability at .0/.2/.4/.6/.8 s. Context content and selected shared threshold stay fixed. Fusion/stability can begin only when Context is available. Report gain retained versus pre-trial availability across the ten strengths and three OPs.

## 8. Descriptive headroom / ambiguity

Use only current/past-causal cached features where possible, including fixed early raw margin, normalized margin, entropy, flips/duration through the observed time, distance from the frozen EEG gates, evidence slope, session and EEG-only headroom. Relate these to held-out M25 gain descriptively; do not tune a new policy or claim deployability.

## Statistics and reproducibility

All primary estimands are paired by trial. Bootstrap trial-level summaries with 2,000 resamples and fixed seed 251002. Keep per-session statistics visible. Report exact sample counts and missing/invalid strata; no unplanned exclusions. Use NumPy only for computations; plots are deterministic SVGs.

## Acceptance invariants

- Exactly 88 unique primary trials with A=30/B1=29/B2=29 and exclusions preserved.
- Input feature/assignment/parameter hashes match INPUT_MANIFEST.json and frozen upstream manifests.
- No strength-specific threshold selection; one config per fold/OP/branch.
- No held-out session used in its threshold selection.
- Context never reranks raw EEG top and never delays beyond paired baseline.
- Unauthorized M23 path equals exact EEG-only fallback.
- Counterfactual below-.70 fusion remains explicitly diagnostic.
- All requested M25 output files and plots exist; final_validation.json reports PASS only if every software-verifiable invariant passes.
