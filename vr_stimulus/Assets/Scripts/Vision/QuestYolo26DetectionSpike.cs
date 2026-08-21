using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;
using Unity.Burst;
using Unity.Collections;
using Unity.InferenceEngine;
using UnityEngine;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.ARSubsystems;
using UnityEngine.XR;
using Unity.Jobs;
using ProviderXRStats = UnityEngine.XR.Provider.XRStats;
using Stopwatch = System.Diagnostics.Stopwatch;

#if UNITY_ANDROID && !UNITY_EDITOR
using UnityEngine.Android;
#endif

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// M7.2 runtime-only probe: Quest CPU camera image to YOLO26n detections.
    /// It intentionally has no target tracking, world mapping, persistence, or BCI binding.
    /// </summary>
    [RequireComponent(typeof(ARCameraManager))]
    public sealed class QuestYolo26DetectionSpike : MonoBehaviour
    {
        private const string HeadsetCameraPermission = "horizonos.permission.HEADSET_CAMERA";
        private const int ModelInputSize = 640;
        // M7.2c backend-isolation gate: keep the detector low-rate while moving
        // both model execution and input preparation off the XR rendering GPU.
        private const float InferenceIntervalSeconds = 0.5f;
        private const string AppCpuFrameTimeStat = "perfmetrics.appcputime";
        private const string AppGpuFrameTimeStat = "perfmetrics.appgputime";
        private const string CompositorDroppedFramesStat = "appstats.compositordroppedframes";

        private static readonly string[] CocoClassNames =
        {
            "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
            "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
            "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
            "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
            "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
            "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant", "bed",
            "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave", "oven",
            "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush"
        };

        private static readonly HashSet<int> DesktopClassIds = new HashSet<int>
        {
            39, // bottle
            41, // cup
            62, // laptop
            64, // mouse
            66, // keyboard
            67, // cell phone
            73  // book
        };

        [SerializeField] private ARCameraManager m_CameraManager;
        [SerializeField] private ModelAsset m_ModelAsset;
        [SerializeField, Range(0.1f, 0.9f)] private float m_ConfidenceThreshold = 0.25f;

        private Worker m_Worker;
        private Tensor<float> m_InputTensor;
        private NativeArray<byte> m_ConversionBuffer;
        private JobHandle m_CpuPreprocessJob;
        private bool m_CpuPreprocessInFlight;
        private double m_CpuPreprocessStartedMonotonicSeconds;
        private double m_PendingCameraConversionMilliseconds;
        private double m_PendingCpuTensorPreparationMilliseconds;
        private double m_PendingInputUploadMilliseconds;
        private double m_NextInferenceTime;
        private int m_StartedInferenceCount;
        private int m_CompletedInferenceCount;
        private bool m_InferenceInFlight;
        private bool m_CameraFrameRequested;
        private int m_CameraFrameRequestFrame;
        private int m_LastResultConsumedFrame = -1;
        private Tensor<float> m_PendingOutput;
        private double m_PendingReceiveMonotonicSeconds;
        private double m_PendingReadbackRequestMonotonicSeconds;
        private double m_PendingCameraAndPreprocessMilliseconds;
        private double m_PendingScheduleMilliseconds;
        private double m_PendingReadbackRequestMilliseconds;
        private int m_PendingSourceWidth;
        private int m_PendingSourceHeight;
        private int m_PendingInferenceIndex;
        private readonly List<XRDisplaySubsystem> m_XrDisplays = new List<XRDisplaySubsystem>();
        private bool m_Disposed;

#if UNITY_ANDROID && !UNITY_EDITOR
        private PermissionCallbacks m_PermissionCallbacks;
#endif

        /// <summary>Editor-only scene setup assigns the imported YOLO26n ModelAsset.</summary>
        public void ConfigureModelAsset(ModelAsset modelAsset)
        {
            m_ModelAsset = modelAsset;
        }

        private void Awake()
        {
            if (m_CameraManager == null)
                m_CameraManager = GetComponent<ARCameraManager>();

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
            if (!InitializeModel())
                return;

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
            Debug.Log("M7YOLO permission_request permission=" + HeadsetCameraPermission, this);
            Permission.RequestUserPermission(HeadsetCameraPermission, m_PermissionCallbacks);
#else
            Debug.LogWarning("M7YOLO unsupported_platform requires_quest_android_runtime", this);
#endif
        }

        private void OnDisable()
        {
            if (m_CameraManager != null)
                m_CameraManager.frameReceived -= OnCameraFrameReceived;
        }

        private void OnDestroy()
        {
            m_Disposed = true;
            m_PendingOutput = null;
            if (m_CpuPreprocessInFlight)
                m_CpuPreprocessJob.Complete();

            m_Worker?.Dispose();
            m_InputTensor?.Dispose();

            if (m_ConversionBuffer.IsCreated)
                m_ConversionBuffer.Dispose();

        }

        private bool InitializeModel()
        {
            if (m_ModelAsset == null)
            {
                Debug.LogError("M7YOLO model_asset_missing expected=yolo26n.onnx", this);
                return false;
            }

            try
            {
                m_Worker = new Worker(ModelLoader.Load(m_ModelAsset), BackendType.CPU);
                m_InputTensor = new Tensor<float>(new TensorShape(1, 3, ModelInputSize, ModelInputSize));
                Debug.Log("M7YOLO model_initialized backend=CPU input_shape=(1,3," + ModelInputSize + "," + ModelInputSize + ") output_contract=(1,300,6)", this);
                return true;
            }
            catch (Exception exception)
            {
                Debug.LogError("M7YOLO model_initialization_failed backend=CPU exception=" + exception, this);
                return false;
            }
        }

        private void EnableCameraAfterPermission()
        {
            if (m_CameraManager == null)
            {
                Debug.LogError("M7YOLO camera_manager_missing", this);
                return;
            }

            m_CameraManager.enabled = true;
            Debug.Log("M7YOLO camera_manager_enabled permission=" + HeadsetCameraPermission, this);
        }

        private void LogPermissionDenied(string result)
        {
            Debug.LogError("M7YOLO permission_result=" + result + " permission=" + HeadsetCameraPermission, this);
        }

        private void OnCameraFrameReceived(ARCameraFrameEventArgs _)
        {
            if (m_Disposed || m_InferenceInFlight || m_CameraFrameRequested ||
                Time.realtimeSinceStartupAsDouble < m_NextInferenceTime)
                return;

            m_NextInferenceTime = Time.realtimeSinceStartupAsDouble + InferenceIntervalSeconds;
            m_CameraFrameRequested = true;
            m_CameraFrameRequestFrame = Time.frameCount;
        }

        private void Update()
        {
            ConsumeCompletedReadback();

            if (m_CpuPreprocessInFlight)
            {
                if (!m_CpuPreprocessJob.IsCompleted)
                    return;

                ScheduleCpuInferenceFromPreparedInput();
                return;
            }

            // Do not do image conversion and worker scheduling in ARCameraManager's
            // frameReceived callback, nor in the same frame that consumes a result.
            if (!m_CameraFrameRequested || m_InferenceInFlight ||
                Time.frameCount <= m_CameraFrameRequestFrame ||
                Time.frameCount == m_LastResultConsumedFrame)
                return;

            m_CameraFrameRequested = false;
            StartCpuInputPreparation();
        }

        private void StartCpuInputPreparation()
        {
            if (m_CameraManager == null || !m_CameraManager.TryAcquireLatestCpuImage(out XRCpuImage image))
            {
                Debug.LogWarning("M7YOLO cpu_image_acquisition_failed", this);
                return;
            }

            double receiveMonotonicSeconds = Time.realtimeSinceStartupAsDouble;
            Stopwatch cameraConversionStopwatch = Stopwatch.StartNew();

            try
            {
                using (image)
                {
                    EnsureInputResources(image);
                    ConvertCpuImageToRgbaBuffer(image);
                    cameraConversionStopwatch.Stop();

                    int pixelCount = ModelInputSize * ModelInputSize;
                    // Inference Engine's supported CPU path: pin the CPU-backed
                    // input tensor and make the Burst job its dependency. This
                    // avoids both TextureConverter GPU work and a second 4.9 MB
                    // CPU upload copy before inference.
                    CPUTensorData cpuInput = CPUTensorData.Pin(m_InputTensor);
                    m_CpuPreprocessJob = new RgbaToNchwJob
                    {
                        Rgba = m_ConversionBuffer,
                        Nchw = cpuInput.array.GetNativeArrayHandle<float>(),
                        PixelCount = pixelCount
                    }.Schedule(pixelCount, 256);
                    cpuInput.fence = m_CpuPreprocessJob;
                    m_CpuPreprocessInFlight = true;
                    m_CpuPreprocessStartedMonotonicSeconds = Time.realtimeSinceStartupAsDouble;
                    m_PendingReceiveMonotonicSeconds = receiveMonotonicSeconds;
                    m_PendingCameraConversionMilliseconds = cameraConversionStopwatch.Elapsed.TotalMilliseconds;
                    m_PendingSourceWidth = image.width;
                    m_PendingSourceHeight = image.height;
                }
            }
            catch (Exception exception)
            {
                m_CpuPreprocessInFlight = false;
                Debug.LogError("M7YOLO cpu_input_preparation_failed exception=" + exception, this);
            }
        }

        private void ScheduleCpuInferenceFromPreparedInput()
        {
            try
            {
                // IsCompleted was checked in Update, so Complete does not wait for
                // the conversion job on Unity's main thread.
                m_CpuPreprocessJob.Complete();
                m_CpuPreprocessInFlight = false;
                m_PendingCpuTensorPreparationMilliseconds =
                    (Time.realtimeSinceStartupAsDouble - m_CpuPreprocessStartedMonotonicSeconds) * 1000.0;

                Stopwatch scheduleStopwatch = Stopwatch.StartNew();
                m_Worker.Schedule(m_InputTensor);
                scheduleStopwatch.Stop();

                Tensor<float> output = m_Worker.PeekOutput() as Tensor<float>;
                if (output == null)
                    throw new InvalidOperationException("YOLO26n output was not a float tensor.");

                Stopwatch readbackRequestStopwatch = Stopwatch.StartNew();
                output.ReadbackRequest();
                readbackRequestStopwatch.Stop();

                m_InferenceInFlight = true;
                m_StartedInferenceCount++;
                m_PendingOutput = output;
                m_PendingReadbackRequestMonotonicSeconds = Time.realtimeSinceStartupAsDouble;
                m_PendingCameraAndPreprocessMilliseconds = m_PendingCameraConversionMilliseconds;
                m_PendingInputUploadMilliseconds = 0.0;
                m_PendingScheduleMilliseconds = scheduleStopwatch.Elapsed.TotalMilliseconds;
                m_PendingReadbackRequestMilliseconds = readbackRequestStopwatch.Elapsed.TotalMilliseconds;
                m_PendingInferenceIndex = m_StartedInferenceCount;
                Debug.Log(
                    "M7YOLO inference_scheduled index=" + m_PendingInferenceIndex +
                    " backend=CPU input_backend=" + m_InputTensor.dataOnBackend.backendType +
                    " output_backend=" + output.dataOnBackend.backendType +
                    " camera_conversion_ms=" + m_PendingCameraConversionMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                    " cpu_tensor_prepare_ms=" + m_PendingCpuTensorPreparationMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                    " input_upload_ms=" + m_PendingInputUploadMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                    " input_path=cpu_direct_pinned_tensor" +
                    " worker_schedule_ms=" + m_PendingScheduleMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                    " readback_request_ms=" + m_PendingReadbackRequestMilliseconds.ToString("F2", CultureInfo.InvariantCulture),
                    this);
            }
            catch (Exception exception)
            {
                m_CpuPreprocessInFlight = false;
                m_InferenceInFlight = false;
                m_PendingOutput = null;
                Debug.LogError("M7YOLO inference_schedule_failed backend=CPU exception=" + exception, this);
            }
        }

        private void EnsureInputResources(XRCpuImage image)
        {
            XRCpuImage.ConversionParams conversionParams = CreateConversionParams(image);
            int requiredBytes = image.GetConvertedDataSize(conversionParams);
            if (!m_ConversionBuffer.IsCreated || m_ConversionBuffer.Length != requiredBytes)
            {
                if (m_ConversionBuffer.IsCreated)
                    m_ConversionBuffer.Dispose();

                m_ConversionBuffer = new NativeArray<byte>(requiredBytes, Allocator.Persistent, NativeArrayOptions.UninitializedMemory);
            }

        }

        private void ConvertCpuImageToRgbaBuffer(XRCpuImage image)
        {
            XRCpuImage.ConversionParams conversionParams = CreateConversionParams(image);
            image.Convert(conversionParams, m_ConversionBuffer);
        }

        private static XRCpuImage.ConversionParams CreateConversionParams(XRCpuImage image)
        {
            return new XRCpuImage.ConversionParams
            {
                inputRect = new RectInt(0, 0, image.width, image.height),
                outputDimensions = new Vector2Int(ModelInputSize, ModelInputSize),
                outputFormat = TextureFormat.RGBA32,
                transformation = XRCpuImage.Transformation.None
            };
        }

        private void ConsumeCompletedReadback()
        {
            if (!m_InferenceInFlight || m_PendingOutput == null || !m_PendingOutput.IsReadbackRequestDone())
                return;

            try
            {
                double resultAvailableMilliseconds =
                    (Time.realtimeSinceStartupAsDouble - m_PendingReadbackRequestMonotonicSeconds) * 1000.0;
                Stopwatch resultConsumeStopwatch = Stopwatch.StartNew();

                // Sentis documents this as immediate after IsReadbackRequestDone().
                using (Tensor<float> cpuOutput = m_PendingOutput.ReadbackAndClone())
                {
                    if (m_Disposed)
                        return;

                    float[] values = cpuOutput.DownloadToArray();
                    string detections = DescribeDesktopDetections(values, m_PendingSourceWidth, m_PendingSourceHeight);
                    resultConsumeStopwatch.Stop();
                    m_CompletedInferenceCount++;
                    Debug.Log(
                        "M7YOLO inference_completed index=" + m_PendingInferenceIndex +
                        " completed_count=" + m_CompletedInferenceCount +
                        " receive_monotonic_s=" + m_PendingReceiveMonotonicSeconds.ToString("F6", CultureInfo.InvariantCulture) +
                        " source_size=(" + m_PendingSourceWidth + "," + m_PendingSourceHeight + ")" +
                        " model_input_size=(" + ModelInputSize + "," + ModelInputSize + ")" +
                        " camera_conversion_ms=" + m_PendingCameraAndPreprocessMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                        " cpu_tensor_prepare_ms=" + m_PendingCpuTensorPreparationMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                        " input_upload_ms=" + m_PendingInputUploadMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                        " worker_schedule_ms=" + m_PendingScheduleMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                        " result_available_ms=" + resultAvailableMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                        " result_consume_ms=" + resultConsumeStopwatch.Elapsed.TotalMilliseconds.ToString("F2", CultureInfo.InvariantCulture) +
                        " " + DescribeFrameTiming() +
                        " " + detections,
                        this);
                }
            }
            catch (Exception exception)
            {
                Debug.LogError("M7YOLO inference_readback_failed index=" + m_PendingInferenceIndex + " exception=" + exception, this);
            }
            finally
            {
                m_PendingOutput = null;
                m_InferenceInFlight = false;
                m_LastResultConsumedFrame = Time.frameCount;
            }
        }

        private string DescribeFrameTiming()
        {
            StringBuilder builder = new StringBuilder("unity_frame_delta_ms=")
                .Append((Time.deltaTime * 1000f).ToString("F2", CultureInfo.InvariantCulture))
                .Append(" unity_smooth_frame_ms=")
                .Append((Time.smoothDeltaTime * 1000f).ToString("F2", CultureInfo.InvariantCulture));

            m_XrDisplays.Clear();
            SubsystemManager.GetSubsystems(m_XrDisplays);
            if (m_XrDisplays.Count == 0)
                return builder.Append(" xr_stats_available=false").ToString();

            XRDisplaySubsystem display = m_XrDisplays[0];
            bool appCpuAvailable = ProviderXRStats.TryGetStat(display, AppCpuFrameTimeStat, out float appCpuMilliseconds);
            bool appGpuAvailable = ProviderXRStats.TryGetStat(display, AppGpuFrameTimeStat, out float appGpuMilliseconds);
            bool droppedFramesAvailable = ProviderXRStats.TryGetStat(display, CompositorDroppedFramesStat, out float droppedFrames);
            builder.Append(" xr_stats_available=").Append(appCpuAvailable || appGpuAvailable || droppedFramesAvailable ? "true" : "false")
                .Append(" xr_app_cpu_ms=").Append(appCpuAvailable ? appCpuMilliseconds.ToString("F2", CultureInfo.InvariantCulture) : "unavailable")
                .Append(" xr_app_gpu_ms=").Append(appGpuAvailable ? appGpuMilliseconds.ToString("F2", CultureInfo.InvariantCulture) : "unavailable")
                .Append(" xr_compositor_dropped_frames=").Append(droppedFramesAvailable ? droppedFrames.ToString("F0", CultureInfo.InvariantCulture) : "unavailable");
            return builder.ToString();
        }

        [BurstCompile]
        private struct RgbaToNchwJob : IJobParallelFor
        {
            [ReadOnly] public NativeArray<byte> Rgba;
            [WriteOnly] public NativeArray<float> Nchw;
            public int PixelCount;

            public void Execute(int pixelIndex)
            {
                int rgbaIndex = pixelIndex * 4;
                Nchw[pixelIndex] = Rgba[rgbaIndex] / 255f;
                Nchw[PixelCount + pixelIndex] = Rgba[rgbaIndex + 1] / 255f;
                Nchw[(2 * PixelCount) + pixelIndex] = Rgba[rgbaIndex + 2] / 255f;
            }
        }

        private string DescribeDesktopDetections(float[] values, int sourceWidth, int sourceHeight)
        {
            const int ValuesPerDetection = 6;
            int detectionCount = 0;
            StringBuilder builder = new StringBuilder("desktop_detection_count=");
            StringBuilder details = new StringBuilder();

            for (int offset = 0; offset + ValuesPerDetection <= values.Length; offset += ValuesPerDetection)
            {
                float confidence = values[offset + 4];
                int classId = Mathf.RoundToInt(values[offset + 5]);
                if (confidence < m_ConfidenceThreshold || classId < 0 || classId >= CocoClassNames.Length || !DesktopClassIds.Contains(classId))
                    continue;

                float x1 = Mathf.Clamp(values[offset] * sourceWidth / ModelInputSize, 0f, sourceWidth);
                float y1 = Mathf.Clamp(values[offset + 1] * sourceHeight / ModelInputSize, 0f, sourceHeight);
                float x2 = Mathf.Clamp(values[offset + 2] * sourceWidth / ModelInputSize, 0f, sourceWidth);
                float y2 = Mathf.Clamp(values[offset + 3] * sourceHeight / ModelInputSize, 0f, sourceHeight);
                if (x2 <= x1 || y2 <= y1)
                    continue;

                if (details.Length > 0)
                    details.Append(";");

                details.Append("class=").Append(CocoClassNames[classId])
                    .Append(" confidence=").Append(confidence.ToString("F3", CultureInfo.InvariantCulture))
                    .Append(" bbox_px=(").Append(x1.ToString("F1", CultureInfo.InvariantCulture))
                    .Append(",").Append(y1.ToString("F1", CultureInfo.InvariantCulture))
                    .Append(",").Append(x2.ToString("F1", CultureInfo.InvariantCulture))
                    .Append(",").Append(y2.ToString("F1", CultureInfo.InvariantCulture)).Append(")");
                detectionCount++;
            }

            builder.Append(detectionCount);
            if (detectionCount > 0)
                builder.Append(" detections=[").Append(details).Append("]");

            return builder.ToString();
        }
    }
}
