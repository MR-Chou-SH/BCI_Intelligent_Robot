using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// Minimal detector-independent world-anchor output for BCI target binding.
    /// </summary>
    public readonly struct StableWorldAnchorSnapshot
    {
        public StableWorldAnchorSnapshot(
            string targetId,
            string className,
            StableTargetState state,
            Vector3 worldPosition)
            : this(
                targetId,
                className,
                state,
                worldPosition,
                0f,
                default(TargetBoundingBox),
                0d,
                0d)
        {
        }

        public StableWorldAnchorSnapshot(
            string targetId,
            string className,
            StableTargetState state,
            Vector3 worldPosition,
            float confidence,
            TargetBoundingBox bbox,
            double firstSeen,
            double lastSeen)
        {
            TargetId = targetId;
            ClassName = className;
            State = state;
            WorldPosition = worldPosition;
            Confidence = confidence;
            Bbox = bbox;
            FirstSeen = firstSeen;
            LastSeen = lastSeen;
        }

        public string TargetId { get; }
        public string ClassName { get; }
        public StableTargetState State { get; }
        public Vector3 WorldPosition { get; }
        public float Confidence { get; }
        public TargetBoundingBox Bbox { get; }
        public double FirstSeen { get; }
        public double LastSeen { get; }
    }
}
