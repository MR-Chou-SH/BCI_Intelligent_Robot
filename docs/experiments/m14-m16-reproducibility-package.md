# M14–M16 Reproducibility Package

All commands below run from the repository root with the existing `.venv`.
They are software/simulation commands and do not open Quest, ND8 or COM11.

## Software evidence

```powershell
.\.venv\Scripts\python.exe -m integration.m14_sequential_closed_loop --mode synthetic --evidence-path <run>\m14-synthetic.jsonl --summary-path <run>\m14-synthetic-summary.json
.\.venv\Scripts\python.exe -m integration.m14_sequential_closed_loop --mode mujoco --evidence-path <run>\m14-mujoco.jsonl --summary-path <run>\m14-mujoco-summary.json
.\.venv\Scripts\python.exe -m integration.m15_comparative_benchmark --output-dir <run>\m15
.\.venv\Scripts\python.exe -m integration.m15_historical_replay --input D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json --output-dir <run>\m15-historical
.\.venv\Scripts\python.exe -m integration.m16_reproducibility --output-dir <run>\m16
```

`<run>` is a new output directory. The historical input is read-only. The M16
session layout contains a manifest, deterministic schedule, M14 episode JSONL,
M15 JSON/JSONL/CSV outputs, reused M13.5 analysis and acceptance, a checkpoint,
and a final report.

## Future human session boundary

The final human protocol remains intentionally open. When authorized, use the
existing `docs/experiments/m13.5-live-readiness-operator-package.md` and the
M13 Quest/ND8 procedure. Run baseline smoke first, then shadow, and only enable
active after the engineering checks pass.

⚠️ USER ACTION: attach approved electrodes, operate Quest and ND8, confirm the
COM port and perform the visual/controller steps described by that procedure.

Codex/PC-side commands can create the manifest, logs, analyzer and acceptance
report after the user session. They must record the evidence as
`REAL_HUMAN_EEG`; synthetic and historical replay outputs must remain separate.
