# M20 Overnight GOAL
## Daily Assistive Desk Scene + New-Scene Full-Chain Regression + Context/EEG Acceleration Study

Repository:
`C:\Users\zsh21\Desktop\BCI_Intelligent_Robot`

Primary Unity project:
`m7_unity6000` (current M7/M9/M19 Unity project)

Reference image:
`M20_ASSISTIVE_DESK_REFERENCE.png`

---

# 0. Mission

Tonight is one long-running campaign with three strictly ordered stages:

1. **Task 1 — Build the new Daily Assistive Desk scene**
   - Unity/Quest and MuJoCo must model the same desk, objects, dimensions, orientation and relative positions.
   - The old block scene must remain intact.
2. **Task 2 — After Task 1 passes, prove the existing M19 end-to-end chain still works in the new scene**
   - synthetic EEG only
   - paged SSVEP selection
   - Previous / Next / Undo Last / Submit
   - context/affordance
   - MuJoCo pick-and-place execution
3. **Task 3 — Only after Task 1 + Task 2 pass, use the legacy block scene and historical EEG to investigate and strengthen Context-assisted decoding**
   - analysis/replay only
   - no new human EEG
   - do not deploy experimental V3/Context logic into the production M19 runtime tonight

This is intended to be a **Long-run / Execute** task.

Do not stop after:
- an audit,
- a plan,
- compilation,
- one passing unit test,
- or one screenshot.

Continue through implementation, focused validation, repair and re-validation until the stated acceptance criteria pass, unless there is a genuine external human/hardware blocker that cannot be solved in software/UI.

Do not ask for confirmation between ordinary engineering steps.

---

# 1. Frozen boundaries / invariants

Preserve these existing contracts unless this GOAL explicitly says otherwise:

- Demo and Research remain separate modes.
- Do not modify tomorrow's formal Research acquisition protocol.
- Do not modify the currently accepted Research audio-cue changes.
- Do not open real ND8 / COM11 tonight.
- Do not collect new human EEG tonight.
- SSVEP class mapping remains:
  - slot 0 = 7.2 Hz
  - slot 1 = 9.0 Hz
  - slot 2 = 12.0 Hz
- Never infer SSVEP class from object color.
- Quest/current page frozen snapshot remains authoritative for slot → TargetId resolution.
- max active SSVEP page size remains 3.
- Previous / Next / Undo Last / Submit must not regress.
- stale results must fail closed.
- global user selection order must remain preserved.
- the old block scene and old task definitions must stay runnable for Task 3.
- do not redesign the robot controller if the current M9 path can be extended with a scene/action adapter.
- tonight's robot execution scope is **pick-and-place only**.
- do not implement robot press/open/close tonight.
- do not turn this into a dual-arm project.

Before editing:
1. record current git commit;
2. record `git status`;
3. preserve all existing user changes;
4. create an M20 overnight run/evidence directory using repository conventions.

---

# 2. Reference layout and shared coordinate convention

The user reference image defines the semantic layout.

From the user's viewpoint facing the desk:

- rear/top center: **robot arm**
- far/middle object row:
  - left: **small medicine box**
  - center: **hinged storage box**
  - right: **phone**
- nearer row:
  - left: **button/switch**
  - right: **wireless charging pad**
- front: large centered **USER ZONE**

The reference image is authoritative for relative arrangement.

## 2.1 Canonical table-local frame

Create one explicit table-local coordinate convention shared by Unity and MuJoCo:

- unit = meter
- origin = tabletop center
- +X = user's visual right
- +Y = away from user / toward robot
- +Z = upward from tabletop

If Unity/MuJoCo native conventions differ, use exactly one documented transform adapter.
Do not hand-maintain different mirrored coordinates.

**LEFT/RIGHT MIRRORING IS A HARD FAILURE.**

Required sign/topology assertions:
- medicine box: X < 0
- button: X < 0
- phone: X > 0
- charging pad: X > 0
- storage box approximately centered
- USER ZONE on the user/front side (smaller Y)
- robot behind/farther than the object rows

