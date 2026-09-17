using System;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public sealed class M9FrankaRuntimeVisualTests
    {
        private static readonly IDictionary<string, string> MeshBodies =
            new Dictionary<string, string>
            {
                { "link0_0", "fr3_link0" }, { "link0_1", "fr3_link0" },
                { "link0_2", "fr3_link0" }, { "link0_3", "fr3_link0" },
                { "link0_4", "fr3_link0" }, { "link0_5", "fr3_link0" },
                { "link0_6", "fr3_link0" }, { "link1", "fr3_link1" },
                { "link2", "fr3_link2" }, { "link3_0", "fr3_link3" },
                { "link3_1", "fr3_link3" }, { "link4_0", "fr3_link4" },
                { "link4_1", "fr3_link4" }, { "link5_0", "fr3_link5" },
                { "link5_1", "fr3_link5" }, { "link5_2", "fr3_link5" },
                { "link6_0", "fr3_link6" }, { "link6_1", "fr3_link6" },
                { "link6_2", "fr3_link6" }, { "link6_3", "fr3_link6" },
                { "link6_4", "fr3_link6" }, { "link6_5", "fr3_link6" },
                { "link6_6", "fr3_link6" }, { "link6_7", "fr3_link6" },
                { "link7_0", "fr3_link7" }, { "link7_1", "fr3_link7" },
                { "link7_2", "fr3_link7" }, { "link7_3", "fr3_link7" },
                { "umi_base_link", "umi_umi_gripper_base" },
                { "umi_gopro", "umi_umi_gripper_base" },
                { "umi_left_finger_holder", "umi_left_finger_holder" },
                { "umi_left_finger", "umi_left_finger_holder" },
                { "umi_right_finger_holder", "umi_right_finger_holder" },
                { "umi_right_finger", "umi_right_finger_holder" },
            };

        private GameObject m_anchor;
        private GameObject m_robot;

        [SetUp]
        public void SetUp()
        {
            m_anchor = new GameObject("M9RuntimeVisualTestAnchor");
            Type factory = typeof(M9FrankaVisualRig).Assembly.GetType(
                "PassthroughCameraSamples.MultiObjectDetection.M9FrankaVisualFactory",
                true);
            MethodInfo create = factory.GetMethod(
                "Create",
                BindingFlags.Static | BindingFlags.NonPublic);
            Assert.That(create, Is.Not.Null);
            m_robot = (GameObject)create.Invoke(
                null,
                new object[] { m_anchor.transform, new Action<Renderer, Color>((_, __) => { }) });
            Assert.That(m_robot, Is.Not.Null);
        }

        [TearDown]
        public void TearDown()
        {
            if (m_anchor != null)
                UnityEngine.Object.DestroyImmediate(m_anchor);
        }

        [Test]
        public void InstantiatedFactoryKeepsTheArticulatedParentChainAndReviewedScale()
        {
            Assert.That(m_robot.name, Is.EqualTo("FrankaRoot"));
            Assert.That(m_robot.transform.parent, Is.EqualTo(m_anchor.transform));
            Assert.That(m_robot.transform.localPosition, Is.EqualTo(Vector3.zero));
            Assert.That(m_robot.transform.localScale, Is.EqualTo(Vector3.one * 0.7f));

            Transform parent = FindDescendant(m_robot.transform, "fr3_link0");
            Assert.That(parent, Is.Not.Null);
            Assert.That(parent.parent, Is.EqualTo(m_robot.transform));
            for (int jointIndex = 1; jointIndex <= 7; jointIndex++)
            {
                Transform joint = FindDescendant(
                    m_robot.transform,
                    "fr3_joint" + jointIndex);
                Transform child = FindDescendant(
                    m_robot.transform,
                    "fr3_link" + jointIndex);
                Assert.That(joint, Is.Not.Null);
                Assert.That(child, Is.Not.Null);
                Assert.That(joint.parent, Is.EqualTo(parent));
                Assert.That(child.parent, Is.EqualTo(joint));
                parent = child;
            }

            Transform umi = FindDescendant(m_robot.transform, "umi_umi_gripper_base");
            Assert.That(umi, Is.Not.Null);
            Transform attachmentSite = FindDescendant(m_robot.transform, "fr3_attachment_site");
            Assert.That(attachmentSite, Is.Not.Null);
            Assert.That(attachmentSite.parent, Is.EqualTo(parent));
            Assert.That(umi.parent, Is.EqualTo(attachmentSite));
            Assert.That(attachmentSite.localPosition, Is.EqualTo(new Vector3(0f, 0f, 0.107f)));
            Assert.That(attachmentSite.localRotation, Is.EqualTo(Quaternion.identity));
            Assert.That(umi.localPosition, Is.EqualTo(Vector3.zero));
            Assert.That(umi.localRotation, Is.EqualTo(Quaternion.identity));
        }

        [Test]
        public void EveryLicensedVisualInstantiatesUnderItsBodyWithAnEnabledMeshRenderer()
        {
            foreach (KeyValuePair<string, string> expected in MeshBodies)
            {
                Transform body = FindDescendant(m_robot.transform, expected.Value);
                Assert.That(body, Is.Not.Null, expected.Value);

                Transform geometryFrame = FindDirectChild(body, expected.Key + "_GeometryFrame");
                Assert.That(geometryFrame, Is.Not.Null, expected.Key + " geometry frame");
                Vector3 expectedGeometryScale = expected.Key == "umi_base_link" ||
                    expected.Key == "umi_gopro"
                    ? new Vector3(-0.55f, 0.55f, 0.55f)
                    : new Vector3(-1f, 1f, 1f);
                Assert.That(geometryFrame.localScale, Is.EqualTo(expectedGeometryScale), expected.Key);

                Transform importedRoot = FindDirectChild(geometryFrame, expected.Key);
                Assert.That(importedRoot, Is.Not.Null, expected.Key + " imported root");
                Assert.That(importedRoot.localPosition, Is.EqualTo(Vector3.zero), expected.Key);
                Assert.That(importedRoot.localRotation, Is.EqualTo(Quaternion.identity), expected.Key);
                Assert.That(importedRoot.localScale, Is.EqualTo(Vector3.one), expected.Key);

                Renderer[] renderers = importedRoot.GetComponentsInChildren<Renderer>(true);
                Assert.That(renderers, Is.Not.Empty, expected.Key);
                foreach (Renderer renderer in renderers)
                {
                    Assert.That(renderer.enabled, Is.True, expected.Key);
                    Assert.That(renderer.gameObject.activeInHierarchy, Is.True, expected.Key);
                    MeshFilter filter = renderer.GetComponent<MeshFilter>();
                    Assert.That(filter, Is.Not.Null, expected.Key);
                    Assert.That(filter.sharedMesh, Is.Not.Null, expected.Key);
                    Assert.That(filter.sharedMesh.vertexCount, Is.GreaterThan(0), expected.Key);
                }
            }
        }

        [Test]
        public void OldFoldedPoseDoesNotReparentOrDisableTheRenderedChain()
        {
            float[] oldPose = { 0.6079f, 0.2806f, 0.6129f, -2.7923f, 0.4065f, 1.5392f, -0.7853f };
            M9FrankaVisualRig rig = m_robot.GetComponent<M9FrankaVisualRig>();
            Assert.That(rig, Is.Not.Null);
            for (int index = 0; index < oldPose.Length; index++)
                Assert.That(rig.TrySetJointValue("fr3_joint" + (index + 1), oldPose[index]), Is.True);

            foreach (KeyValuePair<string, string> expected in MeshBodies)
            {
                Transform body = FindDescendant(m_robot.transform, expected.Value);
                Transform geometryFrame = FindDirectChild(body, expected.Key + "_GeometryFrame");
                Transform importedRoot = FindDirectChild(geometryFrame, expected.Key);
                Assert.That(importedRoot.parent, Is.EqualTo(geometryFrame), expected.Key);
                foreach (Renderer renderer in importedRoot.GetComponentsInChildren<Renderer>(true))
                {
                    Assert.That(renderer.enabled, Is.True, expected.Key);
                    Assert.That(renderer.gameObject.activeInHierarchy, Is.True, expected.Key);
                }
            }
        }

        private static Transform FindDirectChild(Transform parent, string name)
        {
            for (int index = 0; index < parent.childCount; index++)
            {
                Transform child = parent.GetChild(index);
                if (child.name == name)
                    return child;
            }
            return null;
        }

        private static Transform FindDescendant(Transform root, string name)
        {
            Transform[] descendants = root.GetComponentsInChildren<Transform>(true);
            foreach (Transform descendant in descendants)
            {
                if (descendant.name == name)
                    return descendant;
            }
            return null;
        }
    }
}
