using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Immutable user-confirmed collection of Quest-reconciled target results.
    /// It is a downstream boundary, not a robot command.
    /// </summary>
    public sealed class ConfirmedTargetBatch
    {
        public const string ProvenanceValue = "quest_confirmed_target_batch";

        public ConfirmedTargetBatch(
            string batchId,
            string groupId,
            int groupIndex,
            IReadOnlyList<BciTargetSelectionResult> selections,
            DateTime submittedUtc)
        {
            if (string.IsNullOrWhiteSpace(batchId))
                throw new ArgumentException("A batch identity is required.", nameof(batchId));
            if (string.IsNullOrWhiteSpace(groupId))
                throw new ArgumentException("A group identity is required.", nameof(groupId));
            if (selections == null || selections.Count == 0)
                throw new ArgumentException("A confirmed batch requires at least one selection.", nameof(selections));

            var copy = new BciTargetSelectionResult[selections.Count];
            for (int index = 0; index < copy.Length; index++)
                copy[index] = selections[index];

            BatchId = batchId;
            GroupId = groupId;
            GroupIndex = groupIndex;
            Selections = Array.AsReadOnly(copy);
            SubmittedUtc = submittedUtc;
            Provenance = ProvenanceValue;
        }

        public string BatchId { get; }
        public string GroupId { get; }
        public int GroupIndex { get; }
        public IReadOnlyList<BciTargetSelectionResult> Selections { get; }
        public DateTime SubmittedUtc { get; }
        public string Provenance { get; }
    }

    [Serializable]
    public sealed class ConfirmedTargetBatchPayload
    {
        public string batchId;
        public string groupId;
        public int groupIndex;
        public string submittedUtc;
        public string provenance;
        public ConfirmedTargetSelectionPayload[] selections;

        public static ConfirmedTargetBatchPayload From(ConfirmedTargetBatch batch)
        {
            var payload = new ConfirmedTargetBatchPayload
            {
                batchId = batch.BatchId,
                groupId = batch.GroupId,
                groupIndex = batch.GroupIndex,
                submittedUtc = batch.SubmittedUtc.ToString("O"),
                provenance = batch.Provenance,
                selections = new ConfirmedTargetSelectionPayload[batch.Selections.Count]
            };
            for (int index = 0; index < payload.selections.Length; index++)
                payload.selections[index] = ConfirmedTargetSelectionPayload.From(batch.Selections[index]);
            return payload;
        }
    }

    [Serializable]
    public sealed class ConfirmedTargetSelectionPayload
    {
        public string selectionId;
        public int predictedClassIndex;
        public int slotIndex;
        public string targetId;
        public string semanticLabel;
        public bool hasWorldPosition;
        public Vector3 worldPosition;
        public string resolvedUtc;
        public string provenance;

        public static ConfirmedTargetSelectionPayload From(BciTargetSelectionResult result)
        {
            return new ConfirmedTargetSelectionPayload
            {
                selectionId = result.SelectionId,
                predictedClassIndex = result.PredictedClassIndex,
                slotIndex = result.SlotIndex,
                targetId = result.TargetId,
                semanticLabel = result.SemanticLabel,
                hasWorldPosition = result.HasWorldPosition,
                worldPosition = result.WorldPosition,
                resolvedUtc = result.ResolvedUtc.ToString("O"),
                provenance = result.Provenance
            };
        }
    }
}
