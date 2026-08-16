using System;
using System.Text;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Management;

namespace BCIIntelligentRobot.VRStimulus
{
    [DefaultExecutionOrder(100)]
    [DisallowMultipleComponent]
    public sealed class MultiTargetTimingDiagnostics : MonoBehaviour
    {
        private const int RequiredTargetCount = 3;
        private const int RefreshRatePollIntervalFrames = 60;
        private const float RefreshRateChangeToleranceHz = 0.1f;
        private const float LongFrameThresholdMultiplier = 1.5f;

        [SerializeField]
        private MultiTargetStimulusController m_Controller;

        [SerializeField, Min(1f)]
        private float m_MeasurementDurationSeconds = 30f;

        private XRDisplaySubsystem m_DisplaySubsystem;
        private MultiTargetStimulusController.TargetRuntimeSnapshot[] m_StartTargets;
        private bool m_Measuring;
        private bool m_SummaryLogged;
        private bool m_GlobalFrameConsistent = true;
        private double m_StartTime;
        private int m_StartUnityFrame;
        private int m_EndUnityFrame;
        private int m_StartGlobalFrame;
        private int m_EndGlobalFrame;
        private int m_PreviousUnityFrame;
        private int m_LastRefreshPollFrame;
        private int m_ObservedFrames;
        private double m_FrameIntervalSum;
        private float m_MinFrameInterval = float.PositiveInfinity;
        private float m_MaxFrameInterval;
        private int m_LongFrameCount;
        private float m_MaxLongFrameInterval;
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
            if (m_Controller == null)
                m_Controller = GetComponent<MultiTargetStimulusController>();

            if (m_Controller == null)
            {
                Debug.LogError("Multi-target timing diagnostics requires a controller reference.", this);
                enabled = false;
                return;
            }

            m_MeasurementDurationSeconds = Mathf.Max(1f, m_MeasurementDurationSeconds);
        }

        private void LateUpdate()
        {
            if (m_SummaryLogged)
                return;

            if (!m_Measuring)
            {
                if (!m_Controller.enabled || !m_Controller.IsInitialized)
                    return;

                m_DisplaySubsystem = XRGeneralSettings.Instance?
                    .Manager?
                    .activeLoader?
                    .GetLoadedSubsystem<XRDisplaySubsystem>();

                if (m_DisplaySubsystem == null || !m_DisplaySubsystem.running)
                    return;

                BeginMeasurement();
            }

            int currentUnityFrame = Time.frameCount;
            int independentlyCalculatedGlobalFrame = currentUnityFrame - m_Controller.CommonStartFrame;
            int controllerGlobalFrame = m_Controller.CurrentGlobalStimulusFrame;
            if (controllerGlobalFrame != independentlyCalculatedGlobalFrame)
            {
                m_GlobalFrameConsistent = false;
                Debug.LogError(
                    $"SSVEP multi-target global frame mismatch unityFrame={currentUnityFrame}, " +
                    $"controllerGlobalFrame={controllerGlobalFrame}, " +
                    $"independentGlobalFrame={independentlyCalculatedGlobalFrame}.",
                    this);
            }

            m_EndUnityFrame = currentUnityFrame;
            m_EndGlobalFrame = controllerGlobalFrame;
            float frameInterval = Time.unscaledDeltaTime;
            m_ObservedFrames++;
            m_FrameIntervalSum += frameInterval;
            m_MinFrameInterval = Mathf.Min(m_MinFrameInterval, frameInterval);
            m_MaxFrameInterval = Mathf.Max(m_MaxFrameInterval, frameInterval);

            int frameGap = currentUnityFrame - m_PreviousUnityFrame;
            if (frameGap > 1)
            {
                m_FrameGapCount++;
                m_MaxFrameGap = Math.Max(m_MaxFrameGap, frameGap);
            }

            m_PreviousUnityFrame = currentUnityFrame;

            if (m_HasRefreshRate &&
                frameInterval > LongFrameThresholdMultiplier / m_LatestRefreshRate)
            {
                m_LongFrameCount++;
                m_MaxLongFrameInterval = Mathf.Max(m_MaxLongFrameInterval, frameInterval);
            }

            if (currentUnityFrame - m_LastRefreshPollFrame >= RefreshRatePollIntervalFrames)
            {
                ObserveRefreshRate(currentUnityFrame, controllerGlobalFrame, true);
                m_LastRefreshPollFrame = currentUnityFrame;
            }

            if (Time.realtimeSinceStartupAsDouble - m_StartTime >= m_MeasurementDurationSeconds)
                LogSummary();
        }

