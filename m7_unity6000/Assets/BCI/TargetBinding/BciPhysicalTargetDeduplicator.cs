using System;
using System.Collections.Generic;
using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// Conservative presentation-side duplicate gate. It removes only target
    /// tracks with the same semantic label, strongly-overlapping image boxes,
    /// and nearly identical world anchors. It does not alter StableTargetManager.
    /// </summary>
    public static class BciPhysicalTargetDeduplicator
    {
        public const float MinimumBoundingBoxIoU = 0.60f;
        public const float MaximumWorldDistanceMeters = 0.08f;

        public static IReadOnlyList<StableWorldAnchorSnapshot> Select(
            IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            if (candidates == null)
                throw new ArgumentNullException(nameof(candidates));

            var ordered = new List<StableWorldAnchorSnapshot>(candidates);
            ordered.Sort(CompareWinnerPriority);

            var survivors = new List<StableWorldAnchorSnapshot>(ordered.Count);
            var seenTargetIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < ordered.Count; index++)
            {
                StableWorldAnchorSnapshot candidate = ordered[index];
                if (!seenTargetIds.Add(candidate.TargetId))
                    continue;

                bool duplicate = false;
                for (int survivorIndex = 0; survivorIndex < survivors.Count; survivorIndex++)
                {
                    if (AreLikelySamePhysicalObject(candidate, survivors[survivorIndex]))
                    {
                        duplicate = true;
                        break;
                    }
                }

                if (!duplicate)
                    survivors.Add(candidate);
            }

            return survivors;
        }

        public static bool AreLikelySamePhysicalObject(
            StableWorldAnchorSnapshot first,
            StableWorldAnchorSnapshot second)
        {
            if (string.Equals(first.TargetId, second.TargetId, StringComparison.Ordinal))
                return true;
            if (!string.Equals(first.ClassName, second.ClassName, StringComparison.OrdinalIgnoreCase))
                return false;
            if (!first.Bbox.IsValid || !second.Bbox.IsValid)
                return false;
            if (Vector3.Distance(first.WorldPosition, second.WorldPosition) > MaximumWorldDistanceMeters)
                return false;

            return CalculateIoU(first.Bbox, second.Bbox) >= MinimumBoundingBoxIoU;
        }

        private static int CompareWinnerPriority(
            StableWorldAnchorSnapshot left,
            StableWorldAnchorSnapshot right)
        {
            int byState = StatePriority(right.State).CompareTo(StatePriority(left.State));
            if (byState != 0)
                return byState;

            double leftMaturity = Math.Max(0d, left.LastSeen - left.FirstSeen);
            double rightMaturity = Math.Max(0d, right.LastSeen - right.FirstSeen);
            int byMaturity = rightMaturity.CompareTo(leftMaturity);
            if (byMaturity != 0)
                return byMaturity;

            int byConfidence = right.Confidence.CompareTo(left.Confidence);
            if (byConfidence != 0)
                return byConfidence;

            int byFirstSeen = left.FirstSeen.CompareTo(right.FirstSeen);
            if (byFirstSeen != 0)
                return byFirstSeen;

            return string.Compare(left.TargetId, right.TargetId, StringComparison.Ordinal);
        }

        private static int StatePriority(StableTargetState state)
        {
            switch (state)
            {
                case StableTargetState.Active:
                    return 2;
                case StableTargetState.TemporarilyMissing:
                    return 1;
                default:
                    return 0;
            }
        }

        private static float CalculateIoU(TargetBoundingBox first, TargetBoundingBox second)
        {
            float intersectionMinX = Mathf.Max(first.XMin, second.XMin);
            float intersectionMinY = Mathf.Max(first.YMin, second.YMin);
            float intersectionMaxX = Mathf.Min(first.XMax, second.XMax);
            float intersectionMaxY = Mathf.Min(first.YMax, second.YMax);
            float intersectionWidth = Mathf.Max(0f, intersectionMaxX - intersectionMinX);
            float intersectionHeight = Mathf.Max(0f, intersectionMaxY - intersectionMinY);
            float intersection = intersectionWidth * intersectionHeight;
            float union = first.Area + second.Area - intersection;
            return union > Mathf.Epsilon ? intersection / union : 0f;
        }
    }
}
