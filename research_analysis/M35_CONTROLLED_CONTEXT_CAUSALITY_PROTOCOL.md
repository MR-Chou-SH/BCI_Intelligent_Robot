/goal M35 — Controlled Context Causality for High-Speed SSVEP Decoding

PROJECT
Repository:
C:\Users\zsh21\Desktop\BCI_Intelligent_Robot

BACKGROUND

M34 established a frozen EEG-only baseline:
- strongest decoder: three-band FBCCA
- channels [2,3,4,5,7]
- H=3
- dynamic stopping average EEG evidence:
  B2 ≈ 0.412 s
  S7 ≈ 0.497 s
- short-window heldout accuracy approximately 90–93%
- real M33 Context sometimes accelerated decisions, but Context application was sparse and wrong early stops remained.

M35 is NOT another generic Context benchmark.

M35 asks a controlled mechanistic question:

1. If Context is CORRECT and agrees with the true EEG intention, how much can it reduce EEG evidence duration?

2. If Context is WRONG and conflicts with the true EEG intention, can a sufficiently strong EEG signal prevent Context from changing the final decoded intention?

The goal is to isolate the causal effect of Context from the imperfect semantic accuracy of M33.

==================================================
PRIMARY HYPOTHESES
==================================================

H1 — Congruent Context acceleration

For the same EEG trial:

Correct Context
+
EEG evidence
should allow earlier safe stopping than EEG-only.

Target:
approximately 150–250 ms mean gain among Context-assisted trials,
with ~200 ms as the desired central target.

Do not force this value.

H2 — Incongruent Context robustness

When Context points to the wrong class:

Context must NEVER directly replace or rerank the EEG winner.

If EEG evidence becomes sufficiently strong and stable,
the final emitted class should remain the EEG-derived class.

Wrong Context should therefore either:
- have no effect,
- delay Context authorization,
- or fall back to EEG-only,

rather than force an incorrect output.

H3 — Neutral Context equivalence

Neutral/uninformative Context should reproduce EEG-only output and stopping behavior within exact or negligible tolerance.

==================================================
IMPORTANT DISTINCTION
==================================================

This is a CONTROLLED MECHANISTIC / ORACLE ANALYSIS.

Synthetic Context labels may deliberately use the known EEG ground-truth label.

That is allowed ONLY because the purpose is to measure the theoretical effect of correct and incorrect Context.

Do NOT describe congruent synthetic Context as a real semantic prediction.

Clearly label all such results as:

- controlled congruent Context
- controlled incongruent Context
- neutral Context
- mechanistic/oracle replay

NOT:
- real M33 accuracy
- prospective Context performance
- online validation

==================================================
DATA USAGE
==================================================

Use the existing M34 data manifest and frozen EEG pipeline.

Do not modify raw EEG.

Preferred datasets:
A
B1
B2
S7

Important:
B2 and S7 were already consumed as M34 heldout sets.

Therefore:

- A/B1 may be used for design/tuning of M35 Context stopping rules.
- B2/S7 may be used only as POST-HOC MECHANISTIC REPLAY / descriptive confirmation.
- Do NOT claim B2/S7 are fresh heldout for M35.
- Do NOT tune parameters on B2/S7.

Use exactly the same EEG trial waveform when comparing:
EEG-only
vs Congruent Context
vs Incongruent Context
vs Neutral Context.

Every comparison should be paired trial-by-trial.

==================================================
FREEZE EEG-ONLY
==================================================

Do NOT retune the EEG decoder.

Reuse the M34 frozen EEG-only stack unless a reproducibility bug is discovered.

Preserve:
- selected FBCCA configuration;
- preprocessing;
- channel selection;
- harmonic count;
- dynamic evidence schedule;
- EEG score computation.

M35 studies Context effects, not a new EEG decoder search.

If M34 EEG-only cannot be reproduced exactly or within explainable numerical tolerance, stop and debug before proceeding.

==================================================
CONTEXT CONDITIONS
==================================================

For every EEG trial with true class y_true ∈ {0,1,2}, construct four controlled conditions.

A. EEG_ONLY

No Context.

This is the exact M34 dynamic stopping baseline.

B. CONGRUENT_CONTEXT

Synthetic Context top class:

y_ctx = y_true

Construct a Context prior over the 3 EEG classes.

Sweep Context strength, for example:

q_top ∈ {
0.45,
0.50,
0.55,
0.60,
0.65,
0.70,
0.75,
0.80,
0.85,
0.90,
0.95
}

