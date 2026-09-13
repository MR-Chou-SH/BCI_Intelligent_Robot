# Overnight Agent Policy v1

## Purpose and file roles

This policy supports bounded, long-running software work that can be recovered after context loss. It is a manual workflow and file format; v1 does not add a supervisor, loop runner, dependency, Ralph, or multi-agent orchestration.

- Copy PLAN_TEMPLATE.md for one run and record its scope and machine-verifiable success criteria before implementation.
- Create one run-specific state file from STATE_TEMPLATE.json. It is the mutable recovery snapshot, not a project status report.
- Create one run-specific worklog from WORKLOG_TEMPLATE.jsonl. Append one JSON object per event; never edit or rewrite earlier records.
- Prepare the morning report from HANDOFF_TEMPLATE.md.
- By default, place the four run files at docs/agent/overnight/runs/<run-id>/ as plan.md, state.json, worklog.jsonl, and handoff.md. Create that folder only when a concrete run starts; do not treat these templates as an active run.
- Keep these per-run artifacts separate from docs/status/PROJECT_STATUS.md. Update PROJECT_STATUS.md only when a confirmed project milestone or priority changes.
- Do not commit per-run logs or generated artifacts by default. The run plan must specify any Git checkpoint/commit authorization.

## Before work starts

Record the absolute repository path, Git root, branch, HEAD, upstream when available, and working-tree state. Confirm they match the approved plan. If the baseline differs, or unrelated user changes appear, stop before editing and record BLOCKED; never reset, clean, or overwrite changes to make the snapshot match.

Every run must state one objective, allowed paths and actions, exclusions, a bounded sequence of steps, a machine-verifiable success criterion for each step, the validation command and expected result, runtime assumptions, timeout/retry limits, and any actions requiring explicit authorization.

## Execution and verification

Prefer software tasks that can complete the loop: implement → automatically verify → repair → reverify. Use the verified local runtime and named inputs from the plan. Do not install or upgrade dependencies as an implicit workaround.

After each stable atomic result, append a worklog event and update the state snapshot with the completed step, changed paths, exact verification command, exit code, and last green verification. A green result applies only to the exact code and inputs recorded with it.

Retry a failure only when the next attempt tests a specific new hypothesis or uses new evidence. The plan must bound attempts and time. If retries add no evidence, record BLOCKED and stop that item. Continue independent plan steps only when doing so cannot depend on or overwrite the blocked work.

## Safety and blockers

- Do not push. Do not automatically reset, clean, delete, or overwrite user data. Never use bulk deletion or cleanup as recovery.
- Do not operate a real Quest, ND8, or physical robot unless the individual plan explicitly authorizes that exact hardware action and its session boundary.
- Do not claim hardware PASS from code, simulator, or log inspection. Report PASS, BLOCKED, NOT ATTEMPTED, and NEEDS HUMAN VALIDATION distinctly.
- Treat GUI observation, real-device acceptance, missing credentials/dependencies, approval prompts, changed Git baselines, user-data conflicts, and major design choices as blockers. Record the evidence and wait for GPT/user direction where required.
- Do not broaden scope because time remains. Do not reopen GPT/user-frozen research direction, architecture, or interfaces based on preference. Escalate only when repository evidence shows the frozen route is infeasible.
- Resolve ordinary local implementation details independently. Escalate changes to research goals, architecture, or frozen interfaces.

If an approval or user decision is required, persist the current state and stop dependent steps. Never simulate approval or repeat a blocked action.

## Checkpoint and recovery

The state snapshot records run status, baseline, current step, last green verification, most recent checkpoint, changed paths, and blocker. Checkpoint at each stable atomic result; a Git commit is optional and must follow the plan's authorization.

On resume after interruption or compaction:

1. Read this policy, the run plan, the latest state snapshot, and the append-only worklog.
2. Recheck repository path, Git root, branch, HEAD, and working-tree state.
3. Compare the live state with the saved baseline/checkpoint. If unexplained drift exists, record BLOCKED and do not repair it with reset/clean.
4. Resume from the last verified step. Rerun a verification only when the source/input fingerprint changed or the prior result is missing/ambiguous.

Worklog events use monotonically increasing sequence numbers and UTC timestamps. Record run start, plan changes authorized by the user, step start/completion, command and exit status, verification result, checkpoint, blocker, recovery, and final handoff. Keep secrets and unnecessary raw research data out of the log; reference controlled paths instead.

## Morning handoff

Complete HANDOFF_TEMPLATE.md with the final run status, actual changed paths, verification evidence, last green checkpoint, unresolved blockers, and next safe action. Include human validation separately from automated PASS. Do not commit or push unless explicitly authorized by the plan or a later user instruction.
