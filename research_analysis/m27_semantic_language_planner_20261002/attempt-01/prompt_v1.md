# Prompt contract — `m27-semantic-planner-prompt-v1`

The exact system message is embedded in `semantic_planner.py` (`_system_prompt`).
The user message carries a JSON object with the versioned semantic scene, the
ordered BCI selected IDs, allowed action vocabulary, and optional intent
context. A correction request may include the previous structured response and
validator errors. The model is asked to return JSON only with the keys in
`structured_plan_schema.json`.

Frozen request settings for the first benchmark:

- Chat Completions API
- temperature: `0.0`
- `max_tokens`: `1200`
- `response_format`: `{"type":"json_object"}`
- `stream`: `false`
- maximum correction retries: `1`
- timeout: `45` seconds

The model proposes semantic plans only. No generated result is sent to the
MuJoCo dispatcher, Unity, or a physical robot.
