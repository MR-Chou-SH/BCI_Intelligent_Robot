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

        private static StableWorldAnchorSnapshot Anchor(string targetId, float x)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                "bottle",
                StableTargetState.Active,
                new Vector3(x, 0f, 2f),
                0.9f,
                new TargetBoundingBox(x * 10f + 100f, 20f, 30f, 30f),
                0d,
                1d);
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
