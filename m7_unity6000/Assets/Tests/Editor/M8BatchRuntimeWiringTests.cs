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

                Assert.That(binding.SetGroupSlotSelected("group-1", 0, true), Is.True);
                Assert.That(binding.GetCandidateVisualState("left"), Is.EqualTo(BciCandidateVisualState.Selected));

                Assert.That(binding.EndActiveGroup("group-1"), Is.True);
                binding.SetProcessedTargetIds(new[] { "left", "center", "right" }, new[] { "left" });
                Assert.That(binding.GetCandidateVisualState("left"), Is.EqualTo(BciCandidateVisualState.Submitted));
                Assert.That(binding.GetCandidateVisualState("center"), Is.EqualTo(BciCandidateVisualState.Inactive));
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
                Assert.That(height, Is.GreaterThan(width * 3f));
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
    }
}
