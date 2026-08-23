using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using System.Collections.Generic;
using UnityEngine;

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
            var received = new List<BciTargetSelectionResult>();
            coordinator.TargetSelected += received.Add;
            coordinator.Open("selection-" + classIndex, Snapshot("target-0", "target-1", "target-2"));

            BciSelectionTransportResult result = coordinator.Resolve("selection-" + classIndex, classIndex);

            Assert.That(result.IsAccepted, Is.True);
            Assert.That(result.Target.TargetId, Is.EqualTo(expectedTargetId));
            Assert.That(result.Target.SlotIndex, Is.EqualTo(classIndex));
            Assert.That(received, Has.Count.EqualTo(1));
            Assert.That(received[0].SlotIndex, Is.EqualTo(classIndex));
            Assert.That(received[0].TargetId, Is.EqualTo(expectedTargetId));
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

        [Test]
        public void AcceptedDecision_PublishesExactlyOneFrozenTargetSelectedResultWithSpatialSnapshot()
        {
            var coordinator = new BciSelectionCoordinator();
            var received = new List<BciTargetSelectionResult>();
            coordinator.TargetSelected += received.Add;
            var frozenPosition = new Vector3(1.25f, 2.5f, 3.75f);
            coordinator.Open("spatial", SnapshotWithSpatialSlot0("original", frozenPosition));

            BciSelectionSnapshot liveBindingAfterChange = SnapshotWithSpatialSlot0("replacement", new Vector3(9f, 9f, 9f));
            Assert.That(liveBindingAfterChange.ResolveClassIndex(0).Target.TargetId, Is.EqualTo("replacement"));

            Assert.That(coordinator.Resolve("spatial", 0).IsAccepted, Is.True);
            Assert.That(coordinator.Resolve("spatial", 0).Rejection, Is.EqualTo(BciSelectionTransportRejection.DuplicateDecision));

            Assert.That(received, Has.Count.EqualTo(1));
            Assert.That(received[0].SelectionId, Is.EqualTo("spatial"));
            Assert.That(received[0].PredictedClassIndex, Is.EqualTo(0));
            Assert.That(received[0].SlotIndex, Is.EqualTo(0));
            Assert.That(received[0].TargetId, Is.EqualTo("original"));
            Assert.That(received[0].SemanticLabel, Is.EqualTo("cup"));
            Assert.That(received[0].HasWorldPosition, Is.True);
            Assert.That(received[0].WorldPosition, Is.EqualTo(frozenPosition));
            Assert.That(received[0].Provenance, Is.EqualTo(BciTargetSelectionResult.FrozenSnapshotProvenance));
        }

        [Test]
        public void RejectedAndAbortedSelections_DoNotPublishTargetSelected()
        {
            var coordinator = new BciSelectionCoordinator();
            var received = new List<BciTargetSelectionResult>();
            coordinator.TargetSelected += received.Add;

            coordinator.Open("invalid", Snapshot("target-0", "target-1", "target-2"));
            Assert.That(coordinator.Resolve("missing", 0).Rejection, Is.EqualTo(BciSelectionTransportRejection.UnknownSelectionId));
            coordinator.Resolve("invalid", 3);
            coordinator.Open("aborted", Snapshot("target-0", "target-1", "target-2"));
            coordinator.Abort("aborted");
            coordinator.Resolve("aborted", 2);
            coordinator.Open("empty", new BciSelectionSnapshot(new[]
            {
                new BciSelectionTarget(0, "target-0", "cup", StableTargetState.Active),
                default(BciSelectionTarget),
                new BciSelectionTarget(2, "target-2", "book", StableTargetState.TemporarilyMissing)
            }));
            coordinator.Resolve("empty", 1);
            coordinator.Open("inactive", new BciSelectionSnapshot(new[]
            {
                new BciSelectionTarget(0, "target-0", "cup", StableTargetState.Active),
                new BciSelectionTarget(1, "target-1", "bottle", StableTargetState.Active),
                new BciSelectionTarget(2, "target-2", "book", StableTargetState.TemporarilyMissing)
            }));
            coordinator.Resolve("inactive", 2);

            Assert.That(received, Is.Empty);
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

        private static BciSelectionSnapshot SnapshotWithSpatialSlot0(string targetId, Vector3 position)
        {
            return new BciSelectionSnapshot(new[]
            {
                new BciSelectionTarget(0, new StableWorldAnchorSnapshot(targetId, "cup", StableTargetState.Active, position)),
                new BciSelectionTarget(1, "target-1", "bottle", StableTargetState.Active),
                new BciSelectionTarget(2, "target-2", "book", StableTargetState.Active)
            });
        }
    }
}
