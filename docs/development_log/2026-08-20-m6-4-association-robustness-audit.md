# M6.4 Association Robustness Audit and Existing-Session Exploitation

## Scope

This audit used read-only replay of the existing Session A, B1 and B2 raw evidence. No EEG was reacquired and no decoder output was used to select trials.

## Association findings

- B2 trial 023 was a packet-boundary rounding error. The estimated event was about `199.887` samples into packet 1329, which rounded to 200 although the packet contains offsets 0--199. The next packet was sequence-adjacent, continuous, and began at the same timestamp boundary; the event is reassociated to packet 1330, offset 0. This is a bounded half-sample boundary correction, not clamping.
- B2 trial 014's duplicate live association was a concurrency race: packet ingestion and network-event flushing could process the same pending event concurrently. `AssociationCoordinator` now serializes ingestion, event handling and finalization with an `RLock`.
- B1 trial 011 remains invalid because the stop event was older than the frozen five-second accepted-sync freshness limit. This is not converted into a valid trial.

Regression coverage includes exact boundary, just-before-boundary, out-of-tolerance, packet-gap, and concurrent flush cases. The replay artifacts are outside the repository under `D:\EEG_Study\m6_4\replay`.

## Replay and exploratory results

Replay produced A: 30/30 valid, B1: 29/30 valid, and B2: 30/30 valid after correction. B1 and B2 remain exploratory-only because their original formal completeness status is incomplete.

Frozen exploratory decoding used CH2/3/4/5/7, 1000 Hz, 0.5 s onset guard, demean-only epochs, 7.2/9/12 Hz and three harmonics. At 1.5 s, Standard CCA / NumPy FBCCA / legacy-style FBCCA were respectively A 30/30 / 30/30 / 30/30, B1 26/29 / 28/29 / 27/29, and B2 29/30 / 29/30 / 30/30. At 1.0 s they were A 29/30 / 30/30 / 28/30, B1 26/29 / 28/29 / 26/29, and B2 29/30 / 28/30 / 27/30.

These are cross-session exploratory observations, not a formal M6.4 pass. They show a clear session effect and do not justify online or generalized-accuracy claims.

## Evidence boundary and maturity

The sample association remains a software-derived estimate; hardware sample anchoring, physical optical timing, and nominal optical frequencies remain unverified. Existing sessions are sufficient for the present offline robustness analysis, but formal Session B completeness is not retroactively changed.

## Closeout

M6.4 is closed as `Completed / PASS WITH WARNINGS`. The completed object is the association robustness audit and exploratory cross-session evidence: boundary rounding and concurrent flush failure modes were corrected, historical replay was performed without changing formal manifests, and fixed QC-valid subsets were evaluated with frozen decoders. It does not prove formal prospective cross-session generalization, cross-subject generalization, physical timing, hardware-exact timing, or true online performance. M6.5a may implement replay-only pseudo-online decoder infrastructure; it does not begin a real ND8 online experiment.
