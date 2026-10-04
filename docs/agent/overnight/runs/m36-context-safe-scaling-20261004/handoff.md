# M36 Handoff

- Run ID: `m36-context-safe-scaling-20261004`
- Scientific/software analysis: COMPLETE; selective Git checkpoint is the active final step.
- Repository: `C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`
- Branch: `codex/m36-context-safe-scaling`
- Starting HEAD: `41c0f36c83cc0a4effde549f013c41f433dbfcc3`
- Origin: `https://github.com/MR-Chou-SH/BCI_Intelligent_Robot.git`
- Baseline preservation: all 4,355 original status entries remain; zero staged before checkpoint review.
- The exhaustive baseline status/untracked path inventories and repository-profile logs remain local-only because they enumerate unrelated dirty user files. Their full contents were retained unchanged; the compact baseline metadata and aggregate verification result are included.
- M36 final audit: PASS (118 unique EEG trials; 4 raw-file hashes; 11 Python files compiled; 7 verifier modes; 12 SVG figures).
- Repository software-default profile: PASS (15 PASS, 0 FAIL, 0 BLOCKED; CPython 3.9.13 / NumPy 2.0.2).
- Hardware: NOT ATTEMPTED. No COM11, ND8, Quest, ADB/MQDH, live EEG, raw EEG writes, robot, or VLA calls.

## Final research result

The full retrospective analysis used Track 1 (A+B1+B2, 88 trials, five channels) and Track 2 (A+B1+B2+S7, 118 trials, common three channels). No tested gate demonstrates a supported SAFE-95/SAFE-99 cross-group operating point with useful nonzero coverage. The M36 multi-view rule at q=.95 reaches 22.73% / 12.71% coverage, but has 1 / 3 Context-induced wrong early stops and 0/2 / 0/3 intervention precision. The shared q=.50 point has 5.68% / 3.39% observed congruent coverage with no wrong-Context applications, but its incongruent risk denominator is zero; it does not establish safety. The result is retrospective, not fresh held-out or prospective evidence. New independent sessions are required.

Full answers, the primary table, all 12 plots, artifact checks, and hashes for large CSVs omitted from Git are in `research_analysis/m36_context_safe_scaling_20261004/attempt-01/M36_FINAL_REPORT.md` and adjacent artifacts. Large derived tables remain available in the local artifact directory with byte counts and SHA-256 recorded in the report; no raw EEG is in the repository.

## Verification

- M36: run `m36_final_audit.py`; `final_audit.json` records PASS.
- Repository: `scripts/agent/verify.ps1` with `C:\Users\zsh21\.local-tools\neurodance-sdk-venv\Scripts\python.exe`; final summary is under `research_analysis/m36_context_safe_scaling_20261004/attempt-01/repo-verification/verify-20261004T122740649Z-108688/summary.json`.
- The first verifier attempt selected the wrong Python runtime and the second caught mixed line endings in the M36 status edit. Both were investigated and resolved; final repository profile is green.
- The profile intentionally leaves legacy SciPy-dependent EEG suites NOT_ENABLED because SciPy is unavailable and dependency installation is out of scope.

## Git closeout

The M36 goal authorizes selective staging, commit, and push on `codex/m36-context-safe-scaling`. Before staging, compare the live porcelain list to `baseline_status.txt`, confirm all 4,355 entries remain, confirm only M36 paths are staged, and run `git diff --cached --check`. Keep the five original tracked M20 diffs and all unrelated untracked paths untouched. Exclude raw data, caches, `__pycache__`, verification logs from failed attempts, and the large CSVs listed in the report. After push, verify the local HEAD and `git ls-remote origin refs/heads/codex/m36-context-safe-scaling` match; include that commit and hash in the final task completion message.
