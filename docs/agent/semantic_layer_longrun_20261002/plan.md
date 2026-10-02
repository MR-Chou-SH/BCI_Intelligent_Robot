# M28 + M29 Semantic Layer Long-Run Plan

## Run identity

- Run ID: `semantic_layer_longrun_20261002`
- Owner / authorization: user-authorized execution of `M28_M29_SEMANTIC_CONTEXT_LANGUAGE_BRIDGE_GOAL_20261002.md`
- Created: `2026-10-02T09:40:30Z`
- Status: COMPLETE
- Full specification: `M28_M29_SEMANTIC_CONTEXT_LANGUAGE_BRIDGE_GOAL_20261002.md`

## Objective

Build and benchmark a real scene-derived semantic Context engine; transfer its measured held-out quality to the frozen M25 EEG replay without altering the frozen EEG baseline or safety invariants; refactor M27 into a reusable, standalone Chinese/English noun-to-structured-plan-to-VLA-language CLI. Share one semantic scene/affordance/planner core. Selectively commit M28 and M29 and push only the new experimental branch.

## Repository baseline

- Repository / Git root: `C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`
- Source branch: `codex/m27-semantic-language-planner`
- Source HEAD: `ca8d10ab8fac14bfe75a6be8764e577f9339f766`
- Working branch: `codex/m28-m29-semantic-context-bridge`
- Working HEAD: `ca8d10ab8fac14bfe75a6be8764e577f9339f766`
- Upstream: none on the new branch at start
- Origin: `https://github.com/MR-Chou-SH/BCI_Intelligent_Robot.git`
- Stable Task 1/2 checkpoint: `0e5bf5e42847e3f2580e02dcc0a7fd546f8db138`; verified ancestor of M27.
- M27 checkpoint: `ca8d10ab8fac14bfe75a6be8764e577f9339f766`; verified ancestor of HEAD.
- Before run artifacts: 5 modified tracked paths; 4345 untracked paths. Exact status/path snapshots are under `preflight/`.
- Existing tracked M20 edits and untracked inventory are preserved and excluded from M28/M29 staging.

## Scope and authorization

- Allowed: new `integration/` semantic core, CLI and tests; M28/M29 new output directories; this run's `docs/agent/semantic_layer_longrun_20261002/` records; read-only M25/M23/M24/M27 source/results; existing `.venv` Python; bounded live DeepSeek calls using the already-present environment variable.
- Authorized Git: create this experimental branch, selectively stage only M28/M29 files, create the two goal-specified commits, and push this branch to `origin` without force.
- Excluded: Quest/ADB/MQDH, ND8/COM11, raw EEG changes, Task 2 live acceptance, full BCI/VLA integration, M20 stable branch changes, editing existing M25/M27 historical result directories, printing/persisting `DEEPSEEK_API_KEY`, destructive Git commands, dependency installation/upgrades.
- Frozen: M25 FAST/MEDIUM/CONSERVATIVE parameters, folds, cohorts, stopping logic, gates, and provenance; no target-driven Context threshold selection; M29 free-noun output cannot dispatch.

## Steps and machine-verifiable success criteria

| Step | Atomic result | Validation | Expected result | Retry / timeout |
|---|---|---|---|---|
| 1 | Preflight and new branch recorded | compare `preflight/preflight.json`, current Git root/branch/HEAD and ancestry | correct repo; stable checkpoint is ancestor of M27; new branch starts at M27 HEAD; baseline inventory preserved | one audit; no destructive recovery |
| 2 | Freeze shared semantic schema and M28 benchmark before evaluation | benchmark lint verifies case IDs, disjoint model-input/gold fields, valid object IDs/affordances, current and held-out families, ambiguity/invalid/adversarial strata | all benchmark cases pass schema and no expected answer enters model input | fix schema only before any live calls; then freeze/hash |
| 3 | Implement shared scene core, grounding, Context and planning validation | focused deterministic fake-client/unit tests | input validation, aliases, state/affordance transitions, Context OFF/ambiguity, no hallucinated IDs, bounded retry, fail-closed plans all pass | up to 3 evidence-driven software repair loops |
| 4 | Run held-out live semantic Context benchmark | frozen benchmark runner, result completeness, repeatability and metrics audit | every planned call either valid output or explicit transport/API failure; exact counts/rates, latency, confusion, abstention and held-out strata emitted; no post-hoc case edits | one retry for a transient call; no prompt/label retuning against held-out errors |
| 5 | Transfer measured quality to M25 replay and quality frontier | hash M25 manifest/inputs before/after; validate paired FAST/MEDIUM/CONSERVATIVE trial counts, unchanged baseline identity, no-delay, accuracy and wrong-stop invariants; output/plot audit | Monte Carlo mapping is seeded and independent of EEG evidence; measured semantic quality preserved; all requested gains/risk/frontier/arrival outputs emitted | up to 3 analysis repairs, never edit frozen M25/M23/M24 artifacts |
| 6 | Expose reusable M29 CLI on shared core | CLI one-shot and interactive transcript tests; fake client, M27 regressions | Chinese/English/aliases; strict/free modes; executable/ambiguous/invalid; validator rejects all named violations; plan precedes language | up to 3 evidence-driven fixes |
| 7 | Freeze/run M29 benchmark and repeat subset | benchmark linter, live runner, summary validator | 30–50 fixed cases when API/runtime permits; representative repeated live subset; schema/plan/ambiguity/invalid/grounding/repeatability/latency/retry/token metrics | one transient API retry; no expectation edits after calls |
| 8 | Final audit, commits, push and handoff | targeted tests; secret scan; `git diff --check`; explicit staged-file audit; commit ancestry and remote SHA verification; final requirement matrix | two selective commits on new branch; remote SHA equals local; no unrelated files in either commit; M28/M29 deliverables and boundaries clear | retry push only for transient remote errors; never force |

## Runtime and recovery

- Python runtime: repository `.venv\Scripts\python.exe`; record actual version and library versions in preflight/results.
- API secret: read only from `DEEPSEEK_API_KEY`; never output value or headers; do not save `.env`.
- Input identity: M25 `INPUT_MANIFEST.json` SHA-256 and its frozen feature/operating-point/assignment hashes are in `preflight/preflight.json`.
- Checkpoint cadence: append one worklog JSONL event and update state after each stable atomic step. Worklog stays append-only.
- Last-green verification changes only after the exact corresponding tests/inputs pass.
- On resume, read plan/state/worklog, check branch/HEAD/status and hashes, and continue from the last verified step without resetting or cleaning.

## Completion / blocker conditions

- Complete only after M28 and M29 requirements in the authoritative goal pass their software-verifiable audits, two selective commits are pushed, and `FINAL_HANDOFF.md` exists with exact findings and protection statement.
- A negative finding that semantic Context does not support 0.4–0.5 s is not a blocker; finish the frontier and report it honestly.
- If live DeepSeek is unavailable, complete shared architecture, deterministic tests, offline transfer framework and reports; mark live quality/latency and API results PARTIAL/BLOCKED with evidence rather than fabricating them.
- Do not access physical hardware, raw EEG, Task 2 live acceptance, or full-system integration.
