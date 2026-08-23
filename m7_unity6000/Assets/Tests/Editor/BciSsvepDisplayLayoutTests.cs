using System.Reflection;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciSsvepDisplayLayoutTests
    {
        [Test]
        public void ExperimentalSize_IsLargerThanThePreviousRuntimeQuad()
        {
            Assert.That(BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters, Is.EqualTo(0.32f));
            Assert.That(BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters, Is.GreaterThan(0.16f));
        }

        [Test]
        public void CloseThreeAnchorLayout_IsDeterministicAndHasNoViewSpaceOverlap()
        {
            var anchors = new[]
            {
                Anchor("target-0", new Vector3(-0.01f, 0f, 2f)),
                Anchor("target-1", new Vector3(0f, 0f, 2f)),
                Anchor("target-2", new Vector3(0.01f, 0f, 2f))
            };
            var visible = new[] { true, true, true };
            var first = new Vector3[3];
            var second = new Vector3[3];

            BciSsvepDisplayLayout.CalculatePositions(
                anchors, visible, Vector3.zero, Vector3.right, Vector3.up, first);
            BciSsvepDisplayLayout.CalculatePositions(
                anchors, visible, Vector3.zero, Vector3.right, Vector3.up, second);

            for (int slot = 0; slot < 3; slot++)
                Assert.That(second[slot], Is.EqualTo(first[slot]));

            Assert.That(
                BciSsvepDisplayLayout.HasViewSpaceOverlap(
                    first[0], first[1], Vector3.right, Vector3.up, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters),
                Is.False);
            Assert.That(
                BciSsvepDisplayLayout.HasViewSpaceOverlap(
                    first[1], first[2], Vector3.right, Vector3.up, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters),
                Is.False);
        }

        [Test]
        public void LayoutFreezeGate_HoldsUntilEveryActiveSelectionHasTerminated()
        {
            var gate = new BciSelectionLayoutFreezeGate();

            Assert.That(gate.Begin("selection-a"), Is.True);
            Assert.That(gate.Begin("selection-a"), Is.False);
            Assert.That(gate.Begin("selection-b"), Is.True);
            Assert.That(gate.IsFrozen, Is.True);
            Assert.That(gate.End("selection-a"), Is.True);
            Assert.That(gate.IsFrozen, Is.True);
            Assert.That(gate.End("selection-b"), Is.True);
            Assert.That(gate.IsFrozen, Is.False);
        }

        [Test]
        public void LeaderLine_EndsAtTheAnchorForItsOwnSlot()
        {
            var anchors = new[]
            {
                Anchor("target-0", new Vector3(-1f, 0f, 2f)),
                Anchor("target-1", new Vector3(0f, 0f, 2f)),
                Anchor("target-2", new Vector3(1f, 0f, 2f))
            };
            var positions = new Vector3[3];
            BciSsvepDisplayLayout.CalculatePositions(
                anchors, new[] { true, true, true }, Vector3.zero, Vector3.right, Vector3.up, positions);

            for (int slot = 0; slot < 3; slot++)
            {
                Vector3 start = BciSsvepDisplayLayout.CalculateLeaderLineStart(
                    positions[slot],
                    anchors[slot].WorldPosition,
                    BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);

                Assert.That(
                    Vector3.Dot(anchors[slot].WorldPosition - start, anchors[slot].WorldPosition - positions[slot]),
                    Is.GreaterThan(0f),
                    "slot " + slot + " leader must point toward its own anchor.");
            }
        }

        [Test]
        public void Binding_FreezeDefersLiveAnchorLayoutUntilSelectionEnds()
        {
            var cameraObject = new GameObject("BciSsvepLayoutCamera");
            var managerObject = new GameObject("BciSsvepLayoutManager");
            var parentObject = new GameObject("BciSsvepLayoutParent");
            var bindingObject = new GameObject("BciSsvepLayoutBinding");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var manager = managerObject.AddComponent<DetectionManager>();

            try
            {
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                InvokeStableAnchor(binding, Anchor("target-0", new Vector3(0f, 0f, 2f)));
                Vector3 beforeFreeze = SlotObject(binding, 0).transform.position;

                binding.FreezeLayout("layout-freeze");
                InvokeStableAnchor(binding, Anchor("target-0", new Vector3(0.8f, 0f, 2f)));
                Assert.That(SlotObject(binding, 0).transform.position, Is.EqualTo(beforeFreeze));

                binding.ReleaseLayout("layout-freeze");
                Assert.That(SlotObject(binding, 0).transform.position, Is.Not.EqualTo(beforeFreeze));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(parentObject);
                UnityEngine.Object.DestroyImmediate(managerObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        private static StableWorldAnchorSnapshot Anchor(string targetId, Vector3 position)
        {
            return new StableWorldAnchorSnapshot(targetId, "cup", StableTargetState.Active, position);
        }

        private static void InvokeStableAnchor(BciSsvepTargetBinding binding, StableWorldAnchorSnapshot anchor)
        {
            MethodInfo method = typeof(BciSsvepTargetBinding).GetMethod(
                "OnStableWorldAnchorUpdated",
                BindingFlags.Instance | BindingFlags.NonPublic);
            method.Invoke(binding, new object[] { anchor });
        }

        private static GameObject SlotObject(BciSsvepTargetBinding binding, int slot)
        {
            FieldInfo field = typeof(BciSsvepTargetBinding).GetField(
                "m_slotObjects",
                BindingFlags.Instance | BindingFlags.NonPublic);
            return ((GameObject[])field.GetValue(binding))[slot];
        }
    }
}
