using System.Reflection;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciSsvepBatchGroupBindingTests
    {
        [Test]
        public void BatchGroup_FreezesSlotOrderAndMasksSelectedSlotFromLaterSnapshots()
        {
            var cameraObject = new GameObject("M8BatchCamera");
            var managerObject = new GameObject("M8BatchManager");
            var parentObject = new GameObject("M8BatchParent");
            var bindingObject = new GameObject("M8BatchBinding");
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
                    Anchor("left", -1f), Anchor("center", 0f), Anchor("right", 1f)
                };
                Assert.That(binding.ActivateGroup("group-1", group), Is.True);
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(0).Target.TargetId, Is.EqualTo("left"));
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(2).Target.TargetId, Is.EqualTo("right"));

                InvokeStableAnchor(binding, Anchor("left", 2f));
                InvokeStableAnchor(binding, Anchor("right", -2f));
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(0).Target.TargetId, Is.EqualTo("left"));
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(2).Target.TargetId, Is.EqualTo("right"));

                Assert.That(binding.SetGroupSlotSelected("group-1", 0, true), Is.True);
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(0).Rejection,
                    Is.EqualTo(BciSelectionRejection.TargetInvalid));
                Assert.That(binding.IsSlotActiveCandidate(0), Is.False);
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(1).Target.TargetId, Is.EqualTo("center"));
                Assert.That(binding.SetGroupSlotSelected("group-1", 0, false), Is.True);
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(0).Target.TargetId, Is.EqualTo("left"));
                Assert.That(binding.IsSlotActiveCandidate(0), Is.True);
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
    }
}
