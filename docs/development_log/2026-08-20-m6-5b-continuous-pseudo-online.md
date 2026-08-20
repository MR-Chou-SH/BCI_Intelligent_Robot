# M6.5b — Continuous / Stabilized Pseudo-Online Decoding

M6.5b extends the replay-only M6.5a path with frozen 200-sample (0.2 s) sliding opportunities. Every analysis window is 1.5 s after the fixed 0.5 s guard and must end no later than the 4.0 s stimulation boundary. Prediction artifacts retain each candidate-score timeline; ground truth is evaluated only afterwards.

The predeclared policies are First, 2-Consecutive, and 3-Consecutive. They do not use thresholds, majority voting, adaptive stopping, or ground truth. NumPy FBCCA is the primary characterization backend.

For A / B1 / B2, First and 2-Consecutive made decisions for every fixed QC-valid trial. First/2 decision results were A 30/30, B1 28/29, B2 29/30, at 2.0 s and 2.2 s respectively. Three-Consecutive was also 100% coverage and yielded A 30/30, B1 29/29, B2 30/30; its median latency was 2.4 s (a minority of B1/B2 trials required 2.8 s after an interrupted early run). These are exploratory historical replay observations, not an independently validated policy selection.

The simple engineering candidate for a future separately authorized M6.6 is 2-Consecutive: it adds one fixed step to First while retaining full replay coverage. This is not claimed as optimal. Three-Consecutive removed the observed early errors in these historical sessions but adds another step and risks overfitting this same evidence.

M6.5b remains software-only. It does not validate live ND8 source behavior, Quest/transport/render/hardware latency, physical optical timing, generalized accuracy, or robot control.
