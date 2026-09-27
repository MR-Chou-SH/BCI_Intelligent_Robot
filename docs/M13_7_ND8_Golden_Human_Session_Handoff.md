# M13.7 ND8 Golden Human Session Handoff

## Starting boundary

M13.6 Quest visual acceptance is sealed. Do not continue Quest visual polish
before the next research decision. M13.6 PASS does not mean that ND8 or live
human SSVEP acceptance has passed.

## Hardware and current pipeline

- Neurodance ND8
- 1000 Hz
- 8 channels
- live `COM11`
- Meta Quest 3
- current M13 active pipeline

The engineering admission rule remains at least **3/5 candidate occipital
channels quality PASS**. This is an engineering gate, not a claim that three
channels are physiologically sufficient or equivalent to the full montage.

## Next clean human session boundary

Before the next clean post-hotfix session, keep the following software
foundations green:

1. Human audio cue system.
2. `HumanSessionRecorder`.
3. `RecordedND8Replay`.
4. Synthetic and historical end-to-end dry runs.

The earlier partial human session is not resumed or overwritten. It stopped at
the pre-hotfix silence boundary on trial 069 and remains available for
read-only QA only. The next human session must use a new session directory.

## Human audio cue system

The user cannot read the PC while wearing Quest. The trial orchestrator must
provide the cue automatically:

- one short beep: slot 0 / 7.2 Hz;
- two short beeps: slot 1 / 9 Hz;
- three short beeps: slot 2 / 12 Hz.

The first cue contract is:

```text
TARGET_CUE
  -> silent preparation
  -> READY_CUE
  -> at least approximately 1 s silence
  -> STIMULUS_ONSET
  -> silent EEG analysis
  -> STIMULUS_OFFSET / decision
  -> END_CUE
```

The orchestrator must bind every cue to `trialId`, record actual cue
timestamps, and record stimulus onset/offset. No audio may play during the EEG
analysis window. A future Quest-headphone backend may replace the PC audio
backend without changing the cue contract or slot/class mapping. Run a cue
rehearsal before formal acquisition so the user learns the 1/2/3 mapping.

The deterministic rehearsal remains available with:

    & .venv\Scripts\python.exe -B -m integration.m13_7_golden_session rehearsal --output <run>\audio-rehearsal.json

The manual PC-speaker checkpoint is explicit:

    & .venv\Scripts\python.exe -B -m integration.m13_7_golden_session rehearsal --audible --output <run>\audible-audio-rehearsal.json

It uses `PcToneAudioBackend` and real wall-clock sleep, and covers all three
target-beep counts plus READY, one-second silence, a representative silent
analysis interval and END. The current audibility refinement is 880 Hz / 150
ms target beeps with 280 ms between beeps and 700 ms group separation, 1760
Hz / 500 ms READY, and 440 Hz / 600 ms END. This is a human-hearing
checkpoint, not an automated acoustic comfort or physical timing claim.

## Golden-session data contract

The session should preserve, without overwriting raw data:

- continuous raw ND8 EEG, original sampling rate and all channels;
- sample indices and timestamps;
- channel-quality records;
- trial-event JSONL;
- Quest events/logs;
- M13 decisions and per-window evidence;
- context prior and fusion evidence;
- MuJoCo action/telemetry associations;
- manifest, protocol version and Git commit.

Timing boundary: `STIMULUS_ONSET` is EEG t=0. The live recorder preserves the
continuous raw ND8 stream through preparation and READY; the first packet
received at or after the recorded onset is stored as `TRIAL_SAMPLE_ANCHOR`,
with the onset event sequence/time and cumulative sample index. FBCCA consumes
the frozen 0.5 s guard plus existing 1.5 s window relative to that anchor,
inside the unchanged 4.0 s stimulus/analysis interval. Neither TARGET/READY
timestamps nor a fixed preparation offset may be used to derive the sample
index. Recorded replay uses the same anchor-aware pipeline. The manifest keeps
the formal protocol timing even when synthetic preflight uses its explicitly
marked `compressed_preflight` execution timing.

The current formal protocol preparation interval is 6.0 s (the prior 13.0 s
interval is retained only as a regression-test comparison); the projected
Golden v1 duration is 44.20 minutes / 2651.76 s. This preparation value is the
single protocol timing authority consumed by the scheduler, event-spacing
checks, manifest timing and duration estimator. It is never used as an EEG
sample offset.

The formal Golden recording uses PC audio with the existing persistent Quest
visual stimulus lifecycle. A Quest onset ACK is not part of the Golden onset
contract, so `quest.mode=disabled` may be expected while
`physicalOpticalTimingVerified=false` remains explicit. The optional M8 TCP
Quest integrated preflight is separate and validates transport/controller
lifecycle only.

