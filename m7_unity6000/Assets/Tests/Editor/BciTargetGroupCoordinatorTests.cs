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
        public void EmptySubmitIsNoop_WhileConfirmedBatchPreservesSelectionOrderAndLeavesUnselectedTargetsAvailable()
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
            Assert.That(TargetIds(coordinator.ActiveGroup.Value.Targets), Is.EqualTo(new[] { "b", "d", "e" }));
            Assert.That(coordinator.ProcessedTargetIds, Does.Contain("a"));
            Assert.That(coordinator.ProcessedTargetIds, Does.Contain("c"));
            Assert.That(coordinator.ProcessedTargetIds, Does.Not.Contain("b"));
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
            Assert.That(decisions.Count, Is.EqualTo(1));
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

        [Test]
        public void AuthoritativeSnapshotRebindsLegacyVisualOrderBeforeSelection()
        {
            var coordinator = new BciTargetGroupCoordinator();
            StableWorldAnchorSnapshot blue = Anchor("blue", 0f);
            StableWorldAnchorSnapshot yellow = Anchor("yellow", 1f);
            StableWorldAnchorSnapshot green = Anchor("green", 2f);
            StableWorldAnchorSnapshot red = Anchor("red", 3f);
            coordinator.UpdateCandidatePool(new[] { blue, yellow, green, red });

            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            Assert.That(TargetIds(coordinator.ActiveGroup.Value.Targets), Is.EqualTo(new[] { "blue", "yellow", "green" }));

            var authoritative = new BciSelectionSnapshot(
                "authoritative-red-first",
                1,
                new[]
                {
                    new BciSelectionTarget(0, red),
                    new BciSelectionTarget(1, green),
                    new BciSelectionTarget(2, yellow),
                });
            Assert.That(coordinator.TryApplyAuthoritativeSelectionSnapshot(authoritative), Is.True);
            Assert.That(TargetIds(coordinator.ActiveGroup.Value.Targets), Is.EqualTo(new[] { "red", "green", "yellow" }));
            Assert.That(coordinator.TryAccept(Result("selection-red", 0, "red")), Is.True);
            Assert.That(TargetIdsFromResults(coordinator.CurrentSelections), Is.EqualTo(new[] { "red" }));
        }

        [Test]
        public void AuthoritativeSnapshotSupportsInactiveGapForDynamicNextBatch()
        {
            var coordinator = new BciTargetGroupCoordinator();
            StableWorldAnchorSnapshot blue = Anchor("blue", 0f);
            StableWorldAnchorSnapshot yellow = Anchor("yellow", 1f);
            StableWorldAnchorSnapshot green = Anchor("green", 2f);
            coordinator.UpdateCandidatePool(new[] { blue, yellow, green });
            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            Assert.That(coordinator.TryConfirmCurrentGroup(out ConfirmedTargetBatch _), Is.False);

            var authoritative = new BciSelectionSnapshot(
                "authoritative-yellow-blue",
                2,
                new[]
                {
                    new BciSelectionTarget(0, yellow),
                    new BciSelectionTarget(1, null, null, StableTargetState.TemporarilyMissing),
                    new BciSelectionTarget(2, blue),
                });
            Assert.That(coordinator.TryApplyAuthoritativeSelectionSnapshot(authoritative), Is.True);
            Assert.That(coordinator.ActiveGroup.Value.Targets.Count, Is.EqualTo(3));
            Assert.That(coordinator.ActiveGroup.Value.Targets[0].TargetId, Is.EqualTo("yellow"));
            Assert.That(coordinator.ActiveGroup.Value.Targets[1].TargetId, Is.Null);
            Assert.That(coordinator.ActiveGroup.Value.Targets[2].TargetId, Is.EqualTo("blue"));
            Assert.That(coordinator.TryAccept(Result("selection-blue", 2, "blue")), Is.True);
        }

        [Test]
        public void HostBatchCloseValidatesImmutableSelectionFactsAndClearsActiveGroup()
        {
            var coordinator = NewActiveCoordinator();
            Assert.That(coordinator.TryAccept(Result("selection-c", 2, "c")), Is.True);
            Assert.That(coordinator.TryAccept(Result("selection-a", 0, "a")), Is.True);

            var expected = new[]
            {
                new ConfirmedTargetSelectionPayload
                {
                    selectionId = "selection-c",
                    predictedClassIndex = 2,
                    slotIndex = 2,
                    targetId = "c"
                },
                new ConfirmedTargetSelectionPayload
                {
                    selectionId = "selection-a",
                    predictedClassIndex = 0,
                    slotIndex = 0,
                    targetId = "a"
                }
            };

            Assert.That(coordinator.TryCloseCurrentGroupFromHost(expected, out string groupId), Is.True);
            Assert.That(groupId, Is.EqualTo("m8-group-0001"));
            Assert.That(coordinator.HasActiveGroup, Is.False);
            Assert.That(coordinator.CurrentSelections, Is.Empty);
            Assert.That(coordinator.ProcessedTargetIds, Does.Contain("a"));
            Assert.That(coordinator.ProcessedTargetIds, Does.Contain("c"));
            Assert.That(coordinator.TryActivateNextGroup(), Is.True);
            Assert.That(TargetIds(coordinator.ActiveGroup.Value.Targets), Is.EqualTo(new[] { "b", "d", "e" }));
        }

        [Test]
        public void HostBatchCloseRejectsStaleOrReorderedSelectionFacts()
        {
            var coordinator = NewActiveCoordinator();
            Assert.That(coordinator.TryAccept(Result("selection-a", 0, "a")), Is.True);

            var stale = new[]
            {
                new ConfirmedTargetSelectionPayload
                {
                    selectionId = "selection-a",
                    predictedClassIndex = 0,
                    slotIndex = 0,
                    targetId = "b"
                }
            };

            Assert.That(coordinator.TryCloseCurrentGroupFromHost(stale, out string groupId), Is.False);
            Assert.That(groupId, Is.Null);
            Assert.That(coordinator.HasActiveGroup, Is.True);
            Assert.That(coordinator.CurrentSelections, Has.Count.EqualTo(1));
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

        private static StableWorldAnchorSnapshot Anchor(string targetId, float x)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                "block",
                StableTargetState.Active,
                new Vector3(x, 0f, 2f));
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
