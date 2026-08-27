using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// A group-local logical member. It owns only the current tracker identity
    /// for one active M8.4 slot; it is not a second StableTarget tracker.
    /// </summary>
    public readonly struct BciLogicalGroupMember
    {
        public BciLogicalGroupMember(int slotIndex, StableWorldAnchorSnapshot anchor, bool selected)
        {
            SlotIndex = slotIndex;
            CurrentAnchor = anchor;
            LastValidAnchor = anchor;
            IsSelected = selected;
        }

        private BciLogicalGroupMember(
            int slotIndex,
            StableWorldAnchorSnapshot currentAnchor,
            StableWorldAnchorSnapshot lastValidAnchor,
            bool selected)
        {
            SlotIndex = slotIndex;
            CurrentAnchor = currentAnchor;
            LastValidAnchor = lastValidAnchor;
            IsSelected = selected;
        }

        public int SlotIndex { get; }
        public string CurrentTargetId => CurrentAnchor.TargetId;
        public string Label => LastValidAnchor.ClassName;
        public StableWorldAnchorSnapshot CurrentAnchor { get; }
        public StableWorldAnchorSnapshot LastValidAnchor { get; }
        public bool IsSelected { get; }

        public BciLogicalGroupMember WithLiveAnchor(StableWorldAnchorSnapshot anchor)
        {
            return new BciLogicalGroupMember(SlotIndex, anchor, anchor, IsSelected);
        }

        public BciLogicalGroupMember WithHandover(StableWorldAnchorSnapshot anchor)
        {
            return new BciLogicalGroupMember(SlotIndex, anchor, anchor, IsSelected);
        }

        public BciLogicalGroupMember WithSelected(bool selected)
        {
            return new BciLogicalGroupMember(SlotIndex, CurrentAnchor, LastValidAnchor, selected);
        }
    }

    public enum BciGroupTargetReassociationOutcome
    {
        Accepted,
        RejectedNoCandidate,
        RejectedAmbiguous,
        RejectedSelectionFrozen
    }

    public readonly struct BciGroupTargetReassociationDecision
    {
        public BciGroupTargetReassociationDecision(
            BciGroupTargetReassociationOutcome outcome,
            int slotIndex,
            StableWorldAnchorSnapshot oldAnchor,
            StableWorldAnchorSnapshot newTarget,
            float worldDistanceMeters,
            float boundingBoxIoU,
            double timeGapSeconds,
            int competingCandidateCount,
            int competingMemberCount,
            string reason)
        {
            Outcome = outcome;
            SlotIndex = slotIndex;
            OldAnchor = oldAnchor;
            NewTarget = newTarget;
            WorldDistanceMeters = worldDistanceMeters;
            BoundingBoxIoU = boundingBoxIoU;
            TimeGapSeconds = timeGapSeconds;
            CompetingCandidateCount = competingCandidateCount;
            CompetingMemberCount = competingMemberCount;
            Reason = reason;
        }

        public BciGroupTargetReassociationOutcome Outcome { get; }
        public int SlotIndex { get; }
        public StableWorldAnchorSnapshot OldAnchor { get; }
        public string OldTargetId => OldAnchor.TargetId;
        public StableWorldAnchorSnapshot NewTarget { get; }
        public float WorldDistanceMeters { get; }
        public float BoundingBoxIoU { get; }
        public double TimeGapSeconds { get; }
        public int CompetingCandidateCount { get; }
        public int CompetingMemberCount { get; }
        public string Reason { get; }
    }

    /// <summary>
    /// Conservative, pure reassociation policy for M8.4 active groups. It
    /// accepts only isolated 1x1 old-member/new-target matches. Any graph
    /// component with more than one plausible edge is rejected rather than
    /// guessing between adjacent same-label physical objects.
    /// </summary>
    public static class BciGroupTargetReassociation
    {
        public const float MaximumWorldDistanceMeters = 0.04f;
        public const double MaximumTimeGapSeconds = 4d;
        public const double MinimumCandidateMaturitySeconds = 0.75d;

        public static BciGroupTargetReassociationDecision[] Evaluate(
            IReadOnlyList<BciLogicalGroupMember> members,
            IReadOnlyList<StableWorldAnchorSnapshot> candidates,
            bool selectionFrozen)
        {
            if (members == null)
                throw new ArgumentNullException(nameof(members));
            if (candidates == null)
                throw new ArgumentNullException(nameof(candidates));

            var liveIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < candidates.Count; index++)
            {
                if (candidates[index].State == StableTargetState.Active)
                    liveIds.Add(candidates[index].TargetId);
            }

            var missingMembers = new List<BciLogicalGroupMember>();
            for (int index = 0; index < members.Count; index++)
            {
                BciLogicalGroupMember member = members[index];
                if (!liveIds.Contains(member.CurrentTargetId))
                    missingMembers.Add(member);
            }
            if (missingMembers.Count == 0)
                return Array.Empty<BciGroupTargetReassociationDecision>();

            if (selectionFrozen)
            {
                var frozen = new BciGroupTargetReassociationDecision[missingMembers.Count];
                for (int index = 0; index < missingMembers.Count; index++)
                {
                    frozen[index] = Decision(
                        BciGroupTargetReassociationOutcome.RejectedSelectionFrozen,
                        missingMembers[index], default(StableWorldAnchorSnapshot), 0, 0,
                        "selection_layout_frozen");
                }
                return frozen;
            }

            var plausibleCandidatesBySlot = new Dictionary<int, List<PairEvidence>>();
            var plausibleMemberCountByCandidateId = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int memberIndex = 0; memberIndex < missingMembers.Count; memberIndex++)
            {
                BciLogicalGroupMember member = missingMembers[memberIndex];
                var pairs = new List<PairEvidence>();
                for (int candidateIndex = 0; candidateIndex < candidates.Count; candidateIndex++)
                {
                    StableWorldAnchorSnapshot candidate = candidates[candidateIndex];
                    if (!IsPlausible(member, candidate, out PairEvidence pair))
                        continue;

                    pairs.Add(pair);
                    plausibleMemberCountByCandidateId.TryGetValue(candidate.TargetId, out int currentCount);
                    plausibleMemberCountByCandidateId[candidate.TargetId] = currentCount + 1;
                }
                plausibleCandidatesBySlot.Add(member.SlotIndex, pairs);
            }

            var decisions = new BciGroupTargetReassociationDecision[missingMembers.Count];
            for (int index = 0; index < missingMembers.Count; index++)
            {
                BciLogicalGroupMember member = missingMembers[index];
                List<PairEvidence> pairs = plausibleCandidatesBySlot[member.SlotIndex];
                if (pairs.Count == 0)
                {
                    decisions[index] = Decision(
                        BciGroupTargetReassociationOutcome.RejectedNoCandidate,
                        member, default(StableWorldAnchorSnapshot), 0, 0,
                        "no_unique_same_label_continuity_candidate");
                    continue;
                }

                PairEvidence pair = pairs[0];
                int competingMembers = plausibleMemberCountByCandidateId[pair.Anchor.TargetId];
                if (pairs.Count != 1 || competingMembers != 1)
                {
                    decisions[index] = Decision(
                        BciGroupTargetReassociationOutcome.RejectedAmbiguous,
                        member, pair.Anchor, pairs.Count, competingMembers,
                        pairs.Count != 1 ? "multiple_plausible_candidates" : "candidate_matches_multiple_logical_members");
                    continue;
                }

                decisions[index] = Decision(
                    BciGroupTargetReassociationOutcome.Accepted,
                    member, pair.Anchor, 1, 1, "unique_same_label_continuity_match");
            }
            return decisions;
        }

        private static bool IsPlausible(
            BciLogicalGroupMember member,
            StableWorldAnchorSnapshot candidate,
            out PairEvidence evidence)
        {
            evidence = default(PairEvidence);
            StableWorldAnchorSnapshot oldAnchor = member.LastValidAnchor;
            if (candidate.State != StableTargetState.Active ||
                string.IsNullOrWhiteSpace(candidate.TargetId) ||
                string.Equals(candidate.TargetId, member.CurrentTargetId, StringComparison.Ordinal) ||
                !string.Equals(candidate.ClassName, member.Label, StringComparison.OrdinalIgnoreCase))
                return false;

            double maturitySeconds = Math.Max(0d, candidate.LastSeen - candidate.FirstSeen);
            double timeGapSeconds = Math.Max(0d, candidate.FirstSeen - oldAnchor.LastSeen);
            float worldDistance = Vector3.Distance(oldAnchor.WorldPosition, candidate.WorldPosition);
            if (maturitySeconds < MinimumCandidateMaturitySeconds ||
                timeGapSeconds > MaximumTimeGapSeconds ||
                worldDistance > MaximumWorldDistanceMeters)
                return false;

            evidence = new PairEvidence(
                candidate,
                worldDistance,
                CalculateIoU(oldAnchor.Bbox, candidate.Bbox),
                timeGapSeconds);
            return true;
        }

        private static BciGroupTargetReassociationDecision Decision(
            BciGroupTargetReassociationOutcome outcome,
            BciLogicalGroupMember member,
            StableWorldAnchorSnapshot candidate,
            int competingCandidateCount,
            int competingMemberCount,
            string reason)
        {
            float worldDistance = candidate.State == StableTargetState.Active
                ? Vector3.Distance(member.LastValidAnchor.WorldPosition, candidate.WorldPosition)
                : 0f;
            float iou = candidate.State == StableTargetState.Active
                ? CalculateIoU(member.LastValidAnchor.Bbox, candidate.Bbox)
                : 0f;
            double timeGap = candidate.State == StableTargetState.Active
                ? Math.Max(0d, candidate.FirstSeen - member.LastValidAnchor.LastSeen)
                : 0d;
            return new BciGroupTargetReassociationDecision(
                outcome,
                member.SlotIndex,
                member.LastValidAnchor,
                candidate,
                worldDistance,
                iou,
                timeGap,
                competingCandidateCount,
                competingMemberCount,
                reason);
        }

        private static float CalculateIoU(TargetBoundingBox first, TargetBoundingBox second)
        {
            if (!first.IsValid || !second.IsValid)
                return 0f;

            float width = Mathf.Max(0f, Mathf.Min(first.XMax, second.XMax) - Mathf.Max(first.XMin, second.XMin));
            float height = Mathf.Max(0f, Mathf.Min(first.YMax, second.YMax) - Mathf.Max(first.YMin, second.YMin));
            float intersection = width * height;
            float union = first.Area + second.Area - intersection;
            return union > Mathf.Epsilon ? intersection / union : 0f;
        }

        private readonly struct PairEvidence
        {
            public PairEvidence(StableWorldAnchorSnapshot anchor, float worldDistance, float bboxIoU, double timeGap)
            {
                Anchor = anchor;
                WorldDistance = worldDistance;
                BboxIoU = bboxIoU;
                TimeGap = timeGap;
            }

            public StableWorldAnchorSnapshot Anchor { get; }
            public float WorldDistance { get; }
            public float BboxIoU { get; }
            public double TimeGap { get; }
        }
    }
}