Use automated sign/topology checks plus visual screenshots.

---

# 3. One canonical assistive-scene specification

Create one authoritative scene specification consumed by both Unity and MuJoCo, or generate both runtime representations from one source.

Do **not** create two unrelated hand-edited object tables.

The canonical spec should contain at least:

- stable semantic ID / TargetId
- label
- selectable boolean
- source / destination / prop role
- table-local position in meters
- dimensions in meters
- yaw/orientation
- geometry/collider information
- movable/fixed status
- grasp metadata if applicable
- placement metadata if applicable
- affordance/context tags
- articulation metadata for storage-box hinge and button travel

Recommended IDs:

- `assist_medicine_box`
- `assist_storage_box`
- `assist_phone`
- `assist_button_switch`
- `assist_wireless_charger`
- `assist_user_zone`

The robot arm retains the existing stable robot identity/path.

---

# 4. Realistic object dimensions

Use realistic household scale. The values below are the baseline contract unless existing robot/table geometry requires a small, explicitly documented adjustment.

## 4.1 Small medicine box

Approximate external dimensions:

- X size: 0.100 m
- Y size: 0.060 m
- Z size: 0.030 m

Requirements:
- rigid movable object in MuJoCo
- Unity visual and collider dimensions match
- stable resting pose
- practical grasp location
- no gripper/table interpenetration

## 4.2 Hinged storage box

Base approximate outer dimensions:

- 0.180 m × 0.120 m × 0.075 m

Lid:

- 0.180 m × 0.120 m × 0.008 m

Requirements:
- base and lid are separate articulated bodies
- real hinge/pivot, not fake visual-only rotation
- hinge located along the rear edge
- hinge axis/range identical in Unity and MuJoCo
- closed position approximately 0°
- open target approximately 100° (reasonable ~95–110° acceptable)
- lid sits flush in closed state
- no lid/base interpenetration
- open state does not clip through base/table
- a software/UI/manual articulation test must demonstrate opening and closing

Robot does NOT need to open it tonight.

## 4.3 Phone

Approximate dimensions:

- 0.150 m × 0.072 m × 0.008 m

Requirements:
- physically plausible flat smartphone geometry
- stable on table
- stable on charging pad
- practical grasp pose
- collision thickness may be slightly enlarged only if needed for stable MuJoCo collision; document any difference and keep visual geometry realistic

## 4.4 Wireless charging pad

Approximate dimensions:

- 0.095 m × 0.095 m × 0.012 m

Requirements:
- fixed/static destination
- phone placement target centered on pad
- phone final pose must not hover or intersect

## 4.5 Button/switch

Base:

- approx. 0.110 m × 0.060 m × 0.022 m

Button cap:
- approx. 0.024–0.030 m diameter

Visible button travel:
- approx. 0.005 m

Requirements:
- actual vertical travel/depression state
- MuJoCo: slide/prismatic DOF or equivalent physically meaningful articulation
- Unity: matching visible pressed/unpressed height
- pressing must be visually obvious
- pressing/releasing must not pass through the base

Robot does NOT need to press it tonight.

## 4.6 USER ZONE

Approximate footprint:
- 0.46 m × 0.18 m

Requirements:
- front-center tabletop region
- shallow/flush surface marker
- must not form a wall that blocks robot placement
- selectable destination for tonight's source→destination interaction if compatible with current M19 queue
- deterministic safe placement poses for medicine box and phone

---

# 5. Recommended relative layout

Preserve the reference image topology first; adapt exact values only to fit the actual existing M9 table/reach envelope.

Initial table-local centers:

- medicine box:       X ≈ -0.24, Y ≈ +0.10
- storage box:        X ≈  0.00, Y ≈ +0.10
- phone:              X ≈ +0.24, Y ≈ +0.10
- button/switch:      X ≈ -0.24, Y ≈ -0.03
- charging pad:       X ≈ +0.24, Y ≈ -0.03
- USER ZONE:          X ≈  0.00, Y ≈ -0.20

