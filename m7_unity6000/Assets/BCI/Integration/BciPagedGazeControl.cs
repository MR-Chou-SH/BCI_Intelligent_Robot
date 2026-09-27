using System;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Dedicated physical gaze hit target for one M16 HUD command. The
    /// callback is the same controller callback used by the ordinary Button,
    /// so gaze cannot bypass queue/epoch validation.
    /// </summary>
    [DisallowMultipleComponent]
    [RequireComponent(typeof(BoxCollider))]
    public sealed class BciPagedGazeControl : MonoBehaviour
    {
        private BoxCollider m_collider;
        private Button m_button;
        private UnityAction m_action;
        private Func<bool> m_enabledProvider;
        private bool m_enabled;
        private bool m_armed = true;
        private bool m_requireExitToRearm;
        private bool m_trackWhileDisabled;
        private bool m_waitingForGazeExit;
        private float m_dwellSeconds = BciPagedGazeInteractor.DefaultDwellSeconds;

        public BciPagedQueueHud OwnerHud { get; private set; }
        public string ControlId { get; private set; }
        public bool IsEnabled => m_enabled && m_button != null && m_button.interactable;
        public bool CanReceiveGaze => m_collider != null && m_collider.enabled;
        public bool IsWaitingForGazeExit => m_waitingForGazeExit;
        public bool IsArmed => m_armed;
        public float DwellSeconds => m_dwellSeconds;
        public bool HasCollider => m_collider != null;
        public bool IsColliderEnabled => m_collider != null && m_collider.enabled;

        private void Awake()
        {
            m_collider = GetComponent<BoxCollider>();
            m_button = GetComponent<Button>();
            RefreshCollider();
        }

        public void Initialize(
            BciPagedQueueHud ownerHud,
            string controlId,
            UnityAction action,
            Func<bool> enabledProvider,
            bool requireExitToRearm = false,
            bool trackWhileDisabled = false,
            float dwellSeconds = BciPagedGazeInteractor.DefaultDwellSeconds)
        {
            if (dwellSeconds <= 0f)
                throw new ArgumentOutOfRangeException(nameof(dwellSeconds));
            OwnerHud = ownerHud;
            ControlId = controlId;
            m_action = action;
            m_enabledProvider = enabledProvider;
            m_requireExitToRearm = requireExitToRearm;
            m_trackWhileDisabled = trackWhileDisabled;
            m_dwellSeconds = dwellSeconds;
            RefreshCollider();
        }

        public void SetEnabled(bool enabled)
        {
            m_enabled = enabled;
            if (!enabled && !m_requireExitToRearm)
                m_armed = true;
            RefreshCollider();
        }

        public void Activate()
        {
            if (!IsEnabled || !m_armed || m_enabledProvider == null || !m_enabledProvider())
                return;
            m_armed = false;
            if (m_requireExitToRearm)
                m_waitingForGazeExit = true;
            if (m_action != null)
                m_action();
        }

        public void OnGazeExit()
        {
            m_armed = true;
            m_waitingForGazeExit = false;
        }

        private void RefreshCollider()
        {
            if (m_collider == null)
                return;
            RectTransform rect = transform as RectTransform;
            if (rect != null)
            {
                m_collider.center = Vector3.zero;
                m_collider.size = new Vector3(rect.rect.width, rect.rect.height, 8f);
            }
            m_collider.enabled = m_enabled || m_trackWhileDisabled;
        }
    }
}
