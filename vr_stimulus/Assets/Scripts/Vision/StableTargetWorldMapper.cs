using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;
using GraphicsFormat = UnityEngine.Experimental.Rendering.GraphicsFormat;
using GraphicsFormatUsage = UnityEngine.Experimental.Rendering.GraphicsFormatUsage;

#if UNITY_ANDROID && !UNITY_EDITOR
using UnityEngine.Android;
#endif

namespace BCIIntelligentRobot.Vision
{
    public enum StableTargetWorldPositionState
    {
        WaitingForScenePermission,
        WaitingForEnvironmentDepthFrame,
        WaitingForEnvironmentDepthTexture,
        DepthReadbackUnsupported,
        DepthReadbackPending,
        DepthReadbackFailed,
        DepthSampleInvalid,
        ValidEnvironmentDepthSample
    }

    /// <summary>
    /// World-position association for one stable 2D target. An invalid state intentionally
    /// carries no synthetic position; later BCI binding must inspect HasWorldPosition.
    /// </summary>
    public readonly struct StableTargetWorldBinding
    {
        public StableTargetWorldBinding(
            string targetId,
            Vector2 bboxCenterPixels,
            Vector2 depthUv,
            Ray ray,
            bool hasRay,
            bool environmentDepthFrameAvailable,
            bool depthSampleValid,
            float depthMeters,
            Vector3 worldPosition,
            bool hasWorldPosition,
            StableTargetWorldPositionState state)
        {
            TargetId = targetId;
            BboxCenterPixels = bboxCenterPixels;
            DepthUv = depthUv;
            Ray = ray;
            HasRay = hasRay;
            EnvironmentDepthFrameAvailable = environmentDepthFrameAvailable;
            DepthSampleValid = depthSampleValid;
            DepthMeters = depthMeters;
            WorldPosition = worldPosition;
            HasWorldPosition = hasWorldPosition;
            State = state;
        }

        public string TargetId { get; }
        public Vector2 BboxCenterPixels { get; }
        public Vector2 DepthUv { get; }
        public Ray Ray { get; }
        public bool HasRay { get; }
        public bool EnvironmentDepthFrameAvailable { get; }
        public bool DepthSampleValid { get; }
        public float DepthMeters { get; }
        public Vector3 WorldPosition { get; }
        public bool HasWorldPosition { get; }
        public StableTargetWorldPositionState State { get; }
    }

    /// <summary>
    /// M7.4 minimal world-position spike.
    ///
    /// Primary path: stable bbox center -> normalized provisional depth-view UV ->
    /// 5x5 environment-depth texture sample -> depth-view ray -> world position.
    ///
    /// The current Meta OpenXR occlusion provider exposes environment depth as a texture,
    /// but does not implement AR Foundation's public environment-depth CPU-image provider.
    /// This component therefore uses Unity's non-blocking AsyncGPUReadback on only the small
    /// sample patch. It never substitutes the HMD center-eye for the depth view pose.
    /// </summary>
    [RequireComponent(typeof(QuestYolo26DetectionSpike))]
    [RequireComponent(typeof(AROcclusionManager))]
    public sealed class StableTargetWorldMapper : MonoBehaviour
    {
        private const string HeadsetCameraPermission = "horizonos.permission.HEADSET_CAMERA";
        private const string ScenePermission = "com.oculus.permission.USE_SCENE";
        private const string MappingBoundary = "rgb_bbox_uv_to_occlusion_left_view_normalized_provisional";
        private const int DepthSamplePatchSize = 5;
        private const int MinimumValidDepthSamples = 5;
        private const int LeftEnvironmentDepthViewIndex = 0;
        private const float DepthDistanceResponseDiagnosticIntervalSeconds = 1f;
        private const float MarkerDiameterMeters = 0.05f;
        private const GraphicsFormat DepthCopyGraphicsFormat = GraphicsFormat.R32_SFloat;
        private const string DepthCopyShaderResourcePath = "Vision/M7EnvironmentDepthArrayCopy";
        private static readonly int EnvironmentDepthTexturePropertyId = Shader.PropertyToID("_EnvironmentDepthTexture");
        private static readonly int EnvironmentDepthViewSlicePropertyId = Shader.PropertyToID("_EnvironmentDepthViewSlice");

        [SerializeField] private QuestYolo26DetectionSpike m_Detector;
        [SerializeField] private AROcclusionManager m_OcclusionManager;

