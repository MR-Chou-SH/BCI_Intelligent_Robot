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

## Do not request ND8 yet

Before asking the user to wear ND8, complete and dry-run:

1. Human audio cue system.
2. `HumanSessionRecorder`.
3. `RecordedND8Replay`.
4. Synthetic and historical end-to-end dry runs.

The user should not be asked to wear ND8 until these foundations can record,
replay and analyze a session without manual reconstruction.

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
- Next first task: **Audio Cue Infrastructure**
- **DO NOT ASK USER TO WEAR ND8 YET**