Do not blindly paste these if the actual tabletop differs.
Preserve:
1. left/right,
2. far/near row ordering,
3. adequate separation,
4. robot reachability,
5. collision-safe grasp/place paths.

Prefer keeping the already accepted M9 table / robot-anchor / global workspace placement and changing the tabletop contents beneath that frame.

---

# 6. TASK 1 — Build the new scene in Unity/Quest + MuJoCo

## 6.1 Preserve the legacy scene

The old block scene must remain available and unchanged enough to reproduce prior tests and Task 3 analysis.
Create a new scene/profile/config for the assistive desk.

## 6.2 Unity/Quest implementation

Reuse current M9/M19 mechanisms wherever practical.

Required:
- new assistive tabletop contents
- same semantic IDs as MuJoCo
- correct scale
- correct relative position
- correct left/right orientation
- correct colliders/bounds
- storage-box hinge can animate/open/close
- button visibly depresses/releases
- USER ZONE clearly visible
- no mirrored objects/text
- no accidental negative-scale mirror
- M19 paged-selection UI remains functional

Codex is allowed to operate Unity Editor UI/mouse directly.

Use:
- Scene view
- Game view
- Play Mode
- existing EditMode tests
as appropriate.

Take at least:
- one top-down Unity screenshot
- one perspective Unity screenshot showing robot + all objects

If Quest/ADB is available, build/run the new scene and use PC-side Quest mirroring if available.
Do not block the entire task merely because a headset-only visual judgment is impossible; clearly mark any remaining physical-Quest-only check.

## 6.3 MuJoCo implementation

Use the same canonical scene spec.

Required:
- correct collision geometry
- correct table height
- movable medicine box
- movable phone
- articulated storage lid
- articulated button
- fixed charger
- fixed USER ZONE marker/target
- existing robot/control model retained
- reachable pick/place poses

Take:
- one top-down MuJoCo screenshot
- one perspective MuJoCo screenshot

## 6.4 Cross-engine geometry parity

Create a machine-readable parity check.

For every shared scene entity verify:
- same semantic ID
- position difference target ≤ 5 mm
- dimension difference target ≤ 5 mm or ≤ 2%, whichever is looser
- yaw/orientation difference ≤ 2°
- left/right sign agreement
- near/far ordering agreement
- hinge axis/range agreement
- button travel agreement

Also verify:
- nothing starts below the tabletop
- no initial object interpenetration
- storage lid closed/open states are collision sensible
- phone-on-charger pose is collision safe
- medicine/phone grasp poses are reachable
- user-zone target poses are reachable

### Task 1 PASS gate

Task 1 cannot be marked PASS until:
1. Unity scene is implemented;
2. MuJoCo scene is implemented;
3. automated parity checks pass;
4. left/right mirror check passes;
5. hinge works;
6. button depression works;
7. screenshots/evidence are saved.

Only after Task 1 PASS may Task 2 begin.

---

# 7. TASK 2 — New-scene full-chain regression

Use **synthetic/simulated EEG only**.
Do not open ND8.

Goal:

`synthetic EEG → frozen current-page slot mapping → Quest/UI selection → ordered queue → Context → Submit → action resolver → MuJoCo robot execution`

The purpose is to prove that replacing blocks with the assistive scene did not break already accepted infrastructure.

## 7.1 Visual-order candidate assignment

The required semantic visual order is:

**far → near**, and inside a comparable row **left → right**.

For the reference layout, expected candidate ordering is:

1. medicine box
2. storage box
3. phone
4. button/switch
5. wireless charging pad
6. USER ZONE

With max page size = 3:

Page 1:
- slot0 → medicine
- slot1 → storage
- slot2 → phone

Page 2:
- slot0 → button
- slot1 → charger
- slot2 → USER ZONE

This order is semantic/visual.
SSVEP mapping remains:
- slot0 7.2 Hz
- slot1 9 Hz
- slot2 12 Hz

