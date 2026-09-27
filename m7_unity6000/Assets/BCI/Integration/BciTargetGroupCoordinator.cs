using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;

namespace BCIIntelligentRobot.Integration
{
    public readonly struct BciActiveTargetGroup
    {
        public BciActiveTargetGroup(string groupId, int groupIndex, IReadOnlyList<StableWorldAnchorSnapshot> targets)
        {
            var members = new BciLogicalGroupMember[targets.Count];
            for (int index = 0; index < members.Length; index++)
                members[index] = new BciLogicalGroupMember(index, targets[index], selected: false);
            GroupId = groupId;
            GroupIndex = groupIndex;
            Members = Array.AsReadOnly(members);
            Targets = CreateTargets(members);
        }

        private BciActiveTargetGroup(string groupId, int groupIndex, BciLogicalGroupMember[] members)
        {
            GroupId = groupId;
            GroupIndex = groupIndex;
            Members = Array.AsReadOnly(members);
            Targets = CreateTargets(members);
        }

        public string GroupId { get; }
        public int GroupIndex { get; }
        public IReadOnlyList<BciLogicalGroupMember> Members { get; }
        public IReadOnlyList<StableWorldAnchorSnapshot> Targets { get; }

        public BciActiveTargetGroup WithLiveAnchors(IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            var updated = CopyMembers();
            bool changed = false;
            for (int slot = 0; slot < updated.Length; slot++)
            {
                for (int candidateIndex = 0; candidateIndex < candidates.Count; candidateIndex++)
                {
                    StableWorldAnchorSnapshot candidate = candidates[candidateIndex];
                    if (candidate.State != StableTargetState.Active ||
                        !string.Equals(updated[slot].CurrentTargetId, candidate.TargetId, StringComparison.Ordinal))
                        continue;

                    updated[slot] = updated[slot].WithLiveAnchor(candidate);
                    changed = true;
                    break;
                }
            }
            return changed ? new BciActiveTargetGroup(GroupId, GroupIndex, updated) : this;
        }

        public BciActiveTargetGroup WithReassociation(BciGroupTargetReassociationDecision decision)
        {
            if (decision.Outcome != BciGroupTargetReassociationOutcome.Accepted ||
                decision.SlotIndex < 0 || decision.SlotIndex >= Members.Count ||
                !string.Equals(Members[decision.SlotIndex].CurrentTargetId, decision.OldTargetId, StringComparison.Ordinal))
                return this;

            BciLogicalGroupMember[] updated = CopyMembers();
            updated[decision.SlotIndex] = updated[decision.SlotIndex].WithHandover(decision.NewTarget);
            return new BciActiveTargetGroup(GroupId, GroupIndex, updated);
        }

        public BciActiveTargetGroup WithSlotSelected(int slotIndex, bool selected)
        {
            if (slotIndex < 0 || slotIndex >= Members.Count)
                return this;

            BciLogicalGroupMember[] updated = CopyMembers();
            updated[slotIndex] = updated[slotIndex].WithSelected(selected);
            return new BciActiveTargetGroup(GroupId, GroupIndex, updated);
        }

        public BciActiveTargetGroup WithAuthoritativeTargets(
            IReadOnlyList<StableWorldAnchorSnapshot> targets)
        {
            if (targets == null || targets.Count != BciTargetSlotAllocator.SlotCount)
                return this;

            var selectedTargetIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < Members.Count; index++)
            {
                if (Members[index].IsSelected && !string.IsNullOrWhiteSpace(Members[index].CurrentTargetId))
                    selectedTargetIds.Add(Members[index].CurrentTargetId);
            }

            var updated = new BciLogicalGroupMember[targets.Count];
            for (int slot = 0; slot < targets.Count; slot++)
            {
                StableWorldAnchorSnapshot target = targets[slot];
                updated[slot] = new BciLogicalGroupMember(
                    slot,
                    target,
                    selectedTargetIds.Contains(target.TargetId));
            }
            return new BciActiveTargetGroup(GroupId, GroupIndex, updated);
        }

        private BciLogicalGroupMember[] CopyMembers()
        {
            var copy = new BciLogicalGroupMember[Members.Count];
            for (int index = 0; index < copy.Length; index++)
                copy[index] = Members[index];
            return copy;
        }

        private static IReadOnlyList<StableWorldAnchorSnapshot> CreateTargets(
            IReadOnlyList<BciLogicalGroupMember> members)
        {
            var targets = new StableWorldAnchorSnapshot[members.Count];
            for (int index = 0; index < targets.Length; index++)
                targets[index] = members[index].CurrentAnchor;
            return Array.AsReadOnly(targets);
        }
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

