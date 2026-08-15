using System;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Management;

namespace BCIIntelligentRobot.VRStimulus
{
    [DisallowMultipleComponent]
    [RequireComponent(typeof(Renderer))]
    public sealed class FrameDrivenStimulus : MonoBehaviour
    {
        private static readonly int ColorPropertyId = Shader.PropertyToID("_Color");
        private const int RefreshRateRetryIntervalFrames = 30;
        private const int RefreshRateUnavailableWarningFrame = 300;

        [SerializeField]
        private Renderer m_TargetRenderer;

        [SerializeField, Min(1)]
        private int m_FramesPerHalfCycle = 3;

        [SerializeField]
        private bool m_LogTransitions;

        private MaterialPropertyBlock m_PropertyBlock;
        private int m_StartFrame;
        private int m_PreviousFrame;
        private int m_LastRefreshRateAttemptFrame;
        private int m_TransitionCount;
        private int m_AnomalyCount;
        private bool m_IsWhite;
        private bool m_HasAppliedState;
        private bool m_HasObservedRefreshRate;
        private bool m_HasWarnedRefreshRateUnavailable;

        public int FramesPerHalfCycle => m_FramesPerHalfCycle;
        public int StartFrame => m_StartFrame;
        public int TransitionCount => m_TransitionCount;
        public int AnomalyCount => m_AnomalyCount;
        public bool IsWhite => m_IsWhite;

        private void Awake()
        {
            if (m_TargetRenderer == null)
                m_TargetRenderer = GetComponent<Renderer>();

            if (m_TargetRenderer == null)
            {
                Debug.LogError("FrameDrivenStimulus requires a target Renderer.", this);
                enabled = false;
                return;
            }

            Material sharedMaterial = m_TargetRenderer.sharedMaterial;
            if (sharedMaterial == null || !sharedMaterial.HasProperty(ColorPropertyId))
            {
                Debug.LogError("FrameDrivenStimulus requires a material with a verified _Color property.", this);
                enabled = false;
                return;
            }

            m_FramesPerHalfCycle = Mathf.Max(1, m_FramesPerHalfCycle);
            m_PropertyBlock = new MaterialPropertyBlock();
            m_StartFrame = Time.frameCount;
            m_PreviousFrame = m_StartFrame;
            m_LastRefreshRateAttemptFrame = m_StartFrame - RefreshRateRetryIntervalFrames;

            ApplyState(true, m_StartFrame, 0);

            Debug.Log(
                $"SSVEP startup utc={DateTime.UtcNow:O}, " +
                $"monotonicSeconds={Time.realtimeSinceStartupAsDouble:F6}, " +
                $"startFrame={m_StartFrame}, framesPerHalfCycle={m_FramesPerHalfCycle}",
                this);
        }

        private void LateUpdate()
        {
            int currentFrame = Time.frameCount;
            int frameDifference = currentFrame - m_PreviousFrame;
            if (frameDifference > 1)
            {
                m_AnomalyCount++;
                if (m_AnomalyCount <= 5 || m_AnomalyCount % 60 == 0)
                {
                    Debug.LogWarning(
                        $"SSVEP Unity-frame anomaly previousFrame={m_PreviousFrame}, " +
                        $"currentFrame={currentFrame}, difference={frameDifference}, " +
                        $"anomalyCount={m_AnomalyCount}. This is not proof of a dropped physical display frame.",
                        this);
                }
            }

            m_PreviousFrame = currentFrame;
            TryObserveRefreshRate(currentFrame);

            int localFrame = currentFrame - m_StartFrame;
            int halfCycleIndex = localFrame / m_FramesPerHalfCycle;
            bool shouldBeWhite = (halfCycleIndex & 1) == 0;
            if (!m_HasAppliedState || shouldBeWhite != m_IsWhite)
                ApplyState(shouldBeWhite, currentFrame, localFrame);
        }

        private void TryObserveRefreshRate(int currentFrame)
        {
            if (m_HasObservedRefreshRate ||
                currentFrame - m_LastRefreshRateAttemptFrame < RefreshRateRetryIntervalFrames)
                return;

            m_LastRefreshRateAttemptFrame = currentFrame;
            XRDisplaySubsystem displaySubsystem = XRGeneralSettings.Instance?
                .Manager?
                .activeLoader?
                .GetLoadedSubsystem<XRDisplaySubsystem>();

            if (displaySubsystem != null &&
                displaySubsystem.running &&
                displaySubsystem.TryGetDisplayRefreshRate(out float refreshRate) &&
                refreshRate > 0f)
            {
                m_HasObservedRefreshRate = true;
                float derivedFrequency = refreshRate / (2f * m_FramesPerHalfCycle);
                Debug.Log(
                    $"SSVEP XR refresh rate observed={refreshRate:F3}Hz, " +
                    $"Application.targetFrameRate={Application.targetFrameRate}, " +
                    $"framesPerHalfCycle={m_FramesPerHalfCycle}, " +
                    $"derivedSoftwareFrequency={derivedFrequency:F3}Hz",
                    this);
                return;
            }

            if (!m_HasWarnedRefreshRateUnavailable &&
                currentFrame - m_StartFrame >= RefreshRateUnavailableWarningFrame)
            {
                m_HasWarnedRefreshRateUnavailable = true;
                Debug.LogWarning(
                    "SSVEP XR display refresh rate is still unavailable after repeated low-frequency attempts; " +
                    "the script will continue retrying without repeated failure logs.",
                    this);
            }
        }

        private void ApplyState(bool white, int unityFrame, int localFrame)
        {
            m_IsWhite = white;
            m_HasAppliedState = true;
            m_TransitionCount++;

            m_TargetRenderer.GetPropertyBlock(m_PropertyBlock);
            m_PropertyBlock.SetColor(ColorPropertyId, white ? Color.white : Color.black);
            m_TargetRenderer.SetPropertyBlock(m_PropertyBlock);

            if (m_LogTransitions)
            {
                Debug.Log(
                    $"SSVEP transition unityFrame={unityFrame}, localFrame={localFrame}, " +
                    $"state={(white ? "white" : "black")}, " +
                    $"monotonicSeconds={Time.realtimeSinceStartupAsDouble:F6}",
                    this);
            }
        }

        private void OnValidate()
        {
            m_FramesPerHalfCycle = Mathf.Max(1, m_FramesPerHalfCycle);
            if (m_TargetRenderer == null)
                m_TargetRenderer = GetComponent<Renderer>();
        }
    }
}
