# M6.3b — FBCCA Filter Realization Validation

Date: 2026-08-19
Status: Completed / PASS WITH WARNINGS; M6.3 closeout complete

## Purpose and frozen comparison

This experiment changed only FBCCA filter realization. It reused the same
formal M6.1b dataset, 30 trials, epoch provenance, CH2/3/4/5/7 channels,
0.5 s onset guard, target frequencies, three harmonics, three conceptual bands
and `b^-1.25 + 0.25` weighted rho fusion. Only the fixed windows 0.5, 1.0,
1.5 and 3.0 s were compared.

Variant A is the current NumPy rFFT raised-cosine zero-phase filter with 0.5 s
reflection padding. Variant B reconstructs the legacy Chebyshev-I design:
dynamic `cheb1ord` with 3 dB pass/stop transition input and 40 dB stopband
attenuation, 0.5 dB ripple, and bands 4/6/90/100, 10/14/90/100 and
16/22/90/100 Hz. At 1000 Hz its dynamic orders are 11, 10 and 9.

## Dependency and numerical issue

The repository has no dependency declaration and the configured runtime had
NumPy but no SciPy. SciPy 1.14.1 was installed into that workspace runtime to
reproduce the legacy filter family. Its wheel also updated that runtime's
NumPy to 2.2.6. No repository-wide dependency file was invented.

The literal legacy transfer-function form (`cheby1` returning b/a followed by
`filtfilt`) overflowed at the frozen 1000 Hz configuration, due to the high
dynamic orders and ill-conditioned polynomial coefficients; CCA then failed to
converge. This was recorded rather than hidden. Variant B therefore uses the
same Chebyshev-I design in SOS form with SciPy `sosfiltfilt`, preserving
forward/reverse zero-phase filtering while providing a stable realization.
This is legacy-style, not bit-for-bit equivalent to the old b/a path.

SOS default pad lengths are 69, 63 and 57 samples for the three bands. Both
0.5 s (500 samples) and 1.0 s (1000 samples) exceed these pad lengths and
produced finite deterministic outputs. Frequency-response checks found all
three bands with interior passband gain above -1 dB and both stop edges below
-30 dB. Target frequencies and harmonics fall in the expected conceptual
sub-band structure.

## Accuracy and agreement

| Window | NumPy FBCCA | Legacy-style FBCCA | Agreement |
|---:|---:|---:|---:|
| 0.5 s | 23/30 (76.7%) | 19/30 (63.3%) | 21/30 |
| 1.0 s | 30/30 (100%) | 28/30 (93.3%) | 28/30 |
| 1.5 s | 30/30 (100%) | 30/30 (100%) | 30/30 |
| 3.0 s | 30/30 (100%) | 30/30 (100%) | 30/30 |

Disagreement trials were 4, 6, 8, 11, 16, 18, 25, 27 and 28 at 0.5 s;
16 and 29 at 1.0 s; none at 1.5 or 3.0 s. At 0.5 s, legacy-style per-class
accuracy was LEFT 3/10, CENTER 10/10 and RIGHT 6/10. At 1.0 s it was LEFT
8/10, CENTER 10/10 and RIGHT 10/10. The current NumPy variant was 7/8/8 at
0.5 s and 10/10/10 at 1.0 s.

Raw margin summaries (min/median/mean/max) are not directly comparable across
realizations. NumPy: 0.001/0.162/0.194/0.469, 0.027/0.324/0.352/0.638,
0.186/0.443/0.423/0.685 and 0.314/0.535/0.538/0.730. Legacy-style:
0.036/0.177/0.229/0.677, 0.016/0.309/0.318/0.743,
0.082/0.424/0.401/0.772 and 0.223/0.512/0.512/0.714.

## Interpretation and boundary

The short-window result is filter-realization-sensitive: the legacy-style
variant loses 4 trials at 0.5 s and 2 trials at 1.0 s relative to the current
variant, while both agree perfectly at 1.5 and 3.0 s. This does not establish
that either realization is globally better; it identifies a short-window
implementation risk requiring future cross-session validation.

The dataset remains one session/30 trials. Hardware timing, physical optical
timing, ND8 hardware sample anchoring and optical verification of nominal
frequencies remain unverified; epoch association is software-derived. No
channel optimization, dense window search, pseudo-online or online validation
was performed.

M6.3 closeout conclusion: the window-length curve shows a clear
latency/accuracy trade-off, with all tested realizations stable at or above
1.5 s in this session. At or below 1.0 s, FBCCA is sensitive to filter
realization. Direct transfer-function versus SOS numerical stability is a
reproducibility and implementation observation, not evidence that either
filter is globally superior. Further same-session parameter searching would
add selection-bias/overfitting risk; independent cross-session validation is
the next scientific requirement.
