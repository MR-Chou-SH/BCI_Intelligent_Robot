using System;
using System.Collections.Generic;
using BCIIntelligentRobot.VRStimulus;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
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

        private readonly BciTargetSlotAllocator m_slotAllocator = new BciTargetSlotAllocator();
        private readonly Dictionary<string, int> m_slotByTargetId = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly BciSelectionTarget[] m_selectionTargets = new BciSelectionTarget[BciTargetSlotAllocator.SlotCount];
        private readonly StableWorldAnchorSnapshot[] m_slotAnchors = new StableWorldAnchorSnapshot[BciTargetSlotAllocator.SlotCount];
        private readonly bool[] m_slotHasAnchor = new bool[BciTargetSlotAllocator.SlotCount];
        private readonly Vector3[] m_displayPositions = new Vector3[BciTargetSlotAllocator.SlotCount];
        private readonly GameObject[] m_slotObjects = new GameObject[BciTargetSlotAllocator.SlotCount];
        private readonly TextMesh[] m_slotLabels = new TextMesh[BciTargetSlotAllocator.SlotCount];
        private readonly LineRenderer[] m_slotLeaderLines = new LineRenderer[BciTargetSlotAllocator.SlotCount];
        private readonly GameObject[] m_slotTargetMarkers = new GameObject[BciTargetSlotAllocator.SlotCount];
        private readonly BciSelectionLayoutFreezeGate m_layoutFreezeGate = new BciSelectionLayoutFreezeGate();

        private DetectionManager m_detectionManager;
        private MultiTargetStimulusController m_stimulusController;
        private Transform m_contentParent;
        private Camera m_mainCamera;
        private Material m_associationMaterial;
        private float m_stimulusSizeMeters;
        private bool m_layoutDirty;
        private bool m_initialized;

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
            return new BciSelectionSnapshot(m_selectionTargets);
        }

        /// <summary>
        /// Holds the displayed slot positions and target associations still while
        /// one accepted Quest selection is awaiting its terminal message.
        /// </summary>
        public void FreezeLayout(string selectionId)
        {
            if (!m_initialized || string.IsNullOrWhiteSpace(selectionId))
                return;

            RefreshLiveLayout();
            if (m_layoutFreezeGate.Begin(selectionId))
                Debug.Log("M8_SELECTION layout_frozen selection_id=" + selectionId, this);
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
                m_layoutDirty = true;
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
        }

        private void LateUpdate()
        {
            if (!m_initialized)
                return;

            if (m_mainCamera == null)
                m_mainCamera = Camera.main;
            if (m_mainCamera == null)
                return;

            if (m_layoutDirty && !m_layoutFreezeGate.IsFrozen)
                RefreshLiveLayout();

            for (int slot = 0; slot < m_slotObjects.Length; slot++)
            {
                GameObject slotObject = m_slotObjects[slot];
                if (slotObject == null || !slotObject.activeSelf)
                    continue;

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
            }
        }

        private void OnStableWorldAnchorUpdated(StableWorldAnchorSnapshot anchor)
        {
            BciSlotUpdate update = m_slotAllocator.Update(anchor.TargetId, anchor.ClassName, anchor.State);
            switch (update.Kind)
            {
                case BciSlotUpdateKind.Assigned:
                    m_slotByTargetId[anchor.TargetId] = update.SlotIndex;
                    m_selectionTargets[update.SlotIndex] = new BciSelectionTarget(update.SlotIndex, anchor.TargetId, anchor.ClassName, anchor.State);
                    UpdateSlotAnchor(anchor, update.SlotIndex, true);
                    LogSlot(anchor, update, "assigned");
                    break;

                case BciSlotUpdateKind.Retained:
                    m_slotByTargetId[anchor.TargetId] = update.SlotIndex;
                    m_selectionTargets[update.SlotIndex] = new BciSelectionTarget(update.SlotIndex, anchor.TargetId, anchor.ClassName, anchor.State);
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
            marker.SetActive(false);
            return marker;
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

        private void UpdateLeaderLine(int slotIndex, Vector3 displayPosition, Vector3 anchorPosition)
        {
            LineRenderer line = m_slotLeaderLines[slotIndex];
            if (line == null)
                return;

            line.SetPosition(
                0,
                BciSsvepDisplayLayout.CalculateLeaderLineStart(
                    displayPosition,
                    anchorPosition,
                    m_stimulusSizeMeters));
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
