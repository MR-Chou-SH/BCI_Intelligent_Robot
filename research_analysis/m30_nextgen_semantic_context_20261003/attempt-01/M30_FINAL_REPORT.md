# M30 Final Report — Next-generation Semantic Context and EEG Transfer

## Status and evidence boundary

The M30 software experiment and required replay artifacts are complete. The results do **not** support a safe Context-accelerated operating condition: every positive-λ primary condition has at least one wrong early stop and a small paired-accuracy loss. λ=0 is an exact EEG-only baseline and provides no Context gain. M30 therefore does not establish the requested 0.4–0.5 s gain as a safe result.

The EEG integration label is `NEXTGEN_SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION`. Historical M25 EEG trials were randomized and have no prospective M30 scene/task labels. The semantic-to-class mapping is simulation machinery applied after semantic model evaluation. No result below is prospective human-task EEG evidence or deployment validation.

The held-out split is **exploratory, not pristine**: prompt-v2 outputs on a predecessor benchmark were observed before the current ambiguity audit and benchmark relock. Train/dev-only gates were frozen before the current prompt-v4 + precondition-guard evaluation, and no held-out threshold was tuned afterward.

## Frozen inputs and method

- Benchmark lock: `899aae6febb96cb6a63f0589fd7ad8e5bd0b317e97ac81de23c71ff062d237b0`; 33 episodes, 126 next-selection decisions, 8 semantic families. Split counts: 56 train, 24 dev, 46 held-out. Static validation: 126/126 production SceneCore, zero model-input leakage keys, relation compatibility valid, ambiguous labels have at least two distinct acceptable targets, informative labels have exactly one.
- Model/prompt/engine: `deepseek-flash`, `m30-sequential-relational-context-v4`, `m30-context-precondition-guard-v1`. Missing required tool and missing required target affordance are rejected locally as invalid before an API call; the guard is generic task-state logic and does not branch on benchmark IDs or evaluation labels.
- Context is a variable-dimensional `q_global` over remaining selectable objects. The EEG adapter paginates deterministically and renormalizes only the target's three-choice page. A two-choice final page remains supported by the software adapter; the historical three-class transfer maps only a full three-choice page.
- C65/C85/C100 confidence gates use train+dev rows only. They require model status `informative` and a frozen score of `0.5 * top ordinal score + 0.5 * top-minus-second margin` above the corresponding threshold.
- The M25 cohort remains 88 trials (A=30, B1=29, B2=29); all 264 FAST/MEDIUM/CONSERVATIVE EEG-only baseline stops were reproduced. The frozen M25 input manifest SHA-256 is unchanged before and after replay. Raw EEG waveforms were not read.
- Primary transfer: 100 deterministic seeds, all 3 coverage gates × 5 values of `lambda_ctx` × 3 M25 operating points (45 conditions; 396,000 trial rows), with precomputed Context. Timing sensitivity: 30 matched seeds at nominal λ=1 for measured API arrival, 0.2 s, and 0.4 s (71,280 trial rows).
- Safe fusion preserves the raw EEG top as the selected class, requires projected Context top mass ≥ 0.70 and agreement with the instantaneous raw EEG top, and retains frozen M25 minimum evidence, shared top/margin thresholds, stability duration, and a paired EEG-only stop cap. Context cannot delay the baseline.

## Semantic benchmark results

| Gate | Held-out active / all decisions | Eligible informative participation | Active precision | Informative top-1 | Top-3 recall | Wrong-active count | Invalid rejection | Completed rejection |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| C65 | 32/46 (69.6%) | 24/34 (70.6%) | 68.8% | 76.5% | 97.1% | 10 | 100% | 100% |
| C85 | 40/46 (87.0%) | 32/34 (94.1%) | 60.0% | 76.5% | 97.1% | 16 | 100% | 100% |
| C100 | 41/46 (89.1%) | 33/34 (97.1%) | 61.0% | 76.5% | 97.1% | 16 | 100% | 100% |

The C100 confidence threshold is limited by the number of train/dev examples, so realized held-out participation is 89.1% overall rather than exactly 100%. C100 does not force Context on ambiguous, invalid, or completed cases.

Held-out families at C100:

