using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;

namespace BCIIntelligentRobot.Integration
{
    public enum BciSelectionTransportRejection
    {
        None,
        InvalidSelectionId,
        DuplicateSelectionId,
        UnknownSelectionId,
        DuplicateDecision,
        InvalidClassIndex,
        EmptySlot,
        TargetInvalid
    }

    public readonly struct BciSelectionTransportResult
    {
        public BciSelectionTransportResult(string selectionId, int predictedClassIndex, BciSelectionTransportRejection rejection, BciSelectionTarget target)
        {
            SelectionId = selectionId;
            PredictedClassIndex = predictedClassIndex;
            Rejection = rejection;
            Target = target;
        }

        public string SelectionId { get; }
        public int PredictedClassIndex { get; }
        public BciSelectionTransportRejection Rejection { get; }
        public BciSelectionTarget Target { get; }
        public bool IsAccepted => Rejection == BciSelectionTransportRejection.None;
    }

    /// <summary>Owns the one-shot association between a selection ID and its immutable Quest snapshot.</summary>
    public sealed class BciSelectionCoordinator
    {
        private readonly Dictionary<string, BciSelectionSnapshot> m_pendingSnapshots = new Dictionary<string, BciSelectionSnapshot>(StringComparer.Ordinal);
        private readonly HashSet<string> m_completedSelectionIds = new HashSet<string>(StringComparer.Ordinal);

        public BciSelectionTransportResult Open(string selectionId, BciSelectionSnapshot snapshot)
        {
            if (string.IsNullOrWhiteSpace(selectionId) || snapshot == null)
                return Reject(selectionId, -1, BciSelectionTransportRejection.InvalidSelectionId);
            if (m_pendingSnapshots.ContainsKey(selectionId) || m_completedSelectionIds.Contains(selectionId))
                return Reject(selectionId, -1, BciSelectionTransportRejection.DuplicateSelectionId);

            m_pendingSnapshots.Add(selectionId, snapshot);
            return new BciSelectionTransportResult(selectionId, -1, BciSelectionTransportRejection.None, default(BciSelectionTarget));
        }

        public BciSelectionTransportResult Resolve(string selectionId, int predictedClassIndex)
        {
            if (string.IsNullOrWhiteSpace(selectionId))
                return Reject(selectionId, predictedClassIndex, BciSelectionTransportRejection.InvalidSelectionId);
            if (m_completedSelectionIds.Contains(selectionId))
                return Reject(selectionId, predictedClassIndex, BciSelectionTransportRejection.DuplicateDecision);
            if (!m_pendingSnapshots.TryGetValue(selectionId, out BciSelectionSnapshot snapshot))
                return Reject(selectionId, predictedClassIndex, BciSelectionTransportRejection.UnknownSelectionId);

            // Every decision is terminal, including invalid data, so a retry cannot cause a second selection side effect.
            m_pendingSnapshots.Remove(selectionId);
            m_completedSelectionIds.Add(selectionId);
            BciSelectionResolution resolution = snapshot.ResolveClassIndex(predictedClassIndex);
            return resolution.IsAccepted
                ? new BciSelectionTransportResult(selectionId, predictedClassIndex, BciSelectionTransportRejection.None, resolution.Target)
                : Reject(selectionId, predictedClassIndex, MapRejection(resolution.Rejection));
        }

        /// <summary>
        /// Ends an opened selection without applying a class. A later delayed
        /// decision is rejected exactly like any other completed selection.
        /// </summary>
        public BciSelectionTransportResult Abort(string selectionId)
        {
            if (string.IsNullOrWhiteSpace(selectionId))
                return Reject(selectionId, -1, BciSelectionTransportRejection.InvalidSelectionId);
            if (m_completedSelectionIds.Contains(selectionId))
                return Reject(selectionId, -1, BciSelectionTransportRejection.DuplicateDecision);
            if (!m_pendingSnapshots.Remove(selectionId))
                return Reject(selectionId, -1, BciSelectionTransportRejection.UnknownSelectionId);

            m_completedSelectionIds.Add(selectionId);
            return new BciSelectionTransportResult(
                selectionId,
                -1,
                BciSelectionTransportRejection.None,
                default(BciSelectionTarget));
        }

        private static BciSelectionTransportRejection MapRejection(BciSelectionRejection rejection)
        {
            switch (rejection)
            {
                case BciSelectionRejection.InvalidClassIndex: return BciSelectionTransportRejection.InvalidClassIndex;
                case BciSelectionRejection.EmptySlot: return BciSelectionTransportRejection.EmptySlot;
                case BciSelectionRejection.TargetInvalid: return BciSelectionTransportRejection.TargetInvalid;
                default: throw new ArgumentOutOfRangeException(nameof(rejection), rejection, "Unexpected snapshot rejection.");
            }
        }

        private static BciSelectionTransportResult Reject(string selectionId, int predictedClassIndex, BciSelectionTransportRejection rejection)
        {
            return new BciSelectionTransportResult(selectionId, predictedClassIndex, rejection, default(BciSelectionTarget));
        }
    }
}
