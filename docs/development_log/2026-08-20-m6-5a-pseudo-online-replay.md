# M6.5a — Pseudo-Online Decoder Replay

## Scope and frozen policy

M6.5a is replay-only. It uses saved packet order/receive-time evidence and software-derived stimulus-start associations; no ND8 was acquired, no Quest feedback was sent, and no decoder parameter was tuned. The first-decision policy is frozen at CH2/3/4/5/7, 1000 Hz, 0.5 s onset guard, 1.5 s analysis window, demean-only preprocessing, 7.2/9/12 Hz, and three harmonics. NumPy FBCCA is the default pseudo-online baseline backend, while Standard CCA and legacy-style FBCCA share the same interface.

The logical architecture is:

`historical packet → rolling buffer → event known → eligibility → frozen window → decoder → prediction`

The buffer accepts only sample-contiguous packet sequences. It resets on true transition/loss boundaries. A recorded `timestamp_delta_mismatch` anomaly is retained as evidence but does not itself create a sample gap when packet sequence and cumulative sample indices remain continuous.

## Timing and evidence semantics

The earliest event-locked first decision is after 0.5 s guard plus 1.5 s analysis, i.e. 2.0 s algorithmic wait. With 200-sample ND8 packets, packetization can add up to approximately one packet cadence before a newly required endpoint arrives. Compute duration is recorded separately. Transport, render, hardware, and physical optical latency are not measured here and must not be added to a claimed system latency.

Ground truth is attached only after prediction for evaluation. The decoder backend accepts only the EEG window; packet replay never permits samples arriving after the logical decision point.

## Results and equivalence

The artifacts under `D:\EEG_Study\m6_4\replay\m6_5a-pseudo-online-{A,B1,B2}.json` record every decision and compute duration. For all 89 fixed QC-valid trials, pseudo-online predictions exactly equal the existing 1.5 s frozen offline result for the same session/backend/window:

| Session | Standard CCA | NumPy FBCCA | Legacy-style FBCCA |
| --- | ---: | ---: | ---: |
| A (formal) | 30/30 | 30/30 | 30/30 |
| B1 (exploratory) | 26/29 | 28/29 | 27/29 |
| B2 replay (exploratory) | 29/30 | 29/30 | 30/30 |

The equivalence check is 30/30, 29/29, and 30/30 respectively for every backend. B1/B2 remain exploratory evidence; B2's original formal manifest remains incomplete.

Observed compute medians on this machine were approximately 1.4–1.7 ms for Standard CCA, 4.4–4.7 ms for NumPy FBCCA, and 15.3–16.3 ms for legacy-style FBCCA. These measurements suggest computation is small relative to the 1.5 s EEG window, but are not a real-time end-to-end latency benchmark.

## Boundary

M6.5a validates software architecture and offline/pseudo-online epoch equivalence only. It does not establish true online ND8 behavior, event transport latency, end-to-end latency, physical optical timing, hardware sample anchoring, generalized accuracy, or robot control validity.
