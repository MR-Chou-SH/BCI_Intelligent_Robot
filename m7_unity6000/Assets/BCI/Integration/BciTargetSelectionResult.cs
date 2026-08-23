using System;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Immutable Quest-owned result published once after an accepted EEG class
    /// has resolved through the frozen selection snapshot.
    /// </summary>
    public readonly struct BciTargetSelectionResult
    {
        public const string FrozenSnapshotProvenance = "quest_frozen_selection_snapshot";

        public BciTargetSelectionResult(string selectionId, int predictedClassIndex, BciSelectionTarget target, DateTime resolvedUtc)
        {
            SelectionId = selectionId;
            PredictedClassIndex = predictedClassIndex;
            SlotIndex = target.SlotIndex;
            TargetId = target.TargetId;
            SemanticLabel = target.ClassName;
            TargetState = target.State;
            HasWorldPosition = target.HasWorldPosition;
            WorldPosition = target.WorldPosition;
            ResolvedUtc = resolvedUtc;
            Provenance = FrozenSnapshotProvenance;
        }

        public string SelectionId { get; }
        public bool IsAccepted => true;
        public string Status => "accepted";
        public int PredictedClassIndex { get; }
        public int SlotIndex { get; }
        public string TargetId { get; }
        public string SemanticLabel { get; }
        public StableTargetState TargetState { get; }
        public bool HasWorldPosition { get; }
        public Vector3 WorldPosition { get; }
        public DateTime ResolvedUtc { get; }
        public string Provenance { get; }
    }
}