| Family | Top-1 on eligible decisions | Active precision | Wrong-active count | Notes |
|---|---:|---:|---:|---|
| Kitchen / food preparation | 9/10 (90.0%) | 75.0% | 3 | Strongest held-out family by top-1 and precision. |
| Ambiguous / adversarial | 4/5 (80.0%) | 60.0% | 2 | Ambiguity handling is only 50% in this family. |
| Assistive / handover | 7/10 (70.0%) | 58.3% | 5 | All 12 held-out decisions were gate-active. |
| State-dependent | 6/9 (66.7%) | 50.0% | 6 | Weakest active precision; dynamic state remains difficult. |

Held-out round-specific C100 top-1 on eligible cases was 5/5 (100%) at round 1, 7/8 (87.5%) at round 2, 6/11 (54.5%) at round 3, and 8/10 (80.0%) at round 4. Active precision by round was 50.0%, 60.0%, 54.5%, and 80.0%, respectively. Round 3 is the clear dip; the pattern is not monotonic and later rounds have smaller denominators.

Across all held-out rows, top-1 is 26/34 (76.5%), top-3 recall 33/34 (97.1%), ambiguity accuracy 1/9 (11.1%), and invalid/completed rejection 100%. Relation-type accuracy among correctly active rows is 68.0% at C100. The new guard fixed the deterministic impossible-state failure, but it did not fix the broader ambiguity / false-active problem.

## EEG transfer results

M25 mean EEG-only stops reproduced from frozen files are FAST 1.1818 s, MEDIUM 1.2943 s, and CONSERVATIVE 1.5102 s. The table reports the λ=1 primary matrix; the complete 45-condition matrix and paired trial rows are in the CSV artifacts.

| Gate | EEG point | Context-active mean gain | All-trial mean gain | Accuracy delta | Wrong early stops | Active mean reaches 0.4 s? | Reaches 0.5 s? |
|---|---|---:|---:|---:|---:|---|---|
| C65 | FAST | 0.377 s | 0.097 s | −0.02 pp | 2 | No | No |
| C65 | MEDIUM | 0.440 s | 0.147 s | −0.02 pp | 2 | Yes | No |
| C65 | CONSERVATIVE | 0.575 s | 0.245 s | −0.02 pp | 2 | Yes | Yes |
| C85 | FAST | 0.373 s | 0.100 s | −0.11 pp | 10 | No | No |
| C85 | MEDIUM | 0.442 s | 0.154 s | −0.11 pp | 10 | Yes | No |
| C85 | CONSERVATIVE | 0.574 s | 0.258 s | −0.11 pp | 10 | Yes | Yes |
| C100 | FAST | 0.382 s | 0.104 s | −0.07 pp | 6 | No | No |
| C100 | MEDIUM | 0.441 s | 0.158 s | −0.07 pp | 6 | Yes | No |
| C100 | CONSERVATIVE | 0.573 s | 0.261 s | −0.07 pp | 6 | Yes | Yes |

Positive lambda values 0.5, 1.0, 1.5, and 2.0 produced the same stop results at the frozen 100 ms evidence-grid resolution. This shows no observed gain distinction among positive weights at that replay resolution; it does not establish that λ has no effect at finer timing. λ=0 exactly reproduced the paired EEG-only result: zero gain, zero wrong Context early stops.

No positive-λ coverage condition preserves both paired accuracy and zero wrong early stops. The only zero-wrong conditions are the λ=0 EEG-only ablations, which have no Context gain. Therefore **none** of the raw 0.4/0.5 s crossings qualifies as `supported_in_transfer_simulation` under the specified safety rule.

Timing sensitivity at λ=1:

- With measured API arrival (median 1.639 s, p90 3.035 s, mean 1.915 s across 123 actual API calls), Context-active mean gain is only about 0.20–0.25 s and all-trial gain about 0.002–0.009 s; no condition reaches 0.4 s.
- At fixed 0.2 s or 0.4 s availability, raw active gain is roughly 0.37–0.58 s depending on EEG point. However, C85/C100 still produce wrong early stops and slight accuracy loss; C65 has no wrong stop in this 30-seed timing subset but is not enough to override its primary 100-seed result (2 wrong early stops).
- No no-delay violations occurred in any of the 467,280 transfer rows. There were 246 wrong early-stop records across the full primary and timing matrices, which is why the transfer integrity status must not be read as a claim that every condition is safe.

The transfer's held-out gate rates and mapped assignment rates differ. Only 51.6%, 69.2%, and 71.6% of primary C65/C85/C100 trial rows received an alignable semantic page in the seeded simulation; the remaining sampled active outputs had no unique target or no full three-choice target page and fell back to the paired baseline. This is a limitation of mapping the measured semantic benchmark to historical EEG, not a live runtime rule.

