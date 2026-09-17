using System.Collections.Generic;
using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// Named visual joint interface for a later MuJoCo-to-Unity pose mirror.
    /// This component is presentation-only and is not connected to transport.
    /// </summary>
    public sealed class M9FrankaVisualRig : MonoBehaviour
    {
        private readonly Dictionary<string, M9FrankaVisualJoint> m_joints =
            new Dictionary<string, M9FrankaVisualJoint>();

        private bool m_indexBuilt;

        public bool TrySetJointValue(string jointId, float value)
        {
            if (!m_indexBuilt)
                BuildJointIndex();

            if (string.IsNullOrEmpty(jointId) || !m_joints.TryGetValue(jointId, out M9FrankaVisualJoint joint))
                return false;

            joint.SetValue(value);
            return true;
        }

        private void BuildJointIndex()
        {
            m_joints.Clear();
            M9FrankaVisualJoint[] joints = GetComponentsInChildren<M9FrankaVisualJoint>(true);
            for (int index = 0; index < joints.Length; index++)
            {
                M9FrankaVisualJoint joint = joints[index];
                if (joint != null && !string.IsNullOrEmpty(joint.JointId))
                    m_joints[joint.JointId] = joint;
            }
            m_indexBuilt = true;
        }
    }
}
