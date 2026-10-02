# Prompt contract — `m27-semantic-planner-prompt-v3`

V3 preserves the M27 v1/v2 task boundary, scene schema, structured action
schema, benchmark cases, and predeclared expected outcomes. It states the
required exact enum casing and the three required fields for every ambiguous
alternative. The adapter normalizes only enum casing (`status` and action
`type`) before validation; it records each normalization and does not alter
object IDs, selected order, actions, or relation targets.

The request continues to disable thinking mode at temperature 0.0. A bounded
correction is still used for missing or invalid fields. The affordance graph
remains the final local authority and any fallback stays non-executable.
