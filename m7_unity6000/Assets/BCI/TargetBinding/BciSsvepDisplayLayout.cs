using System;
using System.Collections.Generic;
using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
    public enum BciSsvepLayoutMode
    {
        WorldSpaceExperimental,
        ViewLockedHud
    }

    /// <summary>
    /// Small deterministic display-only layout for the three fixed BCI slots.
    /// It does not assign slots or hold selection state.
    /// </summary>
    public static class BciSsvepDisplayLayout
    {
        public const float ExperimentalStimulusSizeMeters = 0.32f;
        public const float MinimumGapMeters = 0.06f;
        public const float DisplayVerticalOffsetMeters = 0.24f;
        public const float HudStimulusSizeMeters = 0.20f;
        public const float HudHorizontalSpacingMeters = 0.32f;
        public const float HudDepthMeters = 0.85f;
        public const float HudHeightMeters = 0.18f;

        public static Vector3 DefaultHudLocalCenter =>
            new Vector3(0f, HudHeightMeters, HudDepthMeters);

        private readonly struct SlotPosition
        {
            public SlotPosition(int slotIndex, float desiredRight)
            {
                SlotIndex = slotIndex;
                DesiredRight = desiredRight;
            }

            public int SlotIndex { get; }
            public float DesiredRight { get; }
        }

        /// <summary>
        /// Starts above each world anchor, then separates close displays in the
        /// current camera-right direction. Ties resolve by slot index.
        /// </summary>
        public static void CalculatePositions(
            IReadOnlyList<StableWorldAnchorSnapshot> anchors,
            IReadOnlyList<bool> slotVisible,
            Vector3 cameraPosition,
            Vector3 cameraRight,
            Vector3 cameraUp,
            Vector3[] displayPositions)
        {
            if (anchors == null)
                throw new ArgumentNullException(nameof(anchors));
            if (slotVisible == null)
                throw new ArgumentNullException(nameof(slotVisible));
            if (displayPositions == null)
                throw new ArgumentNullException(nameof(displayPositions));
            if (anchors.Count < BciTargetSlotAllocator.SlotCount ||
                slotVisible.Count < BciTargetSlotAllocator.SlotCount ||
                displayPositions.Length < BciTargetSlotAllocator.SlotCount)
                throw new ArgumentException("Three slot entries are required.");

            Vector3 right = NormalizeOrFallback(cameraRight, Vector3.right);
            Vector3 up = NormalizeOrFallback(cameraUp, Vector3.up);
            var ordered = new List<SlotPosition>(BciTargetSlotAllocator.SlotCount);
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                if (!slotVisible[slot])
                    continue;

                float desiredRight = Vector3.Dot(anchors[slot].WorldPosition - cameraPosition, right);
                ordered.Add(new SlotPosition(slot, desiredRight));
            }

            ordered.Sort((left, rightEntry) =>
            {
                int byPosition = left.DesiredRight.CompareTo(rightEntry.DesiredRight);
                return byPosition != 0 ? byPosition : left.SlotIndex.CompareTo(rightEntry.SlotIndex);
            });

            if (ordered.Count == 0)
                return;

            float minimumCenterSeparation = ExperimentalStimulusSizeMeters + MinimumGapMeters;
            var separatedRight = new float[ordered.Count];
            separatedRight[0] = ordered[0].DesiredRight;
            for (int index = 1; index < ordered.Count; index++)
            {
                separatedRight[index] = Mathf.Max(
                    ordered[index].DesiredRight,
                    separatedRight[index - 1] + minimumCenterSeparation);
            }

            float desiredMean = 0f;
            float separatedMean = 0f;
            for (int index = 0; index < ordered.Count; index++)
            {
                desiredMean += ordered[index].DesiredRight;
                separatedMean += separatedRight[index];
            }
            float recenterOffset = (desiredMean - separatedMean) / ordered.Count;

            for (int index = 0; index < ordered.Count; index++)
            {
                SlotPosition entry = ordered[index];
                Vector3 anchorPosition = anchors[entry.SlotIndex].WorldPosition;
                float lateralOffset = separatedRight[index] + recenterOffset - entry.DesiredRight;
                displayPositions[entry.SlotIndex] =
                    anchorPosition + up * DisplayVerticalOffsetMeters + right * lateralOffset;
            }
        }

        /// <summary>
        /// Calculates the fixed camera-local HUD positions without consulting
        /// world anchors. Slot order remains the canonical 0/1/2 order.
        /// </summary>
        public static void CalculateViewLockedPositions(
            Vector3 localCenter,
            float horizontalSpacing,
            Vector3[] localPositions)
        {
            if (localPositions == null || localPositions.Length < BciTargetSlotAllocator.SlotCount)
                throw new ArgumentException("Three slot entries are required.");

            float spacing = Mathf.Max(0f, horizontalSpacing);
            localPositions[0] = localCenter + Vector3.left * spacing;
            localPositions[1] = localCenter;
            localPositions[2] = localCenter + Vector3.right * spacing;
        }

        /// <summary>
        /// Calculates HUD panel positions from the current on-screen order of
        /// the stable anchors.  The array index remains the canonical SSVEP
        /// slot; only the camera-facing panel position is reordered.
        /// </summary>
        public static void CalculateViewLockedPositionsByScreenOrder(
            IReadOnlyList<StableWorldAnchorSnapshot> anchors,
            IReadOnlyList<bool> slotVisible,
            Camera camera,
            Vector3 localCenter,
            float horizontalSpacing,
            Vector3[] localPositions)
        {
            if (anchors == null || slotVisible == null || camera == null ||
                localPositions == null ||
                anchors.Count < BciTargetSlotAllocator.SlotCount ||
                slotVisible.Count < BciTargetSlotAllocator.SlotCount ||
                localPositions.Length < BciTargetSlotAllocator.SlotCount)
                throw new ArgumentException("Three slots and a camera are required.");

            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
                localPositions[slot] = localCenter;

            var ordered = new List<SlotPosition>(BciTargetSlotAllocator.SlotCount);
            for (int slot = 0; slot < BciTargetSlotAllocator.SlotCount; slot++)
            {
                if (!slotVisible[slot])
                    continue;

                Vector3 screen = camera.WorldToScreenPoint(anchors[slot].WorldPosition);
                ordered.Add(new SlotPosition(slot, screen.x));
            }

            ordered.Sort((left, right) =>
            {
                int byScreenX = left.DesiredRight.CompareTo(right.DesiredRight);
                return byScreenX != 0 ? byScreenX : left.SlotIndex.CompareTo(right.SlotIndex);
            });

            float spacing = Mathf.Max(0f, horizontalSpacing);
            float centerRank = (ordered.Count - 1) * 0.5f;
            for (int index = 0; index < ordered.Count; index++)
            {
                localPositions[ordered[index].SlotIndex] =
                    localCenter + Vector3.right * ((index - centerRank) * spacing);
            }
        }

        public static bool HasViewSpaceOverlap(
            Vector3 firstPosition,
            Vector3 secondPosition,
            Vector3 cameraRight,
            Vector3 cameraUp,
            float stimulusSizeMeters)
        {
            Vector3 right = NormalizeOrFallback(cameraRight, Vector3.right);
            Vector3 up = NormalizeOrFallback(cameraUp, Vector3.up);
            Vector3 delta = secondPosition - firstPosition;
            return Mathf.Abs(Vector3.Dot(delta, right)) < stimulusSizeMeters &&
                   Mathf.Abs(Vector3.Dot(delta, up)) < stimulusSizeMeters;
        }

        public static Vector3 CalculateLeaderLineStart(
            Vector3 displayPosition,
            Vector3 anchorPosition,
            float stimulusSizeMeters)
        {
            Vector3 towardAnchor = anchorPosition - displayPosition;
            float length = towardAnchor.magnitude;
            return length > Mathf.Epsilon
                ? displayPosition + towardAnchor / length * Mathf.Min(stimulusSizeMeters * 0.55f, length * 0.4f)
                : displayPosition;
        }

        private static Vector3 NormalizeOrFallback(Vector3 value, Vector3 fallback)
        {
            return value.sqrMagnitude > Mathf.Epsilon ? value.normalized : fallback;
        }
    }
}
