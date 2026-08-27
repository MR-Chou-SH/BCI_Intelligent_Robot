using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciGroupTargetReassociationTests
    {
        [Test]
        public void UniqueReplacement_WithStrongBboxAndWorldContinuity_IsAccepted()
        {
            BciGroupTargetReassociationDecision[] decisions = BciGroupTargetReassociation.Evaluate(
                new[] { Member(0, "old", 0f, 10d) },
                new[] { Anchor("new", 0.012f, 104f, 10.2d, 9d) },
                selectionFrozen: false);

            Assert.That(decisions, Has.Length.EqualTo(1));
            Assert.That(decisions[0].Outcome, Is.EqualTo(BciGroupTargetReassociationOutcome.Accepted));
            Assert.That(decisions[0].OldTargetId, Is.EqualTo("old"));
            Assert.That(decisions[0].NewTarget.TargetId, Is.EqualTo("new"));
        }

        [Test]
        public void UniqueReplacement_WithZeroIouButShortContinuousWorldEvidence_IsAccepted()
        {
            BciGroupTargetReassociationDecision[] decisions = BciGroupTargetReassociation.Evaluate(
                new[] { Member(0, "old", 0f, 10d) },
                new[] { Anchor("new", 0.012f, 220f, 10.3d, 9.1d) },
                selectionFrozen: false);

            Assert.That(decisions[0].BoundingBoxIoU, Is.EqualTo(0f));
            Assert.That(decisions[0].Outcome, Is.EqualTo(BciGroupTargetReassociationOutcome.Accepted));
        }

        [Test]
        public void TwoPlausibleSameLabelCandidates_AreRejectedAsAmbiguous()
        {
            BciGroupTargetReassociationDecision[] decisions = BciGroupTargetReassociation.Evaluate(
                new[] { Member(0, "old", 0f, 10d) },
                new[]
                {
                    Anchor("new-a", 0.010f, 220f, 10.2d, 9d),
                    Anchor("new-b", -0.011f, 260f, 10.2d, 9d)
                },
                selectionFrozen: false);

            Assert.That(decisions[0].Outcome, Is.EqualTo(BciGroupTargetReassociationOutcome.RejectedAmbiguous));
            Assert.That(decisions[0].CompetingCandidateCount, Is.EqualTo(2));
        }

        [Test]
        public void OneNewTargetPlausibleForTwoLogicalMembers_IsRejectedForBoth()
        {
            BciGroupTargetReassociationDecision[] decisions = BciGroupTargetReassociation.Evaluate(
                new[]
                {
                    Member(0, "old-left", 0f, 10d),
                    Member(1, "old-right", 0.015f, 10d)
                },
                new[] { Anchor("new", 0.008f, 220f, 10.2d, 9d) },
                selectionFrozen: false);

            Assert.That(decisions, Has.Length.EqualTo(2));
            Assert.That(decisions[0].Outcome, Is.EqualTo(BciGroupTargetReassociationOutcome.RejectedAmbiguous));
            Assert.That(decisions[1].Outcome, Is.EqualTo(BciGroupTargetReassociationOutcome.RejectedAmbiguous));
        }

        [Test]
        public void SelectionFreeze_RejectsOtherwiseUniqueReplacementUntilReleased()
        {
            BciLogicalGroupMember member = Member(0, "old", 0f, 10d);
            StableWorldAnchorSnapshot replacement = Anchor("new", 0.012f, 220f, 10.2d, 9d);

            Assert.That(BciGroupTargetReassociation.Evaluate(
                new[] { member }, new[] { replacement }, selectionFrozen: true)[0].Outcome,
                Is.EqualTo(BciGroupTargetReassociationOutcome.RejectedSelectionFrozen));
            Assert.That(BciGroupTargetReassociation.Evaluate(
                new[] { member }, new[] { replacement }, selectionFrozen: false)[0].Outcome,
                Is.EqualTo(BciGroupTargetReassociationOutcome.Accepted));
        }

        private static BciLogicalGroupMember Member(int slot, string targetId, float x, double lastSeen)
        {
            return new BciLogicalGroupMember(slot, Anchor(targetId, x, 100f, lastSeen, 0d), selected: false);
        }

        private static StableWorldAnchorSnapshot Anchor(
            string targetId,
            float x,
            float bboxX,
            double lastSeen,
            double firstSeen)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                "bottle",
                StableTargetState.Active,
                new Vector3(x, 0f, 2f),
                0.9f,
                new TargetBoundingBox(bboxX, 20f, 30f, 100f),
                firstSeen,
                lastSeen);
        }
    }
}