        private readonly Dictionary<string, StableTargetWorldBinding> m_Bindings =
            new Dictionary<string, StableTargetWorldBinding>();
        private readonly List<float> m_DepthPatchSamples = new List<float>(DepthSamplePatchSize * DepthSamplePatchSize);
        private Pose m_LeftEnvironmentDepthViewPose;
        private XRFov m_LeftEnvironmentDepthFov;
        private XRNearFarPlanes m_EnvironmentDepthNearFarPlanes;
        private bool m_HasEnvironmentDepthFrame;
        private bool m_HasEnvironmentDepthNearFarPlanes;
        private bool m_HasEnvironmentDepthTimestamp;
        private long m_EnvironmentDepthTimestampNs;
        private long m_EnvironmentDepthFrameSequence;
        private bool m_ScenePermissionGranted;
        private bool m_DepthReadbackInFlight;
        private bool m_DepthSampleDiagnosticLogged;
        private float m_NextDepthDistanceResponseDiagnosticTime;
        private bool m_Destroyed;
        private PendingDepthSample m_PendingDepthSample;
        private RenderTexture m_DepthCopyTexture;
        private CommandBuffer m_DepthCopyCommandBuffer;
        private Material m_DepthCopyMaterial;
        private GraphicsFormat m_LastDepthSourceFormat = GraphicsFormat.None;
        private string m_LastDepthCopyStatus = "not_requested";
        private string m_LastDepthReadbackStatus = "not_requested";
        private GameObject m_Marker;
        private Material m_MarkerMaterial;

#if UNITY_ANDROID && !UNITY_EDITOR
        private bool m_ScenePermissionResolved;
        private bool m_ScenePermissionRequested;
        private PermissionCallbacks m_ScenePermissionCallbacks;
#endif

        private readonly struct PendingDepthSample
        {
            public PendingDepthSample(
                string targetId,
                Vector2 bboxCenterPixels,
                Vector2 depthUv,
                Ray ray,
                XRNearFarPlanes nearFarPlanes,
                bool depthFrameAvailable,
                bool depthFrameTimestampAvailable,
                long depthFrameTimestampNs,
                long depthFrameSequence,
                string sourceTextureType,
                TextureDimension sourceTextureDimension,
                GraphicsFormat sourceTextureFormat,
                int sourceTextureWidth,
                int sourceTextureHeight,
                int sourceTextureArraySlices,
                int selectedSourceSlice)
            {
                TargetId = targetId;
                BboxCenterPixels = bboxCenterPixels;
                DepthUv = depthUv;
                Ray = ray;
                NearFarPlanes = nearFarPlanes;
                DepthFrameAvailable = depthFrameAvailable;
                DepthFrameTimestampAvailable = depthFrameTimestampAvailable;
                DepthFrameTimestampNs = depthFrameTimestampNs;
                DepthFrameSequence = depthFrameSequence;
                SourceTextureType = sourceTextureType;
                SourceTextureDimension = sourceTextureDimension;
                SourceTextureFormat = sourceTextureFormat;
                SourceTextureWidth = sourceTextureWidth;
                SourceTextureHeight = sourceTextureHeight;
                SourceTextureArraySlices = sourceTextureArraySlices;
                SelectedSourceSlice = selectedSourceSlice;
            }

            public string TargetId { get; }
            public Vector2 BboxCenterPixels { get; }
            public Vector2 DepthUv { get; }
            public Ray Ray { get; }
            public XRNearFarPlanes NearFarPlanes { get; }
            public bool DepthFrameAvailable { get; }
            public bool DepthFrameTimestampAvailable { get; }
            public long DepthFrameTimestampNs { get; }
            public long DepthFrameSequence { get; }
            public string SourceTextureType { get; }
            public TextureDimension SourceTextureDimension { get; }
            public GraphicsFormat SourceTextureFormat { get; }
            public int SourceTextureWidth { get; }
            public int SourceTextureHeight { get; }
            public int SourceTextureArraySlices { get; }
            public int SelectedSourceSlice { get; }
        }

        public event Action<StableTargetWorldBinding> StableTargetWorldPositionUpdated;

        public void Configure(
            QuestYolo26DetectionSpike detector,
            AROcclusionManager occlusionManager)
        {
            m_Detector = detector;
            m_OcclusionManager = occlusionManager;
        }

        public bool TryGetWorldBinding(string targetId, out StableTargetWorldBinding binding)
        {
            return m_Bindings.TryGetValue(targetId, out binding);
        }

        private void Awake()
        {
            if (m_Detector == null)
                m_Detector = GetComponent<QuestYolo26DetectionSpike>();
            if (m_OcclusionManager == null)
                m_OcclusionManager = GetComponent<AROcclusionManager>();

            // Meta's provider explicitly requires USE_SCENE before the manager starts.
            if (m_OcclusionManager != null)
                m_OcclusionManager.enabled = false;
        }

        private void OnEnable()
        {
            if (m_Detector != null)
                m_Detector.StableTargetsUpdated += OnStableTargetsUpdated;
            if (m_OcclusionManager != null)
                m_OcclusionManager.frameReceived += OnEnvironmentDepthFrameReceived;
        }

