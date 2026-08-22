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
        {
            TargetId = targetId;
            ClassName = className;
            State = state;
            WorldPosition = worldPosition;
        }

        public string TargetId { get; }
        public string ClassName { get; }
        public StableTargetState State { get; }
        public Vector3 WorldPosition { get; }
    }
}
