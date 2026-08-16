using UnityEngine;

namespace BCIIntelligentRobot.VRStimulus
{
    public sealed class M5FrameDelayedTrialStarter : MonoBehaviour
    {
        [SerializeField] private M5TrialStimulusController m_Controller;
        [SerializeField, Min(1)] private int m_DelayUnityFrames = 720;
        private int m_RequestFrame;

        private void Start() { m_RequestFrame = Time.frameCount + m_DelayUnityFrames; }

        private void Update()
        {
            if (m_Controller == null || Time.frameCount < m_RequestFrame)
                return;
            m_Controller.RequestStartTrial();
            enabled = false;
        }
    }
}
