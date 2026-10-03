# M30 / M31 / M32 Final Handoff

## Status

| Phase | Status | Key result | Commit |
| --- | --- | --- | --- |
| M30 sequential semantic Context | PASS for software/artifact integrity; research result remains exploratory | Frozen 126-decision study; 0 no-delay violations, 246 wrong early stops, 0 positive-lambda conditions met the safe-support gate | `570f0dc7694d1bc12a434e59329a084ebcc6ee4e` |
| M31 open-world language bridge | PASS_WITH_REPORTED_LIMITATIONS | 84 cases; 76/84 status accuracy; 62/70 relation accuracy; ambiguity handling remains weak | `570f0dc7694d1bc12a434e59329a084ebcc6ee4e` |
| M32 interactive text-scene Context demo | PASS_WITH_REPORTED_LIMITATIONS | Six 8-object scenes; 60/60 candidate/q invariants; low ambiguity recognition and 5/8 exact repeatability | `2a2f6b7f8bb745b6c987312ac79afabe4ce97200` + audit correction `a03a60adf1cb9bcf5de7f4f7c2531d7e9e0e11ed` |

## Git checkpoints

- Branch: `codex/m30-m31-nextgen-semantic-context-vla-bridge`
- M30/M31 checkpoint-record SHA, pushed before M32: `c951674520cbe371016c3f19b33954e916d986b3`.
- Branch HEAD when M32 began: `ebc2ef5c7210e86d78a3977cb2b280d33f86c7b9`.
- M32 implementation commit SHA: `2a2f6b7f8bb745b6c987312ac79afabe4ce97200`.
- Final M32 checkpoint SHA, including the corrected compile-count audit record: `a03a60adf1cb9bcf5de7f4f7c2531d7e9e0e11ed`.
- The M32 checkpoint was pushed to `origin/codex/m30-m31-nextgen-semantic-context-vla-bridge`; `git ls-remote` matched `a03a60adf1cb9bcf5de7f4f7c2531d7e9e0e11ed`. This handoff is a later documentation/state commit.

## M30 morning summary

- Frozen benchmark: 33 episodes, 126 decisions, 8 semantic families; train/dev/held-out = 56/24/46. Benchmark SHA-256: `899aae6febb96cb6a63f0589fd7ad8e5bd0b317e97ac81de23c71ff062d237b0`.
- Held-out C65/C85/C100 overall participation was 69.6% / 87.0% / 89.1%; eligible-informative participation was 70.6% / 94.1% / 97.1%. Active precision was 68.8% / 60.0% / 61.0%. Held-out top-1 was 26/34 (76.5%), top-3 recall 33/34 (97.1%), relation accuracy among correctly active C100 rows 68.0%, ambiguity accuracy 1/9 (11.1%), and invalid/completed rejection 100%.
- C100 family results: kitchen 9/10 top-1, 75.0% active precision; ambiguous/adversarial 4/5, 60.0%; assistive/handover 7/10, 58.3%; state-dependent 6/9, 50.0%. Round top-1 was 5/5, 7/8, 6/11, and 8/10 for rounds 1–4; round 3 fell to 54.5% before round 4 recovered to 80.0%.
- Page projection covered 279 deterministic pages: 66.7% had three candidates, 11.1% had two; projected page top matched the global top in 44.4%, and a unique acceptable target appeared in 47.0%. These all-page figures are not page-selection accuracy.
- DeepSeek was called on 123/126 decisions. Latency: mean 1,914.9 ms, median 1,639.4 ms, p90 3,035.1 ms, p95 3,243.9 ms, max 3,541.2 ms; one correction retry on 37/126 calls (29.4%). Repeatability on 16 points: status 100%, top candidate 100%, full ranking 87.5%.
- At λ=1, C65/C85/C100 × FAST/MEDIUM/CONSERVATIVE Context-active mean gains were 0.377/0.440/0.575 s, 0.373/0.442/0.574 s, and 0.382/0.441/0.573 s. Corresponding all-trial gains were 0.097/0.147/0.245 s, 0.100/0.154/0.258 s, and 0.104/0.158/0.261 s. Paired accuracy deltas were −0.02/−0.11/−0.07 percentage points; the corresponding wrong early-stop counts were 2/10/6.
- Raw precomputed active means reached 0.4 s for MEDIUM and CONSERVATIVE at all gates, and 0.5 s for CONSERVATIVE only. Across the full matrix there were 246 wrong early stops and zero no-delay violations. Only λ=0 had zero wrong Context early stops, and it is the exact EEG-only baseline with no Context gain. Therefore no positive-λ condition is safe-supported; none of the raw 0.4/0.5 s crossings qualifies as supported.
- M28's descriptive baseline was 47.5% coverage, 100% active precision, and 0.574 s active / 0.262 s all-trial conservative gain with zero wrong stops. M30 increased participation but reduced active precision; held-out top-1 was 76.5% versus M28's 91.7%. The datasets differ, so this is not a controlled comparison and does not demonstrate improved generalization. All EEG integration remains historical transfer simulation, not prospective human-task evidence.

