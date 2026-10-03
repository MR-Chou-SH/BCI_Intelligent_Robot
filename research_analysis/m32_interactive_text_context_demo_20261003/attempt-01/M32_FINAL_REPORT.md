# M32 Final Report

## Status

**Software contract: PASS_WITH_REPORTED_LIMITATIONS.** The text parser, manual-selection session, full-candidate M30 calls, ordered-history recomputation, q vector checks, and six-scene benchmark completed. Semantic quality is partial: ambiguity recognition and repeatability remain weak, so the Context ranking should be reviewed by a person before it is used as an assistive signal.

The final benchmark lock is `c2eda933995158c0d96f3e5f64e5ee44e7422d787cfd1276c019ac6c703dff31`. The first completed live run is retained, with source and summaries, under `iterations/live-run-v1/`; the final revision's results are `text_scene_sequence_results.jsonl`.

## Answers to the acceptance questions

1. **Unseen natural-language scene input:** The CLI accepts free-form scene text. This benchmark exercised six independently written everyday descriptions; broad open-ended generalization still needs manual review.
2. **Seven to eight objects:** All six final scenes produced exactly eight selectable objects; all 48/48 expected object mentions matched, with zero extra selectable mentions.
3. **Explicit attributes:** 10/10 explicit colors and 4/4 explicit states were retained. No unspecified color or state was added.
4. **Sequential q behavior:** The actual M30 `SemanticContextEngine.predict_next_target` API was called for every one of 60 decisions with the complete ordered history and all remaining selectable candidates. Candidate set, selected-object removal, q dimension, and q normalization passed 60/60. The q dimension shrank with each selection.
5. **History sensitivity:** Six pairs used the same remaining candidate set with reversed ordered histories. Four pairs returned full rankings; one changed rank order (electronics: cable remained first, while laptop and user-zone changed relative positions). No pair changed its top candidate. q changed in 4/6 pairs. This is evidence of history sensitivity, but the effect is modest.
6. **Ambiguity:** The UI displays model status, margin, entropy, and q concentration. The model returned `ambiguous` for 3/60 points independently labeled ambiguous. The interface supports ambiguity reporting, but recognition quality is low.
7. **Candidate grounding and relations:** There were zero extra accepted selectable objects and no candidate-set mismatch across 60 Context calls. M30 returned 185 non-`NONE` relation rows. Independent acceptable-target recall was 34/55 top-1 (61.8%) and 53/55 top-3 (96.4%); these scores are not human judgments of physical affordance correctness.
8. **Latency:** Context latency mean 2,236 ms, median 2,402 ms, p90 3,234 ms, maximum 3,667 ms. Scene parser latency mean 1,831 ms, median 1,840 ms, p90 2,030 ms.
9. **Repeatability:** Five of eight repeated calls exactly matched status and ranking (62.5%). Three differed; two changed from `informative` to `context_off`. Mean q L1 distance was 0.493525, median 0.213966. This is a reported DeepSeek/M30 output-quality limitation, not a candidate-wiring failure.
10. **Scene-family results:**

   | Family | Top-1 acceptable | Top-3 acceptable | Notes |
   | --- | ---: | ---: | --- |
   | Laundry | 3/9 | 9/9 | Weakest top-1 result |
   | Kitchen | 6/9 | 9/9 | Strong top-3 coverage |
   | Study/office | 6/7 | 7/7 | Three points returned `ambiguous`; three returned `context_off` |
   | Bedroom/clothing | 4/10 | 10/10 | Plausible alternatives usually appeared in top three |
   | Electronics/desk | 8/10 | 10/10 | Strongest top-1 and top-3 result |
   | Mixed/ambiguous | 7/10 | 8/10 | More top-3 misses than the other families |

11. **Manual review readiness:** The runbook contains the exact launch command, six copy-ready descriptions, suggested selections, and a review checklist. The user can review the live CLI without EEG or Quest.
12. **Hardware and future vision:** No EEG, Quest, image input, or VLA/robot dispatch was used. Image input is not implemented. A future image frontend can supply the existing structured-scene schema without changing the M30 Context API.

## Investigated first-run issue

The first live run parsed room/location phrases as extra selectable items and the benchmark runner passed only scorer-labeled candidates to M30. That made 30/60 Context requests invalid. The exact v1 inputs, results, reports, and source are hash-preserved in `iterations/live-run-v1/`. Revision 3 clarified the scene descriptions while leaving all target labels and selection histories unchanged. The runner now passes all parsed selectable candidates minus the complete selection history; a focused regression test covers an unlabeled extra candidate.

## Verification

Targeted parser, CLI/session, runner candidate-set, and M30 semantic regression results, artifact hashes, scope boundaries, and machine-verifiable acceptance checks are recorded in `final_validation.json`. No M30 prompt, held-out result, or model score was retuned using M32.
