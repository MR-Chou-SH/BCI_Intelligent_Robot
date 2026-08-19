# M6.2b — Legacy-informed FBCCA Baseline

Date: 2026-08-19
Status: Completed / PASS WITH WARNINGS; M6.2 pending closeout review

## Legacy audit and parameter boundary

Drone2.1 configures raw ND8 input at 1000 Hz, resamples to 250 Hz, applies a
50 Hz notch, then calls FBCCA with a 3 s classifier window. `OperationMain`
requests `winLEN + lag` (3.14 s), while `fbCCA.predict` discards the first
0.14 s before retaining the following 3 s. Thus the actual classifier input is
3 s, not an independently established 2 s interval. The historical 2 s
description is not supported by the active code path.

Its FBCCA uses three Chebyshev-I `filtfilt` sub-bands: pass/stop lower edges
6/4, 14/10 and 22/16 Hz, with common high pass/stop edges 90/100 Hz. It builds
three sine/cosine harmonics, calculates one CCA correlation `rho` per
sub-band/class pair, and fuses them linearly with `w_b = b^-1.25 + 0.25` before
`nanargmax`. Its 8--16 Hz candidate grid, 250 Hz resampling, 50 Hz notch and
lag are experiment-bound parameters and were not copied into M6.2b.

## Fixed current baseline

M6.2b reuses M6.2a's exact 30 formal epochs, session-specific channels
CH2/3/4/5/7, 1000 Hz rate, 0.5 s onset guard, 3.0 s analysis window, per-channel
demean, target frequencies 7.2/9/12 Hz and three harmonics. It adds three
legacy-informed filter-bank bands with the same edge values. The runtime lacks
SciPy, so the legacy dynamically designed Chebyshev-I `filtfilt` cannot be
reproduced without installing a dependency. The current implementation instead
uses a deterministic NumPy rFFT raised-cosine, zero-phase response with 0.5 s
reflection padding. This preserves the FBCCA structure but is an explicitly
documented implementation difference. Filter edge transients/circular spectral
assumptions remain a warning for later characterization.

Each sub-band produces all three Standard CCA `rho` scores. M6.2b fuses those
scores with the unchanged legacy formula and predicts by fused-score argmax.
Ground truth appears only in the shared evaluator.

## Formal result

The fixed configuration was set before the formal result was viewed. It produced
30/30 correct predictions: LEFT 10/10, CENTER 10/10 and RIGHT 10/10, with a
diagonal confusion matrix. All true frequencies ranked first. FBCCA raw fused
margin min/median/mean/max was approximately 0.314/0.535/0.538/0.730; frozen
Standard CCA's raw score margins were 0.043/0.137/0.134/0.284. The score scales
differ because FBCCA sums weighted correlations, so this is evidence of
within-decoder rank separation only, not a direct numerical superiority claim.

## Boundary and interpretation

This is single-session offline separability evidence only. Hardware timing,
physical optical timing and ND8 hardware sample anchoring remain unverified;
all epoch indices are software-derived estimates. The result neither establishes
cross-session, subject-independent nor online performance. M6.3 has not begun.