## M31 morning summary

- Frozen independent benchmark: 84 cases (48/12/24 train/dev/held-out). Status accuracy 76/84 (90.5%; held-out 22/24), relation accuracy 62/70 (88.6%; held-out 19/20), and grounding-reference correctness 84/84. No unsupported structured object references or independently audited third-object mentions were accepted (0/84 each; the phrase audit is finite). Explicit color fidelity was 4/4 and explicit state safe outcome 3/3.
- Required example summaries: `药盒 + 收纳盒` → STORE_IN, “把药盒放进收纳盒里”; closed storage box → open, place the small medicine box inside, close; `苹果 + 水果刀` → CUT_WITH without inventing color; `红色苹果 + 水果刀` → CUT_WITH and preserves red; `橙子 + 榨汁机` → JUICE_WITH; `书 + 书架` → PLACE_ON; `手机 + 无线充电座` → CHARGE_WITH. Every result keeps dispatch disabled.
- Ambiguous/invalid status accuracy was 9/14 (64.3%); only 2/7 ambiguity cases were recognized, while invalid task traps were rejected 7/7. Structural repeatability was 14/16 (87.5%); bilingual prose repeatability was 12/16 (75.0%). API latency: mean 1,227 ms, median 1,139 ms, p90 1,500 ms, p95 2,295 ms, max 2,692 ms; correction retry on 8/84 calls.
- Manual review is ready but not performed. Launch the open-world bridge from the repository root with:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli
```

The strict-scene mode remains optional. No real VLA is connected or dispatched.

## M32 launch and manual review

From the repository root:

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_context_demo_cli
```

The benchmark has six scene families, 12 trajectories, 60 sequential decision points, and eight predeclared repeat calls. Each final scene had eight selectable objects. The frozen benchmark SHA-256 is `c2eda933995158c0d96f3e5f64e5ee44e7422d787cfd1276c019ac6c703dff31`.

Example laundry description:

```text
这是一个洗衣房场景。可交互物品包括白色衬衫、蓝色牛仔裤、洗衣篮、洗衣机、洗衣液、晾衣架、关闭的储物柜和垃圾桶。
```

Example kitchen description:

```text
这是一个厨房场景。可交互物品包括红色苹果、水果刀、白色盘子、橙子、榨汁机、水槽、储物柜和垃圾桶。
```

### Complete kitchen selection-history walkthrough

This is the locked `apple-first` trajectory. Each row shows `q_global` after the listed selection was entered. Values are rounded to three decimals here; exact model values are in `research_analysis/m32_interactive_text_context_demo_20261003/attempt-01/text_scene_sequence_results.jsonl`.

