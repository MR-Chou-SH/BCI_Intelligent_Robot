# M28 Experiment Specification — Real Semantic Context Transfer

Version: 1.0. Frozen before any M28 engine evaluation.
Frozen at UTC: `2026-10-02T09:55:01Z`
Benchmark SHA-256: `41310ac326c33950982dd820a7b2235b144e2bc610dd58b95e36ea62c492da8e` (`semantic_context_benchmark_v1.json`)

## Scientific question and limits

Measure whether scene-derived task semantics, affordances, current state, and already observed selections produce reliable pre-next-decision Context. Transfer only the held-out measured result distribution into the frozen M25 decoder as `SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION`. The benchmark is a curated software fixture set, not natural-task or population user-intent accuracy. The transfer maps a correct semantic outcome onto an EEG trial's true class only after the semantic call, as simulation machinery; it is not causal evidence.

## Frozen Context score interpretation

- Model ID is discovered from the official live model catalog at runtime; no key or Authorization data is stored. Prompt: `m28-semantic-context-prompt-v1`; schema: `m28-semantic-context-v1`; temperature 0.0; maximum output 1,000 tokens; one correction retry.
- Scores are ordinal semantic plausibility scores, not calibrated probabilities. No calibration is fitted. The fixed pseudo-prior is `softmax(score / 0.20)` and is explicitly uncalibrated.
- The predeclared active gate is status `informative`, top raw semantic score ≥0.60, and top-vs-second raw score margin ≥0.12. Ambiguous, context_off, invalid, and below-gate cases abstain. No threshold is selected from EEG outcomes or to reach the desired gain.
- M25 authorization still additionally requires prior top mass ≥0.70, agreement with current raw EEG top, and the unchanged frozen stability/evidence gates.

## Frozen benchmark

Forty cases are split into 12 current M20-like cases, 24 held-out cases across future bookshelf, kitchenware, desk-tool, and toy-room families, and 4 adversarial controls. Expected status/target/relation lives only in `evaluation_only`; only `model_input` may reach the engine. The scorer evaluates informative precision/top-k, active coverage, abstention, ambiguous/OFF accuracy, invalid rejection, relation-stratified confusion, score/prior sharpness, repeated structure, and API latency. Exact counts accompany rates.

The cases include phone→charger, medicine→closed storage, medicine→USER ZONE, explicit and vague destinations, open/closed/full/completed states, multiple containers, missing targets, unsupported affordances, unknown candidate IDs, and semantically plausible distractors. Synthetic future families are not physical-scene evidence.

## Frozen EEG transfer

Use the exact M25 manifest, cached M21 250 Hz feature table, M23 fixed FAST/MEDIUM/CONSERVATIVE outer-fold parameters, and M25 per-fold shared Context thresholds. Preserve the M25 no-delay cap, raw-EEG class identity, ≥0.70 prior gate, raw-top agreement, evidence thresholds, and 0.40 s stability. Do not refit any EEG baseline or stopping threshold with Context outcomes.

For each transfer seed, select held-out semantic result rows with a seeded assignment independent of EEG features/evidence. Preserve benchmark coverage, active precision, pseudo-prior top-mass distribution, and wrong-choice offset/confusion pattern. A correct semantic result is aligned to the EEG trial true class only as the explicitly artificial transfer map; wrong results preserve their relative alternative offset under a seeded class permutation. Report this is a transfer simulation, never prospective real Context.

Primary transfer scenarios use measured Context availability at its observed API latency and a precomputed-before-trial 0.0 s sensitivity. Also report measured latency percentiles and M25 arrival sensitivities 0.0/0.2/0.4/0.6/0.8 s. Build a required-quality frontier over precision, coverage, prior sharpness, and arrival time using frozen M25 gates/thresholds only. Include a perfect, instant, full-coverage oracle ceiling as a diagnostic upper bound; it is not semantic-engine performance.

## Reproducibility and acceptance

- M25/M23/M24 historical output directories are read-only inputs; new results go only under `research_analysis/m28_real_semantic_context_20261002/attempt-01/`.
- Confirm source manifest and component hashes before and after replay; exactly N=88 primary EEG trials, A=30/B1=29/B2=29.
- Report FAST, MEDIUM, and CONSERVATIVE, paired accuracy, Context-caused wrong early stops, mean/median/all-trial gains, seed intervals, active rate, and target threshold reachability.
- 0.4/0.5 s is supported only with unchanged EEG baseline, no paired accuracy loss, zero Context-caused wrong early stops, no oracle leakage in semantic inputs, and empirical semantic quality supporting the condition.
- If not supported, report measured ceiling and whether the constraint is EEG headroom/gates, Context precision/coverage, sharpness, or arrival time.
- No raw waveform decoding, raw EEG write, ND8/COM11, Quest/ADB/MQDH, robot, M20 runtime, or full BCI integration.
