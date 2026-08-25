using System.Collections.Generic;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciTargetGroupCoordinatorTests
    {
        [Test]
        public void MoreThanThreeCandidates_ActivatesOnlyTheFirstLeftToRightGroupAndFreezesIt()
        {
            var coordinator = new BciTargetGroupCoordinator();
            coordinator.UpdateCandidatePool(Candidates("a", "b", "c", "d", "e"));

            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            BciActiveTargetGroup group = coordinator.ActiveGroup.Value;
            Assert.That(TargetIds(group.Targets), Is.EqualTo(new[] { "a", "b", "c" }));

            coordinator.UpdateCandidatePool(Candidates("c", "b", "a", "d", "e"));
            Assert.That(TargetIds(coordinator.ActiveGroup.Value.Targets), Is.EqualTo(new[] { "a", "b", "c" }));
        }

        [Test]
        public void SelectedTargetCannotReenterAndUndoRestoresItsOriginalSlot()
        {
            var coordinator = NewActiveCoordinator();
            var changes = new List<string>();
            coordinator.GroupSlotSelectionChanged += (slot, selected) => changes.Add(slot + ":" + selected);

            Assert.That(coordinator.TryAccept(Result("selection-a", 0, "a")), Is.True);
            Assert.That(coordinator.TryAccept(Result("selection-a-duplicate", 0, "a")), Is.False);
            Assert.That(coordinator.TryAccept(Result("selection-c", 2, "c")), Is.True);
            Assert.That(TargetIdsFromResults(coordinator.CurrentSelections), Is.EqualTo(new[] { "a", "c" }));

            Assert.That(coordinator.TryUndoLastSelection(out BciTargetSelectionResult undone), Is.True);
            Assert.That(undone.TargetId, Is.EqualTo("c"));
            Assert.That(TargetIdsFromResults(coordinator.CurrentSelections), Is.EqualTo(new[] { "a" }));
            Assert.That(changes, Is.EqualTo(new[] { "0:True", "2:True", "2:False" }));
        }

        [Test]
        public void EmptySubmitIsNoop_WhileConfirmedBatchPreservesSelectionOrderAndStartsNextGroup()
        {
            var coordinator = NewActiveCoordinator();
            Assert.That(coordinator.TryConfirmCurrentGroup(out ConfirmedTargetBatch empty), Is.False);
            Assert.That(empty, Is.Null);

            Assert.That(coordinator.TryAccept(Result("selection-c", 2, "c")), Is.True);
            Assert.That(coordinator.TryAccept(Result("selection-a", 0, "a")), Is.True);
            Assert.That(coordinator.TryConfirmCurrentGroup(out ConfirmedTargetBatch batch), Is.True);
            Assert.That(TargetIdsFromResults(batch.Selections), Is.EqualTo(new[] { "c", "a" }));
            Assert.That(batch.Provenance, Is.EqualTo(ConfirmedTargetBatch.ProvenanceValue));
            ConfirmedTargetBatchPayload payload = ConfirmedTargetBatchPayload.From(batch);
            Assert.That(new[] { payload.selections[0].targetId, payload.selections[1].targetId },
                Is.EqualTo(new[] { "c", "a" }));
            Assert.That(coordinator.TryConfirmCurrentGroup(out ConfirmedTargetBatch duplicateSubmit), Is.False);
            Assert.That(duplicateSubmit, Is.Null);

            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            Assert.That(TargetIds(coordinator.ActiveGroup.Value.Targets), Is.EqualTo(new[] { "d", "e" }));
        }

        [Test]
        public void AbortedDelayedSelectionDoesNotBecomeBatchMembership()
        {
            var selection = new BciSelectionCoordinator();
            var coordinator = NewActiveCoordinator();
            selection.TargetSelected += result => coordinator.TryAccept(result);
            selection.Open("pending", Snapshot("a", "b", "c"));

            Assert.That(selection.Abort("pending").IsAccepted, Is.True);
            Assert.That(selection.Resolve("pending", 0).Rejection, Is.EqualTo(BciSelectionTransportRejection.DuplicateDecision));
            Assert.That(coordinator.CurrentSelections, Is.Empty);
        }

        private static BciTargetGroupCoordinator NewActiveCoordinator()
        {
            var coordinator = new BciTargetGroupCoordinator();
            coordinator.UpdateCandidatePool(Candidates("a", "b", "c", "d", "e"));
            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            return coordinator;
        }

        private static StableWorldAnchorSnapshot[] Candidates(params string[] targetIds)
        {
            var values = new StableWorldAnchorSnapshot[targetIds.Length];
            for (int index = 0; index < targetIds.Length; index++)
            {
                values[index] = new StableWorldAnchorSnapshot(
                    targetIds[index],
                    index < 3 ? "bottle" : "cup",
                    StableTargetState.Active,
                    new Vector3(index, 0f, 2f));
            }
            return values;
        }

        private static BciTargetSelectionResult Result(string selectionId, int slot, string targetId)
        {
            return new BciTargetSelectionResult(
                selectionId,
                slot,
                new BciSelectionTarget(slot, new StableWorldAnchorSnapshot(
                    targetId, "bottle", StableTargetState.Active, new Vector3(slot, 0f, 2f))),
                System.DateTime.UtcNow);
        }

        private static BciSelectionSnapshot Snapshot(string slot0, string slot1, string slot2)
        {
            return new BciSelectionSnapshot(new[]
            {
                new BciSelectionTarget(0, new StableWorldAnchorSnapshot(slot0, "bottle", StableTargetState.Active, Vector3.zero)),
                new BciSelectionTarget(1, new StableWorldAnchorSnapshot(slot1, "bottle", StableTargetState.Active, Vector3.right)),
                new BciSelectionTarget(2, new StableWorldAnchorSnapshot(slot2, "bottle", StableTargetState.Active, Vector3.left)),
            });
        }

        private static string[] TargetIds(IReadOnlyList<StableWorldAnchorSnapshot> targets)
        {
            var ids = new string[targets.Count];
            for (int index = 0; index < ids.Length; index++)
                ids[index] = targets[index].TargetId;
            return ids;
        }

        private static string[] TargetIdsFromResults(IReadOnlyList<BciTargetSelectionResult> results)
        {
            var ids = new string[results.Count];
            for (int index = 0; index < ids.Length; index++)
                ids[index] = results[index].TargetId;
            return ids;
        }
    }
}
