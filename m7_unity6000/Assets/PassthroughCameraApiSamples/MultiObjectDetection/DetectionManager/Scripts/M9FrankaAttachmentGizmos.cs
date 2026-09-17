#if UNITY_EDITOR
using UnityEditor;
using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// Scene-view-only frames for checking the two reported Franka seams and
    /// the adjacent wrist joint. This component is never compiled into builds.
    /// </summary>
    [ExecuteAlways]
    public sealed class M9FrankaAttachmentGizmos : MonoBehaviour
    {
        private const float ParentSphereRadiusMeters = 0.007f;
        private const float ChildSphereRadiusMeters = 0.004f;
        private const float ParentAxisLengthMeters = 0.04f;
        private const float ChildAxisLengthMeters = 0.032f;
        private static readonly Color ParentColor = new Color(0.1f, 1f, 0.2f, 1f);
        private static readonly Color ChildColor = new Color(1f, 0.1f, 0.8f, 1f);

        private Transform m_joint5;
        private Transform m_link5;
        private Transform m_joint7;
        private Transform m_link7;
        private Transform m_attachmentSite;
        private Transform m_umiBase;

        internal void Configure(
            Transform joint5,
            Transform link5,
            Transform joint7,
            Transform link7,
            Transform attachmentSite,
            Transform umiBase)
        {
            m_joint5 = joint5;
            m_link5 = link5;
            m_joint7 = joint7;
            m_link7 = link7;
            m_attachmentSite = attachmentSite;
            m_umiBase = umiBase;
        }

        private void OnDrawGizmos()
        {
            DrawJoint("A parent link4 / joint5", m_joint5, "A child link5", m_link5);
            DrawJoint("B1 parent link6 / joint7", m_joint7, "B1 child link7", m_link7);
            if (m_attachmentSite != null)
                DrawParentFrame("B2 parent link7 / attachment_site", m_attachmentSite.position, m_attachmentSite.rotation);
            if (m_umiBase != null)
                DrawChildFrame("B2 child UMI base", m_umiBase.position, m_umiBase.rotation);
        }

        private static void DrawJoint(string parentLabel, Transform joint, string childLabel, Transform child)
        {
            if (joint == null || joint.parent == null || child == null)
                return;

            Transform parentBody = joint.parent;
            Vector3 predictedChildPosition = parentBody.TransformPoint(joint.localPosition);
            Quaternion predictedChildRotation = parentBody.rotation * joint.localRotation;
            DrawParentFrame(parentLabel, predictedChildPosition, predictedChildRotation);
            DrawChildFrame(childLabel, child.position, child.rotation);
        }

        private static void DrawParentFrame(string label, Vector3 position, Quaternion rotation)
        {
            DrawFrame(label, position, rotation, ParentColor, ParentSphereRadiusMeters,
                ParentAxisLengthMeters, Vector3.left * 0.01f);
        }

        private static void DrawChildFrame(string label, Vector3 position, Quaternion rotation)
        {
            DrawFrame(label, position, rotation, ChildColor, ChildSphereRadiusMeters,
                ChildAxisLengthMeters, Vector3.right * 0.01f);
        }

        private static void DrawFrame(
            string label,
            Vector3 position,
            Quaternion rotation,
            Color color,
            float sphereRadius,
            float axisLength,
            Vector3 labelOffset)
        {
            Gizmos.color = color;
            Gizmos.DrawSphere(position, sphereRadius);
            Gizmos.DrawLine(position, position + rotation * Vector3.right * axisLength);
            Gizmos.DrawLine(position, position + rotation * Vector3.up * axisLength);
            Gizmos.DrawLine(position, position + rotation * Vector3.forward * axisLength);
            Handles.Label(position + Vector3.up * (sphereRadius * 2f) + labelOffset, label);
        }
    }
}
#endif
