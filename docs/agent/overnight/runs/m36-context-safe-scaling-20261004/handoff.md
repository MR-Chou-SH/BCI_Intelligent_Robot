# M36 Handoff

- Run ID: `m36-context-safe-scaling-20261004`
- Scientific/software analysis and authorized Git checkpoint: COMPLETE.
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

The M36 artifact checkpoint is `fc9fd9ebf59ad6367a9d3075a6ad283f65640324` on `codex/m36-context-safe-scaling`; it was pushed to origin without force, and `git ls-remote` matched the local HEAD. The 77-path allowlist contained M36 source, report, audit outputs, compact tables and 12 figures; staged whitespace validation passed and no staged file exceeded 1 MB. The final closeout commit hash is supplied in the task completion record.

All 4,355 baseline status entries remain present. The five pre-existing tracked user changes remain unstaged. Large derived CSVs listed with byte counts and SHA-256 in the report, the three repository-profile summary snapshots, exhaustive baseline path inventories, verification logs, and Python caches remain local and were not added to Git. Raw EEG was never staged.
