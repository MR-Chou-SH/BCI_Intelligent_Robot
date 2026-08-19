# M6.3a — Window-Length Characterization

Date: 2026-08-19
Status: Completed / PASS WITH WARNINGS; M6.3 closeout complete

## Experimental design

This characterization changes only the analysis window. The formal M6.1b
session, 30 trials, session-specific usable channels CH2/3/4/5/7, 1000 Hz raw
sampling, 0.5 s onset guard, target frequencies, three harmonics and frozen
Standard CCA/FBCCA implementations remain unchanged. Windows are the predefined
grid 0.5/1.0/1.5/2.0/2.5/3.0 s. Every window uses the same stimulus-relative
epoch start and the same trial provenance.

## Latency-accuracy result

| Window | CCA | FBCCA |
|---:|---:|---:|
| 0.5 s | 23/30 (76.7%) | 23/30 (76.7%) |
| 1.0 s | 29/30 (96.7%) | 30/30 (100%) |
| 1.5 s | 30/30 (100%) | 30/30 (100%) |
| 2.0 s | 30/30 (100%) | 30/30 (100%) |
| 2.5 s | 30/30 (100%) | 30/30 (100%) |
| 3.0 s | 30/30 (100%) | 30/30 (100%) |

Sample counts are 500, 1000, 1500, 2000, 2500 and 3000. At 0.5 s, CCA
per-class accuracy is LEFT 8/10, CENTER 7/10, RIGHT 8/10; FBCCA is LEFT
7/10, CENTER 8/10, RIGHT 8/10. At 1.0 s, CCA is LEFT 10/10, CENTER 9/10,
RIGHT 10/10; FBCCA is 10/10 for every class. From 1.5 s onward both decoders
are 10/10 for every class.

The shortest predefined window reaching 80%, 90% and 100% is respectively
1.0 s, 1.0 s and 1.5 s for Standard CCA. For FBCCA it is 1.0 s, 1.0 s and
1.0 s. These are current-session grid observations, not a declared optimal
online latency.

At 0.5 s, Standard CCA errors were mainly CENTER→LEFT (3) and LEFT→CENTER
(2), with two RIGHT→LEFT errors. FBCCA had broader short-window errors,
including LEFT→RIGHT (2). CENTER is the weakest CCA class at 0.5/1.0 s;
LEFT is the weakest FBCCA class at 0.5 s.

## Margins and numerical validity

CCA margin min/median/mean/max by window: 0.001/0.068/0.082/0.205 at 0.5 s;
0.005/0.105/0.117/0.299 at 1.0 s; 0.007/0.121/0.135/0.232 at 1.5 s;
0.017/0.150/0.138/0.271 at 2.0 s; 0.034/0.121/0.127/0.260 at 2.5 s;
0.043/0.137/0.134/0.284 at 3.0 s. FBCCA margins were
0.001/0.162/0.194/0.469; 0.027/0.324/0.352/0.638;
0.186/0.443/0.423/0.685; 0.233/0.474/0.484/0.698;
0.320/0.513/0.517/0.720; and 0.314/0.535/0.538/0.730.

All windows produced finite deterministic results. Signal/reference ranks were
recorded per trial. The FBCCA 0.5 s window legally accepts the fixed 0.5 s
reflection padding, but filtering occupies a large fraction of the short
window; this boundary effect is a warning, not a parameter adjustment. Raw
CCA and FBCCA margins have different score scales and are not directly
comparable as absolute algorithm quality.

## Interpretation and boundary

The current session supports reliable three-class evidence at 1.5 s for both
decoders, while FBCCA reached 100% at 1.0 s in this predefined grid. This does
not establish online latency: the dataset is one session/subject context with
software-derived sample association, and there is no cross-session,
cross-subject or online validation. Hardware timing, physical optical timing,
ND8 hardware sample anchoring and optical verification of nominal frequencies
remain unverified.

The result is suitable for a future window-length figure and motivates
short-window latency/robustness, preprocessing and filter-realization
characterization, followed by session-generalization or pseudo-online work.
