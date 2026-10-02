| Phase | Status | Key result | Commit |
|---|---|---|---|
| M28 semantic Context engine | PASS | 40-case curated benchmark; active precision 19/19, coverage 19/40; ambiguity 7/7 and invalid rejection 8/8 | `cdccd53ac3f81c7ce57e91102b21bf0c39db7b94` |
| M28 EEG transfer | PASS | 100 seeded transfer simulations per frozen FAST/MEDIUM/CONSERVATIVE point; unchanged M25 inputs; zero wrong early stops | `cdccd53ac3f81c7ce57e91102b21bf0c39db7b94` |
| 0.4–0.5 s feasibility | NOT SUPPORTED | The target is not supported prospectively at measured coverage/arrival; precomputed gains are transfer-simulation estimates only | `cdccd53ac3f81c7ce57e91102b21bf0c39db7b94` |
| M29 CLI bridge | PARTIAL | 43-case frozen benchmark plus 16 repeats; final-code targeted retests 3/3 pass; two frozen expected-status labels conflict with unknown-state fail-closed behavior | `e679b1591743cd4be7f36460e07866272742c703` |
| Git push | PASS | M28/M29 feature tip `e679b1591743cd4be7f36460e07866272742c703` was verified on `origin/codex/m28-m29-semantic-context-bridge`; this handoff is included in the docs closeout push | `e679b1591743cd4be7f36460e07866272742c703` |

## M28

- The frozen semantic benchmark has 40 cases. Informative single-target top-1 is 19/20 (95%); top-3 recall is 20/20 (100%).
- Active Context precision is 19/19 (100%) with 19/40 (47.5%) overall coverage. These are curated semantic benchmark results, not population user-intent accuracy.
- Status counts: 7 ambiguous, 5 Context OFF, 9 invalid, and 19 informative. Ambiguity was correct in 7/7 and invalid rejection in 8/8. Thus 21/40 cases did not activate Context.
- Held-out single-target performance is 11/12 (91.7%); the one held-out miss was rejected as invalid. Current M20-like cases were 6/6 and adversarial informative cases 2/2.
- Active DeepSeek API latency was mean 1.142 s, median 1.005 s, P90 1.829 s. Same-trial reasoning often arrives after the useful FAST/MEDIUM EEG window; precomputation is the relevant future path to investigate.
- Estimated mean Context-active / all-trial latency gains in `SEMANTIC_CONTEXT_QUALITY_TRANSFER_SIMULATION`:

  | Availability | FAST | MEDIUM | CONSERVATIVE |
  |---|---:|---:|---:|
  | Measured live-API arrival | 0.337 / 0.025 s | 0.361 / 0.043 s | 0.376 / 0.079 s |
  | Precomputed before trial | 0.386 / 0.105 s | 0.444 / 0.159 s | 0.574 / 0.262 s |

- All reported transfer conditions retained paired baseline accuracy and had zero Context-caused wrong early stops. The precomputed Context-active conditional means reach 0.4 s for MEDIUM and 0.5 s for CONSERVATIVE in simulation; the all-trial means are lower.
- Neither 0.4 s nor 0.5 s is established as a prospective result. At the measured 47.5% coverage, no CONSERVATIVE frontier combination supports either target. The illustrative safe synthetic conditions assume precision 1.00, prior top mass 0.90, immediate availability, and coverage 0.75 for 0.4 s or 1.00 for 0.5 s; these assumptions exceed measured coverage.
- This remains a transfer simulation because historical EEG trials were randomized independently of the semantic scenes. Seeded semantic-to-trial assignment is simulation machinery, not causal evidence that a real scene predicts the next intended target. A prospective natural-task EEG study must freeze and log scene/task context before each trial and compare it with EEG-only decisions.
- M25 `INPUT_MANIFEST.json` SHA-256 remained `0bc5b2032df564342562d3af085a174d81f707db42dfc161c14db4715d5fec5a`; frozen source hashes were unchanged. No raw waveform was decoded or modified.

## M29

- Interactive launch from the repository root:

  ```powershell
  & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli
  ```

- One-shot strict example:

  ```powershell
  & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli '手机' '无线充电座'
  ```

- Phone → charger returned `EXECUTABLE`: `PICK(assist_phone)` → `PLACE_ON(assist_phone, assist_wireless_charger)`, rendered as “拿起手机，然后把手机放到无线充电座上。”
- Medicine → storage with observed closed-lid state returned `EXECUTABLE`: `OPEN` → `PICK` → `PLACE_IN` → `CLOSE`. Pass `--states-json '{"assist_storage_box":{"lid":"closed"}}'`; unknown state is not guessed.
- The ambiguous-relation fixture returned `AMBIGUOUS` with alternatives. A medicine-box `PRESS` request returned `INVALID` with no actions.
- Frozen benchmark: 43 primary cases and 16 repeat calls. Hybrid final-code metrics are schema 43/43, expected status 41/43, exact action sequence 42/43, target grounding 43/43, strict validator 42/43, and free-mode safety 6/6. Three final-code live retests passed 3/3. There were zero API failures in the first pass.
- The two remaining expected-status differences are `toy_in_bin` and `toy_ambiguous_tidy`: both frozen gold labels assume the openable toy bin is usable despite the fixture omitting its current state. The planner returns invalid under its fail-closed state rule. The frozen benchmark and lock were not changed.
- Repeat subset: 7/8 structurally consistent, 8/8 exact-prose consistent. Primary logical-call latency: mean 1.523 s, median 1.348 s, P90 2.598 s. The CLI is ready for manual language review. It is not integrated with a VLA policy, BCI runtime, Quest, MuJoCo, or robot.

## Verification

- M28/M29 focused deterministic tests: 19/19 passed.
- M27 compatibility regression: 12/12 passed after replacing the historical implementation with a shared-core compatibility shim.
- Python syntax/compile and CLI help checks passed.
- M29 frozen live benchmark completed 43 primary calls and 16 repeats; the three final-code targeted retests passed. One initial local unary-input contract failure was fixed and retested; no API failures occurred.
- M28 generated all nine required SVG figures. M28 and M29 frozen benchmark hashes match their lock files. The exact environment secret value was absent from staged task files; no `.env` or persisted authorization header was included.

## Protection

| Boundary | Result |
|---|---|
| Quest accessed | No |
| ADB/MQDH accessed | No |
| ND8 accessed | No |
| COM11 accessed | No |
| Physical robot accessed | No |
| Raw EEG modified | No |
| Task2 stable branch modified | No |
| DeepSeek API key persisted or printed | No |

The feature work was committed in two selective commits and pushed without force; this handoff and run-state record are in a docs-only closeout commit. The pre-existing unrelated dirty files and untracked inventory were left in place and excluded from all task commits.
