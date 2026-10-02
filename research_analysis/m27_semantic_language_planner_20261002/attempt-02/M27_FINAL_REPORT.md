# M27 Final Report — Semantic Language Planner

## Result

- Overall status: **PASS**
- External component: DeepSeek pretrained chat model; not a model trained by this project.
- Resolved model: `deepseek-flash`
- API key available: yes (value never logged or persisted).
- Base URL: `https://api.deepseek.com`
- Prompt/schema: `m27-semantic-planner-prompt-v2` / `m27-structured-plan-v1` / `m27-semantic-scene-v1`
- Frozen request: temperature 0.0, thinking disabled, max_tokens 1200, JSON-object response, one correction retry.

## Benchmark results

| Case | Repeat | Final status | Direct model matched | Safe fallback | Latency |
|---|---:|---|---|---|---:|
| m20_phone_to_wireless_charger | 1 | executable | yes | no | 2353.156 ms |
| m20_phone_to_wireless_charger | 2 | executable | yes | no | 1897.157 ms |
| m20_medicine_into_closed_storage | 1 | executable | yes | no | 2370.558 ms |
| m20_medicine_into_closed_storage | 2 | executable | yes | no | 2388.2 ms |
| m20_phone_charger_or_storage_ambiguous | 1 | ambiguous | yes | yes | 3215.126 ms |
| m20_phone_charger_or_storage_ambiguous | 2 | ambiguous | yes | yes | 3340.04 ms |
| m20_medicine_to_user_zone | 1 | executable | yes | no | 2284.172 ms |
| m20_pressable_affordance_conflict | 1 | invalid | yes | no | 2006.379 ms |
| m20_container_relation_conflict | 1 | invalid | yes | no | 2559.851 ms |
| m20_missing_named_destination_hallucination_trap | 1 | invalid | yes | no | 1656.392 ms |
| future_book_to_bookshelf | 1 | executable | yes | no | 2509.712 ms |
| future_book_to_bookshelf | 2 | executable | yes | no | 2255.012 ms |
| future_pen_into_closed_drawer | 1 | executable | yes | no | 2489.579 ms |
| future_pen_into_closed_drawer | 2 | executable | yes | no | 2408.509 ms |

- Completed live calls: 14/14; final structured outputs matched: 14; direct model candidates matched: 14; local validator passed: 14.
- Safe ambiguity/invalid fallbacks: 2.
- Median / maximum latency: 2379.379 / 3340.04 ms.
- Total reported tokens: 80132.
- Correction retries: 14.
- Low-temperature structural repeatability: `{"m20_phone_to_wireless_charger": {"repeat_count": 2, "structurally_identical": true}, "m20_medicine_into_closed_storage": {"repeat_count": 2, "structurally_identical": true}, "m20_phone_charger_or_storage_ambiguous": {"repeat_count": 2, "structurally_identical": true}, "future_book_to_bookshelf": {"repeat_count": 2, "structurally_identical": true}, "future_pen_into_closed_drawer": {"repeat_count": 2, "structurally_identical": true}}`.

## Interpretation and limitations

The semantic schema is derived from the versioned M20 SceneLayoutSnapshot and its affordance tags; selected IDs are kept in input order. The model returns constrained JSON. A local validator rejects unknown objects, actions outside the whitelist, non-container PLACE_IN targets, incompatible PLACE_ON relations, unsupported OPEN/CLOSE states, and invalid alternatives. A single correction retry is bounded; unresolved output fails closed with no executable actions.

The M20 snapshot was deterministically reconstructed with Task 2 seed 20261002 and its serialized SHA-256 matches the stored Task 2 first-seed snapshot. Future book/shelf and pen/drawer objects are synthetic semantic fixtures; they are not physical scene evidence. This experiment did not dispatch to Unity, MuJoCo, a VLA, Quest, ND8, COM11, or a physical robot. It evaluates structured semantic generalization on this compact benchmark only; it makes no claim of safe real-robot execution.

Whether further work is worthwhile depends on repeatable held-out scene performance and later adapter validation. The next justified step is a separately reviewed VLA adapter contract; no production integration is part of this experiment.

## Reproducibility and secret handling

- Model-list resolution occurred via the live official `/models` endpoint, intersected with official Chat Completions model IDs.
- No API key, Authorization header, or raw HTTP headers were stored. Output secret scan: **PASS**.
- Task 1/Task 2 stable checkpoint remained on `feature/m9-virtual-manipulation`; this experiment is isolated on `codex/m27-semantic-language-planner`.