        private void Start()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            if (Permission.HasUserAuthorizedPermission(ScenePermission))
            {
                OnScenePermissionGranted(ScenePermission);
                return;
            }

            Debug.Log("M7WORLD waiting_for_headset_camera_permission before_scene_permission_request", this);
#else
            Debug.Log("M7WORLD scene_permission_bypassed editor=true", this);
            OnScenePermissionGranted(ScenePermission);
#endif
        }

        private void Update()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            if (!m_ScenePermissionResolved && !m_ScenePermissionRequested &&
                Permission.HasUserAuthorizedPermission(HeadsetCameraPermission))
            {
                RequestScenePermission();
            }
#endif
        }

        private void OnDisable()
        {
            if (m_Detector != null)
                m_Detector.StableTargetsUpdated -= OnStableTargetsUpdated;
            if (m_OcclusionManager != null)
                m_OcclusionManager.frameReceived -= OnEnvironmentDepthFrameReceived;
        }

        private void OnDestroy()
        {
            m_Destroyed = true;
            ReleaseDepthCopyResources();
            if (m_Marker != null)
                Destroy(m_Marker);
            if (m_MarkerMaterial != null)
                Destroy(m_MarkerMaterial);
        }

#if UNITY_ANDROID && !UNITY_EDITOR
        private void RequestScenePermission()
        {
            m_ScenePermissionRequested = true;
            m_ScenePermissionCallbacks = new PermissionCallbacks();
            m_ScenePermissionCallbacks.PermissionGranted += OnScenePermissionGranted;
            m_ScenePermissionCallbacks.PermissionDenied += OnScenePermissionDenied;
            Debug.Log("M7WORLD scene_permission_request_started permission=" + ScenePermission, this);
            Permission.RequestUserPermission(ScenePermission, m_ScenePermissionCallbacks);
        }
