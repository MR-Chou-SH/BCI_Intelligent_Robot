# Frozen Policy Confirmation

The replay did not tune or modify policy parameters. Values below were read
from `STAGE2_SOFTWARE_FREEZE.json` and checked against the current M12/M13
implementation before replay.

```json
{
  "freezeGate": {
    "status": "READY",
    "meaning": "The offline software/replay boundary is frozen for a new-human validation run. This is not a claim of generalized human performance or production readiness.",
    "productionIntegration": "OFF",
    "humanValidation": "REQUIRED",
    "hardwareValidation": "NOT_ATTEMPTED"
  },
  "samplingRateHz": 1000,
  "selectedChannelsZeroBased": [
    2,
    3,
    4,
    5,
    7
  ],
  "frequencyMappingHz": {
    "slot0": 7.2,
    "slot1": 9.0,
    "slot2": 12.0
  },
  "harmonicCount": 3,
  "windowSeconds": 1.5,
  "onsetGuardSeconds": 0.5,
  "effectiveAcquisitionSeconds": 4.0,
  "scoreThreshold": 0.7,
  "marginThreshold": 0.2,
  "requiredConsecutive": 2,
  "contextRule": "weak evidence may be assisted only at the frozen integration seam; strong EEG overrides contradictory context; context-only execution is forbidden",
  "idleRule": "no command without EEG evidence; no context-triggered IDLE to CONTROL transition"
}
```
