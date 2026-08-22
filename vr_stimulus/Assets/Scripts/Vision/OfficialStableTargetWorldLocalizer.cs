using System;
using System.Collections.Generic;
using System.Globalization;
using Meta.XR;
using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// M7.6 adapter for the Meta-validated 2D-to-world localization path.
    /// It consumes the existing YOLO/StableTarget output and does not own inference.
    /// </summary>
    [RequireComponent(typeof(QuestYolo26DetectionSpike))]
    public sealed class OfficialStableTargetWorldLocalizer : MonoBehaviour
    {
        private const float MarkerDiameterMeters = 0.08f;
        private const float DiagnosticIntervalSeconds = 1f;

        [SerializeField] private QuestYolo26DetectionSpike m_Detector;
        [SerializeField] private PassthroughCameraAccess m_CameraAccess;
        [SerializeField] private EnvironmentRaycastManager m_EnvironmentRaycastManager;
        [SerializeField] private Transform m_TrackingSpace;

        private readonly Dictionary<string, double> m_LastDiagnosticTimes =
            new Dictionary<string, double>(StringComparer.Ordinal);
        private GameObject m_Marker;
        private Material m_MarkerMaterial;

        public void Configure(
            QuestYolo26DetectionSpike detector,
            PassthroughCameraAccess cameraAccess,
            EnvironmentRaycastManager environmentRaycastManager,
            Transform trackingSpace)
        {
            m_Detector = detector;
            m_CameraAccess = cameraAccess;
            m_EnvironmentRaycastManager = environmentRaycastManager;
            m_TrackingSpace = trackingSpace;
        }

        private void Awake()
        {
            if (m_Detector == null)
                m_Detector = GetComponent<QuestYolo26DetectionSpike>();

            if (m_CameraAccess == null)
                m_CameraAccess = FindAnyObjectByType<PassthroughCameraAccess>();

            if (m_EnvironmentRaycastManager == null)
                m_EnvironmentRaycastManager = FindAnyObjectByType<EnvironmentRaycastManager>();
        }

        private void OnEnable()
        {
            if (m_Detector != null)
                m_Detector.StableTargetsUpdated += OnStableTargetsUpdated;
        }

        private void OnDisable()
        {
            if (m_Detector != null)
                m_Detector.StableTargetsUpdated -= OnStableTargetsUpdated;
        }

        private void Start()
        {
            if (m_EnvironmentRaycastManager == null)
            {
                Debug.LogError("M7_OFFICIAL_LOCALIZATION environment_raycast_manager_missing", this);
                return;
            }

            if (m_TrackingSpace != null)
                m_EnvironmentRaycastManager.CustomTrackingSpace = m_TrackingSpace;

            Debug.Log(
                "M7_OFFICIAL_LOCALIZATION ready " +
                "api=PassthroughCameraAccess.ViewportPointToRay->EnvironmentRaycastManager.Raycast->hitInfo.point " +
                "supported=" + EnvironmentRaycastManager.IsSupported,
                this);
        }

        private void OnStableTargetsUpdated(
            IReadOnlyList<StableTargetSnapshot> stableTargets,
            int sourceWidth,
            int sourceHeight)
        {
            if (m_CameraAccess == null || m_EnvironmentRaycastManager == null)
                return;

            if (!TrySelectTarget(stableTargets, out StableTargetSnapshot target))
                return;

            Vector2 bboxCenterPixels = new Vector2(
                (target.Bbox.XMin + target.Bbox.XMax) * 0.5f,
                (target.Bbox.YMin + target.Bbox.YMax) * 0.5f);
            if (sourceWidth <= 0 || sourceHeight <= 0 ||
                !IsFinite(bboxCenterPixels.x) || !IsFinite(bboxCenterPixels.y))
            {
                LogDiagnostic(target, bboxCenterPixels, default, false, "invalid_source_dimensions_or_center", null);
                return;
            }

            // Detector bboxes use top-left image coordinates; Meta viewport uses bottom-left.
            Vector2 viewportPoint = new Vector2(
                bboxCenterPixels.x / sourceWidth,
                1f - (bboxCenterPixels.y / sourceHeight));
            if (viewportPoint.x < 0f || viewportPoint.x > 1f ||
                viewportPoint.y < 0f || viewportPoint.y > 1f)
            {
                LogDiagnostic(target, bboxCenterPixels, default, false, "viewport_out_of_range", null);
                return;
            }

            Pose cameraPose = m_CameraAccess.GetCameraPose();
            Ray ray = m_CameraAccess.ViewportPointToRay(viewportPoint, cameraPose);
            bool raycastSucceeded = m_EnvironmentRaycastManager.Raycast(ray, out EnvironmentRaycastHit hitInfo);
            LogDiagnostic(
                target,
                bboxCenterPixels,
                ray,
                raycastSucceeded,
                hitInfo.status.ToString(),
                raycastSucceeded ? (Vector3?)hitInfo.point : null);

            if (raycastSucceeded)
                ShowMarker(hitInfo.point, hitInfo.normal);
        }

        private void LogDiagnostic(
            StableTargetSnapshot target,
            Vector2 bboxCenterPixels,
            Ray ray,
            bool raycastSucceeded,
            string status,
            Vector3? worldHitPoint)
        {
            double now = Time.realtimeSinceStartupAsDouble;
            if (m_LastDiagnosticTimes.TryGetValue(target.TargetId, out double lastTime) &&
                now - lastTime < DiagnosticIntervalSeconds)
            {
                return;
            }

            m_LastDiagnosticTimes[target.TargetId] = now;
            string worldPoint = worldHitPoint.HasValue
                ? " world_hit_point=" + FormatVector(worldHitPoint.Value)
                : string.Empty;
            Debug.Log(
                "M7_OFFICIAL_LOCALIZATION " +
                "target_id=" + target.TargetId +
                " class=" + target.ClassName +
                " state=" + target.State +
                " bbox_center_px=" + FormatVector2(bboxCenterPixels) +
                " ray_origin=" + FormatVector(ray.origin) +
                " ray_direction=" + FormatVector(ray.direction) +
                " raycast=" + (raycastSucceeded ? "hit" : "miss") +
                " status=" + status +
                worldPoint,
                this);
        }

        private void ShowMarker(Vector3 worldPoint, Vector3 normal)
        {
            EnsureMarker();
            m_Marker.transform.SetPositionAndRotation(
                worldPoint,
                normal.sqrMagnitude > 0.001f
                    ? Quaternion.LookRotation(normal)
                    : Quaternion.identity);
            m_Marker.SetActive(true);
        }

        private void EnsureMarker()
        {
            if (m_Marker != null)
                return;

            m_Marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            m_Marker.name = "M7.6 Official World Localization Marker";
            m_Marker.transform.localScale = Vector3.one * MarkerDiameterMeters;

            Collider collider = m_Marker.GetComponent<Collider>();
            if (collider != null)
                Destroy(collider);

            Renderer renderer = m_Marker.GetComponent<Renderer>();
            Shader shader = Shader.Find("Unlit/Color") ?? Shader.Find("Universal Render Pipeline/Unlit");
            if (renderer != null && shader != null)
            {
                m_MarkerMaterial = new Material(shader)
                {
                    color = new Color(1f, 0.05f, 0.8f, 1f),
                    name = "M7.6 Official Localization Marker Material"
                };
                renderer.material = m_MarkerMaterial;
            }
        }

        private static bool TrySelectTarget(
            IReadOnlyList<StableTargetSnapshot> stableTargets,
            out StableTargetSnapshot selectedTarget)
        {
            selectedTarget = default;
            bool found = false;
            for (int i = 0; i < stableTargets.Count; i++)
            {
                StableTargetSnapshot candidate = stableTargets[i];
                if (candidate.State == StableTargetState.Lost ||
                    (found && candidate.Confidence <= selectedTarget.Confidence))
                {
                    continue;
                }

                selectedTarget = candidate;
                found = true;
            }

            return found;
        }

        private static string FormatVector(Vector3 value)
        {
            return "(" + value.x.ToString("F3", CultureInfo.InvariantCulture) + "," +
                value.y.ToString("F3", CultureInfo.InvariantCulture) + "," +
                value.z.ToString("F3", CultureInfo.InvariantCulture) + ")";
        }

        private static string FormatVector2(Vector2 value)
        {
            return "(" + value.x.ToString("F1", CultureInfo.InvariantCulture) + "," +
                value.y.ToString("F1", CultureInfo.InvariantCulture) + ")";
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }
    }
}
