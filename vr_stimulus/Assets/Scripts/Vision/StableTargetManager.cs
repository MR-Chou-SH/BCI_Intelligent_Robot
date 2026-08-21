using System;
using System.Collections.Generic;
using System.Globalization;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// Minimal 2D bounding box used by the stable target layer.
    /// Coordinates are expressed in the source camera image pixel space.
    /// </summary>
    public readonly struct TargetBoundingBox
    {
        public TargetBoundingBox(float x, float y, float width, float height)
        {
            X = x;
            Y = y;
            Width = width;
            Height = height;
        }

        public float X { get; }
        public float Y { get; }
        public float Width { get; }
        public float Height { get; }

        public float XMin => X;
        public float YMin => Y;
        public float XMax => X + Width;
        public float YMax => Y + Height;
        public float Area => Math.Max(0f, Width) * Math.Max(0f, Height);

        public bool IsValid =>
            !float.IsNaN(X) && !float.IsNaN(Y) &&
            !float.IsNaN(Width) && !float.IsNaN(Height) &&
            Width > 0f && Height > 0f;

        public override string ToString()
        {
            return "(" + X.ToString("F1", CultureInfo.InvariantCulture) + "," +
                Y.ToString("F1", CultureInfo.InvariantCulture) + "," +
                Width.ToString("F1", CultureInfo.InvariantCulture) + "," +
                Height.ToString("F1", CultureInfo.InvariantCulture) + ")";
        }
    }

    /// <summary>One detector observation for one inference result.</summary>
    public readonly struct TargetDetection2D
    {
        public TargetDetection2D(
            string className,
            float confidence,
            TargetBoundingBox bbox,
            float frameWidth,
            float frameHeight)
        {
            ClassName = className ?? string.Empty;
            Confidence = confidence;
            Bbox = bbox;
            FrameWidth = frameWidth;
            FrameHeight = frameHeight;
        }

        public string ClassName { get; }
        public float Confidence { get; }
        public TargetBoundingBox Bbox { get; }
        public float FrameWidth { get; }
        public float FrameHeight { get; }

        public bool IsValid =>
            !string.IsNullOrWhiteSpace(ClassName) &&
            !float.IsNaN(Confidence) &&
            !float.IsInfinity(Confidence) &&
            Bbox.IsValid &&
            FrameWidth > 0f &&
            FrameHeight > 0f;
    }

    public enum StableTargetState
    {
        Active,
        TemporarilyMissing,
        Lost
    }

    /// <summary>
    /// Stable target record exposed to later visualization and BCI binding layers.
    /// first_seen and last_seen use the same monotonic seconds clock supplied to Update.
    /// </summary>
    public readonly struct StableTargetSnapshot
    {
        public StableTargetSnapshot(
            string targetId,
            string className,
            float confidence,
            TargetBoundingBox bbox,
            double firstSeen,
            double lastSeen,
            StableTargetState state)
        {
            TargetId = targetId;
            ClassName = className;
            Confidence = confidence;
            Bbox = bbox;
            FirstSeen = firstSeen;
            LastSeen = lastSeen;
            State = state;
        }

        public string TargetId { get; }
        public string ClassName { get; }
        public float Confidence { get; }
        public TargetBoundingBox Bbox { get; }
        public double FirstSeen { get; }
        public double LastSeen { get; }
        public StableTargetState State { get; }
    }

    /// <summary>
    /// Small, deterministic multi-instance matcher for low-rate 2D detections.
    /// It deliberately has no prediction model, appearance embedding, or tracker dependency.
    /// </summary>
    public sealed class StableTargetManager
    {
        public const float DefaultMinimumIoU = 0.10f;
        public const float DefaultMaximumNormalizedCenterDistance = 0.12f;
        public const double DefaultMissingTimeoutSeconds = 1.5d;

        private sealed class TargetRecord
        {
            public string TargetId;
            public string ClassName;
            public float Confidence;
            public TargetBoundingBox Bbox;
            public double FirstSeen;
            public double LastSeen;
            public StableTargetState State;
            public float FrameWidth;
            public float FrameHeight;
        }

        private readonly struct MatchCandidate
        {
            public MatchCandidate(int targetIndex, int detectionIndex, float score)
            {
                TargetIndex = targetIndex;
                DetectionIndex = detectionIndex;
                Score = score;
            }

            public int TargetIndex { get; }
            public int DetectionIndex { get; }
            public float Score { get; }
        }

        private readonly List<TargetRecord> m_Targets = new List<TargetRecord>();
        private readonly float m_MinimumIoU;
        private readonly float m_MaximumNormalizedCenterDistance;
        private readonly double m_MissingTimeoutSeconds;
        private int m_NextTargetNumber = 1;
        private double m_LastUpdateTime = double.NegativeInfinity;

        public StableTargetManager(
            float minimumIoU = DefaultMinimumIoU,
            float maximumNormalizedCenterDistance = DefaultMaximumNormalizedCenterDistance,
            double missingTimeoutSeconds = DefaultMissingTimeoutSeconds)
        {
            if (minimumIoU < 0f || minimumIoU > 1f)
                throw new ArgumentOutOfRangeException(nameof(minimumIoU));
            if (maximumNormalizedCenterDistance <= 0f)
                throw new ArgumentOutOfRangeException(nameof(maximumNormalizedCenterDistance));
            if (missingTimeoutSeconds <= 0d)
                throw new ArgumentOutOfRangeException(nameof(missingTimeoutSeconds));

            m_MinimumIoU = minimumIoU;
            m_MaximumNormalizedCenterDistance = maximumNormalizedCenterDistance;
            m_MissingTimeoutSeconds = missingTimeoutSeconds;
        }

        public double MissingTimeoutSeconds => m_MissingTimeoutSeconds;

        /// <summary>
        /// Applies one complete detector result and returns current active/missing targets.
        /// Lost records remain queryable for diagnostics but are excluded from this result.
        /// </summary>
        public IReadOnlyList<StableTargetSnapshot> Update(
            IReadOnlyList<TargetDetection2D> detections,
            double monotonicSeconds)
        {
            if (detections == null)
                throw new ArgumentNullException(nameof(detections));
            if (double.IsNaN(monotonicSeconds) || double.IsInfinity(monotonicSeconds))
                throw new ArgumentOutOfRangeException(nameof(monotonicSeconds));
            if (monotonicSeconds < m_LastUpdateTime)
                throw new ArgumentException("Update time must be monotonic.", nameof(monotonicSeconds));

            m_LastUpdateTime = monotonicSeconds;
            MarkUnmatchedTargetsAsMissingOrLost(monotonicSeconds);

            var candidates = new List<MatchCandidate>();
            for (int targetIndex = 0; targetIndex < m_Targets.Count; targetIndex++)
            {
                TargetRecord target = m_Targets[targetIndex];
                if (target.State == StableTargetState.Lost)
                    continue;

                for (int detectionIndex = 0; detectionIndex < detections.Count; detectionIndex++)
                {
                    TargetDetection2D detection = detections[detectionIndex];
                    if (!detection.IsValid || !string.Equals(target.ClassName, detection.ClassName, StringComparison.Ordinal))
                        continue;

                    float iou = CalculateIoU(target.Bbox, detection.Bbox);
                    float centerDistance = CalculateNormalizedCenterDistance(target, detection);
                    if (iou < m_MinimumIoU && centerDistance > m_MaximumNormalizedCenterDistance)
                        continue;

                    // IoU is the primary signal; center distance breaks ties when
                    // two same-class instances are close but non-overlapping.
                    float score = (iou * 2f) + (1f - Math.Min(centerDistance, 1f));
                    candidates.Add(new MatchCandidate(targetIndex, detectionIndex, score));
                }
            }

            candidates.Sort(CompareCandidates);
            var matchedTargets = new HashSet<int>();
            var matchedDetections = new HashSet<int>();
            foreach (MatchCandidate candidate in candidates)
            {
                if (!matchedTargets.Add(candidate.TargetIndex) || !matchedDetections.Add(candidate.DetectionIndex))
                    continue;

                ApplyDetection(m_Targets[candidate.TargetIndex], detections[candidate.DetectionIndex], monotonicSeconds);
            }

            for (int detectionIndex = 0; detectionIndex < detections.Count; detectionIndex++)
            {
                if (matchedDetections.Contains(detectionIndex) || !detections[detectionIndex].IsValid)
                    continue;

                CreateTarget(detections[detectionIndex], monotonicSeconds);
            }

            return GetCurrentTargets();
        }

        public bool TryGetTarget(string targetId, out StableTargetSnapshot snapshot)
        {
            for (int i = 0; i < m_Targets.Count; i++)
            {
                if (!string.Equals(m_Targets[i].TargetId, targetId, StringComparison.Ordinal))
                    continue;

                snapshot = CreateSnapshot(m_Targets[i]);
                return true;
            }

            snapshot = default(StableTargetSnapshot);
            return false;
        }

        public IReadOnlyList<StableTargetSnapshot> GetCurrentTargets()
        {
            var snapshots = new List<StableTargetSnapshot>();
            for (int i = 0; i < m_Targets.Count; i++)
            {
                if (m_Targets[i].State != StableTargetState.Lost)
                    snapshots.Add(CreateSnapshot(m_Targets[i]));
            }

            return snapshots;
        }

        private void MarkUnmatchedTargetsAsMissingOrLost(double monotonicSeconds)
        {
            for (int i = 0; i < m_Targets.Count; i++)
            {
                TargetRecord target = m_Targets[i];
                if (target.State == StableTargetState.Lost)
                    continue;

                double age = monotonicSeconds - target.LastSeen;
                target.State = age > m_MissingTimeoutSeconds
                    ? StableTargetState.Lost
                    : StableTargetState.TemporarilyMissing;
            }
        }

        private void ApplyDetection(TargetRecord target, TargetDetection2D detection, double monotonicSeconds)
        {
            target.ClassName = detection.ClassName;
            target.Confidence = detection.Confidence;
            target.Bbox = detection.Bbox;
            target.FrameWidth = detection.FrameWidth;
            target.FrameHeight = detection.FrameHeight;
            target.LastSeen = monotonicSeconds;
            target.State = StableTargetState.Active;
        }

        private void CreateTarget(TargetDetection2D detection, double monotonicSeconds)
        {
            m_Targets.Add(new TargetRecord
            {
                TargetId = "target-" + m_NextTargetNumber++.ToString("D4"),
                ClassName = detection.ClassName,
                Confidence = detection.Confidence,
                Bbox = detection.Bbox,
                FirstSeen = monotonicSeconds,
                LastSeen = monotonicSeconds,
                State = StableTargetState.Active,
                FrameWidth = detection.FrameWidth,
                FrameHeight = detection.FrameHeight
            });
        }

        private float CalculateNormalizedCenterDistance(TargetRecord target, TargetDetection2D detection)
        {
            float frameWidth = Math.Max(Math.Max(target.FrameWidth, target.FrameHeight),
                Math.Max(detection.FrameWidth, detection.FrameHeight));
            if (frameWidth <= 0f)
                return float.MaxValue;

            float targetCenterX = (target.Bbox.XMin + target.Bbox.XMax) * 0.5f;
            float targetCenterY = (target.Bbox.YMin + target.Bbox.YMax) * 0.5f;
            float detectionCenterX = (detection.Bbox.XMin + detection.Bbox.XMax) * 0.5f;
            float detectionCenterY = (detection.Bbox.YMin + detection.Bbox.YMax) * 0.5f;
            float deltaX = targetCenterX - detectionCenterX;
            float deltaY = targetCenterY - detectionCenterY;
            return (float)Math.Sqrt((deltaX * deltaX) + (deltaY * deltaY)) / frameWidth;
        }

        private static float CalculateIoU(TargetBoundingBox first, TargetBoundingBox second)
        {
            float intersectionWidth = Math.Max(0f, Math.Min(first.XMax, second.XMax) - Math.Max(first.XMin, second.XMin));
            float intersectionHeight = Math.Max(0f, Math.Min(first.YMax, second.YMax) - Math.Max(first.YMin, second.YMin));
            float intersection = intersectionWidth * intersectionHeight;
            float union = first.Area + second.Area - intersection;
            return union <= 0f ? 0f : intersection / union;
        }

        private static int CompareCandidates(MatchCandidate left, MatchCandidate right)
        {
            int scoreComparison = right.Score.CompareTo(left.Score);
            if (scoreComparison != 0)
                return scoreComparison;
            int targetComparison = left.TargetIndex.CompareTo(right.TargetIndex);
            return targetComparison != 0 ? targetComparison : left.DetectionIndex.CompareTo(right.DetectionIndex);
        }

        private static StableTargetSnapshot CreateSnapshot(TargetRecord target)
        {
            return new StableTargetSnapshot(
                target.TargetId,
                target.ClassName,
                target.Confidence,
                target.Bbox,
                target.FirstSeen,
                target.LastSeen,
                target.State);
        }
    }
}
