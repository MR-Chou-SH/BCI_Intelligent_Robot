using UnityEngine;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;

#if UNITY_ANDROID && !UNITY_EDITOR
using UnityEngine.Android;
#endif

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// M7.1 runtime-only probe for Quest passthrough camera CPU frames.
    /// It intentionally does not retain, convert, render, or persist image data.
    /// </summary>
    [RequireComponent(typeof(ARCameraManager))]
    public sealed class QuestCameraFrameSpike : MonoBehaviour
    {
        private const string HeadsetCameraPermission = "horizonos.permission.HEADSET_CAMERA";
        private const float CaptureIntervalSeconds = 1f;

        [SerializeField] private ARCameraManager m_CameraManager;

        private float m_NextCaptureTime;
        private int m_AcquiredFrameCount;
        private int m_FailedFrameCount;

#if UNITY_ANDROID && !UNITY_EDITOR
        private PermissionCallbacks m_PermissionCallbacks;
#endif

        private void Awake()
        {
            if (m_CameraManager == null)
                m_CameraManager = GetComponent<ARCameraManager>();

            // The scene builder serializes this as disabled so camera initialization only
            // happens after the privacy permission has been granted.
            if (m_CameraManager != null)
                m_CameraManager.enabled = false;
        }

        private void OnEnable()
        {
            if (m_CameraManager != null)
                m_CameraManager.frameReceived += OnCameraFrameReceived;
        }

        private void Start()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            if (Permission.HasUserAuthorizedPermission(HeadsetCameraPermission))
            {
                EnableCameraAfterPermission();
                return;
            }

            m_PermissionCallbacks = new PermissionCallbacks();
            m_PermissionCallbacks.PermissionGranted += _ => EnableCameraAfterPermission();
            m_PermissionCallbacks.PermissionDenied += _ => LogPermissionDenied("denied");
            m_PermissionCallbacks.PermissionDeniedAndDontAskAgain += _ => LogPermissionDenied("denied_dont_ask_again");
            Debug.Log("M7CAM permission_request permission=" + HeadsetCameraPermission, this);
            Permission.RequestUserPermission(HeadsetCameraPermission, m_PermissionCallbacks);
#else
            Debug.LogWarning("M7CAM unsupported_platform requires_quest_android_runtime", this);
#endif
        }

        private void OnDisable()
        {
            if (m_CameraManager != null)
                m_CameraManager.frameReceived -= OnCameraFrameReceived;
        }

        private void EnableCameraAfterPermission()
        {
            if (m_CameraManager == null)
            {
                Debug.LogError("M7CAM camera_manager_missing", this);
                return;
            }

            m_CameraManager.enabled = true;
            Debug.Log("M7CAM camera_manager_enabled permission=" + HeadsetCameraPermission, this);
        }

        private void LogPermissionDenied(string result)
        {
            Debug.LogError("M7CAM permission_result=" + result + " permission=" + HeadsetCameraPermission, this);
        }

        private void OnCameraFrameReceived(ARCameraFrameEventArgs frameEvent)
        {
            if (Time.unscaledTime < m_NextCaptureTime)
                return;

            m_NextCaptureTime = Time.unscaledTime + CaptureIntervalSeconds;
            TryLogCpuImage(frameEvent.timestampNs);
        }

        private void TryLogCpuImage(long? frameTimestampNs)
        {
            if (m_CameraManager == null || !m_CameraManager.TryAcquireLatestCpuImage(out XRCpuImage image))
            {
                m_FailedFrameCount++;
                Debug.LogWarning("M7CAM cpu_image_acquisition_failed failed_count=" + m_FailedFrameCount, this);
                return;
            }

            using (image)
            {
                m_AcquiredFrameCount++;
                string intrinsics = DescribeIntrinsics();
                string imageTimestamp = DescribeImageTimestamp(image.timestamp);
                string frameTimestamp = DescribeFrameTimestamp(frameTimestampNs);

                Debug.Log(
                    "M7CAM cpu_image_acquired success_count=" + m_AcquiredFrameCount +
                    " width=" + image.width +
                    " height=" + image.height +
                    " format=" + image.format +
                    " plane_count=" + image.planeCount +
                    " " + imageTimestamp +
                    " " + frameTimestamp +
                    " " + intrinsics,
                    this);
            }
        }

        private static string DescribeImageTimestamp(double timestampSeconds)
        {
            // XRCpuImage.timestamp is a non-nullable AR Foundation value. This label
            // distinguishes a provider-supplied zero from a missing nullable frame timestamp.
            return "image_timestamp_property_available=true" +
                " image_timestamp_s=" + timestampSeconds.ToString("F6", System.Globalization.CultureInfo.InvariantCulture) +
                " image_timestamp_is_zero=" + (timestampSeconds == 0d ? "true" : "false");
        }

        private static string DescribeFrameTimestamp(long? timestampNs)
        {
            if (!timestampNs.HasValue)
                return "frame_timestamp_available=false frame_timestamp_ns=unavailable";

            return "frame_timestamp_available=true" +
                " frame_timestamp_ns=" + timestampNs.Value +
                " frame_timestamp_is_zero=" + (timestampNs.Value == 0L ? "true" : "false");
        }

        private string DescribeIntrinsics()
        {
            if (m_CameraManager != null && m_CameraManager.TryGetIntrinsics(out XRCameraIntrinsics intrinsics))
            {
                return "intrinsics=available" +
                    " focal_length_px=(" + intrinsics.focalLength.x.ToString("F3", System.Globalization.CultureInfo.InvariantCulture) +
                    "," + intrinsics.focalLength.y.ToString("F3", System.Globalization.CultureInfo.InvariantCulture) + ")" +
                    " principal_point_px=(" + intrinsics.principalPoint.x.ToString("F3", System.Globalization.CultureInfo.InvariantCulture) +
                    "," + intrinsics.principalPoint.y.ToString("F3", System.Globalization.CultureInfo.InvariantCulture) + ")" +
                    " intrinsics_resolution=(" + intrinsics.resolution.x + "," + intrinsics.resolution.y + ")";
            }

            return "intrinsics=unavailable";
        }
    }
}
