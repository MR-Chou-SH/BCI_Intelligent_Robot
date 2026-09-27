# M20 Overnight Final Report

## Gate status

| Gate | Result | Evidence boundary |
|---|---|---|
| Task 1 — canonical assistive desk scene | **PASS** | Unity Editor and MuJoCo software validation passed. Quest headset validation was not attempted because the Quest is charging and disconnected. |
| Task 2 — M19 synthetic full chain | **PASS** | Selection, Context, and all three pick-and-place paths passed with synthetic EEG in MuJoCo. No ND8 or physical robot was used. |
| Task 3 — historical Context/EEG replay | **PASS** | Historical V3 and M12/M13 baselines reproduced; offline sweeps and conflict/override checks passed. No raw EEG or production M19 file was changed. |

M20 software work is complete. Headset appearance, Quest-side interaction comfort, and physical robot behavior remain **NEEDS HUMAN VALIDATION**. These are not claimed as software passes.

## Task 1 — scene and parity

Unity and MuJoCo use the same canonical table-local specification. Automated checks passed for left/right signs, front/back ordering, dimensions, pose tolerances, hinge travel, button travel, collision-free initial placement, and reachable medicine/phone grasp poses. The Unity Editor validation passed 2 assertions; the storage lid opened to 100° and the button traveled 5 mm. MuJoCo found zero initial M20 interpenetrations.

The existing M9 block scene remains available and its recorded last-write time is unchanged. Unity and MuJoCo screenshots are saved under `docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task1/`. The acceptance record is `docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task1/acceptance.json`.

Quest Build & Run was not attempted: Developer Hub reported the headset disconnected and ADB was unavailable. This report does not claim in-headset visual comfort or orientation acceptance.

## Task 2 — M19 full-chain regression

The M20 candidate order and page layout passed:

- Page 1: medicine box, storage box, phone.
- Page 2: button, wireless charger, user zone.
- Slot 0/1/2 remains 7.2/9/12 Hz.

Previous, Next, cross-page selection, Undo Last, Submit order, stale-result rejection, active-slot checks, Context affordance ranking, and strong synthetic EEG override passed. The focused M20/M16/M19/M9 regression ran **61 tests, all passed**.

All three MuJoCo tasks passed: medicine→user zone, phone→user zone, and phone→charger. The phone grasp weld was enabled only after bilateral finger contact and released on gripper opening. Post-release stability and measured finger-to-table clearance are in `docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task2/body-weld-clearance-20260927/attempt-02/`. No physical robot operation is claimed.

Acceptance: `docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task2/acceptance.json`.

## Task 3 — historical EEG and Context study

The analysis uses 89 replay records: 88 formal-manifest records plus the separately identified B2 `m6_4-trial-023` exploratory replay supplement. Formal-only and all-record results are reported separately. The analysis reads the historical datasets and writes only new M20 output directories.

### Baseline reproduction

The V3 analysis matched all **267** preserved per-timepoint rows with zero mismatches. On the 88 formal records, both V3 EEG-only and V3 EEG+Context were 88/88 correct with zero wrong early stops. Effective EEG evidence time subtracts the 0.5 s onset guard:

| V3 policy | Mean onset-relative decision | Mean effective EEG evidence | Difference |
|---|---:|---:|---:|
| EEG-only | 1.445 s | 0.945 s | — |
| EEG+Context | 1.432 s | 0.932 s | 13.6 ms earlier |

The current M12/M13 paired replay also reproduced the preserved 89-trial artifact exactly. On the 88 formal records, Context-off was 88/88 correct; Context-on was 87/88 correct, produced three early stops, and changed one final class incorrectly. That error was not an early stop. Production M19 was left unchanged.

### Forensic findings and controlled tests

- FBCCA contributes three raw, nonnegative fused CCA scores; they are **not calibrated probabilities**.
- Historical V3 does not use M12 multiplicative fusion. It requires a unique Context top to agree with the raw EEG top, then evaluates a cross-fitted relaxed gate; the selected class remains the raw EEG top.
- Current M12 softens the prior with `lambda=0.5`. A raw `[0.90, 0.05, 0.05]` prior becomes approximately `[0.6167, 0.1917, 0.1917]`. The active-candidate projection does not flatten the prior because all three candidates remain active.
- Current M12 uses a raw EEG top-margin override at 0.20. M13 requires fused probability ≥0.70, fused margin ≥0.20, raw EEG confirmation, and two consecutive windows. Context can raise fused evidence before those gates, but cannot select a class that disagrees with the raw EEG top.
- The endpoint sweep covers alpha `0/0.25/0.5/1/1.5`, softening lambda `0.5/0.75/1.0`, aligned prior strengths `0.60/0.75/0.90/0.95`, neutral priors, and a wrong-top 0.90 conflict prior.
- The stronger offline stop variant requires a raw EEG margin ≥0.15 under Context and three consecutive windows. The cross-fitted selector chose alpha 0 in every fold, so this historical set did not justify a nonzero Context weight for a recommended stopping policy.
- A fixed alpha 0.25 / lambda 0.50 setting still showed limited offline acceleration with the stricter stability gates: 3/88 formal trials accelerated by 0.20 s under a target-aligned 0.90 prior, with 88/88 accuracy. Observable-history priors accelerated 1/88 by 0.20 s. The Green-history→Red-prior 0.90 illustration covers 30 red-target trials and accelerated one by 0.20 s. These target-derived priors are offline stress conditions, not deployable Context sources.
- Across 2,124 formal/all-record evaluations of fixed nonzero-alpha wrong-prior policies, there were zero wrong early stops and zero Context-caused errors. All 8,232 strong contradictory EEG windows preserved the raw EEG top. Under conflict, Context sometimes delayed stopping; it did not create a wrong decision in this replay.