| Round | Selection history | q dimension | q over remaining objects |
| --- | --- | ---: | --- |
| 1 | 红色苹果 | 7 | 水果刀 .609; 白色盘子 .050; 橙子 .014; 榨汁机 .288; 水槽 .024; 储物柜 .009; 垃圾桶 .007 |
| 2 | 红色苹果 → 水果刀 | 6 | 白色盘子 .053; 橙子 .238; 榨汁机 .646; 水槽 .032; 储物柜 .020; 垃圾桶 .012 |
| 3 | 红色苹果 → 水果刀 → 白色盘子 | 5 | 橙子 .240; 榨汁机 .654; 水槽 .054; 储物柜 .033; 垃圾桶 .020 |
| 4 | 红色苹果 → 水果刀 → 白色盘子 → 水槽 | 4 | 橙子 .171; 榨汁机 .767; 储物柜 .038; 垃圾桶 .023 |
| 5 | 红色苹果 → 水果刀 → 白色盘子 → 水槽 → 橙子 | 3 | 榨汁机 .957; 储物柜 .029; 垃圾桶 .014 |

The q dimension shrank `7 → 6 → 5 → 4 → 3`, and all previously selected IDs were absent from the candidate list on all 60 benchmark decisions.

### History sensitivity

In the electronics scene, both histories had the same six remaining objects:

- `手机 → 无线充电座`: USB线 > 笔记本电脑 > 书桌 > 用户交接区 > 杯子 > 抽屉
- `无线充电座 → 手机`: USB线 > 用户交接区 > 笔记本电脑 > 书桌 > 杯子 > 抽屉

The rank order changed and q L1 distance was `0.084293`; the top candidate remained USB线. Across all six paired tests, four returned comparable full rankings, one changed rank order, and four of six comparable q vectors changed.

## M32 results and limits

- Scene parsing: 48/48 exact object mentions, exactly eight selectable objects in each of six scenes, zero extra selectable mentions.
- Explicit facts: 10/10 colors and 4/4 states preserved; zero unspecified color/state inventions.
- Context: all 60 calls received the full remaining parsed candidate set; candidate-set integrity, gold-candidate coverage, selected-object removal, q dimension, and q normalization all passed 60/60.
- Independent next-target labels: top-1 acceptable 34/55 (61.8%); top-3 acceptable 53/55 (96.4%). Electronics was strongest at 8/10 top-1 and 10/10 top-3; laundry was weakest at 3/9 and 9/9.
- Ambiguity: the UI displays status, margin, entropy, and q concentration. DeepSeek returned `ambiguous` for 3/60 benchmark points labeled ambiguous; recognition remains a quality limitation.
- Repeatability: status and ranking matched on 5/8 repeats. Two repeats changed from `informative` to `context_off`; mean q L1 distance was 0.493525.
- Latency: Context mean 2,236 ms, median 2,402 ms, p90 3,234 ms; parser mean 1,831 ms.
- Verification: 56 focused M32/M30 semantic regression tests passed; 18 targeted Python source/test files passed `py_compile`; staged credential-pattern scan had 0 findings. Machine checks are in `attempt-01/final_validation.json`.

The initial live run and its exact source are retained under `attempt-01/iterations/live-run-v1/`. It exposed a candidate-set bug in the runner and extra location phrases in the benchmark descriptions. Revision 3 clarified the scene wording without changing target labels or selection histories. M30 itself and its held-out results were not changed or retuned.

## Boundaries and next action

M32 used no EEG, Quest, image input, VLA dispatch, or physical robot. Image input remains future work; a future frontend can supply the current structured-scene schema. The runbook is ready for human review using the launch command above; model ambiguity and repeatability should be evaluated directly before treating q as an assistive signal.

Pre-existing unrelated M20/Task2 changes and other dirty/untracked work were left untouched and excluded from both M32 commits. No reset, clean, stash, force push, or raw EEG modification was performed. The DeepSeek API key was not printed or persisted.