`LiveND8Source(COM11)` and `RecordedND8Replay(session)` must feed the same
decoder downstream interface. The first human session should be reusable for
future filter, decoder, window, stopping, fusion, M15/M17 and M13-to-M14
analysis changes instead of requiring a new wearer for every software idea.

## Data categories to prepare

Do not freeze final trial counts in this handoff. Prepare recording support for:

- channel quality and rest baseline;
- static SSVEP calibration;
- independent M13 Active trials;
- context-aligned, context-neutral and context-conflict trials;
- no-intent/rejection trials;
- sequential House/Tower/Bridge closed-loop trials;
- realistic artifact trials;
- optional ErrP pilot.

Final counts and ordering require the next experiment decision.

## NEW GPT/CODEX SESSION ENTRY

- Branch: `feature/m9-virtual-manipulation`
- Final commit: the M13.6 closeout commit recorded by `git rev-parse HEAD`
- Milestone tag: `m13.6-quest-visual-pass`
- M13.6 Quest Visual Acceptance: **PASS**
- Next first task: **low-risk runtime check, Quest integrated synthetic preflight, then the new full Golden live launcher**
- **DO NOT START THE GOLDEN HUMAN SESSION IN A SOFTWARE-ONLY RUN**

The M13.7 live launcher is now implemented in
`integration/m13_7_live_launcher.py`. It reads the frozen 147-segment protocol,
records raw packets before decoder delivery, freezes provenance, and supports
PREFLIGHT, explicit LIVE HUMAN confirmation, block-boundary resume and partial
session preservation. Confirmed LIVE HUMAN selects `PcToneAudioBackend` and
fails fast if PC audio is unavailable; it cannot silently select
`NullLoggingAudioBackend`. The no-ND8 audible preflight is:

    & .venv\Scripts\python.exe -B -m integration.m13_7_live_launcher preflight --source synthetic --audio pc --protocol docs\protocols\M13_7_Golden_Protocol_v1.json --data-root <external-preflight-root>

It uses synthetic EEG and does not open COM11. Use `--audio silent` for the
speaker-independent automated preflight. The first live command remains gated
by the documented external-runtime, audio, Quest and 3/5 channel-quality
preflight; this handoff does not authorize launching COM11 in a software-only
run.

The final no-ND8 Quest integrated preflight is explicit:

    & .venv\Scripts\python.exe -B -m integration.m13_7_live_launcher preflight --source synthetic --audio pc --quest m8-tcp --quest-host 0.0.0.0 --quest-port 11001 --protocol docs\protocols\M13_7_Golden_Protocol_v1.json --data-root <external-preflight-root>

This variant selects one `m13_active` trial for each slot and reuses the M9
Quest `BciSelectionTransportClient` / M8 selection lifecycle. It requires
`M9_VIRTUAL scene_ready`, `M8_SELECTION transport initialized`, and a Quest
`selection_ack` for each `selection_open` and terminal `eeg_selection`. The
manifest and summary must report `questOperated=true`, `mode=m8_selection_tcp`,
and at least six accepted ACKs. It still uses synthetic EEG and never opens
COM11 or ND8.

## Overnight historical human-EEG software readiness

The complete read-only M6.1b session at
`D:\EEG_Study\m6_1b\m6_1b-dataset-20260819T090215Z-d70eaa0d` was replayed through
the M13.7 source boundary. Its verbatim raw values reached the recorder,
sample-anchor/pipeline, NumPy FBCCA, existing M13.5 runtime, M8 selection seam,
RecordedND8Replay and verifier. The result was 30/30 trials, verifier PASS,
replay first-window agreement 30/30, and all actual 5-channel fixed-window
results PASS; no COM11, ND8, Quest or new human EEG was operated.

Run a future finalized session through the read-only QA package with:

    & .venv\Scripts\python.exe -B -m integration.m13_7_golden_qa --session <session-root> --output-dir <session-root>\qa

Run the low-risk runtime/audio prerequisite before live work with the SDK
environment:

    & .venv\Scripts\python.exe -B -m integration.m13_7_operator_preflight --protocol docs\protocols\M13_7_Golden_Protocol_v1.json --python-executable C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe --com COM11 --output <external-preflight-root>\operator-preflight.json

The operator preflight does not open COM11 or connect to Quest. Real hardware
acceptance remains a separate manual checkpoint, including the 3/5 channel
quality gate and the explicit Quest integrated preflight above.