## M28 comparison and the eleven required questions

1. **Did relational-affordance reasoning improve generalization?** Not demonstrated. M30 exercised 46 held-out sequential decisions across four held-out families, but top-1 was 76.5%; M28's static curated held-out subset reported 11/12 (91.7%). The case designs differ, so this is descriptive rather than a controlled head-to-head. M30 increased measured overall participation from M28's 47.5% to 69.6–89.1%, while active precision fell to 60.0–68.8%. The evidence is consistent with broader coverage bought by more guessing, not proven generalization improvement.
2. **Realized C65/C85/C100 participation?** Overall held-out 69.6% / 87.0% / 89.1%; among eligible informative decisions 70.6% / 94.1% / 97.1%.
3. **Precision paid for higher participation?** Active precision was 68.8% / 60.0% / 61.0%; the higher gates add wrong-active Context and do not improve top-1 over C65.
4. **Strongest and weakest families?** Kitchen/food preparation was strongest. State-dependent was weakest by active precision; assistive/handover also remained weak. Per-family denominators are small.
5. **Later-round degradation?** Round 3 dropped to 54.5% held-out top-1 before round 4 recovered to 80.0%. This is a concerning non-monotonic dip, not a smooth trend.
6. **How does `lambda_ctx` change gain and safety?** λ=0 is exact EEG-only. All tested positive values had identical stop outcomes on the 100 ms grid, with similar raw gains and the same wrong-stop pattern; no positive value passed the safety rule.
7. **Which coverage × lambda combinations preserve zero wrong early stops?** Only λ=0 across all three gates; these are the no-Context ablations.
8. **Which conditions reach 0.4 s?** Raw precomputed active means reach 0.4 s for MEDIUM and CONSERVATIVE at all three gates. FAST does not. None is supported under the combined accuracy/zero-error rule.
9. **Which conditions reach 0.5 s?** Only CONSERVATIVE raw active means reach 0.5 s at all three gates. None is safe-supported.
10. **Is any result more than transfer simulation?** No. Semantic model results are curated benchmark observations; EEG gains are simulated transfer on randomized historical trials.
11. **What should be prospectively validated next?** Freeze a new untouched family/template test set and improve ambiguity/false-active calibration using train/dev only. Then evaluate with independently authored user tasks and true next-target labels; only afterward run a prospective human EEG study with Context precomputed before trial onset, retaining paired EEG-only baselines and a zero-wrong-stop safety gate. M32 text-scene observations must not be used to retune these held-out gates or lambda results.

## Reliability, limits, and artifacts

- Repeatability subset: 16 points; same status 100%, same top candidate 100%, same full ranking 87.5%.
- API: 123 of 126 points called the model; mean 1,914.9 ms, median 1,639.4 ms, p90 3,035.1 ms, p95 3,243.9 ms, max 3,541.2 ms. 37/126 points used one correction retry (29.4%). Hallucinated-candidate attempts: 0. Explicit state/affordance contradiction attempts: 25.
- Global-to-page projection audit: 279 deterministic pages; 66.7% had three choices and 11.1% had two. Across all page rows, the projected page top matched the global top in 44.4%; a unique acceptable target appeared in 47.0% of pages. These all-page proportions include pages that are not the target page and should not be read as page-selection accuracy.
- M28 static baseline reported 1.0 active precision at 47.5% overall coverage, 100% ambiguity/invalid rejection, and (precomputed, CONSERVATIVE) 0.574 s active / 0.262 s all-trial mean gain with zero wrong early stops. M30's corresponding C65 transfer gain is similar in magnitude but has two wrong early stops and a small accuracy loss. The datasets are not interchangeable.
- Fourteen SVG plots are in `plots/`; the first eight cover semantic coverage, families, rounds, prior distributions, page projection, and API latency. Six more cover coverage×lambda gains/errors, EEG operating-point gains, M28 comparison, and 0.4/0.5 s feasibility.
- No Quest, ADB/MQDH, ND8, COM11, physical robot, live EEG collection, raw EEG modification, production M19 code change, or real VLA dispatch was performed. The API key was not persisted.

Machine checks are recorded in `final_validation.json` and `eeg_transfer_validation.json`. They establish experiment/output integrity; they do not imply safe Context acceleration.
