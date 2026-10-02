# M28 Final Report — Real Semantic Context and EEG Transfer

Generated 2026-10-02T10:55:27Z. Benchmark SHA-256: 41310ac326c33950982dd820a7b2235b144e2bc610dd58b95e36ea62c492da8e.

## Findings

- The frozen curated semantic benchmark contains 40 cases. Informative single-target top-1 was 19/20 (95.0%); top-3 recall was 20/20 (100.0%).
- Context activated in 19/40 cases (47.5%). Active target precision was 19/19 (100.0%). This is a small curated sample, not population user-intent accuracy.
- Ambiguity handling was correct in 7/7 (100.0%); invalid inputs were rejected in 8/8 (100.0%). 21/40 cases were ambiguous, Context OFF, or invalid and did not activate Context.
- Held-out single-target top-1 was 11/12 (91.7%); current M20 was 6/6 (100.0%), and adversarial was 2/2 (100.0%). One held-out target was rejected as invalid and remains a miss.
- The 8-case repeat subset had 7/8 structurally consistent cases.
- Active API latency was mean 1.142s, median 1.005s, P90 1.829s. Same-trial Context often arrives after FAST/MEDIUM EEG decisions.

## EEG transfer estimates

This is SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION, not prospective causal Context performance. Historical EEG trials were randomized; measured semantic quality was mapped to trial classes with seeded simulation machinery. That mapping is not evidence that a real scene prior predicts the next trial target.

| Availability | Operating point | Context-active mean / median | All-trial mean [95% seed interval] | Context accuracy | Applied rate | Paired baseline accuracy | Wrong early stops |
|---|---|---:|---:|---:|---:|---:|---:|
| Live API | FAST | 0.337 [0.207, 0.476] / 0.300 | 0.025 [0.009, 0.044] | 1.000 | 7.3% | 1.000 | 0 |
| Live API | MEDIUM | 0.361 [0.239, 0.469] / 0.400 | 0.043 [0.021, 0.067] | 1.000 | 11.9% | 1.000 | 0 |
| Live API | CONSERVATIVE | 0.376 [0.300, 0.454] / 0.400 | 0.079 [0.052, 0.112] | 1.000 | 21.0% | 1.000 | 0 |
| Precomputed | FAST | 0.386 [0.304, 0.469] / 0.300 | 0.105 [0.071, 0.142] | 1.000 | 27.1% | 1.000 | 0 |
| Precomputed | MEDIUM | 0.444 [0.359, 0.532] / 0.400 | 0.159 [0.118, 0.207] | 1.000 | 35.9% | 1.000 | 0 |
| Precomputed | CONSERVATIVE | 0.574 [0.499, 0.630] / 0.500 | 0.262 [0.203, 0.324] | 1.000 | 45.7% | 1.000 | 0 |

Precomputed Context-active estimates reach 0.444s for MEDIUM and 0.574s for CONSERVATIVE. Their all-trial means are 0.159s and 0.262s. Live API Context-active means are below 0.4s. The 0.4–0.5s prospective target is NOT ESTABLISHED: transfer alignment is simulation machinery, measured coverage is 47.5%, and no natural-task EEG labels were collected. The synthetic frontier contains 29 safe combinations for 0.40s and 13 for 0.50s; those are assumed combinations, not measured engine support.

At the measured 47.5% coverage, 0 / 0 Conservative frontier combinations meet 0.40s / 0.50s. One safe synthetic 0.40s example assumes precision 1.00, coverage 0.75, prior top mass 0.90, and availability at 0.0s. A safe synthetic 0.50s example assumes precision 1.00, coverage 1.00, prior top mass 0.90, and availability at 0.0s. These are simulated assumptions, not measured engine capability.

The binding limits are Context coverage and same-trial arrival time. Precomputation exposes transfer headroom at CONSERVATIVE; prospective causal alignment to natural task intent remains untested. A natural-task study must freeze scene state and intent before each trial, log the prior before EEG evidence, compare EEG-only and Context-assisted decisions prospectively, and report participant-level errors and latency.

## Integrity and boundaries

- M25 input manifest before/after: 0bc5b2032df564342562d3af085a174d81f707db42dfc161c14db4715d5fec5a / 0bc5b2032df564342562d3af085a174d81f707db42dfc161c14db4715d5fec5a; source hashes unchanged: True.
- Transfer used 100 seeds and retained FAST, MEDIUM, and CONSERVATIVE frozen operating points.
- No Quest, ADB/MQDH, ND8, COM11, robot, or raw EEG waveform was accessed or modified.
- Required SVG figures are in plots/.
