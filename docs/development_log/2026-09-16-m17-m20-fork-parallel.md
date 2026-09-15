# M17–M20 Fork-Parallel Preparation

## Scope

The M13 real-human Quest + ND8 result is still pending, so this campaign did
not select a formal next research direction. It prepared direction-agnostic
software that can be applied to the first authorized real session.

## Delivered

- M17 deterministic fork diagnostics for EEG, context, fusion, stopping,
  task, and correction signals, with synthetic A–G validation patterns.
- M18 isolated experimental sandboxes for calibration diagnostics, sparse
  context-weight sensitivity, sparse stopping sensitivity, uncertainty
  features, participant/session configuration storage, correction-event
  contract preparation, and an M11 ContextPrior extension audit.
- M19 idempotent experiment-to-report package with QC, benchmark tables,
  trial/episode/stopping/context-fusion CSVs, fork diagnostics, and sandbox
  output. Empty real-human input is explicit and valid.
- M20 environment audit, canonical commands, M16 resume checks, M19 rerun
  checks, and one-command synthetic dry run.

## Evidence boundary

All campaign outputs are `SYNTHETIC` or `EXPERIMENTAL_SANDBOX`. No Quest,
ND8, COM11, new human EEG, or physical robot was accessed. Production M12/M13
defaults remain `0.5 / 0.70 / 0.20 / 2`.

## Limitations

The known legacy M6 FBCCA check remains optional-SciPy limited. M17 labels are
transparent descriptive flags, not learned diagnoses. No calibration,
parameter selection, statistical claim, or formal post-M13 research method is
created by this log.
