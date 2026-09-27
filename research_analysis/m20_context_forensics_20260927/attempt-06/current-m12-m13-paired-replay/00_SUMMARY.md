# M15 Stage 2 Paired Context Replay Summary

1. Context OFF: **0 / 89 early-stop**.
2. Context ON: **3 / 89 early-stop**.
3. Context additionally created **3 early-stop(s)** relative to OFF.
4. Context made **3 trial(s)** decide earlier.
5. Those trials advanced by **1.200 s on average**.
6. All 89 paired trials: mean latency delta (OFF − ON) = **0.040 s**.
7. Accuracy: **OFF = 89/89 (100.000%)**, **ON = 88/89 (98.876%)**.
8. Context harmed final accuracy in **1 trial(s)**.
9. Wrong early-stop: **OFF = 0**, **ON = 0**.

## Evidence boundary

This is an offline/pseudo-online replay of **real human EEG-derived FBCCA
score trajectories** with a **simulated causal observable-history Context
prior**. It is not a prospective human Context-aware experiment, a physical
end-to-end latency measurement, or cross-subject validation. The synthetic
Context generator never receives the current true label.

## Frozen timing semantics

The reported 2.0–4.0 s values are effective algorithmic acquisition times
since stimulus onset: 0.5 s onset guard + 1.5 s analysis window + 0.2 s
causal update increments. The 1.5 s window length is not itself the decision
latency, and 4.0 s is not a physical display-to-robot latency.

## Paired effects

| Metric | Context OFF | Context ON | Difference / paired result |
|---|---:|---:|---:|
| Trials | 89 | 89 | same EEG trials |
| Accuracy | 100.000% | 98.876% | helped 0, harmed 1 |
| Early-stop | 0 | 3 | ON−OFF 3 |
| Fallback | 89 | 86 | ON−OFF -3 |
| Mean decision time | 4.000s | 3.960s | mean OFF−ON 0.040s |
| Median decision time | 4.000s | 4.000s | median OFF−ON 0.000s |
| P90 decision time | 4.000s | 4.000s | — |
| Mean saved time vs 4.0s | 0.000s | 0.040s | — |

Context earlier: **3**; OFF fallback → ON early:
**3**; OFF early → ON earlier:
**0**; delayed: **0**;
final class changed: **1**.

Full machine-readable details are in `03_PAIRED_RESULTS.md`, the two summary
JSON files, and the paired CSV files in this directory.

Provenance digest: `99e47ddcd15216fe…`.
