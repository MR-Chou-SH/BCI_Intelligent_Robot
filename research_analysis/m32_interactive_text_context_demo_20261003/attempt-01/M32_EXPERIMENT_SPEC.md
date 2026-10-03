# M32 Experiment Specification

## Objective

M32 is a software-only, interactive demonstration of natural-language scene input followed by sequential semantic Context recomputation. It exercises the public M30 `SemanticContextEngine.predict_next_target` path; M32 does not create a second ranking or q implementation.

## Architecture

```text
Natural-language description
  -> TextSceneParser
  -> M27 SemanticSceneCore-compatible structured scene
  -> manual selection history
  -> M30 SemanticContextEngine.predict_next_target
  -> variable-dimensional global q
```

Scene mentions are accepted only when the quoted mention is present in the input text. The parser separately marks selectable targets and non-selectable scene context; the model receives every remaining selectable candidate and never receives scorer labels. Color and state are retained as observed facts only when literal evidence appears in the same source clause as the object mention. World-knowledge affordance tags are marked separately as inferred semantic metadata. Unknown colors and states remain absent. The parser and Context engine are separate APIs so a future image parser can supply the same scene schema.

The primary display is `q_global` over every remaining selectable scene object. Scores are ordinal and uncalibrated; the softmax q is not a calibrated probability. M32 does not show EEG page projections in its main display.

## Frozen benchmark

- Six families: laundry, kitchen, study/office, bedroom/clothing, electronics/desk, and mixed ambiguous/adversarial.
- Eight objects per scene; 12 manually authored trajectories; five sequential Context decisions per trajectory; 60 total decision points.
- Two trajectories per scene reverse the first selected pair. At round two, each pair has the same remaining candidate set and a different ordered history.
- Eight repeatability points are declared in the frozen benchmark before model calls.
- Acceptable next targets are independent alternatives and may be multi-label; they are not generated from DeepSeek results.
- Final benchmark lock: SHA-256 `c2eda933995158c0d96f3e5f64e5ee44e7422d787cfd1276c019ac6c703dff31`.
- An earlier static draft referenced three already-selected objects in its acceptable-target labels. Static validation caught this before any live calls. The exact draft and lock are preserved under `prevalidation/v1-invalid-selected-target-labels/`.
- The first live run used benchmark revision 2 (`f01e79b8…`). It is preserved with its source and summaries in `iterations/live-run-v1/`. That run exposed incidental room/location wording being parsed as selectable candidates and a runner bug that incorrectly passed only scorer-labeled candidates. Revision 3 clarified the six scene descriptions without changing target labels or histories, and the runner now sends the full parsed remaining selectable set.

## Live run and evaluation

The final runner made six scene-parser calls, 60 base Context calls and eight predeclared repeat calls at temperature 0. The parser receives only natural-language scene text. The M30 engine receives the parsed scene, full accepted history, current generic task context and all remaining selectable candidate IDs. Evaluation labels are held outside both model requests.

The final run extracted all 48 labeled object mentions in six scenes, retained all 10 explicit colors and four explicit states, invented no unspecified colors or states, and returned exactly eight selectable objects per scene. All 60 Context points had complete candidate sets, selected-object removal, expected q dimensions and normalized q. Top-1 acceptable recall was 34/55 (61.8%); top-3 recall was 53/55 (96.4%). The model returned `ambiguous` at 3/60 points labeled ambiguous by the benchmark. Four history pairs had comparable full rankings; one pair changed ranking order, while no pair changed the top candidate. Five of eight repeat calls exactly reproduced status and ranking. Mean Context latency was 2.24 s (median 2.40 s, p90 3.23 s). See `M32_FINAL_REPORT.md` for limitations. Results are not tuned against the held-out M30 benchmark and do not modify M30 outputs.

## Boundaries

M32 uses no EEG, Quest, image input, live robot, or VLA dispatch. Image input is future work. The demonstration may display a final selection history but cannot start a robot action.
