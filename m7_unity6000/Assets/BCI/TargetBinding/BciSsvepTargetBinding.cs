using System;
using System.Collections.Generic;
using BCIIntelligentRobot.VRStimulus;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
    public enum BciCandidateVisualState
    {
        Inactive,
        Available,
        Selected,
        Submitted
    }

    /// <summary>
    /// Binds at most three stable world anchors to the verified, frame-driven
    /// three-frequency SSVEP controller. It owns no detector or raycast logic.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class BciSsvepTargetBinding : MonoBehaviour
    {
        private static readonly float[] NominalFrequenciesHz = { 7.2f, 9f, 12f };
        private const string StimulusMaterialResourcePath = "BCI/SSVEP/SSVEP_Unlit";
        private const float TargetMarkerSizeMeters = 0.035f;
        private const float LeaderLineWidthMeters = 0.006f;
        private const float CandidateIndicatorSizeMeters = 0.20f;
        private const float CandidateIndicatorWidthMeters = 0.006f;
        private const float CandidateIndicatorTowardCameraOffsetMeters = 0.012f;
        private static readonly Color DefaultAssociationColor = new Color(0.1f, 0.75f, 0.95f, 0.75f);
        private static readonly Color GroupAvailableColor = new Color(0.15f, 0.95f, 0.25f, 1f);
        private static readonly Color GroupSelectedColor = new Color(0.15f, 0.45f, 1f, 1f);
        private static readonly Color GroupInactiveColor = new Color(0.55f, 0.55f, 0.55f, 0.9f);

        private readonly BciTargetSlotAllocator m_slotAllocator = new BciTargetSlotAllocator();
        private readonly Dictionary<string, int> m_slotByTargetId = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly BciSelectionTarget[] m_selectionTargets = new BciSelectionTarget[BciTargetSlotAllocator.SlotCount];
        private readonly StableWorldAnchorSnapshot[] m_slotAnchors = new StableWorldAnchorSnapshot[BciTargetSlotAllocator.SlotCount];
        private readonly bool[] m_slotHasAnchor = new bool[BciTargetSlotAllocator.SlotCount];
        private readonly Vector3[] m_displayPositions = new Vector3[BciTargetSlotAllocator.SlotCount];
        private readonly Vector3[] m_hudLocalPositions = new Vector3[BciTargetSlotAllocator.SlotCount];
        private readonly Vector3[] m_frozenAnchorPositions = new Vector3[BciTargetSlotAllocator.SlotCount];
        private readonly bool[] m_frozenAnchorVisible = new bool[BciTargetSlotAllocator.SlotCount];
        private readonly GameObject[] m_slotObjects = new GameObject[BciTargetSlotAllocator.SlotCount];
        private readonly TextMesh[] m_slotLabels = new TextMesh[BciTargetSlotAllocator.SlotCount];
        private readonly TextMesh[] m_slotTargetLabels = new TextMesh[BciTargetSlotAllocator.SlotCount];
        private readonly LineRenderer[] m_slotLeaderLines = new LineRenderer[BciTargetSlotAllocator.SlotCount];
        private readonly GameObject[] m_slotTargetMarkers = new GameObject[BciTargetSlotAllocator.SlotCount];
        private readonly BciSelectionLayoutFreezeGate m_layoutFreezeGate = new BciSelectionLayoutFreezeGate();
        // HUD-only presentation registry. The selection snapshot remains the
        // canonical slot output; this registry lets HUD ordering be rebuilt
        // from all currently published stable anchors after deduplication.
        private readonly Dictionary<string, StableWorldAnchorSnapshot> m_hudCandidatesByTargetId =
            new Dictionary<string, StableWorldAnchorSnapshot>(StringComparer.Ordinal);
        private readonly Dictionary<string, LineRenderer> m_candidateIndicatorsByTargetId =
            new Dictionary<string, LineRenderer>(StringComparer.Ordinal);
        private readonly Dictionary<string, TextMesh> m_candidateIndicatorLabelsByTargetId =
            new Dictionary<string, TextMesh>(StringComparer.Ordinal);
        private readonly HashSet<string> m_processedTargetIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> m_submittedTargetIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly bool[] m_groupSlotSelected = new bool[BciTargetSlotAllocator.SlotCount];

        private DetectionManager m_detectionManager;
        private MultiTargetStimulusController m_stimulusController;
        private Transform m_contentParent;
        private Camera m_mainCamera;
        private Transform m_viewLockedHudRoot;
        private Material m_associationMaterial;
        private Material m_candidateIndicatorMaterial;
        private float m_stimulusSizeMeters;
        private BciSsvepLayoutMode m_layoutMode = BciSsvepLayoutMode.WorldSpaceExperimental;
        private Vector3 m_hudLocalCenter = BciSsvepDisplayLayout.DefaultHudLocalCenter;
        private float m_hudHorizontalSpacing = BciSsvepDisplayLayout.HudHorizontalSpacingMeters;
        private float m_hudStimulusSizeMeters = BciSsvepDisplayLayout.HudStimulusSizeMeters;
        private bool m_layoutDirty;
        private bool m_initialized;
        private bool m_batchGroupModeEnabled;
        private string m_activeGroupId;

        public BciSsvepLayoutMode LayoutMode => m_layoutMode;
        public bool IsBatchGroupModeEnabled => m_batchGroupModeEnabled;
        public bool HasActiveGroup => !string.IsNullOrWhiteSpace(m_activeGroupId);
        public event Action<IReadOnlyList<StableWorldAnchorSnapshot>> HudCandidatesChanged;

        public bool IsSlotActiveCandidate(int slotIndex)
        {
            return m_stimulusController != null && m_stimulusController.IsSlotCandidateActive(slotIndex);
        }

        /// <summary>
        /// Presentation-only state for an existing StableTarget candidate. It
        /// never derives identity from raw YOLO detections.
        /// </summary>
        public BciCandidateVisualState GetCandidateVisualState(string targetId)
        {
            if (!m_batchGroupModeEnabled || string.IsNullOrWhiteSpace(targetId) ||
                !m_hudCandidatesByTargetId.ContainsKey(targetId))
                return BciCandidateVisualState.Inactive;

            if (m_submittedTargetIds.Contains(targetId))
                return BciCandidateVisualState.Submitted;

            if (!HasActiveGroup || !m_slotByTargetId.TryGetValue(targetId, out int slotIndex))
                return BciCandidateVisualState.Inactive;

            return m_groupSlotSelected[slotIndex]
                ? BciCandidateVisualState.Selected
                : BciCandidateVisualState.Available;
        }

        public void ConfigureLayout(
            BciSsvepLayoutMode layoutMode,
            Vector3 hudLocalCenter,
            float hudHorizontalSpacing,
            float hudStimulusSizeMeters)
        {
            if (m_initialized)
                return;

            m_layoutMode = layoutMode;
            m_hudLocalCenter = hudLocalCenter;
            m_hudHorizontalSpacing = Mathf.Max(0f, hudHorizontalSpacing);
            m_hudStimulusSizeMeters = Mathf.Max(0.1f, hudStimulusSizeMeters);
        }

        public void Initialize(DetectionManager detectionManager, Transform contentParent)
        {
            Initialize(detectionManager, contentParent, BciSsvepDisplayLayout.ExperimentalStimulusSizeMeters);
        }

        public void Initialize(DetectionManager detectionManager, Transform contentParent, float stimulusSizeMeters)
        {
            if (m_initialized)
                return;
            if (detectionManager == null || contentParent == null)
            {
                Debug.LogWarning("M7_BCI_SLOT binding initialization skipped because detection manager or content parent is unavailable.", this);
                return;
            }

            m_detectionManager = detectionManager;
            m_contentParent = contentParent;
            m_stimulusSizeMeters = Mathf.Max(0.1f, stimulusSizeMeters);
            CreateStimulusSlots();
            m_detectionManager.StableWorldAnchorUpdated += OnStableWorldAnchorUpdated;
            m_initialized = true;
            Debug.Log("M7_BCI_SLOT binding initialized slots=3 frames_per_half_cycle=5,4,3 stimulus_size_m=" +
                m_stimulusSizeMeters.ToString("0.##"), this);
        }

        public BciSelectionSnapshot CreateSelectionSnapshot()
        {
            // HUD assignment is normally refreshed in LateUpdate. Snapshot it
            // synchronously here so selection_open cannot freeze a slot view
            // older than the visible HUD association.
            if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud && !m_layoutFreezeGate.IsFrozen)
                RefreshHudAssignments(true);
            if (!m_batchGroupModeEnabled || !HasActiveGroup)
                return new BciSelectionSnapshot(m_selectionTargets);

            var snapshotTargets = new BciSelectionTarget[BciTargetSlotAllocator.SlotCount];
            for (int slot = 0; slot < snapshotTargets.Length; slot++)
            {
                BciSelectionTarget target = m_selectionTargets[slot];
                snapshotTargets[slot] = m_groupSlotSelected[slot]
                    ? target.WithState(StableTargetState.TemporarilyMissing)
                    : target;
            }
            return new BciSelectionSnapshot(snapshotTargets);
        }

        /// <summary>
        /// Enables the outer M8.4 lifecycle. It is HUD-only because the HUD
        /// already retains the complete stable-anchor candidate pool.
        /// </summary>
        public bool EnableBatchGroupMode()
        {
            if (!m_initialized || m_layoutMode != BciSsvepLayoutMode.ViewLockedHud)
                return false;

            m_batchGroupModeEnabled = true;
            ClearActiveGroupPresentation();
            RefreshHudAssignments(true);
            return true;
        }

        public void DisableBatchGroupMode()
        {
            if (!m_batchGroupModeEnabled)
                return;

            m_batchGroupModeEnabled = false;
            m_activeGroupId = null;
            m_processedTargetIds.Clear();
            m_submittedTargetIds.Clear();
            Array.Clear(m_groupSlotSelected, 0, m_groupSlotSelected.Length);
            HideCandidateIndicators();
            RefreshHudAssignments(true);
        }

        /// <summary>Applies one already ordered, frozen group to slots 0/1/2.</summary>
        public bool ActivateGroup(string groupId, IReadOnlyList<StableWorldAnchorSnapshot> targets)
        {
            if (!m_batchGroupModeEnabled || string.IsNullOrWhiteSpace(groupId) ||
                targets == null || targets.Count == 0 || targets.Count > BciTargetSlotAllocator.SlotCount ||
                m_layoutFreezeGate.IsFrozen)
                return false;

            m_activeGroupId = groupId;
            Array.Clear(m_groupSlotSelected, 0, m_groupSlotSelected.Length);
            m_slotByTargetId.Clear();
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                if (slot < targets.Count)
                {
                    StableWorldAnchorSnapshot anchor = targets[slot];
                    m_slotByTargetId[anchor.TargetId] = slot;
                    m_selectionTargets[slot] = new BciSelectionTarget(slot, anchor);
                    m_slotAnchors[slot] = anchor;
                    m_slotHasAnchor[slot] = true;
                    SetSlotCandidateActive(slot, true);
                    ApplySlotAssociationColor(slot, GroupAvailableColor);
                    LogHudAssignment(anchor, slot);
                }
                else
                {
                    m_selectionTargets[slot] = default(BciSelectionTarget);
                    m_slotAnchors[slot] = default(StableWorldAnchorSnapshot);
                    m_slotHasAnchor[slot] = false;
                    SetSlotCandidateActive(slot, false);
                }
            }
            m_layoutDirty = true;
            RefreshLiveLayout();
            RefreshCandidateIndicators(BuildOrderedHudCandidates());
            Debug.Log("M8_GROUP activated group_id=" + groupId + " targets=" + targets.Count, this);
            return true;
        }

        public bool SetGroupSlotSelected(string groupId, int slotIndex, bool selected)
        {
            if (!HasActiveGroup || !string.Equals(m_activeGroupId, groupId, StringComparison.Ordinal) ||
                slotIndex < 0 || slotIndex >= BciTargetSlotAllocator.SlotCount || !m_slotHasAnchor[slotIndex])
                return false;

            m_groupSlotSelected[slotIndex] = selected;
            SetSlotCandidateActive(slotIndex, !selected);
            ApplySlotAssociationColor(slotIndex, selected ? GroupSelectedColor : GroupAvailableColor);
            UpdateSlotLabel(slotIndex, m_slotAnchors[slotIndex]);
            RefreshCandidateIndicators(BuildOrderedHudCandidates());
            return true;
        }

        public bool EndActiveGroup(string groupId)
        {
            if (!HasActiveGroup || !string.Equals(m_activeGroupId, groupId, StringComparison.Ordinal) ||
                m_layoutFreezeGate.IsFrozen)
                return false;

            ClearActiveGroupPresentation();
            RefreshCandidateIndicators(BuildOrderedHudCandidates());
            Debug.Log("M8_GROUP ended group_id=" + groupId, this);
            return true;
        }

        public void SetProcessedTargetIds(
            IEnumerable<string> processedTargetIds,
            IEnumerable<string> submittedTargetIds)
        {
            m_processedTargetIds.Clear();
            m_submittedTargetIds.Clear();
            AddTargetIds(m_processedTargetIds, processedTargetIds);
            AddTargetIds(m_submittedTargetIds, submittedTargetIds);
            RefreshCandidateIndicators(BuildOrderedHudCandidates());
        }

        /// <summary>
        /// Holds the displayed slot positions and target associations still while
        /// one accepted Quest selection is awaiting its terminal message.
        /// </summary>
        public void FreezeLayout(string selectionId)
        {
            if (!m_initialized || string.IsNullOrWhiteSpace(selectionId))
                return;

            if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud && !m_layoutFreezeGate.IsFrozen)
                RefreshHudAssignments(true);
            RefreshLiveLayout();
            bool wasFrozen = m_layoutFreezeGate.IsFrozen;
            if (m_layoutFreezeGate.Begin(selectionId))
            {
                if (!wasFrozen)
                {
                    for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
                    {
                        m_frozenAnchorVisible[slot] = m_slotHasAnchor[slot];
                        if (m_frozenAnchorVisible[slot])
                            m_frozenAnchorPositions[slot] = m_slotAnchors[slot].WorldPosition;
                    }
                }
                Debug.Log("M8_SELECTION layout_frozen selection_id=" + selectionId, this);
            }
        }

        /// <summary>
        /// Applies any stable-target updates accumulated during a completed or
        /// explicitly aborted selection.
        /// </summary>
        public void ReleaseLayout(string selectionId)
        {
            if (!m_layoutFreezeGate.End(selectionId))
                return;

            if (!m_layoutFreezeGate.IsFrozen)
            {
                Array.Clear(m_frozenAnchorVisible, 0, m_frozenAnchorVisible.Length);
                m_layoutDirty = true;
                if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud)
                    RefreshHudAssignments(true);
                else
                    RefreshLiveLayout();
                Debug.Log("M8_SELECTION layout_released selection_id=" + selectionId, this);
            }
        }

        private void OnDestroy()
        {
            if (m_detectionManager != null)
                m_detectionManager.StableWorldAnchorUpdated -= OnStableWorldAnchorUpdated;
            if (m_associationMaterial != null)
                Destroy(m_associationMaterial);
            if (m_candidateIndicatorMaterial != null)
                Destroy(m_candidateIndicatorMaterial);
            if (m_viewLockedHudRoot != null)
                Destroy(m_viewLockedHudRoot.gameObject);
            foreach (LineRenderer indicator in m_candidateIndicatorsByTargetId.Values)
            {
                if (indicator != null)
                    Destroy(indicator.gameObject);
            }
        }

        private void LateUpdate()
        {
            if (!m_initialized)
                return;

            if (m_mainCamera == null)
                m_mainCamera = Camera.main;
            if (m_mainCamera == null)
                return;

            if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud && !m_layoutFreezeGate.IsFrozen)
                RefreshHudAssignments(false);

            if (m_layoutDirty && !m_layoutFreezeGate.IsFrozen)
                RefreshLiveLayout();

            if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud)
                UpdateViewLockedHudPresentation();

            for (int slot = 0; slot < m_slotObjects.Length; slot++)
            {
                GameObject slotObject = m_slotObjects[slot];
                if (slotObject == null || !slotObject.activeSelf)
                    continue;

                if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud)
                {
                    slotObject.transform.localRotation = Quaternion.identity;
                }

                Vector3 cameraDirection = m_mainCamera.transform.position - slotObject.transform.position;
                if (cameraDirection.sqrMagnitude > Mathf.Epsilon)
                {
                    // Unity Quad geometry uses a +Z front normal. The verified
                    // Quest orientation is the opposite of the camera-facing
                    // direction used by TextMesh, so face the Quad with -Z
                    // toward the HMD and keep labels explicitly camera-facing.
                    slotObject.transform.rotation = Quaternion.LookRotation(-cameraDirection.normalized);
                    if (m_slotLabels[slot] != null)
                        m_slotLabels[slot].transform.rotation = Quaternion.LookRotation(cameraDirection.normalized);
                }

                if (m_slotTargetLabels[slot] != null && m_slotTargetMarkers[slot] != null)
                {
                    Vector3 markerDirection = m_mainCamera.transform.position - m_slotTargetMarkers[slot].transform.position;
                    if (markerDirection.sqrMagnitude > Mathf.Epsilon)
                        m_slotTargetLabels[slot].transform.rotation = Quaternion.LookRotation(markerDirection.normalized);
                }
            }
        }

        private void OnStableWorldAnchorUpdated(StableWorldAnchorSnapshot anchor)
        {
            if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud)
            {
                if (anchor.State == StableTargetState.Lost)
                    m_hudCandidatesByTargetId.Remove(anchor.TargetId);
                else
                    m_hudCandidatesByTargetId[anchor.TargetId] = anchor;

                // Keep receiving live metadata while a selection or group is
                // frozen. M8.4 may use it only for a later group.
                RefreshHudAssignments(true);
                return;
            }

            BciSlotUpdate update = m_slotAllocator.Update(anchor.TargetId, anchor.ClassName, anchor.State);
            switch (update.Kind)
            {
                case BciSlotUpdateKind.Assigned:
                    m_slotByTargetId[anchor.TargetId] = update.SlotIndex;
                    m_selectionTargets[update.SlotIndex] = new BciSelectionTarget(update.SlotIndex, anchor);
                    UpdateSlotAnchor(anchor, update.SlotIndex, true);
                    LogSlot(anchor, update, "assigned");
                    break;

                case BciSlotUpdateKind.Retained:
                    m_slotByTargetId[anchor.TargetId] = update.SlotIndex;
                    m_selectionTargets[update.SlotIndex] = new BciSelectionTarget(update.SlotIndex, anchor);
                    UpdateSlotAnchor(anchor, update.SlotIndex, true);
                    break;

                case BciSlotUpdateKind.Released:
                    m_slotByTargetId.Remove(anchor.TargetId);
                    m_selectionTargets[update.SlotIndex] = default(BciSelectionTarget);
                    UpdateSlotAnchor(anchor, update.SlotIndex, false);
                    LogSlot(anchor, update, "released");
                    break;

                case BciSlotUpdateKind.Full:
                    Debug.Log("M7_BCI_SLOT target_id=" + anchor.TargetId +
                        " class=" + anchor.ClassName +
                        " event=ignored_full slots=3", this);
                    break;
            }
        }

        private void CreateStimulusSlots()
        {
            var renderers = new Renderer[BciTargetSlotAllocator.SlotCount];
            CreateAssociationMaterial();
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                GameObject target = GameObject.CreatePrimitive(PrimitiveType.Quad);
                target.name = "M7_BCI_SSVEP_Slot_" + slot;
                target.transform.SetParent(m_contentParent, false);
                target.transform.localScale = Vector3.one * m_stimulusSizeMeters;
                target.SetActive(false);
                m_slotObjects[slot] = target;
                renderers[slot] = target.GetComponent<Renderer>();
                m_slotLabels[slot] = CreateLabel(target.transform);
                m_slotLeaderLines[slot] = CreateLeaderLine(slot);
                m_slotTargetMarkers[slot] = CreateTargetMarker(slot);
            }

            Material stimulusMaterial = Resources.Load<Material>(StimulusMaterialResourcePath);
            if (stimulusMaterial == null)
            {
                Debug.LogError("M7_BCI_VISUAL missing legacy SSVEP material resource=" + StimulusMaterialResourcePath, this);
            }
            else
            {
                for (int slot = 0; slot < renderers.Length; slot++)
                    renderers[slot].sharedMaterial = stimulusMaterial;
            }

            GameObject controllerObject = new GameObject("M7_BCI_FrameDrivenStimulusController");
            controllerObject.transform.SetParent(m_contentParent, false);
            m_stimulusController = controllerObject.AddComponent<MultiTargetStimulusController>();
            m_stimulusController.ConfigureRuntimeTargets(renderers);
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
                m_stimulusController.SetSlotVisible(slot, false);

            controllerObject.AddComponent<MultiTargetTimingDiagnostics>();
        }

        private void CreateAssociationMaterial()
        {
            Shader shader = Shader.Find("Unlit/Color");
            if (shader == null)
                shader = Shader.Find("Sprites/Default");
            if (shader == null)
            {
                Debug.LogError("M7_BCI_VISUAL missing association visual shader.", this);
                return;
            }

            m_associationMaterial = new Material(shader);
            m_associationMaterial.color = new Color(0.1f, 0.75f, 0.95f, 0.75f);

            Shader indicatorShader = Shader.Find("Sprites/Default");
            if (indicatorShader == null)
                indicatorShader = shader;
            m_candidateIndicatorMaterial = new Material(indicatorShader);
            m_candidateIndicatorMaterial.color = Color.white;
        }

        private LineRenderer CreateLeaderLine(int slotIndex)
        {
            var lineObject = new GameObject("M7_BCI_LeaderLine_" + slotIndex);
            lineObject.transform.SetParent(m_contentParent, false);
            var line = lineObject.AddComponent<LineRenderer>();
            line.useWorldSpace = true;
            line.positionCount = 2;
            line.startWidth = LeaderLineWidthMeters;
            line.endWidth = LeaderLineWidthMeters;
            line.numCapVertices = 0;
            line.numCornerVertices = 0;
            line.alignment = LineAlignment.View;
            if (m_associationMaterial != null)
                line.sharedMaterial = m_associationMaterial;
            line.startColor = new Color(0.1f, 0.75f, 0.95f, 0.75f);
            line.endColor = new Color(0.1f, 0.75f, 0.95f, 0.75f);
            lineObject.SetActive(false);
            return line;
        }

        private GameObject CreateTargetMarker(int slotIndex)
        {
            GameObject marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            marker.name = "M7_BCI_TargetMarker_" + slotIndex;
            marker.transform.SetParent(m_contentParent, false);
            marker.transform.localScale = Vector3.one * TargetMarkerSizeMeters;
            Renderer markerRenderer = marker.GetComponent<Renderer>();
            if (markerRenderer != null && m_associationMaterial != null)
                markerRenderer.sharedMaterial = m_associationMaterial;
            Collider markerCollider = marker.GetComponent<Collider>();
            if (markerCollider != null)
                Destroy(markerCollider);
            m_slotTargetLabels[slotIndex] = CreateMarkerLabel(marker.transform, slotIndex);
            marker.SetActive(false);
            return marker;
        }

        private static TextMesh CreateMarkerLabel(Transform parent, int slotIndex)
        {
            var labelObject = new GameObject("MarkerLabel");
            labelObject.transform.SetParent(parent, false);
            // The label inherits the 0.035 m marker scale; compensate so the
            // number remains a small, readable world-space annotation.
            labelObject.transform.localPosition = new Vector3(0f, 0.65f, 0f);
            labelObject.transform.localScale = new Vector3(-0.35f, 0.35f, 0.35f);
            var label = labelObject.AddComponent<TextMesh>();
            label.text = (slotIndex + 1).ToString();
            label.anchor = TextAnchor.MiddleCenter;
            label.alignment = TextAlignment.Center;
            label.characterSize = 0.25f;
            label.fontSize = 48;
            label.color = Color.cyan;
            return label;
        }

        private static TextMesh CreateLabel(Transform parent)
        {
            var labelObject = new GameObject("Label");
            labelObject.transform.SetParent(parent, false);
            labelObject.transform.localPosition = new Vector3(0f, 0.65f, -0.01f);
            // TextMesh faces the opposite local X direction on the world-space target.
            // Negating only X fixes the mirror while preserving the readable vertical layout.
            labelObject.transform.localScale = new Vector3(-0.08f, 0.08f, 0.08f);
            var label = labelObject.AddComponent<TextMesh>();
            label.anchor = TextAnchor.MiddleCenter;
            label.alignment = TextAlignment.Center;
            label.characterSize = 0.25f;
            label.fontSize = 48;
            label.color = Color.cyan;
            return label;
        }

        private void UpdateSlotAnchor(StableWorldAnchorSnapshot anchor, int slotIndex, bool visible)
        {
            m_slotAnchors[slotIndex] = anchor;
            m_slotHasAnchor[slotIndex] = visible;
            m_layoutDirty = true;
            RefreshLiveLayout();
        }

        private void RefreshLiveLayout()
        {
            if (m_layoutFreezeGate.IsFrozen || !m_layoutDirty)
                return;

            if (m_mainCamera == null)
                m_mainCamera = Camera.main;
            if (m_mainCamera == null)
                return;

            if (m_layoutMode == BciSsvepLayoutMode.ViewLockedHud)
            {
                RefreshViewLockedHudLayout();
                return;
            }

            BciSsvepDisplayLayout.CalculatePositions(
                m_slotAnchors,
                m_slotHasAnchor,
                m_mainCamera.transform.position,
                m_mainCamera.transform.right,
                m_mainCamera.transform.up,
                m_displayPositions);

            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                if (!m_slotHasAnchor[slot])
                {
                    SetSlotPresentationVisible(slot, false);
                    continue;
                }

                GameObject slotObject = m_slotObjects[slot];
                slotObject.transform.position = m_displayPositions[slot];
                if (m_slotLabels[slot] != null)
                {
                    StableWorldAnchorSnapshot anchor = m_slotAnchors[slot];
                    m_slotLabels[slot].text =
                        anchor.TargetId + "\n" + anchor.ClassName +
                        "\nslot " + slot + " / " + NominalFrequenciesHz[slot].ToString("0.#") + " Hz";
                }

                GameObject marker = m_slotTargetMarkers[slot];
                if (marker != null)
                    marker.transform.position = m_slotAnchors[slot].WorldPosition;
                UpdateLeaderLine(slot, m_displayPositions[slot], m_slotAnchors[slot].WorldPosition);
                SetSlotPresentationVisible(slot, true);
            }

            m_layoutDirty = false;
        }

        private void RefreshHudAssignments(bool force)
        {
            List<StableWorldAnchorSnapshot> ordered = BuildOrderedHudCandidates();
            PublishHudCandidates(ordered);

            if (m_batchGroupModeEnabled)
            {
                if (!m_layoutFreezeGate.IsFrozen)
                    RefreshCandidateIndicators(ordered);
                return;
            }

            if (m_layoutFreezeGate.IsFrozen)
                return;

            if (ordered.Count > BciTargetSlotAllocator.SlotCount)
                ordered.RemoveRange(BciTargetSlotAllocator.SlotCount, ordered.Count - BciTargetSlotAllocator.SlotCount);

            if (!force && HasSameHudAssignment(ordered))
                return;

            m_slotByTargetId.Clear();
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                if (slot < ordered.Count)
                {
                    StableWorldAnchorSnapshot anchor = ordered[slot];
                    m_slotByTargetId[anchor.TargetId] = slot;
                    m_selectionTargets[slot] = new BciSelectionTarget(slot, anchor);
                    m_slotAnchors[slot] = anchor;
                    m_slotHasAnchor[slot] = true;
                    SetSlotCandidateActive(slot, true);
                    ApplySlotAssociationColor(slot, DefaultAssociationColor);
                    LogHudAssignment(anchor, slot);
                }
                else
                {
                    m_selectionTargets[slot] = default(BciSelectionTarget);
                    m_slotAnchors[slot] = default(StableWorldAnchorSnapshot);
                    m_slotHasAnchor[slot] = false;
                    SetSlotCandidateActive(slot, false);
                }
            }

            m_layoutDirty = true;
            RefreshLiveLayout();
        }

        private List<StableWorldAnchorSnapshot> BuildOrderedHudCandidates()
        {
            var candidates = new List<StableWorldAnchorSnapshot>(m_hudCandidatesByTargetId.Values);
            candidates.RemoveAll(candidate => candidate.State == StableTargetState.Lost);
            IReadOnlyList<StableWorldAnchorSnapshot> deduplicated = BciPhysicalTargetDeduplicator.Select(candidates);
            var ordered = new List<StableWorldAnchorSnapshot>(deduplicated);

            if (m_mainCamera == null)
                m_mainCamera = Camera.main;

            Vector3 cameraPosition = m_mainCamera != null ? m_mainCamera.transform.position : Vector3.zero;
            Vector3 cameraRight = m_mainCamera != null ? m_mainCamera.transform.right : Vector3.right;
            cameraRight = cameraRight.sqrMagnitude > Mathf.Epsilon ? cameraRight.normalized : Vector3.right;
            ordered.Sort((left, right) =>
            {
                float leftX = Vector3.Dot(left.WorldPosition - cameraPosition, cameraRight);
                float rightX = Vector3.Dot(right.WorldPosition - cameraPosition, cameraRight);
                int byPosition = leftX.CompareTo(rightX);
                return byPosition != 0
                    ? byPosition
                    : string.Compare(left.TargetId, right.TargetId, StringComparison.Ordinal);
            });
            return ordered;
        }

        private void PublishHudCandidates(IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            if (!m_batchGroupModeEnabled || HudCandidatesChanged == null)
                return;

            var copy = new StableWorldAnchorSnapshot[candidates.Count];
            for (int index = 0; index < copy.Length; index++)
                copy[index] = candidates[index];
            HudCandidatesChanged.Invoke(copy);
        }

        private void ClearActiveGroupPresentation()
        {
            m_activeGroupId = null;
            Array.Clear(m_groupSlotSelected, 0, m_groupSlotSelected.Length);
            m_slotByTargetId.Clear();
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                m_selectionTargets[slot] = default(BciSelectionTarget);
                m_slotAnchors[slot] = default(StableWorldAnchorSnapshot);
                m_slotHasAnchor[slot] = false;
                SetSlotCandidateActive(slot, false);
                SetSlotPresentationVisible(slot, false);
            }
            m_layoutDirty = false;
        }

        private void AddTargetIds(HashSet<string> destination, IEnumerable<string> source)
        {
            if (source == null)
                return;
            foreach (string targetId in source)
            {
                if (!string.IsNullOrWhiteSpace(targetId))
                    destination.Add(targetId);
            }
        }

        private void RefreshCandidateIndicators(IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            if (!m_batchGroupModeEnabled)
                return;

            if (m_mainCamera == null)
                m_mainCamera = Camera.main;
            Vector3 right = m_mainCamera != null ? m_mainCamera.transform.right : Vector3.right;
            Vector3 up = m_mainCamera != null ? m_mainCamera.transform.up : Vector3.up;
            right = right.sqrMagnitude > Mathf.Epsilon ? right.normalized : Vector3.right;
            up = up.sqrMagnitude > Mathf.Epsilon ? up.normalized : Vector3.up;

            var liveIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < candidates.Count; index++)
            {
                StableWorldAnchorSnapshot anchor = candidates[index];
                liveIds.Add(anchor.TargetId);
                LineRenderer indicator = GetOrCreateCandidateIndicator(anchor.TargetId);
                if (indicator == null)
                    continue;

                BciCandidateVisualState visualState = GetCandidateVisualState(anchor.TargetId);
                Color color = GetCandidateVisualColor(visualState);
                indicator.startColor = color;
                indicator.endColor = color;
                Vector3 center = anchor.WorldPosition;
                if (m_mainCamera != null)
                {
                    Vector3 towardCamera = m_mainCamera.transform.position - center;
                    if (towardCamera.sqrMagnitude > Mathf.Epsilon)
                        center += towardCamera.normalized * CandidateIndicatorTowardCameraOffsetMeters;
                }
                SetCandidateIndicatorRectangle(indicator, center, right, up);
                indicator.gameObject.SetActive(true);

                if (m_candidateIndicatorLabelsByTargetId.TryGetValue(anchor.TargetId, out TextMesh label) && label != null)
                {
                    label.text = visualState == BciCandidateVisualState.Submitted ? "✓" : string.Empty;
                    label.color = color;
                    label.transform.position = center + up * (CandidateIndicatorSizeMeters * 0.75f);
                    Vector3 direction = m_mainCamera != null
                        ? m_mainCamera.transform.position - label.transform.position
                        : Vector3.forward;
                    if (direction.sqrMagnitude > Mathf.Epsilon)
                        label.transform.rotation = Quaternion.LookRotation(direction.normalized);
                }
            }

            var staleIds = new List<string>();
            foreach (KeyValuePair<string, LineRenderer> entry in m_candidateIndicatorsByTargetId)
            {
                if (!liveIds.Contains(entry.Key))
                    staleIds.Add(entry.Key);
            }
            for (int index = 0; index < staleIds.Count; index++)
            {
                string targetId = staleIds[index];
                if (m_candidateIndicatorsByTargetId.TryGetValue(targetId, out LineRenderer indicator) && indicator != null)
                    Destroy(indicator.gameObject);
                m_candidateIndicatorsByTargetId.Remove(targetId);
                m_candidateIndicatorLabelsByTargetId.Remove(targetId);
            }
        }

        private LineRenderer GetOrCreateCandidateIndicator(string targetId)
        {
            if (m_candidateIndicatorsByTargetId.TryGetValue(targetId, out LineRenderer existing))
                return existing;

            var indicatorObject = new GameObject("M8_BCI_CandidateIndicator_" + targetId);
            indicatorObject.transform.SetParent(m_contentParent, false);
            var indicator = indicatorObject.AddComponent<LineRenderer>();
            indicator.useWorldSpace = true;
            indicator.positionCount = 5;
            indicator.loop = false;
            indicator.startWidth = CandidateIndicatorWidthMeters;
            indicator.endWidth = CandidateIndicatorWidthMeters;
            indicator.alignment = LineAlignment.View;
            if (m_candidateIndicatorMaterial != null)
                indicator.sharedMaterial = m_candidateIndicatorMaterial;
            m_candidateIndicatorsByTargetId.Add(targetId, indicator);

            var labelObject = new GameObject("SubmittedMarker");
            labelObject.transform.SetParent(indicatorObject.transform, false);
            var label = labelObject.AddComponent<TextMesh>();
            label.anchor = TextAnchor.MiddleCenter;
            label.alignment = TextAlignment.Center;
            label.characterSize = 0.06f;
            label.fontSize = 64;
            m_candidateIndicatorLabelsByTargetId.Add(targetId, label);
            return indicator;
        }

        private static void SetCandidateIndicatorRectangle(LineRenderer indicator, Vector3 center, Vector3 right, Vector3 up)
        {
            float half = CandidateIndicatorSizeMeters * 0.5f;
            indicator.SetPosition(0, center + right * -half + up * -half);
            indicator.SetPosition(1, center + right * half + up * -half);
            indicator.SetPosition(2, center + right * half + up * half);
            indicator.SetPosition(3, center + right * -half + up * half);
            indicator.SetPosition(4, center + right * -half + up * -half);
        }

        private static Color GetCandidateVisualColor(BciCandidateVisualState state)
        {
            switch (state)
            {
                case BciCandidateVisualState.Available:
                    return GroupAvailableColor;
                case BciCandidateVisualState.Selected:
                case BciCandidateVisualState.Submitted:
                    return GroupSelectedColor;
                default:
                    return GroupInactiveColor;
            }
        }

        private void HideCandidateIndicators()
        {
            foreach (LineRenderer indicator in m_candidateIndicatorsByTargetId.Values)
            {
                if (indicator != null)
                    indicator.gameObject.SetActive(false);
            }
        }

        private void ApplySlotAssociationColor(int slotIndex, Color color)
        {
            if (m_slotLeaderLines[slotIndex] != null)
            {
                m_slotLeaderLines[slotIndex].startColor = color;
                m_slotLeaderLines[slotIndex].endColor = color;
            }
            GameObject marker = m_slotTargetMarkers[slotIndex];
            Renderer markerRenderer = marker != null ? marker.GetComponent<Renderer>() : null;
            if (markerRenderer != null)
            {
                var block = new MaterialPropertyBlock();
                markerRenderer.GetPropertyBlock(block);
                block.SetColor("_Color", color);
                markerRenderer.SetPropertyBlock(block);
            }
            if (m_slotLabels[slotIndex] != null)
                m_slotLabels[slotIndex].color = color;
            if (m_slotTargetLabels[slotIndex] != null)
                m_slotTargetLabels[slotIndex].color = color;
        }

        private void SetSlotCandidateActive(int slotIndex, bool active)
        {
            if (m_stimulusController != null)
                m_stimulusController.SetSlotCandidateActive(slotIndex, active);
        }

        private bool HasSameHudAssignment(IReadOnlyList<StableWorldAnchorSnapshot> ordered)
        {
            if (m_slotByTargetId.Count != ordered.Count)
                return false;

            for (int slot = 0; slot < ordered.Count; slot++)
            {
                if (!m_slotByTargetId.TryGetValue(ordered[slot].TargetId, out int currentSlot) ||
                    currentSlot != slot)
                    return false;
            }

            return true;
        }

        private void RefreshViewLockedHudLayout()
        {
            EnsureViewLockedHudRoot();
            BciSsvepDisplayLayout.CalculateViewLockedPositions(
                m_hudLocalCenter,
                m_hudHorizontalSpacing,
                m_hudLocalPositions);

            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                if (!m_slotHasAnchor[slot])
                {
                    SetSlotPresentationVisible(slot, false);
                    continue;
                }

                GameObject slotObject = m_slotObjects[slot];
                slotObject.transform.localPosition = m_hudLocalPositions[slot];
                slotObject.transform.localRotation = Quaternion.identity;
                slotObject.transform.localScale = Vector3.one * m_hudStimulusSizeMeters;
                UpdateSlotLabel(slot, m_slotAnchors[slot]);

                GameObject marker = m_slotTargetMarkers[slot];
                if (marker != null)
                    marker.transform.position = m_slotAnchors[slot].WorldPosition;
                UpdateLeaderLine(slot, slotObject.transform.position, m_slotAnchors[slot].WorldPosition, m_hudStimulusSizeMeters);
                SetSlotPresentationVisible(slot, true);
            }

            m_layoutDirty = false;
        }

        private void EnsureViewLockedHudRoot()
        {
            if (m_viewLockedHudRoot != null || m_mainCamera == null)
                return;

            var rootObject = new GameObject("M7_BCI_ViewLockedHudRoot");
            m_viewLockedHudRoot = rootObject.transform;
            m_viewLockedHudRoot.SetParent(m_mainCamera.transform, false);
            m_viewLockedHudRoot.localPosition = Vector3.zero;
            m_viewLockedHudRoot.localRotation = Quaternion.identity;
            m_viewLockedHudRoot.localScale = Vector3.one;

            for (int slot = 0; slot < m_slotObjects.Length; slot++)
            {
                if (m_slotObjects[slot] == null)
                    continue;
                m_slotObjects[slot].transform.SetParent(m_viewLockedHudRoot, false);
                m_slotObjects[slot].transform.localScale = Vector3.one * m_hudStimulusSizeMeters;
            }
        }

        private void UpdateViewLockedHudPresentation()
        {
            if (m_viewLockedHudRoot == null)
                return;

            for (int slot = 0; slot < m_slotObjects.Length; slot++)
            {
                GameObject slotObject = m_slotObjects[slot];
                if (slotObject == null || !slotObject.activeSelf)
                    continue;

                Vector3 anchorPosition;
                if (m_layoutFreezeGate.IsFrozen)
                {
                    if (!m_frozenAnchorVisible[slot])
                        continue;
                    anchorPosition = m_frozenAnchorPositions[slot];
                }
                else
                {
                    if (!m_slotHasAnchor[slot])
                        continue;
                    anchorPosition = m_slotAnchors[slot].WorldPosition;
                }

                UpdateLeaderLine(slot, slotObject.transform.position, anchorPosition, m_hudStimulusSizeMeters);
            }
        }

        private void UpdateSlotLabel(int slot, StableWorldAnchorSnapshot anchor)
        {
            if (m_slotLabels[slot] == null)
                return;

            string prefix = (slot + 1).ToString() + "\n";
            m_slotLabels[slot].text = prefix + anchor.TargetId + "\n" + anchor.ClassName;
        }

        private void LogHudAssignment(StableWorldAnchorSnapshot anchor, int slot)
        {
            Debug.Log("M7_BCI_SLOT target_id=" + anchor.TargetId +
                " class=" + anchor.ClassName +
                " event=hud_spatial_assignment" +
                " slot=" + slot +
                " nominal_frequency_hz=" + NominalFrequenciesHz[slot].ToString("0.#") +
                " world_point=" + anchor.WorldPosition.ToString("F4"), this);
        }

        private void UpdateLeaderLine(int slotIndex, Vector3 displayPosition, Vector3 anchorPosition)
        {
            UpdateLeaderLine(slotIndex, displayPosition, anchorPosition, m_stimulusSizeMeters);
        }

        private void UpdateLeaderLine(int slotIndex, Vector3 displayPosition, Vector3 anchorPosition, float stimulusSizeMeters)
        {
            LineRenderer line = m_slotLeaderLines[slotIndex];
            if (line == null)
                return;

            line.SetPosition(
                0,
                BciSsvepDisplayLayout.CalculateLeaderLineStart(
                    displayPosition,
                    anchorPosition,
                    stimulusSizeMeters));
            line.SetPosition(1, anchorPosition);
        }

        private void SetSlotPresentationVisible(int slotIndex, bool visible)
        {
            if (slotIndex < 0 || slotIndex >= m_slotObjects.Length)
                return;

            if (m_stimulusController != null)
                m_stimulusController.SetSlotVisible(slotIndex, visible);
            if (m_slotLeaderLines[slotIndex] != null)
                m_slotLeaderLines[slotIndex].gameObject.SetActive(visible);
            if (m_slotTargetMarkers[slotIndex] != null)
                m_slotTargetMarkers[slotIndex].SetActive(visible);
        }

        private void LogSlot(StableWorldAnchorSnapshot anchor, BciSlotUpdate update, string eventName)
        {
            Debug.Log("M7_BCI_SLOT target_id=" + anchor.TargetId +
                " class=" + anchor.ClassName +
                " event=" + eventName +
                " slot=" + update.SlotIndex +
                " nominal_frequency_hz=" + NominalFrequenciesHz[update.SlotIndex].ToString("0.#") +
                " world_point=" + anchor.WorldPosition.ToString("F4"), this);
        }
    }
}
