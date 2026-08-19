# M6.4 — Acquisition Recovery Before Session B Reacquisition

Date: 2026-08-20
Status: Recovery in progress; M6.4 remains incomplete

## Failed Session B evidence

The first M6.4 Session B candidate was
`m6_4-dataset-20260819T155147Z-386f95dd`.  It used the frozen 30-trial plan
with LEFT/CENTER/RIGHT = 10/10/10.  Quest evidence contains 30 software
stimulus starts and 30 corresponding stops; raw ND8 evidence contains 2022
packets with no packet-sequence gap.  Dataset completeness was nevertheless
29/30 and `classificationPerformed=false`; no decoder evaluation was run.

This failed session is retained as audit evidence and is not eligible for
cross-session decoding.  Its raw EEG, event, association, and ground-truth
files are not modified by this recovery work.

The initial acquisition process was stopped after all 30 stimulus pairs had
been observed, leaving its manifest status as `acquiring`.  Before this
recovery task, that manifest status was corrected to `incomplete` with the
verifier failure recorded; this recovery does not further modify the failed
session.

## Root-cause distinction

At packet 51, the ND8 SDK timestamp changed from startup-relative milliseconds
to Unix milliseconds.  The pre-recovery gate classified the resulting
`timestamp_delta_mismatch` and `timestamp_jump` as `continuity_lost`, although
packet sequence and packet shape were continuous and the subsequent 1961
packets reached stable `association_ready`.  M6.1b represented the analogous
domain boundary as `transition`; this was a gate semantic inconsistency.

Separately, the `m6_4-trial-011` stop event was rejected because its Quest-PC
clock snapshot was about 5.19 seconds after the most recent accepted sync
sample, exceeding the fixed 5.0-second freshness gate.  This rejection is
retained as correct.  The threshold, association math, decoder configuration,
and Quest protocol were not changed.

PC synchronization evidence rules out a connection drop and confirms that the
Quest runtime continued to emit samples at roughly the configured two-second
cadence.  Samples 175, 177 and 178 reached the PC but had RTTs of about 322,
423 and 282 ms, respectively, so they were rejected by the existing 250 ms
acceptance rule.  The last accepted sample was 176; trial 011 stopped before
sample 179, the next accepted sample, arrived.  The evidence does not separate
Quest/runtime scheduling delay from an asymmetric network delay, so the cause
below that RTT measurement layer remains unknown.

## Recovery fix

`PostSyncAssociationGate` now recognizes only the explicit transition from a
previous non-Unix timestamp to a Unix-ms timestamp when the only continuity
diagnostics are the expected timestamp mismatch and jump.  It records a
`transition` decision while preserving the raw timeline diagnostics.  Packet
gaps, shape changes, timestamp regression, and Unix-to-Unix jumps remain
`continuity_lost`.

Regression tests cover the recognized transition and two true-loss cases.
After tests pass, a new Session B candidate must receive a new session ID and
use the unchanged M6.4 protocol.  Reacquisition is not started by this log.
