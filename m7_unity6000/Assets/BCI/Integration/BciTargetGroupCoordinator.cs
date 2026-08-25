using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;

namespace BCIIntelligentRobot.Integration
{
    public readonly struct BciActiveTargetGroup
    {
        public BciActiveTargetGroup(string groupId, int groupIndex, IReadOnlyList<StableWorldAnchorSnapshot> targets)
        {
            var copy = new StableWorldAnchorSnapshot[targets.Count];
            for (int index = 0; index < copy.Length; index++)
                copy[index] = targets[index];
            GroupId = groupId;
            GroupIndex = groupIndex;
            Targets = Array.AsReadOnly(copy);
        }

        public string GroupId { get; }
        public int GroupIndex { get; }
        public IReadOnlyList<StableWorldAnchorSnapshot> Targets { get; }
    }

    /// <summary>
    /// Pure group/batch state. Candidate order is supplied by the view binding
    /// after its existing physical-object deduplication and left-to-right sort.
    /// </summary>
    public sealed class BciTargetGroupCoordinator
    {
        public const int MaximumGroupSize = BciTargetSlotAllocator.SlotCount;

        private readonly List<StableWorldAnchorSnapshot> m_candidates = new List<StableWorldAnchorSnapshot>();
        private readonly List<BciTargetSelectionResult> m_selectedResults = new List<BciTargetSelectionResult>();
        private readonly HashSet<string> m_selectedTargetIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> m_processedTargetIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> m_submittedTargetIds = new HashSet<string>(StringComparer.Ordinal);
        private BciActiveTargetGroup? m_activeGroup;
        private int m_nextGroupIndex;
        private int m_nextBatchIndex;

        public event Action<BciActiveTargetGroup> GroupActivated;
        public event Action<int, bool> GroupSlotSelectionChanged;
        public event Action<ConfirmedTargetBatch> BatchConfirmed;

        public bool HasActiveGroup => m_activeGroup.HasValue;
        public BciActiveTargetGroup? ActiveGroup => m_activeGroup;
        public IReadOnlyList<BciTargetSelectionResult> CurrentSelections => m_selectedResults.AsReadOnly();
        public IReadOnlyCollection<string> ProcessedTargetIds => m_processedTargetIds;
        public IReadOnlyCollection<string> SubmittedTargetIds => m_submittedTargetIds;

        public void UpdateCandidatePool(IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            m_candidates.Clear();
            if (candidates == null)
                return;

            var seen = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < candidates.Count; index++)
            {
                StableWorldAnchorSnapshot candidate = candidates[index];
                if (candidate.State != StableTargetState.Active ||
                    string.IsNullOrWhiteSpace(candidate.TargetId) ||
                    !seen.Add(candidate.TargetId))
                    continue;
                m_candidates.Add(candidate);
            }
        }

        /// <summary>Called after candidate updates have settled for the frame.</summary>
        public bool TryActivateNextGroup()
        {
            if (m_activeGroup.HasValue)
                return false;

            var targets = new List<StableWorldAnchorSnapshot>(MaximumGroupSize);
            for (int index = 0; index < m_candidates.Count && targets.Count < MaximumGroupSize; index++)
            {
                StableWorldAnchorSnapshot candidate = m_candidates[index];
                if (!m_processedTargetIds.Contains(candidate.TargetId))
                    targets.Add(candidate);
            }
            if (targets.Count == 0)
                return false;

            int groupIndex = ++m_nextGroupIndex;
            var group = new BciActiveTargetGroup("m8-group-" + groupIndex.ToString("D4"), groupIndex, targets);
            m_selectedResults.Clear();
            m_selectedTargetIds.Clear();
            m_activeGroup = group;
            GroupActivated?.Invoke(group);
            return true;
        }

        public bool TryAccept(BciTargetSelectionResult result)
        {
            if (!m_activeGroup.HasValue || string.IsNullOrWhiteSpace(result.TargetId) ||
                m_selectedTargetIds.Contains(result.TargetId))
                return false;

            BciActiveTargetGroup group = m_activeGroup.Value;
            for (int slot = 0; slot < group.Targets.Count; slot++)
            {
                if (!string.Equals(group.Targets[slot].TargetId, result.TargetId, StringComparison.Ordinal) ||
                    result.SlotIndex != slot)
                    continue;

                m_selectedTargetIds.Add(result.TargetId);
                m_selectedResults.Add(result);
                GroupSlotSelectionChanged?.Invoke(slot, true);
                return true;
            }
            return false;
        }

        public bool TryUndoLastSelection(out BciTargetSelectionResult undone)
        {
            undone = default(BciTargetSelectionResult);
            if (!m_activeGroup.HasValue || m_selectedResults.Count == 0)
                return false;

            int lastIndex = m_selectedResults.Count - 1;
            undone = m_selectedResults[lastIndex];
            m_selectedResults.RemoveAt(lastIndex);
            m_selectedTargetIds.Remove(undone.TargetId);
            GroupSlotSelectionChanged?.Invoke(undone.SlotIndex, false);
            return true;
        }

        public bool TryConfirmCurrentGroup(out ConfirmedTargetBatch batch)
        {
            batch = null;
            if (!m_activeGroup.HasValue || m_selectedResults.Count == 0)
                return false;

            BciActiveTargetGroup group = m_activeGroup.Value;
            batch = new ConfirmedTargetBatch(
                "m8-batch-" + (++m_nextBatchIndex).ToString("D4"),
                group.GroupId,
                group.GroupIndex,
                m_selectedResults,
                DateTime.UtcNow);

            for (int index = 0; index < group.Targets.Count; index++)
                m_processedTargetIds.Add(group.Targets[index].TargetId);
            for (int index = 0; index < m_selectedResults.Count; index++)
                m_submittedTargetIds.Add(m_selectedResults[index].TargetId);

            m_selectedResults.Clear();
            m_selectedTargetIds.Clear();
            m_activeGroup = null;
            BatchConfirmed?.Invoke(batch);
            return true;
        }
    }
}
