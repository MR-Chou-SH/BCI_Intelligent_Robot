# M10.1 — Formal Benchmark Acceptance & Semantics Freeze

Date: 2026-09-15
Status: **M10.1 FORMAL SOFTWARE BENCHMARK = PASS**.

This change promotes the existing M10 Prep state machine to a reproducible formal software acceptance layer. The underlying transition semantics were not changed. The runner now reports the task's `episodeOutcome` separately from the formal acceptance layer's `benchmarkCaseStatus`:

- `completed`: all task steps were accepted;
- `valid_incomplete`: the supplied sequence is a valid prefix but the task is not complete;
- `invalid`: an unknown or wrong-order selection made the episode terminally invalid.

The fixture-driven entry point is `integration/m10_benchmark_acceptance.py`. It reads `integration/fixtures/m10/task_definitions.json`, executes all seven fixture cases, derives shared prefixes from the three frozen task definitions, and writes append-style JSONL evidence. It does not add a graph engine, prediction, probability, EEG fusion, robot execution, or hardware behavior.

## Formal result

- Summary: **7/7 benchmark cases PASS**.
- Canonical House, Tower, and Bridge: `completed`.
- Valid partial House prefix: case `PASS`, episode `valid_incomplete`.
- Wrong-order and unknown-ID: expected negative cases `PASS`, episode `invalid`.
- Post-completion selection: case `PASS`, rejection `task_already_completed`, episode remains `completed`.
- Shared-prefix evidence: **2/2 PASS**.
  - `block_sim_01`: House/Bridge → `block_sim_02`; Tower → `block_sim_03`.
  - `block_sim_01 → block_sim_02`: House → `block_sim_03`; Bridge → `block_sim_04`.

## Verification

Runtime: repository `.venv\Scripts\python.exe` (CPython 3.12.14), standard library only.

```powershell
.\.venv\Scripts\python.exe -B -m unittest integration.test_m10_task_benchmark -v
.\.venv\Scripts\python.exe -B -m integration.m10_benchmark_acceptance --evidence-path docs/agent/overnight/runs/m10-1-formal-benchmark-20260915T090343Z/evidence/episodes-final.jsonl
```

Results: **12/12 tests PASS**, formal acceptance exit code `0`, summary `overallStatus=PASS`.

The evidence file contains nine deterministic JSONL records: seven `episodeEvidence` records and two `branchingEvidence` records. No `obj_N` simulator identifiers appear in the public summary or evidence. No Unity/Quest, EEG/ND8, or physical robot operation was performed. M11 has not started.
