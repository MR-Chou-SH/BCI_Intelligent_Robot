using System;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Management;

namespace BCIIntelligentRobot.VRStimulus
{
    [DisallowMultipleComponent]
    public sealed class SSVEPTimingDiagnostics : MonoBehaviour
    {
        private const int RefreshRatePollIntervalFrames = 60;
        private const float RefreshRateChangeToleranceHz = 0.1f;
        private const float LongFrameThresholdMultiplier = 1.5f;

        [SerializeField]
        private FrameDrivenStimulus m_Stimulus;

        [SerializeField, Min(1f)]
        private float m_MeasurementDurationSeconds = 30f;

        private XRDisplaySubsystem m_DisplaySubsystem;
        private bool m_Measuring;
        private bool m_SummaryLogged;
        private double m_StartTime;
        private int m_StartFrame;
        private int m_PreviousFrame;
        private int m_LastRefreshPollFrame;
        private int m_ObservedFrames;
        private double m_FrameIntervalSum;
        private float m_MinFrameInterval = float.PositiveInfinity;
        private float m_MaxFrameInterval;
        private int m_LongFrameCount;
        private int m_FrameGapCount;
        private int m_MaxFrameGap;
        private bool m_HasRefreshRate;
        private float m_InitialRefreshRate;
        private float m_LatestRefreshRate;
        private float m_MinRefreshRate;
        private float m_MaxRefreshRate;
        private int m_RefreshRateChangeCount;
        private bool m_PresentStartSupported;
        private int m_StartPresentCount;
        private bool m_DroppedStartSupported;
        private int m_StartDroppedCount;

        private void Awake()
        {
            if (m_Stimulus == null)
                m_Stimulus = GetComponent<FrameDrivenStimulus>();

            if (m_Stimulus == null)
            {
                Debug.LogError("SSVEP timing diagnostics requires a FrameDrivenStimulus reference.", this);
                enabled = false;
            }

            m_MeasurementDurationSeconds = Mathf.Max(1f, m_MeasurementDurationSeconds);
        }

        private void LateUpdate()
        {
            if (m_SummaryLogged)
                return;

            if (!m_Measuring)
            {
                m_DisplaySubsystem = XRGeneralSettings.Instance?
                    .Manager?
                    .activeLoader?
                    .GetLoadedSubsystem<XRDisplaySubsystem>();

                if (m_DisplaySubsystem == null || !m_DisplaySubsystem.running)
                    return;

                BeginMeasurement();
            }

            int currentFrame = Time.frameCount;
            float frameInterval = Time.unscaledDeltaTime;
            m_ObservedFrames++;
            m_FrameIntervalSum += frameInterval;
            m_MinFrameInterval = Mathf.Min(m_MinFrameInterval, frameInterval);
            m_MaxFrameInterval = Mathf.Max(m_MaxFrameInterval, frameInterval);

            int frameGap = currentFrame - m_PreviousFrame;
            if (frameGap > 1)
            {
                m_FrameGapCount++;
                m_MaxFrameGap = Math.Max(m_MaxFrameGap, frameGap);
            }

            m_PreviousFrame = currentFrame;

            if (m_HasRefreshRate &&
                frameInterval > LongFrameThresholdMultiplier / m_LatestRefreshRate)
            {
                m_LongFrameCount++;
            }

            if (currentFrame - m_LastRefreshPollFrame >= RefreshRatePollIntervalFrames)
            {
                ObserveRefreshRate(currentFrame, true);
                m_LastRefreshPollFrame = currentFrame;
            }

            if (Time.realtimeSinceStartupAsDouble - m_StartTime >= m_MeasurementDurationSeconds)
                LogSummary(currentFrame);
        }

        private void BeginMeasurement()
        {
            m_Measuring = true;
            m_StartTime = Time.realtimeSinceStartupAsDouble;
            m_StartFrame = Time.frameCount;
            m_PreviousFrame = m_StartFrame;
            m_LastRefreshPollFrame = m_StartFrame - RefreshRatePollIntervalFrames;

            ObserveRefreshRate(m_StartFrame, false);
            m_PresentStartSupported = m_DisplaySubsystem.TryGetFramePresentCount(out m_StartPresentCount);
            m_DroppedStartSupported = m_DisplaySubsystem.TryGetDroppedFrameCount(out m_StartDroppedCount);

            Debug.Log(
                $"SSVEP timing measurement started utc={DateTime.UtcNow:O}, " +
                $"monotonicSeconds={m_StartTime:F6}, startFrame={m_StartFrame}, " +
                $"durationSeconds={m_MeasurementDurationSeconds:F3}, " +
                $"presentCounterStartSupported={m_PresentStartSupported}, " +
                $"droppedCounterStartSupported={m_DroppedStartSupported}. " +
                "Counters are reported exactly as returned by XRDisplaySubsystem.",
                this);
        }

