using System;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Management;

namespace BCIIntelligentRobot.VRStimulus
{
    [DefaultExecutionOrder(-100)]
    [DisallowMultipleComponent]
    public sealed class M5TrialStimulusController : MonoBehaviour
    {
        public enum TrialState
        {
            Idle,
            TrialStart,
            Stimulating,
            TrialStop
        }

        private static readonly int ColorPropertyId = Shader.PropertyToID("_Color");
        private const int RequiredTargetCount = 3;
        private const string SoftwareTimingSemantics =
            "software_render_scheduling;not_physical_optical_timing";

        [Serializable]
        private sealed class TargetConfiguration
        {
            [SerializeField]
            private string m_TargetId;

            [SerializeField]
            private int m_TargetIndex;

            [SerializeField]
            private Renderer m_TargetRenderer;

            [SerializeField, Min(1)]
            private int m_FramesPerHalfCycle = 3;

            [SerializeField, Min(0)]
            private int m_PhaseOffsetFrames;

            [NonSerialized]
            private MaterialPropertyBlock m_PropertyBlock;

            [NonSerialized]
            private bool m_IsWhite;

            public string TargetId => m_TargetId;
            public int TargetIndex => m_TargetIndex;
            public Renderer TargetRenderer => m_TargetRenderer;
            public int FramesPerHalfCycle => m_FramesPerHalfCycle;
            public int PhaseOffsetFrames => m_PhaseOffsetFrames;
            public bool IsWhite => m_IsWhite;

            public void Initialize()
            {
                m_PropertyBlock = new MaterialPropertyBlock();
                ApplyState(false);
            }

            public void ApplyState(bool white)
            {
                m_IsWhite = white;
                m_TargetRenderer.GetPropertyBlock(m_PropertyBlock);
                m_PropertyBlock.SetColor(ColorPropertyId, white ? Color.white : Color.black);
                m_TargetRenderer.SetPropertyBlock(m_PropertyBlock);
            }

            public StimulusTargetEventSnapshot CreateSnapshot()
            {
                return new StimulusTargetEventSnapshot
                {
                    targetId = m_TargetId,
                    targetIndex = m_TargetIndex,
                    framesPerHalfCycle = m_FramesPerHalfCycle,
                    phaseOffsetFrames = m_PhaseOffsetFrames,
                    isWhite = m_IsWhite
                };
            }
        }

        [SerializeField]
        private LocalStimulusEventLogger m_EventLogger;

        [SerializeField]
        private TargetConfiguration[] m_Targets = new TargetConfiguration[RequiredTargetCount];

        [SerializeField]
        private bool m_AutoStartFirstTrial = true;

        [SerializeField, Min(1)]
        private int m_AutoStopAfterStimulusFrames = 2160;

        private TrialState m_State = TrialState.Idle;
        private string m_SessionId;
        private string m_TrialId;
        private string m_TargetConfigurationId;
        private int m_CommonStartFrame = -1;
        private long m_EventSequence;
        private string m_PendingStopReason;
        private bool m_IsInitialized;

        public TrialState State => m_State;
        public string SessionId => m_SessionId;
        public string TrialId => m_TrialId;
        public int CommonStartFrame => m_CommonStartFrame;
        public int CurrentGlobalStimulusFrame =>
            m_State == TrialState.Stimulating ? Time.frameCount - m_CommonStartFrame : -1;

        private void Awake()
        {
            if (!ValidateConfiguration())
            {
                enabled = false;
                return;
            }

            foreach (TargetConfiguration target in m_Targets)
                target.Initialize();

            m_TargetConfigurationId = BuildTargetConfigurationId();
            m_SessionId = Guid.NewGuid().ToString("N");
            if (!m_EventLogger.BeginSession(m_SessionId))
            {
                enabled = false;
                return;
            }

            m_IsInitialized = true;
            RecordEvent(
                "session_started",
                string.Empty,
                Time.frameCount,
                -1,
                -1,
                -1,
                string.Empty);
        }

        private void Start()
        {
            if (m_AutoStartFirstTrial)
                RequestStartTrial();
        }

