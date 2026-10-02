# M28 EEG transfer specification

Label: `SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION`. Historical EEG trials are randomized and do not carry natural semantic-scene target labels. Correct engine predictions are aligned to each trial's true class only after engine evaluation as explicit simulation machinery. Observed incorrect active outputs retain their candidate-offset error under seeded class mapping.

The source is the frozen 40-case M28 semantic benchmark. Primary transfer samples the empirical 19/40 active rate, full uncalibrated three-class pseudo-prior vectors, and observed API latencies with seeded assignments independent of EEG evidence. A separate precomputed-before-trial sensitivity sets availability to 0 s; fixed arrival sensitivities are 0.2/0.4/0.6/0.8 s. All M25 priors still pass its prior-top mass ≥0.70, raw-top agreement, frozen shared threshold, 0.40 s stability, and paired EEG-only cap. No M25/M23/M24 historical output is modified.

The required-quality frontier is a separate synthetic sensitivity surface. It varies assumed active precision, coverage, prior top mass, and availability while retaining the exact frozen M25 stopping parameters. It is not a measured property of the semantic engine.