### Supported explanation

The small historical V3 gain is primarily explained by already strong EEG: EEG-only reached 100% accuracy on the formal set at about 0.945 s mean effective evidence time. V3 Context only relaxed a gate when its top agreed with EEG, leaving little room to advance decisions. Current M12 additionally softens priors, reducing their influence; however, the prior projection does not erase Context. Stronger fixed offline weights advanced only a few trials under aligned conditions, while cross-fitted selection chose alpha 0. These results support keeping experimental Context fusion offline until weaker Natural-gaze data are available. They do not support changing the production M19 decoder tonight.

Task 3 acceptance and detailed machine-readable artifacts:

- `docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task3/acceptance.json`
- `research_analysis/m20_context_forensics_20260927/attempt-06/REPORT.md`
- `research_analysis/m20_context_forensics_20260927/attempt-06/context_forensic_audit.json`
- `research_analysis/m20_context_forensics_20260927/attempt-06/context_stop_sensitivity_summary.csv`
- `research_analysis/m20_context_forensics_20260927/attempt-06/safety_checks.json`

Earlier attempts are preserved alongside attempt-06 as an audit trail; none overwrites prior output.

## Commands to reproduce

Run from the repository root. Use a new output directory for each replay because the analysis refuses to overwrite an existing directory.

```powershell
.venv\Scripts\python.exe -B -m integration.m20_assistive_scene_contract --spec m7_unity6000\Assets\Resources\BCI\M20\assistive_desk_scene.json --report docs\agent\overnight\runs\m20-daily-assistive-desk-20260926\task1\geometry-parity-recheck.json
```

```powershell
.venv\Scripts\python.exe -B -m integration.m20_assistive_desk_e2e --all-scenarios --synthetic-eeg --output-dir docs\agent\overnight\runs\m20-daily-assistive-desk-20260926\task2\replay-next
```

```powershell
.venv\Scripts\python.exe -B -m unittest integration.test_m20_assistive_scene_contract integration.test_m20_assistive_desk_e2e integration.test_m16_paged_queue integration.test_m16_paged_queue_orchestration integration.test_m19_paged_live_eeg_demo integration.test_m9_batch_dispatch integration.test_m9_mujoco_execution -v
```

```powershell
.venv\Scripts\python.exe -B -m research_analysis.m20_context_forensics --output-dir research_analysis/m20_context_forensics_20260927/attempt-07
```

The full repository software verifier was run with:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/agent/verify.ps1 -OutputDirectory docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/verification
```

It reported **14 PASS / 1 FAIL / 0 BLOCKED**. The sole failure is `git diff --check` on pre-existing dirty files: `BciSelectionTransportClient.cs:825` has the baseline extra blank line at EOF, and Git reports existing LF→CRLF working-copy warnings for that file and `robot_arm/utils/gripper_scene.py`. Both files were already dirty at the M20 baseline and were preserved. The verifier also reports the optional SciPy-dependent decoder suites as `NOT_ENABLED`; the M20-specific historical replay and targeted regressions were run separately and passed.

## M20 source files added

- `integration/m20_assistive_scene_contract.py`
- `integration/m20_assistive_desk_e2e.py`
- `integration/test_m20_assistive_scene_contract.py`
- `integration/test_m20_assistive_desk_e2e.py`
- `research_analysis/m20_context_forensics.py`
- `m7_unity6000/Assets/BCI/VirtualManipulation/M20AssistiveDeskUnityScene.cs` and its `.meta`
- `m7_unity6000/Assets/Editor/M20OvernightTask1EditorValidation.cs` and its `.meta`
- `m7_unity6000/Assets/Tests/Editor/M20AssistiveDeskSceneTests.cs` and its `.meta`
- `m7_unity6000/Assets/Resources/BCI/M20.meta`
- `m7_unity6000/Assets/Resources/BCI/M20/assistive_desk_scene.json` and its `.meta`
- `m7_unity6000/Assets/PassthroughCameraApiSamples/MultiObjectDetection/M20DailyAssistiveDesk.unity` and its `.meta`

The M20 run plan, state, append-only worklog, handoff, gate acceptances, and verification outputs are under `docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/`. No commit, push, reset, clean, stash, or historical EEG write was performed.

## Next Quest check

After the Quest has charged, reconnect its USB cable to the PC so Developer Hub/ADB can see it. Then build and launch `M20DailyAssistiveDesk` from the existing Unity Editor and check left/right layout, visual comfort, and M16 paged selection on the headset. Report any headset-side mismatch before treating Quest acceptance as complete. No ND8/COM11 or physical robot action is part of this check.