The remaining mass should be divided equally across the two other classes unless a better symmetric formulation is justified.

Example:

q_top = 0.80

then:

correct target = 0.80
wrong class 1 = 0.10
wrong class 2 = 0.10

C. INCONGRUENT_CONTEXT

Synthetic Context points to a WRONG target.

For every trial, test BOTH possible wrong classes separately.

If y_true = 0, evaluate:
ctx = 1
ctx = 2

Do not randomly choose only one wrong class.

Sweep the same Context strengths as above.

D. NEUTRAL_CONTEXT

Use:

q = [1/3, 1/3, 1/3]

or an equivalent explicit Context-off condition.

Neutral Context should behave like EEG-only.

==================================================
CORE SAFETY RULE
==================================================

Context may influence STOPPING CONFIDENCE / STOPPING THRESHOLD.

Context must NOT directly replace the EEG class decision.

At every time t:

final_class(t) = argmax EEG_score(t)

NOT:

final_class(t) = argmax fused_EEG_plus_Context_score

unless such a fusion is evaluated only as an explicitly labelled unsafe/ablation baseline.

The main M35 algorithm must preserve:

Context = accelerator
EEG = classifier

This is essential.

==================================================
WHY WRONG CONTEXT IS STILL DANGEROUS
==================================================

Even if Context cannot rerank EEG, a wrong Context may still cause an incorrect early stop when short-window EEG is temporarily wrong.

Example:

true class = A

at 0.20 s:
EEG top = B
Context top = B

If Context lowers the stopping threshold too aggressively,
the system could stop early on B.

Therefore explicitly measure:

Context-induced wrong early stop

defined as:

EEG-only eventually produces the correct class,
but Context-assisted stopping terminates earlier with the wrong class.

This is one of the PRIMARY safety metrics.

==================================================
CONTEXT-ASSISTED STOPPING DESIGN
==================================================

Design Context as a modifier of stopping confidence.

Explore bounded, interpretable mechanisms on TRAIN/DEV only.

Possible mechanisms include:

1. Threshold reduction when:
   Context top == current EEG top

2. Required EEG margin reduction proportional to Context confidence

3. Required temporal stability reduction by one step when Context is highly confident

4. Bayesian / log-prior-style confidence adjustment ONLY for stop authorization,
   while final emitted class remains raw EEG top

5. Confidence multiplier / evidence bonus

6. Learned or calibrated probability that:
   P(EEG top is correct | EEG features, Context agreement)

Do NOT create an opaque large neural model unless clearly justified.

Prefer simple, interpretable, reproducible rules.

==================================================
EEG FEATURES AVAILABLE FOR STOP AUTHORIZATION
==================================================

Potential features may include:

- current EEG top score
- second score
- absolute margin
- relative margin
- score ratio
- score entropy
- number of consecutive stable top predictions
- evidence duration
- change in top score over recent windows
- class-specific score trajectory
- Context strength
- Context agreement/disagreement
- Context entropy
- page mass if relevant

Do not use true label as an input to the stopping model except to construct the synthetic experimental condition.

Ground truth is for evaluation only.

==================================================
EVIDENCE SCHEDULE
==================================================

Reuse or extend the M34 evidence grid.

At minimum evaluate:

0.20
0.25
0.30
0.35
0.40
0.50
0.60
0.80
1.00 s

Do not count the 0.5 s onset guard as EEG evidence duration.

Primary latency metric for M35 is:

EEG evidence duration

not:
nominal onset-relative time.

You may still report nominal time separately for completeness.

==================================================
ONSET GUARD SIDE ANALYSIS
==================================================

Because the user believes the 0.5 s guard is not central to decoding speed, perform a small exploratory guard sensitivity analysis on A/B1 only.

Test approximately:

guard = 0.0 / 0.1 / 0.3 / 0.5 s

Do NOT optimize M35 Context using B2/S7 guard results.

Report whether EEG evidence accuracy materially changes.

This is secondary.

Do not let guard analysis consume most of M35 runtime.

==================================================
PRIMARY METRICS
==================================================

For each Context condition and strength report:

1. Accuracy
2. Mean EEG evidence duration
3. Median EEG evidence duration
4. p90 evidence duration
5. Mean paired latency gain vs EEG-only
6. Median paired latency gain
7. Fraction stopping <= 0.20 s
8. Fraction stopping <= 0.25 s
9. Fraction stopping <= 0.30 s
10. Fraction stopping <= 0.40 s
11. Fraction stopping <= 0.50 s
12. Wrong early stops
13. Context-induced wrong early stops
14. Exact EEG-only fallback rate
15. Context application rate

