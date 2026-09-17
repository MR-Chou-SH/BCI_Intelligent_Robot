using System;
using System.Collections.Generic;
using System.Text;
using UnityEngine;
using UnityEngine.Rendering;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// Creates a visual-only FR3 + UMI hierarchy from the licensed meshes
    /// copied from the repository's fixed MuJoCo baseline snapshot.
    /// </summary>
    internal static class M9FrankaVisualFactory
    {
        private const string ResourcePrefix = "M9/Franka/Meshes/";
        private const float VisualScale = 0.7f;
        // Unity's OBJ importer converts the source right-handed coordinates
        // into its left-handed mesh space by mirroring the X vertex axis.
        // The FR3/UMI source meshes and MJCF body frames remain in the
        // original MuJoCo frame, so apply the inverse conversion once on the
        // geometry frame rather than changing any body or joint transform.
        private static readonly Vector3 ImportedObjAxisCorrection =
            new Vector3(-1f, 1f, 1f);

        private static readonly string[] RequiredMeshes =
        {
            "link0_0", "link0_1", "link0_2", "link0_3", "link0_4", "link0_5", "link0_6",
            "link1", "link2", "link3_0", "link3_1", "link4_0", "link4_1",
            "link5_0", "link5_1", "link5_2",
            "link6_0", "link6_1", "link6_2", "link6_3", "link6_4", "link6_5", "link6_6", "link6_7",
            "link7_0", "link7_1", "link7_2", "link7_3",
            "umi_base_link", "umi_gopro",
            "umi_left_finger_holder", "umi_left_finger",
            "umi_right_finger_holder", "umi_right_finger"
        };

        private static readonly Color Black = new Color(0.20f, 0.20f, 0.20f, 1f);
        private static readonly Color White = Color.white;

        internal static GameObject Create(Transform robotAnchor, Action<Renderer, Color> setRendererColor)
        {
            if (robotAnchor == null || setRendererColor == null)
                return null;

            var meshes = new Dictionary<string, GameObject>(StringComparer.Ordinal);
            for (int index = 0; index < RequiredMeshes.Length; index++)
            {
                string meshName = RequiredMeshes[index];
                GameObject mesh = Resources.Load<GameObject>(ResourcePrefix + meshName);
                if (mesh == null)
                {
                    Debug.LogError("M9_FRANKA missing visual mesh resource=" + ResourcePrefix + meshName);
                    return null;
                }
                meshes.Add(meshName, mesh);
            }

            var root = new GameObject("FrankaRoot");
            root.transform.SetParent(robotAnchor, false);
            root.transform.localPosition = Vector3.zero;
            // The MuJoCo model is Z-up. This basis rotates that native frame
            // into Unity Y-up while leaving all MJCF body/joint values intact.
            root.transform.localRotation = Quaternion.Euler(-90f, 0f, 0f);
            // Keep the visual scale at one hierarchy boundary so joint and
            // mesh-local transforms remain reusable for a later pose mirror.
            root.transform.localScale = Vector3.one * VisualScale;
            root.AddComponent<M9FrankaVisualRig>();

            // Static presentation pose produced by the baseline's existing
            // 6-DOF IK utility for a robot-local tabletop pre-grasp target.
            // It extends the wrist toward the block row with the UMI pointing
            // down. This is a fixed visual pose, not runtime IK or control.
            const float joint1PoseRadians = 0.084207f;
            const float joint2PoseRadians = -1.087620f;
            const float joint3PoseRadians = 1.221978f;
            const float joint4PoseRadians = -2.349414f;
            const float joint5PoseRadians = 0.992534f;
            const float joint6PoseRadians = 1.681765f;
            const float joint7PoseRadians = 0.822657f;

            Transform link0 = CreateLink(root.transform, "fr3_link0");
            AddMesh(meshes, link0, "link0_0", "link0_0", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link0, "link0_1", "link0_1", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link0, "link0_2", "link0_2", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link0, "link0_3", "link0_3", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link0, "link0_4", "link0_4", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link0, "link0_5", "link0_5", new Color(1f, 0.072272f, 0.039546f), Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link0, "link0_6", "link0_6", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Transform joint1 = CreateJoint(link0, "fr3_joint1", new Vector3(0f, 0f, 0.333f), Quaternion.identity, Vector3.forward, false, joint1PoseRadians);
            Transform link1 = CreateLink(joint1, "fr3_link1");
            AddMesh(meshes, link1, "link1", "link1", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Quaternion body2 = MujocoQuaternion(0.707107f, -0.707107f, 0f, 0f);
            Transform joint2 = CreateJoint(link1, "fr3_joint2", Vector3.zero, body2, Vector3.forward, false, joint2PoseRadians);
            Transform link2 = CreateLink(joint2, "fr3_link2");
            AddMesh(meshes, link2, "link2", "link2", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Quaternion body3 = MujocoQuaternion(0.707107f, 0.707107f, 0f, 0f);
            Transform joint3 = CreateJoint(link2, "fr3_joint3", new Vector3(0f, -0.316f, 0f), body3, Vector3.forward, false, joint3PoseRadians);
            Transform link3 = CreateLink(joint3, "fr3_link3");
            AddMesh(meshes, link3, "link3_0", "link3_0", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link3, "link3_1", "link3_1", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Transform joint4 = CreateJoint(link3, "fr3_joint4", new Vector3(0.0825f, 0f, 0f), body3, Vector3.forward, false, joint4PoseRadians);
            Transform link4 = CreateLink(joint4, "fr3_link4");
            AddMesh(meshes, link4, "link4_0", "link4_0", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link4, "link4_1", "link4_1", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Transform joint5 = CreateJoint(link4, "fr3_joint5", new Vector3(-0.0825f, 0.384f, 0f), body2, Vector3.forward, false, joint5PoseRadians);
            Transform link5 = CreateLink(joint5, "fr3_link5");
            AddMesh(meshes, link5, "link5_0", "link5_0", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link5, "link5_1", "link5_1", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link5, "link5_2", "link5_2", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Transform joint6 = CreateJoint(link5, "fr3_joint6", Vector3.zero, body3, Vector3.forward, false, joint6PoseRadians);
            Transform link6 = CreateLink(joint6, "fr3_link6");
            AddMesh(meshes, link6, "link6_0", "link6_0", new Color(0.102241f, 0.571125f, 0.102242f), Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_1", "link6_1", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_2", "link6_2", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_3", "link6_3", new Color(0.863156f, 0.863156f, 0.863157f), Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_4", "link6_4", new Color(0.520996f, 0.008023f, 0.013702f), Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_5", "link6_5", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_6", "link6_6", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link6, "link6_7", "link6_7", new Color(0.024157f, 0.445201f, 0.737911f), Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            Transform joint7 = CreateJoint(link6, "fr3_joint7", new Vector3(0.088f, 0f, 0f), body3, Vector3.forward, false, joint7PoseRadians);
            Transform link7 = CreateLink(joint7, "fr3_link7");
            AddMesh(meshes, link7, "link7_0", "link7_0", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link7, "link7_1", "link7_1", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link7, "link7_2", "link7_2", White, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, link7, "link7_3", "link7_3", Black, Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);

            // The merged MJCF exposes the flange as an attachment site at
            // link7 + (0, 0, 0.107 m); the UMI base body origin is coincident
            // with that site. Keep that source frame explicit in Unity.
            Transform attachmentSite = CreateBody(
                link7,
                "fr3_attachment_site",
                new Vector3(0f, 0f, 0.107f),
                Quaternion.identity);
            Transform umiBase = CreateBody(
                attachmentSite,
                "umi_umi_gripper_base",
                Vector3.zero,
                Quaternion.identity);
            CreateUmiGripper(meshes, umiBase, setRendererColor);
#if UNITY_EDITOR
            M9FrankaAttachmentGizmos attachmentGizmos = root.AddComponent<M9FrankaAttachmentGizmos>();
            attachmentGizmos.Configure(
                joint5,
                link5,
                joint7,
                link7,
                attachmentSite,
                umiBase);
#endif
            return root;
        }

        internal static void LogTransformDiagnostics(Transform robotRoot)
        {
            if (robotRoot == null)
                return;

            Debug.Log("M9_FRANKA_XFORM audit=fixed_presentation_pose" +
                " robot_scale=" + robotRoot.localScale.ToString("F3") +
                " pose_source=baseline_ik_tabletop_pregrasp");

            for (int linkIndex = 0; linkIndex < 8; linkIndex++)
            {
                string bodyName = "fr3_link" + linkIndex;
                Transform body = FindDescendant(robotRoot, bodyName);
                if (body == null)
                {
                    Debug.LogWarning("M9_FRANKA_XFORM missing_body=" + bodyName);
                    continue;
                }

                Transform joint = linkIndex == 0 ? null : body.parent;
                Transform parentBody = joint != null ? joint.parent : null;
                Vector3 normalizedRootLocalPosition = robotRoot.InverseTransformPoint(body.position);
                Quaternion normalizedRootLocalRotation = Quaternion.Inverse(robotRoot.rotation) * body.rotation;
                var message = new StringBuilder();
                message.Append("M9_FRANKA_XFORM body=").Append(bodyName)
                    .Append(" robot_local_position_unscaled=").Append(normalizedRootLocalPosition.ToString("F5"))
                    .Append(" robot_local_rotation_unscaled_xyzw=").Append(normalizedRootLocalRotation.ToString("F5"))
                    .Append(" world_position=").Append(body.position.ToString("F5"))
                    .Append(" world_rotation_xyzw=").Append(body.rotation.ToString("F5"));

                if (joint != null)
                {
                    message.Append(" parent_body=").Append(parentBody != null ? parentBody.name : "missing")
                        .Append(" joint=").Append(joint.name)
                        .Append(" joint_local_position=").Append(joint.localPosition.ToString("F5"))
                        .Append(" joint_world_position=").Append(joint.position.ToString("F5"))
                        .Append(" parent_to_joint_anchor_m=")
                            .Append(parentBody != null
                                ? Vector3.Distance(parentBody.position, joint.position).ToString("F5")
                                : "missing")
                        .Append(" joint_to_child_link_anchor_m=")
                            .Append(Vector3.Distance(joint.position, body.position).ToString("F5"));
                }

                AppendMeshRootDiagnostics(message, body, robotRoot);
                Debug.Log(message.ToString());
            }

            Transform umiBase = FindDescendant(robotRoot, "umi_umi_gripper_base");
            if (umiBase != null)
            {
                Vector3 normalizedRootLocalPosition = robotRoot.InverseTransformPoint(umiBase.position);
                Quaternion normalizedRootLocalRotation = Quaternion.Inverse(robotRoot.rotation) * umiBase.rotation;
                var message = new StringBuilder();
                message.Append("M9_FRANKA_XFORM body=umi_umi_gripper_base")
                    .Append(" robot_local_position_unscaled=").Append(normalizedRootLocalPosition.ToString("F5"))
                    .Append(" robot_local_rotation_unscaled_xyzw=").Append(normalizedRootLocalRotation.ToString("F5"))
                    .Append(" world_position=").Append(umiBase.position.ToString("F5"))
                    .Append(" world_rotation_xyzw=").Append(umiBase.rotation.ToString("F5"));
                AppendMeshRootDiagnostics(message, umiBase, robotRoot);
                Debug.Log(message.ToString());
            }
            else
                Debug.LogWarning("M9_FRANKA_XFORM missing_body=umi_umi_gripper_base");

            LogRendererDiagnostics(robotRoot);
#if UNITY_EDITOR
            LogAttachmentDiagnostics(robotRoot);
#endif
        }

#if UNITY_EDITOR
        private static void LogAttachmentDiagnostics(Transform robotRoot)
        {
            LogJointClosure(robotRoot, "A", "fr3_joint5", "fr3_link5");
            LogJointClosure(robotRoot, "B1", "fr3_joint7", "fr3_link7");

            Transform site = FindDescendant(robotRoot, "fr3_attachment_site");
            Transform umiBase = FindDescendant(robotRoot, "umi_umi_gripper_base");
            if (site == null || umiBase == null)
            {
                Debug.LogWarning("M9_FRANKA_ATTACHMENT id=B2 missing_site_or_umi_base");
                return;
            }

            LogFrameClosure(
                robotRoot,
                "B2",
                "parent=fr3_link7/attachment_site renderers=link7_0,link7_1,link7_2,link7_3",
                site.position,
                site.rotation,
                "child=umi_umi_gripper_base renderer=umi_base_link",
                umiBase.position,
                umiBase.rotation);
        }

        private static void LogJointClosure(Transform robotRoot, string id, string jointName, string childName)
        {
            Transform joint = FindDescendant(robotRoot, jointName);
            Transform child = FindDescendant(robotRoot, childName);
            if (joint == null || joint.parent == null || child == null)
            {
                Debug.LogWarning("M9_FRANKA_ATTACHMENT id=" + id + " missing_joint_or_child=" + jointName);
                return;
            }

            Transform parentBody = joint.parent;
            Vector3 expectedPosition = parentBody.TransformPoint(joint.localPosition);
            Quaternion expectedRotation = parentBody.rotation * joint.localRotation;
            LogFrameClosure(
                robotRoot,
                id,
                "parent=" + parentBody.name + "/" + joint.name + " renderers=" + RendererNames(robotRoot, parentBody),
                expectedPosition,
                expectedRotation,
                "child=" + child.name + " renderers=" + RendererNames(robotRoot, child),
                child.position,
                child.rotation);
        }

        private static void LogFrameClosure(
            Transform robotRoot,
            string id,
            string parentDescription,
            Vector3 parentPosition,
            Quaternion parentRotation,
            string childDescription,
            Vector3 childPosition,
            Quaternion childRotation)
        {
            Vector3 normalizedPositionDelta =
                robotRoot.InverseTransformPoint(parentPosition) - robotRoot.InverseTransformPoint(childPosition);
            float rotationDelta = Quaternion.Angle(parentRotation, childRotation);
            Debug.Log("M9_FRANKA_ATTACHMENT id=" + id +
                " " + parentDescription +
                " parent_world_position=" + parentPosition.ToString("F6") +
                " parent_world_rotation_xyzw=" + parentRotation.ToString("F6") +
                " " + childDescription +
                " child_world_position=" + childPosition.ToString("F6") +
                " child_world_rotation_xyzw=" + childRotation.ToString("F6") +
                " position_delta_robot_local_m=" + normalizedPositionDelta.magnitude.ToString("F8") +
                " position_delta_world_m=" + Vector3.Distance(parentPosition, childPosition).ToString("F8") +
                " rotation_delta_deg=" + rotationDelta.ToString("F6") +
                " parent_forward=" + (parentRotation * Vector3.forward).ToString("F6") +
                " child_forward=" + (childRotation * Vector3.forward).ToString("F6") +
                " parent_up=" + (parentRotation * Vector3.up).ToString("F6") +
                " child_up=" + (childRotation * Vector3.up).ToString("F6"));
        }

        private static string RendererNames(Transform robotRoot, Transform body)
        {
            Renderer[] renderers = robotRoot.GetComponentsInChildren<Renderer>(true);
            var names = new StringBuilder();
            for (int index = 0; index < renderers.Length; index++)
            {
                if (renderers[index] == null || FindAssociatedBody(renderers[index].transform, robotRoot) != body)
                    continue;
                if (names.Length > 0)
                    names.Append(',');
                names.Append(renderers[index].gameObject.name);
            }
            return names.Length == 0 ? "none" : names.ToString();
        }
#endif

        private static void LogRendererDiagnostics(Transform robotRoot)
        {
            Renderer[] renderers = robotRoot.GetComponentsInChildren<Renderer>(true);
            for (int index = 0; index < renderers.Length; index++)
            {
                Renderer renderer = renderers[index];
                if (renderer == null)
                    continue;

                Transform geometryFrame = FindAncestorWithSuffix(renderer.transform, "_GeometryFrame");
                Transform importedRoot = geometryFrame != null
                    ? FindDirectChildContaining(geometryFrame, renderer.transform)
                    : null;
                Transform body = FindAssociatedBody(renderer.transform, robotRoot);
                MeshFilter meshFilter = renderer.GetComponent<MeshFilter>();
                SkinnedMeshRenderer skinned = renderer as SkinnedMeshRenderer;
                Mesh mesh = meshFilter != null ? meshFilter.sharedMesh :
                    (skinned != null ? skinned.sharedMesh : null);

                var materialSummary = new StringBuilder();
                Material[] materials = renderer.sharedMaterials;
                for (int materialIndex = 0; materialIndex < materials.Length; materialIndex++)
                {
                    if (materialIndex > 0)
                        materialSummary.Append('|');
                    Material material = materials[materialIndex];
                    Shader shader = material != null ? material.shader : null;
                    materialSummary.Append(material != null ? material.name : "missing")
                        .Append(':')
                        .Append(shader != null ? shader.name : "missing")
                        .Append(':')
                        .Append(shader != null && shader.isSupported ? "supported" : "unsupported");
                }

                Debug.Log("M9_FRANKA_RUNTIME" +
                    " renderer=" + renderer.gameObject.name +
                    " renderer_path=" + BuildPath(robotRoot, renderer.transform) +
                    " parent_chain=" + BuildParentChain(renderer.transform, robotRoot) +
                    " associated_body=" + (body != null ? body.name : "missing") +
                    " geometry_frame=" + (geometryFrame != null ? geometryFrame.name : "primitive_or_missing") +
                    " geometry_frame_local_scale=" + (geometryFrame != null ? geometryFrame.localScale.ToString("F5") : "n/a") +
                    " import_root=" + (importedRoot != null ? importedRoot.name : "primitive_or_missing") +
                    " import_local_position=" + (importedRoot != null ? importedRoot.localPosition.ToString("F5") : "n/a") +
                    " import_local_rotation_xyzw=" + (importedRoot != null ? importedRoot.localRotation.ToString("F5") : "n/a") +
                    " import_local_scale=" + (importedRoot != null ? importedRoot.localScale.ToString("F5") : "n/a") +
                    " renderer_local_position=" + renderer.transform.localPosition.ToString("F5") +
                    " renderer_local_rotation_xyzw=" + renderer.transform.localRotation.ToString("F5") +
                    " renderer_local_scale=" + renderer.transform.localScale.ToString("F5") +
                    " renderer_world_position=" + renderer.transform.position.ToString("F5") +
                    " renderer_world_rotation_xyzw=" + renderer.transform.rotation.ToString("F5") +
                    " bounds_center=" + renderer.bounds.center.ToString("F5") +
                    " bounds_size=" + renderer.bounds.size.ToString("F5") +
                    " vertex_count=" + (mesh != null ? mesh.vertexCount.ToString() : "missing") +
                    " mesh_bounds=" + (mesh != null
                        ? "center:" + mesh.bounds.center.ToString("F5") + ",size:" + mesh.bounds.size.ToString("F5")
                        : "missing") +
                    " enabled=" + renderer.enabled +
                    " active_in_hierarchy=" + renderer.gameObject.activeInHierarchy +
                    " layer=" + renderer.gameObject.layer +
                    " materials=" + materialSummary);
            }
        }

        private static void AppendMeshRootDiagnostics(StringBuilder message, Transform body, Transform robotRoot)
        {
            message.Append(" mesh_roots=[");
            bool firstMesh = true;
            for (int index = 0; index < body.childCount; index++)
            {
                Transform geometryFrame = body.GetChild(index);
                const string suffix = "_GeometryFrame";
                if (!geometryFrame.name.EndsWith(suffix, StringComparison.Ordinal))
                    continue;

                string meshName = geometryFrame.name.Substring(0, geometryFrame.name.Length - suffix.Length);
                Transform importedRoot = geometryFrame.Find(meshName);
                if (importedRoot == null)
                    continue;

                if (!firstMesh)
                    message.Append(';');
                firstMesh = false;
                Vector3 rootLocalPosition = robotRoot.InverseTransformPoint(importedRoot.position);
                Quaternion rootLocalRotation = Quaternion.Inverse(robotRoot.rotation) * importedRoot.rotation;
                message.Append(meshName)
                    .Append(" frame_world_position=").Append(geometryFrame.position.ToString("F5"))
                    .Append(" frame_world_rotation_xyzw=").Append(geometryFrame.rotation.ToString("F5"))
                    .Append(" frame_local_scale=").Append(geometryFrame.localScale.ToString("F5"))
                    .Append(" import_local_position=").Append(importedRoot.localPosition.ToString("F5"))
                    .Append(" import_local_rotation_xyzw=").Append(importedRoot.localRotation.ToString("F5"))
                    .Append(" import_local_scale=").Append(importedRoot.localScale.ToString("F5"))
                    .Append(" robot_local_position_unscaled=").Append(rootLocalPosition.ToString("F5"))
                    .Append(" robot_local_rotation_unscaled_xyzw=").Append(rootLocalRotation.ToString("F5"))
                    .Append(" world_position=").Append(importedRoot.position.ToString("F5"))
                    .Append(" world_rotation_xyzw=").Append(importedRoot.rotation.ToString("F5"));

                Renderer[] renderers = importedRoot.GetComponentsInChildren<Renderer>(true);
                bool hasBounds = false;
                Bounds rendererBounds = new Bounds();
                for (int rendererIndex = 0; rendererIndex < renderers.Length; rendererIndex++)
                {
                    if (renderers[rendererIndex] == null)
                        continue;
                    if (!hasBounds)
                    {
                        rendererBounds = renderers[rendererIndex].bounds;
                        hasBounds = true;
                    }
                    else
                        rendererBounds.Encapsulate(renderers[rendererIndex].bounds);
                }
                message.Append(" renderer_bounds=")
                    .Append(hasBounds
                        ? "center:" + rendererBounds.center.ToString("F5") + ",size:" + rendererBounds.size.ToString("F5")
                        : "missing");
            }
            message.Append(']');
        }

        private static Transform FindDescendant(Transform root, string targetName)
        {
            Transform[] transforms = root.GetComponentsInChildren<Transform>(true);
            for (int index = 0; index < transforms.Length; index++)
            {
                if (transforms[index] != root && transforms[index].name == targetName)
                    return transforms[index];
            }
            return null;
        }

        private static Transform FindAncestorWithSuffix(Transform start, string suffix)
        {
            Transform current = start;
            while (current != null)
            {
                if (current.name.EndsWith(suffix, StringComparison.Ordinal))
                    return current;
                current = current.parent;
            }
            return null;
        }

        private static Transform FindDirectChildContaining(Transform ancestor, Transform descendant)
        {
            Transform current = descendant;
            while (current != null && current.parent != ancestor)
                current = current.parent;
            return current != null && current.parent == ancestor ? current : null;
        }

        private static Transform FindAssociatedBody(Transform start, Transform robotRoot)
        {
            Transform current = start;
            while (current != null && current != robotRoot)
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

        private static string BuildPath(Transform root, Transform leaf)
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
            return string.Join("/", names);
        }

        private static string BuildParentChain(Transform leaf, Transform root)
        {
            var names = new List<string>();
            Transform current = leaf.parent;
            while (current != null)
            {
                names.Add(current.name);
                if (current == root)
                    break;
                current = current.parent;
            }
            return string.Join("<-", names);
        }

        private static Transform CreateLink(Transform parent, string name)
        {
            var link = new GameObject(name).transform;
            link.SetParent(parent, false);
            return link;
        }

        private static Transform CreateBody(Transform parent, string name, Vector3 position, Quaternion rotation)
        {
            var body = new GameObject(name).transform;
            body.SetParent(parent, false);
            body.localPosition = position;
            body.localRotation = rotation;
            return body;
        }

        private static Transform CreateJoint(
            Transform parent,
            string name,
            Vector3 bodyPosition,
            Quaternion bodyRotation,
            Vector3 jointAxis,
            bool isSlider,
            float initialValue)
        {
            var jointObject = new GameObject(name);
            Transform joint = jointObject.transform;
            joint.SetParent(parent, false);
            var visualJoint = jointObject.AddComponent<M9FrankaVisualJoint>();
            visualJoint.Configure(name, isSlider, jointAxis, bodyPosition, bodyRotation, initialValue);
            return joint;
        }

        private static void CreateUmiGripper(
            IDictionary<string, GameObject> meshes,
            Transform umiBase,
            Action<Renderer, Color> setRendererColor)
        {
            Color lightGray = new Color(0.6f, 0.6f, 0.6f, 1f);
            Color gray = new Color(0.15f, 0.15f, 0.15f, 1f);
            Color mirrorBlack = Color.black;
            Color orange = new Color(0.92f, 0.68f, 0.24f, 1f);

            AddBox(umiBase, "umi_linear_guide_rail", new Vector3(0f, -0.010325f, 0.041525f),
                Quaternion.identity, new Vector3(0.088f, 0.0033f, 0.0044f), lightGray, setRendererColor);
            AddBox(umiBase, "umi_left_mirror", new Vector3(-0.03795f, -0.040575f, 0.029425f),
                MujocoQuaternion(0.828106f, 0f, 0.560572f, 0f), new Vector3(0.033f, 0.02057f, 0.00011f), mirrorBlack, setRendererColor);
            AddBox(umiBase, "umi_right_mirror", new Vector3(0.03795f, -0.040575f, 0.029425f),
                MujocoQuaternion(0.828106f, 0f, -0.560572f, 0f), new Vector3(0.033f, 0.02057f, 0.00011f), mirrorBlack, setRendererColor);
            AddMesh(meshes, umiBase, "umi_base_link", "umi_base_link", White,
                new Vector3(0f, -0.009225f, 0.007425f), Quaternion.identity,
                new Vector3(0.55f, 0.55f, 0.55f), setRendererColor);
            AddMesh(meshes, umiBase, "umi_gopro", "umi_gopro", gray,
                new Vector3(0.0108075f, -0.0525375f, -0.00286f),
                MujocoQuaternion(0f, 0f, -0.707107f, 0.707107f),
                new Vector3(0.55f, 0.55f, 0.55f), setRendererColor);

            Quaternion fingerBodyRotation = MujocoQuaternion(0.707107f, 0.707107f, 0f, 0f);
            Transform leftJoint = CreateJoint(umiBase, "umi_left_finger_joint", Vector3.zero,
                fingerBodyRotation, Vector3.right, true, 0f);
            Transform leftHolder = CreateLink(leftJoint, "umi_left_finger_holder");
            AddBox(leftHolder, "umi_left_rail_block", new Vector3(-0.051f, 0.063f, 0.006f),
                Quaternion.identity, new Vector3(0.018f, 0.024f, 0.008f), gray, setRendererColor);
            AddMesh(meshes, leftHolder, "umi_left_finger_holder", "umi_left_finger_holder", White,
                Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, leftHolder, "umi_left_finger", "umi_left_finger", orange,
                new Vector3(-0.041722f, 0.0779f, -0.0159f),
                MujocoQuaternion(0f, 0f, -0.707107f, 0.707107f), Vector3.one, setRendererColor);

            Transform rightJoint = CreateJoint(umiBase, "umi_right_finger_joint", Vector3.zero,
                fingerBodyRotation, Vector3.left, true, 0f);
            Transform rightHolder = CreateLink(rightJoint, "umi_right_finger_holder");
            AddBox(rightHolder, "umi_right_rail_block", new Vector3(0.051f, 0.063f, 0.006f),
                Quaternion.identity, new Vector3(0.018f, 0.024f, 0.008f), gray, setRendererColor);
            AddMesh(meshes, rightHolder, "umi_right_finger_holder", "umi_right_finger_holder", White,
                Vector3.zero, Quaternion.identity, Vector3.one, setRendererColor);
            AddMesh(meshes, rightHolder, "umi_right_finger", "umi_right_finger", orange,
                new Vector3(0.041722f, 0.0779f, 0.0099f),
                MujocoQuaternion(0.707107f, 0.707107f, 0f, 0f), Vector3.one, setRendererColor);
        }

        private static GameObject AddMesh(
            IDictionary<string, GameObject> meshes,
            Transform parent,
            string resourceName,
            string objectName,
            Color color,
            Vector3 position,
            Quaternion rotation,
            Vector3 scale,
            Action<Renderer, Color> setRendererColor)
        {
            // MJCF geom pose and the Unity-imported model root are separate
            // frames. Applying the geom pose to the imported root overwrites
            // any importer-owned transform and can disconnect adjacent links.
            Transform geometryFrame = new GameObject(objectName + "_GeometryFrame").transform;
            geometryFrame.SetParent(parent, false);
            geometryFrame.localPosition = position;
            geometryFrame.localRotation = rotation;
            geometryFrame.localScale = Vector3.Scale(scale, ImportedObjAxisCorrection);

            GameObject instance = UnityEngine.Object.Instantiate(meshes[resourceName], geometryFrame, false);
            instance.name = objectName;
            Renderer[] renderers = instance.GetComponentsInChildren<Renderer>(true);
            for (int index = 0; index < renderers.Length; index++)
            {
                renderers[index].shadowCastingMode = ShadowCastingMode.Off;
                renderers[index].receiveShadows = false;
                setRendererColor(renderers[index], color);
            }
            return instance;
        }

        private static GameObject AddBox(
            Transform parent,
            string name,
            Vector3 position,
            Quaternion rotation,
            Vector3 size,
            Color color,
            Action<Renderer, Color> setRendererColor)
        {
            GameObject box = GameObject.CreatePrimitive(PrimitiveType.Cube);
            box.name = name;
            box.transform.SetParent(parent, false);
            box.transform.localPosition = position;
            box.transform.localRotation = rotation;
            box.transform.localScale = size;
            Collider collider = box.GetComponent<Collider>();
            if (collider != null)
                collider.enabled = false;
            setRendererColor(box.GetComponent<Renderer>(), color);
            return box;
        }

        private static Quaternion MujocoQuaternion(float w, float x, float y, float z)
        {
            return new Quaternion(x, y, z, w);
        }
    }
}
