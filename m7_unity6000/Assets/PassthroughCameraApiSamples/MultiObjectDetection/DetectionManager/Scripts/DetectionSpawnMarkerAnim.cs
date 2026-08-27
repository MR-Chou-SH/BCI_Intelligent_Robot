// Copyright (c) Meta Platforms, Inc. and affiliates.

using Meta.XR.Samples;
using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    [MetaCodeSample("PassthroughCameraApiSamples-MultiObjectDetection")]
    public class DetectionSpawnMarkerAnim : MonoBehaviour
    {
        [SerializeField] private Vector3 m_anglesSpeed = new(20.0f, 40.0f, 60.0f);
        [SerializeField] private Transform m_model;
        [SerializeField] private Renderer m_rotatingCubeRenderer;
        [SerializeField] private TextMesh m_textModel;
        [SerializeField] private Transform m_textEntity;

        private Vector3 m_angles;
        private OVRCameraRig m_camera;
        private bool m_rotatingCubeVisible = true;

        private void Awake()
        {
            m_camera = FindFirstObjectByType<OVRCameraRig>();
        }

        private void LateUpdate()
        {
            if (m_rotatingCubeVisible)
            {
                m_angles.x = AddAngle(m_angles.x, m_anglesSpeed.x * Time.deltaTime);
                m_angles.y = AddAngle(m_angles.y, m_anglesSpeed.y * Time.deltaTime);
                m_angles.z = AddAngle(m_angles.z, m_anglesSpeed.z * Time.deltaTime);
                m_model.rotation = Quaternion.Euler(m_angles);
            }
            m_textEntity.gameObject.transform.LookAt(m_camera.centerEyeAnchor);
        }

        private static float AddAngle(float value, float toAdd)
        {
            value += toAdd;
            if (value > 360.0f)
            {
                value -= 360.0f;
            }

            if (value < 0.0f)
            {
                value = 360.0f - value;
            }

            return value;
        }

        public void SetYoloClassName(string name)
        {
            m_textModel.text = name;
        }

        public string GetYoloClassName()
        {
            return m_textModel.text;
        }

        /// <summary>
        /// BCI presentation hides only the legacy rotating Cube renderer. The
        /// marker root, center sphere, text, and StableTarget anchor remain.
        /// </summary>
        public void SetRotatingCubeVisible(bool visible)
        {
            m_rotatingCubeVisible = visible;
            if (m_rotatingCubeRenderer != null)
                m_rotatingCubeRenderer.enabled = visible;
        }
    }
}
