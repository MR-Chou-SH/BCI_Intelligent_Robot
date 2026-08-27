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

        [Test]
        public void GroupHandover_PreservesBlueStateAndRejectsChangesDuringSelectionFreeze()
        {
            var cameraObject = new GameObject("M8HandoverCamera");
            var managerObject = new GameObject("M8HandoverManager");
            var parentObject = new GameObject("M8HandoverParent");
            var bindingObject = new GameObject("M8HandoverBinding");
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

                StableWorldAnchorSnapshot oldLeft = Anchor("old-left", -1f);
                StableWorldAnchorSnapshot[] group =
                {
                    oldLeft, Anchor("center", 0f), Anchor("right", 1f)
                };
                foreach (StableWorldAnchorSnapshot anchor in group)
                    InvokeStableAnchor(binding, anchor);
                Assert.That(binding.ActivateGroup("group-handover", group), Is.True);
                Assert.That(binding.SetGroupSlotSelected("group-handover", 0, true), Is.True);

                InvokeStableAnchor(binding, new StableWorldAnchorSnapshot(
                    "old-left", "bottle", StableTargetState.Lost, oldLeft.WorldPosition,
                    oldLeft.Confidence, oldLeft.Bbox, oldLeft.FirstSeen, oldLeft.LastSeen));
                StableWorldAnchorSnapshot replacement = new StableWorldAnchorSnapshot(
                    "new-left", "bottle", StableTargetState.Active, new Vector3(-0.988f, 0f, 2f),
                    0.9f, new TargetBoundingBox(104f, 20f, 30f, 30f), 0d, 1d);
                InvokeStableAnchor(binding, replacement);
                BciGroupTargetReassociationDecision decision = new BciGroupTargetReassociationDecision(
                    BciGroupTargetReassociationOutcome.Accepted, 0, oldLeft, replacement,
                    0.012f, 0f, 0d, 1, 1, "test_unique");

                binding.FreezeLayout("selection-1");
                Assert.That(binding.TryApplyGroupTargetHandover("group-handover", decision), Is.False);
                binding.ReleaseLayout("selection-1");
                Assert.That(binding.TryApplyGroupTargetHandover("group-handover", decision), Is.True);
                Assert.That(binding.GetCandidateVisualState("new-left"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(binding.GetCandidateVisualState("old-left"), Is.EqualTo(BciCandidateVisualState.Inactive));

                Assert.That(binding.SetGroupSlotSelected("group-handover", 0, false), Is.True);
                Assert.That(binding.CreateSelectionSnapshot().ResolveClassIndex(0).Target.TargetId, Is.EqualTo("new-left"));
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
