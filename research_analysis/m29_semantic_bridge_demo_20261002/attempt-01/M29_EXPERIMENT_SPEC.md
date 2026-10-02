# M29 Experiment Specification — Standalone Semantic Language Bridge

Version: 1.0
Benchmark frozen before live model evaluation: `2026-10-02T10:28:25Z`
Frozen benchmark SHA-256: `ca1ed24dae3a8321952855a590ee011fff05e48ef1a872ab15fa1efa1f3d2b3c`

## Question and scope

Evaluate the shared semantic scene core as a standalone Chinese/English noun-to-structured-plan-to-VLA-language bridge. This is a software benchmark over curated M20-like and synthetic scene snapshots. It is not user-intent accuracy or physical-scene evidence. No Quest, robot, EEG, or ND8 integration occurs here.

## Modes

- `strict`: resolve nouns against the supplied scene catalog and preserve stable scene object IDs. Unknown or ambiguous nouns fail closed.
- `free`: if nouns are not in the scene catalog, a separate bounded model call may infer provisional categories and affordance tags. Every such object is labeled `model_inferred_unverified`; all outputs keep `dispatch_allowed=false`. The inferred facts are never represented as observation or robot authority.

Structured actions are validated against the shared M27/M29 affordance, state, object-ID, action-sequence, and selected-object rules before bilingual imperative rendering. One correction retry is bounded for model-produced plans and free-noun inference.

## Frozen benchmark

The benchmark contains 43 cases: 16 current M20-like cases, 21 held-out synthetic cases, and 6 exploratory free-noun cases. Expected statuses/actions live only in `evaluation_only`; only `model_input` reaches the bridge. Free-noun cases evaluate schema, unverified labels, validator behavior, and disabled dispatch; they do not score semantic truth.

## Scoring

Report status accuracy, exact expected action-sequence rate, validator pass/rejection rates, ambiguity and invalid-input behavior, deterministic grounding agreement, free-noun inference/schema safety, dispatch prohibition, structural plan repeatability, exact prose repeatability separately, model latency, bounded retry counts, token usage, and API failures. Repeated-call structural consistency does not require identical prose.

## Model and secret controls

Use the shared M27 environment-only DeepSeek client. Resolve the model from the official available-model endpoint unless `DEEPSEEK_MODEL` is configured. Do not print or persist `DEEPSEEK_API_KEY`, Authorization data, prompts, or raw API responses. Persist only validated bridge outputs and benchmark evaluation records.

The benchmark can be rerun only in a new attempt directory; JSONL observations in this attempt are append-only.
