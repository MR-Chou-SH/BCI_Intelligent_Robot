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
