using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.XR;
using UnityEngine.XR.Management;

namespace BCIIntelligentRobot.VRStimulus
{
    [DisallowMultipleComponent]
    public sealed class MultiTargetStimulusController : MonoBehaviour
    {
        public readonly struct TargetRuntimeSnapshot
        {
            public TargetRuntimeSnapshot(
                string targetId,
                int targetIndex,
                int framesPerHalfCycle,
                int phaseOffsetFrames,
                int transitionCount,
                bool isWhite)
            {
                TargetId = targetId;
                TargetIndex = targetIndex;
                FramesPerHalfCycle = framesPerHalfCycle;
                PhaseOffsetFrames = phaseOffsetFrames;
                TransitionCount = transitionCount;
                IsWhite = isWhite;
            }

            public string TargetId { get; }
            public int TargetIndex { get; }
            public int FramesPerHalfCycle { get; }
            public int PhaseOffsetFrames { get; }
            public int TransitionCount { get; }
            public bool IsWhite { get; }
        }

        private static readonly int ColorPropertyId = Shader.PropertyToID("_Color");
        private static readonly Color SelectedStaticColor = new Color(0.15f, 0.45f, 1f, 1f);
        private const int RequiredTargetCount = 3;
        private const int RefreshRateRetryIntervalFrames = 30;
        private const int RefreshRateUnavailableWarningFrame = 300;
        private const float ExpectedRefreshRateHz = 72f;
        private const float RefreshRateToleranceHz = 0.1f;

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

            [NonSerialized]
            private bool m_HasAppliedState;

            [NonSerialized]
            private int m_TransitionCount;

            [NonSerialized]
            private bool m_IsCandidateActive = true;

            [NonSerialized]
            private bool m_IsStaticSelectionVisual;

            public string TargetId => m_TargetId;
            public int TargetIndex => m_TargetIndex;
            public Renderer TargetRenderer => m_TargetRenderer;
            public int FramesPerHalfCycle => m_FramesPerHalfCycle;
            public int PhaseOffsetFrames => m_PhaseOffsetFrames;
            public int TransitionCount => m_TransitionCount;
            public bool IsWhite => m_IsWhite;
            public bool IsCandidateActive => m_IsCandidateActive;

            public void Configure(
                string targetId,
                int targetIndex,
                Renderer targetRenderer,
                int framesPerHalfCycle,
                int phaseOffsetFrames)
            {
                m_TargetId = targetId;
                m_TargetIndex = targetIndex;
                m_TargetRenderer = targetRenderer;
                m_FramesPerHalfCycle = framesPerHalfCycle;
                m_PhaseOffsetFrames = phaseOffsetFrames;
            }

            public void Initialize()
            {
                m_PropertyBlock = new MaterialPropertyBlock();
                m_HasAppliedState = false;
                m_TransitionCount = 0;
                m_IsCandidateActive = true;
                m_IsStaticSelectionVisual = false;
            }

            public void SetCandidateActive(bool active)
            {
                if (m_IsCandidateActive == active)
                    return;

                m_IsCandidateActive = active;
                m_HasAppliedState = false;
            }

            public void ApplyState(bool white)
            {
                if (!m_IsCandidateActive)
                {
                    if (m_HasAppliedState && m_IsStaticSelectionVisual)
                        return;

                    m_HasAppliedState = true;
                    m_IsStaticSelectionVisual = true;
                    m_TransitionCount++;
                    m_TargetRenderer.GetPropertyBlock(m_PropertyBlock);
                    m_PropertyBlock.SetColor(ColorPropertyId, SelectedStaticColor);
                    m_TargetRenderer.SetPropertyBlock(m_PropertyBlock);
                    return;
                }

                if (m_HasAppliedState && !m_IsStaticSelectionVisual && white == m_IsWhite)
                    return;

                m_IsWhite = white;
                m_HasAppliedState = true;
                m_IsStaticSelectionVisual = false;
                m_TransitionCount++;
                m_TargetRenderer.GetPropertyBlock(m_PropertyBlock);
                m_PropertyBlock.SetColor(ColorPropertyId, white ? Color.white : Color.black);
                m_TargetRenderer.SetPropertyBlock(m_PropertyBlock);
            }
        }

        [SerializeField]
        private TargetConfiguration[] m_Targets = new TargetConfiguration[RequiredTargetCount];

