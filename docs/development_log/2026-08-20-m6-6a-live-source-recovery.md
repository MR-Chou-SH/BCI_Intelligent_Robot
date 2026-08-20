# M6.6a Live-Source Integration Recovery

Commit `120bde3` recorded the first live-source bridge before validation. Its software simulation failed with no stabilized decision and was never hardware-ready.

The exact test failure was a sample-timeline mismatch in the fixture: at 1000 Hz, 0.1 s guard plus 0.1 s window makes the first endpoint sample 200, while the fixed online step makes the second endpoint sample 400. The fixture supplied only 30 × 10 = 300 samples, so only one prediction was causally eligible and 2-Consecutive correctly had no decision. The recovered test supplies 400 samples and produces two identical CENTER predictions, confirming once at prediction index 1.

The audit also found that the replay stabilizer expected a post-hoc ground-truth field. The live controller now owns a ground-truth-free 2-Consecutive state (`lastLabel`, `consecutiveCount`, immutable `decision`). Duplicate trial identity and duplicate/old packet sequence are idempotently rejected. Packet append and state snapshots are locked; decoder compute occurs outside the ingestion lock, and a generation check rejects stale results after stop/reset.

Sample intervals use `[start,end)`. A stimulus association sample `S` schedules `[S+500,S+2000)`, then `[S+700,S+2200)`, with eligibility driven only by buffer sample availability. Timestamp anomalies do not create sample gaps when the continuity record retains sample continuity; true transition/loss resets the rolling buffer.

Software regression passes, but this recovery did not run real hardware. Hardware timing, optical timing, hardware sample anchoring, and true online accuracy remain unverified.

## Final software gate

The callback-style controller was compared against M6.5b for all 89 fixed QC-valid historical trials. Window boundaries, prediction labels, candidate scores (absolute tolerance `1e-9`), 2-Consecutive final labels, and decision prediction indices agreed for A 30/30, B1 29/29, and B2 30/30, with zero unexplained mismatch. The decision artifact keeps its immutable at-decision snapshot and now separately exposes the complete stopped-trial prediction timeline.

Additional regression covers duplicate/old packets, continuity-loss invalidation, anomaly-preserved sample continuity, duplicate trial start/final stop semantics, and ground-truth-free live decisions. A diagnostic-live manifest preparer creates the complete evidence-file skeleton and explicit false hardware/optical verification flags without opening a device.

The final two concurrency gates use deterministic `threading.Event` coordination. A decoder blocked outside the ingestion lock can be stopped without deadlock; after release, its stale result is rejected and produces no prediction or decision update. A separate sequential-trial regression confirms that prediction index, last label, consecutive count, association anchor, timeline, and decision are trial-local. The complete historical callback equivalence was rerun after these changes and remains A 30/30, B1 29/29, B2 30/30 with zero mismatch.