Do not assign class by color.

If the current implementation has a better generic spatial sorter, use it, but it must reproduce the required reference order and be deterministic.

## 7.2 Paged-selection regression

Validate in the new scene:

- Next
- Previous
- cross-page selection
- Undo Last
- Submit
- selected target persistence across page switches
- global ordered queue
- exact Submit order
- selected/static visual semantics
- no duplicate selection
- stale result fail-closed
- active-slot validation
- Trigger re-arm
- single active trial safety

Do not reintroduce already accepted bugs.

## 7.3 Tonight's only required robot actions

Do not implement press/open-close robot skills tonight.

Required pick-and-place demonstrations:

A. medicine box → USER ZONE  
B. phone → USER ZONE  
C. phone → wireless charging pad

Prefer fitting this to the existing ordered-selection contract, e.g.:

- `[assist_medicine_box, assist_user_zone]`
- `[assist_phone, assist_user_zone]`
- `[assist_phone, assist_wireless_charger]`

If the current architecture already has a source/destination adapter, extend it instead of building a parallel queue.

Unknown/invalid source-destination pairs must fail closed.

### Pick/place acceptance

For all three:
- gripper reaches actual object body
- no empty grasp
- no obvious gripper/object penetration
- no table penetration
- object lifts
- object transports
- object reaches correct target
- object releases
- object remains stable after release

Phone → charger additionally:
- centered or intentionally aligned on charging pad
- realistic final orientation
- no hover
- no interpenetration

USER ZONE:
- deterministic placement coordinates
- medicine and phone placements do not collide with region border/other objects

## 7.4 Context / affordance in the new scene

This is a **regression/semantic smoke**, not Task 3 tuning.

Context must use:
- completed observable selection history
- current available candidates
- semantic affordance relations

It must not use:
- future EEG label
- hidden task truth
- oracle next target
- object color as identity

Required example after selecting phone:

`P(charger) > P(storage_box) > P(medicine_box) ≈ P(button_switch)`

The user explicitly wants this affordance ordering.

USER ZONE can be represented as a generic destination separately; do not use its probability to invalidate the required ranking above.

Demonstrate at least:
- aligned context: phone history favors charger
- neutral context
- conflicting context where strong synthetic EEG can still override context

Save the exact prior vectors and fused result traces.

### Task 2 PASS gate

Task 2 is PASS only when:
1. visual ordering/pagination passes;
2. Previous/Next/Undo Last/Submit pass;
3. synthetic EEG selection works across both pages;
4. Context/affordance trace works;
5. all three requested pick/place demonstrations pass;
6. focused M19/M9 regressions pass.

Only after Task 2 PASS may Task 3 begin.

---

# 8. TASK 3 — Investigate and strengthen Context-assisted EEG decoding

Task 3 is deliberately separated from the new assistive scene.

Use:
- the legacy block scene semantics
- the same historical human EEG dataset/artifacts used for the previous V3 analysis

Do NOT:
- use tonight's new scene to generate the scientific comparison
- collect new EEG
- alter raw EEG
- overwrite historical data
- deploy experimental Context/V3 changes into the live M19 production decoder

Create new analysis-only scripts/config/artifacts if needed.

---

# 9. Correct EEG decoding-time definition

The user's requested metric is **actual EEG evidence time used by decoding**.

The first 0.5 s is a guard/gaze-transfer interval and was not used as EEG evidence.

Therefore for tonight's analysis:

`effective_eeg_evidence_time = onset_relative_decision_time - 0.5 s`

Examples:

- fixed 2.4 s onset-relative → 1.9 s effective EEG evidence
- V3 EEG-only mean 1.445 s onset-relative → ~0.945 s effective EEG evidence
- V3 EEG+Context mean 1.431 s onset-relative → ~0.931 s effective EEG evidence

Important:
- do not physically delete the first 0.5 s from raw recordings
- do not rewrite old logs
- report both metrics when useful:
  - onset-relative time
  - effective EEG evidence time