        private int m_CommonStartFrame;
        private int m_LastRefreshRateAttemptFrame;
        private bool m_HasObservedRefreshRate;
        private bool m_HasWarnedRefreshRateUnavailable;
        private bool m_IsInitialized;

        public int CommonStartFrame => m_CommonStartFrame;
        public int CurrentGlobalStimulusFrame => Time.frameCount - m_CommonStartFrame;
        public bool IsInitialized => m_IsInitialized;
        public int TargetCount => m_Targets?.Length ?? 0;

        public bool IsSlotCandidateActive(int slotIndex)
        {
            return m_IsInitialized && slotIndex >= 0 && slotIndex < m_Targets.Length &&
                   m_Targets[slotIndex].IsCandidateActive;
        }

        /// <summary>
        /// Configures the verified three-slot frame-driven controller from runtime-created world targets.
        /// All slots still share one common frame origin and use 5/4/3 frames per half-cycle.
        /// </summary>
        public void ConfigureRuntimeTargets(Renderer[] targetRenderers)
        {
            if (m_IsInitialized)
                throw new InvalidOperationException("SSVEP targets are already initialized.");
            if (targetRenderers == null || targetRenderers.Length != RequiredTargetCount)
                throw new ArgumentException($"Exactly {RequiredTargetCount} target renderers are required.", nameof(targetRenderers));

            int[] framesPerHalfCycle = { 5, 4, 3 };
            m_Targets = new TargetConfiguration[RequiredTargetCount];
            for (int i = 0; i < RequiredTargetCount; i++)
            {
                m_Targets[i] = new TargetConfiguration();
                m_Targets[i].Configure($"slot-{i}", i, targetRenderers[i], framesPerHalfCycle[i], 0);
            }

            InitializeController();
        }

        public void SetSlotVisible(int slotIndex, bool visible)
        {
            if (!m_IsInitialized || slotIndex < 0 || slotIndex >= m_Targets.Length)
                return;

            Renderer renderer = m_Targets[slotIndex].TargetRenderer;
            if (renderer != null)
                renderer.gameObject.SetActive(visible);
        }

        /// <summary>
        /// Keeps the slot identity and renderer visible, but removes a selected
        /// target from the active frame-driven SSVEP candidate set.
        /// </summary>
        public void SetSlotCandidateActive(int slotIndex, bool active)
        {
            if (!m_IsInitialized || slotIndex < 0 || slotIndex >= m_Targets.Length)
                return;

            m_Targets[slotIndex].SetCandidateActive(active);
        }

        public TargetRuntimeSnapshot[] GetTargetRuntimeSnapshots()
        {
            if (!m_IsInitialized || m_Targets == null)
                return Array.Empty<TargetRuntimeSnapshot>();

            var snapshots = new TargetRuntimeSnapshot[m_Targets.Length];
            for (int i = 0; i < m_Targets.Length; i++)
            {
                TargetConfiguration target = m_Targets[i];
                snapshots[i] = new TargetRuntimeSnapshot(
                    target.TargetId,
                    target.TargetIndex,
                    target.FramesPerHalfCycle,
                    target.PhaseOffsetFrames,
                    target.TransitionCount,
                    target.IsWhite);
            }

            return snapshots;
        }

        private void Awake()
        {
            // Legacy inspector-configured scenes initialize here. The M7 runtime binding
            // calls ConfigureRuntimeTargets after it has created its three world targets.
            if (HasCompleteConfiguration())
                InitializeController();
        }

        private void InitializeController()
        {
            if (!ValidateConfiguration())
            {
                enabled = false;
                return;
            }

            foreach (TargetConfiguration target in m_Targets)
                target.Initialize();

            m_CommonStartFrame = Time.frameCount;
            m_LastRefreshRateAttemptFrame = m_CommonStartFrame - RefreshRateRetryIntervalFrames;
            ApplyStates(0);
            m_IsInitialized = true;

            Debug.Log(
                $"SSVEP multi-target startup utc={DateTime.UtcNow:O}, " +
                $"monotonicSeconds={Time.realtimeSinceStartupAsDouble:F6}, " +
                $"commonStartFrame={m_CommonStartFrame}, Application.targetFrameRate={Application.targetFrameRate}, " +
                $"targetCount={m_Targets.Length}. All targets use this single common frame origin.",
                this);
        }

        private bool HasCompleteConfiguration()
        {
            if (m_Targets == null || m_Targets.Length != RequiredTargetCount)
                return false;

            for (int i = 0; i < m_Targets.Length; i++)
            {
                if (m_Targets[i] == null || m_Targets[i].TargetRenderer == null)
                    return false;
            }

            return true;
        }

