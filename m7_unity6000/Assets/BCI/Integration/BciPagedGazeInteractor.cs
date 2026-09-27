using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Minimal XR-independent head-gaze input for the M16 command strip. It
    /// uses the main camera forward ray and only activates the dedicated HUD
    /// controls; it does not inspect or alter SSVEP target objects.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class BciPagedGazeInteractor : MonoBehaviour
    {
        public const float DefaultDwellSeconds = 0.8f;
        public const float TriggerDwellSeconds = 1.5f;
        public const int RaycastLayerMask = ~0;

        private BciGazeDwellState m_dwell =
            new BciGazeDwellState(DefaultDwellSeconds);
        private BciPagedQueueHud m_hud;
        private BciPagedGazeControl m_currentControl;
        private GameObject m_reticle;
        private Renderer m_reticleRenderer;
        private Material m_reticleMaterial;

        public BciGazeDwellState DwellState => m_dwell;
        public bool IsInitialized => m_hud != null;
        public bool ReticleReady => m_reticle != null;
        public Transform ReticleTransform => m_reticle == null ? null : m_reticle.transform;

        public void Initialize(BciPagedQueueHud hud)
        {
            if (m_hud != null || hud == null)
                return;
            m_hud = hud;
            EnsureReticle();
        }

        private void Update()
        {
            ProcessGaze(Time.unscaledTime);
        }

        private void ProcessGaze(float now)
        {
            if (m_hud == null)
                return;

            Camera camera = Camera.main;
            if (camera == null)
            {
                ClearCurrentControl();
                m_dwell.Reset();
                SetReticle(Color.white, 0f);
                return;
            }

            EnsureReticle(camera);
            Ray ray = new Ray(camera.transform.position, camera.transform.forward);
            BciPagedGazeControl control = FindControl(ray);

            if (control == null || control.OwnerHud != m_hud)
            {
                ClearCurrentControl();
                m_dwell.Update(null, false, now);
                SetReticle(Color.white, 0f);
                return;
            }

            if (m_currentControl != control)
            {
                if (m_currentControl != null)
                    m_currentControl.OnGazeExit();
                m_currentControl = control;
                if (Mathf.Abs(m_dwell.DwellSeconds - control.DwellSeconds) > 0.0001f)
                    m_dwell = new BciGazeDwellState(control.DwellSeconds);
                Debug.Log("M16_GAZE hover_enter control=" + control.ControlId +
                    " enabled=" + control.IsEnabled +
                    " dwell_s=" + control.DwellSeconds.ToString("F1"), this);
            }
            bool enabled = control.IsEnabled;
            if (control.IsWaitingForGazeExit)
            {
                m_dwell.Reset();
                SetReticle(Color.cyan, 1f);
                return;
            }
            if (!enabled)
            {
                m_dwell.Reset();
                SetReticle(Color.white, 0f);
                return;
            }
            bool activate = m_dwell.Update(control.ControlId, enabled, now);
            float progress = m_dwell.Progress(now);
            SetReticle(activate || progress >= 1f ? Color.cyan : Color.yellow, progress);
            if (activate)
            {
                control.Activate();
                Debug.Log("M16_GAZE dwell_activation control=" + control.ControlId, this);
            }
        }

        private BciPagedGazeControl FindControl(Ray ray)
        {
            RaycastHit[] hits = Physics.RaycastAll(ray, 20f, RaycastLayerMask, QueryTriggerInteraction.Collide);
            BciPagedGazeControl nearest = null;
            float nearestDistance = float.PositiveInfinity;
            for (int index = 0; index < hits.Length; index++)
            {
                BciPagedGazeControl candidate =
                    hits[index].collider.GetComponentInParent<BciPagedGazeControl>();
                if (candidate == null || candidate.OwnerHud != m_hud || !candidate.CanReceiveGaze ||
                    hits[index].distance >= nearestDistance)
                    continue;
                nearest = candidate;
                nearestDistance = hits[index].distance;
            }
            return nearest;
        }

        private void ClearCurrentControl()
        {
            if (m_currentControl != null)
            {
                m_currentControl.OnGazeExit();
                Debug.Log("M16_GAZE hover_exit control=" + m_currentControl.ControlId, this);
            }
            m_currentControl = null;
        }

        private void EnsureReticle(Camera camera = null)
        {
            if (m_reticle != null)
            {
                if (camera != null && m_reticle.transform.parent != camera.transform)
                    m_reticle.transform.SetParent(camera.transform, false);
                return;
            }

            if (camera == null)
                camera = Camera.main;
            if (camera == null)
                return;

            m_reticle = GameObject.CreatePrimitive(PrimitiveType.Quad);
            m_reticle.name = "M16_HeadGazeReticle";
            m_reticle.transform.SetParent(camera.transform, false);
            m_reticle.transform.localPosition = new Vector3(0f, 0f, 1.2f);
            m_reticle.transform.localRotation = Quaternion.identity;
            m_reticle.transform.localScale = Vector3.one * 0.012f;
            Collider collider = m_reticle.GetComponent<Collider>();
            if (collider != null)
            {
                collider.enabled = false;
                DestroyOwnedObject(collider);
            }
            m_reticleRenderer = m_reticle.GetComponent<Renderer>();
            Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Standard");
            if (shader != null)
            {
                m_reticleMaterial = new Material(shader);
                m_reticleRenderer.material = m_reticleMaterial;
            }
            SetReticle(Color.white, 0f);
        }

        private void SetReticle(Color color, float progress)
        {
            if (m_reticle == null)
                return;
            float size = 0.012f + 0.004f * Mathf.Clamp01(progress);
            m_reticle.transform.localScale = Vector3.one * size;
            if (m_reticleMaterial != null)
                m_reticleMaterial.color = color;
            else if (m_reticleRenderer != null)
                m_reticleRenderer.material.color = color;
        }

        private void OnDestroy()
        {
            if (m_reticleMaterial != null)
                DestroyOwnedObject(m_reticleMaterial);
            if (m_reticle != null)
                DestroyOwnedObject(m_reticle);
        }

        private static void DestroyOwnedObject(Object value)
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
