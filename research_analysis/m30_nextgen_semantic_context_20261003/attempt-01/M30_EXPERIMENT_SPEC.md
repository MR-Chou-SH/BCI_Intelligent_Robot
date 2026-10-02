# M30 Experiment Specification

## Question and scope

After each accepted selection, can the M30 semantic engine use the structured scene, explicit object state, full accepted selection history and relational affordances to produce an uncalibrated prior over the next selection? Separately, does measured held-out semantic quality transfer safely to frozen M25 adaptive EEG replay?

This is software-only research. It does not use Quest, ND8, COM11, raw EEG, a physical robot, or real VLA dispatch. M30 generates no VLA instruction. Historical EEG transfer is a simulation because the 88 randomized historical trials do not have prospective semantic-scene labels.

## Frozen benchmark and split

- 33 episodes and 126 next-selection decisions.
- Eight scene families with family-level splits: train 14 episodes / 56 decisions; dev 6 / 24; held-out 13 / 46.
- Train: desktop/office and storage/organization. Dev: household cleaning and tools/simple manipulation. Held-out: kitchen/food, assistive handover, state-dependent and ambiguous/adversarial.
- Human-authored deterministic templates in `build_m30_benchmark.py` defined expected targets, relations, ambiguity, invalidity and task completion before any model evaluation.
- `semantic_context_sequence_benchmark_lock.json` freezes benchmark SHA-256 `899aae6febb96cb6a63f0589fd7ad8e5bd0b317e97ac81de23c71ff062d237b0`. The model receives only `model_input`; `evaluation_only` is scorer-only. The read-only validator checks prohibited evaluation fields recursively.
- Explicit states include open/closed, free/occupied, full/available, blocked/unavailable, absent required tool, completed task, and equally plausible destinations.

## Semantic Context method

The model ranks every remaining selectable object ID once. It can report `informative`, `ambiguous`, `context_off`, or `invalid`; ambiguous/invalid/completed cases are never forced active. Ordinal 0..1 semantic scores are transformed using the frozen softmax temperature 0.20. The resulting variable-dimensional `q_global` is uncalibrated prior-like evidence, not a probability.

Candidates are paginated in stable scene order, three per page. Each `q_page` contains only candidates on that page and is renormalized. A last page of one or two real candidates is preserved without a fake semantic object. Page projection is an interface adapter and does not mutate global order or selection history.

Coverage gates C65/C85/C100 use only informative, semantically eligible train/dev predictions. A predeclared confidence score combines top semantic score and top-versus-second score margin equally. The fixed gate threshold for each operating point is chosen from train/dev confidence distributions, with C100 including all eligible train/dev cases. Held-out participation is measured as realized and can differ from the target; ambiguous, invalid and completed decisions remain off.

Report overall and eligible participation, active precision, top-1 and top-k recall, relation-type accuracy, ambiguity/completion/invalid handling, wrong-active count, entropy, prior top mass, score margin, repeatability, API latency, correction rate, hallucinated-candidate rate and state contradiction rate by split, scene family and round.

## EEG quality-transfer simulation

Reuse the frozen M25 88-trial cohort and the unchanged FAST, MEDIUM and CONSERVATIVE stopping parameters. Re-verify the M25 manifest and M23 operating-point sidecar before and after replay. The lambda sweep is `0, 0.5, 1.0, 1.5, 2.0`.

For each Context prior, fusion is `Normalize(E_i * q_page_i**lambda_ctx)`. Lambda 0 is an exact EEG-only ablation. Context can accelerate only when its page prior top agrees with raw EEG top, q-page top mass meets the frozen 0.70 guard, and frozen EEG evidence, margin and stability requirements hold. The paired EEG-only stop is a hard cap; absent, ambiguous, invalid, late, page-incompatible or disagreeing Context uses exact EEG-only fallback. The selected class remains the raw EEG top.

The primary matrix is 3 coverage OP × 5 `0, 0.5, 1.0, 1.5, 2.0` values × 3 frozen EEG OPs, using precomputed Context. A smaller timing sensitivity compares measured API arrival, 0.2 s and 0.4 s. Use deterministic seeded assignments over the held-out semantic predictions. When mapping measured semantic errors onto historical class labels, preserve the observed page-level top-1 correctness/error offset and q-page vector. This mapping is simulation machinery only and will be reported explicitly.

Report per trial, session and condition: accuracy/delta, all-trial and Context-active gain, accelerated/unchanged/delayed counts, assignment/authorization/application rates, wrong early stops, Context-caused errors and no-delay violations. A condition is supported only with no accuracy loss, zero Context-caused wrong stops, zero no-delay violations, frozen gates and held-out semantic support. No result is called prospective.

## Reproducibility and interpretation

All stochastic transfer mapping uses fixed published seeds. Thresholds are frozen before held-out scoring. No lambda or gate is selected on held-out results. The M25/M23/M24 inputs and all historical analysis outputs remain read-only. Performance claims must retain actual negative or null results and separate measured semantic quality from synthetic transfer assumptions.

### Portable transfer artifact

The complete `eeg_transfer_per_trial.csv` remains in the working tree. It is 176,573,307 bytes, so the Git checkpoint stores the deterministic lossless `eeg_transfer_per_trial.csv.gz` representation instead of adding a single file above GitHub's normal per-file limit. `compress_m30_transfer.py` creates this representation without touching the original and verifies the decompressed SHA-256 byte-for-byte. The M30 validator streams the compressed form when it is present; decompress it to the required `.csv` path if a plain CSV is needed.
