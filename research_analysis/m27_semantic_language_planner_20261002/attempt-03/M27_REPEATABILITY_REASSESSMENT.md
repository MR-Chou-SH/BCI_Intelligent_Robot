# M27 Repeatability Reassessment

## Result

- Structural repeatability: **PASS** across 5 repeated cases.
- Saved live response rows reused: 14; new API calls: **0**.
- Source result SHA-256: `9cef20d5f92d37b14e1656aefa78f390e8aee54dd132b3bb0f0c7a97f11aafe4`.

The first metric included `intent_summary` in its structural hash. That made a prose paraphrase appear to be a changed plan. This reassessment hashes status, selected-object order, ordered executable actions, and ordered alternative action sequences. Natural-language fields are compared separately and are not treated as executable-plan structure.

## Repeated cases

| Case | Repeats | Structure identical | Exact prose identical |
|---|---:|---|---|
| m20_phone_to_wireless_charger | 2 | yes | no |
| m20_medicine_into_closed_storage | 2 | yes | no |
| m20_phone_charger_or_storage_ambiguous | 2 | yes | no |
| future_book_to_bookshelf | 2 | yes | no |
| future_pen_into_closed_drawer | 2 | yes | no |

This is an offline reanalysis of the unchanged attempt-03 JSONL. It did not contact DeepSeek, alter the saved model responses, dispatch a robot action, or change the benchmark cases.
