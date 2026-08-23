using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;

namespace BCIIntelligentRobot.Tests
{
    public class BciSelectionCoordinatorTests
    {
        [TestCase(0, "target-0")]
        [TestCase(1, "target-1")]
        [TestCase(2, "target-2")]
        public void Decision_ResolvesEveryClassThroughItsFrozenSnapshot(int classIndex, string expectedTargetId)
        {
            var coordinator = new BciSelectionCoordinator();
            coordinator.Open("selection-" + classIndex, Snapshot("target-0", "target-1", "target-2"));

            BciSelectionTransportResult result = coordinator.Resolve("selection-" + classIndex, classIndex);

            Assert.That(result.IsAccepted, Is.True);
            Assert.That(result.Target.TargetId, Is.EqualTo(expectedTargetId));
            Assert.That(result.Target.SlotIndex, Is.EqualTo(classIndex));
        }

        [Test]
        public void InvalidAndUnknownDecisions_AreRejectedAndTerminal()
        {
            var coordinator = new BciSelectionCoordinator();
            coordinator.Open("invalid", Snapshot("target-0", "target-1", "target-2"));

            Assert.That(coordinator.Resolve("missing", 0).Rejection, Is.EqualTo(BciSelectionTransportRejection.UnknownSelectionId));
            Assert.That(coordinator.Resolve("invalid", 3).Rejection, Is.EqualTo(BciSelectionTransportRejection.InvalidClassIndex));
            Assert.That(coordinator.Resolve("invalid", 0).Rejection, Is.EqualTo(BciSelectionTransportRejection.DuplicateDecision));
        }

        [Test]
        public void DuplicateDecision_DoesNotResolveTwice()
        {
            var coordinator = new BciSelectionCoordinator();
            coordinator.Open("once", Snapshot("target-0", "target-1", "target-2"));

            Assert.That(coordinator.Resolve("once", 1).IsAccepted, Is.True);
            Assert.That(coordinator.Resolve("once", 1).Rejection, Is.EqualTo(BciSelectionTransportRejection.DuplicateDecision));
        }

        [Test]
        public void BindingChangeAfterOpen_CannotChangeResolvedTarget()
        {
            var coordinator = new BciSelectionCoordinator();
            coordinator.Open("frozen", Snapshot("original", "target-1", "target-2"));
            BciSelectionSnapshot liveBindingAfterChange = Snapshot("replacement", "target-1", "target-2");

            Assert.That(liveBindingAfterChange.ResolveClassIndex(0).Target.TargetId, Is.EqualTo("replacement"));
            Assert.That(coordinator.Resolve("frozen", 0).Target.TargetId, Is.EqualTo("original"));
        }

        [Test]
        public void Abort_TerminatesTheSnapshotAndRejectsDelayedDecision()
        {
            var coordinator = new BciSelectionCoordinator();
            coordinator.Open("aborted", Snapshot("target-0", "target-1", "target-2"));

            Assert.That(coordinator.Abort("aborted").IsAccepted, Is.True);
            Assert.That(
                coordinator.Resolve("aborted", 1).Rejection,
                Is.EqualTo(BciSelectionTransportRejection.DuplicateDecision));
        }

        private static BciSelectionSnapshot Snapshot(string slot0, string slot1, string slot2)
        {
            return new BciSelectionSnapshot(new[]
            {
                new BciSelectionTarget(0, slot0, "cup", StableTargetState.Active),
                new BciSelectionTarget(1, slot1, "bottle", StableTargetState.Active),
                new BciSelectionTarget(2, slot2, "book", StableTargetState.Active)
            });
        }
    }
}
