# Initial prompt version — `m27-semantic-planner-prompt-v1`

This version was used for the first live run recorded in the M27 experiment.
It asked for one JSON object with status, summary, selected IDs, ordered
actions, VLA-ready language, assumptions, ambiguity reason, and alternatives.
It told the model not to invent objects or affordances and to return ambiguous
when more than one relation was plausible, but did not explicitly require
enumerating the selected-object relation graph. The API request left DeepSeek
thinking mode at its default.

V1 request parameters were temperature 0.0, `max_tokens=1200`, JSON-object
response format, streaming off, one correction retry, and a 45-second timeout.
The first live run was partial (6/14 predeclared outcomes matched). V2 records
the corrective iteration without changing benchmark expectations.
