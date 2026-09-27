using System.Text;
using UnityEngine;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Small world-space M16 control strip. It is placed once from stable
    /// workspace anchors and is never parented to or repositioned from the
    /// HMD camera. Ordinary Button input remains available; the dedicated
    /// head-gaze interactor is an additional input path for these controls.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class BciPagedQueueHud : MonoBehaviour
    {
        private BciPagedTargetQueueController m_controller;
        private Canvas m_canvas;
        private Text m_pageText;
        private Text m_queueText;
        private Button m_previous;
        private Button m_next;
        private Button m_undo;
        private Button m_submit;
        private Button m_trigger;
        private Text m_triggerText;
        private BciPagedGazeControl m_previousGaze;
        private BciPagedGazeControl m_nextGaze;
        private BciPagedGazeControl m_undoGaze;
        private BciPagedGazeControl m_submitGaze;
        private BciPagedGazeControl m_triggerGaze;
        private BciPagedGazeInteractor m_gazeInteractor;
        private bool m_canvasInitializationAttempted;
        private bool m_canvasInitializationCompleted;

        public Transform WorldSpaceCanvasTransform => m_canvas == null ? null : m_canvas.transform;
        public BciPagedGazeInteractor GazeInteractor => m_gazeInteractor;
        public bool IsInitialized => m_canvasInitializationCompleted && m_canvas != null;
        public int GazeControlCount =>
            (m_previousGaze == null ? 0 : 1) +
            (m_nextGaze == null ? 0 : 1) +
            (m_undoGaze == null ? 0 : 1) +
            (m_submitGaze == null ? 0 : 1) +
            (m_triggerGaze == null ? 0 : 1);
        public bool GazeControlsReady =>
            HasReadyControl(m_previousGaze) &&
            HasReadyControl(m_nextGaze) &&
            HasReadyControl(m_undoGaze) &&
            HasReadyControl(m_submitGaze) &&
            HasReadyControl(m_triggerGaze);

        public void Initialize(BciPagedTargetQueueController controller)
        {
            if (m_controller != null || controller == null)
                return;
            m_controller = controller;
            Refresh();
        }

        public void Refresh()
        {
            if (m_controller == null || m_controller.CurrentPage == null)
                return;
            if (!m_canvasInitializationCompleted)
            {
                if (m_canvasInitializationAttempted)
                    return;
                Vector3 position;
                Quaternion rotation;
                if (!m_controller.TryGetWorldSpacePanelPose(out position, out rotation))
                    return;
                m_canvasInitializationAttempted = true;
                BuildCanvas(position, rotation);
                m_canvasInitializationCompleted = true;
            }
            BciPagedPage page = m_controller.CurrentPage;
            m_pageText.text = "Page " + (page.PageIndex + 1) + " / " + page.PageCount;
            var queue = m_controller.Queue.Queue;
            var text = new StringBuilder();
            text.Append("Robot: ").Append(m_controller.RobotExecutionStatus);
            for (int index = 0; index < queue.Count; index++)
                text.Append("\n").Append(index + 1).Append(". ").Append(queue[index].Candidate.Label);
            m_queueText.text = text.ToString();
            m_previous.interactable = m_controller.Queue.NavigationEnabled && page.PageIndex > 0;
            m_next.interactable = m_controller.Queue.NavigationEnabled && page.PageIndex < page.PageCount - 1;
            m_undo.interactable = m_controller.Queue.UndoEnabled;
            m_submit.interactable = m_controller.Queue.SubmitEnabled;
            m_previousGaze.SetEnabled(m_previous.interactable);
            m_nextGaze.SetEnabled(m_next.interactable);
            m_undoGaze.SetEnabled(m_undo.interactable);
            m_submitGaze.SetEnabled(m_submit.interactable);
            m_trigger.interactable = m_controller.CanStartTriggeredTrial;
            m_triggerGaze.SetEnabled(m_trigger.interactable);
            if (m_triggerText != null)
                m_triggerText.text = m_triggerGaze.IsWaitingForGazeExit
                    ? "LOOK AWAY TO RE-ARM"
                    : "GAZE HERE TO START EEG SELECTION";
        }

        private void BuildCanvas(Vector3 position, Quaternion rotation)
        {
            GameObject canvasObject = new GameObject("M16_PagedQueueHud");
            Camera camera = Camera.main;
            canvasObject.transform.SetPositionAndRotation(position, rotation);
            m_canvas = canvasObject.AddComponent<Canvas>();
            m_canvas.renderMode = RenderMode.WorldSpace;
            m_canvas.worldCamera = camera;
            m_canvas.sortingOrder = 100;
            canvasObject.AddComponent<GraphicRaycaster>();
            canvasObject.transform.localScale = Vector3.one * 0.0015f;

            RectTransform root = canvasObject.GetComponent<RectTransform>();
            root.sizeDelta = new Vector2(900f, 320f);
            m_trigger = CreateButton(
                root,
                "M16_EegTriggerBand",
                "GAZE HERE TO START EEG SELECTION",
                "eeg_trigger",
                new Vector2(0f, 235f),
                () => m_controller.BeginUserTriggeredSelection(),
                out m_triggerGaze,
                width: 760f,
                height: 50f,
                requireExitToRearm: true,
                trackWhileDisabled: true,
                dwellSeconds: BciPagedGazeInteractor.TriggerDwellSeconds);
            m_triggerText = m_trigger.transform.Find("M16_Text_Button_eeg_trigger").GetComponent<Text>();
            Image triggerImage = m_trigger.GetComponent<Image>();
            triggerImage.color = new Color(0.02f, 0.25f, 0.32f, 0.96f);
            m_pageText = CreateText(root, "M16_Text_Page", "Page", new Vector2(0f, 155f), new Vector2(260f, 42f), 28, true);
            m_queueText = CreateText(
    root,
    "M16_Text_QueueItems",
    string.Empty,
    new Vector2(0f, -20f),
    new Vector2(360f, 120f),
    22,
    mirrorTextOnY: true);
            m_previous = CreateButton(root, "M16_Button_<Previous", "< Previous", "previous", new Vector2(280f, 70f), () => m_controller.PreviousPage(), out m_previousGaze);
            m_next = CreateButton(root, "M16_Button_Next_>", "Next >", "next", new Vector2(-280f, 70f), () => m_controller.NextPage(), out m_nextGaze);
            m_undo = CreateButton(root, "M16_Button_Undo_Last", "Undo Last", "undo", new Vector2(280f, -96f), () => m_controller.UndoLastSelection(), out m_undoGaze);
            m_submit = CreateButton(root, "M16_Button_Submit", "Submit", "submit", new Vector2(-280f, -96f), () => m_controller.Submit(), out m_submitGaze);
            m_gazeInteractor = gameObject.GetComponent<BciPagedGazeInteractor>();
            if (m_gazeInteractor == null)
                m_gazeInteractor = gameObject.AddComponent<BciPagedGazeInteractor>();
            m_gazeInteractor.Initialize(this);
            Debug.Log("M16_UI_READY mode=PagedQueueV1 panel_parent=null panel_world_position=" +
                position.ToString("F3") +
                " camera=" + (camera == null ? "missing" : camera.name) +
                " workspace_anchor=stable_anchor_snapshot" +
                " page_count=" + (m_controller == null || m_controller.CurrentPage == null ? 0 : m_controller.CurrentPage.PageCount) +
                " candidate_count=" + (m_controller == null || m_controller.CurrentPage == null ? 0 : m_controller.CurrentPage.Candidates.Count), this);
            Debug.Log("M16_GAZE_READY camera=" + (camera == null ? "missing" : camera.name) +
                " ray_mask=" + BciPagedGazeInteractor.RaycastLayerMask +
                " control_count=" + GazeControlCount +
                " trigger_dwell_s=" + BciPagedGazeInteractor.TriggerDwellSeconds.ToString("F1") +
                " command_dwell_s=" + BciPagedGazeInteractor.DefaultDwellSeconds.ToString("F1") +
                " reticle_ready=" + (m_gazeInteractor != null && m_gazeInteractor.ReticleReady), this);
        }

        private static bool HasReadyControl(BciPagedGazeControl control)
        {
            return control != null && control.HasCollider && control.OwnerHud != null;
        }

        private static Text CreateText(
            RectTransform parent,
            string objectName,
            string initial,
            Vector2 position,
            Vector2 size,
            int fontSize,
            bool mirrorTextOnY = false)
        {
            GameObject objectValue = new GameObject(objectName);
            objectValue.transform.SetParent(parent, false);
            RectTransform rect = objectValue.AddComponent<RectTransform>();
            rect.anchoredPosition = position;
            rect.sizeDelta = size;
            rect.localRotation = mirrorTextOnY
                ? Quaternion.Euler(0f, 180f, 0f)
                : Quaternion.identity;
            Text text = objectValue.AddComponent<Text>();
            text.text = initial;
            text.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            text.fontSize = fontSize;
            text.color = Color.white;
            text.alignment = TextAnchor.MiddleCenter;
            return text;
        }

        private Button CreateButton(
            RectTransform parent,
            string objectName,
            string label,
            string controlId,
            Vector2 position,
            UnityEngine.Events.UnityAction action,
            out BciPagedGazeControl gazeControl,
            float width = 180f,
            float height = 48f,
            bool requireExitToRearm = false,
            bool trackWhileDisabled = false,
            float dwellSeconds = BciPagedGazeInteractor.DefaultDwellSeconds)
        {
            GameObject objectValue = new GameObject(objectName);
            objectValue.transform.SetParent(parent, false);
            RectTransform rect = objectValue.AddComponent<RectTransform>();
            rect.anchoredPosition = position;
            rect.sizeDelta = new Vector2(width, height);
            Image image = objectValue.AddComponent<Image>();
            image.color = new Color(0.08f, 0.12f, 0.18f, 0.92f);
            Button button = objectValue.AddComponent<Button>();
            button.onClick.AddListener(action);
            gazeControl = objectValue.AddComponent<BciPagedGazeControl>();
            gazeControl.Initialize(this, controlId, action, () => button.interactable,
                requireExitToRearm, trackWhileDisabled, dwellSeconds);
            Text text = CreateText(rect, "M16_Text_Button_" + controlId, label, Vector2.zero, Vector2.zero, 20, true);
            text.rectTransform.anchorMin = Vector2.zero;
            text.rectTransform.anchorMax = Vector2.one;
            text.rectTransform.offsetMin = Vector2.zero;
            text.rectTransform.offsetMax = Vector2.zero;
            return button;
        }

        private void LateUpdate()
        {
            Refresh();
        }

        private void OnDestroy()
        {
            if (m_canvas != null)
            {
                if (Application.isPlaying)
                    Destroy(m_canvas.gameObject);
                else
                    DestroyImmediate(m_canvas.gameObject);
            }
        }
    }
}
