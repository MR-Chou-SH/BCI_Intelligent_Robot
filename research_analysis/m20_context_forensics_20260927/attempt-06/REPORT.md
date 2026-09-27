# M20 Context forensics and paired historical replay

## Evidence boundary

This is an offline replay of the same historical raw EEG and M6.5b score artifacts. The 89-trial analysis set contains 88 formal-manifest trials plus the B2 `m6_4-trial-023` replay supplement. Formal-only results are recomputed with that supplement removed before fitting each leave-one-session-out fold. No raw EEG or production M19 file is changed.

Context is simulated from predeclared observable history. The target-aligned and wrong-target prior sweeps use the recorded target only to create named offline stress conditions; they do not represent a deployable prior generator.

## Reproduction and source audit

The historical V3 EEG-only and EEG+Context results were recomputed from raw EEG and match the saved 89-trial per-trial artifact exactly. The current M12/M13 paired replay was separately rerun into a new output directory and compared with the preserved paired artifact.

See `v3_baseline_summary.json`, `M12_M13_reproduction_summary.json`, and `context_forensic_audit.json` for exact values and code-path details.

## Controlled sweeps and stopping tests

`fusion_endpoint_summary.csv` compares alpha 0/0.25/0.5/1/1.5 with current (`lambda=0.5`), reduced (`0.75`), and no (`1.0`) uniform softening across causal-history, neutral, target-aligned, and wrong-target priors. `controlled_scenario_summary.csv` reports all four target-aligned strengths (0.60/0.75/0.90/0.95) across all classes, plus the separate Green-history → Red-prior 0.90 example on red-target trials. FBCCA values remain uncalibrated CCA scores throughout.

`context_stop_metrics.csv` reports cross-fitted EEG-only and Context-aware stop policies on the same held-out trials, with decision time converted to effective EEG evidence by subtracting the 0.5 s guard. Paired Context acceleration fields compare directly with the same-trial EEG-only policy; the separate `accelerated_fraction` field remains relative to the fixed 2.4 s endpoint. `controlled_scenario_summary.csv` reports aligned, neutral, conflict, and the requested Green-history → Red-prior 0.90 example. T@95/99/100 mean the earliest evidence time by which that fraction of all trials has completed correctly; an unreachable threshold is null.

The stronger stop candidate requires a unique raw EEG top, a fused top equal to that raw top, a raw EEG top margin of at least 0.15 when Context is active, fused top/margin thresholds, and three consecutive qualifying windows. EEG-only comparison policies retain two consecutive windows. A raw EEG margin at or above the frozen 0.20 threshold bypasses Context fusion. Selected policies are trained on two sessions and evaluated on the held-out session; the process is repeated for 88-formal and 89-all analyses.
`context_stop_sensitivity_summary.csv` separately reports a fixed cross-condition alpha/lambda sweep with those Context stability requirements. The fixed policy is never selected using the evaluated trial; its paired EEG baseline uses the same stop gates with Context disabled.

## Held-out conflict safety

- Conflict-prior wrong early stops: `0`
- Context-caused errors against paired EEG-only policy: `0`
- Strong contradictory EEG windows preserved as raw EEG top: 8232/8232 across fixed nonzero-alpha conflict policies.
- Fixed nonzero-alpha conflict-policy wrong early stops / Context-caused errors: 0 / 0 across 2124 held-out trial evaluations.

## Reproduction

Run `python -m research_analysis.m20_context_forensics --output-dir research_analysis/m20_context_forensics_20260927/attempt-NN` with the repository `.venv`; the script refuses to overwrite an existing output directory.
