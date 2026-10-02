# M30 + M31 + M32 Long-Run Plan

## Objective and authorization

Execute `M30_M31_LONGRUN_GOAL_20261003.md` in full, in this order: M30 sequential next-generation semantic Context and frozen EEG transfer study; M31 open-world language bridge; complete M30/M31 verification, selective commits and push; freeze exact checkpoint; only then M32 interactive text-scene Context demo, verification, separate selective commit and push; final handoff. The user explicitly authorized the two experimental branch pushes.

## Baseline

- Repository: `C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`
- Base checkpoint: `codex/m28-m29-semantic-context-bridge` / `e564ca093d48692879853d493135071316e34a52` (already pushed)
- Active branch: `codex/m30-m31-nextgen-semantic-context-vla-bridge`
- Active upstream: none yet; push will establish `origin/codex/m30-m31-nextgen-semantic-context-vla-bridge`
- Origin: `https://github.com/MR-Chou-SH/BCI_Intelligent_Robot.git`
- M28/M29 pushed checkpoint: `e564ca093d48692879853d493135071316e34a52`
- Pre-existing dirty paths: 5 tracked; 4346 untracked. Exact snapshots in `preflight/`; do not stage or alter these.
- Preflight manifest and contract SHA recorded in `preflight/preflight.json`.

## Allowed and prohibited scope

Allowed task-owned paths: `integration/` new/additive semantic modules and focused tests; `research_analysis/m30_nextgen_semantic_context_20261003/attempt-01/`; `research_analysis/m31_open_world_vla_bridge_20261003/attempt-01/`; `research_analysis/m32_interactive_text_context_demo_20261003/attempt-01/`; `docs/agent/m30_m31_longrun_20261003/`. Selectively stage only audited task-owned files.

Prohibited: Quest/ADB/MQDH/ND8/COM11/physical robot/live EEG; raw EEG edits; production M19; real VLA dispatch; changes to M28/M29 historical outputs or M25 frozen inputs; key exposure/persistence; broad add, reset, clean, destructive stash, force push, dependency upgrades.

## Bounded sequence and machine-verifiable gates

1. **Preflight and isolate.** Capture root/branch/HEAD/origin/upstream/status and all dirty paths; create the preferred new branch from current HEAD without disturbing the worktree. Gate: current M28/M29 SHA is the branch base; all pre-existing dirty paths remain.
2. **M30 shared sequential Context.** Freeze independently authored 30–40 episode benchmark (120–180 decision points), model-input/evaluation-only separation and family-based train/dev/held-out split. Implement relational affordances, variable-dimensional q_global, deterministic page projection, eligibility/ambiguity/state checks, C65/C85/C100 gates fitted only on train/dev, and lambda sweep `{0,0.5,1,1.5,2}`. Gate: leakage validators; sequential fake-client tests; measured live held-out calls; full coverage×lambda×EEG-op matrix; reports/13 plots; M28 comparison; preserve unfavorable results. Retries: at most 3 hypothesis-led software repair loops; do not tune on held-out.
3. **M31 open-world bridge.** Implement explicit `SemanticLanguageBridge.generate_instruction`, descriptor parsing and high-level relations with dispatch disabled; strict-scene validation remains optional. Freeze 80–120 independent-label cases before evaluation. Gate: required Chinese/English/state/color/reverse/ambiguous/incompatible examples, fake-client tests, live benchmark and report. No pair lookup as primary behavior.
4. **M30/M31 closeout.** Run focused tests, relevant M27/M28/M29 regressions, compile checks, benchmark/leakage/secret audits and staged diff checks. Commit M30/M31 selectively, push without force, verify remote SHA, record `M30_M31_PRE_M32_CHECKPOINT.md` and commit hash. Do not start M32 until this passes.
5. **M32 parser and interactive demo.** Only after checkpoint: parse natural-language text into structured explicit facts; reuse the frozen M30 engine; implement interactive sequential selection/undo/reset/submit/help with q_global, score, relation, uncertainty and latency display. Freeze 6–10 scenes, 7–8 objects each, at least two trajectories per scene, 60–100 decision points, independent acceptable targets. Gate: parser fidelity, non-hallucination, shrinking normalized q, real history-sensitive ranking changes, ambiguity, selected-object removal, six-family runbook, deterministic and live test evidence; no M30 held-out retuning. Retries: at most 3 hypothesis-led fixes.
6. **M32 closeout.** Run M32 focused tests, M30/M31 regressions, compile, secret scan and diff audit; commit only M32/necessary shared code and reports; push; verify remote SHA. Extend final handoff with M32 evidence and exact checkpoint hashes.

## Runtime, cost, recovery

Use existing `.venv\Scripts\python.exe`; no installs/upgrades. Use `DEEPSEEK_API_KEY` only from environment and never print/log/persist it or headers. Freeze every benchmark before live calls. Limit API retries to one transient retry per failed call and never substitute fabricated results. Keep worklog append-only and state snapshots current after atomic steps.

## Completion

Complete only when M30/M31 reports, tests, selective commits and remote checkpoint are verified; then M32 requirements and separate push are verified; `FINAL_HANDOFF.md` answers every required question and protection statement. PASS/PARTIAL labels must match evidence; no forced coverage, gain or lambda result.