        private void LateUpdate()
        {
            int currentFrame = Time.frameCount;

            if (m_State == TrialState.TrialStart)
            {
                StartTrialSoftware(currentFrame);
                return;
            }

            if (m_State == TrialState.Idle)
                return;

            int globalStimulusFrame = currentFrame - m_CommonStartFrame;
            bool reachedConfiguredEnd =
                m_AutoStopAfterStimulusFrames > 0 &&
                globalStimulusFrame >= m_AutoStopAfterStimulusFrames;

            if (reachedConfiguredEnd && m_State == TrialState.Stimulating)
            {
                m_State = TrialState.TrialStop;
                m_PendingStopReason = "configured_frame_limit";
            }

            if (m_State == TrialState.TrialStop)
            {
                StopTrialSoftware(currentFrame, globalStimulusFrame, m_PendingStopReason);
                return;
            }

            ApplyStimulusStates(globalStimulusFrame);
        }

        public bool RequestStartTrial()
        {
            if (!m_IsInitialized || m_State != TrialState.Idle)
                return false;

            m_State = TrialState.TrialStart;
            return true;
        }

        public bool RequestStopTrial(string stopReason)
        {
            if (!m_IsInitialized || m_State != TrialState.Stimulating)
                return false;

            m_State = TrialState.TrialStop;
            m_PendingStopReason = string.IsNullOrWhiteSpace(stopReason)
                ? "unspecified"
                : stopReason.Trim();
            return true;
        }

        [ContextMenu("M5/Request Start Trial")]
        private void RequestStartTrialFromContextMenu()
        {
            RequestStartTrial();
        }

        [ContextMenu("M5/Request Stop Trial")]
        private void RequestStopTrialFromContextMenu()
        {
            RequestStopTrial("manual_context_menu");
        }

        private void StartTrialSoftware(int currentFrame)
        {
            m_PendingStopReason = string.Empty;
            m_TrialId = Guid.NewGuid().ToString("N");
            m_CommonStartFrame = currentFrame;
            m_State = TrialState.Stimulating;

            ApplyStimulusStates(0);
            RecordEvent(
                "stimulus_started_software",
                m_TrialId,
                currentFrame,
                m_CommonStartFrame,
                0,
                -1,
                string.Empty);

            Debug.Log(
                $"M5 stimulus started in software sessionId={m_SessionId}, trialId={m_TrialId}, " +
                $"commonStartFrame={m_CommonStartFrame}, globalStimulusFrame=0. " +
                "This is a Unity software/render-scheduling event, not a physical optical onset.",
                this);
        }

        private void StopTrialSoftware(int currentFrame, int globalStimulusFrame, string stopReason)
        {
            int lastActiveGlobalFrame = Mathf.Max(-1, globalStimulusFrame - 1);
            ApplyIdleBlackState();
            m_State = TrialState.Idle;

            RecordEvent(
                "stimulus_stopped_software",
                m_TrialId,
                currentFrame,
                m_CommonStartFrame,
                globalStimulusFrame,
                lastActiveGlobalFrame,
                stopReason);

            Debug.Log(
                $"M5 stimulus stopped in software sessionId={m_SessionId}, trialId={m_TrialId}, " +
                $"stopUnityFrame={currentFrame}, stopGlobalStimulusFrame={globalStimulusFrame}, " +
                $"lastActiveGlobalStimulusFrame={lastActiveGlobalFrame}, stopReason={stopReason}. " +
                "All targets were switched to the black engineering idle state; this is not a physical optical timestamp.",
                this);

            m_TrialId = string.Empty;
            m_CommonStartFrame = -1;
            m_PendingStopReason = string.Empty;
        }

        private void ApplyStimulusStates(int globalStimulusFrame)
        {
            foreach (TargetConfiguration target in m_Targets)
            {
                int effectiveFrame = globalStimulusFrame + target.PhaseOffsetFrames;
                int halfCycleIndex = effectiveFrame / target.FramesPerHalfCycle;
                target.ApplyState((halfCycleIndex & 1) == 0);
            }
        }

        private void ApplyIdleBlackState()
        {
            foreach (TargetConfiguration target in m_Targets)
                target.ApplyState(false);
        }

