# M29 Final Report — Standalone Semantic Language Bridge

Generated 2026-10-02T11:01:06Z.

## CLI and examples

Run the interactive demo from the repository root:

    & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli

Strict one-shot example:

    & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli '手机' '无线充电座'

Known closed-container example with observed state:

    & '.venv\Scripts\python.exe' -m integration.semantic_bridge_cli --intent 'Put the medicine box into the storage box.' --states-json '{"assist_storage_box":{"lid":"closed"}}' '小药盒' '收纳盒'

The live frozen benchmark returned phone-to-charger status **executable**, actions: PICK(assist_phone) → PLACE_ON(assist_phone, assist_wireless_charger). Chinese: 拿起手机，然后把手机放到无线充电座上。 English: Pick up the smartphone, then place the smartphone on the wireless charging pad.

Medicine-to-storage returned **executable**, actions: OPEN(assist_storage_box) → PICK(assist_medicine_box) → PLACE_IN(assist_medicine_box, assist_storage_box) → CLOSE(assist_storage_box). Chinese: 打开收纳盒，然后拿起小药盒，然后把小药盒放进收纳盒里，然后关上收纳盒。

The ambiguous relation returned **ambiguous** with 2 alternatives and no executable top-level action list. A medicine-box PRESS request returned **invalid** with no actions.

Strict scene mode grounds only known scene objects. Free mode labels inferred affordances as unverified, validates their schema, and keeps dispatch disabled. This module is not connected to Quest, BCI runtime, MuJoCo, a robot, or a VLA policy.

## Frozen benchmark and final-code retests

The frozen benchmark has 43 cases and SHA-256 ca1ed24dae3a8321952855a590ee011fff05e48ef1a872ab15fa1efa1f3d2b3c. All 43 primary rows and 16 repeat calls are present. The raw first pass ran before the final one-object/alias input correction; three affected cases were live-retested with the same frozen inputs. The combined view replaces only those cases and keeps other first-pass results.

| Final-code hybrid metric | Result |
|---|---:|
| Schema valid | 43/43 |
| Frozen expected status match | 41/43 |
| Exact action sequence | 42/43 |
| Target grounding | 43/43 |
| Strict validator accepted | 42/43 |
| Free-mode safety | 6/6 |
| Expected ambiguous status | 3/4 |
| Expected invalid status | 7/7 |

Remaining expected-status mismatches: toy_in_bin, toy_ambiguous_tidy. Both frozen toy-bin gold labels conflict with the state-safety contract: the synthetic scene marks the bin openable but supplies no current state, while the expected outcomes assume storage is executable or one of several viable relations. The final planner preserves fail-closed state handling and returns invalid. The benchmark file and lock were left unchanged.

The repeated subset had 7/8 structurally consistent outputs and 8/8 exact-prose-consistent outputs. One ambiguous case varied whether a safe CLOSE action appeared in one alternative; the top-level status and actions stayed stable. Primary logical-call latency: mean 1523 ms, median 1348 ms, P90 2598 ms, P95 2703 ms. The benchmark estimated 74 model requests, used 11 bounded schema correction retries (14.9%), and recorded 0 API failures. One first-pass unary input contract error was fixed and all three final-code retests passed.

## Targeted live retest

The Chinese and English button/switch labels and the one-noun invalid medicine-box action passed: 3/3. Equivalent labels coalesced to one stable scene ID, strict unary input reached the planner, and unsupported PRESS remained non-executable.

## Review status

The CLI is ready for the user's manual language review. Human review has not occurred. Free-noun mode is exploratory only. No full-system integration is claimed.