        private void BeginMeasurement()
        {
            m_StartTargets = m_Controller.GetTargetRuntimeSnapshots();
            if (m_StartTargets.Length != RequiredTargetCount)
            {
                Debug.LogError(
                    $"Multi-target timing diagnostics expected {RequiredTargetCount} initialized targets, " +
                    $"but received {m_StartTargets.Length}.",
                    this);
                enabled = false;
                return;
            }

            m_Measuring = true;
            m_StartTime = Time.realtimeSinceStartupAsDouble;
            m_StartUnityFrame = Time.frameCount;
            m_StartGlobalFrame = m_Controller.CurrentGlobalStimulusFrame;
            m_EndUnityFrame = m_StartUnityFrame;
            m_EndGlobalFrame = m_StartGlobalFrame;
            m_PreviousUnityFrame = m_StartUnityFrame;
            m_LastRefreshPollFrame = m_StartUnityFrame - RefreshRatePollIntervalFrames;

            ObserveRefreshRate(m_StartUnityFrame, m_StartGlobalFrame, false);
            m_PresentStartSupported = m_DisplaySubsystem.TryGetFramePresentCount(out m_StartPresentCount);
            m_DroppedStartSupported = m_DisplaySubsystem.TryGetDroppedFrameCount(out m_StartDroppedCount);

            Debug.Log(
                $"SSVEP MULTI-TARGET TIMING START utc={DateTime.UtcNow:O}, " +
                $"monotonicSeconds={m_StartTime:F6}, durationSeconds={m_MeasurementDurationSeconds:F3}, " +
                $"commonStartFrame={m_Controller.CommonStartFrame}, " +
                $"measurementStartUnityFrame={m_StartUnityFrame}, " +
                $"measurementStartGlobalFrame={m_StartGlobalFrame}, targetCount={m_StartTargets.Length}, " +
                $"presentCounterStartSupported={m_PresentStartSupported}, " +
                $"droppedCounterStartSupported={m_DroppedStartSupported}.",
                this);
        }

