# Post-M13 Real-Data Decision Package

This document is a decision aid, not a preselected research milestone. It is
used only after an authorized real M13 Quest + ND8 session has produced
human EEG evidence. Until then, M13 real-human evidence remains `PENDING` and
M17/M18 outputs remain synthetic, historical replay, or experimental sandbox
evidence.

## First analysis command

From the repository root, run the M19 pipeline with the completed M13.5 JSONL
session path:

```powershell
python -m integration.m19_experiment_report `
  --output-dir <REPORT_DIR> `
  --real-session <M13_5_SESSION_JSONL>
```

Then inspect `fork-diagnostics.json`, `qc.json`, `benchmark.csv`,
`stopping-table.csv`, `context-fusion-table.csv`, and `sandbox-summary.json`.
The pipeline is descriptive and does not choose parameters.

## Evidence-to-candidate map

| Observed diagnostic | Candidate direction to investigate | Required caution |
|---|---|---|
| `EEG_SHORT_WINDOW_UNSTABLE` | streaming EEG/evidence-quality improvement | keep M13 defaults frozen until a formal decision |
| `EEG_CLASSIFICATION_WEAK` | decoder/evidence-quality investigation | do not infer a decoder failure from one session |
| `CONTEXT_MISLEADING` | context calibration or adaptive context weighting | preserve raw EEG confirmation and user agency |
| `FUSION_DOMINATES_TOO_MUCH` | context-weight/fusion calibration | do not write a sandbox λ back to production |
| `FUSION_TOO_WEAK` | context usefulness and fusion sensitivity | confirm context is actually informative |
| `STOPPING_TOO_CONSERVATIVE` | stopping personalization or policy study | no threshold optimization from one replay |
| `STOPPING_TOO_AGGRESSIVE` | uncertainty-aware stopping and safety review | compare with full-window evidence and corrections |
| `CONTEXT_UNINFORMATIVE` | richer observable context/predictor investigation | M11 semantics remain frozen pending a decision |
| `USER_CORRECTION_NEEDED` | correction/ErrP investigation | no ErrP decoder is implemented here |
| `CALIBRATION_UNCLEAR` | confidence calibration diagnostics | current normalized evidence is not claimed calibrated |

Multiple labels are allowed. `NO_CLEAR_BOTTLENECK` means more trials are
needed, not that the baseline is proven optimal.

## Minimum disambiguating evidence

- repeated trials across active targets and task steps;
- complete raw-winner, fused-winner, margin, context, and stopping trajectories;
- explicit provenance and selection/trial identity;
- shadow-mode comparison before any active dynamic-stop decision;
- recorded user corrections if they occur.

No branch becomes a formal post-M13 research direction until the user/GPT
research decision explicitly selects it.
