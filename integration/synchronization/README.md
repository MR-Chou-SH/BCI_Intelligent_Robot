# M5.2 Quest-PC synchronization baseline

This module is the PC-side endpoint for the M5.2 engineering baseline. It uses a
persistent TCP connection carrying newline-delimited UTF-8 JSON. It records the
original Quest event unchanged inside each PC record, adds PC receive timestamps,
returns an explicit ACK, and exchanges four-timestamp clock samples.

Run from the repository root:

```powershell
python -m integration.synchronization.trigger_server --host 0.0.0.0 --port 11000
```

Run local tests:

```powershell
python -m unittest discover integration/synchronization/tests -v
```

Runtime output defaults to `integration/synchronization/runtime_logs/`, which is
ignored by Git. `offsetPcMinusQuestSeconds` uses the sign convention
`PC monotonic time - Quest monotonic time`; therefore an estimated PC-domain time
is `quest time + offset`. These software clocks are not physical optical timing.
