# M31 Open-World DeepSeek → VLA Language Bridge

## Objective

Evaluate a separate post-Submit language bridge that turns two to eight selected object descriptors into a validated, bilingual, high-level semantic intent. The bridge uses ordinary world knowledge without requiring a match in the M20 robot-scene catalogue. It is not an action planner: low-level motion data is rejected and `dispatch_allowed` remains false.

Public API: `integration.semantic_language_bridge.SemanticLanguageBridge.generate_instruction(...)`.
CLI default: `semantic` open-world mode. The older M29 scene-grounded validator remains available as `--mode strict-scene`.

## Frozen benchmark

`open_world_bridge_benchmark.json` contains 84 cases across 12 categories: storage, tool use, kitchen, charging/electronics, cleaning/care, everyday placement, open-world ordinary nouns, reversed input order, explicit color/state, ambiguity, incompatible task traps, and scene-context/reference control. It includes every required object pair and required descriptor/state form.

The labels and acceptable relation sets were authored independently of DeepSeek. The runner serializes only each case's `input` object; its expected status, relation labels, notes, and scoring rules remain in the evaluation section. The SHA-256 content lock is in `open_world_bridge_benchmark_lock.json`.

| Split | Cases | Use |
|---|---:|---|
| Train | 48 | Inspect broad prompt behavior and identify implementation defects |
| Dev | 12 | Freeze prompt and schema choices |
| Held-out | 24 | One final prompt-version evaluation; do not tune against it |

The benchmark validator checks the hash, required pairs, schema separation, split counts, and that evaluation labels are not serialized into model input.

## Method and metrics

Calls use the existing environment-configured DeepSeek client, resolved model ID, temperature 0, JSON-only output, and at most one schema/safety correction retry. A saved prompt-freeze record is required before the main held-out split can run. Results are appended to JSONL after every case, so an interrupted run can resume without replacing prior output.

Report status accuracy; relation accuracy on executable labels; selected/reference ID correctness; hallucinated-reference validation events; independently annotated forbidden object mentions; explicit-color fidelity in both languages; unobserved color/state claims; bilingual field presence and a finite bilingual relation-cue consistency audit; high-level step/relation alignment; ambiguity/incompatibility handling; schema retries; token usage; and API latency mean/median/p90/p95/max. A representative repeat set reports exact structural repeatability separately from exact prose repeatability. The cue and step checks are deterministic proxies, not general semantic judges. These are benchmark observations, not calibrated probabilities or robot execution success.

## Safety and boundaries

- No EEG, Quest, ND8, COM11, physical robot, MuJoCo execution, or VLA dispatch is part of M31.
- Scene/context object IDs are the only permitted structured references.
- Colors and states are facts only when present in an explicit descriptor or metadata.
- Unknown container state does not prevent a simple high-level storage instruction.
- A reasonable relation outside the robot skill set may use `CUSTOM` and is not rejected for lack of a low-level skill.
- API errors are redacted by the shared client. The API key is read from the process environment and is never written to experiment files.

## Artifact layout

All M31 experiment data is kept under this `attempt-01` directory. Historical M28/M29 results and the frozen M30 results are not modified.
