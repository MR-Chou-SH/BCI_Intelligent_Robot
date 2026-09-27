# Provenance

## Evidence boundary

- EEG: real human EEG-derived historical M6.5b NumPy FBCCA score trajectories.
- Evaluation: offline/pseudo-online replay only.
- Context: simulated causal observable-history M11 prior, using the same
  predeclared history cycle as the existing current-policy replay.
- No true current target, future EEG, future selection, or future Context was
  passed to the Context generator.

## Inputs and code

```json
{
  "input_path": "D:\\EEG_Study\\m6_4\\replay\\m6_5b-continuous-results.json",
  "input_sha256": "99e47ddcd15216feeef33d0212c3730e8b14980fa8c711f4a8e6995a5e4afbf3",
  "freeze_path": "C:\\Users\\zsh21\\Desktop\\BCI_Intelligent_Robot\\artifacts\\m15_stage2\\STAGE2_SOFTWARE_FREEZE.json",
  "freeze_sha256": "d89713c2361a4e4c74bfabc76093efe4f0cfd08af5a6ef92257408fce1cf7b21",
  "freeze": {
    "recordType": "m15_stage2_software_freeze",
    "schemaVersion": 1,
    "createdAtUtc": "2026-09-20T18:38:12Z",
    "status": "READY_FOR_NEW_HUMAN_EEG_VALIDATION",
    "freezeGate": {
      "status": "READY",
      "meaning": "The offline software/replay boundary is frozen for a new-human validation run. This is not a claim of generalized human performance or production readiness.",
      "productionIntegration": "OFF",
      "humanValidation": "REQUIRED",
      "hardwareValidation": "NOT_ATTEMPTED"
    },
    "repository": {
      "path": "C:\\Users\\zsh21\\Desktop\\BCI_Intelligent_Robot",
      "branch": "feature/m9-virtual-manipulation",
      "headAtFreeze": "ea497b26587a877605874d8109d809ae17c50d61",
      "workingTreeAtFreeze": "dirty with pre-existing user changes; no unrelated files modified by this freeze"
    },
    "decoder": {
      "inputSamplingRateHz": 1000,
      "resampling": "none",
      "preprocessing": "per-channel demean only; raw EEG is preserved and never overwritten",
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
      "implementation": "eeg.decoder.fbcca.predict_fbcca",
      "filterImplementation": "numpy_rfft_raised_cosine_zero_phase_with_reflection_padding",
      "filterBands": [
        {
          "stopLowHz": 4.0,
          "passLowHz": 6.0,
          "passHighHz": 90.0,
          "stopHighHz": 100.0
        },
        {
          "stopLowHz": 10.0,
          "passLowHz": 14.0,
          "passHighHz": 90.0,
          "stopHighHz": 100.0
        },
        {
          "stopLowHz": 16.0,
          "passLowHz": 22.0,
          "passHighHz": 90.0,
          "stopHighHz": 100.0
        }
      ],
      "weighting": "b^-1.25 + 0.25 for sub-band b=1..3",
      "scoreDefinition": "fused raw FBCCA CCA-score vector; top1=max(score), margin=top1-top2, confidence=top1/sum(scores); scores are not calibrated probabilities"
    },
    "frozenStage2Policy": {
      "mode": "dynamic_sync",
      "windowSeconds": 1.5,
      "onsetGuardSeconds": 0.5,
      "effectiveAcquisitionSeconds": 4.0,
      "scoreThreshold": 0.7,
      "marginThreshold": 0.2,
      "requiredConsecutive": 2,
      "windowScheduleSeconds": [
        0.5,
        1.0,
        1.5,
        2.0,
        2.5,
        3.0
      ],
      "contextRule": "weak evidence may be assisted only at the frozen integration seam; strong EEG overrides contradictory context; context-only execution is forbidden",
      "idleRule": "no command without EEG evidence; no context-triggered IDLE to CONTROL transition"
    },
    "softwareEvidence": {
      "existingTargetedTests": {
        "passed": 11,
        "failed": 0,
        "note": "The attempted combined command also named a non-existent test module; the named existing modules passed. The invalid module name is not a code failure."
      },
      "historicalM15Replay": {
        "trialCount": 89,
        "currentPolicyEarlyStopCount": 3,
        "currentPolicyFallbackCount": 86,
        "status": "PASS_DESCRIPTIVE_REPLAY"
      },
      "m13_7GoldenSoftwareReplay": {
        "status": "PASS_WITH_CAVEATS",
        "staticCalibrationTrials": 60,
        "staticCalibrationCorrect": 59,
        "staticCalibrationAccuracy": 0.9833333333333334,
        "source": "read-only M13.7 Golden QA artifacts"
      },
      "interpretation": "All metrics above are replay/software evidence. They are not an independent human holdout, not a live optical timing measurement, and not a production acceptance gate."
    },
    "knownLimitations": [
      "No independent new-human EEG holdout exists in this run.",
      "M13.7 Golden packages have protocol-derived target labels and no independent ground-truth file; target metrics are descriptive against the recorded protocol stimulus label.",
      "M13.7 package sessions overlap trial IDs and require authoritative deduplication before analysis.",
      "No-intent evidence is a controlled protocol block from one human campaign, not a population-level idle model.",
      "Physical display refresh/timing, ND8 contact quality in a new session, Quest timing, and real robot behavior were not validated.",
      "Existing M6/M13 evidence contains no context-aware human experiment; synthetic context must not be presented as human context evidence."
    ],
    "sourceArtifacts": [
      "docs/status/PROJECT_STATUS.md",
      "docs/roadmap/context-aware-bci-shared-autonomy.md",
      "artifacts/paper_eeg_gap_closing_20260920_022218/00_GAP_CLOSING_SUMMARY.md",
      "artifacts/paper_eeg_gap_closing_20260920_022218/p0_dynamic_stopping/current_policy_replay/summary.json",
      "eeg/decoder/fbcca.py",
      "integration/m13_7_golden_qa.py",
      "docs/protocols/M13_7_Golden_Protocol_v1.json"
    ],
    "nextAllowedStep": "Run the new-human EEG validation protocol and keep Stage3 production integration OFF until ACTIVE/IDLE metrics meet the predeclared safety gate on held-out human data."
  },
  "implementation_sha256": {
    "integration\\m12_context_eeg_fusion.py": "b05660f46d1afba9b113a491e1fbf9ba82960e8ee3cc2827c15115d8b8692b2e",
    "integration\\m13_dynamic_stopping.py": "479968d073ce6c7186799e596221ab3391d5353d4f258ec9778bb270af29daee",
    "integration\\m13_historical_replay.py": "1eadb68ed8adf3c718b60a4e0d33e707563dfaa9029af63be57064dba3e18e97",
    "eeg\\decoder\\fbcca.py": "e309b852409f7990397fd0433150625c64de1083f8131727a810e4a0b23bc7ec"
  },
  "source_record_type": "m6_5b_continuous_pseudo_online",
  "backend": "numpy_fbcca",
  "session_trial_counts": {
    "A": 30,
    "B1": 29,
    "B2": 30
  },
  "trajectory_points_per_trial": 11,
  "context_on_generator": "integration.m13_historical_replay._context_for_trial; history cycle (), (block_sim_01,)",
  "context_off_semantics": "M12 context_prior=None formal disabled path; active prior is neutral uniform",
  "ground_truth_used_for_context": false,
  "timing_semantics": "0.5 s onset guard + 1.5 s analysis window + 0.2 s causal step; effective times 2.0..4.0 s",
  "production_runtime_modified": false
}
```