        private void RecordEvent(
            string eventType,
            string trialId,
            int unityFrame,
            int commonStartFrame,
            int globalStimulusFrame,
            int lastActiveGlobalStimulusFrame,
            string stopReason)
        {
            bool refreshAvailable = TryGetRefreshRate(out float refreshRate);
            var targetSnapshots = new StimulusTargetEventSnapshot[m_Targets.Length];
            for (int i = 0; i < m_Targets.Length; i++)
                targetSnapshots[i] = m_Targets[i].CreateSnapshot();

            var eventRecord = new StimulusEventRecord
            {
                eventType = eventType,
                sessionId = m_SessionId,
                trialId = trialId,
                sequence = m_EventSequence++,
                trialState = m_State.ToString(),
                unityFrame = unityFrame,
                commonStartFrame = commonStartFrame,
                globalStimulusFrame = globalStimulusFrame,
                lastActiveGlobalStimulusFrame = lastActiveGlobalStimulusFrame,
                questMonotonicSeconds = Time.realtimeSinceStartupAsDouble,
                utc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture),
                xrRefreshRateAvailable = refreshAvailable,
                xrRefreshRateHz = refreshAvailable ? refreshRate : 0f,
                targetConfigurationId = m_TargetConfigurationId,
                targets = targetSnapshots,
                stopReason = stopReason,
                timingSemantics = SoftwareTimingSemantics
            };

            if (!m_EventLogger.Record(eventRecord))
            {
                Debug.LogError(
                    $"Failed to enqueue M5 local stimulus event eventType={eventType}, " +
                    $"sessionId={m_SessionId}, trialId={trialId}.",
                    this);
            }
        }

        private static bool TryGetRefreshRate(out float refreshRate)
        {
            refreshRate = 0f;
            XRDisplaySubsystem displaySubsystem = XRGeneralSettings.Instance?
                .Manager?
                .activeLoader?
                .GetLoadedSubsystem<XRDisplaySubsystem>();

            return displaySubsystem != null &&
                displaySubsystem.running &&
                displaySubsystem.TryGetDisplayRefreshRate(out refreshRate) &&
                refreshRate > 0f;
        }

        private string BuildTargetConfigurationId()
        {
            var parts = new string[m_Targets.Length];
            for (int i = 0; i < m_Targets.Length; i++)
            {
                TargetConfiguration target = m_Targets[i];
                parts[i] = string.Format(
                    CultureInfo.InvariantCulture,
                    "{0}:{1}:n{2}:p{3}",
                    target.TargetId,
                    target.TargetIndex,
                    target.FramesPerHalfCycle,
                    target.PhaseOffsetFrames);
            }

            return string.Join("|", parts);
        }

        private bool ValidateConfiguration()
        {
            if (m_EventLogger == null)
            {
                Debug.LogError("M5 trial stimulus controller requires a local event logger reference.", this);
                return false;
            }

            if (m_Targets == null || m_Targets.Length != RequiredTargetCount)
            {
                Debug.LogError($"M5 trial stimulus controller requires exactly {RequiredTargetCount} targets.", this);
                return false;
            }

            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            var targetIndexes = new HashSet<int>();
            var renderers = new HashSet<Renderer>();
            for (int i = 0; i < m_Targets.Length; i++)
            {
                TargetConfiguration target = m_Targets[i];
                if (target == null ||
                    string.IsNullOrWhiteSpace(target.TargetId) ||
                    !targetIds.Add(target.TargetId) ||
                    !targetIndexes.Add(target.TargetIndex) ||
                    target.TargetRenderer == null ||
                    !renderers.Add(target.TargetRenderer) ||
                    target.FramesPerHalfCycle < 1 ||
                    target.PhaseOffsetFrames < 0)
                {
                    Debug.LogError($"Invalid M5 target configuration at array position {i}.", this);
                    return false;
                }

                Material material = target.TargetRenderer.sharedMaterial;
                if (material == null || !material.HasProperty(ColorPropertyId))
                {
                    Debug.LogError(
                        $"M5 target '{target.TargetId}' requires a material with a _Color property.",
                        this);
                    return false;
                }
            }

            m_AutoStopAfterStimulusFrames = Mathf.Max(1, m_AutoStopAfterStimulusFrames);
            return true;
        }

        private void OnValidate()
        {
            m_AutoStopAfterStimulusFrames = Mathf.Max(1, m_AutoStopAfterStimulusFrames);
            if (m_EventLogger == null)
                m_EventLogger = GetComponent<LocalStimulusEventLogger>();
        }
    }
}