        private void LateUpdate()
        {
            if (!m_IsInitialized)
                return;

            int currentFrame = Time.frameCount;
            int globalStimulusFrame = currentFrame - m_CommonStartFrame;
            ApplyStates(globalStimulusFrame);
            TryObserveRefreshRate(currentFrame);
        }

        private void ApplyStates(int globalStimulusFrame)
        {
            foreach (TargetConfiguration target in m_Targets)
            {
                int effectiveFrame = globalStimulusFrame + target.PhaseOffsetFrames;
                int halfCycleIndex = effectiveFrame / target.FramesPerHalfCycle;
                bool shouldBeWhite = (halfCycleIndex & 1) == 0;
                target.ApplyState(shouldBeWhite);
            }
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
                Debug.Log(
                    $"SSVEP multi-target XR refresh rate observed={refreshRate:F3}Hz, " +
                    $"Application.targetFrameRate={Application.targetFrameRate}.",
                    this);

                if (Mathf.Abs(refreshRate - ExpectedRefreshRateHz) > RefreshRateToleranceHz)
                {
                    Debug.LogWarning(
                        $"XR refresh rate differs from expected {ExpectedRefreshRateHz:F3}Hz; " +
                        "configured integer-frame stimuli therefore have different derived software frequencies. " +
                        "The controller will continue without changing framesPerHalfCycle.",
                        this);
                }

                foreach (TargetConfiguration target in m_Targets)
                {
                    float derivedFrequency = refreshRate / (2f * target.FramesPerHalfCycle);
                    Debug.Log(
                        $"SSVEP multi-target parameter targetId={target.TargetId}, " +
                        $"targetIndex={target.TargetIndex}, " +
                        $"worldPosition={target.TargetRenderer.transform.position}, " +
                        $"framesPerHalfCycle={target.FramesPerHalfCycle}, " +
                        $"phaseOffsetFrames={target.PhaseOffsetFrames}, " +
                        $"derivedSoftwareFrequency={derivedFrequency:F3}Hz, " +
                        $"commonStartFrame={m_CommonStartFrame}.",
                        this);
                }

                return;
            }

            if (!m_HasWarnedRefreshRateUnavailable &&
                currentFrame - m_CommonStartFrame >= RefreshRateUnavailableWarningFrame)
            {
                m_HasWarnedRefreshRateUnavailable = true;
                Debug.LogWarning(
                    "SSVEP multi-target XR display refresh rate remains unavailable after repeated low-frequency attempts; " +
                    "stimulation continues from the shared Unity frame origin without forcing a refresh rate.",
                    this);
            }
        }

        private bool ValidateConfiguration()
        {
            if (m_Targets == null || m_Targets.Length != RequiredTargetCount)
            {
                Debug.LogError($"MultiTargetStimulusController requires exactly {RequiredTargetCount} target configurations.", this);
                return false;
            }

            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            var targetIndexes = new HashSet<int>();
            var renderers = new HashSet<Renderer>();

            for (int i = 0; i < m_Targets.Length; i++)
            {
                TargetConfiguration target = m_Targets[i];
                if (target == null)
                {
                    Debug.LogError($"SSVEP target configuration at array position {i} is null.", this);
                    return false;
                }

                if (string.IsNullOrWhiteSpace(target.TargetId) || !targetIds.Add(target.TargetId))
                {
                    Debug.LogError($"SSVEP target at array position {i} requires a non-empty, unique target ID.", this);
                    return false;
                }

                if (!targetIndexes.Add(target.TargetIndex))
                {
                    Debug.LogError($"SSVEP target index {target.TargetIndex} is duplicated.", this);
                    return false;
                }

                if (target.TargetRenderer == null || !renderers.Add(target.TargetRenderer))
                {
                    Debug.LogError($"SSVEP target '{target.TargetId}' requires a unique Renderer reference.", this);
                    return false;
                }

                if (target.FramesPerHalfCycle < 1)
                {
                    Debug.LogError($"SSVEP target '{target.TargetId}' requires framesPerHalfCycle >= 1.", this);
                    return false;
                }

                Material material = target.TargetRenderer.sharedMaterial;
                if (material == null || !material.HasProperty(ColorPropertyId))
                {
                    Debug.LogError($"SSVEP target '{target.TargetId}' requires a material with a _Color property.", this);
                    return false;
                }
            }

            return true;
        }
    }
}
