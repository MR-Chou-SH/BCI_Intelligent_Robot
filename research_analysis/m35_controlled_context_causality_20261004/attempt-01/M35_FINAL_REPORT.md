# M35 Controlled Context Causality — Final Report

Date: 2026-10-04
Run: m35-controlled-context-causality-20261004
Scope: software-only, paired mechanistic replay
Protocol: research_analysis/M35_CONTROLLED_CONTEXT_CAUSALITY_PROTOCOL.md (copied byte-for-byte from the user attachment; SHA-256 verified)

## Result

M35 completed the controlled mechanism and tradeoff characterization. It does not establish prospective or population-level performance. The key result is mixed:

- The frozen M34 EEG-only policy reproduced exactly at its selected stop for B1, B2, and S7.
- A conservative Context stop rule selected using A/B1 retained a small conditional acceleration with zero induced wrong early stops on A/B1.
- That rule applied to only 6.8% of A/B1 trials at the strongest Context setting, so overall gain was 11.9 ms. The gain among applied trials was 175 ms.
- In post-hoc S7 replay, the same frozen rule produced 15 Context-induced wrong early stops over the full strength sweep. B2 had none. This post-hoc result was not used to select or tune the rule and prevents a cross-session safety claim.
- Neutral Context reproduced EEG-only class and stopping time exactly.
- Oracle replay indicates the EEG trajectories can support about 190–212 ms mean acceleration with no margin/stability requirement. The conservative realisable rule captured only a small part of that upper bound.

## Design and data boundary

Each trial’s frozen EEG score trajectory was paired across conditions. Context priors used the requested q_top grid [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]; remaining mass was split equally between the other two classes. Each incongruent trial was replayed against both wrong Context classes.

A/B1 were the only sessions used to select a Context rule. B2 and S7 were opened only after that rule was frozen and are reported as post-hoc mechanistic replays. The controlled congruent and incongruent labels are synthetic experimental conditions, not semantic predictions.

The M34 EEG stack stayed frozen: FBCCA-003, three-band filter bank, three harmonics, channels [2,3,4,5,7], 0.5 s nominal onset guard, and dynamic policy E200_M175_S2 (0.20 s minimum evidence, 0.175 relative margin, two consecutive EEG tops, 1.0 s maximum). M34’s stress-session implementation uses the common S7 channel subset [2,4,7]; this was preserved. In every Context condition, the emitted class is argmax(raw EEG score). Context can authorize an earlier stop only.

## EEG-only reproduction

| Session | Split/use | Correct | Accuracy | Mean evidence | Median | p90 |
|---|---|---:|---:|---:|---:|---:|
| A | Development | 29/30 | 96.7% | 0.595 s | 0.500 s | 1.000 s |
| B1 | Development | 28/29 | 96.6% | 0.486 s | 0.400 s | 0.840 s |
| B2 | M34-consumed primary set; post-hoc only | 26/29 | 89.7% | 0.412 s | 0.350 s | 0.600 s |
| S7 | M34-consumed stress set; post-hoc only | 28/30 | 93.3% | 0.497 s | 0.375 s | 1.000 s |

The selected predictions, selected score vectors (absolute tolerance 1e-12), and evidence windows matched the M34 saved dynamic decisions for all 29 B1, 29 B2, and 30 S7 trials. A was recomputed with the same frozen FBCCA implementation. The source raw EEG hashes matched the M34 manifest before and after analysis.

Pooled A/B1 EEG-only performance was 57/59 (96.6%); mean evidence was 0.542 s, median 0.400 s, and p90 1.000 s.

## Controlled Context results

The A/B1 search compared 105 interpretable rules: relative-margin reduction coefficient alpha in [0, .05, .10, .15, .20, .25, .30], margin floor in [0, .025, .05, .075, .10], and two, three, or four consecutive raw EEG top updates. Selection required zero Context-induced wrong early stops across both wrong target classes and all strengths, no incongruent accuracy loss, and no delay; the eligible rule with the greatest congruent gain was selected.

Selected A/B1 rule:

- alpha = 0.05
- margin floor = 0.10
- four consecutive raw EEG top updates
- required Context top equals the current raw EEG top
- if authorization does not occur before the frozen EEG-only decision, return that exact decision and time

At q_top = 0.95 on A/B1:

