# M6 Development Log — ND8 EEG Online Classification

Date: 2026-08-18
Status: M6.0 audit completed; M6.1a implementation complete / real-device validation pending

## M6.0 audit conclusions

The Drone2.1 reference contains a legacy filter-bank CCA-style path, but it assumes timestamp/sample semantics that are not verified for this project and does not provide the M5 evidence chain. The active legacy invocation uses a 3.0-second window at 250 Hz after a 0.14-second lag; it is reference material only. No classifier code was copied or enabled in M6.0/M6.1a.

## M6.1a signal/channel sanity architecture

`eeg.signal_sanity.record` reuses the M5 `Nd8SerialAdapter`, its append-only raw packet and packet-metadata records, the host-MAC readiness lifecycle, continuity timeline, and `PostSyncAssociationGate`. A new external session stores every SDK channel (exactly eight) rather than selecting channels before evidence review.

Each session contains raw packet data, packet metadata, post-sync gate records, a manifest and optional append-only manual annotations. The manifest records mode, requested/actual duration, expected/observed sample count, 1000 Hz configuration, channel schema, free-text electrode note, Git commit, Python runtime, and the persistent timing-evidence boundary.

The modes are `rest` (30 s default), `artifact_sanity` (30 s default), and `single_ssvep_sanity` (15 s default). The latter records only the intended Quest center target at nominal software frequency 9 Hz; it is not a classifier and does not assert physical optical timing.

## Offline analysis and software verification

`eeg.signal_sanity.offline_analyze` reads saved raw JSONL rather than live objects. It writes `analysis/signal-quality-summary.json`, including per-channel finite counts, descriptive statistics, constant/placeholder candidate, repeated-extreme/clipping candidate, continuity evidence, quality recommendation and reasons.

The vendor CPython 3.9 environment has NumPy 2.0.2 but no SciPy or matplotlib. To avoid changing that environment, the analyzer uses a deterministic NumPy Welch PSD (Hann window, at most 1024 samples per segment, 50% overlap) and emits numerical 0–100 Hz peak/neighborhood summaries. For single-SSVEP sanity it reports 9, 18, 27 and 50 Hz neighborhoods and a defined nearby-background ratio. No plots are generated because matplotlib is unavailable and was not installed.

Quality labels are conservative engineering checks, not medical metrics: a non-finite or constant channel is `invalid`; a finite nonconstant channel with continuity issues or repeated extrema is `degraded`; otherwise it is `usable`. The ND8 ADC range is unknown, so a clipping candidate is never claimed as hardware saturation. Any malformed raw input forces the session-level result to `invalid`.

Synthetic tests cover: all-eight-channel raw reading, a constant channel, a 9-Hz sinusoid, non-finite values, malformed JSON/packet continuity evidence, and deterministic re-analysis. M5 acquisition, timestamp mapping, association and protocol regression tests remain passing. These are software checks only; no real M6 EEG result is recorded here.

## Real-device validation pending

Before M6.1b or decoder work, conduct REST, artifact sanity and center-9-Hz Quest sanity sessions with the external CPython 3.9 SDK environment and preserve data outside Git. Review the machine-readable summaries and raw/gate evidence; do not promote the result to hardware timing, physical optical timing, or a verified hardware sample anchor.
