// Copyright (c) Meta Platforms, Inc. and affiliates.

using System;
using System.Collections;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;
using Meta.XR;
using Meta.XR.Samples;
using UnityEngine;
using UnityEngine.Events;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    [MetaCodeSample("PassthroughCameraApiSamples-MultiObjectDetection")]
    public class DetectionManager : MonoBehaviour
    {
        [SerializeField] private PassthroughCameraAccess m_cameraAccess;

        [Header("Placement configuration")]
        [SerializeField] private DetectionSpawnMarkerAnim m_spawnMarker;

        [SerializeField] private SentisInferenceUiManager m_uiInference;
        [SerializeField] private EnvironmentRayCastSampleManager m_environmentRaycast;
        [Space(10)]
        public UnityEvent<int> OnObjectsIdentified;
        public event Action<StableWorldAnchorSnapshot> StableWorldAnchorUpdated;

        private readonly List<DetectionSpawnMarkerAnim> m_spawnedEntities = new();
        private readonly Dictionary<string, StableWorldAnchorRecord> m_stableWorldAnchors = new();
        private readonly Dictionary<string, float> m_lastStableLogTime = new();
        private BciSsvepTargetBinding m_ssvepBinding;
        private bool m_isStarted;
        internal OVRSpatialAnchor m_spatialAnchor;
        private bool m_isHeadsetTracking;

        // A world anchor is created or moved only after continuous, geometrically
        // consistent Active evidence. These values are deliberately small and explicit.
        private const double AnchorConfirmationSeconds = 0.75d;
        private const double CandidateMaximumGapSeconds = 0.9d;
        private const float CandidateAgreementMeters = 0.10f;
        private const float AnchorUpdateDistanceMeters = 0.15f;
        private const float StableLocalizationLogIntervalSeconds = 1f;

        private sealed class StableWorldAnchorRecord
        {
            public string TargetId;
            public string ClassName;
            public bool HasAnchor;
            public Vector3 WorldPosition;
            public DetectionSpawnMarkerAnim Marker;
            public Vector3 CandidatePosition;
            public double CandidateFirstSeen;
            public double CandidateLastSeen;
        }

        private void Awake()
        {
            if (m_environmentRaycast == null)
            {
                m_environmentRaycast = FindFirstObjectByType<EnvironmentRayCastSampleManager>();
            }

            if (m_uiInference != null)
            {
                m_uiInference.StableTargetsUpdated += OnStableTargetsUpdated;
            }

            StartCoroutine(UpdateSpatialAnchor());
            OVRManager.TrackingLost += OnTrackingLost;
            OVRManager.TrackingAcquired += OnTrackingAcquired;
        }

        private void OnDestroy()
        {
            if (m_uiInference != null)
            {
                m_uiInference.StableTargetsUpdated -= OnStableTargetsUpdated;
            }

            EraseSpatialAnchor();
            OVRManager.TrackingLost -= OnTrackingLost;
            OVRManager.TrackingAcquired -= OnTrackingAcquired;
        }

        private void OnTrackingLost() => m_isHeadsetTracking = false;
        private void OnTrackingAcquired() => m_isHeadsetTracking = true;

        private void Update()
        {
            if (!m_isStarted)
            {
                // Manage the Initial Ui Menu
                if (m_cameraAccess.IsPlaying)
                {
                    m_isStarted = true;
                }
            }
            else
            {
                // Press A button to spawn 3d markers
                if (InputManager.IsButtonADownOrPinchStarted())
                {
                    SpawnCurrentDetectedObjects();
                }
            }

            // Press B button to clean all markers
            if (InputManager.IsButtonBDownOrMiddleFingerPinchStarted())
            {
                CleanMarkers();
            }
        }

        private IEnumerator UpdateSpatialAnchor()
        {
            while (true)
            {
                yield return null;
                if (m_spatialAnchor == null)
                {
                    yield return CreateSpatialAnchorAndSave();
                    if (m_spatialAnchor == null)
                    {
                        continue;
                    }
                }

                if (!m_spatialAnchor.IsTracked)
                {
                    yield return RestoreSpatialAnchorTracking();
                }
            }

            IEnumerator CreateSpatialAnchorAndSave()
            {
                m_spatialAnchor = m_uiInference.ContentParent.gameObject.AddComponent<OVRSpatialAnchor>();

                // Wait for localization because SaveAnchorAsync() requires the anchor to be localized first.
                while (true)
                {
                    if (m_spatialAnchor == null)
                    {
                        // Spatial Anchor destroys itself when creation fails.
                        yield break;
                    }
                    if (m_spatialAnchor.Localized)
                    {
                        break;
                    }
                    yield return null;
                }

                // Save the anchor.
                var awaiter = m_spatialAnchor.SaveAnchorAsync().GetAwaiter();
                while (!awaiter.IsCompleted)
                {
                    yield return null;
                }
                var saveAnchorResult = awaiter.GetResult();
                if (!saveAnchorResult.Success)
                {
                    LogSpatialAnchor($"SaveAnchorAsync() failed {saveAnchorResult}", LogType.Error);
                    EraseSpatialAnchor();
                    yield break;
                }
                LogSpatialAnchor("created");
            }

            IEnumerator RestoreSpatialAnchorTracking()
            {
                // Try to restore spatial anchor tracking. If restoration fails, erase it.
                LogSpatialAnchor("tracking was lost, restoring...");
                const int numRetries = 20;
                for (int i = 0; i < numRetries; i++)
                {
                    yield return new WaitForSeconds(1f);
                    if (!m_isHeadsetTracking)
                    {
                        LogSpatialAnchor($"{nameof(m_isHeadsetTracking)} is false, retrying ({i})");
                        continue;
                    }

                    var unboundAnchors = new List<OVRSpatialAnchor.UnboundAnchor>(1);
                    var awaiter = OVRSpatialAnchor.LoadUnboundAnchorsAsync(new[]
                    {
                        m_spatialAnchor.Uuid
                    }, unboundAnchors).GetAwaiter();
                    while (!awaiter.IsCompleted)
                    {
                        yield return null;
                    }
                    var loadResult = awaiter.GetResult();
                    if (!loadResult.Success)
                    {
                        LogSpatialAnchor($"LoadUnboundAnchorsAsync() failed {loadResult.Status}, retrying ({i})", LogType.Error);
                        continue;
                    }
                    if (unboundAnchors.Count != 0)
                    {
                        LogSpatialAnchor($"LoadUnboundAnchorsAsync() unexpected count:{unboundAnchors.Count}, retrying ({i})", LogType.Error);
                        continue;
                    }
                    yield return null;
                    if (!m_spatialAnchor.IsTracked)
                    {
                        LogSpatialAnchor($"tracking is not restored, retrying ({i})");
                        continue;
                    }

                    LogSpatialAnchor("tracking was restored successfully");
                    yield break;
                }

                LogSpatialAnchor($"tracking restoration failed after {numRetries} retries", LogType.Warning);
                EraseSpatialAnchor();
            }
        }

        private void EraseSpatialAnchor()
        {
            if (m_spatialAnchor != null)
            {
                LogSpatialAnchor("EraseSpatialAnchor");
                m_spatialAnchor.EraseAnchorAsync();
                DestroyImmediate(m_spatialAnchor);
                m_spatialAnchor = null;

                CleanMarkers();
                m_uiInference.ClearAnnotations();
            }
        }

        private void CleanMarkers()
        {
            LogSpatialAnchor("CleanMarkers");
            foreach (var e in m_spawnedEntities)
            {
                Destroy(e.gameObject);
            }
            m_spawnedEntities.Clear();

            foreach (var anchor in m_stableWorldAnchors.Values)
            {
                PublishStableAnchor(anchor, StableTargetState.Lost);
                if (anchor.Marker != null)
                {
                    Destroy(anchor.Marker.gameObject);
                }
            }
            m_stableWorldAnchors.Clear();
            m_lastStableLogTime.Clear();
            OnObjectsIdentified?.Invoke(-1);
        }

        private void OnStableTargetsUpdated(
            IReadOnlyList<StableTargetSnapshot> stableTargets,
            Vector2 inputSize,
            Pose cameraPose)
        {
            if (m_environmentRaycast == null || m_cameraAccess == null || m_spawnMarker == null || m_uiInference == null)
            {
                return;
            }

            EnsureSsvepBinding();

            for (int i = 0; i < stableTargets.Count; i++)
            {
                StableTargetSnapshot target = stableTargets[i];
                if (target.State == StableTargetState.Lost)
                {
                    ReleaseStableAnchor(target);
                    continue;
                }

                if (!m_stableWorldAnchors.TryGetValue(target.TargetId, out StableWorldAnchorRecord anchor))
                {
                    if (target.State == StableTargetState.Active && TryGetWorldHit(target, inputSize, cameraPose, out var firstHit, out _))
                    {
                        anchor = new StableWorldAnchorRecord
                        {
                            TargetId = target.TargetId,
                            ClassName = target.ClassName,
                            CandidatePosition = firstHit,
                            CandidateFirstSeen = target.LastSeen,
                            CandidateLastSeen = target.LastSeen
                        };
                        m_stableWorldAnchors.Add(target.TargetId, anchor);
                        LogStable(target, "candidate", firstHit);
                    }

                    continue;
                }

                anchor.ClassName = target.ClassName;
                if (target.State == StableTargetState.TemporarilyMissing)
                {
                    LogStable(target, anchor.HasAnchor ? "hold_missing" : "hold_missing_before_anchor", anchor.HasAnchor ? anchor.WorldPosition : null);
                    continue;
                }

                if (!TryGetWorldHit(target, inputSize, cameraPose, out var worldHit, out var ray))
                {
                    LogStable(target, anchor.HasAnchor ? "hold_raycast_miss" : "candidate_raycast_miss", anchor.HasAnchor ? anchor.WorldPosition : null);
                    continue;
                }

                if (!anchor.HasAnchor)
                {
                    if (!IsContinuousCandidate(anchor, worldHit, target.LastSeen))
                    {
                        ResetCandidate(anchor, worldHit, target.LastSeen);
                        LogStable(target, "candidate_reset", worldHit);
                        continue;
                    }

                    anchor.CandidatePosition = worldHit;
                    anchor.CandidateLastSeen = target.LastSeen;

                    if (target.LastSeen - anchor.CandidateFirstSeen >= AnchorConfirmationSeconds)
                    {
                        CreateStableAnchor(anchor, target, worldHit, ray);
                    }

                    continue;
                }

                float anchorDistance = Vector3.Distance(anchor.WorldPosition, worldHit);
                if (anchorDistance <= AnchorUpdateDistanceMeters)
                {
                    ResetCandidate(anchor, worldHit, target.LastSeen);
                    LogStable(target, "hold", anchor.WorldPosition);
                    PublishStableAnchor(anchor, StableTargetState.Active);
                    continue;
                }

                if (!IsContinuousCandidate(anchor, worldHit, target.LastSeen))
                {
                    ResetCandidate(anchor, worldHit, target.LastSeen);
                    LogStable(target, "move_candidate", worldHit);
                    PublishStableAnchor(anchor, StableTargetState.Active);
                    continue;
                }

                anchor.CandidatePosition = worldHit;
                anchor.CandidateLastSeen = target.LastSeen;

                if (target.LastSeen - anchor.CandidateFirstSeen >= AnchorConfirmationSeconds)
                {
                    anchor.WorldPosition = worldHit;
                    anchor.Marker.transform.SetPositionAndRotation(worldHit, Quaternion.LookRotation(ray.direction));
                    ResetCandidate(anchor, worldHit, target.LastSeen);
                    LogStable(target, "updated", worldHit);
                    PublishStableAnchor(anchor, StableTargetState.Active);
                }
                else
                {
                    PublishStableAnchor(anchor, StableTargetState.Active);
                }
            }
        }

        private void EnsureSsvepBinding()
        {
            if (m_ssvepBinding == null)
            {
                m_ssvepBinding = GetComponent<BciSsvepTargetBinding>();
                if (m_ssvepBinding == null)
                {
                    m_ssvepBinding = gameObject.AddComponent<BciSsvepTargetBinding>();
                }
            }

            m_ssvepBinding.Initialize(this, m_uiInference.ContentParent);
        }

        private bool TryGetWorldHit(
            StableTargetSnapshot target,
            Vector2 inputSize,
            Pose cameraPose,
            out Vector3 worldHit,
            out Ray ray)
        {
            Vector2 bboxCenter = new Vector2(
                (target.Bbox.XMin + target.Bbox.XMax) * 0.5f,
                (target.Bbox.YMin + target.Bbox.YMax) * 0.5f);
            Vector2 viewportPoint = new Vector2(
                bboxCenter.x / inputSize.x,
                1f - (bboxCenter.y / inputSize.y));
            ray = m_cameraAccess.ViewportPointToRay(viewportPoint, cameraPose);
            Vector3? hit = m_environmentRaycast.Raycast(ray);
            if (hit.HasValue)
            {
                worldHit = hit.Value;
                LogStableLocalization(target, bboxCenter, viewportPoint, ray, worldHit);
                return true;
            }

            worldHit = default;
            LogStableLocalizationMiss(target, bboxCenter, viewportPoint, ray);
            return false;
        }

        private static bool IsContinuousCandidate(StableWorldAnchorRecord anchor, Vector3 worldHit, double timestamp)
        {
            return timestamp - anchor.CandidateLastSeen <= CandidateMaximumGapSeconds &&
                Vector3.Distance(anchor.CandidatePosition, worldHit) <= CandidateAgreementMeters;
        }

        private static void ResetCandidate(StableWorldAnchorRecord anchor, Vector3 worldPosition, double timestamp)
        {
            anchor.CandidatePosition = worldPosition;
            anchor.CandidateFirstSeen = timestamp;
            anchor.CandidateLastSeen = timestamp;
        }

        private void CreateStableAnchor(StableWorldAnchorRecord anchor, StableTargetSnapshot target, Vector3 worldPosition, Ray ray)
        {
            anchor.HasAnchor = true;
            anchor.WorldPosition = worldPosition;
            anchor.Marker = Instantiate(
                m_spawnMarker,
                worldPosition,
                Quaternion.LookRotation(ray.direction),
                m_uiInference.ContentParent);
            anchor.Marker.SetYoloClassName(target.ClassName);
            LogStable(target, "created", worldPosition);
            PublishStableAnchor(anchor, StableTargetState.Active);
        }

        private void ReleaseStableAnchor(StableTargetSnapshot target)
        {
            if (!m_stableWorldAnchors.TryGetValue(target.TargetId, out StableWorldAnchorRecord anchor))
            {
                return;
            }

            if (anchor.Marker != null)
            {
                Destroy(anchor.Marker.gameObject);
            }
            PublishStableAnchor(anchor, StableTargetState.Lost);
            m_stableWorldAnchors.Remove(target.TargetId);
            LogStable(target, "released", null);
        }

        private void PublishStableAnchor(StableWorldAnchorRecord anchor, StableTargetState state)
        {
            if (!anchor.HasAnchor && state != StableTargetState.Lost)
                return;

            StableWorldAnchorUpdated?.Invoke(new StableWorldAnchorSnapshot(
                anchor.TargetId,
                anchor.ClassName,
                state,
                anchor.WorldPosition));
        }

        private void LogStableLocalization(StableTargetSnapshot target, Vector2 bboxCenter, Vector2 viewportPoint, Ray ray, Vector3 worldHit)
        {
            string key = target.TargetId + ":localization";
            if (m_lastStableLogTime.TryGetValue(key, out float lastLogTime) &&
                Time.unscaledTime - lastLogTime < StableLocalizationLogIntervalSeconds)
            {
                return;
            }

            m_lastStableLogTime[key] = Time.unscaledTime;
            Debug.Log(
                "M7_STABLE_LOCALIZATION target_id=" + target.TargetId +
                " state=" + target.State +
                " class=" + target.ClassName +
                " bbox_center_model_px=" + bboxCenter.ToString("F1") +
                " viewport=" + viewportPoint.ToString("F4") +
                " ray_origin=" + ray.origin.ToString("F4") +
                " ray_direction=" + ray.direction.ToString("F4") +
                " raycast=hit world_hit_point=" + worldHit.ToString("F4"));
        }

        private void LogStableLocalizationMiss(StableTargetSnapshot target, Vector2 bboxCenter, Vector2 viewportPoint, Ray ray)
        {
            string key = target.TargetId + ":localization_miss";
            if (m_lastStableLogTime.TryGetValue(key, out float lastLogTime) &&
                Time.unscaledTime - lastLogTime < StableLocalizationLogIntervalSeconds)
            {
                return;
            }

            m_lastStableLogTime[key] = Time.unscaledTime;
            Debug.LogWarning(
                "M7_STABLE_LOCALIZATION target_id=" + target.TargetId +
                " state=" + target.State +
                " class=" + target.ClassName +
                " bbox_center_model_px=" + bboxCenter.ToString("F1") +
                " viewport=" + viewportPoint.ToString("F4") +
                " ray_origin=" + ray.origin.ToString("F4") +
                " ray_direction=" + ray.direction.ToString("F4") +
                " raycast=miss world_hit_point=unavailable");
        }

        private void LogStable(StableTargetSnapshot target, string eventName, Vector3? worldPosition)
        {
            string key = target.TargetId + ":" + eventName;
            if (m_lastStableLogTime.TryGetValue(key, out float lastLogTime) &&
                Time.unscaledTime - lastLogTime < StableLocalizationLogIntervalSeconds)
            {
                return;
            }

            m_lastStableLogTime[key] = Time.unscaledTime;
            string message =
                "M7_WORLD_ANCHOR target_id=" + target.TargetId +
                " state=" + target.State +
                " class=" + target.ClassName +
                " event=" + eventName;
            if (worldPosition.HasValue)
            {
                message += " world_point=" + worldPosition.Value.ToString("F4");
            }
            Debug.Log(message);
        }

        private static void LogSpatialAnchor(string message, LogType logType = LogType.Log)
        {
            Debug.unityLogger.Log(logType, $"{nameof(OVRSpatialAnchor)}: {message}");
        }

        /// <summary>
        /// Spwan 3d markers for the detected objects
        /// </summary>
        private void SpawnCurrentDetectedObjects()
        {
            var newCount = 0;
            foreach (SentisInferenceUiManager.BoundingBoxData box in m_uiInference.m_boxDrawn)
            {
                if (!HasExistingMarkerInBoundingBox(box))
                {
                    LogSpatialAnchor($"spawn marker {box.ClassName}");
                    var marker = Instantiate(m_spawnMarker, box.BoxRectTransform.position, box.BoxRectTransform.rotation, m_uiInference.ContentParent);
                    marker.GetComponent<DetectionSpawnMarkerAnim>().SetYoloClassName(box.ClassName);

                    m_spawnedEntities.Add(marker);
                    newCount++;
                }
            }

            // M7.5 validation fallback: while a recent detection is still held, A re-runs the
            // official ViewportPointToRay -> EnvironmentRaycast path instead of reusing an old world point.
            if (newCount == 0 && m_uiInference.TryRaycastRecentDetection(out var recentDetection, out var recentRay, out var recentHitPoint, out var recentAgeSeconds))
            {
                LogSpatialAnchor($"spawn held marker {recentDetection.ClassName} age={recentAgeSeconds:F2}s");
                var marker = Instantiate(m_spawnMarker, recentHitPoint, Quaternion.LookRotation(recentRay.direction), m_uiInference.ContentParent);
                marker.GetComponent<DetectionSpawnMarkerAnim>().SetYoloClassName(recentDetection.ClassName);
                m_spawnedEntities.Add(marker);
                newCount++;
            }
            OnObjectsIdentified?.Invoke(newCount);

            bool HasExistingMarkerInBoundingBox(SentisInferenceUiManager.BoundingBoxData box)
            {
                foreach (var marker in m_spawnedEntities)
                {
                    if (marker.GetYoloClassName() == box.ClassName)
                    {
                        var markerWorldPos = marker.transform.position;
                        Vector2 localPos = box.BoxRectTransform.InverseTransformPoint(markerWorldPos);
                        var sizeDelta = box.BoxRectTransform.sizeDelta;
                        var currentBox = new Rect(
                            -sizeDelta.x * 0.5f,
                            -sizeDelta.y * 0.5f,
                            sizeDelta.x,
                            sizeDelta.y
                        );

                        if (currentBox.Contains(localPos))
                        {
                            return true;
                        }
                    }
                }

                return false;
            }
        }
    }
}