#endif

        private void OnScenePermissionGranted(string permission)
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            m_ScenePermissionResolved = true;
#endif
            m_ScenePermissionGranted = true;
            if (m_OcclusionManager != null)
                m_OcclusionManager.enabled = true;

            Debug.Log("M7WORLD scene_permission_granted occlusion_manager_enabled=" +
                (m_OcclusionManager != null && m_OcclusionManager.enabled).ToString().ToLowerInvariant(), this);
        }

        private void OnScenePermissionDenied(string permission)
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            m_ScenePermissionResolved = true;
#endif
            m_ScenePermissionGranted = false;
            HideMarker();
            Debug.LogWarning("M7WORLD scene_permission_denied permission=" + permission, this);
        }

        private void OnEnvironmentDepthFrameReceived(AROcclusionFrameEventArgs args)
        {
            m_HasEnvironmentDepthFrame = false;
            m_EnvironmentDepthFrameSequence++;
            m_HasEnvironmentDepthTimestamp = args.TryGetTimestamp(out m_EnvironmentDepthTimestampNs);
            m_HasEnvironmentDepthNearFarPlanes = args.TryGetNearFarPlanes(out m_EnvironmentDepthNearFarPlanes);

            if (!args.TryGetPoses(out var poses) || !args.TryGetFovs(out var fovs) ||
                poses.Count == 0 || fovs.Count == 0)
            {
                return;
            }

            // OpenXR specifies index 0 as the left eye. This same index is explicitly
            // selected from a multi-view depth texture before readback.
            m_LeftEnvironmentDepthViewPose = poses[LeftEnvironmentDepthViewIndex];
            m_LeftEnvironmentDepthFov = fovs[LeftEnvironmentDepthViewIndex];
            m_HasEnvironmentDepthFrame = true;
        }

        private void OnStableTargetsUpdated(
            IReadOnlyList<StableTargetSnapshot> stableTargets,
            int sourceWidth,
            int sourceHeight)
        {
            if (!TrySelectHighestConfidenceActiveTarget(stableTargets, out StableTargetSnapshot target))
            {
                HideMarker();
                return;
            }

            Vector2 bboxCenterPixels = new Vector2(
                target.Bbox.X + (target.Bbox.Width * 0.5f),
                target.Bbox.Y + (target.Bbox.Height * 0.5f));
            RequestWorldPosition(target.TargetId, bboxCenterPixels, sourceWidth, sourceHeight);
        }

        private void RequestWorldPosition(
            string targetId,
            Vector2 bboxCenterPixels,
            int sourceWidth,
            int sourceHeight)
        {
            if (!m_ScenePermissionGranted)
            {
                PublishBinding(targetId, bboxCenterPixels, default, default, false, false, false,
                    default, default, false, StableTargetWorldPositionState.WaitingForScenePermission);
                HideMarker();
                return;
            }

            if (!m_HasEnvironmentDepthFrame || !m_HasEnvironmentDepthNearFarPlanes || sourceWidth <= 0 || sourceHeight <= 0)
            {
                PublishBinding(targetId, bboxCenterPixels, default, default, false,
                    m_HasEnvironmentDepthFrame, false, default, default, false,
                    StableTargetWorldPositionState.WaitingForEnvironmentDepthFrame);
                HideMarker();
                return;
            }

            // Detector pixels use a top-left image origin; OpenXR depth views are not flipped.
            // This is normalized UV bridging only, not RGB/depth extrinsic calibration.
            Vector2 provisionalDepthUv = new Vector2(
                Mathf.Clamp01(bboxCenterPixels.x / sourceWidth),
                Mathf.Clamp01(1f - (bboxCenterPixels.y / sourceHeight)));
            if (!WorldTargetRayMath.TryCreateWorldRay(
                    m_LeftEnvironmentDepthViewPose,
                    m_LeftEnvironmentDepthFov,
                    provisionalDepthUv,
                    out Ray worldRay))
            {
                PublishBinding(targetId, bboxCenterPixels, provisionalDepthUv, default, false, true,
                    false, default, default, false, StableTargetWorldPositionState.DepthSampleInvalid);
                HideMarker();
                return;
            }

            if (!SystemInfo.supportsAsyncGPUReadback)
            {
                PublishBinding(targetId, bboxCenterPixels, provisionalDepthUv, worldRay, true, true,
                    false, default, default, false, StableTargetWorldPositionState.DepthReadbackUnsupported);
                HideMarker();
                return;
            }

            if (m_DepthReadbackInFlight)
                return;

            if (m_OcclusionManager == null || !m_OcclusionManager.TryGetEnvironmentDepthTexture(out Texture depthTexture) ||
                depthTexture == null || depthTexture.width <= 0 || depthTexture.height <= 0)
            {
                m_LastDepthSourceFormat = GraphicsFormat.None;
                m_LastDepthCopyStatus = "source_unavailable";
                m_LastDepthReadbackStatus = "not_requested";
                PublishBinding(targetId, bboxCenterPixels, provisionalDepthUv, worldRay, true, true,
                    false, default, default, false, StableTargetWorldPositionState.WaitingForEnvironmentDepthTexture);
                HideMarker();
                return;
            }

            m_LastDepthSourceFormat = depthTexture.graphicsFormat;
            m_LastDepthReadbackStatus = "not_requested";
            int sourceTextureArraySlices = GetDepthTextureArraySliceCount(depthTexture);
            if (sourceTextureArraySlices <= LeftEnvironmentDepthViewIndex ||
                !TryCopyEnvironmentDepthTexture(depthTexture, LeftEnvironmentDepthViewIndex))
            {
                PublishBinding(targetId, bboxCenterPixels, provisionalDepthUv, worldRay, true, true,
                    false, default, default, false, StableTargetWorldPositionState.DepthReadbackUnsupported);
                HideMarker();
                return;
            }

            int sampleWidth = Mathf.Min(DepthSamplePatchSize, m_DepthCopyTexture.width);
            int sampleHeight = Mathf.Min(DepthSamplePatchSize, m_DepthCopyTexture.height);
            int sampleX = Mathf.Clamp(
                Mathf.FloorToInt(provisionalDepthUv.x * m_DepthCopyTexture.width) - (sampleWidth / 2),
                0,
                m_DepthCopyTexture.width - sampleWidth);
            int sampleY = Mathf.Clamp(
                Mathf.FloorToInt(provisionalDepthUv.y * m_DepthCopyTexture.height) - (sampleHeight / 2),
                0,
                m_DepthCopyTexture.height - sampleHeight);

            m_PendingDepthSample = new PendingDepthSample(
                targetId,
                bboxCenterPixels,
                provisionalDepthUv,
                worldRay,
                m_EnvironmentDepthNearFarPlanes,
                m_HasEnvironmentDepthFrame,
                m_HasEnvironmentDepthTimestamp,
                m_EnvironmentDepthTimestampNs,
                m_EnvironmentDepthFrameSequence,
                depthTexture.GetType().Name,
                depthTexture.dimension,
                depthTexture.graphicsFormat,
                depthTexture.width,
                depthTexture.height,
                sourceTextureArraySlices,
                LeftEnvironmentDepthViewIndex);
            m_DepthReadbackInFlight = true;
            PublishBinding(targetId, bboxCenterPixels, provisionalDepthUv, worldRay, true, true,
                false, default, default, false, StableTargetWorldPositionState.DepthReadbackPending);

            try
            {
                AsyncGPUReadback.Request(
                    m_DepthCopyTexture,
                    0,
                    sampleX,
                    sampleWidth,
                    sampleY,
                    sampleHeight,
                    0,
                    1,
                    DepthCopyGraphicsFormat,
                    OnDepthPatchReadbackComplete);
                m_LastDepthReadbackStatus = "requested";
            }
            catch (Exception exception)
            {
                m_DepthReadbackInFlight = false;
                m_LastDepthReadbackStatus = "request_exception";
                Debug.LogWarning("M7WORLD depth_readback_request_failed exception=" + exception.GetType().Name, this);
                PublishBinding(targetId, bboxCenterPixels, provisionalDepthUv, worldRay, true, true,
                    false, default, default, false, StableTargetWorldPositionState.DepthReadbackFailed);
                HideMarker();
            }
        }

        private void OnDepthPatchReadbackComplete(AsyncGPUReadbackRequest request)
        {
            m_DepthReadbackInFlight = false;
            if (m_Destroyed)
                return;

            PendingDepthSample pending = m_PendingDepthSample;
            if (request.hasError)
            {
                m_LastDepthReadbackStatus = "failed";
                PublishBinding(pending.TargetId, pending.BboxCenterPixels, pending.DepthUv, pending.Ray, true,
                    pending.DepthFrameAvailable, false, default, default, false,
                    StableTargetWorldPositionState.DepthReadbackFailed);
                HideMarker();
                return;
            }

            m_LastDepthReadbackStatus = "success";

            m_DepthPatchSamples.Clear();
            var rawSamples = request.GetData<float>();
            for (int i = 0; i < rawSamples.Length; i++)
                m_DepthPatchSamples.Add(rawSamples[i]);

            LogDepthSampleDiagnostic(m_DepthPatchSamples, pending.NearFarPlanes);

            bool hasMedianNormalizedDepth = WorldTargetRayMath.TryGetMedianValidDepth(
                m_DepthPatchSamples,
                MinimumValidDepthSamples,
                out float medianNormalizedDepth);
            float depthMeters = default;
            bool hasMetricDepth = hasMedianNormalizedDepth &&
                WorldTargetRayMath.TryDecodeEnvironmentDepthMeters(
                    medianNormalizedDepth,
                    pending.NearFarPlanes,
                    out depthMeters);
            Vector3 worldPosition = default;
            bool hasWorldPosition = hasMetricDepth &&
                WorldTargetRayMath.TryCreateWorldPosition(pending.Ray, depthMeters, out worldPosition);

            LogDepthDistanceResponseDiagnostic(
                pending,
                hasMedianNormalizedDepth,
                medianNormalizedDepth,
                hasMetricDepth,
                depthMeters,
                hasWorldPosition,
                worldPosition);

            if (!hasWorldPosition)
            {
                PublishBinding(pending.TargetId, pending.BboxCenterPixels, pending.DepthUv, pending.Ray, true,
                    pending.DepthFrameAvailable, false, default, default, false,
                    StableTargetWorldPositionState.DepthSampleInvalid);
                HideMarker();
                return;
            }

            PublishBinding(pending.TargetId, pending.BboxCenterPixels, pending.DepthUv, pending.Ray, true,
                pending.DepthFrameAvailable, true, depthMeters, worldPosition, true,
                StableTargetWorldPositionState.ValidEnvironmentDepthSample);
            ShowMarker(worldPosition);
        }

        private void PublishBinding(
            string targetId,
            Vector2 bboxCenterPixels,
            Vector2 depthUv,
            Ray ray,
            bool hasRay,
            bool depthFrameAvailable,
            bool depthSampleValid,
            float depthMeters,
            Vector3 worldPosition,
            bool hasWorldPosition,
            StableTargetWorldPositionState state)
        {
            var binding = new StableTargetWorldBinding(
                targetId,
                bboxCenterPixels,
                depthUv,
                ray,
                hasRay,
                depthFrameAvailable,
                depthSampleValid,
                depthMeters,
                worldPosition,
                hasWorldPosition,
                state);
            m_Bindings[targetId] = binding;
            StableTargetWorldPositionUpdated?.Invoke(binding);

            string rayLog = hasRay
                ? "(" + Format(ray.origin.x) + "," + Format(ray.origin.y) + "," + Format(ray.origin.z) + ")->(" +
                  Format(ray.direction.x) + "," + Format(ray.direction.y) + "," + Format(ray.direction.z) + ")"
                : "unavailable";
            string worldPositionLog = hasWorldPosition
                ? "(" + Format(worldPosition.x) + "," + Format(worldPosition.y) + "," + Format(worldPosition.z) + ")"
                : "unavailable";
            Debug.Log(
                "M7WORLD target_id=" + targetId +
                " bbox_center_px=(" + Format(bboxCenterPixels.x) + "," + Format(bboxCenterPixels.y) + ")" +
                " depth_uv=(" + Format(depthUv.x) + "," + Format(depthUv.y) + ")" +
                " ray_ready=" + hasRay.ToString().ToLowerInvariant() +
                " ray=" + rayLog +
                " depth_frame_available=" + depthFrameAvailable.ToString().ToLowerInvariant() +
                " depth_frame_timestamp_available=" + m_HasEnvironmentDepthTimestamp.ToString().ToLowerInvariant() +
                " depth_frame_timestamp_ns=" + (m_HasEnvironmentDepthTimestamp ? m_EnvironmentDepthTimestampNs.ToString(CultureInfo.InvariantCulture) : "unavailable") +
                " depth_sample_valid=" + depthSampleValid.ToString().ToLowerInvariant() +
                " depth_value_m=" + (depthSampleValid ? Format(depthMeters) : "unavailable") +
                " depth_source_format=" + m_LastDepthSourceFormat +
                " depth_copy_target_format=" + DepthCopyGraphicsFormat +
                " depth_copy_status=" + m_LastDepthCopyStatus +
                " depth_readback_status=" + m_LastDepthReadbackStatus +
                " world_position=" + worldPositionLog +
                " state=" + state.ToString().ToLowerInvariant() +
                " mapping_boundary=" + MappingBoundary,
                this);
        }

        private void LogDepthSampleDiagnostic(IReadOnlyList<float> rawSamples, XRNearFarPlanes nearFarPlanes)
        {
            if (m_DepthSampleDiagnosticLogged)
                return;

            m_DepthSampleDiagnosticLogged = true;
            int finiteCount = 0;
            int nanCount = 0;
            int infinityCount = 0;
            int zeroCount = 0;
            float minimum = float.PositiveInfinity;
            float maximum = float.NegativeInfinity;
            var finiteValues = new List<float>(rawSamples.Count);
            var rawValues = new StringBuilder(rawSamples.Count * 10);

            for (int i = 0; i < rawSamples.Count; i++)
            {
                float value = rawSamples[i];
                if (i > 0)
                    rawValues.Append(',');
                rawValues.Append(FormatDiagnostic(value));

                if (float.IsNaN(value))
                {
                    nanCount++;
                    continue;
                }

                if (float.IsInfinity(value))
                {
                    infinityCount++;
                    continue;
                }

                finiteCount++;
                if (value == 0f)
                    zeroCount++;
                if (value < minimum)
                    minimum = value;
                if (value > maximum)
                    maximum = value;
                finiteValues.Add(value);
            }

            finiteValues.Sort();
            float median = default;
            bool hasMedian = finiteValues.Count > 0;
            if (hasMedian)
            {
                int middle = finiteValues.Count / 2;
                median = (finiteValues.Count & 1) == 0
                    ? (finiteValues[middle - 1] + finiteValues[middle]) * 0.5f
                    : finiteValues[middle];
            }

            int centerIndex = rawSamples.Count / 2;
            Debug.Log(
                "M7WORLD depth_raw_sample_diagnostic sample_count=" + rawSamples.Count +
                " finite_count=" + finiteCount +
                " nan_count=" + nanCount +
                " infinity_count=" + infinityCount +
                " zero_count=" + zeroCount +
                " min=" + (finiteCount > 0 ? FormatDiagnostic(minimum) : "unavailable") +
                " max=" + (finiteCount > 0 ? FormatDiagnostic(maximum) : "unavailable") +
                " median_all_finite=" + (hasMedian ? FormatDiagnostic(median) : "unavailable") +
                " center_sample=" + (rawSamples.Count > 0 ? FormatDiagnostic(rawSamples[centerIndex]) : "unavailable") +
                " near_z_m=" + FormatDiagnostic(nearFarPlanes.nearZ) +
                " far_z_m=" + FormatDiagnostic(nearFarPlanes.farZ) +
                " raw_samples=[" + rawValues + "]",
                this);
        }

        private void LogDepthDistanceResponseDiagnostic(
            PendingDepthSample pending,
            bool hasMedianNormalizedDepth,
            float medianNormalizedDepth,
            bool hasMetricDepth,
            float depthMeters,
            bool hasWorldPosition,
            Vector3 worldPosition)
        {
            if (Time.unscaledTime < m_NextDepthDistanceResponseDiagnosticTime)
                return;

            m_NextDepthDistanceResponseDiagnosticTime =
                Time.unscaledTime + DepthDistanceResponseDiagnosticIntervalSeconds;
            string timestamp = pending.DepthFrameTimestampAvailable
                ? pending.DepthFrameTimestampNs.ToString(CultureInfo.InvariantCulture)
                : "unavailable";
            string worldPositionLog = hasWorldPosition
                ? "(" + Format(worldPosition.x) + "," + Format(worldPosition.y) + "," + Format(worldPosition.z) + ")"
                : "unavailable";

            Debug.Log(
                "M7WORLD depth_distance_response" +
                " depth_frame_sequence=" + pending.DepthFrameSequence.ToString(CultureInfo.InvariantCulture) +
                " depth_frame_timestamp_available=" + pending.DepthFrameTimestampAvailable.ToString().ToLowerInvariant() +
                " depth_frame_timestamp_ns=" + timestamp +
                " source_type=" + pending.SourceTextureType +
                " source_dimension=" + pending.SourceTextureDimension +
                " source_format=" + pending.SourceTextureFormat +
                " source_width=" + pending.SourceTextureWidth.ToString(CultureInfo.InvariantCulture) +
                " source_height=" + pending.SourceTextureHeight.ToString(CultureInfo.InvariantCulture) +
                " source_array_slices=" + pending.SourceTextureArraySlices.ToString(CultureInfo.InvariantCulture) +
                " selected_depth_view=left" +
                " selected_depth_view_index=" + LeftEnvironmentDepthViewIndex.ToString(CultureInfo.InvariantCulture) +
                " selected_source_slice=" + pending.SelectedSourceSlice.ToString(CultureInfo.InvariantCulture) +
                " copy_path=texture2darray_shader_explicit_left_slice" +
                " bbox_center_px=(" + Format(pending.BboxCenterPixels.x) + "," + Format(pending.BboxCenterPixels.y) + ")" +
                " depth_uv=(" + Format(pending.DepthUv.x) + "," + Format(pending.DepthUv.y) + ")" +
                " raw_median=" + (hasMedianNormalizedDepth ? FormatDiagnostic(medianNormalizedDepth) : "unavailable") +
                " decoded_depth_m=" + (hasMetricDepth ? FormatDiagnostic(depthMeters) : "unavailable") +
                " world_position=" + worldPositionLog,
                this);
        }

        private bool TryCopyEnvironmentDepthTexture(Texture sourceTexture, int sourceDepthSlice)
        {
            if (sourceTexture == null || sourceTexture.width <= 0 || sourceTexture.height <= 0)
            {
                m_LastDepthCopyStatus = "source_invalid";
                return false;
            }

            if (!SystemInfo.IsFormatSupported(DepthCopyGraphicsFormat, GraphicsFormatUsage.Render) ||
                !SystemInfo.IsFormatSupported(DepthCopyGraphicsFormat, GraphicsFormatUsage.ReadPixels))
            {
                m_LastDepthCopyStatus = "target_format_unsupported";
                Debug.LogWarning(
                    "M7WORLD depth_copy_target_unsupported format=" + DepthCopyGraphicsFormat +
                    " render=" + SystemInfo.IsFormatSupported(DepthCopyGraphicsFormat, GraphicsFormatUsage.Render) +
                    " read_pixels=" + SystemInfo.IsFormatSupported(DepthCopyGraphicsFormat, GraphicsFormatUsage.ReadPixels),
                    this);
                return false;
            }

            if (sourceTexture.dimension != TextureDimension.Tex2DArray)
            {
                m_LastDepthCopyStatus = "source_not_texture2darray";
                Debug.LogWarning(
                    "M7WORLD depth_copy_source_unsupported dimension=" + sourceTexture.dimension +
                    " expected=" + TextureDimension.Tex2DArray,
                    this);
                return false;
            }

            if (!EnsureDepthCopyMaterial())
                return false;

            if (m_DepthCopyTexture == null ||
                m_DepthCopyTexture.width != sourceTexture.width ||
                m_DepthCopyTexture.height != sourceTexture.height)
            {
                ReleaseDepthCopyTexture();
                var descriptor = new RenderTextureDescriptor(
                    sourceTexture.width,
                    sourceTexture.height,
                    DepthCopyGraphicsFormat,
                    0)
                {
                    msaaSamples = 1,
                    volumeDepth = 1,
                    dimension = TextureDimension.Tex2D,
                    useMipMap = false,
                    autoGenerateMips = false,
                    sRGB = false
                };
                m_DepthCopyTexture = new RenderTexture(descriptor)
                {
                    name = "M7.4 Environment Depth Readback Copy",
                    filterMode = FilterMode.Point,
                    wrapMode = TextureWrapMode.Clamp,
                    hideFlags = HideFlags.HideAndDontSave
                };
                if (!m_DepthCopyTexture.Create())
                {
                    ReleaseDepthCopyTexture();
                    m_LastDepthCopyStatus = "target_create_failed";
                    return false;
                }
            }

            if (m_DepthCopyCommandBuffer == null)
            {
                m_DepthCopyCommandBuffer = new CommandBuffer
                {
                    name = "M7.4 Environment Depth Format Copy"
                };
            }

            try
            {
                m_DepthCopyCommandBuffer.Clear();
                m_DepthCopyMaterial.SetTexture(EnvironmentDepthTexturePropertyId, sourceTexture);
                m_DepthCopyMaterial.SetFloat(EnvironmentDepthViewSlicePropertyId, sourceDepthSlice);
                m_DepthCopyCommandBuffer.SetRenderTarget(m_DepthCopyTexture);
                m_DepthCopyCommandBuffer.ClearRenderTarget(false, true, Color.clear);
                m_DepthCopyCommandBuffer.DrawProcedural(
                    Matrix4x4.identity,
                    m_DepthCopyMaterial,
                    0,
                    MeshTopology.Triangles,
                    3,
                    1);
                Graphics.ExecuteCommandBuffer(m_DepthCopyCommandBuffer);
                m_DepthCopyCommandBuffer.Clear();
                m_LastDepthCopyStatus = "success";
                return true;
            }
            catch (Exception exception)
            {
                m_DepthCopyCommandBuffer.Clear();
                m_LastDepthCopyStatus = "exception";
                Debug.LogWarning("M7WORLD depth_copy_failed exception=" + exception.GetType().Name, this);
                return false;
            }
        }

        private bool EnsureDepthCopyMaterial()
        {
            if (m_DepthCopyMaterial != null)
                return true;

            Shader depthCopyShader = Resources.Load<Shader>(DepthCopyShaderResourcePath);
            if (depthCopyShader == null)
            {
                m_LastDepthCopyStatus = "shader_missing";
                Debug.LogWarning(
                    "M7WORLD depth_copy_shader_missing resource=" + DepthCopyShaderResourcePath,
                    this);
                return false;
            }

            if (!depthCopyShader.isSupported)
            {
                m_LastDepthCopyStatus = "shader_unsupported";
                Debug.LogWarning(
                    "M7WORLD depth_copy_shader_unsupported shader=" + depthCopyShader.name,
                    this);
                return false;
            }

            m_DepthCopyMaterial = new Material(depthCopyShader)
            {
                name = "M7.4 Environment Depth Texture Array Copy",
                hideFlags = HideFlags.HideAndDontSave
            };
            return true;
        }

        private static int GetDepthTextureArraySliceCount(Texture texture)
        {
            if (texture is RenderTexture renderTexture && renderTexture.dimension == TextureDimension.Tex2DArray)
                return renderTexture.volumeDepth;
            if (texture is Texture2DArray textureArray)
                return textureArray.depth;
            return 1;
        }

        private void ReleaseDepthCopyResources()
        {
            if (m_DepthCopyCommandBuffer != null)
            {
                m_DepthCopyCommandBuffer.Release();
                m_DepthCopyCommandBuffer = null;
            }

            if (m_DepthCopyMaterial != null)
            {
                Destroy(m_DepthCopyMaterial);
                m_DepthCopyMaterial = null;
            }

            ReleaseDepthCopyTexture();
        }

        private void ReleaseDepthCopyTexture()
        {
            if (m_DepthCopyTexture == null)
                return;

            m_DepthCopyTexture.Release();
            Destroy(m_DepthCopyTexture);
            m_DepthCopyTexture = null;
        }

        private static bool TrySelectHighestConfidenceActiveTarget(
            IReadOnlyList<StableTargetSnapshot> stableTargets,
            out StableTargetSnapshot selectedTarget)
        {
            selectedTarget = default;
            bool found = false;
            for (int i = 0; i < stableTargets.Count; i++)
            {
                StableTargetSnapshot candidate = stableTargets[i];
                if (candidate.State != StableTargetState.Active ||
                    (found && candidate.Confidence <= selectedTarget.Confidence))
                {
                    continue;
                }

                selectedTarget = candidate;
                found = true;
            }

            return found;
        }

        private void ShowMarker(Vector3 worldPosition)
        {
            EnsureMarker();
            m_Marker.transform.position = worldPosition;
            m_Marker.SetActive(true);
        }

        private void HideMarker()
        {
            if (m_Marker != null)
                m_Marker.SetActive(false);
        }

        private void EnsureMarker()
        {
            if (m_Marker != null)
                return;

            m_Marker = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            m_Marker.name = "M7.4 World Target Marker";
            m_Marker.transform.localScale = Vector3.one * MarkerDiameterMeters;
            Collider markerCollider = m_Marker.GetComponent<Collider>();
            if (markerCollider != null)
                Destroy(markerCollider);

            Renderer markerRenderer = m_Marker.GetComponent<Renderer>();
            Shader markerShader = Shader.Find("Unlit/Color");
            if (markerShader != null)
            {
                m_MarkerMaterial = new Material(markerShader)
                {
                    color = Color.green
                };
                markerRenderer.material = m_MarkerMaterial;
            }
        }

        private static string Format(float value)
        {
            return value.ToString("F3", CultureInfo.InvariantCulture);
        }

        private static string FormatDiagnostic(float value)
        {
            if (float.IsNaN(value))
                return "NaN";
            if (float.IsPositiveInfinity(value))
                return "+Inf";
            if (float.IsNegativeInfinity(value))
                return "-Inf";
            return value.ToString("F6", CultureInfo.InvariantCulture);
        }
    }
}
