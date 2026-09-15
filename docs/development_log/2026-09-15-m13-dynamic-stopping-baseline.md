# M13 Context-aware Dynamic Stopping Baseline

Date: 2026-09-15
Status: **Software / replay PASS; real Quest + ND8 acceptance pending**

## Scope

This campaign implemented the smallest deterministic M13 policy over repeated M12 fused-evidence snapshots. It did not rewrite FBCCA, change the M6 window definition, redesign M12 fusion, alter the M8 wire contract, call the Robot Adapter, or operate Quest, ND8/COM11, a controller or a physical robot.

The policy defaults are frozen engineering defaults for this baseline:

- M6 formal window grid: `0.5, 1.0, 1.5, 2.0, 2.5, 3.0 s`.
- Existing M6.5b online semantics: `0.5 s` onset guard, `1.5 s` analysis, `0.2 s` step.
- Fused top evidence `>= 0.70`.
- Top-1 minus top-2 margin `>= 0.20`.
- Two consecutive eligible windows.
- The fused top must equal the raw EEG top in every confirming window.

The raw EEG confirmation requirement preserves user agency: context can bias a decision but cannot early-stop a context-favored target while current EEG evidence favors another target.

## Evidence

The policy suite passed 14/14 tests. Synthetic acceptance passed all 10 required cases, including first-window waiting, transient spikes, target-switch reset, context/EEG conflict, strong EEG override, ambiguous evidence, valid fallback, tie/no-decision, invalid evidence, deterministic replay and uniform-context behavior.

The existing read-only M6.5b result fixture at `D:\EEG_Study\m6_4\replay\m6_5b-continuous-results.json` was replayed without copying or modifying it. It contains 89 historical trials from sessions A/B1/B2. M13 produced 20 early stops and 69 full-window fallbacks. Context was a deterministic synthetic overlay cycling only predeclared observable M11 histories; the recorded ground-truth label was excluded from context input. This replay demonstrates software trajectory consumption and descriptive timing behavior only; it is not a context-aware human experiment, live-latency measurement or generalized accuracy study.

The thin M8 integration passed exactly-once, no-decision suppression and identity checks. Readiness passed. The related regression was 70/70. The existing M6 pseudo-online suite was 7/8 only because the local pre-existing environment does not contain optional scipy required by the legacy FBCCA backend; the pure NumPy FBCCA score seam passed, and no dependency was installed.

## Boundary and next step

The software/replay baseline is complete. A future interactive session must observe a live ND8 score trajectory, the M12 fused trajectory, the M13 stop window/reason, the final class and the Quest frozen-selection ACK, including duplicate suppression. Those are not claimed here. Use the operator procedure and stop before M14, learned stopping, threshold optimization or adaptive lambda.

Run evidence: `docs/agent/overnight/runs/m13-dynamic-stopping-campaign-20260915T112500Z/`.