| Condition | Accuracy | Mean evidence | Median | p90 | Mean paired gain | Applied-trial gain | Application | ≤300 ms | Context-induced wrong early stops |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EEG-only | 96.6% | 0.542 s | 0.400 s | 1.000 s | — | — | — | 28.8% | — |
| Congruent | 96.6% | 0.530 s | 0.400 s | 1.000 s | 11.9 ms | 175 ms | 6.8% | 28.8% | 0 |
| Incongruent, both wrong targets pooled | 96.6% | 0.542 s | 0.400 s | 1.000 s | 0 ms | — | 0% | 28.8% | 0 |
| Neutral | 96.6% | 0.542 s | 0.400 s | 1.000 s | 0 ms | — | 0% | 28.8% | 0 |

The congruent strength curve rose from 8.5 ms overall gain at q_top=.45 to 11.9 ms at .95, then largely plateaued; applied-trial gain was 250 ms at .45 and 175 ms at .95. Stronger Context increased the fraction of trials authorized, but did not increase the share finishing by 300 ms. The selected four-update rule cannot authorize before 350 ms on this schedule. For q_top=.95, the paired mean-gain bootstrap 95% interval was [0.8, 27.1] ms; paired sign-flip Monte Carlo p=.125, so the small development replay does not establish a statistically reliable mean gain. Paired accuracy did not change on A/B1.

The development-only selected rule produced zero induced wrong early stops. This safety finding did not transfer to S7: the frozen post-hoc S7 replay had 15 induced events across the q grid (none on B2). At q_top=.95, S7 had two induced events among 60 wrong-target trial replays, with 90.0% replay accuracy versus the 93.3% EEG-only baseline. The post-hoc result is descriptive and was not used to change the selected rule. Unsafe candidate failure trajectories from A/B1 and the frozen-rule S7 failures are saved in incongruent_failure_cases.csv.

### H1 — congruent acceleration

About 200 ms is achievable among the small subset of trials where the conservative gate applies: the selected rule yielded 175 ms mean applied-trial gain at the strongest setting and 250 ms at the weakest tested setting. Overall gain remained about 8–12 ms because application was sparse. The target is therefore conditionally attainable, not achieved as an overall average.

### H2 — incongruent robustness

On development A/B1, the selected gate fell back exactly to EEG-only for all incongruent cases and caused no early-stop errors. Post-hoc S7 contradicts a general safety claim: the same frozen rule caused 15 such events across the strength sweep. An A/B1-selected gate has not been shown to protect a shifted session.

### H3 — neutral equivalence

Neutral matched EEG-only prediction and evidence window exactly on every A/B1/B2/S7 trial.

## Oracle and stored M33 comparison

Oracle rules used the true class only as a controlled Context target; the emitted class remained the raw EEG top. The least constrained upper bound stopped at the earliest point where EEG top equaled the oracle target, with no margin or stability requirement.

| Post-hoc session | Oracle mean gain | Gain when accelerated | Oracle fraction ≤300 ms | Two-update oracle mean gain |
|---|---:|---:|---:|---:|
| B2 | 190 ms | 190 ms | 96.6% | 128 ms |
| S7 | 212 ms | 219 ms | 76.7% | 127 ms |

This upper bound shows that current score trajectories can contain about 200 ms of usable acceleration. Requiring two stable updates reduces mean upper-bound gain to about 127–128 ms; the A/B1-selected four-update safe gate is more conservative still.

M34’s stored precomputed M33 replay was also summarized without rerunning or retuning it:

| Session | Trial-seed pairs | Mean replay gain | Application rate | Gain among applied | Context-caused errors |
|---|---:|---:|---:|---:|---:|
| B2 | 2,900 | 26.3 ms | 15.8% | 166 ms | 11 |
| S7 | 3,000 | 23.7 ms | 11.1% | 214 ms | 12 |

This is a seeded historical-transfer replay, not prospective paired EEG/Context collection. Its modest overall gain is mostly a coverage problem; its applied-trial gain approaches the oracle, while the post-hoc error counts show calibration and stopping safety remain concerns.

## Guard sensitivity

