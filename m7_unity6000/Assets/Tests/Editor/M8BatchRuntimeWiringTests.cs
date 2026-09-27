using System.Reflection;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class M8BatchRuntimeWiringTests
    {
        [Test]
        public void BatchMode_GivesAAndBInputOwnershipToTheBatchController()
        {
            Assert.That(DetectionManager.ShouldHandleLegacyMarkerInput(false), Is.True);
            Assert.That(DetectionManager.ShouldHandleLegacyMarkerInput(true), Is.False);
        }

        [Test]
        public void BatchMode_SuppressesRawDetectionPresentationWithoutChangingInferenceOwnership()
        {
            Assert.That(SentisInferenceUiManager.ShouldRenderRawDetectionVisuals(false), Is.True);
            Assert.That(SentisInferenceUiManager.ShouldRenderRawDetectionVisuals(true), Is.False);
        }

        [Test]
        public void ControllerInitialization_ReceivesExistingHudCandidatesAndTakesBatchInputOwnership()
        {
            var cameraObject = new GameObject("M8ControllerCamera");
            var managerObject = new GameObject("M8ControllerManager");
            var parentObject = new GameObject("M8ControllerParent");
            var bindingObject = new GameObject("M8ControllerBinding");
            var transportObject = new GameObject("M8ControllerTransport");
            var controllerObject = new GameObject("M8Controller");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var transport = transportObject.AddComponent<BciSelectionTransportClient>();
            var controller = controllerObject.AddComponent<BciTargetBatchController>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                InvokeStableAnchor(binding, Anchor("preexisting", 0f));

                controller.Initialize(binding, transport);
                InvokeLifecycle(controller, "LateUpdate");

                Assert.That(controller.OwnsBatchInput, Is.True);
                Assert.That(binding.HasActiveGroup, Is.True,
                    "The controller must receive HUD candidates that existed before it subscribed.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(controllerObject);
                UnityEngine.Object.DestroyImmediate(transportObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void HostBatchClose_PreparesNextGroupButDefersPresentationUntilSelectionOpen()
        {
            var cameraObject = new GameObject("M8HostCloseCamera");
            var managerObject = new GameObject("M8HostCloseManager");
            var parentObject = new GameObject("M8HostCloseParent");
            var bindingObject = new GameObject("M8HostCloseBinding");
            var transportObject = new GameObject("M8HostCloseTransport");
            var controllerObject = new GameObject("M8HostCloseController");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var transport = transportObject.AddComponent<BciSelectionTransportClient>();
            var controller = controllerObject.AddComponent<BciTargetBatchController>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                foreach (StableWorldAnchorSnapshot anchor in new[]
                {
                    Anchor("m9-vblock-red-01", -1f),
                    Anchor("m9-vblock-green-01", 0f),
                    Anchor("m9-vblock-yellow-01", 1f),
                    Anchor("m9-vblock-blue-01", 2f)
                })
                    InvokeStableAnchor(binding, anchor);

                controller.Initialize(binding, transport);
                InvokeLifecycle(controller, "LateUpdate");
                BciTargetGroupCoordinator groups = GetPrivateField<BciTargetGroupCoordinator>(controller, "m_groups");
                BciActiveTargetGroup firstGroup = groups.ActiveGroup.Value;
                Assert.That(firstGroup.Targets.Count, Is.EqualTo(3));
                Assert.That(firstGroup.Targets[0].TargetId, Is.EqualTo("m9-vblock-red-01"));
                Assert.That(firstGroup.Targets[1].TargetId, Is.EqualTo("m9-vblock-green-01"));
                Assert.That(firstGroup.Targets[2].TargetId, Is.EqualTo("m9-vblock-yellow-01"));
                InvokePrivate(controller, "OnSelectionOpened", "selection-red");
                Assert.That(binding.IsSlotActiveCandidate(0), Is.True,
                    "The first SSVEP window must begin at selection_open.");
                BciTargetSelectionResult first = SelectionResult("selection-red", 0, firstGroup.Targets[0].TargetId);
                BciTargetSelectionResult second = SelectionResult("selection-green", 1, firstGroup.Targets[1].TargetId);
                InvokePrivate(controller, "OnTargetSelected", first);
                InvokePrivate(controller, "OnTargetSelected", second);

                var payload = new ConfirmedTargetBatchPayload
                {
                    batchId = "batch-1",
                    groupId = firstGroup.GroupId,
                    groupIndex = firstGroup.GroupIndex,
                    selections = new[]
                    {
                        ConfirmedTargetSelectionPayload.From(first),
                        ConfirmedTargetSelectionPayload.From(second)
                    }
                };

                Assert.That((bool)InvokePrivate(controller, "OnHostBatchCloseRequested", payload), Is.True);
                Assert.That(groups.HasActiveGroup, Is.True,
                    "Host close must make the next group available before its batch ACK returns.");
                Assert.That(groups.ProcessedTargetIds, Does.Contain("m9-vblock-red-01"));
                Assert.That(groups.ProcessedTargetIds, Does.Contain("m9-vblock-green-01"));
                Assert.That(groups.ProcessedTargetIds, Does.Not.Contain("m9-vblock-yellow-01"),
                    "An unselected S1 candidate must remain available for S2.");

                Assert.That(binding.IsSlotActiveCandidate(0), Is.False,
                    "Protocol-ready S2 must not start its SSVEP candidate timing before selection_open.");
                Assert.That(binding.IsSlotActiveCandidate(1), Is.False,
                    "Protocol-ready S2 must not start its SSVEP candidate timing before selection_open.");
                LineRenderer oldRedIndicator = GetCandidateIndicator(binding, "m9-vblock-red-01");
                LineRenderer oldGreenIndicator = GetCandidateIndicator(binding, "m9-vblock-green-01");
                Assert.That(oldRedIndicator == null || !oldRedIndicator.gameObject.activeSelf, Is.True,
                    "Committed Red overlay must be hidden before the next selection opens.");
                Assert.That(oldGreenIndicator == null || !oldGreenIndicator.gameObject.activeSelf, Is.True,
                    "Committed Green overlay must be hidden before the next selection opens.");

                BciActiveTargetGroup secondGroup = groups.ActiveGroup.Value;
                Assert.That(secondGroup.Targets.Count, Is.EqualTo(2));
                Assert.That(secondGroup.Targets[0].TargetId, Is.EqualTo("m9-vblock-yellow-01"));
                Assert.That(secondGroup.Targets[1].TargetId, Is.EqualTo("m9-vblock-blue-01"));

                var nextSnapshot = new BciSelectionSnapshot(
                    "snapshot-2",
                    2,
                    new[]
                    {
                        new BciSelectionTarget(0, new StableWorldAnchorSnapshot(
                            "m9-vblock-yellow-01", "yellow_block", StableTargetState.Active, Vector3.zero)),
                        new BciSelectionTarget(1, new StableWorldAnchorSnapshot(
                            "m9-vblock-blue-01", "blue_block", StableTargetState.Active, Vector3.right)),
                        new BciSelectionTarget(2, null, null, StableTargetState.TemporarilyMissing)
                    });

                Assert.That((bool)InvokePrivate(
                    controller,
                    "OnAuthoritativeSelectionOpening",
                    "selection-yellow",
                    nextSnapshot), Is.True,
                    "The immediate next selection_open must see the newly active group.");
                InvokePrivate(controller, "OnSelectionOpened", "selection-yellow");
                Assert.That(binding.IsSlotActiveCandidate(0), Is.True,
                    "S2 stimulus must begin at selection_open, not at protocol group activation.");
                Assert.That(binding.IsSlotActiveCandidate(1), Is.True,
                    "S2 stimulus must begin at selection_open, not at protocol group activation.");

                BciTargetSelectionResult third = SelectionResult(
                    "selection-yellow", 0, "m9-vblock-yellow-01");
                BciTargetSelectionResult fourth = SelectionResult(
                    "selection-blue", 1, "m9-vblock-blue-01");
                InvokePrivate(controller, "OnTargetSelected", third);
                InvokePrivate(controller, "OnTargetSelected", fourth);
                var secondPayload = new ConfirmedTargetBatchPayload
                {
                    batchId = "batch-2",
                    groupId = secondGroup.GroupId,
                    groupIndex = secondGroup.GroupIndex,
                    selections = new[]
                    {
                        ConfirmedTargetSelectionPayload.From(third),
                        ConfirmedTargetSelectionPayload.From(fourth)
                    }
                };

                Assert.That((bool)InvokePrivate(
                    controller, "OnHostBatchCloseRequested", secondPayload), Is.True,
                    "S2 Yellow+Blue must close through the same host-driven lifecycle.");
                Assert.That(groups.ProcessedTargetIds, Does.Contain("m9-vblock-yellow-01"));
                Assert.That(groups.ProcessedTargetIds, Does.Contain("m9-vblock-blue-01"));
                Assert.That(groups.HasActiveGroup, Is.False,
                    "The final S2 close must leave no active candidate group.");
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(controllerObject);
                UnityEngine.Object.DestroyImmediate(transportObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void ThreePlusOne_PresentationVisibilityIsConcreteAtBothSelectionWindows()
        {
            var cameraObject = new GameObject("M8ForensicCamera");
            var managerObject = new GameObject("M8ForensicManager");
            var parentObject = new GameObject("M8ForensicParent");
            var bindingObject = new GameObject("M8ForensicBinding");
            var transportObject = new GameObject("M8ForensicTransport");
            var controllerObject = new GameObject("M8ForensicController");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var transport = transportObject.AddComponent<BciSelectionTransportClient>();
            var controller = controllerObject.AddComponent<BciTargetBatchController>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                foreach (StableWorldAnchorSnapshot anchor in new[]
                {
                    Anchor("m9-vblock-red-01", -1f),
                    Anchor("m9-vblock-green-01", 0f),
                    Anchor("m9-vblock-yellow-01", 1f),
                    Anchor("m9-vblock-blue-01", 2f)
                })
                    InvokeStableAnchor(binding, anchor);

                controller.Initialize(binding, transport);
                InvokeLifecycle(controller, "LateUpdate");

                GameObject[] slotObjects = GetPrivateField<GameObject[]>(binding, "m_slotObjects");
                LineRenderer[] leaderLines = GetPrivateField<LineRenderer[]>(binding, "m_slotLeaderLines");
                for (int slot = 0; slot < 3; slot++)
                {
                    Assert.That(slotObjects[slot].activeSelf, Is.False,
                        "GroupReady must hide the first protocol group panels.");
                    Assert.That(leaderLines[slot].gameObject.activeSelf, Is.False,
                        "GroupReady must hide the first protocol group connector lines.");
                }

                BciTargetGroupCoordinator groups = GetPrivateField<BciTargetGroupCoordinator>(controller, "m_groups");
                BciActiveTargetGroup firstGroup = groups.ActiveGroup.Value;
                Assert.That(firstGroup.Targets.Count, Is.EqualTo(3));
                InvokePrivate(controller, "OnSelectionOpened", "forensic-selection-1");
                Assert.That(binding.IsSlotActiveCandidate(0), Is.True);
                Assert.That(binding.IsSlotActiveCandidate(1), Is.True);
                Assert.That(binding.IsSlotActiveCandidate(2), Is.True);
                for (int slot = 0; slot < 3; slot++)
                {
                    Assert.That(slotObjects[slot].activeSelf, Is.True,
                        "selection_open must activate the concrete SSVEP panel.");
                    Assert.That(leaderLines[slot].gameObject.activeSelf, Is.True,
                        "selection_open must activate the concrete connector line.");
                    Assert.That(
                        GetCandidateIndicator(binding, firstGroup.Targets[slot].TargetId).gameObject.activeSelf,
                        Is.True,
                        "selection_open must activate the concrete candidate indicator.");
                }

                BciTargetSelectionResult first = SelectionResult(
                    "forensic-red", 0, firstGroup.Targets[0].TargetId);
                BciTargetSelectionResult second = SelectionResult(
                    "forensic-green", 1, firstGroup.Targets[1].TargetId);
                BciTargetSelectionResult third = SelectionResult(
                    "forensic-yellow", 2, firstGroup.Targets[2].TargetId);
                InvokePrivate(controller, "OnTargetSelected", first);
                InvokePrivate(controller, "OnTargetSelected", second);
                InvokePrivate(controller, "OnTargetSelected", third);
                var firstPayload = new ConfirmedTargetBatchPayload
                {
                    batchId = "forensic-batch-1",
                    groupId = firstGroup.GroupId,
                    groupIndex = firstGroup.GroupIndex,
                    selections = new[]
                    {
                        ConfirmedTargetSelectionPayload.From(first),
                        ConfirmedTargetSelectionPayload.From(second),
                        ConfirmedTargetSelectionPayload.From(third)
                    }
                };

                Assert.That((bool)InvokePrivate(controller, "OnHostBatchCloseRequested", firstPayload), Is.True);
                BciActiveTargetGroup secondGroup = groups.ActiveGroup.Value;
                Assert.That(secondGroup.Targets.Count, Is.EqualTo(1));
                Assert.That(secondGroup.Targets[0].TargetId, Is.EqualTo("m9-vblock-blue-01"));
                Assert.That(binding.IsSlotActiveCandidate(0), Is.False,
                    "GroupReady must keep the second group stimulus closed.");
                Assert.That(slotObjects[0].activeSelf, Is.False);
                Assert.That(leaderLines[0].gameObject.activeSelf, Is.False);

                // This is the stale-telemetry interleave that the physical run
                // can produce before the host opens the next selection window.
                Assert.That(controller.SetM13_6ExecutionPresentation(true, "forensic_stale_execution"), Is.True);

                var secondSnapshot = new BciSelectionSnapshot(
                    "forensic-snapshot-2",
                    2,
                    new[]
                    {
                        new BciSelectionTarget(0, Anchor("m9-vblock-blue-01", 2f)),
                        new BciSelectionTarget(1, null, null, StableTargetState.TemporarilyMissing),
                        new BciSelectionTarget(2, null, null, StableTargetState.TemporarilyMissing)
                    });
                Assert.That((bool)InvokePrivate(
                    controller,
                    "OnAuthoritativeSelectionOpening",
                    "forensic-selection-2",
                    secondSnapshot), Is.True);
                InvokePrivate(controller, "OnSelectionOpened", "forensic-selection-2");

                Assert.That(binding.IsSlotActiveCandidate(0), Is.True,
                    "The real remaining target must become an active SSVEP candidate at selection_open.");
                Assert.That(binding.IsSlotActiveCandidate(1), Is.False);
                Assert.That(binding.IsSlotActiveCandidate(2), Is.False);
                Assert.That(slotObjects[0].activeSelf, Is.True,
                    "The second selection panel must be concretely visible.");
                Assert.That(leaderLines[0].gameObject.activeSelf, Is.True,
                    "The second selection connector line must be concretely visible.");
                Assert.That(GetCandidateIndicator(binding, "m9-vblock-blue-01").gameObject.activeSelf, Is.True,
                    "The second selection candidate indicator must be concretely visible.");
                Assert.That(slotObjects[1].activeSelf, Is.False);
                Assert.That(slotObjects[2].activeSelf, Is.False);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(controllerObject);
                UnityEngine.Object.DestroyImmediate(transportObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void M13TelemetryExecutionCannotHideOpenSelectionPresentation()
        {
            var cameraObject = new GameObject("M8TelemetryGateCamera");
            var managerObject = new GameObject("M8TelemetryGateManager");
            var parentObject = new GameObject("M8TelemetryGateParent");
            var bindingObject = new GameObject("M8TelemetryGateBinding");
            var transportObject = new GameObject("M8TelemetryGateTransport");
            var controllerObject = new GameObject("M8TelemetryGateController");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var transport = transportObject.AddComponent<BciSelectionTransportClient>();
            var controller = controllerObject.AddComponent<BciTargetBatchController>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                StableWorldAnchorSnapshot[] group =
                {
                    Anchor("m9-vblock-red-01", -1f),
                    Anchor("m9-vblock-green-01", 0f),
                    Anchor("m9-vblock-yellow-01", 1f)
                };
                foreach (StableWorldAnchorSnapshot anchor in group)
                    InvokeStableAnchor(binding, anchor);

                controller.Initialize(binding, transport);
                InvokeLifecycle(controller, "LateUpdate");

                GameObject[] slotObjects = GetPrivateField<GameObject[]>(binding, "m_slotObjects");
                LineRenderer[] leaderLines = GetPrivateField<LineRenderer[]>(binding, "m_slotLeaderLines");
                for (int slot = 0; slot < group.Length; slot++)
                {
                    Assert.That(slotObjects[slot].activeSelf, Is.False,
                        "GroupReady must keep stimulus panels hidden.");
                    Assert.That(leaderLines[slot].gameObject.activeSelf, Is.False,
                        "GroupReady must keep connector lines hidden.");
                }

                InvokePrivate(controller, "OnSelectionOpened", "selection-telemetry-gate");
                for (int slot = 0; slot < group.Length; slot++)
                {
                    Assert.That(binding.IsSlotActiveCandidate(slot), Is.True,
                        "selection_open must activate the real candidate slot.");
                    Assert.That(slotObjects[slot].activeSelf, Is.True,
                        "selection_open must show the stimulus panel.");
                    Assert.That(leaderLines[slot].gameObject.activeSelf, Is.True,
                        "selection_open must show the connector line.");
                    Assert.That(
                        GetCandidateIndicator(binding, group[slot].TargetId).gameObject.activeSelf,
                        Is.True,
                        "selection_open must show the candidate indicator.");
                }

                Assert.That(controller.SetM13_6ExecutionPresentation(true, "stale_executing_frame"), Is.True);
                for (int slot = 0; slot < group.Length; slot++)
                {
                    Assert.That(slotObjects[slot].activeSelf, Is.True,
                        "A stale executing telemetry frame must not hide an open selection panel.");
                    Assert.That(leaderLines[slot].gameObject.activeSelf, Is.True,
                        "A stale executing telemetry frame must not hide an open connector line.");
                    Assert.That(
                        GetCandidateIndicator(binding, group[slot].TargetId).gameObject.activeSelf,
                        Is.True,
                        "A stale executing telemetry frame must not hide an open candidate indicator.");
                }
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(controllerObject);
                UnityEngine.Object.DestroyImmediate(transportObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void CandidateVisualState_UsesFrozenStableTargetIdentityForInactiveAvailableSelectedAndSubmitted()
        {
            var cameraObject = new GameObject("M8WiringCamera");
            var managerObject = new GameObject("M8WiringManager");
            var parentObject = new GameObject("M8WiringParent");
            var bindingObject = new GameObject("M8WiringBinding");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);

                StableWorldAnchorSnapshot[] group = { Anchor("left", -1f), Anchor("center", 0f), Anchor("right", 1f) };
                InvokeStableAnchor(binding, group[0]);
                InvokeStableAnchor(binding, group[1]);
                InvokeStableAnchor(binding, group[2]);
                InvokeStableAnchor(binding, Anchor("other", 2f));
                Assert.That(binding.ActivateGroup("group-1", group), Is.True);

                Assert.That(binding.GetCandidateVisualState("left"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("other"), Is.EqualTo(BciCandidateVisualState.Inactive));

                // An execution frame can be stale while this active group is
                // still SelectionOpen.  The production contract keeps the
                // selection presentation visible until the group is committed.
                Assert.That(binding.SetExecutionPresentationHidden(true, "test_execution"), Is.True);
                Assert.That(GetCandidateIndicator(binding, "left").gameObject.activeSelf, Is.True);
                InvokeLifecycle(binding, "LateUpdate");
                Assert.That(GetCandidateIndicator(binding, "center").gameObject.activeSelf, Is.True);
                Assert.That(binding.SetExecutionPresentationHidden(false, "test_selection_open"), Is.True);
                Assert.That(GetCandidateIndicator(binding, "left").gameObject.activeSelf, Is.True);

                Assert.That(binding.SetGroupSlotSelected("group-1", 0, true), Is.True);
                Assert.That(binding.SetGroupSlotSelected("group-1", 1, true), Is.True);
                Assert.That(binding.GetCandidateVisualState("left"), Is.EqualTo(BciCandidateVisualState.Selected));

                Assert.That(binding.EndActiveGroup("group-1"), Is.True);
                // Once the active group is ended, the same presentation gate
                // is allowed to hide the committed group for robot execution.
                Assert.That(binding.SetExecutionPresentationHidden(true, "test_execution_after_commit"), Is.True);
                Assert.That(GetCandidateIndicator(binding, "left").gameObject.activeSelf, Is.False);
                // Match the canonical batch contract: only the confirmed
                // selections are processed; the unselected right target stays
                // available for the next group.
                binding.SetProcessedTargetIds(new[] { "left", "center" }, new[] { "left", "center" });
                Assert.That(binding.GetCandidateVisualState("left"), Is.EqualTo(BciCandidateVisualState.Submitted));
                Assert.That(binding.GetCandidateVisualState("center"), Is.EqualTo(BciCandidateVisualState.Submitted));
                Assert.That(GetCandidateIndicator(binding, "left").gameObject.activeSelf, Is.False);
                Assert.That(GetCandidateIndicator(binding, "center").gameObject.activeSelf, Is.False);

                // LateUpdate must not resurrect selection presentation while
                // the controller is executing the accepted batch.
                InvokeLifecycle(binding, "LateUpdate");
                Assert.That(GetCandidateIndicator(binding, "left").gameObject.activeSelf, Is.False);
                Assert.That(GetCandidateIndicator(binding, "center").gameObject.activeSelf, Is.False);

                // A new selection group must not restore committed overlays.
                StableWorldAnchorSnapshot[] nextGroup = { group[2], Anchor("other", 2f) };
                Assert.That(binding.ActivateGroup("group-2", nextGroup), Is.True);
                LineRenderer leftIndicator = GetCandidateIndicator(binding, "left");
                LineRenderer centerIndicator = GetCandidateIndicator(binding, "center");
                Assert.That(leftIndicator == null || !leftIndicator.gameObject.activeSelf, Is.True,
                    "A committed target must not regain its old blue overlay in a later group.");
                Assert.That(centerIndicator == null || !centerIndicator.gameObject.activeSelf, Is.True,
                    "A committed target must not regain its old blue overlay in a later group.");
                Assert.That(GetCandidateIndicator(binding, "right").gameObject.activeSelf, Is.True);
                Assert.That(GetCandidateIndicator(binding, "other").gameObject.activeSelf, Is.True);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void CandidateRefresh_PreservesFrozenGroupIndicatorWhenLiveDuplicateWinsDeduplication()
        {
            var cameraObject = new GameObject("M8FrozenCandidateCamera");
            var managerObject = new GameObject("M8FrozenCandidateManager");
            var parentObject = new GameObject("M8FrozenCandidateParent");
            var bindingObject = new GameObject("M8FrozenCandidateBinding");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);

                StableWorldAnchorSnapshot[] group =
                {
                    Anchor("frozen-left", -1f, 1d),
                    Anchor("frozen-center", 0f, 1d),
                    Anchor("frozen-right", 1f, 1d)
                };
                foreach (StableWorldAnchorSnapshot anchor in group)
                    InvokeStableAnchor(binding, anchor);
                Assert.That(binding.ActivateGroup("group-frozen", group), Is.True);

                // This newer Active track is a physical duplicate of frozen-left.
                // Without frozen-group precedence, normal deduplication selects it
                // and destroys the green indicator owned by frozen-left.
                InvokeStableAnchor(binding, Anchor("replacement-left", -1f, 2d));

                LineRenderer frozenIndicator = GetCandidateIndicator(binding, "frozen-left");
                Assert.That(frozenIndicator, Is.Not.Null);
                Assert.That(frozenIndicator.gameObject.activeSelf, Is.True);
                Assert.That(frozenIndicator.startColor.g, Is.GreaterThan(frozenIndicator.startColor.b));
                Assert.That(GetCandidateIndicator(binding, "replacement-left"), Is.Null);
                Assert.That(binding.GetCandidateVisualState("frozen-left"),
                    Is.EqualTo(BciCandidateVisualState.Available));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void CandidateIndicator_UsesStableTargetBoundingBoxAspectRatio()
        {
            var cameraObject = new GameObject("M8BboxCamera");
            var managerObject = new GameObject("M8BboxManager");
            var parentObject = new GameObject("M8BboxParent");
            var bindingObject = new GameObject("M8BboxBinding");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var manager = managerObject.AddComponent<DetectionManager>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);

                InvokeStableAnchor(binding, new StableWorldAnchorSnapshot(
                    "tall-bottle", "bottle", StableTargetState.Active, new Vector3(0f, 0f, 2f),
                    0.9f, new TargetBoundingBox(100f, 20f, 30f, 120f), 0d, 1d));

                LineRenderer indicator = GetCandidateIndicator(binding, "tall-bottle");
                Assert.That(indicator, Is.Not.Null);
                float width = Vector3.Distance(indicator.GetPosition(0), indicator.GetPosition(1));
                float height = Vector3.Distance(indicator.GetPosition(1), indicator.GetPosition(2));
                // The runtime clamps tall boxes to a 0.35 width/height ratio,
                // so their height/width ratio is capped at about 2.857.
                Assert.That(height, Is.GreaterThan(width * 2.8f));
                Assert.That(height, Is.LessThanOrEqualTo(width * 3f));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void LegacyMarker_BciPresentationHidesOnlyTheRotatingCubeRenderer()
        {
            var markerObject = new GameObject("M8LegacyMarker");
            GameObject cubeObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
            cubeObject.transform.SetParent(markerObject.transform, false);
            var marker = markerObject.AddComponent<DetectionSpawnMarkerAnim>();

            try
            {
                FieldInfo field = typeof(DetectionSpawnMarkerAnim).GetField(
                    "m_rotatingCubeRenderer",
                    BindingFlags.Instance | BindingFlags.NonPublic);
                field.SetValue(marker, cubeObject.GetComponent<Renderer>());

                marker.SetRotatingCubeVisible(false);

                Assert.That(markerObject.activeSelf, Is.True);
                Assert.That(marker, Is.Not.Null);
                Assert.That(cubeObject.GetComponent<Renderer>().enabled, Is.False);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(markerObject);
            }
        }

        private static StableWorldAnchorSnapshot Anchor(string targetId, float x, double lastSeen = 1d)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                "bottle",
                StableTargetState.Active,
                new Vector3(x, 0f, 2f),
                0.9f,
                new TargetBoundingBox(x * 10f + 100f, 20f, 30f, 30f),
                0d,
                lastSeen);
        }

        private static LineRenderer GetCandidateIndicator(BciSsvepTargetBinding binding, string targetId)
        {
            FieldInfo field = typeof(BciSsvepTargetBinding).GetField(
                "m_candidateIndicatorsByTargetId",
                BindingFlags.Instance | BindingFlags.NonPublic);
            var indicators = (System.Collections.Generic.Dictionary<string, LineRenderer>)field.GetValue(binding);
            return indicators.TryGetValue(targetId, out LineRenderer indicator) ? indicator : null;
        }

        private static void InvokeStableAnchor(BciSsvepTargetBinding binding, StableWorldAnchorSnapshot anchor)
        {
            MethodInfo method = typeof(BciSsvepTargetBinding).GetMethod(
                "OnStableWorldAnchorUpdated",
                BindingFlags.Instance | BindingFlags.NonPublic);
            method.Invoke(binding, new object[] { anchor });
        }

        private static void InvokeLifecycle(BciTargetBatchController controller, string methodName)
        {
            MethodInfo method = typeof(BciTargetBatchController).GetMethod(
                methodName,
                BindingFlags.Instance | BindingFlags.NonPublic);
            method.Invoke(controller, null);
        }

        private static void InvokeLifecycle(BciSsvepTargetBinding binding, string methodName)
        {
            MethodInfo method = typeof(BciSsvepTargetBinding).GetMethod(
                methodName,
                BindingFlags.Instance | BindingFlags.NonPublic);
            method.Invoke(binding, null);
        }

        private static object InvokePrivate(object target, string methodName, params object[] arguments)
        {
            MethodInfo method = target.GetType().GetMethod(
                methodName,
                BindingFlags.Instance | BindingFlags.NonPublic);
            return method.Invoke(target, arguments);
        }

        private static T GetPrivateField<T>(object target, string fieldName)
        {
            FieldInfo field = target.GetType().GetField(
                fieldName,
                BindingFlags.Instance | BindingFlags.NonPublic);
            return (T)field.GetValue(target);
        }

        private static BciTargetSelectionResult SelectionResult(string selectionId, int slot, string targetId)
        {
            return new BciTargetSelectionResult(
                selectionId,
                slot,
                new BciSelectionTarget(slot, new StableWorldAnchorSnapshot(
                    targetId, "block", StableTargetState.Active, new Vector3(slot, 0f, 2f))),
                System.DateTime.UtcNow);
        }
    }
}
