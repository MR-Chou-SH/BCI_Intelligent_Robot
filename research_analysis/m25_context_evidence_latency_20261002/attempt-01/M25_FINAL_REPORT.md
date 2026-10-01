# M25 Context Evidence and Shared-Threshold Latency Study

## Executive result

This is an offline replay on the frozen M21 250 Hz feature cache. The 88 formal trials remain A=30, B1=29, B2=29. B1 trial-011 and exploratory B2 trial-023 remain excluded. No raw EEG waveform was decoded or modified; ND8/COM11/Quest were not accessed for Task 1.

The pure target-aligned evidence branch shows strength-dependent numeric evidence at the same cached EEG timepoint. At 0.90 s the across-strength ranges in mean fused top, margin, normalized entropy and true-class evidence are respectively 0.5605, 0.8693, 0.9696, and 0.5610. Strengths below 0.70 are labeled COUNTERFACTUAL_FUSION_DIAGNOSTIC_ONLY and are never counted as authorized stopping behavior.

The latency question is reported independently. M25 selected one Context threshold configuration per LOSO fold, operating point and mechanism branch from training sessions only; the same selected parameters were applied to every strength in the held-out session. See the tables and machine-readable selection audit for the exact configurations and held-out paired gains.

## Provenance and frozen decisions

- Feature cache: research_analysis/m21_context_algorithm_search_20260928/attempt-01/per_trial_feature_table.csv SHA-256 458cc6a79f8bd3e4e38d79ac7163cd8636039eb0698c74d9cad98ee59d9b2309.
- Frozen M23 operating-point parameter SHA-256: 1ff7690941778c0a58f3ac1976cc111054b4fecf5dcec1f5d2c1c2d56e3f0513. The fold's M23 EEG-only top/margin parameters are reused as fixed values; training and held-out trials within a fold use the same parameters. Held-out raw baselines reproduce the corresponding stored M23 baseline rows (264/264 OP-trial checks).
- No EEG-only parameter was recalibrated using Context outcomes. Candidate selection uses no held-out session.
- Primary continuous strengths: 1/3, .50, .60, .70, .80, .90, .95, .98, .99, 1.00; fixed times .50/.70/.90/1.10/1.30/1.50 s; complete .10 s trajectory retained.

## 1. Direct continuous evidence response

At 0.90 s, the strength grid spans 0.5605 in fused top evidence, 0.8693 in fused top-second margin, 0.9696 in normalized entropy, and 0.5610 in true-class evidence. This is direct numerical evidence propagation under Context top=true target, precision=1.00 and coverage=1.00; it is not a deployed predictor or stopping policy.

The 1/3 prior is uniform and leaves the EEG evidence unchanged. The one-hot endpoint is the exact top-class posterior. The one-hot change fraction is the L1 movement from normalized EEG toward the one-hot posterior divided by the full L1 distance to that endpoint. Curves are also reported per session and per frozen EEG operating point.

## 2. Historical authorized response

The exact M23 helper replay covered 2376 trial × OP × strength combinations and compared 2376 matching M24 p=1/c=1 cells. M23 and M24 mismatch counts were both zero. This branch authorizes only when Context top agrees with current raw EEG top and prior top mass is at least .70; the per-point minimum/top/margin gates and .40 s stability are recorded separately. Unauthorized evidence is exact EEG-only fallback.

## 3. Shared-threshold LOSO latency

Threshold selection uses the corresponding M23 fold parameters, the compact preregistered candidate grid, all ten strengths, and training trials only. It rejects any candidate with lower training accuracy than the paired frozen baseline, any target-aligned wrong early stop, or any delay. The optimization and tie-break order are frozen in M25_EXPERIMENT_SPEC.md. The selected gate and per-fold parameters are recorded in shared_threshold_outer_fold_selection.json.

Across the frozen-gate test set, held-out strength-separated trial count summed over FAST/MEDIUM/CONSERVATIVE is 198; maximum per-fold/OP strength-separated fraction under primary .10/.40 is 1.000. These are trial-level descriptive replay results, not population estimates.

## 4. Precision and risk surface

The precision surface reuses M23 seed-230929 assignment rows at coverage 1.00. It reports realized assignment precision, authorized precision, fixed-time fused evidence, latency gain, accuracy and wrong-stop outcomes. This is one frozen deterministic assignment realization, so observed risk must not be generalized as a population safety rate.
- Requested precision 1.00: mean gain 0.272 s; wrong early stops 0; Context-caused errors 0; mean realized assignment precision 1.000; mean authorized precision 1.000.
- Requested precision 0.95: mean gain 0.255 s; wrong early stops 0; Context-caused errors 0; mean realized assignment precision 0.955; mean authorized precision 1.000.
- Requested precision 0.90: mean gain 0.242 s; wrong early stops 0; Context-caused errors 0; mean realized assignment precision 0.898; mean authorized precision 1.000.
- Requested precision 0.85: mean gain 0.233 s; wrong early stops 0; Context-caused errors 0; mean realized assignment precision 0.864; mean authorized precision 0.987.
- Requested precision 0.80: mean gain 0.216 s; wrong early stops 0; Context-caused errors 0; mean realized assignment precision 0.795; mean authorized precision 0.972.

## 5. Wrong-Context safety stress

The separate deterministic wrong-top stress was excluded from threshold selection. It recorded 18 wrong early-stop events in the held-out evaluation. CSV rows identify raw-top disagreements/rejections, points where wrong Context coincided with temporarily wrong EEG top, earliest failure time, raw margin, entropy, recent top flips and same-top duration. These results characterize this synthetic stress rule only.

## 6. Context arrival timing

Context availability was delayed to .0/.2/.4/.6/.8 s without changing content or thresholds. The mean held-out gains are:

- available at 0.0 s: 0.254 s.
- available at 0.2 s: 0.254 s.
- available at 0.4 s: 0.254 s.
- available at 0.6 s: 0.214 s.
- available at 0.8 s: 0.142 s.

## 7. Grid, stability and descriptive headroom

The same selected thresholds were evaluated on cached .10 s and .04 s grids and .20/.40/.60 s stability windows without refitting. The sensitivity CSV includes mean stop/gain and per-trial strength separation. The headroom table uses only evidence available by 0.90 s, reports descriptive Spearman associations and quartile summaries, and is not used to select a policy.

## Interpretation and scientific limits

- A numeric strength response exists in the fused evidence, but a latency response is an empirical outcome of the shared gates and may remain saturated at the discrete stop-time level.
- FROZEN_GATE_SHARED_THRESHOLD retains the historical .70 authorization boundary. NONDEPLOYABLE_DECOUPLED_STRENGTH_GATE is a mechanism-only sensitivity, not production behavior.
- Target-aligned, deterministic Context is not a deployable semantic predictor. Lower-precision and wrong-Context simulations are controlled risk overlays.
- The unit is the trial, but only three acquisition sessions are present. Trial bootstrap intervals do not establish population or cross-subject generalization.
- Software grid time is effective EEG evidence time in cached features, not physical optical onset, exact hardware timing, online performance or Quest display timing.

## Outputs and validation

The complete required artifact set and 12 SVG plots are listed in final_validation.json. Input/source fingerprints and M23/M24 helper agreement are checked. Final validation status: PASS only if all software invariants passed.