The secondary guard analysis used A/B1 only and the requested guards 0.0/0.1/0.3/0.5 s. B1 dynamic E200_M175_S2 accuracy was 51.7%, 55.2%, 72.4%, and 96.6%, respectively. At fixed 0.30 s EEG evidence it was 37.9%, 44.8%, 41.4%, and 79.3%. Accuracy changed materially with the software event-to-sample guard; the 0.5 s guard is not physical onset validation. All timing remains nominal because optical onset and sample anchoring were not independently measured.

## Answers to the M35 questions

1. Frozen EEG-only mean/median/p90 evidence on A/B1: 0.542 / 0.400 / 1.000 s.
2. Congruent q_top=.95 reduced mean evidence by 11.9 ms overall; by 175 ms among applied trials.
3. About 200 ms was not achieved overall. It was near the target among applied trials. Oracle replay reached 190–212 ms overall.
4. 28.8% of A/B1 trials finished by 300 ms with EEG-only and with selected Context; Context added none.
5. Mean gain rose from 8.5 ms at q_top=.45 to 11.9 ms at .95; the applied-trial gain ranged from 250 ms down to 175 ms and application rose from 3.4% to 6.8%.
6. Stronger Context did not lower A/B1 accuracy under the selected rule. Post-hoc S7 accuracy fell to 90.0% at .95, with two induced events at that strength.
7. Across both wrong Context classes, A/B1 accuracy stayed at EEG-only and mean gain was zero. B2 had no induced events; S7 had 15 over the full strength sweep.
8. Context-induced wrong early stops: A/B1 0 under the selected rule; B2 0; S7 15 post-hoc.
9. The dangerous pattern is a temporarily wrong raw EEG top that remains stable with adequate margin long enough to satisfy the context-assisted stop rule, even though the EEG-only trajectory later resolves differently. Representative successful, safe-fallback, and dangerous trajectories are in figure 7; full traces are in the failure CSV.
10. The four-update A/B1 gate eliminated development errors while retaining 175 ms gain on applied trials, but only 6.8% coverage. S7 failures mean this gate has not been shown to eliminate errors outside development.
11. Neutral was exactly EEG-only.
12. Oracle mean gain was 190 ms on B2 and 212 ms on S7 with the one-update/no-margin upper bound; two-update oracle gain was about 127 ms.
13. Stored M33 replay gained 24–26 ms overall, compared with the 190–212 ms oracle upper bound. Its conditional applied gain was 166–214 ms, but application was sparse and errors remained.
14. The main bottleneck is safe stopping-gate calibration and coverage under session shift, with M33 application coverage also low. The EEG trajectories themselves permit a larger oracle gain. More independent sessions are needed to tell whether the gate can generalize.
15. The evidence does not support the intended claim as a general result. A/B1 supports small controlled congruent acceleration and exact neutral behavior; the post-hoc S7 failures prevent claiming that incongruent Context is safely contained across sessions. These data are small and historically reused, so no population-level or prospective claim follows.

The 95% target is reached on development A/B1 (96.6%), but not on the M34-consumed post-hoc baselines (B2 89.7%, S7 93.3%). Current evidence does not support a cross-session 95% accuracy claim.

## Figures and artifacts

The required seven SVG figures are in plots/:

1. Mean evidence by condition and strength
2. Strength versus paired latency gain
3. Strength versus accuracy
4. Context-induced wrong early stops, with development and post-hoc curves
5. Fraction finishing by 300 ms
6. Oracle versus stored M33 replay acceleration
7. Representative successful, safe-fallback, and dangerous trajectories

The required CSV/JSON artifacts are alongside this report: protocol, EEG-only reproduction, per-trial and condition results, oracle summary, all unsafe-development and frozen-rule post-hoc failure cases, strength tradeoff, paired statistics, and guard sensitivity.

## Verification limits and next action

This run used software replays only. No COM11, ND8, Quest, ADB, EEG acquisition, robot, or VLA action was performed. No raw EEG file was modified. Results must not be described as prospective Context validation or real semantic accuracy.

Reproduce the run with:

C:\Users\zsh21\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe research_analysis\m35_controlled_context_causality_20261004\attempt-01\run_m35_analysis.py

The repository software-default verification profile and the M35-specific artifact audit are recorded separately in the run handoff. The current scientific recommendation is to retain the frozen M34 EEG-only decoder and treat the M35 Context rule as an analysis result only; its post-hoc S7 safety failures do not justify presenting it as a validated operational gate.
