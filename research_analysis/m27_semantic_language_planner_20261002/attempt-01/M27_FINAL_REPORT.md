# M27 Final Report — Semantic Language Planner

## Result

- Overall status: **PARTIAL**
- External component: DeepSeek pretrained chat model; not a model trained by this project.
- Resolved model: `deepseek-flash`
- API key available: yes (value never logged or persisted).
- Base URL: `https://api.deepseek.com`
- Prompt/schema: `m27-semantic-planner-prompt-v1` / `m27-structured-plan-v1` / `m27-semantic-scene-v1`
- Frozen request: temperature 0.0, max_tokens 1200, JSON-object response, one correction retry.

## Benchmark results

| Case | Repeat | Model status | Expected relation/status matched | Latency |
|---|---:|---|---|---:|
| m20_phone_to_wireless_charger | 1 | executable | yes | 9225.369 ms |
| m20_phone_to_wireless_charger | 2 | executable | yes | 5608.607 ms |
| m20_medicine_into_closed_storage | 1 | invalid | no | 11686.362 ms |
| m20_medicine_into_closed_storage | 2 | invalid | no | 11127.915 ms |
| m20_phone_charger_or_storage_ambiguous | 1 | executable | no | 10696.227 ms |
| m20_phone_charger_or_storage_ambiguous | 2 | invalid | no | 13508.126 ms |
| m20_medicine_to_user_zone | 1 | invalid | no | 11681.77 ms |
| m20_pressable_affordance_conflict | 1 | invalid | yes | 9903.442 ms |
| m20_container_relation_conflict | 1 | invalid | yes | 11906.639 ms |
| m20_missing_named_destination_hallucination_trap | 1 | executable | no | 7862.507 ms |
| future_book_to_bookshelf | 1 | executable | yes | 2784.082 ms |
| future_book_to_bookshelf | 2 | executable | yes | 2452.799 ms |
| future_pen_into_closed_drawer | 1 | invalid | no | 10466.243 ms |
| future_pen_into_closed_drawer | 2 | invalid | no | 11215.696 ms |

- Completed live calls: 14/14; predeclared cases matched: 6; local validator passed: 6.
- Median / maximum latency: 10581.235 / 13508.126 ms.
- Total reported tokens: 90096.
- Correction retries: 12.
- Low-temperature structural repeatability: `{"m20_phone_to_wireless_charger": {"repeat_count": 2, "structurally_identical": true}, "m20_medicine_into_closed_storage": {"repeat_count": 2, "structurally_identical": true}, "m20_phone_charger_or_storage_ambiguous": {"repeat_count": 2, "structurally_identical": false}, "future_book_to_bookshelf": {"repeat_count": 2, "structurally_identical": true}, "future_pen_into_closed_drawer": {"repeat_count": 2, "structurally_identical": true}}`.

## Interpretation and limitations

The semantic schema is derived from the versioned M20 SceneLayoutSnapshot and its affordance tags; selected IDs are kept in input order. The model returns constrained JSON. A local validator rejects unknown objects, actions outside the whitelist, non-container PLACE_IN targets, incompatible PLACE_ON relations, unsupported OPEN/CLOSE states, and invalid alternatives. A single correction retry is bounded; unresolved output fails closed with no executable actions.

The M20 snapshot was deterministically reconstructed with Task 2 seed 20261002 and its serialized SHA-256 matches the stored Task 2 first-seed snapshot. Future book/shelf and pen/drawer objects are synthetic semantic fixtures; they are not physical scene evidence. This experiment did not dispatch to Unity, MuJoCo, a VLA, Quest, ND8, COM11, or a physical robot. It evaluates structured semantic generalization on this compact benchmark only; it makes no claim of safe real-robot execution.

Whether further work is worthwhile depends on repeatable held-out scene performance and later adapter validation. The next justified step is a separately reviewed VLA adapter contract; no production integration is part of this experiment.

## Reproducibility and secret handling

- Model-list resolution occurred via the live official `/models` endpoint, intersected with official Chat Completions model IDs.
- No API key, Authorization header, or raw HTTP headers were stored. Output secret scan: **PASS**.
- Task 1/Task 2 stable checkpoint remained on `feature/m9-virtual-manipulation`; this experiment is isolated on `codex/m27-semantic-language-planner`.
