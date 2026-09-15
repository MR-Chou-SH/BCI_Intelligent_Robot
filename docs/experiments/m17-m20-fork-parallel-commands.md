# M17–M20 Fork-Parallel Software Commands

These commands are software-only. They do not open COM11, connect ND8,
operate Quest, collect human EEG, or operate a physical robot.

Run from `C:\Users\zsh21\Desktop\BCI_Intelligent_Robot` with the project
Python environment selected by the existing setup.

```powershell
# Existing M13.5 software readiness
python -m integration.m13_5_readiness

# M17 synthetic fork diagnostics
python -m integration.m17_fork_diagnostics --output <OUTPUT>\fork-diagnostics.json

# M18 isolated candidate sandboxes
python -m integration.m18_research_sandboxes --output <OUTPUT>\sandbox-summary.json

# M19 M16→M17→M18 report package; empty real-human state is valid
python -m integration.m19_experiment_report --output-dir <OUTPUT>

# M20 environment/recovery/dry-run report
python -m integration.m20_release_readiness `
  --output-dir <OUTPUT> `
  --report <OUTPUT>\m20-readiness.json
```

The M20 command composes the synthetic M14/M15/M16 dry run, M17 diagnostics,
M18 sandboxes, M19 reports, and rerun/idempotence checks. Existing M14/M15/M16
commands remain valid and are not replaced.

## Future real-data use

After an authorized M13.5 real session, append its JSONL path to the M19
command using `--real-session`. The resulting package will retain provenance
and will not manufacture a human row when the path is absent.

## Environment note

The known legacy M6 FBCCA suite remains separately limited by optional SciPy
availability in the historical environment. The M13 NumPy-compatible runtime
path and M17–M20 modules do not install or require SciPy.
