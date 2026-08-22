using System.Linq;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;

namespace BCIIntelligentRobot.Tests
{
    public sealed class StableTargetManagerTests
    {
        private static TargetDetection2D Detection(string className, float x, float y = 100f)
        {
            return new TargetDetection2D(
                className,
                0.8f,
                new TargetBoundingBox(x, y, 100f, 100f),
                1000f,
                1000f);
        }

        [Test]
        public void SingleTargetKeepsSameIdAcrossContinuousFrames()
        {
            var manager = new StableTargetManager();
            StableTargetSnapshot first = manager.Update(new[] { Detection("cup", 100f) }, 0d).Single();
            StableTargetSnapshot second = manager.Update(new[] { Detection("cup", 108f) }, 0.5d).Single();

            Assert.That(second.TargetId, Is.EqualTo(first.TargetId));
            Assert.That(second.State, Is.EqualTo(StableTargetState.Active));
            Assert.That(second.FirstSeen, Is.EqualTo(0d));
            Assert.That(second.LastSeen, Is.EqualTo(0.5d));
        }

        [Test]
        public void SameClassInstancesKeepSeparateIdsWhenDetectionOrderChanges()
        {
            var manager = new StableTargetManager();
            var firstFrame = manager.Update(
                new[] { Detection("cup", 100f), Detection("cup", 700f) },
                0d);
            string leftId = firstFrame.Single(target => target.Bbox.X == 100f).TargetId;
            string rightId = firstFrame.Single(target => target.Bbox.X == 700f).TargetId;

            var secondFrame = manager.Update(
                new[] { Detection("cup", 704f), Detection("cup", 96f) },
                0.5d);

            Assert.That(secondFrame.Single(target => target.Bbox.X == 704f).TargetId, Is.EqualTo(rightId));
            Assert.That(secondFrame.Single(target => target.Bbox.X == 96f).TargetId, Is.EqualTo(leftId));
            Assert.That(secondFrame.Select(target => target.TargetId).Distinct().Count(), Is.EqualTo(2));
        }

        [Test]
        public void ShortMissingIntervalPreservesIdAndRecoversToActive()
        {
            var manager = new StableTargetManager(missingTimeoutSeconds: 1.5d);
            string targetId = manager.Update(new[] { Detection("bottle", 200f) }, 0d).Single().TargetId;

            StableTargetSnapshot missing = manager.Update(new TargetDetection2D[0], 0.5d).Single();
            StableTargetSnapshot recovered = manager.Update(new[] { Detection("bottle", 208f) }, 0.75d).Single();

            Assert.That(missing.TargetId, Is.EqualTo(targetId));
            Assert.That(missing.State, Is.EqualTo(StableTargetState.TemporarilyMissing));
            Assert.That(recovered.TargetId, Is.EqualTo(targetId));
            Assert.That(recovered.State, Is.EqualTo(StableTargetState.Active));
        }

        [Test]
        public void MissingBeyondTimeoutMarksTargetLostAndRemovesItFromCurrentSet()
        {
            var manager = new StableTargetManager(missingTimeoutSeconds: 1.5d);
            string targetId = manager.Update(new[] { Detection("book", 300f) }, 0d).Single().TargetId;

            Assert.That(manager.Update(new TargetDetection2D[0], 1.51d), Is.Empty);
            Assert.That(manager.TryGetTarget(targetId, out StableTargetSnapshot lost), Is.True);
            Assert.That(lost.State, Is.EqualTo(StableTargetState.Lost));
            Assert.That(manager.GetAllTargets().Single().TargetId, Is.EqualTo(targetId));
            Assert.That(manager.GetAllTargets().Single().State, Is.EqualTo(StableTargetState.Lost));
        }
    }
}