            if (m_activeGroup.HasValue)
                m_activeGroup = m_activeGroup.Value.WithLiveAnchors(m_candidates);
        }

        public IReadOnlyList<BciGroupTargetReassociationDecision> EvaluateActiveGroupReassociation(
            bool selectionFrozen)
        {
            if (!m_activeGroup.HasValue)
                return Array.Empty<BciGroupTargetReassociationDecision>();

            var unprocessedCandidates = new List<StableWorldAnchorSnapshot>(m_candidates.Count);
            for (int index = 0; index < m_candidates.Count; index++)
            {
                StableWorldAnchorSnapshot candidate = m_candidates[index];
                if (!m_processedTargetIds.Contains(candidate.TargetId))
                    unprocessedCandidates.Add(candidate);
            }
            return BciGroupTargetReassociation.Evaluate(
                m_activeGroup.Value.Members,
                unprocessedCandidates,
                selectionFrozen);
        }

        public bool TryCommitReassociation(BciGroupTargetReassociationDecision decision)
        {
            if (!m_activeGroup.HasValue || decision.Outcome != BciGroupTargetReassociationOutcome.Accepted)
                return false;

            BciActiveTargetGroup group = m_activeGroup.Value;
            if (decision.SlotIndex < 0 || decision.SlotIndex >= group.Members.Count ||
                !string.Equals(group.Members[decision.SlotIndex].CurrentTargetId, decision.OldTargetId, StringComparison.Ordinal))
                return false;

            m_activeGroup = group.WithReassociation(decision);
            return true;
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

        /// <summary>
        /// Applies the PC authoritative slot-to-TargetId snapshot at the
        /// selection_open boundary. This changes only the current Quest view
        /// of slot membership; accepted-result order and batch semantics stay
        /// owned by the existing coordinator.
        /// </summary>
        public bool TryApplyAuthoritativeSelectionSnapshot(BciSelectionSnapshot snapshot)
        {
            if (!m_activeGroup.HasValue || snapshot == null)
                return false;

            var targets = new StableWorldAnchorSnapshot[BciTargetSlotAllocator.SlotCount];
            var seenTargetIds = new HashSet<string>(StringComparer.Ordinal);
            for (int slot = 0; slot < targets.Length; slot++)
            {
                BciSelectionResolution resolution = snapshot.ResolveClassIndex(slot);
                if (resolution.Rejection == BciSelectionRejection.EmptySlot)
                {
                    targets[slot] = default(StableWorldAnchorSnapshot);
                    continue;
                }
                if (!resolution.IsAccepted || string.IsNullOrWhiteSpace(resolution.Target.TargetId) ||
                    !seenTargetIds.Add(resolution.Target.TargetId))
                    return false;

                StableWorldAnchorSnapshot matchingCandidate = default(StableWorldAnchorSnapshot);
                bool found = false;
                for (int index = 0; index < m_candidates.Count; index++)
                {
                    StableWorldAnchorSnapshot candidate = m_candidates[index];
                    if (string.Equals(candidate.TargetId, resolution.Target.TargetId, StringComparison.Ordinal) &&
                        candidate.State == StableTargetState.Active)
                    {
                        matchingCandidate = candidate;
                        found = true;
                        break;
                    }
                }
                if (!found)
                    return false;
                targets[slot] = matchingCandidate;
            }

            BciActiveTargetGroup current = m_activeGroup.Value;
            for (int index = 0; index < m_selectedResults.Count; index++)
            {
                BciTargetSelectionResult selected = m_selectedResults[index];
                if (selected.SlotIndex < 0 || selected.SlotIndex >= targets.Length ||
                    !string.Equals(targets[selected.SlotIndex].TargetId, selected.TargetId, StringComparison.Ordinal))
                    return false;
            }

            m_activeGroup = current.WithAuthoritativeTargets(targets);
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
                m_activeGroup = group.WithSlotSelected(slot, true);
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
            m_activeGroup = m_activeGroup.Value.WithSlotSelected(undone.SlotIndex, false);
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

            for (int index = 0; index < m_selectedResults.Count; index++)
            {
                string selectedTargetId = group.Targets[m_selectedResults[index].SlotIndex].TargetId;
                // A confirms only the provisional selections actually present
                // in the ordered batch. Unselected members stay available for
                // the next group instead of being silently consumed.
                m_processedTargetIds.Add(selectedTargetId);
                m_submittedTargetIds.Add(selectedTargetId);
            }

            m_selectedResults.Clear();
            m_selectedTargetIds.Clear();
            m_activeGroup = null;
            BatchConfirmed?.Invoke(batch);
            return true;
        }

        /// <summary>
        /// Closes the current group for the PC Showcase after validating the
        /// immutable Quest selection facts. This is the host-driven equivalent
        /// of controller A; it never remaps a slot and never accepts a
        /// selection that is not already present in the active group.
        /// </summary>
        public bool TryCloseCurrentGroupFromHost(
            IReadOnlyList<ConfirmedTargetSelectionPayload> expectedSelections,
            out string groupId)
        {
            groupId = null;
            if (!m_activeGroup.HasValue || m_selectedResults.Count == 0 ||
                expectedSelections == null || expectedSelections.Count != m_selectedResults.Count)
                return false;

            BciActiveTargetGroup group = m_activeGroup.Value;
            for (int index = 0; index < m_selectedResults.Count; index++)
            {
                BciTargetSelectionResult actual = m_selectedResults[index];
                ConfirmedTargetSelectionPayload expected = expectedSelections[index];
                if (expected == null ||
                    !string.Equals(actual.SelectionId, expected.selectionId, StringComparison.Ordinal) ||
                    actual.PredictedClassIndex != expected.predictedClassIndex ||
                    actual.SlotIndex != expected.slotIndex ||
                    !string.Equals(actual.TargetId, expected.targetId, StringComparison.Ordinal) ||
                    actual.SlotIndex < 0 || actual.SlotIndex >= group.Targets.Count ||
                    !string.Equals(group.Targets[actual.SlotIndex].TargetId, actual.TargetId, StringComparison.Ordinal))
                    return false;
            }

            for (int index = 0; index < m_selectedResults.Count; index++)
            {
                string selectedTargetId = group.Targets[m_selectedResults[index].SlotIndex].TargetId;
                m_processedTargetIds.Add(selectedTargetId);
                m_submittedTargetIds.Add(selectedTargetId);
            }

            groupId = group.GroupId;
            m_selectedResults.Clear();
            m_selectedTargetIds.Clear();
            m_activeGroup = null;
            return true;
        }
    }
}
