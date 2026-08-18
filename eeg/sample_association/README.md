# M5.3 Quest event → ND8 association

Run only from the externally maintained CPython 3.9 x64 Neurodance SDK environment.
Keep `--data-root` outside this repository; the session records raw EEG separately
from Quest-PC events, clock synchronization, ND8 metadata, gate decisions, and
derived associations.

```powershell
<Python39VendorEnv> -m eeg.sample_association.live_session --com COM11 --data-root D:\EEG_Study\m5_3
```

Wait until the newest `nd8-association-gate.jsonl` record has
`gate.association_ready: true`, then launch the existing M5.2 Quest scene and run
a trial. The gate requires a contiguous, Unix-millisecond SDK timestamp segment
with at least 10 packets spanning at least 1.8 seconds; it rejects pre-sync,
transitions, shape changes, timestamp jumps, and packet-sequence discontinuities.

After stopping a trial, keep ND8 running for at least 3 seconds, stop the Python
process with Ctrl+C, then run:

```powershell
<Python39VendorEnv> -m eeg.sample_association.offline_verify --session D:\EEG_Study\m5_3\<session-directory>
```

`derived-association.jsonl` contains only software-derived packet/sample estimates.
The vendor demo calls the SDK timestamp the first point time, but this project has
not independently verified that anchor or hardware timing. All records therefore
retain `hardwareTimingVerified: false` and `physicalOpticalTimingVerified: false`.