For congruent Context additionally report:

gain among Context-applied trials

because this directly measures:
"when Context is useful, how much does it accelerate?"

==================================================
PRIMARY RESEARCH TARGETS
==================================================

Priority 1:

Correct Context should produce approximately ~200 ms average acceleration among Context-applied trials if the EEG evidence supports it.

Priority 2:

Overall accuracy should remain approximately around the strongest attainable level.

Target ~95% if supported by the data,
but do NOT force 95%.

Priority 3:

Wrong Context must not materially degrade final accuracy.

Ideal:

Context-induced wrong early stops = 0

or as close to zero as possible.

Priority 4:

Neutral Context should be identical to EEG-only.

==================================================
CONTEXT STRENGTH TRADEOFF
==================================================

Produce a full tradeoff curve:

Context strength
vs
latency gain
vs
accuracy
vs
wrong early stop rate

The desired operating point is NOT necessarily the strongest q_top.

Find the Pareto region where:

- congruent Context gives large acceleration;
- incongruent Context causes negligible harm;
- accuracy remains stable.

Do not select only by latency.

==================================================
ORACLE UPPER BOUND
==================================================

Compute an explicit Oracle Context upper bound.

Assume Context target is always correct.

Test the earliest theoretically safe stopping rule under several constraints:

A. EEG top must equal oracle Context top
B. optionally minimum EEG margin
C. optionally one/two consecutive stable EEG tops

Measure:

T_EEG_ONLY
T_ORACLE_CONTEXT

and:

G_ORACLE = T_EEG_ONLY - T_ORACLE_CONTEXT

Report:

- overall oracle gain
- gain among accelerated trials
- fraction <= 300 ms
- maximum theoretically reachable gain under the current EEG trajectories

This analysis should answer:

Is ~200 ms Context acceleration even feasible with the current EEG data?

If Oracle itself cannot provide ~200 ms,
do not keep optimizing Context blindly.

==================================================
INCONGRUENT STRESS TEST
==================================================

For every EEG trial:

test both wrong Context classes
across all Context strengths.

Important analysis:

When EEG is initially wrong but later becomes correct:
does wrong Context reinforce the temporary error?

Identify all such dangerous trajectories.

For each Context-induced error save:

- trial ID
- true class
- wrong Context class
- Context strength
- EEG score trajectory
- EEG top sequence
- stopping time
- EEG-only final outcome
- Context-assisted outcome

These failure cases are extremely important.

==================================================
STATISTICAL ANALYSIS
==================================================

Use paired statistics because every condition uses the same EEG trial.

Report uncertainty.

At minimum consider:

- bootstrap 95% CI for mean latency gain
- paired permutation or Wilcoxon-style test for latency reduction
- McNemar or paired categorical comparison for accuracy differences
- exact counts for Context-induced wrong stops

Do not overclaim statistical significance with small samples.

Clearly distinguish:
A/B1 development
from
B2/S7 post-hoc mechanistic confirmation.

==================================================
REAL M33 CONTEXT COMPARISON
==================================================

After the controlled Context analysis is complete, compare the results against M34's REAL/PRECOMPUTED M33 Context replay.

Do NOT retune M33 using B2/S7.

Create a comparison:

EEG-only
vs
Oracle Congruent Context
vs
Controlled Safe Context
vs
Real M33 Context replay

This should reveal the gap between:

theoretical Context value
and
current semantic implementation.

==================================================
REQUIRED FIGURES
==================================================

Create at least these figures:

1. Mean EEG evidence duration:
   EEG-only vs Congruent vs Incongruent vs Neutral

2. Context strength vs mean latency gain

3. Context strength vs accuracy

4. Context strength vs Context-induced wrong early stops

5. Fraction <=300 ms vs Context strength

6. Oracle vs real Context acceleration

7. Representative EEG score trajectories for:
   - successful congruent acceleration
   - safe incongruent rejection
   - dangerous wrong-Context early-stop case

==================================================
ARTIFACT DIRECTORY
==================================================

Create:

research_analysis/
  m35_controlled_context_causality_20261004/
    attempt-01/

Save:

