using System.Collections.Generic;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciPhysicalTargetDeduplicatorTests
    {
        [Test]
        public void SamePhysicalObjectKeepsOnlyTheMoreMatureTrack()
        {
            var candidates = new[]
            {
                Anchor("target-young", "bottle", new Vector3(0f, 0f, 1f), 0f, 0.8f, 20f),
                Anchor("target-mature", "bottle", new Vector3(0.01f, 0f, 1f), 0f, 0.8f, 10f)
            };

            IReadOnlyList<StableWorldAnchorSnapshot> selected =
                BciPhysicalTargetDeduplicator.Select(candidates);

            Assert.That(selected, Has.Count.EqualTo(1));
            Assert.That(selected[0].TargetId, Is.EqualTo("target-mature"));
        }

        [Test]
        public void SameLabelDifferentObjectsRemainSeparate()
        {
            var candidates = new[]
            {
                Anchor("left-bottle", "bottle", new Vector3(-0.4f, 0f, 1f), 0f, 0.8f, 10f),
                Anchor("right-bottle", "bottle", new Vector3(0.4f, 0f, 1f), 0f, 0.8f, 20f)
            };

            IReadOnlyList<StableWorldAnchorSnapshot> selected =
                BciPhysicalTargetDeduplicator.Select(candidates);

            Assert.That(selected, Has.Count.EqualTo(2));
            Assert.That(selected[0].TargetId, Is.EqualTo("left-bottle"));
            Assert.That(selected[1].TargetId, Is.EqualTo("right-bottle"));
        }

        [Test]
        public void MatchingLabelAloneDoesNotSuppressCandidatesWithoutStrongGeometry()
        {
            var first = Anchor("bottle-a", "bottle", new Vector3(0f, 0f, 1f), 0f, 0.8f, 0f);
            var second = Anchor("bottle-b", "bottle", new Vector3(0.01f, 0f, 1f), 0f, 0.8f, 80f);

            Assert.That(BciPhysicalTargetDeduplicator.AreLikelySamePhysicalObject(first, second), Is.False);
            Assert.That(BciPhysicalTargetDeduplicator.Select(new[] { first, second }), Has.Count.EqualTo(2));
        }

        private static StableWorldAnchorSnapshot Anchor(
            string targetId,
            string className,
            Vector3 position,
            double firstSeen,
            float confidence,
            float bboxX)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                className,
                StableTargetState.Active,
                position,
                confidence,
                new TargetBoundingBox(bboxX, 10f, 40f, 40f),
                firstSeen,
                firstSeen + (targetId.Contains("mature") ? 5d : 1d));
        }
    }
}
