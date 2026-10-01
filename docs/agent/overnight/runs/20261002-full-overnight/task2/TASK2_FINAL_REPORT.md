# M20 Task 2 Final Report

Date: 2026-10-02

## Result

**Software/simulator validation: PASS. Quest runtime validation: pending interactive Editor access.** No known Python or C# compile failure remains in Task 2. Quest Build & Run, installed app launch, three cold launches, screenshots/cast, and real Quest TCP transport were not performed; they remain explicitly unverified, not PASS.

## Implementation

- Unity creates one versioned `SceneLayoutSnapshot` for the M20 app session, supports `-m20-layout-seed`, randomizes the five non-zone task objects with bounded sampling and a validated fallback, then derives the spatial order from those final poses.
- The fixed USER ZONE, table, robot, M16 paging, Submit, TargetIds, labels, black/white flicker, transport envelope and 0/1/2 = 7.2/9/12 Hz mapping are preserved.
- Medicine and phone source positions use a conservative table-local reachability envelope, X −0.24…+0.24 m and Y 0…0.14 m. This came from probes against the existing M9 planner after broad samples produced unreachable grasp poses. Planner, IK, trajectory and object geometry were not changed.
- The confirmed M8 batch carries the exact frozen Quest snapshot. PC validates the full snapshot and scene/selection identities before ACK, and builds MuJoCo from the accepted positions. It rejects missing snapshots and changed poses for a scene already frozen by the receiver.

## Software verification

- Python: 20 deterministic randomization seeds passed exact replay, distinct-layout, bounds/clearance/overlap, spatial order, fixed USER ZONE, paging/Submit freeze and slot mapping. The existing M9 IK planner reached both practical grasp sources for all seeds; no fallback was used.
- The validated fallback layout was also checked separately against scene geometry and both existing M9 grasp IK targets; all passed.
- Quest contract to compiled MuJoCo pose parity: 5 mm tolerance; observed maximum error 0 m across the emitted pose rows. This validates serialized scene-contract poses against MuJoCo state, not actual Unity Transform observations.
- MuJoCo manipulation: 3 randomized seeds × 3 scenarios passed (medicine→USER ZONE; phone→USER ZONE; phone→charger). The traces record current-snapshot source positions, contact at closure, M9 `place_ok`, placement error, table clearance, release, and settled final object speeds. Phone attachment required bilateral contact and was released after placement.
- Production PC receiver loopback: synthetic M16/M19 source→destination batch received over local TCP, exact scene snapshot validated before the matching `batch_ack`, and the accepted scene was executed through M9/MuJoCo.
- Negative cases: stale-scene command rejected; absent snapshot and a changed pose for an already frozen scene received no ACK. The changed-pose case was rejected before dispatch.
- Python focused regression: 14/14 tests passed. Project-generated Unity runtime and Editor assemblies both compiled with `dotnet msbuild`; warnings were from existing Unity sample/dependency references. Unity GUI Console/EditMode execution was not independently verified.

Evidence:

- `randomization_validation.json`
- `quest_mujoco_pose_sync.csv`
- `randomized_manipulation_validation.json`
- `end_to_end_acceptance.json`
- `quest-pc-loopback-01/loopback_acceptance.json`
- `e2e-run-03/acceptance.json`, `e2e-run-04/acceptance.json`, `e2e-run-05/acceptance.json`

## Quest boundary and next action

ADB can see the Quest 3 over the network at `192.168.43.110:5555`; this run did not need USB. The existing Unity 6000.0.66f2 Editor is open on `M20DailyAssistiveDesk` and responding. The available computer-use control could not activate the native Editor window, and starting another Editor would risk the open project lock, so no Quest build or launch was attempted. The Quest was on its system Library activity; no M20 visual screenshot or cast was captured.

Next, use the existing Editor to refresh/confirm the Console, Build & Run the M20 scene to Quest, then collect three cold-launch SceneLayoutSnapshot logs/screenshots and verify USER ZONE stability, object labels/black-white patches, paging/Submit, and a real Quest batch ACK. No ND8, COM port, raw EEG, or physical robot is involved.
