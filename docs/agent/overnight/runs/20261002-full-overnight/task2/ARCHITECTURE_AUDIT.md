# Task 2 Architecture Audit

Audit date: 2026-10-02
Repository baseline: `feature/m9-virtual-manipulation` at `f9cd64d114a7bec6822ec6ccb6d8e9727ef1a7be`
Scope: M20 Assistive Desk Quest scene, M16 presentation/selection path, M8 transport boundary, and existing M9/MuJoCo execution path. No Quest/ND8/COM hardware operation was performed for this audit.

## Current Quest scene authority

`m7_unity6000/Assets/Resources/BCI/M20/assistive_desk_scene.json` is the only hand-authored canonical scene definition. It contains stable semantic IDs, target IDs, logical block IDs, table-local poses/dimensions, the fixed `assist_user_zone`, slot frequencies, candidate order, affordances, placement allowlists, and articulation data.

`M20AssistiveDeskUnityScene.LoadFromResources` and `Parse` load/validate this resource. `CreatePagedQueueCatalog` copies the JSON candidate order, poses, and labels into the M16 catalog. `CreateTargets` creates visible candidate roots and geometry at `ToUnityWorkspacePosition(positionMeters)`, whose calibrated axis adapter is `(-x, z, -y)`. Articulation roots for the storage lid and button cap are built from the same canonical entity poses.

`M9VirtualManipulationBootstrap.Initialize` selects the M20 profile, loads the canonical spec, creates the workspace/table, creates M20 targets, then initializes `BciSsvepTargetBinding`, the TCP transport, `BciPagedTargetQueueController`, and `BciTargetBatchController`. It passes `useCatalogCandidateOrder: true` for M20, so the live order is the static resource array; randomized poses would not currently cause a spatial reorder.

## IDs, ordering, paging, slots, and visuals

Candidate identities are the stable `semanticId`/`targetId`/`logicalBlockId` values in the JSON. The frozen order is medicine, storage, phone, button, charger, USER ZONE. In canonical table coordinates it is far-to-near by decreasing Y, with same-row objects left-to-right by increasing X; the Python validator enforces those row rules with a 0.02 m Y grouping tolerance.

`BciPagedTargetQueueController.Initialize` builds the target-to-logical-ID map from catalog entries. `OnHudCandidatesChanged` creates `BciPagedCandidate`s in `OrderInitialCandidates`; with M20 catalog ordering enabled it compares the explicit catalog indices. `BciPagedSelectionQueue` owns three-candidate pages and fixed slot indices. `NextPage`, `PreviousPage`, and `Submit` act on the existing queue and immutable submitted plan. `BciSsvepTargetBinding` maps slots 0/1/2 to the catalog's 7.2/9/12 Hz definitions; `MultiTargetStimulusController` renders the existing black/white phases. Page refresh and Submit do not reconstruct scene roots.

The existing scene contract tests are `m7_unity6000/Assets/Tests/Editor/M20AssistiveDeskSceneTests.cs` and `integration/test_m20_assistive_scene_contract.py`; queue/end-to-end tests are `integration/test_m20_assistive_desk_e2e.py`. They check the fixed order, slots, geometry, transforms, paging/Submit contracts, and canonical MuJoCo scene. They do not yet test randomized scene snapshots or Quest-to-MuJoCo runtime pose parity.

## Quest to PC transport

`BciSelectionTransportClient` sends newline-delimited JSON over TCP. Existing M8 confirmed batches carry ordered selections and candidate/page metadata; the receiving M8 consumer validates the frozen batch, after which `M9BatchDispatcher` resolves TargetId to logical block ID. The current M8/M9 payloads do not carry a `SceneLayoutSnapshot`/scene ID or the actual Quest table-local poses. Therefore the current PC path cannot know a randomized Quest layout from the selection message alone.

`integration/m19_mujoco_rpc.py` is an additional JSONL RPC boundary for confirmed batches, but its current `m19_confirmed_batch_dispatch` message likewise wraps the confirmed batch and ACK and does not provide a Quest scene snapshot. The M20 synthetic E2E helper calls the queue, M8 receipt consumer, and M9 dispatcher in process; its `scene_builder` uses the canonical spec directly.

## MuJoCo scene, transform, and manipulation

