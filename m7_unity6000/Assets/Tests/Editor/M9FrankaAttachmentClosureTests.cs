using System;
using System.Collections.Generic;
using System.Reflection;
using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public sealed class M9FrankaAttachmentClosureTests
    {
        private const float PositionToleranceMeters = 1e-5f;
        private const float RotationToleranceDegrees = 0.02f;
        private const float FrameAxisToleranceDegrees = 0.02f;
        private const string GeometryFrameSuffix = "_GeometryFrame";

        private static readonly float[][] ValidPoses =
        {
            new[] { 0.084207f, -1.087620f, 1.221978f, -2.349414f, 0.992534f, 1.681765f, 0.822657f },
            new[] { 0.6079f, 0.2806f, 0.6129f, -2.7923f, 0.4065f, 1.5392f, -0.7853f },
            new[] { 0.25f, -1.25f, 1.10f, -2.20f, 0.60f, 1.40f, -0.40f },
        };

        private GameObject m_anchor;
        private GameObject m_robot;

        [SetUp]
        public void SetUp()
        {
            m_anchor = new GameObject("M9AttachmentClosureTestAnchor");
            m_anchor.transform.SetPositionAndRotation(
                new Vector3(1.25f, 0.95f, -0.75f),
                Quaternion.Euler(0f, 35f, 0f));

            Type factory = typeof(M9FrankaVisualRig).Assembly.GetType(
                "PassthroughCameraSamples.MultiObjectDetection.M9FrankaVisualFactory",
                true);
            MethodInfo create = factory.GetMethod("Create", BindingFlags.Static | BindingFlags.NonPublic);
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
        public void ReportedSeamsMapToJoint5AndTheLink7ToUmiAttachmentWithTheirActualRenderers()
        {
            Transform link4 = Find("fr3_link4");
            Transform joint5 = Find("fr3_joint5");
            Transform link5 = Find("fr3_link5");
            Assert.That(joint5.parent, Is.EqualTo(link4));
            Assert.That(link5.parent, Is.EqualTo(joint5));
            AssertCanonicalRendererIdentities(link4, "link4_0", "link4_1");
            AssertCanonicalRendererIdentities(link5, "link5_0", "link5_1", "link5_2");

            Transform link6 = Find("fr3_link6");
            Transform joint7 = Find("fr3_joint7");
            Transform link7 = Find("fr3_link7");
            Assert.That(joint7.parent, Is.EqualTo(link6));
            Assert.That(link7.parent, Is.EqualTo(joint7));
            AssertCanonicalRendererIdentities(link6,
                "link6_0", "link6_1", "link6_2", "link6_3",
                "link6_4", "link6_5", "link6_6", "link6_7");
            AssertCanonicalRendererIdentities(link7, "link7_0", "link7_1", "link7_2", "link7_3");

            Transform attachmentSite = Find("fr3_attachment_site");
            Transform umiBase = Find("umi_umi_gripper_base");
            Assert.That(attachmentSite.parent, Is.EqualTo(link7));
            Assert.That(umiBase.parent, Is.EqualTo(attachmentSite));
            AssertCanonicalRendererIdentities(umiBase,
                "umi_linear_guide_rail", "umi_left_mirror", "umi_right_mirror",
                "umi_base_link", "umi_gopro");
            Transform leftFingerJoint = Find("umi_left_finger_joint");
            Transform leftFingerHolder = Find("umi_left_finger_holder");
            Assert.That(leftFingerJoint, Is.Not.Null, "left UMI finger joint transform exists");
            Assert.That(leftFingerHolder, Is.Not.Null, "left UMI finger holder transform exists");
            Assert.That(leftFingerJoint.parent, Is.EqualTo(umiBase), "left UMI finger joint parent");
            Assert.That(leftFingerHolder.parent, Is.EqualTo(leftFingerJoint), "left UMI holder attachment hierarchy");
            Assert.That(leftFingerHolder.GetComponent<Renderer>(), Is.Null, "left UMI holder is a structure transform");
            Assert.That(Find("umi_left_rail_block").parent, Is.EqualTo(leftFingerHolder), "left UMI rail block hierarchy");
            Assert.That(Find("umi_left_finger_GeometryFrame").parent, Is.EqualTo(leftFingerHolder),
                "left UMI finger visual hierarchy");
            AssertCanonicalRendererIdentities(leftFingerHolder,
                "umi_left_rail_block", "umi_left_finger");

            Transform rightFingerJoint = Find("umi_right_finger_joint");
            Transform rightFingerHolder = Find("umi_right_finger_holder");
            Assert.That(rightFingerJoint, Is.Not.Null, "right UMI finger joint transform exists");
            Assert.That(rightFingerHolder, Is.Not.Null, "right UMI finger holder transform exists");
            Assert.That(rightFingerJoint.parent, Is.EqualTo(umiBase), "right UMI finger joint parent");
            Assert.That(rightFingerHolder.parent, Is.EqualTo(rightFingerJoint), "right UMI holder attachment hierarchy");
            Assert.That(rightFingerHolder.GetComponent<Renderer>(), Is.Null, "right UMI holder is a structure transform");
            Assert.That(Find("umi_right_rail_block").parent, Is.EqualTo(rightFingerHolder), "right UMI rail block hierarchy");
            Assert.That(Find("umi_right_finger_GeometryFrame").parent, Is.EqualTo(rightFingerHolder),
                "right UMI finger visual hierarchy");
            AssertCanonicalRendererIdentities(rightFingerHolder,
                "umi_right_rail_block", "umi_right_finger");
        }

        [Test]
        public void JointAndFlangeAttachmentFramesCloseAcrossThreeLegalQposes()
        {
            M9FrankaVisualRig rig = m_robot.GetComponent<M9FrankaVisualRig>();
            Assert.That(rig, Is.Not.Null);

            for (int poseIndex = 0; poseIndex < ValidPoses.Length; poseIndex++)
            {
                SetPose(rig, ValidPoses[poseIndex]);
                string poseName = poseIndex == 0 ? "M9.5 presentation" :
                    poseIndex == 1 ? "previous presentation" : "alternate legal pose";

                AssertJointClosure(
                    poseName,
                    "Issue A link4/joint5 to link5",
                    "fr3_joint5",
                    "fr3_link5",
                    new Vector3(-0.0825f, 0.384f, 0f),
                    MujocoQuaternion(0.707107f, -0.707107f, 0f, 0f),
                    ValidPoses[poseIndex][4]);

                AssertJointClosure(
                    poseName,
                    "Distal joint7 link6 to link7",
                    "fr3_joint7",
                    "fr3_link7",
                    new Vector3(0.088f, 0f, 0f),
                    MujocoQuaternion(0.707107f, 0.707107f, 0f, 0f),
                    ValidPoses[poseIndex][6]);

                AssertUmiAttachmentClosure(poseName);
            }
        }

        private void AssertJointClosure(
            string poseName,
            string interfaceName,
            string jointName,
            string childName,
            Vector3 expectedLocalPosition,
            Quaternion zeroValueBodyRotation,
            float jointValueRadians)
        {
            Transform joint = Find(jointName);
            Transform child = Find(childName);
            Transform parent = joint.parent;
            Assert.That(parent, Is.Not.Null, interfaceName + " parent");
            Assert.That(child.parent, Is.EqualTo(joint), interfaceName + " hierarchy");
            Assert.That(joint.localPosition, Is.EqualTo(expectedLocalPosition), interfaceName + " fixed body translation");
            Quaternion expectedLocalRotation = zeroValueBodyRotation *
                Quaternion.AngleAxis(jointValueRadians * Mathf.Rad2Deg, Vector3.forward);
            Assert.That(Quaternion.Angle(joint.localRotation, expectedLocalRotation),
                Is.LessThanOrEqualTo(RotationToleranceDegrees), interfaceName + " fixed frame plus qpos");
            Assert.That(child.localPosition, Is.EqualTo(Vector3.zero), interfaceName + " child attachment origin");
            Assert.That(child.localRotation, Is.EqualTo(Quaternion.identity), interfaceName + " child attachment orientation");

            Vector3 parentFrameWorldPosition = parent.TransformPoint(joint.localPosition);
            Quaternion parentFrameWorldRotation = parent.rotation * joint.localRotation;
            AssertWorldClosure(poseName, interfaceName, parentFrameWorldPosition, parentFrameWorldRotation,
                child.position, child.rotation);
        }

        private void AssertUmiAttachmentClosure(string poseName)
        {
            Transform link7 = Find("fr3_link7");
            Transform site = Find("fr3_attachment_site");
            Transform umiBase = Find("umi_umi_gripper_base");
            Assert.That(site.parent, Is.EqualTo(link7));
            Assert.That(site.localPosition, Is.EqualTo(new Vector3(0f, 0f, 0.107f)));
            Assert.That(site.localRotation, Is.EqualTo(Quaternion.identity));
            Assert.That(umiBase.parent, Is.EqualTo(site));
            Assert.That(umiBase.localPosition, Is.EqualTo(Vector3.zero));
            Assert.That(umiBase.localRotation, Is.EqualTo(Quaternion.identity));

            AssertWorldClosure(
                poseName,
                "Issue B link7 attachment_site to UMI base",
                site.position,
                site.rotation,
                umiBase.position,
                umiBase.rotation);
        }

        private void AssertWorldClosure(
            string poseName,
            string interfaceName,
            Vector3 parentWorldPosition,
            Quaternion parentWorldRotation,
            Vector3 childWorldPosition,
            Quaternion childWorldRotation)
        {
            Vector3 parentRobotLocalPosition = m_robot.transform.InverseTransformPoint(parentWorldPosition);
            Vector3 childRobotLocalPosition = m_robot.transform.InverseTransformPoint(childWorldPosition);
            float localPositionDelta = Vector3.Distance(parentRobotLocalPosition, childRobotLocalPosition);
            float worldPositionDelta = Vector3.Distance(parentWorldPosition, childWorldPosition);
            float rotationDelta = Quaternion.Angle(parentWorldRotation, childWorldRotation);
            float forwardDelta = Vector3.Angle(
                parentWorldRotation * Vector3.forward,
                childWorldRotation * Vector3.forward);
            float upDelta = Vector3.Angle(
                parentWorldRotation * Vector3.up,
                childWorldRotation * Vector3.up);

            TestContext.WriteLine(
                poseName + " | " + interfaceName +
                " | parentWorldPos=" + parentWorldPosition.ToString("F6") +
                " | childWorldPos=" + childWorldPosition.ToString("F6") +
                " | parentWorldRot=" + parentWorldRotation.ToString("F6") +
                " | childWorldRot=" + childWorldRotation.ToString("F6") +
                " | parentForward=" + (parentWorldRotation * Vector3.forward).ToString("F6") +
                " | childForward=" + (childWorldRotation * Vector3.forward).ToString("F6") +
                " | parentUp=" + (parentWorldRotation * Vector3.up).ToString("F6") +
                " | childUp=" + (childWorldRotation * Vector3.up).ToString("F6") +
                " | positionDeltaRobotLocalM=" + localPositionDelta.ToString("F8") +
                " | positionDeltaWorldM=" + worldPositionDelta.ToString("F8") +
                " | rotationDeltaDeg=" + rotationDelta.ToString("F6") +
                " | forwardDeltaDeg=" + forwardDelta.ToString("F6") +
                " | upDeltaDeg=" + upDelta.ToString("F6"));

            Assert.That(localPositionDelta, Is.LessThanOrEqualTo(PositionToleranceMeters), interfaceName + " position closure");
            Assert.That(rotationDelta, Is.LessThanOrEqualTo(RotationToleranceDegrees), interfaceName + " rotation closure");
            Assert.That(forwardDelta, Is.LessThanOrEqualTo(FrameAxisToleranceDegrees), interfaceName + " forward axis");
            Assert.That(upDelta, Is.LessThanOrEqualTo(FrameAxisToleranceDegrees), interfaceName + " up axis");
        }

        private static void SetPose(M9FrankaVisualRig rig, float[] pose)
        {
            for (int jointIndex = 0; jointIndex < pose.Length; jointIndex++)
            {
                Assert.That(rig.TrySetJointValue("fr3_joint" + (jointIndex + 1), pose[jointIndex]), Is.True);
            }
        }

        private static Quaternion MujocoQuaternion(float w, float x, float y, float z)
        {
            return new Quaternion(x, y, z, w);
        }

        private Transform Find(string name)
        {
            Transform[] descendants = m_robot.GetComponentsInChildren<Transform>(true);
            foreach (Transform descendant in descendants)
            {
                if (descendant.name == name)
                    return descendant;
            }
            Assert.Fail("Missing runtime transform " + name);
            return null;
        }

        private void AssertCanonicalRendererIdentities(Transform body, params string[] expectedNames)
        {
            var actualNames = new HashSet<string>(StringComparer.Ordinal);
            Renderer[] renderers = m_robot.GetComponentsInChildren<Renderer>(true);
            foreach (Renderer renderer in renderers)
            {
                if (renderer != null && FindAssociatedBody(renderer.transform) == body)
                    actualNames.Add(GetCanonicalRendererIdentity(renderer, body));
            }
            CollectionAssert.AreEquivalent(expectedNames, actualNames, body.name + " canonical renderer mapping");
        }

        private string GetCanonicalRendererIdentity(Renderer renderer, Transform body)
        {
            Transform geometryFrame = renderer.transform;
            while (geometryFrame != null && geometryFrame != body &&
                   !geometryFrame.name.EndsWith(GeometryFrameSuffix, StringComparison.Ordinal))
            {
                geometryFrame = geometryFrame.parent;
            }

            if (geometryFrame != null && geometryFrame != body)
            {
                Assert.That(geometryFrame.parent, Is.EqualTo(body),
                    body.name + " geometry frame should be a direct child of its source body: " + geometryFrame.name);
                string canonicalName = geometryFrame.name.Substring(
                    0,
                    geometryFrame.name.Length - GeometryFrameSuffix.Length);
                Assert.That(geometryFrame.childCount, Is.EqualTo(1),
                    body.name + " geometry frame should contain its imported root: " + geometryFrame.name);

                Transform importedRoot = geometryFrame.GetChild(0);
                Assert.That(importedRoot.name, Is.EqualTo(canonicalName),
                    body.name + " imported root should retain its canonical mesh identity");
                Assert.That(renderer.transform == importedRoot || renderer.transform.IsChildOf(importedRoot), Is.True,
                    body.name + " renderer should belong to imported root " + canonicalName);

                TestContext.WriteLine(
                    "Renderer identity: " + GetHierarchyPath(renderer.transform, m_robot.transform) +
                    " => " + canonicalName);
                return canonicalName;
            }

            // UMI helper boxes are intentionally direct children of the base body,
            // while imported OBJ visuals carry their source identity on GeometryFrame.
            Assert.That(renderer.transform.parent, Is.EqualTo(body),
                body.name + " renderer without a GeometryFrame must be a direct helper child: " +
                GetHierarchyPath(renderer.transform, m_robot.transform));
            string helperIdentity = renderer.gameObject.name;
            TestContext.WriteLine(
                "Renderer identity: " + GetHierarchyPath(renderer.transform, m_robot.transform) +
                " => " + helperIdentity);
            return helperIdentity;
        }

        private static string GetHierarchyPath(Transform leaf, Transform root)
        {
            var names = new List<string>();
            Transform current = leaf;
            while (current != null)
            {
                names.Add(current.name);
                if (current == root)
                    break;
                current = current.parent;
            }
            names.Reverse();
            return string.Join("/", names.ToArray());
        }

        private Transform FindAssociatedBody(Transform start)
        {
            Transform current = start;
            while (current != null && current != m_robot.transform)
            {
                if (current.name.StartsWith("fr3_link", StringComparison.Ordinal) ||
                    current.name == "umi_umi_gripper_base" ||
                    current.name == "umi_left_finger_holder" ||
                    current.name == "umi_right_finger_holder")
                    return current;
                current = current.parent;
            }
            return null;
        }
    }
}
