# M33 Semantic Context Repair

Run date: 2026-10-03
Branch: `codex/m33-task-state-context-fix`
Benchmark: frozen M30 sequence benchmark `899aae6f…d237b0` (56 train / 24 dev / 46 held-out)

## Result

The shared M30/M32 engine now asks for ordered task hypothesis and progress, scores each candidate's task continuation separately from its relation, and computes the final score locally as `0.70 continuation + 0.20 relation confidence + 0.10 (1 - task switch penalty)`. An incompatible relation is downgraded for only that candidate; the other candidates retain their scores. Unusable top-level responses still fail closed. Ambiguous/context-off outputs do not expose a sharp q prior.

The train/dev-only gate froze at `0.3039226`: **20/21 correct active cases (95.24%)**, covering **21/80 train+dev decisions (26.25%)**. The measured held-out result was **16/17 (94.12%)** at **17/46 coverage (36.96%)**. The single false active case was `assist_phone`, round 2: the gate ranked cable above the labeled user zone. Two other held-out responses ended in invalid status after bounded schema correction and were rejected. Held-out was called once after gate freeze and was not used to change the gate or prompt.

## Before and after

| Measure | M30 baseline | M33 result |
|---|---:|---:|
| Held-out informative top-1 | 26/34 (76.47%) | 17/22 (77.27%) |
| High-precision active precision | C100: 25/41 (60.98%) | Frozen train/dev gate: 16/17 (94.12%) held-out; 20/21 (95.24%) train/dev |
| Active coverage | C100: 41/46 (89.13%) | 17/46 (36.96%) held-out |
| Expected ambiguity recognized | 1/9 (11.11%) | 3/9 (33.33%) held-out |
| Held-out `CONTEXT_OFF` | 2/46 (4.35%) | 12/46 (26.09%) |
| Repeated-call status agreement | 16/16 (100%) | 6/6 dev repeats (100%) |
| Repeated-call top-1 agreement | 16/16 (100%) | 5/5 informative dev repeats (100%) |
| Exact repeated full ranking | 14/16 (87.50%) | 3/5 informative dev repeats (60.00%) |
| Mean q L1 on repeats | 0.0729 | 0.2729 over informative dev repeats |

The raw provider response is still less repeatable than the previous M30 run on full ranking and q values. To make M32 Undo/Reset stable within a running process, `SemanticContextEngine` now memoizes up to 128 successful sequential results in memory, keyed by the canonical scene, ordered history, candidate list, task state, model, and prompt. It does not write scene data to disk; a cache miss still makes one model call. Identical cached inputs return the exact same status, ranking and q. The cache does not make a claim that the remote model itself is deterministic. The official [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/) documents temperature/top-p controls but no request seed, so no server seed was added.

## M32 ordered-history regressions

- `apple → knife`: inferred `cut_fruit_with_knife`, progress `fruit_and_tool_selected`. The same remaining candidate IDs were used for the reverse order. The forward q ranked the plate first (`q=0.632`), then sink (`0.148`), orange (`0.086`); orange was not promoted just because the knife can cut it. Reversing the history changed task progress and q (L1 distance `0.440`).
- `knife → apple`: inferred the same broad cutting task but a different progress code (`knife_selected_fruit_selected`); q changed with the order.
- `phone → wireless charger` and the reverse order used the same candidate IDs. The reverse response was conservatively marked ambiguous and returned uniform q; it did not produce a sharp EEG prior. Pair q L1 distance was `1.195`.
- The mixed phone/food scene returned ambiguous with uniform q and `eligible_informative=false`.

The candidate-level regression also passes: a full-box relation becomes `NONE`, the call is not retried, other candidate scores remain, and q is non-uniform. The M30 validator confirmed 126 model inputs contain no evaluation-label leakage.

## M25 historical transfer

The read-only replay reused the frozen 88-trial M25 cohort, its M23 operating-point sidecar, and 100 deterministic assignment seeds. No raw EEG waveform was read and the M25/M23 source hashes matched before and after.

| EEG point | M30 C100 Context-active gain | M30 all-trial gain | M33 Context-active gain | M33 all-trial gain | Wrong early stops (M30 → M33) |
|---|---:|---:|---:|---:|---:|
| FAST | 0.382 s | 0.104 s | N/A (0 applied) | 0.000 s | 6 → 0 |
| MEDIUM | 0.441 s | 0.158 s | N/A (0 applied) | 0.000 s | 6 → 0 |
| CONSERVATIVE | 0.573 s | 0.261 s | N/A (0 applied) | 0.000 s | 6 → 0 |

M33 semantic authorization occurred for 17/46 held-out decisions, but none passed the unchanged M25 page-mass, raw-EEG-top agreement, and EEG evidence/margin/stability conditions early enough to stop sooner. Accuracy delta was 0 pp, wrong early stops 0, and no-delay violations 0. This is a safe exact-EEG fallback result with no demonstrated acceleration; it is not prospective EEG validation.

## Verification and boundaries

- Focused Python compile check: PASS.
- Relevant sequence, engine, M32 CLI, and M32 runner tests: **29/29 PASS**.
- M30 frozen benchmark validator: PASS (126 points and zero evaluation-input leakage).
- M32 final validator: `PASS_WITH_REPORTED_LIMITATIONS` (60 decisions; historical limitations retained).
- No Quest, ADB, ND8, COM11, live EEG, raw EEG modification, physical robot, production M19, or real VLA dispatch was used.

The held-out gate precision is 0.88 percentage points below the train/dev 95% target. This is reported as a generalization limitation; the held-out run was not retuned or repeated. The remote model's full-ranking/q repeatability also remains a limitation outside the same-process cache guarantee.