- m35_protocol.json
- eeg_only_reproduction.json
- controlled_context_results.csv
- controlled_context_per_trial.csv
- oracle_context_summary.json
- incongruent_failure_cases.csv
- context_strength_tradeoff.csv
- statistical_summary.json
- guard_sensitivity.csv
- plots/
- M35_FINAL_REPORT.md

==================================================
FINAL REPORT MUST ANSWER
==================================================

1. What is the frozen EEG-only mean/median/p90 evidence time?

2. With perfectly congruent Context:
   how much does mean evidence time decrease?

3. Is ~200 ms mean gain achievable:
   a. overall?
   b. among Context-applied trials?

4. What proportion of trials can safely finish <=300 ms?

5. How does gain vary with Context strength?

6. Does stronger Context eventually hurt accuracy?

7. Under wrong Context:
   does final accuracy remain approximately EEG-only?

8. How many Context-induced wrong early stops occur?

9. Under what EEG trajectory do those failures occur?

10. Can a safe gate eliminate wrong Context failures without destroying most of the acceleration?

11. Does Neutral Context reproduce EEG-only?

12. What is the Oracle upper bound?

13. How far is current M33 Context from Oracle?

14. Is the bottleneck:
    - EEG trajectory,
    - Context semantic accuracy,
    - Context calibration,
    - stopping gate,
    - or insufficient data?

15. Does the evidence support the intended scientific claim:

"Congruent semantic Context significantly accelerates SSVEP decoding, while incongruent Context does not override sufficiently strong EEG evidence."

If not, state exactly why.

==================================================
SCIENTIFIC CLAIM BOUNDARY
==================================================

Allowed claim if supported:

"Under controlled replay, task-consistent Context reduced the EEG evidence required for decision, while a conservative gating policy prevented most/all task-inconsistent Context from overriding the EEG-derived class."

Do NOT claim:
- real-time online Context validation
- prospective EEG/Context validation
- population-level generalization
- true semantic Context accuracy from oracle experiments

==================================================
SOFTWARE / SAFETY
==================================================

Software only.

Do NOT:
- open COM11
- use ND8
- use Quest
- use ADB
- collect EEG
- control a robot
- dispatch VLA
- modify raw EEG
- alter unrelated project files

==================================================
GIT
==================================================

Create branch:

codex/m35-controlled-context-causality

Preserve unrelated dirty/untracked files.

No:
git reset --hard
git clean
force push
git add .
git add -A

Selectively stage M35 files only.

Run compile/tests/audit.

Commit and push.

Verify remote hash.

==================================================
AUTONOMOUS LONG-RUN RULE
==================================================

Treat M35 as a closed-loop research task.

Do not stop after the first experiment.

After each major result:

1. compare against H1/H2/H3;
2. identify the limiting factor;
3. choose the next scientifically justified software experiment;
4. execute it;
5. keep or reject the change based on A/B1 evidence;
6. repeat.

Do not ask the user to choose among software-only next steps if the evidence supports a rational choice.

Do not tune against B2/S7.

Stop only when:

A. the controlled Context mechanism is fully characterized;

or

B. further progress requires new EEG / prospective data / hardware;

or

C. further tuning would violate the frozen evaluation boundary.

==================================================
PRIORITY ORDER
==================================================

1. Correct experimental design
2. Congruent Context acceleration
3. Protection against incongruent Context
4. Accuracy
5. Coverage/application rate
6. Statistical characterization
7. Engineering cleanup

Do not optimize for pretty numbers.

==================================================
COMPLETION SUMMARY
==================================================

At completion print:

EEG-ONLY MEAN EVIDENCE:
EEG-ONLY ACCURACY:

BEST CONGRUENT CONTEXT STRENGTH:
CONGRUENT MEAN EVIDENCE:
CONGRUENT MEAN GAIN:
CONGRUENT APPLIED-TRIAL GAIN:
CONGRUENT ACCURACY:
<=300MS FRACTION:

BEST SAFE INCONGRUENT RESULT:
INCONGRUENT ACCURACY:
CONTEXT-CAUSED WRONG EARLY STOPS:

NEUTRAL == EEG-ONLY:
ORACLE MEAN EVIDENCE:
ORACLE MEAN GAIN:

~200MS GAIN FEASIBLE:
95% ACCURACY FEASIBLE WITH CURRENT DATA:

MAIN BOTTLENECK:
NEXT RECOMMENDED ACTION:

FINAL COMMIT:
REMOTE HASH VERIFIED: