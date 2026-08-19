# M6 Development Log — ND8 EEG Online Classification

Date: 2026-08-18
Status: M6.0 audit completed; M6.1a Completed / PASS WITH WARNINGS

## M6.0 audit conclusions

The Drone2.1 audit found a legacy 1000 Hz acquisition path resampled to 250 Hz, a 50 Hz notch, a 0.14 s lag and legacy 8–16 Hz frequency assumptions. The reference contains a filter-bank CCA-style structure, but it assumes timestamp/sample semantics not verified for this project and does not provide the M5 evidence chain. Its class default is a 2.0 s FBCCA window; however, the active `OperationMain.py` invocation passes `Config.winLEN=3`, so the effective active call is 3.0 s at 250 Hz after the 0.14 s lag. This distinction is recorded to avoid treating the legacy reference as production behavior. M5→M6 therefore proceeds through raw evidence, channel sanity and controlled offline data before any decoder. No classifier code was copied or enabled in M6.0/M6.1a.

## M6.1a signal/channel sanity architecture

`eeg.signal_sanity.record` reuses the M5 `Nd8SerialAdapter`, its append-only raw packet and packet-metadata records, the host-MAC readiness lifecycle, continuity timeline, and `PostSyncAssociationGate`. A new external session stores every SDK channel (exactly eight) rather than selecting channels before evidence review.

Each session contains raw packet data, packet metadata, post-sync gate records, a manifest and optional append-only manual annotations. The manifest records mode, requested/actual duration, expected/observed sample count, 1000 Hz configuration, channel schema, free-text electrode note, Git commit, Python runtime, and the persistent timing-evidence boundary.

The modes are `rest` (30 s default), `artifact_sanity` (30 s default), and `single_ssvep_sanity` (15 s default). The latter records only the intended Quest center target at nominal software frequency 9 Hz; it is not a classifier and does not assert physical optical timing.

## Offline analysis and software verification

`eeg.signal_sanity.offline_analyze` reads saved raw JSONL rather than live objects. It writes `analysis/signal-quality-summary.json`, including per-channel finite counts, descriptive statistics, constant/placeholder candidate, repeated-extreme/clipping candidate, continuity evidence, quality recommendation and reasons.

The vendor CPython 3.9 environment has NumPy 2.0.2 but no SciPy or matplotlib. To avoid changing that environment, the analyzer uses a deterministic NumPy Welch PSD (Hann window, at most 1024 samples per segment, 50% overlap) and emits numerical 0–100 Hz peak/neighborhood summaries. For single-SSVEP sanity it reports 9, 18, 27 and 50 Hz neighborhoods and a defined nearby-background ratio. No plots are generated because matplotlib is unavailable and was not installed.

Quality labels are conservative engineering checks, not medical metrics: a non-finite or constant channel is `invalid`; a finite nonconstant channel with continuity issues or repeated extrema is `degraded`; otherwise it is `usable`. The ND8 ADC range is unknown, so a clipping candidate is never claimed as hardware saturation. Any malformed raw input forces the session-level result to `invalid`.

Synthetic tests cover: all-eight-channel raw reading, a constant channel, a 9-Hz sinusoid, non-finite values, malformed JSON/packet continuity evidence, and deterministic re-analysis. M5 acquisition, timestamp mapping, association and protocol regression tests remain passing. These are software checks only; no real M6 EEG result is recorded here.

For the fixed 30-second `artifact_sanity` human-action protocol, the offline summary also reports per-channel standard deviation, RMS and peak-to-peak values for the declared rest/blink/rest/jaw/rest intervals. The intervals are indexed from the first saved raw sample at the nominal 1000 Hz rate; they are descriptive markers, not a hardware trigger, artifact detector or classifier.

For `single_ssvep_sanity`, the recorder can first wait for the M5 post-sync gate, then retain a separate preparation raw/metadata stream during a user-facing countdown. It switches to the formal raw/metadata files only at a recorded formal-start boundary. The offline PSD analyzer reads only those formal files, so Quest wearing and stimulus-app launch movement are excluded from the analysis window. This remains a software evidence boundary, not a physical optical or hardware sample-timing verification.

## M6.1a real-device validation

### REST

Session `m6_1a-signal-sanity-20260819T055452Z-ec3ed511` recorded 149 packets, 29,800 samples/channel and 8×200 packet shape in 30.046 s. The final gate was `association_ready`, segment 2, with no `continuity_lost`, no input errors and one non-severe `timestamp_delta_mismatch`. CH2/3/4/5/7 were usable; CH0/1/6 were constant/placeholder candidates; no channel was degraded and no clipping candidate was reported. Some usable channels showed 50 Hz evidence. The result was `PASS WITH WARNINGS`.

### ARTIFACT_SANITY

Session `m6_1a-signal-sanity-20260819T060234Z-622e1bac` recorded 149 packets and 29,800 samples/channel in 30.079 s. The same five SDK channels remained nonconstant and the same three remained placeholder candidates; gate and continuity were normal with no input errors. Fixed descriptive segments were Rest 1 / Blink / Rest 2 / Jaw / Rest 3. Blink showed higher standard deviation than Rest 1 on CH2, CH4, CH5 and CH7; jaw showed its clearest candidate change on CH5 (74.90 versus final-rest 42.45). The absence of precise action markers limits interpretation to physiological/movement-related variation candidates. Result: `PASS WITH WARNINGS`.

### SINGLE_SSVEP_SANITY

Session `m6_1a-signal-sanity-20260819T061605Z-33c80383` contained 75 preparation packets and 75 formal packets. ND8 was already `association_ready` before the 13 s countdown; the formal window began approximately 13.10 s later and contained 15,000 samples/channel. Preparation and formal raw evidence were separate, and PSD read only formal raw. All five usable channels showed 9 Hz neighborhood evidence candidates (ratios CH2/3/4/5/7: 2.16/2.18/2.26/1.94/2.41) and stronger 18 Hz harmonic candidates (3.61/4.66/3.87/4.32/4.06); 27 Hz evidence was weak. 50 Hz evidence remained present. The user wore Quest 3, started the existing M5 three-target application and viewed the nominal center 9 Hz target, but this mode did not run the Quest-PC trigger server, so application start was not independently timestamped by PC. Result: `PASS WITH WARNINGS`.

## Engineering lessons and research candidates

- Actual electrode count and usable SDK channel count can be inferred from raw behavior without assuming 10–20 channel identity.
- Constant placeholder channels must remain in raw evidence and must not invalidate an otherwise valid multi-channel session.
- Preparation/movement evidence should be physically separated from formal SSVEP analysis evidence.
- Waveform change is weaker evidence than frequency-specific spectral evidence; a single session is not classification performance.
- Raw and derived evidence separation remains essential.

Progressive evidence validation (REST → artifact sanity → frequency-specific stimulation sanity), preparation separation, retention of unusable channels, and uncertainty-aware continuation from M5 are research/paper discussion candidates only; no novelty claim is made.

## M6.1a closeout boundary

M6.1a is `Completed / PASS WITH WARNINGS` for the current hardware setup. It does not validate classification, three-class separability, physical optical timing, hardware timing, ND8 hardware sample anchoring, or exact standardized electrode positions. No new ADR was required for M6.1a closeout.