        private void ObserveRefreshRate(int currentFrame, bool detectChange)
        {
            if (!m_DisplaySubsystem.TryGetDisplayRefreshRate(out float refreshRate) || refreshRate <= 0f)
                return;

            if (!m_HasRefreshRate)
            {
                m_HasRefreshRate = true;
                m_InitialRefreshRate = refreshRate;
                m_LatestRefreshRate = refreshRate;
                m_MinRefreshRate = refreshRate;
                m_MaxRefreshRate = refreshRate;
                return;
            }

            if (detectChange && Mathf.Abs(refreshRate - m_LatestRefreshRate) > RefreshRateChangeToleranceHz)
            {
                Debug.LogWarning(
                    $"SSVEP XR refresh-rate change unityFrame={currentFrame}, " +
                    $"previous={m_LatestRefreshRate:F3}Hz, new={refreshRate:F3}Hz, " +
                    $"monotonicSeconds={Time.realtimeSinceStartupAsDouble:F6}; " +
                    "framesPerHalfCycle remains unchanged.",
                    this);
                m_RefreshRateChangeCount++;
            }

            m_LatestRefreshRate = refreshRate;
            m_MinRefreshRate = Mathf.Min(m_MinRefreshRate, refreshRate);
            m_MaxRefreshRate = Mathf.Max(m_MaxRefreshRate, refreshRate);
        }

        private void LogSummary(int endFrame)
        {
            m_SummaryLogged = true;
            double actualDuration = Time.realtimeSinceStartupAsDouble - m_StartTime;
            double meanInterval = m_ObservedFrames > 0 ? m_FrameIntervalSum / m_ObservedFrames : 0d;
            double meanFps = meanInterval > 0d ? 1d / meanInterval : 0d;
            float minInterval = m_ObservedFrames > 0 ? m_MinFrameInterval : 0f;

            bool presentEndSupported = m_DisplaySubsystem.TryGetFramePresentCount(out int endPresentCount);
            bool droppedEndSupported = m_DisplaySubsystem.TryGetDroppedFrameCount(out int endDroppedCount);
            bool presentDeltaSupported = m_PresentStartSupported && presentEndSupported;
            bool droppedDeltaSupported = m_DroppedStartSupported && droppedEndSupported;
            string presentDelta = presentDeltaSupported
                ? (endPresentCount - m_StartPresentCount).ToString()
                : "unsupported";
            string droppedDelta = droppedDeltaSupported
                ? (endDroppedCount - m_StartDroppedCount).ToString()
                : "unsupported";
            string refreshValues = m_HasRefreshRate
                ? $"initialRefreshHz={m_InitialRefreshRate:F3}, minRefreshHz={m_MinRefreshRate:F3}, " +
                  $"maxRefreshHz={m_MaxRefreshRate:F3}, latestRefreshHz={m_LatestRefreshRate:F3}"
                : "initialRefreshHz=unsupported, minRefreshHz=unsupported, " +
                  "maxRefreshHz=unsupported, latestRefreshHz=unsupported";
            string derivedFrequency = m_HasRefreshRate
                ? (m_LatestRefreshRate / (2f * m_Stimulus.FramesPerHalfCycle)).ToString("F3") + "Hz"
                : "unsupported";

            Debug.Log(
                $"SSVEP TIMING SUMMARY durationSeconds={actualDuration:F3}, " +
                $"startFrame={m_StartFrame}, endFrame={endFrame}, observedUnityFrames={m_ObservedFrames}, " +
                $"refreshRateReadSupported={m_HasRefreshRate}, {refreshValues}, " +
                $"refreshRateChangeCount={m_RefreshRateChangeCount}, " +
                $"Application.targetFrameRate={Application.targetFrameRate}, " +
                $"framesPerHalfCycle={m_Stimulus.FramesPerHalfCycle}, " +
                $"derivedSoftwareFrequency={derivedFrequency}, " +
                $"meanFrameIntervalMs={meanInterval * 1000d:F3}, " +
                $"minFrameIntervalMs={minInterval * 1000f:F3}, " +
                $"maxFrameIntervalMs={m_MaxFrameInterval * 1000f:F3}, " +
                $"approxMeanUnityFps={meanFps:F3}, longUnityFrameCount={m_LongFrameCount}, " +
                $"unityFrameIndexGapCount={m_FrameGapCount}, maxUnityFrameIndexGap={m_MaxFrameGap}, " +
                $"presentCounterSupported={presentDeltaSupported}, presentStart={FormatCounter(m_PresentStartSupported, m_StartPresentCount)}, " +
                $"presentEnd={FormatCounter(presentEndSupported, endPresentCount)}, presentDelta={presentDelta}, " +
                $"droppedCounterSupported={droppedDeltaSupported}, droppedStart={FormatCounter(m_DroppedStartSupported, m_StartDroppedCount)}, " +
                $"droppedEnd={FormatCounter(droppedEndSupported, endDroppedCount)}, droppedDelta={droppedDelta}. " +
                "Long Unity frames and Unity frame-index gaps are software-side diagnostics, not proof of dropped physical display frames. " +
                "Derived software frequency is not a photodiode-verified physical optical frequency.",
                this);
        }

        private static string FormatCounter(bool supported, int value)
        {
            return supported ? value.ToString() : "unsupported";
        }

        private void OnValidate()
        {
            m_MeasurementDurationSeconds = Mathf.Max(1f, m_MeasurementDurationSeconds);
            if (m_Stimulus == null)
                m_Stimulus = GetComponent<FrameDrivenStimulus>();
        }
    }
}