        private void ObserveRefreshRate(int unityFrame, int globalFrame, bool detectChange)
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
                    $"SSVEP multi-target timing refresh-rate change unityFrame={unityFrame}, " +
                    $"globalStimulusFrame={globalFrame}, previous={m_LatestRefreshRate:F3}Hz, " +
                    $"new={refreshRate:F3}Hz, monotonicSeconds={Time.realtimeSinceStartupAsDouble:F6}; " +
                    "configured N values remain unchanged.",
                    this);
                m_RefreshRateChangeCount++;
            }

            m_LatestRefreshRate = refreshRate;
            m_MinRefreshRate = Mathf.Min(m_MinRefreshRate, refreshRate);
            m_MaxRefreshRate = Mathf.Max(m_MaxRefreshRate, refreshRate);
        }

        private void LogSummary()
        {
            m_SummaryLogged = true;
            MultiTargetStimulusController.TargetRuntimeSnapshot[] endTargets =
                m_Controller.GetTargetRuntimeSnapshots();
            double actualDuration = Time.realtimeSinceStartupAsDouble - m_StartTime;
            double meanInterval = m_ObservedFrames > 0 ? m_FrameIntervalSum / m_ObservedFrames : 0d;
            double meanFps = meanInterval > 0d ? 1d / meanInterval : 0d;
            float minInterval = m_ObservedFrames > 0 ? m_MinFrameInterval : 0f;

            bool presentEndSupported = m_DisplaySubsystem.TryGetFramePresentCount(out int endPresentCount);
            bool droppedEndSupported = m_DisplaySubsystem.TryGetDroppedFrameCount(out int endDroppedCount);
            bool presentSupported = m_PresentStartSupported && presentEndSupported;
            bool droppedSupported = m_DroppedStartSupported && droppedEndSupported;

            var summary = new StringBuilder(2048);
            summary.Append(
                $"SSVEP MULTI-TARGET TIMING SUMMARY durationSeconds={actualDuration:F3}, " +
                $"commonStartFrame={m_Controller.CommonStartFrame}, " +
                $"startUnityFrame={m_StartUnityFrame}, endUnityFrame={m_EndUnityFrame}, " +
                $"startGlobalFrame={m_StartGlobalFrame}, endGlobalFrame={m_EndGlobalFrame}, " +
                $"observedUnityFrames={m_ObservedFrames}, targetCount={endTargets.Length}.\n" +
                $"refreshSupported={m_HasRefreshRate}, " +
                $"initialRefreshHz={FormatRefresh(m_HasRefreshRate, m_InitialRefreshRate)}, " +
                $"minRefreshHz={FormatRefresh(m_HasRefreshRate, m_MinRefreshRate)}, " +
                $"maxRefreshHz={FormatRefresh(m_HasRefreshRate, m_MaxRefreshRate)}, " +
                $"latestRefreshHz={FormatRefresh(m_HasRefreshRate, m_LatestRefreshRate)}, " +
                $"refreshRateChangeCount={m_RefreshRateChangeCount}, " +
                $"Application.targetFrameRate={Application.targetFrameRate}.\n" +
                $"meanFrameIntervalMs={meanInterval * 1000d:F3}, " +
                $"minFrameIntervalMs={minInterval * 1000f:F3}, " +
                $"maxFrameIntervalMs={m_MaxFrameInterval * 1000f:F3}, " +
                $"approxMeanUnityFps={meanFps:F3}, longUnityFrameCount={m_LongFrameCount}, " +
                $"maxLongFrameIntervalMs={m_MaxLongFrameInterval * 1000f:F3}, " +
                $"unityFrameIndexGapCount={m_FrameGapCount}, maxUnityFrameIndexGap={m_MaxFrameGap}.\n" +
                $"globalFrameConsistency={(m_GlobalFrameConsistent ? "PASS" : "FAIL")}, " +
                $"derivedFrequenciesChangedWithRefresh={(m_RefreshRateChangeCount > 0)}.\n");

            bool allTransitionsMatch = endTargets.Length == m_StartTargets.Length;
            int comparableTargetCount = Math.Min(m_StartTargets.Length, endTargets.Length);
            for (int i = 0; i < comparableTargetCount; i++)
            {
                MultiTargetStimulusController.TargetRuntimeSnapshot start = m_StartTargets[i];
                MultiTargetStimulusController.TargetRuntimeSnapshot end = endTargets[i];
                int observedDelta = end.TransitionCount - start.TransitionCount;
                int expectedDelta = CalculateExpectedTransitionDelta(
                    m_StartGlobalFrame,
                    m_EndGlobalFrame,
                    end.FramesPerHalfCycle,
                    end.PhaseOffsetFrames);
                bool transitionMatch =
                    start.TargetId == end.TargetId &&
                    start.TargetIndex == end.TargetIndex &&
                    observedDelta == expectedDelta;
                allTransitionsMatch &= transitionMatch;
                string derivedFrequency = m_HasRefreshRate
                    ? (m_LatestRefreshRate / (2f * end.FramesPerHalfCycle)).ToString("F3") + "Hz"
                    : "unsupported";

                summary.Append(
                    $"target[{i}]Id={end.TargetId}, target[{i}]Index={end.TargetIndex}, " +
                    $"target[{i}]N={end.FramesPerHalfCycle}, " +
                    $"target[{i}]PhaseOffsetFrames={end.PhaseOffsetFrames}, " +
                    $"target[{i}]DerivedLatestFrequency={derivedFrequency}.\n" +
                    $"target[{i}]TransitionStart={start.TransitionCount}, " +
                    $"target[{i}]TransitionEnd={end.TransitionCount}, " +
                    $"target[{i}]TransitionDelta={observedDelta}, " +
                    $"target[{i}]ExpectedTransitionDelta={expectedDelta}, " +
                    $"target[{i}]TransitionMatch={transitionMatch}.\n");
            }

            summary.Append(
                $"allTargetTransitionsMatch={allTransitionsMatch}, " +
                $"presentCounterSupported={presentSupported}, " +
                $"presentStart={FormatCounter(m_PresentStartSupported, m_StartPresentCount)}, " +
                $"presentEnd={FormatCounter(presentEndSupported, endPresentCount)}, " +
                $"presentDelta={FormatDelta(presentSupported, endPresentCount - m_StartPresentCount)}, " +
                $"droppedCounterSupported={droppedSupported}, " +
                $"droppedStart={FormatCounter(m_DroppedStartSupported, m_StartDroppedCount)}, " +
                $"droppedEnd={FormatCounter(droppedEndSupported, endDroppedCount)}, " +
                $"droppedRawDelta={FormatDelta(droppedSupported, endDroppedCount - m_StartDroppedCount)}.\n" +
                "Long Unity frames and Unity frame-index gaps are software-side diagnostics, not proof of physical display drops. " +
                "This is software/runtime timing verification and does not replace physical optical measurement.");

            string[] summaryLines = summary.ToString().Split('\n');
            for (int i = 0; i < summaryLines.Length; i++)
            {
                if (!string.IsNullOrWhiteSpace(summaryLines[i]))
                {
                    Debug.Log($"SSVEP MULTI-TARGET TIMING SUMMARY PART {i + 1}: {summaryLines[i]}", this);
                }
            }
        }

        private static int CalculateExpectedTransitionDelta(
            int startGlobalFrame,
            int endGlobalFrame,
            int framesPerHalfCycle,
            int phaseOffsetFrames)
        {
            int startHalfCycle = (startGlobalFrame + phaseOffsetFrames) / framesPerHalfCycle;
            int endHalfCycle = (endGlobalFrame + phaseOffsetFrames) / framesPerHalfCycle;
            return endHalfCycle - startHalfCycle;
        }

        private static string FormatRefresh(bool supported, float value)
        {
            return supported ? value.ToString("F3") : "unsupported";
        }

        private static string FormatCounter(bool supported, int value)
        {
            return supported ? value.ToString() : "unsupported";
        }

        private static string FormatDelta(bool supported, int value)
        {
            return supported ? value.ToString() : "unsupported";
        }

        private void OnValidate()
        {
            m_MeasurementDurationSeconds = Mathf.Max(1f, m_MeasurementDurationSeconds);
            if (m_Controller == null)
                m_Controller = GetComponent<MultiTargetStimulusController>();
        }
    }
}
