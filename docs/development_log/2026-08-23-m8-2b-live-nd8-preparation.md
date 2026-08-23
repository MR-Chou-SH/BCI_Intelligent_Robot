# M8.2b Live ND8 Preparation

Date: 2026-08-23

## Prepared entry point

M8.2b adds one future live command at `integration/m8_selection_cli.py --mode live-nd8`. It does not create a new ND8 reader or decoder. The command reuses the external Windows x64 CPython 3.9 Neurodance environment, `Nd8SerialAdapter(COM11)`, M6.7 channel admission and synthetic warm-up, frozen NumPy FBCCA `LiveOnlineController`, the existing 2-Consecutive final-decision policy, and the completed M8.2a Quest snapshot transport.

The fixed engineering-smoke plan is exactly three trials: slot/class 0 at 7.2 Hz, slot/class 1 at 9 Hz, then slot/class 2 at 12 Hz. Expected classes are stored only as post-hoc evidence and are not supplied to the decoder. Each trial performs the console 13-second preparation countdown, then `selection_open` / Quest ACK, then starts the M6 controller at the next software ND8 packet boundary. Only `LiveOnlineController.stop_trial()`'s immutable final record can submit `eeg_selection`.

## Fail-closed and evidence behavior

The command refuses a non-CPython-3.9 / missing-Neurodance runtime before constructing an ND8 adapter. It records a unique session below the external EEG study root and stops on host/dongle failure, channel-admission failure, ND8 callback/stall/continuity/frozen-channel failure, decoder exception, open rejection, no-decision, invalid final label, final transport failure, or Quest rejection. Raw EEG is written once as the session's M6/ND8 evidence; M8 records store only references and derived selection evidence.

No ND8 streaming acquisition, raw EEG recording or formal trial occurred while preparing this entry point. A test initially reached the adapter's host-MAC readiness query under the real vendor interpreter; it timed out and closed before `start_streaming()`, so it is not hardware acceptance and prompted removal of that environment-dependent test. A no-hardware dry run using the verified external runtime created `D:\EEG_Study\m8_2b-dryrun-20260823T092355Z-51174cb2` with `status=dry_run_passed`, `nd8Started=false`, and the three planned unique selection IDs. The runtime import check confirmed CPython 3.9.13 x64 plus `neuro_dance.core` and `neuro_dance.nd_device_process` imports. This does not establish EEG performance, hardware sample timing, physical optical timing, physical end-to-end latency, or robot behavior.

## First real session review and operator-cue follow-up

The first real session was preserved at `D:\EEG_Study\m8_2b-live-20260823T121814Z-4c641f27`. ND8 preflight, five-channel admission, packet continuity, Quest transport/ACK, M7 StableTarget/SSVEP operation, and one-final-decision-per-trial all passed. Trial 1 (slot 0/class 0) and Trial 3 (slot 2/class 2) were valid/correct. During Trial 2 the operator did not realize the next preparation phase had started and did not switch gaze from the prior target in time; its gaze ground truth is therefore unreliable and the trial is marked protocol-invalid. The session is `inconclusive` and is not evidence for a `2/3` decoder accuracy claim; the raw and derived evidence are retained unchanged.

The live runner now provides best-effort Windows standard-library audible cues without changing the frozen M6 decoder or M8 transport. Each trial keeps the approximately 13-second preparation phase, with a preparation cue followed by short cues at 3, 2, and 1 seconds. After Quest `selection_open` ACK and M6 trial start, a distinct start cue plays inside the existing 0.5-second onset guard; the existing 4-second trial deadline and 1.5-second analysis window remain unchanged. A low-pitched end cue plays after the final trial result. Audio failures are warnings only and cannot change selection, no-decision, or abort state.

## Experimental SSVEP display-layout variant — awaiting Quest visual acceptance

Static geometry audit confirmed that the formal M6 scene used three `0.4 × 0.4 × 0.4 m` Unity Cubes (one renderer/material state drives all cube faces), whereas the M8 runtime used one `0.16 × 0.16 m` Quad per slot. Material, `Color.black / Color.white`, alpha and `5/4/3` frame-driven timing remain unchanged.

The M8 diagnostic variant uses a `0.32 m` Quad, a deterministic camera-right separation with a `0.06 m` minimum gap, a `0.24 m` display lift, one non-flashing cyan leader line per slot and a `0.035 m` anchor marker. The line/marker use an independent unlit material and are not passed to the SSVEP controller. The current StableTarget → slot → TargetId authority remains unchanged.

An accepted `selection_open` freezes the currently presented display positions and lines. Final `eeg_selection` and an explicit terminal `selection_abort` release that presentation state; abort also makes a delayed decision terminally rejectable. This compatible lifecycle addition does not send a TargetId from PC or alter class/index resolution. The variant must receive Quest-only visual acceptance before any further M8.2b EEG session, and it must not be described as an accuracy improvement.