`integration/m20_assistive_scene_contract.py` loads the same Unity resource JSON. `build_mujoco_scene` constructs M20 table/object bodies and articulation joints at the JSON table-local poses; `check_mujoco_scene` compares compiled geom/body positions, dimensions, contact interpenetration, placement poses, and the existing M9 grasp planner's reachability. The canonical table top is at MuJoCo world Z=0.75 m; scene object local Z is added to that table-top height. Unity uses the established table/workspace adapter `(-x, z, -y)` with its existing M13.6-scaled robot visual anchor. No second hand-entered Quest-to-MuJoCo offset currently exists in the JSON contract.

`integration/m20_assistive_desk_e2e.py::_make_mujoco_dispatcher` builds a MuJoCo model/data pair from the static canonical spec and passes it to `create_fr3_umi_mujoco_adapter(persistent_world=True)`. Source/destination semantics and placement positions come from stable IDs and the canonical placement allowlist. The existing M9 adapter resets isolated requests by default; persistent mode preserves the live world between actions and retains free-body states. The M20 phone path adds a weld only after bilateral left/right fingertip contact and computes the relative transform at the current pose; it rejects a discontinuous anchor. The test helper records planner phases, object/gripper contacts, object pose, placement error, speed, and weld evidence. Medicine and phone are the current practical graspable classes; the storage box and button are articulated context/interactable objects, while charger and USER ZONE are fixed surfaces.

## Gaps that Task 2 implementation must close

1. The Quest bootstrap currently uses static JSON positions and catalog order; it needs one generated, frozen, versioned runtime snapshot and dynamic spatial order computed from the final positions.
2. The M8/M9 scene startup boundary currently transmits selections but no snapshot. Task 2 must establish an explicit snapshot handoff before dispatch and bind every confirmed batch/command to the same scene ID; stale/missing/unknown/duplicate scene data must fail closed.
3. The PC MuJoCo builder currently consumes the canonical JSON only. It needs to consume the accepted serialized snapshot while preserving stable semantic identities, unchanged geometry/affordances, and the existing calibrated coordinate adapter.
4. Existing tests cover the canonical baseline but not ≥20 deterministic layouts, randomized geometry/reachability, same/different seed behavior, USER ZONE invariance, queue/layout freeze, stale-scene rejection, exact Quest↔MuJoCo pose parity, or negative wrong-pose grasp behavior.

## Preservation boundary

Task 2 will change only runtime placement/order/snapshot handling and the minimum PC receipt/builder validation needed to use that exact layout. It will preserve the M16 page size/navigation/selection/highlights/Submit behavior, existing black/white stimulus rendering, 0/1/2 = 7.2/9/12 Hz mapping, semantic IDs, M9 planner/IK/trajectories, M13.6 coordinate convention, and the fixed USER ZONE pose. No ND8, COM11, raw EEG, or physical robot path is involved.

## Post-implementation closure (2026-10-02)

The runtime path now generates one versioned `SceneLayoutSnapshot` in `M20AssistiveDeskUnityScene.CreateRuntimeSceneSpec`, rebuilds dynamic spatial order from its randomized table-local poses, and sends that exact serialized snapshot with the existing M8 confirmed-batch envelope. `M20SceneSnapshotRegistry` validates identity, pose, footprint, fixed USER ZONE, coordinate adapter and scene ID before `M20SceneBoundBatchConsumer` accepts a batch; the MuJoCo builder then consumes the validated runtime spec from that snapshot. The bootstrap passes the generated scene spec into the existing M16 catalog and presentation path once per app initialization.

Python and Unity randomizers use the same geometry clearances and a tested conservative source-pose envelope for `assist_medicine_box` and `assist_phone` (table-local X −0.24…+0.24 m, Y 0…0.14 m). This was added after fixed-seed M9 IK probes found unreachable samples near the far edge and far left. No planner, IK, trajectory, object footprint, slot map, selection semantics or placement allowlist changed. Other task-object translations remain randomized; USER ZONE, table, robot and UI stay fixed. Python validates 20 deterministic seeds with no fallback; both graspable objects pass the existing M9 IK check in all 20.

Additional focused evidence is in `SCENE_LAYOUT_CONTRACT.md`, `randomization_validation.json`, `quest_mujoco_pose_sync.csv`, `randomized_manipulation_validation.json`, and `end_to_end_acceptance.json`. Python M20 targeted regression and real local TCP receiver tests pass; three deterministic seeds each pass medicine→USER ZONE, phone→USER ZONE and phone→charger MuJoCo runs. The Quest runtime build/install/cold-launch portion remains separately pending: the live Editor is open and responsive but the available UI control surface could not activate its Unity window. No Quest app was built or launched in this run.