- primary Task 3 conclusions should use effective EEG evidence time

---

# 10. First audit: why was previous Context gain tiny?

Before tuning anything, trace the exact historical V3 EEG+Context analysis path from code.

Answer from code/artifacts, not memory:

1. What exact FBCCA vector enters Context fusion?
2. Are those values calibrated class probabilities?
   - If not, do not describe `0.7` as “70% confidence”.
3. How is ContextPrior constructed?
4. How strong are typical priors?
5. Is Context prior softened toward uniform?
6. Is a `lambda = 0.5` softening rule actually used in the relevant path?
7. Is fusion multiplicative, log-space, additive, or something else?
8. Does dynamic stopping use:
   - raw EEG scores,
   - normalized EEG evidence,
   - fused posterior,
   - or a mixture?
9. Which stopping gates are evaluated before vs after Context?
10. Can Context make a decision satisfy stopping criteria earlier?
11. Or does Context only change class ranking after EEG-only gates are already satisfied?
12. Does candidate projection / renormalization substantially flatten Context?

Explicitly test the user's suspected failure mode.

For a 3-class raw prior:

`[0.90, 0.05, 0.05]`

If current code does:

`p_soft = λ * p + (1-λ) * [1/3,1/3,1/3]`

with `λ = 0.5`,

then:

`p_soft ≈ [0.6167, 0.1917, 0.1917]`

If this is the actual path, document that a nominal “90% prior” becomes only about 61.7% effective before fusion.

This is a hypothesis to verify, not an assumption.

---

# 11. Reproduce the previous baseline first

Locate the exact prior V3 analysis code/data.

Reproduce:

A. V3 EEG-only  
B. existing V3 EEG+Context

Match prior results as closely as possible before changing fusion.

Preserve the known evidence distinction if present:

- 88 formal-manifest records
- + 1 replay-supplemented exploratory record

Where practical report:
- formal-only
- all-analysis-records

Do not silently treat the exploratory record as a new independent formal trial.

---

# 12. Controlled Context-prior strength experiment

Run paired analysis on the **same EEG trials**.

Test aligned Context top prior strengths:

- 0.60
- 0.75
- 0.90
- 0.95

For 3 classes, distribute the remaining mass across non-top classes.

Also test:
- neutral uniform prior
- conflict prior where the wrong class receives 0.90

Include the user's illustrative scenario in the report:

- observed history: Green block selected
- Context predicts Red next with prior 0.90
- replay EEG target is Red
- compare EEG-only vs Context-assisted effective evidence time

Do this systematically across many applicable trials/classes, not as a single anecdote.

---

# 13. Strengthen how Context participates

Do not blindly increase one number without understanding the current fusion.

Build an **analysis-only configurable fusion**.

A reasonable candidate, if compatible with the current score semantics:

`log fused_i = log EEG_i + alpha * log prior_i`

or an equivalent stable multiplicative rule.

Suggested alpha sweep:

- 0 = EEG-only
- 0.25
- 0.5
- 1.0
- optionally 1.5 only if conflict safety remains good

Also compare:
- current Context softening
- reduced/no softening

Do not call FBCCA values probabilities unless calibrated.

---

# 14. Context must be able to accelerate stopping

The scientific question is not only “can Context change the final ranking?”

Determine whether Context can **legitimately make the dynamic stopping rule pass earlier**.

If current stopping is mostly raw-EEG gated, create an analysis-only stronger Context-aware stopping variant.

Safety shape:

- Context may accelerate when EEG evidence is compatible with Context.
- Strong contradictory EEG must override Context.
- Conflict Context must not create wrong early stops.
- Context cannot resurrect inactive classes.
- No hidden/future labels.
- final fallback remains EEG-grounded.

The same raw EEG trial must be compared paired:
- EEG-only
- EEG+Context

No separate acquisition is allowed for the comparison.

---

# 15. Task 3 metrics

For every relevant policy report:

