# M32 Iteration Evidence

- `live-run-v1/` preserves the first completed live benchmark, its frozen inputs, summaries, exact runner/parser sources, and a SHA-256 manifest.
- `live-run-v2/` contains the second live run's raw JSONL. It is the same final run copied to the canonical `text_scene_sequence_results.jsonl` at the attempt root; the summaries beside it are an earlier scorer rendering of those same records.
- `live-run-v2-final/` and `live-run-v2-final-audit/` are successive summary-only recalculations of that same v2 JSONL after the history-sensitivity denominator and per-scene reporting were clarified. They are not additional model calls.
- The authoritative final summaries are the seven summary/CSV files at the attempt root. The final-audit directory also retains the focused regression and compile logs.
