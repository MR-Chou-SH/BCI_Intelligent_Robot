using System;
using UnityEngine;
using UnityEngine.UI;
using BCIIntelligentRobot.VRStimulus;
using BCIIntelligentRobot.Vision;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Separate M19 Research Acquisition screen. It has three continuously
    /// frame-driven black/white SSVEP targets and an intentional gaze Trigger; it
    /// does not create the Demo queue, selected-target state, or robot batch.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class BciM19ResearchAcquisitionController : MonoBehaviour
    {
        private const float TriggerDwellSeconds = 1.5f;
        private const float TriggerResponseTimeoutSeconds = 30f;
        private static readonly float[] FrequenciesHz = { 7.2f, 9f, 12f };
        private static readonly string[] TargetLabels = { "YELLOW", "BLUE", "GREEN" };

        private BciSelectionTransportClient m_transport;
        private MultiTargetStimulusController m_stimulusController;
        private BciGazeDwellState m_gazeDwell = new BciGazeDwellState(TriggerDwellSeconds);
        private Transform m_hudRoot;
        private Transform m_stimulusRoot;
        private GameObject m_reticleObject;
        private Renderer m_reticleRenderer;
        private Material m_reticleMaterial;
        private Collider m_triggerCollider;
        private Collider m_cancelCollider;
        private Text m_triggerText;
        private Text m_cancelText;
        private BciSelectionTransportMessage m_offer;
        private string m_sessionId;
        private string m_pausedBlockId;
        private float m_triggerPublishedAt;
        private bool m_triggerPending;
        private bool m_triggerRetryRequired;
        private bool m_isPaused;
        private bool m_sessionComplete;
        private bool m_waitingForTriggerGazeExit;
        private bool m_initialized;

        public bool IsInitialized => m_initialized;
        public bool IsWaitingForTrigger => m_offer != null && !m_triggerPending;
        public bool IsPausedForBlockBreak => m_isPaused;
        public bool IsContinuousFlickerActive => m_stimulusController != null && m_stimulusController.IsVisualFlickerEnabled;
        public int TargetCount => m_stimulusController == null ? 0 : m_stimulusController.TargetCount;
        public float TriggerDwellSecondsValue => TriggerDwellSeconds;

        public void Initialize(BciSelectionTransportClient transport)
        {
            if (m_initialized || transport == null)
                return;

            m_transport = transport;
            m_transport.ResearchMessageReceived += OnResearchMessage;
            Camera camera = Camera.main;
            if (camera == null)
            {
                Debug.LogError("M19_RESEARCH initialization_failed reason=main_camera_missing", this);
                return;
            }

            BuildPresentation(camera);
            m_initialized = true;
            Debug.Log("M19_RESEARCH_UI_READY targets=YELLOW:7.2,BLUE:9, GREEN:12 " +
                "trigger_dwell_s=1.5 page_queue=false persistent_selection=false robot_dispatch=false " +
                "continuous_browse_flicker=true physical_refresh_verified=false", this);
            Publish("m19_research_ready");
        }

        private void BuildPresentation(Camera camera)
        {
            Vector3 forward = camera.transform.forward.normalized;
            Vector3 panelPosition = camera.transform.position + forward * 1.25f + Vector3.down * 0.02f;
            Quaternion panelRotation = Quaternion.LookRotation(forward, Vector3.up);

            GameObject canvasObject = new GameObject("M19_ResearchAcquisitionHud");
            m_hudRoot = canvasObject.transform;
            canvasObject.transform.SetPositionAndRotation(panelPosition, panelRotation);
            Canvas canvas = canvasObject.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.worldCamera = camera;
            canvas.sortingOrder = 120;
            canvasObject.AddComponent<GraphicRaycaster>();
            canvasObject.transform.localScale = Vector3.one * 0.0013f;
            RectTransform canvasRect = canvasObject.GetComponent<RectTransform>();
            canvasRect.sizeDelta = new Vector2(900f, 420f);

            m_stimulusRoot = new GameObject("M19_ResearchViewLockedStimuli").transform;
            m_stimulusRoot.SetParent(camera.transform, false);
            m_stimulusRoot.localPosition = Vector3.zero;
            m_stimulusRoot.localRotation = Quaternion.identity;
            Vector3[] slotPositions = new Vector3[3];
            BciSsvepDisplayLayout.CalculateViewLockedPositions(
                BciSsvepDisplayLayout.DefaultHudLocalCenter,
                BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                slotPositions);
            var renderers = new Renderer[3];
            for (int slot = 0; slot < 3; slot++)
            {
                GameObject target = GameObject.CreatePrimitive(PrimitiveType.Quad);
                target.name = "M19_ResearchStimulus_Slot_" + slot;
                target.transform.SetParent(m_stimulusRoot, false);
                target.transform.localPosition = slotPositions[slot];
                target.transform.localScale = Vector3.one * BciSsvepDisplayLayout.HudStimulusSizeMeters;
                Collider targetCollider = target.GetComponent<Collider>();
                if (targetCollider != null)
                {
                    if (Application.isPlaying)
                        Destroy(targetCollider);
                    else
                        DestroyImmediate(targetCollider);
                }
                renderers[slot] = target.GetComponent<Renderer>();
                Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Sprites/Default") ?? Shader.Find("Standard");
                if (shader == null)
                    throw new InvalidOperationException("M19 Research requires a color shader for its fixed stimuli.");
                renderers[slot].sharedMaterial = new Material(shader);
            }

            GameObject stimulusControllerObject = new GameObject("M19_ResearchFrameDrivenStimulusController");
            stimulusControllerObject.transform.SetParent(m_stimulusRoot, false);
            m_stimulusController = stimulusControllerObject.AddComponent<MultiTargetStimulusController>();
            m_stimulusController.ConfigureRuntimeTargets(renderers);
            for (int slot = 0; slot < 3; slot++)
            {
                m_stimulusController.SetSlotVisible(slot, true);
                m_stimulusController.SetSlotBrowseFlicker(slot, true);
            }
            stimulusControllerObject.AddComponent<MultiTargetTimingDiagnostics>();

            CreateResearchReticle(camera);
            CreateButton(canvasRect, "M19_ResearchTrigger", "GAZE HERE FOR 1.5 s TO TRIGGER", new Vector2(0f, -115f), 620f, 58f, out m_triggerCollider, out m_triggerText);
            CreateButton(canvasRect, "M19_ResearchCancel", "CANCEL BEFORE TRIGGER", new Vector2(0f, -180f), 360f, 42f, out m_cancelCollider, out m_cancelText);
            m_cancelText.color = new Color(1f, 0.68f, 0.42f, 1f);
        }

        private void CreateResearchReticle(Camera camera)
        {
            m_reticleObject = GameObject.CreatePrimitive(PrimitiveType.Quad);
            m_reticleObject.name = "M19_ResearchGazeReticle";
            m_reticleObject.transform.SetParent(camera.transform, false);
            m_reticleObject.transform.localPosition = new Vector3(0f, 0f, 0.55f);
            m_reticleObject.transform.localRotation = Quaternion.identity;
            m_reticleObject.transform.localScale = Vector3.one * 0.006f;

            Collider collider = m_reticleObject.GetComponent<Collider>();
            if (collider != null)
            {
                if (Application.isPlaying)
                    Destroy(collider);
                else
                    DestroyImmediate(collider);
            }

            m_reticleRenderer = m_reticleObject.GetComponent<Renderer>();
            Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Sprites/Default") ?? Shader.Find("Standard");
            if (shader == null)
                throw new InvalidOperationException("M19 Research requires a color shader for its gaze reticle.");
            m_reticleMaterial = new Material(shader);
            m_reticleRenderer.sharedMaterial = m_reticleMaterial;
            SetReticle(0f);
            RefreshReticleVisibility();
        }

        private void SetReticle(float dwellProgress)
        {
            if (m_reticleObject == null || m_reticleMaterial == null)
                return;

            float progress = Mathf.Clamp01(dwellProgress);
            float size = 0.006f + progress * 0.002f;
            m_reticleObject.transform.localScale = Vector3.one * size;
            m_reticleMaterial.color = progress > 0f
                ? new Color(1f, 0.82f, 0.18f, 1f)
                : Color.cyan;
        }

        private void RefreshReticleVisibility()
        {
            if (m_reticleRenderer != null)
                m_reticleRenderer.enabled = !m_triggerPending && !m_sessionComplete;
        }

        private static Text CreateText(RectTransform parent, string name, string value, Vector2 position, Vector2 size, int fontSize, bool bold)
        {
            GameObject item = new GameObject(name);
            item.transform.SetParent(parent, false);
            RectTransform rect = item.AddComponent<RectTransform>();
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
            Text text = item.AddComponent<Text>();
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.text = value;
            text.fontSize = fontSize;
            text.fontStyle = bold ? FontStyle.Bold : FontStyle.Normal;
            text.alignment = TextAnchor.MiddleCenter;
            text.color = Color.white;
            return text;
        }

        private static void CreateButton(RectTransform parent, string name, string value, Vector2 position, float width, float height, out Collider collider, out Text text)
        {
            GameObject buttonObject = new GameObject(name);
            buttonObject.transform.SetParent(parent, false);
            RectTransform rect = buttonObject.AddComponent<RectTransform>();
            rect.anchoredPosition = position;
            rect.sizeDelta = new Vector2(width, height);
            Image image = buttonObject.AddComponent<Image>();
            image.color = new Color(0.02f, 0.24f, 0.33f, 0.96f);
            buttonObject.AddComponent<Button>();
            BoxCollider box = buttonObject.AddComponent<BoxCollider>();
            box.center = Vector3.zero;
            box.size = new Vector3(width, height, 12f);
            collider = box;
            text = CreateText(rect, name + "_Text", value, Vector2.zero, Vector2.zero, 22, true);
            text.rectTransform.anchorMin = Vector2.zero;
            text.rectTransform.anchorMax = Vector2.one;
            text.rectTransform.offsetMin = Vector2.zero;
            text.rectTransform.offsetMax = Vector2.zero;
        }

        private void Update()
        {
            if (!m_initialized)
                return;
            ProcessGaze(Time.unscaledTime);
            if (m_triggerPending && Time.unscaledTime - m_triggerPublishedAt >= TriggerResponseTimeoutSeconds)
            {
                m_triggerPending = false;
                m_triggerRetryRequired = true;
                m_waitingForTriggerGazeExit = true;
                Debug.LogWarning("M19_RESEARCH response_timeout trial_id=" + (m_offer == null ? "" : m_offer.trialId) +
                    " timeout_seconds=" + TriggerResponseTimeoutSeconds.ToString("F1") + " action=retry_same_attempt", this);
            }
            RefreshButtons();
            RefreshReticleVisibility();
        }

        private void ProcessGaze(float now)
        {
            Camera camera = Camera.main;
            if (camera == null)
            {
                m_gazeDwell.Reset();
                SetReticle(0f);
                return;
            }
            RaycastHit[] hits = Physics.RaycastAll(new Ray(camera.transform.position, camera.transform.forward), 20f, ~0, QueryTriggerInteraction.Collide);
            Collider hit = null;
            float nearest = float.PositiveInfinity;
            for (int index = 0; index < hits.Length; index++)
            {
                Collider candidate = hits[index].collider;
                if ((candidate == m_triggerCollider || candidate == m_cancelCollider) && hits[index].distance < nearest)
                {
                    hit = candidate;
                    nearest = hits[index].distance;
                }
            }
            string controlId = hit == m_triggerCollider ? "research_trigger" : hit == m_cancelCollider ? "research_cancel" : null;
            if (m_waitingForTriggerGazeExit)
            {
                if (hit == m_triggerCollider)
                {
                    m_gazeDwell.Reset();
                    SetReticle(0f);
                    return;
                }
                m_waitingForTriggerGazeExit = false;
                m_gazeDwell.Reset();
            }
            bool enabled = hit == m_triggerCollider
                ? (m_offer != null && !m_triggerPending && !m_sessionComplete)
                : hit == m_cancelCollider && m_offer != null && !m_triggerPending;
            bool activated = m_gazeDwell.Update(controlId, enabled, now);
            SetReticle(enabled ? m_gazeDwell.Progress(now) : 0f);
            if (!enabled)
                return;
            if (activated)
            {
                if (hit == m_triggerCollider)
                    ActivateTrigger();
                else
                    CancelBeforeTrigger("participant_gaze_cancel");
            }
        }

        private void ActivateTrigger()
        {
            if (m_isPaused)
            {
                Publish("m19_research_resume", null, null);
                m_isPaused = false;
                return;
            }
            if (m_offer == null || m_triggerPending || m_sessionComplete)
                return;
            if (!Publish("m19_research_trigger", m_offer, null))
                return;
            m_triggerPending = true;
            m_triggerRetryRequired = false;
            m_waitingForTriggerGazeExit = true;
            m_triggerPublishedAt = Time.unscaledTime;
            if (m_triggerText != null)
                m_triggerText.text = "TRIGGER SENT — CAPTURE IN PROGRESS";
            RefreshReticleVisibility();
        }

        private void CancelBeforeTrigger(string reason)
        {
            if (m_offer == null || m_triggerPending)
                return;
            BciSelectionTransportMessage offer = m_offer;
            Publish("m19_research_cancel", offer, reason);
            ClearOffer();
            Publish("m19_research_ready");
        }

        private void OnResearchMessage(BciSelectionTransportMessage message)
        {
            if (message == null)
                return;
            if (message.messageType == "m19_research_offer")
            {
                if (message.slotIndex < 0 || message.slotIndex > 2 ||
                    Mathf.Abs(message.frequencyHz - FrequenciesHz[message.slotIndex]) > 0.01f ||
                    !string.Equals(message.targetLabel, TargetLabels[message.slotIndex], StringComparison.Ordinal) ||
                    string.IsNullOrWhiteSpace(message.sessionId) || string.IsNullOrWhiteSpace(message.trialId) ||
                    string.IsNullOrWhiteSpace(message.attemptId) || m_offer != null || m_triggerPending)
                {
                    Debug.LogWarning("M19_RESEARCH offer_rejected reason=invalid_or_overlapping_offer", this);
                    return;
                }
                m_sessionId = message.sessionId;
                m_offer = message;
                m_isPaused = false;
                m_sessionComplete = false;
                m_triggerPending = false;
                m_triggerRetryRequired = false;
                m_triggerText.text = "GAZE HERE FOR 1.5 s TO TRIGGER";
                Publish("m19_research_offer_ack", message, null);
                Debug.Log("M19_RESEARCH offer_ready trial_id=" + message.trialId +
                    " slot=" + message.slotIndex + " frequency_hz=" + message.frequencyHz.ToString("F1") +
                    " cue_beeps=" + message.cueBeepCount + " condition_visible=false", this);
            }
            else if (message.messageType == "m19_research_block_pause")
            {
                if (!string.Equals(message.sessionId, m_sessionId, StringComparison.Ordinal))
                    return;
                ClearOffer();
                m_isPaused = true;
                m_pausedBlockId = message.blockId;
                m_triggerText.text = "GAZE HERE TO CONTINUE AFTER YOUR BREAK";
            }
            else if (message.messageType == "m19_research_trial_complete")
            {
                if (m_offer == null || !MatchesCurrentOffer(message))
                {
                    Debug.LogWarning("M19_RESEARCH stale_trial_complete_rejected trial_id=" + (message.trialId ?? ""), this);
                    return;
                }
                string status = string.IsNullOrWhiteSpace(message.status) ? "completed" : message.status;
                ClearOffer();
                m_triggerPending = false;
                m_triggerText.text = "TRIAL " + status.ToUpperInvariant() + " — WAITING FOR NEXT CUE";
                Publish("m19_research_ready");
            }
            else if (message.messageType == "m19_research_session_complete")
            {
                if (!string.Equals(message.sessionId, m_sessionId, StringComparison.Ordinal))
                    return;
                ClearOffer();
                m_isPaused = false;
                m_sessionComplete = true;
                m_triggerText.text = "SESSION COMPLETE";
                RefreshReticleVisibility();
                Debug.Log("M19_RESEARCH session_complete session_id=" + m_sessionId, this);
            }
        }

        private bool MatchesCurrentOffer(BciSelectionTransportMessage message)
        {
            return message != null && m_offer != null &&
                string.Equals(message.sessionId, m_offer.sessionId, StringComparison.Ordinal) &&
                string.Equals(message.trialId, m_offer.trialId, StringComparison.Ordinal) &&
                string.Equals(message.attemptId, m_offer.attemptId, StringComparison.Ordinal);
        }

        private bool Publish(string messageType, BciSelectionTransportMessage offer = null, string reason = null)
        {
            if (m_transport == null)
                return false;
            var message = new BciSelectionTransportMessage
            {
                protocolVersion = BciSelectionTransportMessage.ProtocolVersion,
                messageType = messageType,
                sessionId = offer == null ? m_sessionId : offer.sessionId,
                trialId = offer == null ? null : offer.trialId,
                attemptId = offer == null ? null : offer.attemptId,
                blockId = offer == null ? m_pausedBlockId : offer.blockId,
                ordinal = offer == null ? -1 : offer.ordinal,
                slotIndex = offer == null ? -1 : offer.slotIndex,
                frequencyHz = offer == null ? 0f : offer.frequencyHz,
                targetLabel = offer == null ? null : offer.targetLabel,
                cueBeepCount = offer == null ? 0 : offer.cueBeepCount,
                accepted = messageType == "m19_research_offer_ack",
                reason = reason
            };
            return m_transport.PublishResearchMessage(message);
        }

        private void ClearOffer()
        {
            m_offer = null;
            m_triggerPending = false;
            m_triggerRetryRequired = false;
        }

        private void RefreshButtons()
        {
            if (m_triggerText == null || m_triggerPending || m_sessionComplete)
                return;
            if (m_isPaused)
                m_triggerText.text = "GAZE HERE TO CONTINUE AFTER YOUR BREAK";
            else if (m_waitingForTriggerGazeExit)
                m_triggerText.text = "WAITING — LOOK AWAY TO RE-ARM";
            else if (m_triggerRetryRequired)
                m_triggerText.text = "LOOK AWAY, THEN GAZE TO RETRY";
            else if (m_offer != null)
                m_triggerText.text = "GAZE HERE FOR 1.5 s TO TRIGGER";
            else
                m_triggerText.text = "WAITING FOR NEXT RESEARCH CUE";
            if (m_cancelText != null)
                m_cancelText.text = m_offer != null && !m_triggerPending ? "CANCEL BEFORE TRIGGER" : "CANCEL UNAVAILABLE";
        }

        private void OnDestroy()
        {
            if (m_transport != null)
                m_transport.ResearchMessageReceived -= OnResearchMessage;
            if (m_stimulusController != null)
                m_stimulusController.EndFormalStimulusEpoch();
            DestroyOwnedObject(m_reticleMaterial);
            DestroyOwnedObject(m_reticleObject);
            DestroyStimulusRoot();
            DestroyOwnedObject(m_hudRoot == null ? null : m_hudRoot.gameObject);
        }

        private void DestroyStimulusRoot()
        {
            if (m_stimulusRoot == null)
                return;
            Renderer[] renderers = m_stimulusRoot.GetComponentsInChildren<Renderer>();
            for (int index = 0; index < renderers.Length; index++)
                DestroyOwnedObject(renderers[index].sharedMaterial);
            DestroyOwnedObject(m_stimulusRoot.gameObject);
        }

        private static void DestroyOwnedObject(UnityEngine.Object value)
        {
            if (value == null)
                return;
            if (Application.isPlaying)
                Destroy(value);
            else
                DestroyImmediate(value);
        }
    }
}
