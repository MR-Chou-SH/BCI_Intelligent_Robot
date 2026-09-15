# M14–M16 Assumption-Jump Campaign

## Scope

This campaign composed the existing M10 sequential task state, M11 observable
history prior, M12 fusion, M13 stopping policy, M8 final-decision lifecycle and
M9 FR3/UMI MuJoCo execution. It then added a paired A/B/C descriptive benchmark
and a small manifest/schedule/session reproducibility layer. No new research
algorithm was introduced.

## Results

### M14 — Full sequential shared-autonomy closed loop

`integration/m14_sequential_closed_loop.py` runs each step as:

`M11 history → frozen 3-slot M12 evidence → M13 decision → M8 final decision → frozen M9 batch → MuJoCo execution → M10 commit`.

House, Tower and Bridge each passed four M13 early decisions, four dispatches,
four successful MuJoCo `place_ok` executions and four M10 commits. Negative
cases passed for no-decision suppression, wrong-target prefix preservation,
robot failure without commit, duplicate final-decision suppression and
deterministic replay. Public records preserve the frozen mapping boundary and
contain no simulator-internal object identity.

### M15 — Comparative benchmark

`integration/m15_comparative_benchmark.py` produces paired A/B/C JSON, JSONL and
CSV outputs from 12 synthetic step trials. The conditions are EEG full-window,
M12 full-window and M13 dynamic stopping. The synthetic C condition produced 6
early stops and 6 full-window fallbacks; its agency check recorded zero
context-only early stops.

The registered historical fixture
`D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json` was also replayed
read-only through all three condition semantics. It contains 89 recorded M6.5b
trials; the historical C condition produced 20 early stops, 69 fallbacks and 0
no-decisions. Context remained a deterministic overlay and was not derived from
ground truth. These are descriptive replay metrics, not human context-aware
experiment results.

### M16 — Reproducibility infrastructure

`integration/m16_reproducibility.py` creates a pseudonymous manifest, a seeded
development schedule, stable session directories, M14 episode JSONL, M15
condition outputs, reused M13.5 analyzer/acceptance outputs and a checkpoint.
The dry run passes, and a second invocation leaves completed artifacts
unchanged, proving the bounded resume behavior. The final human protocol is not
frozen by this tooling.

## Verification

- M14 focused tests: 3/3 PASS; synthetic acceptance PASS; MuJoCo positive
  acceptance PASS (3 episodes, 12 `place_ok` executions).
- M15 focused tests: 3/3 PASS; historical replay test: 1/1 PASS.
- M16 focused tests: 3/3 PASS; dry-run report: PASS.
- Existing M13.5 streaming/analyzer/acceptance composition in M16: PASS.
- Historical M13 replay: 89/89 trial records processed, PASS.

The repository `.venv` was used. No dependency was installed or upgraded. The
known legacy M6 SciPy limitation remains documented: the legacy FBCCA path
imports optional `scipy`, while the production NumPy seam used here does not.

## Evidence boundary

Quest operated = NO; ND8/COM11 operated = NO; new human EEG collected = NO;
physical robot operated = NO. MuJoCo execution is simulation only. The M13
real-human SSVEP evidence remains `PENDING`; downstream composition used the
explicit development-only `ASSUMED_PASS` label.

## Endpoint

M14 `SOFTWARE / MUJOCO / REPLAY PASS`, M15 `SOFTWARE / REPLAY PASS`, and M16
`SOFTWARE READY`. The next choice is the first unavoidable research fork and
must be informed by real human M13 observations; no new research direction was
selected automatically.
