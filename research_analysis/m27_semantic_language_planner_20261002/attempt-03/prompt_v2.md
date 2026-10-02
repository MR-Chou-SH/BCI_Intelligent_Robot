# Prompt contract — `m27-semantic-planner-prompt-v2`

The exact system message is embedded in `semantic_planner.py` in
`_system_prompt`. The JSON user message carries the M20 snapshot-derived
semantic scene, BCI selected IDs in order, allowed action vocabulary, optional
intent context, and validator errors for the single correction.

V2 keeps the V1 schema and frozen benchmark cases. It adds explicit
selected-object relation enumeration, conservative ambiguity handling when
more than one selected relation is supported, refusal to substitute an
available object for a missing named target, and the canonical multi-step
sequence for a known-closed container. The API request disables thinking mode
so the JSON response channel is the only generated content consumed.

Frozen request parameters:

- DeepSeek Chat Completions API
- temperature `0.0`
- thinking `{"type":"disabled"}`
- `max_tokens: 1200`
- `response_format: {"type":"json_object"}`
- streaming disabled
- one correction retry maximum
- 45 second request timeout

The local validator remains authoritative. After correction failure, the
planner emits a non-executable ambiguous result for multiple affordance-valid
selected relations, or an invalid result otherwise. No fallback plan is sent
to any robot or simulator dispatcher.
