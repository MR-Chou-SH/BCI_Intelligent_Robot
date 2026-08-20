# M6.6a Live-Source Integration Recovery

Commit `120bde3` recorded the first live-source bridge before validation. Its software simulation failed with no stabilized decision and was never hardware-ready.

The exact test failure was a sample-timeline mismatch in the fixture: at 1000 Hz, 0.1 s guard plus 0.1 s window makes the first endpoint sample 200, while the fixed online step makes the second endpoint sample 400. The fixture supplied only 30 × 10 = 300 samples, so only one prediction was causally eligible and 2-Consecutive correctly had no decision. The recovered test supplies 400 samples and produces two identical CENTER predictions, confirming once at prediction index 1.

The audit also found that the replay stabilizer expected a post-hoc ground-truth field. The live controller now owns a ground-truth-free 2-Consecutive state (`lastLabel`, `consecutiveCount`, immutable `decision`). Duplicate trial identity and duplicate/old packet sequence are idempotently rejected. Packet append and state snapshots are locked; decoder compute occurs outside the ingestion lock, and a generation check rejects stale results after stop/reset.

Sample intervals use `[start,end)`. A stimulus association sample `S` schedules `[S+500,S+2000)`, then `[S+700,S+2200)`, with eligibility driven only by buffer sample availability. Timestamp anomalies do not create sample gaps when the continuity record retains sample continuity; true transition/loss resets the rolling buffer.

Software regression passes, but this recovery did not run real hardware. Hardware timing, optical timing, hardware sample anchoring, and true online accuracy remain unverified.
