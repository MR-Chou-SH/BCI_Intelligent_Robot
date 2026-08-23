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
        public void ViewLockedHudPositions_AreDeterministicEqualHeightAndOrderedBySlot()
        {
            var first = new Vector3[3];
            var second = new Vector3[3];
            Vector3 center = BciSsvepDisplayLayout.DefaultHudLocalCenter;

            BciSsvepDisplayLayout.CalculateViewLockedPositions(
                center, BciSsvepDisplayLayout.HudHorizontalSpacingMeters, first);
            BciSsvepDisplayLayout.CalculateViewLockedPositions(
                center, BciSsvepDisplayLayout.HudHorizontalSpacingMeters, second);

            Assert.That(BciSsvepLayoutMode.ViewLockedHud, Is.Not.EqualTo(BciSsvepLayoutMode.WorldSpaceExperimental));
            for (int slot = 0; slot < 3; slot++)
            {
                Assert.That(second[slot], Is.EqualTo(first[slot]));
                Assert.That(first[slot].y, Is.EqualTo(center.y));
                Assert.That(first[slot].z, Is.EqualTo(center.z));
            }

            Assert.That(first[1].x - first[0].x, Is.EqualTo(BciSsvepDisplayLayout.HudHorizontalSpacingMeters));
            Assert.That(first[2].x - first[1].x, Is.EqualTo(BciSsvepDisplayLayout.HudHorizontalSpacingMeters));
        }

        [Test]
        public void ViewLockedHudBinding_PreservesCameraLocalPoseAndUniformScale()
        {
            var cameraObject = new GameObject("BciHudCamera");
            var managerObject = new GameObject("BciHudManager");
            var parentObject = new GameObject("BciHudParent");
            var bindingObject = new GameObject("BciHudBinding");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var manager = managerObject.AddComponent<DetectionManager>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                for (int slot = 0; slot < 3; slot++)
                    InvokeStableAnchor(binding, Anchor("hud-target-" + slot, new Vector3(slot, 0f, 2f)));
                InvokeLateUpdate(binding);

                GameObject[] slots = SlotObjects(binding);
                Vector3[] initialLocalPositions = new Vector3[3];
                for (int slot = 0; slot < 3; slot++)
                {
                    initialLocalPositions[slot] = slots[slot].transform.localPosition;
                    Assert.That(slots[slot].transform.localScale, Is.EqualTo(Vector3.one * BciSsvepDisplayLayout.HudStimulusSizeMeters));
                }

                Vector3 initialWorldPosition = slots[1].transform.position;
                cameraObject.transform.SetPositionAndRotation(new Vector3(1f, 0.1f, 0.2f), Quaternion.Euler(0f, 35f, 0f));
                InvokeLateUpdate(binding);

                for (int slot = 0; slot < 3; slot++)
                    Assert.That(slots[slot].transform.localPosition, Is.EqualTo(initialLocalPositions[slot]));
                Assert.That(slots[1].transform.position, Is.Not.EqualTo(initialWorldPosition));
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
        public void ViewLockedHudFreezeKeepsAssociationWhileCameraContinuesToMove()
        {
            var cameraObject = new GameObject("BciHudFreezeCamera");
            var managerObject = new GameObject("BciHudFreezeManager");
            var parentObject = new GameObject("BciHudFreezeParent");
            var bindingObject = new GameObject("BciHudFreezeBinding");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
            var manager = managerObject.AddComponent<DetectionManager>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                binding.Initialize(manager, parentObject.transform, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
                InvokeStableAnchor(binding, Anchor("frozen-target", new Vector3(0f, 0f, 2f)));
                InvokeLateUpdate(binding);
                LineRenderer line = LeaderLines(binding)[0];
                Vector3 frozenEnd = line.GetPosition(1);
                Vector3 frozenLocalPosition = SlotObjects(binding)[0].transform.localPosition;

                binding.FreezeLayout("hud-freeze");
                InvokeStableAnchor(binding, Anchor("frozen-target", new Vector3(1f, 0f, 2f)));
                cameraObject.transform.position = new Vector3(0.5f, 0f, 0f);
                InvokeLateUpdate(binding);

                Assert.That(SlotObjects(binding)[0].transform.localPosition, Is.EqualTo(frozenLocalPosition));
                Assert.That(line.GetPosition(1), Is.EqualTo(frozenEnd));

                binding.ReleaseLayout("hud-freeze");
                Assert.That(line.GetPosition(1), Is.EqualTo(new Vector3(1f, 0f, 2f)));
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
            return SlotObjects(binding)[slot];
        }

        private static GameObject[] SlotObjects(BciSsvepTargetBinding binding)
        {
            FieldInfo field = typeof(BciSsvepTargetBinding).GetField(
                "m_slotObjects",
                BindingFlags.Instance | BindingFlags.NonPublic);
            return (GameObject[])field.GetValue(binding);
        }

        private static LineRenderer[] LeaderLines(BciSsvepTargetBinding binding)
        {
            FieldInfo field = typeof(BciSsvepTargetBinding).GetField(
                "m_slotLeaderLines",
                BindingFlags.Instance | BindingFlags.NonPublic);
            return (LineRenderer[])field.GetValue(binding);
        }

        private static void InvokeLateUpdate(BciSsvepTargetBinding binding)
        {
            MethodInfo method = typeof(BciSsvepTargetBinding).GetMethod(
                "LateUpdate",
                BindingFlags.Instance | BindingFlags.NonPublic);
            method.Invoke(binding, null);
        }
    }
}
