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
        public void Undo_RestoresOnlyTheLastSlotForAnotherAcceptedSelection()
        {
            var coordinator = NewActiveCoordinator();

            Assert.That(coordinator.TryAccept(Result("selection-b", 1, "b")), Is.True);
            Assert.That(coordinator.TryUndoLastSelection(out BciTargetSelectionResult undone), Is.True);
            Assert.That(undone.SlotIndex, Is.EqualTo(1));
            Assert.That(coordinator.CurrentSelections, Is.Empty);
            Assert.That(coordinator.TryAccept(Result("selection-b-rearmed", 1, "b")), Is.True,
                "The undone slot must accept a fresh immutable result.");

            Assert.That(coordinator.TryAccept(Result("selection-a", 0, "a")), Is.True);
            Assert.That(coordinator.TryUndoLastSelection(out BciTargetSelectionResult secondUndone), Is.True);
            Assert.That(secondUndone.SlotIndex, Is.EqualTo(0));
            Assert.That(coordinator.TryAccept(Result("selection-b-duplicate", 1, "b")), Is.False,
                "The still-selected slot must remain unavailable.");
            Assert.That(coordinator.TryAccept(Result("selection-c", 2, "c")), Is.True,
                "A different free slot remains eligible after Undo.");
            Assert.That(TargetIdsFromResults(coordinator.CurrentSelections), Is.EqualTo(new[] { "b", "c" }));
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

        [Test]
        public void Reassociation_UpdatesLogicalMemberButPreservesFrozenSelectedResult()
        {
            var coordinator = new BciTargetGroupCoordinator();
            coordinator.UpdateCandidatePool(new[]
            {
                MatureAnchor("old", 0f, 100f),
                MatureAnchor("center", 1f, 200f),
                MatureAnchor("right", 2f, 300f)
            });
            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            Assert.That(coordinator.TryAccept(Result("selection-old", 0, "old")), Is.True);

            coordinator.UpdateCandidatePool(new[]
            {
                MatureAnchor("replacement", 0.012f, 104f),
                MatureAnchor("center", 1f, 200f),
                MatureAnchor("right", 2f, 300f)
            });
            IReadOnlyList<BciGroupTargetReassociationDecision> decisions =
                coordinator.EvaluateActiveGroupReassociation(false);
            Assert.That(decisions, Has.Count.EqualTo(1));
            Assert.That(decisions[0].Outcome, Is.EqualTo(BciGroupTargetReassociationOutcome.Accepted));
            Assert.That(coordinator.TryCommitReassociation(decisions[0]), Is.True);

            Assert.That(coordinator.ActiveGroup.Value.Targets[0].TargetId, Is.EqualTo("replacement"));
            Assert.That(coordinator.ActiveGroup.Value.Members[0].IsSelected, Is.True);
            Assert.That(TargetIdsFromResults(coordinator.CurrentSelections), Is.EqualTo(new[] { "old" }));

            Assert.That(coordinator.TryConfirmCurrentGroup(out ConfirmedTargetBatch batch), Is.True);
            Assert.That(TargetIdsFromResults(batch.Selections), Is.EqualTo(new[] { "old" }),
                "The immutable M8.3 result records the original accepted selection fact.");
            Assert.That(coordinator.ProcessedTargetIds, Does.Contain("replacement"));
            Assert.That(coordinator.SubmittedTargetIds, Does.Contain("replacement"));
        }

        [Test]
        public void Reassociation_IsUnavailableAfterGroupSubmit()
        {
            var coordinator = NewActiveCoordinator();
            Assert.That(coordinator.TryAccept(Result("selection-a", 0, "a")), Is.True);
            Assert.That(coordinator.TryConfirmCurrentGroup(out ConfirmedTargetBatch _), Is.True);

            Assert.That(coordinator.EvaluateActiveGroupReassociation(false), Is.Empty);
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

        private static StableWorldAnchorSnapshot MatureAnchor(string targetId, float x, float bboxX)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                "bottle",
                StableTargetState.Active,
                new Vector3(x, 0f, 2f),
                0.9f,
                new TargetBoundingBox(bboxX, 20f, 30f, 100f),
                0d,
                1d);
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
