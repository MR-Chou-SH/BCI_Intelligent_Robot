using System.Collections.Generic;
using System.Reflection;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciPagedBrowsePresentationTests
    {
        private const float ColorChannelTolerance = 1f / 255f + 1e-4f;

        [Test]
        public void BrowseShowsCurrentPageWithoutStartingFormalStimulus()
        {
            Camera camera = null;
            GameObject parentObject = null;
            BciSsvepTargetBinding binding;
            GameObject bindingObject = null;
            try
            {
                binding = CreateBinding(4, out camera, out parentObject, out bindingObject);
                StableWorldAnchorSnapshot[] pageOne =
                {
                    Anchor("red", -1f),
                    Anchor("green", 0f),
                    Anchor("yellow", 1f)
                };

                Assert.That(binding.EnableBatchGroupMode(), Is.True);
                Assert.That(binding.EnablePagedQueueMode(), Is.True);
                Assert.That(binding.ActivateGroup("m16-page-1-epoch-0", pageOne, false), Is.True);
                Assert.That(binding.ShowPagedBrowsePresentation(), Is.True);
                Assert.That(binding.IsVisualFlickerEnabled, Is.True,
                    "Browse must visibly flicker current-page panels.");
                Assert.That(binding.IsFormalStimulusActive, Is.False,
                    "Browse flicker must not open a formal EEG stimulus epoch.");
                Assert.That(binding.FormalStimulusEpochCount, Is.EqualTo(0));

                GameObject[] panels = GetPrivateField<GameObject[]>(binding, "m_slotObjects");
                LineRenderer[] lines = GetPrivateField<LineRenderer[]>(binding, "m_slotLeaderLines");
                for (int slot = 0; slot < 3; slot++)
                {
                    Assert.That(panels[slot].activeSelf, Is.True);
                    Assert.That(lines[slot].gameObject.activeSelf, Is.True);
                    Assert.That(binding.IsSlotActiveCandidate(slot), Is.False,
                        "Browse preview must not start formal SSVEP stimulation.");
                }

                Assert.That(binding.GetCandidateVisualState("red"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("green"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("yellow"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("blue"), Is.EqualTo(BciCandidateVisualState.Inactive));
                Dictionary<string, LineRenderer> indicators =
                    GetPrivateField<Dictionary<string, LineRenderer>>(binding, "m_candidateIndicatorsByTargetId");
                Assert.That(indicators.Count, Is.EqualTo(4));
                Assert.That(indicators["red"].gameObject.activeSelf, Is.True);
                Assert.That(indicators["green"].gameObject.activeSelf, Is.True);
                Assert.That(indicators["yellow"].gameObject.activeSelf, Is.True);
                Assert.That(indicators["blue"].gameObject.activeSelf, Is.True);
                AssertColorApproximately(
                    new Color(0.15f, 0.95f, 0.25f, 1f),
                    indicators["red"].startColor,
                    ColorChannelTolerance);
                AssertColorApproximately(
                    new Color(0.15f, 0.95f, 0.25f, 1f),
                    indicators["green"].startColor,
                    ColorChannelTolerance);
                AssertColorApproximately(
                    new Color(0.15f, 0.95f, 0.25f, 1f),
                    indicators["yellow"].startColor,
                    ColorChannelTolerance);
                AssertColorApproximately(
                    new Color(0.55f, 0.55f, 0.55f, 0.9f),
                    indicators["blue"].startColor,
                    ColorChannelTolerance);

                int browseEpochStartFrame = binding.FormalCommonStartFrame;
                Assert.That(binding.SetExecutionPresentationHidden(false, "m16_selection_open"), Is.True);
                Assert.That(binding.IsFormalStimulusActive, Is.True);
                Assert.That(binding.FormalStimulusEpochCount, Is.EqualTo(1));
                Assert.That(binding.FormalCommonStartFrame, Is.EqualTo(Time.frameCount),
                    "selection_open must re-anchor the formal frame origin.");
                Assert.That(binding.FormalCommonStartFrame, Is.GreaterThanOrEqualTo(browseEpochStartFrame));
                Assert.That(binding.IsSlotActiveCandidate(0), Is.True,
                    "Only selection_open may activate the formal candidate slot.");
                Assert.That(binding.IsSlotActiveCandidate(1), Is.True);
                Assert.That(binding.IsSlotActiveCandidate(2), Is.True);
                Assert.That(binding.SetExecutionPresentationHidden(false, "m16_selection_open_duplicate"), Is.True);
                Assert.That(binding.FormalStimulusEpochCount, Is.EqualTo(1),
                    "Repeated selection_open must not create another formal onset epoch.");
            }
            finally
            {
                Destroy(bindingObject, parentObject, camera == null ? null : camera.gameObject);
            }
        }

        [Test]
        public void PageChangeClosesOldPanelsAndPreservesSelectedTargetVisualState()
        {
            Camera camera = null;
            GameObject parentObject = null;
            BciSsvepTargetBinding binding;
            GameObject bindingObject = null;
            try
            {
                binding = CreateBinding(4, out camera, out parentObject, out bindingObject);
                StableWorldAnchorSnapshot red = Anchor("red", -1f);
                StableWorldAnchorSnapshot green = Anchor("green", 0f);
                StableWorldAnchorSnapshot yellow = Anchor("yellow", 1f);
                StableWorldAnchorSnapshot blue = Anchor("blue", 2f);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);
                Assert.That(binding.EnablePagedQueueMode(), Is.True);

                Assert.That(binding.ActivateGroup("m16-page-1-epoch-0", new[] { red, green, yellow }, false), Is.True);
                Assert.That(binding.ShowPagedBrowsePresentation(), Is.True);
                Assert.That(binding.SetGroupSlotSelected("m16-page-1-epoch-0", 0, true), Is.True);

                Assert.That(binding.EndActiveGroup("m16-page-1-epoch-0"), Is.True);
                Assert.That(binding.ActivateGroup("m16-page-2-epoch-1", new[] { blue }, false), Is.True);
                Assert.That(binding.ShowPagedBrowsePresentation(), Is.True);
                Assert.That(binding.IsVisualFlickerEnabled, Is.True);
                Assert.That(binding.IsFormalStimulusActive, Is.False);

                GameObject[] panels = GetPrivateField<GameObject[]>(binding, "m_slotObjects");
                LineRenderer[] lines = GetPrivateField<LineRenderer[]>(binding, "m_slotLeaderLines");
                Assert.That(panels[0].activeSelf, Is.True);
                Assert.That(lines[0].gameObject.activeSelf, Is.True);
                Assert.That(panels[1].activeSelf, Is.False);
                Assert.That(panels[2].activeSelf, Is.False);
                Assert.That(lines[1].gameObject.activeSelf, Is.False);
                Assert.That(lines[2].gameObject.activeSelf, Is.False);
                Assert.That(binding.GetCandidateVisualState("red"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(binding.GetCandidateVisualState("blue"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("green"), Is.EqualTo(BciCandidateVisualState.Inactive));
                Assert.That(binding.GetCandidateVisualState("yellow"), Is.EqualTo(BciCandidateVisualState.Inactive));

                Assert.That(binding.EndActiveGroup("m16-page-2-epoch-1"), Is.True);
                Assert.That(binding.ActivateGroup("m16-page-1-epoch-2", new[] { red, green, yellow }, false), Is.True);
                Assert.That(binding.ShowPagedBrowsePresentation(), Is.True);
                Assert.That(binding.GetCandidateVisualState("red"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(binding.GetCandidateVisualState("green"), Is.EqualTo(BciCandidateVisualState.Available));
            }
            finally
            {
                Destroy(bindingObject, parentObject, camera == null ? null : camera.gameObject);
            }
        }

        [Test]
        public void CrossPageUndoRebuildsCurrentPageVisualsFromGlobalQueue()
        {
            Camera camera = null;
            GameObject parentObject = null;
            GameObject bindingObject = null;
            GameObject controllerObject = null;
            try
            {
                BciSsvepTargetBinding binding = CreateBinding(
                    4, out camera, out parentObject, out bindingObject);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);
                Assert.That(binding.EnablePagedQueueMode(), Is.True);

                var queue = new BciPagedSelectionQueue(new[]
                {
                    new BciPagedCandidate("block-blue", "blue", "Blue"),
                    new BciPagedCandidate("block-green", "green", "Green"),
                    new BciPagedCandidate("block-yellow", "yellow", "Yellow"),
                    new BciPagedCandidate("block-red", "red", "Red")
                });
                controllerObject = new GameObject("M19CrossPageUndoController");
                BciPagedTargetQueueController controller =
                    controllerObject.AddComponent<BciPagedTargetQueueController>();
                SetPrivateField(controller, "m_queue", queue);
                SetPrivateField(controller, "m_binding", binding);
                SetPrivateField(
                    controller, "m_transport",
                    controllerObject.AddComponent<BciSelectionTransportClient>());
                Dictionary<string, StableWorldAnchorSnapshot> anchors =
                    GetPrivateField<Dictionary<string, StableWorldAnchorSnapshot>>(
                        controller, "m_candidatesByTargetId");
                anchors.Add("blue", Anchor("blue", 2f));
                anchors.Add("green", Anchor("green", 0f));
                anchors.Add("yellow", Anchor("yellow", 1f));
                anchors.Add("red", Anchor("red", -1f));

                MethodInfo activatePage = typeof(BciPagedTargetQueueController).GetMethod(
                    "ActivateCurrentPage", BindingFlags.Instance | BindingFlags.NonPublic);
                Assert.That(activatePage, Is.Not.Null);
                activatePage.Invoke(controller, new object[] { "test_initial_page" });
                AcceptThroughProductionController(controller, "blue", anchors["blue"]);
                AcceptThroughProductionController(controller, "green", anchors["green"]);
                Assert.That(binding.GetCandidateVisualState("blue"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(binding.GetCandidateVisualState("green"), Is.EqualTo(BciCandidateVisualState.Selected));

                Assert.That(controller.NextPage(), Is.True);
                Assert.That(controller.CurrentPage.Candidates.Count, Is.EqualTo(1));
                Assert.That(controller.CurrentPage.Candidates[0].TargetId, Is.EqualTo("red"));
                AcceptThroughProductionController(controller, "red", anchors["red"]);
                Assert.That(controller.PreviousPage(), Is.True);

                Assert.That(controller.UndoLastSelection(), Is.True);
                Assert.That(queue.Queue.Count, Is.EqualTo(2));
                Assert.That(queue.Queue[0].TargetId, Is.EqualTo("blue"));
                Assert.That(queue.Queue[1].TargetId, Is.EqualTo("green"));
                Assert.That(binding.GetCandidateVisualState("blue"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(binding.GetCandidateVisualState("green"), Is.EqualTo(BciCandidateVisualState.Selected));

                Assert.That(controller.NextPage(), Is.True);
                Assert.That(binding.GetCandidateVisualState("red"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.IsVisualFlickerEnabled, Is.True,
                    "The unselected current-page Red panel must resume frame-driven browse flicker.");
                Assert.That(controller.CanStartTriggeredTrial, Is.True,
                    "The page rebuilt from the global queue must make Red selectable again.");

                AcceptThroughProductionController(controller, "red", anchors["red"]);
                Assert.That(queue.Queue.Count, Is.EqualTo(3));
                Assert.That(CountQueuedTarget(queue, "red"), Is.EqualTo(1));
                AcceptThroughProductionController(controller, "red", anchors["red"]);
                Assert.That(queue.Queue.Count, Is.EqualTo(3));
                Assert.That(CountQueuedTarget(queue, "red"), Is.EqualTo(1),
                    "A reselected Red target must still be accepted only once.");
            }
            finally
            {
                Destroy(controllerObject, bindingObject, parentObject, camera == null ? null : camera.gameObject);
            }
        }

        [Test]
        public void PagedHudShowsRobotExecutingAndCompleteStatus()
        {
            GameObject cameraObject = null;
            GameObject controllerObject = null;
            try
            {
                cameraObject = new GameObject("M19RobotStatusCamera");
                cameraObject.tag = "MainCamera";
                cameraObject.AddComponent<Camera>();
                controllerObject = new GameObject("M19RobotStatusController");
                BciPagedTargetQueueController controller =
                    controllerObject.AddComponent<BciPagedTargetQueueController>();
                SetPrivateField(controller, "m_queue", new BciPagedSelectionQueue(new[]
                {
                    new BciPagedCandidate("block-red", "red", "Red")
                }));
                Dictionary<string, StableWorldAnchorSnapshot> anchors =
                    GetPrivateField<Dictionary<string, StableWorldAnchorSnapshot>>(
                        controller, "m_candidatesByTargetId");
                anchors.Add("red", Anchor("red", 0f));
                BciPagedQueueHud hud = controllerObject.AddComponent<BciPagedQueueHud>();
                SetPrivateField(controller, "m_hud", hud);
                hud.Initialize(controller);

                controller.SetRobotExecutionStatus("EXECUTING");
                UnityEngine.UI.Text queueText = GetPrivateField<UnityEngine.UI.Text>(hud, "m_queueText");
                StringAssert.Contains("Robot: EXECUTING", queueText.text);
                controller.SetRobotExecutionStatus("COMPLETE");
                StringAssert.Contains("Robot: COMPLETE", queueText.text);
            }
            finally
            {
                Destroy(controllerObject, cameraObject);
            }
        }

        [Test]
        public void AcceptedDemoSelectionRefreshesTriggerBandToReadyState()
        {
            Camera camera = null;
            GameObject parentObject = null;
            GameObject bindingObject = null;
            GameObject controllerObject = null;
            try
            {
                BciSsvepTargetBinding binding = CreateBinding(4, out camera, out parentObject, out bindingObject);
                string groupId = "m16-page-1-epoch-0";
                StableWorldAnchorSnapshot[] pageOne =
                {
                    Anchor("red", -1f),
                    Anchor("green", 0f),
                    Anchor("yellow", 1f)
                };
                Assert.That(binding.EnableBatchGroupMode(), Is.True);
                Assert.That(binding.EnablePagedQueueMode(), Is.True);
                Assert.That(binding.ActivateGroup(groupId, pageOne, false), Is.True);
                Assert.That(binding.ShowPagedBrowsePresentation(), Is.True);

                var queue = new BciPagedSelectionQueue(new[]
                {
                    new BciPagedCandidate("logical-red", "red", "Red"),
                    new BciPagedCandidate("logical-green", "green", "Green"),
                    new BciPagedCandidate("logical-yellow", "yellow", "Yellow"),
                    new BciPagedCandidate("logical-blue", "blue", "Blue")
                });
                BciPagedSelectionTrial trial = queue.OpenTrial("selection-ui-rearm");
                Assert.That(trial, Is.Not.Null);

                controllerObject = new GameObject("M19AcceptedSelectionRearmController");
                BciPagedTargetQueueController controller = controllerObject.AddComponent<BciPagedTargetQueueController>();
                SetPrivateField(controller, "m_queue", queue);
                SetPrivateField(controller, "m_binding", binding);
                SetPrivateField(controller, "m_activePageGroupId", groupId);
                SetPrivateField(controller, "m_pendingSelectionId", trial.SelectionId);
                Dictionary<string, StableWorldAnchorSnapshot> anchors =
                    GetPrivateField<Dictionary<string, StableWorldAnchorSnapshot>>(controller, "m_candidatesByTargetId");
                for (int index = 0; index < pageOne.Length; index++)
                    anchors.Add(pageOne[index].TargetId, pageOne[index]);

                BciPagedQueueHud hud = controllerObject.AddComponent<BciPagedQueueHud>();
                SetPrivateField(controller, "m_hud", hud);
                hud.Initialize(controller);
                UnityEngine.UI.Button triggerButton =
                    GetPrivateField<UnityEngine.UI.Button>(hud, "m_trigger");
                Assert.That(triggerButton.interactable, Is.False,
                    "The trigger must be disabled while the frozen trial is pending.");

                var result = new BciTargetSelectionResult(
                    trial.SelectionId,
                    0,
                    new BciSelectionTarget(0, pageOne[0]),
                    System.DateTime.UtcNow);
                MethodInfo callback = typeof(BciPagedTargetQueueController).GetMethod(
                    "OnTargetSelected", BindingFlags.Instance | BindingFlags.NonPublic);
                Assert.That(callback, Is.Not.Null);
                callback.Invoke(controller, new object[] { result });

                Assert.That(queue.Queue.Count, Is.EqualTo(1));
                Assert.That(binding.GetCandidateVisualState("red"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(controller.CanStartTriggeredTrial, Is.True,
                    "The accepted decision must restore the controller's Trigger eligibility.");
                Assert.That(triggerButton.interactable, Is.True,
                    "The accepted decision must re-enable the visible Trigger button.");
            }
            finally
            {
                Destroy(controllerObject, bindingObject, parentObject, camera == null ? null : camera.gameObject);
            }
        }

        private static BciSsvepTargetBinding CreateBinding(
            int candidateCount,
            out Camera camera,
            out GameObject parentObject,
            out GameObject bindingObject)
        {
            GameObject cameraObject = new GameObject("M16BrowseCamera");
            cameraObject.tag = "MainCamera";
            camera = cameraObject.AddComponent<Camera>();
            camera.transform.position = Vector3.zero;
            camera.transform.forward = Vector3.forward;

            parentObject = new GameObject("M16BrowseParent");
            bindingObject = new GameObject("M16BrowseBinding");
            BciSsvepTargetBinding binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            binding.ConfigureLayout(
                BciSsvepLayoutMode.ViewLockedHud,
                BciSsvepDisplayLayout.DefaultHudLocalCenter,
                BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                BciSsvepDisplayLayout.HudStimulusSizeMeters);
            Assert.That(binding.InitializeVirtualTargets(
                parentObject.transform,
                BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters), Is.True);

            var candidates = new List<StableWorldAnchorSnapshot>();
            string[] ids = { "red", "green", "yellow", "blue" };
            for (int index = 0; index < candidateCount; index++)
                candidates.Add(Anchor(ids[index], index - 1f));
            Assert.That(binding.SetVirtualTargetCandidates(candidates), Is.True);
            return binding;
        }

        private static StableWorldAnchorSnapshot Anchor(string targetId, float x)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                targetId + "_block",
                StableTargetState.Active,
                new Vector3(x, 0f, 2f),
                0.9f,
                new TargetBoundingBox(100f + x * 10f, 20f, 30f, 30f),
                0d,
                1d);
        }

        private static void AcceptThroughProductionController(
            BciPagedTargetQueueController controller,
            string targetId,
            StableWorldAnchorSnapshot anchor)
        {
            BciPagedSelectionTrial trial = controller.Queue.OpenTrial("cross-page-" + targetId + "-" +
                controller.Queue.Queue.Count);
            Assert.That(trial, Is.Not.Null);
            BciPagedPageCandidate candidate = null;
            for (int index = 0; index < controller.CurrentPage.Candidates.Count; index++)
            {
                if (string.Equals(controller.CurrentPage.Candidates[index].TargetId, targetId,
                    System.StringComparison.Ordinal))
                {
                    candidate = controller.CurrentPage.Candidates[index];
                    break;
                }
            }
            Assert.That(candidate, Is.Not.Null);
            var result = new BciTargetSelectionResult(
                trial.SelectionId,
                candidate.SlotIndex,
                new BciSelectionTarget(candidate.SlotIndex, anchor),
                System.DateTime.UtcNow);
            MethodInfo callback = typeof(BciPagedTargetQueueController).GetMethod(
                "OnTargetSelected", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(callback, Is.Not.Null);
            callback.Invoke(controller, new object[] { result });
        }

        private static int CountQueuedTarget(BciPagedSelectionQueue queue, string targetId)
        {
            int count = 0;
            for (int index = 0; index < queue.Queue.Count; index++)
                if (string.Equals(queue.Queue[index].TargetId, targetId, System.StringComparison.Ordinal))
                    count++;
            return count;
        }

        private static T GetPrivateField<T>(object instance, string fieldName)
        {
            FieldInfo field = instance.GetType().GetField(
                fieldName,
                BindingFlags.Instance | BindingFlags.NonPublic);
            return (T)field.GetValue(instance);
        }

        private static void SetPrivateField<T>(object instance, string fieldName, T value)
        {
            FieldInfo field = instance.GetType().GetField(
                fieldName,
                BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(field, Is.Not.Null, "Expected field " + fieldName);
            field.SetValue(instance, value);
        }

        private static void AssertColorApproximately(Color expected, Color actual, float tolerance)
        {
            Assert.That(Mathf.Abs(actual.r - expected.r), Is.LessThanOrEqualTo(tolerance), "red channel");
            Assert.That(Mathf.Abs(actual.g - expected.g), Is.LessThanOrEqualTo(tolerance), "green channel");
            Assert.That(Mathf.Abs(actual.b - expected.b), Is.LessThanOrEqualTo(tolerance), "blue channel");
            Assert.That(Mathf.Abs(actual.a - expected.a), Is.LessThanOrEqualTo(tolerance), "alpha channel");
        }

        private static void Destroy(params GameObject[] objects)
        {
            for (int index = 0; index < objects.Length; index++)
            {
                if (objects[index] != null)
                    Object.DestroyImmediate(objects[index]);
            }
        }
    }
}
