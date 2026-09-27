using System.Collections.Generic;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using BCIIntelligentRobot.VirtualManipulation;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public sealed class M20AssistiveDeskSceneTests
    {
        [Test]
        public void CanonicalResourceBuildsPagedCatalogWithFrozenSlotsAndSixExplicitTargets()
        {
            M20AssistiveDeskSceneSpec spec = M20AssistiveDeskUnityScene.LoadFromResources();
            M9VirtualBlockCatalogData catalog = M20AssistiveDeskUnityScene.CreatePagedQueueCatalog(spec);

            Assert.That(spec.candidatePageSize, Is.EqualTo(3));
            Assert.That(catalog.tableSizeMeters, Is.EqualTo(new Vector3(0.96f, 0.05f, 0.56f)));
            Assert.That(catalog.slots.Length, Is.EqualTo(3));
            Assert.That(catalog.slots[0].nominalFrequencyHz, Is.EqualTo(7.2f));
            Assert.That(catalog.slots[1].nominalFrequencyHz, Is.EqualTo(9f));
            Assert.That(catalog.slots[2].nominalFrequencyHz, Is.EqualTo(12f));
            Assert.That(catalog.blocks.Length, Is.EqualTo(6));
            Assert.That(catalog.blocks[0].targetId, Is.EqualTo("assist_medicine_box"));
            Assert.That(catalog.blocks[1].targetId, Is.EqualTo("assist_storage_box"));
            Assert.That(catalog.blocks[2].targetId, Is.EqualTo("assist_phone"));
            Assert.That(catalog.blocks[3].targetId, Is.EqualTo("assist_button_switch"));
            Assert.That(catalog.blocks[4].targetId, Is.EqualTo("assist_wireless_charger"));
            Assert.That(catalog.blocks[5].targetId, Is.EqualTo("assist_user_zone"));

            var bindingHorizontalOrder = new List<StableWorldAnchorSnapshot>();
            for (int index = catalog.blocks.Length - 1; index >= 0; index--)
            {
                M9VirtualBlockDefinition block = catalog.blocks[index];
                bindingHorizontalOrder.Add(new StableWorldAnchorSnapshot(
                    block.targetId,
                    block.semanticLabel,
                    StableTargetState.Active,
                    Vector3.zero));
            }
            List<StableWorldAnchorSnapshot> visualOrder = BciPagedTargetQueueController.OrderInitialCandidates(
                bindingHorizontalOrder,
                catalog,
                useCatalogOrder: true);
            Assert.That(visualOrder.ConvertAll(item => item.TargetId), Is.EqualTo(new[]
            {
                "assist_medicine_box",
                "assist_storage_box",
                "assist_phone",
                "assist_button_switch",
                "assist_wireless_charger",
                "assist_user_zone"
            }));

            Vector3 medicine = M20AssistiveDeskUnityScene.ToUnityWorkspacePosition(
                FindEntity(spec, "assist_medicine_box").positionMeters);
            Vector3 phone = M20AssistiveDeskUnityScene.ToUnityWorkspacePosition(
                FindEntity(spec, "assist_phone").positionMeters);
            Assert.That(medicine.x, Is.GreaterThan(0f));
            Assert.That(phone.x, Is.LessThan(0f));
            Assert.That(M20AssistiveDeskUnityScene.ToUnityWorkspaceDimensions(new Vector3(0.1f, 0.06f, 0.03f)),
                Is.EqualTo(new Vector3(0.1f, 0.03f, 0.06f)));
        }

        [Test]
        public void GeneratedGeometryUsesPositiveScaleAndMatchesHingeAndButtonArticulation()
        {
            M20AssistiveDeskSceneSpec spec = M20AssistiveDeskUnityScene.LoadFromResources();
            var owner = new GameObject("M20 assistive articulation test owner");
            var blocks = new GameObject("M20 assistive articulation test blocks");
            blocks.transform.SetParent(owner.transform, false);
            List<GameObject> targets = null;
            try
            {
                List<BCIIntelligentRobot.Vision.StableWorldAnchorSnapshot> candidates =
                    M20AssistiveDeskUnityScene.CreateTargets(
                        spec,
                        blocks.transform,
                        owner,
                        ApplyTestColor,
                        out targets,
                        out M20AssistiveDeskArticulationController controller);

                Assert.That(candidates.Count, Is.EqualTo(6));
                Assert.That(targets.Count, Is.EqualTo(6));
                Assert.That(controller.IsInitialized, Is.True);
                Assert.That(owner.GetComponentsInChildren<Collider>(true).Length, Is.GreaterThan(10));
                foreach (Transform child in owner.GetComponentsInChildren<Transform>(true))
                {
                    Vector3 scale = child.localScale;
                    Assert.That(scale.x, Is.GreaterThan(0f), child.name);
                    Assert.That(scale.y, Is.GreaterThan(0f), child.name);
                    Assert.That(scale.z, Is.GreaterThan(0f), child.name);
                }
                for (int index = 0; index < targets.Count; index++)
                {
                    M20AssistiveDeskEntity entity = FindEntity(spec, spec.candidateOrderFarToNearLeftToRight[index]);
                    Assert.That(targets[index].transform.localPosition,
                        Is.EqualTo(M20AssistiveDeskUnityScene.ToUnityWorkspacePosition(entity.positionMeters)));
                    Vector3 boundsSize = GetLocalColliderBoundsSize(targets[index].transform);
                    Vector3 expectedSize = M20AssistiveDeskUnityScene.ToUnityWorkspaceDimensions(entity.dimensionsMeters);
                    Assert.That(boundsSize.x, Is.EqualTo(expectedSize.x).Within(0.000001f), entity.semanticId);
                    Assert.That(boundsSize.y, Is.EqualTo(expectedSize.y).Within(0.000001f), entity.semanticId);
                    Assert.That(boundsSize.z, Is.EqualTo(expectedSize.z).Within(0.000001f), entity.semanticId);
                }

                Transform lidPivot = blocks.transform.Find("M20_assist_storage_lid_hinge");
                Assert.That(lidPivot, Is.Not.Null);
                controller.SetStorageLidOpen(true);
                Quaternion expectedOpen = Quaternion.AngleAxis(-100f, Vector3.right);
                Assert.That(Quaternion.Angle(lidPivot.localRotation, expectedOpen), Is.LessThan(0.01f));
                controller.SetStorageLidOpen(false);
                Assert.That(Quaternion.Angle(lidPivot.localRotation, Quaternion.identity), Is.LessThan(0.01f));

                Transform cap = blocks.transform.Find("M20_assist_button_cap_slide");
                Assert.That(cap, Is.Not.Null);
                float unpressedHeight = cap.localPosition.y;
                controller.SetButtonPressed(true);
                Assert.That(unpressedHeight - cap.localPosition.y, Is.EqualTo(0.005f).Within(0.000001f));
                controller.SetButtonPressed(false);
                Assert.That(cap.localPosition.y, Is.EqualTo(unpressedHeight).Within(0.000001f));
            }
            finally
            {
                Object.DestroyImmediate(owner);
            }
        }

        private static M20AssistiveDeskEntity FindEntity(M20AssistiveDeskSceneSpec spec, string semanticId)
        {
            foreach (M20AssistiveDeskEntity entity in spec.entities)
                if (entity.semanticId == semanticId)
                    return entity;
            Assert.Fail("Missing canonical entity " + semanticId);
            return null;
        }

        private static void ApplyTestColor(Renderer renderer, Color color)
        {
            // Geometry tests intentionally avoid creating editor materials.
        }

        private static Vector3 GetLocalColliderBoundsSize(Transform root)
        {
            BoxCollider[] colliders = root.GetComponentsInChildren<BoxCollider>(true);
            Vector3 minimum = new Vector3(float.PositiveInfinity, float.PositiveInfinity, float.PositiveInfinity);
            Vector3 maximum = new Vector3(float.NegativeInfinity, float.NegativeInfinity, float.NegativeInfinity);
            foreach (BoxCollider collider in colliders)
            {
                Vector3 size = Vector3.Scale(collider.size, collider.transform.localScale);
                Vector3 center = collider.transform.localPosition +
                    Vector3.Scale(collider.center, collider.transform.localScale);
                minimum = Vector3.Min(minimum, center - size / 2f);
                maximum = Vector3.Max(maximum, center + size / 2f);
            }
            return maximum - minimum;
        }
    }
}
