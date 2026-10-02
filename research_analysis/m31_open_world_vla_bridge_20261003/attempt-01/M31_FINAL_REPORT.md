# M31 Final Report — Open-World Semantic Language Bridge

## Outcome

**Software bridge: PASS. Benchmark quality: PARTIAL, with a clear ambiguity weakness.** The bridge now uses DeepSeek world knowledge by default, produces validated bilingual high-level intent for ordinary objects outside the strict M29 scene catalogue, preserves explicit color/state, rejects unsafe/unavailable-state outputs, and never dispatches. It is not connected to a real VLA.

The primary run used frozen prompt `m31-open-world-vla-language-v5`, schema `m31-structured-intent-v2`, engine `m31-descriptor-parse-and-reference-guard-v4`, model `deepseek-flash`, temperature 0, and the benchmark SHA-256 in `open_world_bridge_benchmark_lock.json`. The 84-case benchmark was human-authored and locked before model calls. The frozen main run has 48 train, 12 dev, and 24 held-out cases. Train/dev informed prompt revisions; the held-out prompt was frozen before any held-out request and was not tuned afterward.

## Required example outputs

The exact result objects for all required examples are in `required_manual_examples.json`. Representative primary-run outputs:

| Input | Relation | Chinese instruction | English instruction |
|---|---|---|---|
| 药盒 + 收纳盒 | `STORE_IN` | 把药盒放进收纳盒里。 | Put the medicine box into the storage box. |
| 小药盒 + 收纳盒（关闭状态） | `STORE_IN` | 打开收纳盒，把小药盒放进去，然后关上收纳盒。 | Open the storage box, put the small pill box inside, and then close the storage box. |
| 手机 + 无线充电座 | `CHARGE_WITH` | 把手机放到无线充电座上充电。 | Place the phone on the wireless charging pad to charge it. |
| 苹果 + 水果刀 | `CUT_WITH` | 用水果刀切苹果。 | Cut the apple with the fruit knife. |
| 红色苹果 + 水果刀 | `CUT_WITH` | 用水果刀切红色苹果。 | Use the fruit knife to cut the red apple. |
| 橙子 + 榨汁机 | `JUICE_WITH` | 用榨汁机榨橙子。 | Juice the orange with the juicer. |
| 书 + 书架 | `PLACE_ON` | 把书放到书架上。 | Place the book on the bookshelf. |

The reversed `水果刀 + 苹果` example still identifies `CUT_WITH`. `手机 + 盒子` returned `AMBIGUOUS` with no committed instruction. The incompatible “use a book to charge a battery” example returned `INVALID`. All results retain `dispatch_allowed=false`.

## Benchmark results

Primary run metrics (84 cases):

- Status accuracy: **76/84 (90.5%)**; held-out **22/24 (91.7%)**.
- Relation accuracy on independently labeled executable cases: **62/70 (88.6%)**; held-out **19/20 (95.0%)**.
- Ambiguous/invalid status accuracy: **9/14 (64.3%)**; held-out **2/4 (50%)**. Invalid task traps were rejected **7/7**, but the model still over-committed on several ambiguous pairs. Two of the seven ambiguity cases were recognized as ambiguous.
- Structured selected/reference correctness: **84/84 (100%)**. No hallucinated structured object reference was accepted.
- Independently annotated forbidden third-object mentions: **0/84**. This is a bounded phrase audit, not a general open-vocabulary entity detector.
- Explicit color fidelity: **4/4 (100%)** in both languages; no unspecified color claims were detected.
- Explicit state acknowledgement/safe outcome: **3/3 (100%)**; no unobserved state claims were detected.
- Bilingual instructions appeared for **67/70** expected-executable cases. Every output accepted as `EXECUTABLE` passed the bridge's requirement for both language fields.
- Finite bilingual relation-cue consistency audit: **65/72 (90.3%)** among executable outputs. High-level step/relation alignment: **65/67 (97.0%)** among expected-executable cases that returned executable intent. Both are explicit deterministic proxies, not general semantic judges.
- Exact repeatability on 16 repeated examples: structure **14/16 (87.5%)**; bilingual prose **12/16 (75.0%)**.
- API latency: mean **1,227 ms**, median **1,139 ms**, p90 **1,500 ms**, p95 **2,295 ms**, max **2,692 ms**. Schema/safety correction retry occurred in **8/84 (9.5%)** main cases.
- Main-run token usage: **120,757 total tokens**.

Storage, tool-use, charging/electronics, everyday placement, and explicit-context families were strongest. Kitchen and cleaning relations were less consistent against the narrow independently labeled relation sets; several outputs such as `WASH_WITH` are semantically plausible but were not in those cases' frozen acceptable-relation labels. Reversed-order success was 6/7; the reversed storage-box/medicine-box pair was conservatively marked ambiguous. The ambiguous-pair family was weakest at 2/7 status accuracy.

One frozen-label conflict is disclosed: the `occupied charger + phone` development case was labeled executable `CHARGE_WITH`, while its explicit `occupied` state makes that action unavailable. The bridge rejected it as `INVALID`, which is the safe state-aware behavior. The pre-model benchmark label was preserved, so the raw status score counts this as a miss. No held-out label was altered.

The first prompt iteration had a schema-wording mismatch (`high_level_action` and null `ambiguity_reason`) and was stopped after 19 pilot cases; those outputs remain in JSONL under a separate run label. Prompt iterations and corrections are preserved. Only v5 is used for the primary metrics above.

## Answers to the 11 required questions

1. **Did the bridge stop rejecting ordinary open-world nouns?** Yes. The default CLI path calls the new open-world API, not the strict M29 scene catalogue. Common pairs outside that catalogue produced structured outputs.
2. **Do required examples produce reasonable relations?** Yes for the required manual examples shown above, including reversed knife/apple order. Results are model-derived rather than a pair-to-sentence lookup table.
3. **Does it preserve explicit color?** Yes, 4/4 benchmark color cases retained color in both Chinese and English.
4. **Does it avoid inventing color?** No unobserved color claim was detected in the 84 primary results. The detector and per-case forbidden-mention audit are described in the experiment scripts; this is not a universal color entity recognizer.
5. **Does it use explicit object state correctly?** Closed storage generated an open/place/close sequence. An explicitly occupied charger was rejected as unavailable. Explicit-state safety/acknowledgement was 3/3, subject to the benchmark-label conflict above.
6. **Does it avoid requiring unknown state for high-level language?** Yes. `药盒 + 收纳盒` was accepted without inventing whether the box was open or closed.
7. **What is relation accuracy on held-out cases?** 19/20 executable held-out cases (95.0%) matched the frozen acceptable relation set. Held-out status accuracy was 22/24 (91.7%).
8. **What is the hallucination rate?** 0/84 accepted structured outputs used an unsupported object ID; the bounded manual third-object phrase audit also found 0/84 mentions.
9. **Is structural intent repeatable?** Mostly, not perfectly: 14/16 exact structure and 12/16 exact Chinese/English prose on the repeated subset.
10. **Is the module ready for user manual review?** Yes. The CLI defaults to open-world mode and the runbook contains the exact command and all required examples. The ambiguity weakness should be reviewed explicitly.
11. **Is it connected to a real VLA?** No. `dispatch_allowed` is always false; no real VLA call, robot, trajectory, or hardware was used.

## User launch command

```powershell
& '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli
```

The older grounded validator remains available using `--mode strict-scene`. M31 used no Quest, EEG, ND8, COM11, physical robot, or VLA dispatch.
