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
        private const float VerticalOffsetMeters = 0.18f;
        private const float TargetSizeMeters = 0.16f;
        private const string StimulusMaterialResourcePath = "BCI/SSVEP/SSVEP_Unlit";

        private readonly BciTargetSlotAllocator m_slotAllocator = new BciTargetSlotAllocator();
        private readonly Dictionary<string, int> m_slotByTargetId = new Dictionary<string, int>(StringComparer.Ordinal);
        private readonly GameObject[] m_slotObjects = new GameObject[BciTargetSlotAllocator.SlotCount];
        private readonly TextMesh[] m_slotLabels = new TextMesh[BciTargetSlotAllocator.SlotCount];

        private DetectionManager m_detectionManager;
        private MultiTargetStimulusController m_stimulusController;
        private Transform m_contentParent;
        private Camera m_mainCamera;
        private bool m_initialized;

        public void Initialize(DetectionManager detectionManager, Transform contentParent)
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
            CreateStimulusSlots();
            m_detectionManager.StableWorldAnchorUpdated += OnStableWorldAnchorUpdated;
            m_initialized = true;
            Debug.Log("M7_BCI_SLOT binding initialized slots=3 frames_per_half_cycle=5,4,3", this);
        }

        private void OnDestroy()
        {
            if (m_detectionManager != null)
                m_detectionManager.StableWorldAnchorUpdated -= OnStableWorldAnchorUpdated;
        }

        private void LateUpdate()
        {
            if (!m_initialized)
                return;

            if (m_mainCamera == null)
                m_mainCamera = Camera.main;
            if (m_mainCamera == null)
                return;

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
                    SetSlot(anchor, update.SlotIndex, true);
                    LogSlot(anchor, update, "assigned");
                    break;

                case BciSlotUpdateKind.Retained:
                    m_slotByTargetId[anchor.TargetId] = update.SlotIndex;
                    SetSlot(anchor, update.SlotIndex, true);
                    break;

                case BciSlotUpdateKind.Released:
                    m_slotByTargetId.Remove(anchor.TargetId);
                    SetSlotVisible(update.SlotIndex, false);
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
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                GameObject target = GameObject.CreatePrimitive(PrimitiveType.Quad);
                target.name = "M7_BCI_SSVEP_Slot_" + slot;
                target.transform.SetParent(m_contentParent, false);
                target.transform.localScale = Vector3.one * TargetSizeMeters;
                target.SetActive(false);
                m_slotObjects[slot] = target;
                renderers[slot] = target.GetComponent<Renderer>();
                m_slotLabels[slot] = CreateLabel(target.transform);
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

        private void SetSlot(StableWorldAnchorSnapshot anchor, int slotIndex, bool visible)
        {
            GameObject slotObject = m_slotObjects[slotIndex];
            slotObject.transform.position = anchor.WorldPosition + Vector3.up * VerticalOffsetMeters;
            m_slotLabels[slotIndex].text =
                anchor.TargetId + "\n" + anchor.ClassName +
                "\nslot " + slotIndex + " / " + NominalFrequenciesHz[slotIndex].ToString("0.#") + " Hz";
            SetSlotVisible(slotIndex, visible);
        }

        private void SetSlotVisible(int slotIndex, bool visible)
        {
            if (slotIndex < 0 || slotIndex >= m_slotObjects.Length)
                return;

            m_stimulusController.SetSlotVisible(slotIndex, visible);
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
