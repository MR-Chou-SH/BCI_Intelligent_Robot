# 2026-10-04 — M34 High-Speed SSVEP and Context Stopping

M34 completed a session-aware, software-only analysis of 118 usable historical EEG trials: A (train, 30), B1 (dev, 29), B2 (primary heldout, 29), and S7 (separate common-channel stress heldout, 30). Raw EEG stayed read-only at its original `D:\EEG_Study` paths. Fresh SHA-256 checks match the frozen manifest and official before/after heldout records.

The existing NumPy FBCCA baseline reproduced the recorded A/B1 1.5 s results exactly. A bounded A/B1 search evaluated FBCCA, ensemble TRCA, and TDCA over nine evidence windows; the predeclared B1 rule selected three-band FBCCA on channels `[2,3,4,5,7]`, H=3, with a 0.50 s nominal onset guard. The DEV-only dynamic policy requires 0.20 s minimum evidence, 0.175 relative margin, and two stable top updates. B2 and S7 were evaluated once after freeze; no retuning or retry followed.

On B2, selected FBCCA reached 89.7% at 0.40 s and 93.1% at 0.60 s. On S7, it reached 90.0% at 0.40/0.50 s and 93.3% at 0.80 s. Neither heldout reached 95%. Dynamic stopping achieved 26/29 (89.7%) on B2 and 28/30 (93.3%) on S7. The Context integration was a 100-seed historical transfer simulation, not prospective paired collection. It shifted 266/5,900 seeded pairings from >0.30 s to ≤0.30 s under precomputed Context, but produced 28 wrong early-stop pairings (23 Context-caused); measured API-latency sensitivity produced zero applications and exact EEG-only fallback. Therefore Context acceleration is not safe to claim or deploy.

The frozen heldout cannot be reused for software parameter selection without invalidating the evaluation. Further evidence requires a prospective dataset with independent sessions/participants, actual preceding-selection Context histories, and physical timing measurement. M19 remains the active operational milestone; this research analysis does not change its Quest/ND8 boundary.

Artifacts, plots, code, full report, and 27-check machine audit are in [M34 attempt-01](../../research_analysis/m34_high_speed_ssvep_context_20261004/attempt-01/M34_FINAL_REPORT.md). The report distinguishes EEG evidence duration, nominal logged-onset-relative time, and computation time and lists remaining scientific limitations.

Verification: all M34 Python scripts compiled with Python 3.12.14; the read-only artifact audit passed 29 checks, including fresh source hashes. No full repository test suite, Quest, ADB, ND8, COM11, live EEG, robot, or MuJoCo run was performed.
