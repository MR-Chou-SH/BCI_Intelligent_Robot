# M27 Semantic Language Planner — Experiment Spec

## Objective

Test whether an external chat model can compose affordance-grounded semantic
plans from ordered BCI object selections and a versioned scene, including
unseen object categories, while refusing unsupported relations and preserving
ambiguity. DeepSeek is an external pretrained component; it is not the research
contribution.

## Frozen boundary

- Input begins with the production M20 `M20SceneLayoutSnapshot` schema from
  `integration/m20_scene_layout_snapshot.py` and its current scene ID.
- The adapter copies stable semantic IDs, object labels/types, roles, source
  kinds, selectable/fixed-pose flags, table-local geometry, and affordance tags.
- Current articulated state is supplied separately only when observed. An
  absent state remains unknown and cannot authorize OPEN/CLOSE actions.
- The ordered BCI selection is preserved exactly. Optional text context can
  clarify intent but cannot add objects or override scene affordances.
- Structured actions are checked locally before an instruction is emitted.
- This experiment is offline planning only; it does not call the M9 dispatcher,
  MuJoCo execution, Quest, ND8, COM11, or a real VLA policy.

## Structured output and validation

The model returns `executable`, `ambiguous`, or `invalid`, an intent summary,
the exact selected IDs, ordered actions, a natural-language VLA instruction,
assumptions, ambiguity reason, and alternatives. The local validator checks
object identity, whitelist membership, affordance support, container/surface
relations, known open/closed state, selected source grounding, and alternatives.
One structured correction retry is permitted. A second failure produces a
non-executable `ambiguous` result with affordance-valid alternatives when the
selected IDs support multiple relations, otherwise an `invalid` result.

## Benchmark

`benchmark_cases.json` is frozen before live calls. It covers the two M20
acceptance examples, a competing-relation ambiguity case, surface placement,
press/container affordance conflicts, a nonexistent-target hallucination trap,
and unseen book/shelf and pen/drawer combinations. Core valid and ambiguous
cases run twice at temperature 0.0. Expected statuses and action relations are
scored programmatically; language quality is reviewed descriptively, not used
to weaken safety validation.

## Prompt iteration record

Prompt/schema and benchmark case expectations stayed frozen for both live
runs. Initial prompt v1 completed 14 calls but only 6 final plans passed the
local validator and predeclared expectation checks. Failures showed that
thinking output made JSON-only responses brittle, generic selection text let
the model choose the most salient of multiple selected destinations, and the
model sometimes substituted an existing target for a named missing target.
Prompt v2 disables model thinking, asks it to enumerate relations among the
selected IDs, and explicitly requires ambiguity or invalid output for those
cases. A generic local affordance-graph check enforces the same contract and
returns safe non-executable output after one failed correction. The benchmark
cases and expected outcomes were not changed.

Prompt v2's final outputs all passed, but its first responses needed correction:
12 responses used enum casing outside the schema, and both ambiguity responses
omitted the required natural-language field for each alternative. Prompt v3
states the exact enum casing and alternative object fields. The parser now
canonicalizes enum casing before validation and records each normalization.
The two earlier ambiguity fallbacks remain preserved in attempt-02; v3 reruns
the same frozen benchmark to measure whether they are still needed.

## Model and reproducibility

Resolve a configured model only if it appears in the live official model
catalog and the documented Chat Completions model set. Otherwise choose the
first available model in the frozen preference order. Record model ID, base
URL without credentials, prompt/schema versions, API request parameters,
timestamp, per-case latency, and returned token usage when available. Never
persist the API key, Authorization header, or raw request headers.

On 2026-10-02 the live official catalog listed `deepseek-flash` and
`deepseek-v4-pro`; `deepseek-flash` was resolved because no model override was
configured. Official references:

- [DeepSeek model catalog](https://api-docs.deepseek.com/api/list-models/)
- [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek model pricing and versions](https://api-docs.deepseek.com/quick_start/pricing/)

## Success interpretation

Report per-case schema/grounding/action/affordance/expected-relation/ambiguity
results, repeat agreement, latency, token usage, retry rate, and transport
failures. The experiment supports semantic-generalization claims only for the
synthetic benchmark cases that actually pass; it does not establish robot
execution success, VLA performance, or safety in physical scenes.
