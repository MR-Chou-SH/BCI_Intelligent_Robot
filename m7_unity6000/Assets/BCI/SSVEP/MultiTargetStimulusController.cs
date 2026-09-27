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

            [SerializeField]
            private Color m_ActiveColor = Color.white;

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

            [NonSerialized]
            private bool m_IsStaticPreview;

            [NonSerialized]
            private bool m_StaticPreviewWhite;

            [NonSerialized]
            private bool m_IsBrowseFlicker;

            public string TargetId => m_TargetId;
            public int TargetIndex => m_TargetIndex;
            public Renderer TargetRenderer => m_TargetRenderer;
            public int FramesPerHalfCycle => m_FramesPerHalfCycle;
            public int PhaseOffsetFrames => m_PhaseOffsetFrames;
            public int TransitionCount => m_TransitionCount;
            public bool IsWhite => m_IsWhite;
            public bool IsCandidateActive => m_IsCandidateActive;
            public bool IsBrowseFlicker => m_IsBrowseFlicker;

            public void Configure(
                string targetId,
                int targetIndex,
                Renderer targetRenderer,
                int framesPerHalfCycle,
                int phaseOffsetFrames,
                Color activeColor)
            {
                m_TargetId = targetId;
                m_TargetIndex = targetIndex;
                m_TargetRenderer = targetRenderer;
                m_FramesPerHalfCycle = framesPerHalfCycle;
                m_PhaseOffsetFrames = phaseOffsetFrames;
                m_ActiveColor = activeColor;
            }

            public void Initialize()
            {
                m_PropertyBlock = new MaterialPropertyBlock();
                m_HasAppliedState = false;
                m_TransitionCount = 0;
                m_IsCandidateActive = true;
                m_IsStaticSelectionVisual = false;
                m_IsStaticPreview = false;
                m_StaticPreviewWhite = false;
                m_IsBrowseFlicker = false;
            }

            public void SetCandidateActive(bool active)
            {
                if (m_IsCandidateActive == active && !m_IsStaticPreview && !m_IsBrowseFlicker)
                    return;

                m_IsCandidateActive = active;
                m_IsStaticPreview = false;
                m_IsBrowseFlicker = false;
                m_HasAppliedState = false;
            }

            public void SetStaticPreview(bool white)
            {
                m_IsCandidateActive = false;
                m_IsStaticPreview = true;
                m_StaticPreviewWhite = white;
                m_IsBrowseFlicker = false;
                m_HasAppliedState = false;
            }

            public void SetBrowseFlicker(bool enabled)
            {
                m_IsCandidateActive = false;
                m_IsStaticPreview = false;
                m_IsBrowseFlicker = enabled;
                m_HasAppliedState = false;
            }

            public void ApplyState(bool white)
            {
                if (m_IsBrowseFlicker)
                {
                    if (m_HasAppliedState && m_IsWhite == white)
                        return;

                    m_IsWhite = white;
                    m_HasAppliedState = true;
                    m_IsStaticSelectionVisual = false;
                    m_TransitionCount++;
                    m_TargetRenderer.GetPropertyBlock(m_PropertyBlock);
                    m_PropertyBlock.SetColor(ColorPropertyId, white ? m_ActiveColor : Color.black);
                    m_TargetRenderer.SetPropertyBlock(m_PropertyBlock);
                    return;
                }

                if (m_IsStaticPreview)
                {
                    if (m_HasAppliedState && m_IsWhite == m_StaticPreviewWhite)
                        return;

                    m_IsWhite = m_StaticPreviewWhite;
                    m_HasAppliedState = true;
                    m_IsStaticSelectionVisual = false;
                    m_TransitionCount++;
                    m_TargetRenderer.GetPropertyBlock(m_PropertyBlock);
                    m_PropertyBlock.SetColor(ColorPropertyId, m_StaticPreviewWhite ? Color.white : Color.black);
                    m_TargetRenderer.SetPropertyBlock(m_PropertyBlock);
                    return;
                }

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
                m_PropertyBlock.SetColor(ColorPropertyId, white ? m_ActiveColor : Color.black);
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
        private bool m_FormalStimulusActive;
        private int m_FormalStimulusEpochCount;

        public int CommonStartFrame => m_CommonStartFrame;
        public int CurrentGlobalStimulusFrame => Time.frameCount - m_CommonStartFrame;
        public bool IsInitialized => m_IsInitialized;
        public int TargetCount => m_Targets?.Length ?? 0;
        public bool IsFormalStimulusActive => m_FormalStimulusActive;
        public int FormalStimulusEpochCount => m_FormalStimulusEpochCount;
        public bool IsVisualFlickerEnabled
        {
            get
            {
                if (!m_IsInitialized || m_Targets == null)
                    return false;
                for (int index = 0; index < m_Targets.Length; index++)
                {
                    if (m_Targets[index].IsCandidateActive || m_Targets[index].IsBrowseFlicker)
                        return true;
                }
                return false;
            }
        }

        public bool IsSlotCandidateActive(int slotIndex)
        {
            return m_IsInitialized && slotIndex >= 0 && slotIndex < m_Targets.Length &&
                   m_Targets[slotIndex].IsCandidateActive;
        }

        /// <summary>
        /// Configures the verified three-slot frame-driven controller from runtime-created world targets.
        /// All slots still share one common frame origin and use 5/4/3 frames per half-cycle.
        /// </summary>
        public void ConfigureRuntimeTargets(Renderer[] targetRenderers, Color[] activeColors = null)
        {
            if (m_IsInitialized)
                throw new InvalidOperationException("SSVEP targets are already initialized.");
            if (targetRenderers == null || targetRenderers.Length != RequiredTargetCount)
                throw new ArgumentException($"Exactly {RequiredTargetCount} target renderers are required.", nameof(targetRenderers));
            if (activeColors != null && activeColors.Length != RequiredTargetCount)
                throw new ArgumentException($"Exactly {RequiredTargetCount} active colors are required.", nameof(activeColors));

            int[] framesPerHalfCycle = { 5, 4, 3 };
            m_Targets = new TargetConfiguration[RequiredTargetCount];
            for (int i = 0; i < RequiredTargetCount; i++)
            {
                m_Targets[i] = new TargetConfiguration();
                m_Targets[i].Configure(
                    $"slot-{i}", i, targetRenderers[i], framesPerHalfCycle[i], 0,
                    activeColors == null ? Color.white : activeColors[i]);
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

        /// <summary>
        /// Starts a Browse-only frame-driven preview. Browse flicker is visual
        /// exposure only: it is not a formal EEG stimulus epoch and does not
        /// create an onset/sample anchor.
        /// </summary>
        public void SetSlotBrowseFlicker(int slotIndex, bool enabled)
        {
            if (!m_IsInitialized || slotIndex < 0 || slotIndex >= m_Targets.Length)
                return;

            m_Targets[slotIndex].SetBrowseFlicker(enabled);
            m_Targets[slotIndex].ApplyState((Time.frameCount - m_CommonStartFrame +
                m_Targets[slotIndex].PhaseOffsetFrames) / m_Targets[slotIndex].FramesPerHalfCycle % 2 == 0);
        }

        /// <summary>
        /// Re-anchors the formal SSVEP epoch at selection_open. This is kept
        /// separate from Browse flicker so pre-exposure cannot become the EEG
        /// trial time zero.
        /// </summary>
        public void BeginFormalStimulusEpoch()
        {
            if (!m_IsInitialized)
                return;

            m_CommonStartFrame = Time.frameCount;
            m_LastRefreshRateAttemptFrame = m_CommonStartFrame - RefreshRateRetryIntervalFrames;
            m_FormalStimulusActive = true;
            m_FormalStimulusEpochCount++;
            foreach (TargetConfiguration target in m_Targets)
                target.SetCandidateActive(target.IsCandidateActive);
        }

        public void EndFormalStimulusEpoch()
        {
            m_FormalStimulusActive = false;
        }

        /// <summary>
        /// Shows a non-flickering black/white browse preview. This is not a
        /// formal SSVEP candidate: selection_open must call SetSlotCandidateActive
        /// before any EEG trial can use the slot.
        /// </summary>
        public void SetSlotStaticPreview(int slotIndex, bool white)
        {
            if (!m_IsInitialized || slotIndex < 0 || slotIndex >= m_Targets.Length)
                return;

            m_Targets[slotIndex].SetStaticPreview(white);
            m_Targets[slotIndex].ApplyState(white);
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
