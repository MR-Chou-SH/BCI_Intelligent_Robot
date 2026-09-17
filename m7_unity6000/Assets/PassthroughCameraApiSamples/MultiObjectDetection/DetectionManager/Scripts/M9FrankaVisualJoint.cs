using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// A visual-only joint transform copied from the FR3 + UMI MuJoCo model.
    /// It does not simulate forces, perform IK, or send robot commands.
    /// </summary>
    public sealed class M9FrankaVisualJoint : MonoBehaviour
    {
        [SerializeField] private string m_jointId;
        [SerializeField] private bool m_isSlider;
        [SerializeField] private Vector3 m_localAxis = Vector3.forward;
        [SerializeField] private Quaternion m_bodyFrameRotation = Quaternion.identity;
        [SerializeField] private Vector3 m_bodyFramePosition;
        [SerializeField] private float m_value;

        public string JointId => m_jointId;
        public float Value => m_value;

        internal void Configure(
            string jointId,
            bool isSlider,
            Vector3 localAxis,
            Vector3 bodyFramePosition,
            Quaternion bodyFrameRotation,
            float initialValue)
        {
            m_jointId = jointId;
            m_isSlider = isSlider;
            m_localAxis = localAxis.normalized;
            m_bodyFramePosition = bodyFramePosition;
            m_bodyFrameRotation = bodyFrameRotation;
            SetValue(initialValue);
        }

        /// <summary>
        /// Applies one visual joint value. Revolute values are radians and
        /// slider values are meters, matching the MuJoCo state units.
        /// </summary>
        public void SetValue(float value)
        {
            m_value = value;
            transform.localRotation = m_bodyFrameRotation;
            transform.localPosition = m_bodyFramePosition;
            if (m_isSlider)
            {
                transform.localPosition += m_bodyFrameRotation * (m_localAxis * value);
            }
            else
            {
                transform.localRotation = m_bodyFrameRotation *
                    Quaternion.AngleAxis(value * Mathf.Rad2Deg, m_localAxis);
            }
        }
    }
}