- accuracy
- wrong early stops
- mean effective EEG evidence time
- median effective EEG evidence time
- P90
- latest
- T@95
- T@99
- T@100 where meaningful
- fraction of trials accelerated by Context
- mean acceleration among accelerated trials
- aligned / neutral / conflict breakdown
- Context-caused errors
- strong-EEG override behavior

Also optionally show onset-relative numbers for backward comparison, but do not use them as the user's primary decoding-time metric.

Final Task 3 answer must directly determine which explanation is supported:

1. historical EEG is already so strong that Context has little room;
2. Context prior is too weak/too softened;
3. fusion weight is too small;
4. stopping gates are mostly EEG-only and Context cannot actually advance them;
5. a combination of the above.

Do not force a preferred conclusion.

---

# 16. Task 3 output boundary

Task 3 may produce a recommended **research candidate** algorithm/config.

It must remain:
- offline/replay
- analysis-only
- not production M19
- not live-validated

Do not replace tomorrow's M19 human-acquisition path.

Tomorrow's weaker Natural-gaze data will later test the separate “EEG too strong” hypothesis.

---

# 17. Required evidence/artifacts

Create a final run directory under existing repository conventions.

At minimum save:

## Task 1
- canonical assistive scene spec
- Unity/MuJoCo geometry parity report
- left/right mirror check
- articulation check
- Unity top-down screenshot
- Unity perspective screenshot
- MuJoCo top-down screenshot
- MuJoCo perspective screenshot
- Task 1 acceptance summary

## Task 2
- page/candidate ordering trace
- synthetic EEG selection trace
- Previous/Next/Undo/Submit regression evidence
- context/affordance trace
- pick/place traces for all 3 actions
- Task 2 acceptance summary

## Task 3
- existing fusion forensic audit
- reproduced EEG-only baseline
- reproduced existing EEG+Context baseline
- prior-strength sweep result CSV/JSON
- fusion-strength sweep result CSV/JSON
- aligned/neutral/conflict comparison
- effective-evidence-time comparison
- final scientific summary

Final top-level report:

`M20_OVERNIGHT_FINAL_REPORT.md`

It must clearly state:

- TASK1 = PASS / BLOCKED
- TASK2 = PASS / BLOCKED
- TASK3 = PASS / BLOCKED
- exact remaining manual/human-only checks
- exact commands to reproduce the assistive-scene demo
- exact commands to reproduce Task 3 analysis
- exact files changed
- known limitations / evidence boundary

---

# 18. Runtime / test strategy

Prefer:
- focused tests
- current accepted regression suites
- static geometry checks
- deterministic synthetic EEG
- MuJoCo simulation
- Unity EditMode/PlayMode validation
- screenshots/logs

Do not run an enormous unrelated full-suite repeatedly unless necessary.

When a focused test fails:
1. diagnose;
2. fix;
3. re-run;
4. continue.

Do not stop at the first failure.

---

# 19. GUI / Computer Use authority

The user explicitly authorizes Codex to operate the PC mouse/UI for this task.

Use Unity UI where it is faster or necessary:
- scene opening
- hierarchy inspection
- Play Mode
- Test Runner
- Game view
- Scene view
- screenshot verification

If MuJoCo viewer is useful, launch it and inspect it.

Do not change unrelated Windows/system settings.

---

# 20. Final completion condition

The campaign is complete only when:

### Task 1
New assistive scene is implemented and cross-engine parity is accepted.

### Task 2
New scene passes selection/context/robot full-chain simulation and all 3 requested pick/place actions.

### Task 3
Historical EEG baseline is reproduced, Context's weak historical contribution is explained from code/data, and a stronger analysis-only Context candidate is quantitatively evaluated with the corrected effective EEG evidence-time metric.

If any stage is genuinely blocked by a physical/human-only requirement, do not pretend PASS.
Complete everything possible in software, state the exact blocker, and preserve all evidence.

Priority:
**correctness > reproducibility > completion speed > code elegance**

Prefer reuse of the existing M9/M19 architecture over parallel rewrites.
