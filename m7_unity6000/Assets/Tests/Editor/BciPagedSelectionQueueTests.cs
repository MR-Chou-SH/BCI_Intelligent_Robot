using System.Collections.Generic;
using System.IO;
using BCIIntelligentRobot.Integration;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciPagedSelectionQueueTests
    {
        [Test]
        public void CrossPageSelectionUsesGlobalOrderAndStablePageState()
        {
            BciPagedSelectionQueue queue = NewQueue(8);
            Assert.That(queue.NextPage(), Is.True);
            Assert.That(queue.NextPage(), Is.True);
            Select(queue, 0);
            Assert.That(queue.PreviousPage(), Is.True);
            Assert.That(queue.PreviousPage(), Is.True);
            Select(queue, 1);
            Assert.That(queue.NextPage(), Is.True);
            Select(queue, 1);

            Assert.That(TargetIds(queue.Queue), Is.EqualTo(new[] { "target-g", "target-b", "target-e" }));
            Assert.That(queue.CurrentPage.Candidates[1].TargetId, Is.EqualTo("target-e"));
            Assert.That(queue.CurrentPage.Candidates[1].Selected, Is.True);
            Assert.That(queue.CurrentPage.Candidates[1].Active, Is.False);
        }

        [Test]
        public void PageChangeInvalidatesOldTrialAndRejectsLateClassZero()
        {
            BciPagedSelectionQueue queue = NewQueue(4);
            BciPagedSelectionTrial trial = queue.OpenTrial("old-selection");
            Assert.That(queue.NextPage(), Is.True);

            Assert.That(queue.TryAcceptResult(
                trial.SelectionId,
                0,
                "target-a",
                trial.PageEpoch,
                out BciPagedQueueEntry _,
                out string rejection), Is.False);
            Assert.That(rejection, Is.EqualTo("STALE_SELECTION_REJECTED"));
            Assert.That(queue.Queue, Is.Empty);
        }

        [Test]
        public void UndoIsQueueTailOnlyAndSubmitChunksWithoutReordering()
        {
            BciPagedSelectionQueue queue = NewQueue(5);
            Select(queue, 0);
            Select(queue, 1);
            Select(queue, 2);
            Assert.That(queue.NextPage(), Is.True);
            Select(queue, 1);
            Assert.That(queue.TryUndoLast(out BciPagedQueueEntry undone), Is.True);
            Assert.That(undone.TargetId, Is.EqualTo("target-e"));
            Select(queue, 0);

            Assert.That(queue.TrySubmit(out BciPagedCommitPlan plan), Is.True);
            Assert.That(queue.NavigationEnabled, Is.False);
            Assert.That(TargetIds(plan.OrderedEntries), Is.EqualTo(new[] { "target-a", "target-b", "target-c", "target-d" }));
            Assert.That(plan.Batches.Count, Is.EqualTo(2));
            Assert.That(plan.Batches[0].Entries.Count, Is.EqualTo(3));
            Assert.That(plan.Batches[1].Entries.Count, Is.EqualTo(1));
        }

        [Test]
        public void EmptySubmitIsNoOpAndProcessedRebuildsPagesAfterCompletion()
        {
            BciPagedSelectionQueue queue = NewQueue(4);
            Assert.That(queue.TrySubmit(out BciPagedCommitPlan _), Is.False);
            Select(queue, 0);
            Assert.That(queue.TrySubmit(out BciPagedCommitPlan plan), Is.True);
            Assert.That(queue.CompleteExecution(TargetIds(plan.OrderedEntries)), Is.True);
            Assert.That(queue.CurrentPage.Candidates[0].TargetId, Is.EqualTo("target-b"));
            Assert.That(queue.ProcessedTargetIds, Does.Contain("target-a"));
        }

        [Test]
        public void CandidateCountsOneThroughSevenHaveExpectedPageCounts()
        {
            for (int count = 1; count <= 7; count++)
            {
                BciPagedSelectionQueue queue = NewQueue(count);
                Assert.That(queue.PageCount, Is.EqualTo((count + 2) / 3), "candidate count=" + count);
            }
        }

        [Test]
        public void AppendingCandidatesKeepsExistingPageSlotsAndSelectedOverlay()
        {
            BciPagedSelectionQueue queue = NewQueue(4);
            Select(queue, 1);
            string[] firstPageBefore = new string[queue.CurrentPage.Candidates.Count];
            for (int index = 0; index < firstPageBefore.Length; index++)
                firstPageBefore[index] = queue.CurrentPage.Candidates[index].TargetId;

            Assert.That(queue.AppendCandidates(new[]
            {
                new BciPagedCandidate("block-x", "target-x", "X"),
                new BciPagedCandidate("block-y", "target-y", "Y"),
                new BciPagedCandidate("block-z", "target-z", "Z")
            }), Is.True);
            Assert.That(queue.PageCount, Is.EqualTo(3));
            Assert.That(queue.PageEpoch, Is.EqualTo(1));
            Assert.That(queue.CurrentPage.Candidates[1].Selected, Is.True);
            for (int index = 0; index < firstPageBefore.Length; index++)
                Assert.That(queue.CurrentPage.Candidates[index].TargetId, Is.EqualTo(firstPageBefore[index]));

            Assert.That(queue.NextPage(), Is.True);
            Assert.That(queue.CurrentPage.Candidates[0].TargetId, Is.EqualTo("target-d"));
            Assert.That(queue.CurrentPage.Candidates[1].TargetId, Is.EqualTo("target-x"));
            Assert.That(queue.CurrentPage.Candidates[2].TargetId, Is.EqualTo("target-y"));
            Assert.That(queue.NextPage(), Is.True);
            Assert.That(queue.CurrentPage.Candidates.Count, Is.EqualTo(1));
            Assert.That(queue.CurrentPage.Candidates[0].TargetId, Is.EqualTo("target-z"));
        }

        [Test]
        public void AppendingCandidatesCancelsPendingTrialAndRejectsItsLateResult()
        {
            BciPagedSelectionQueue queue = NewQueue(3);
            BciPagedSelectionTrial trial = queue.OpenTrial("m19-pending-before-append");
            Assert.That(queue.AppendCandidates(new[]
            {
                new BciPagedCandidate("block-x", "target-x", "X")
            }), Is.True);
            Assert.That(queue.TryAcceptResult(trial.SelectionId, 0, "target-a", trial.PageEpoch,
                out BciPagedQueueEntry _, out string rejection), Is.False);
            Assert.That(rejection, Is.EqualTo("STALE_SELECTION_REJECTED"));
        }

        [Test]
        public void CancelTrialDoesNotNavigateAndMakesLateResultStale()
        {
            BciPagedSelectionQueue queue = NewQueue(4);
            BciPagedSelectionTrial trial = queue.OpenTrial("m19-pending-cancel");
            Assert.That(queue.CancelTrial(trial.SelectionId), Is.True);
            Assert.That(queue.PageIndex, Is.EqualTo(0));
            Assert.That(queue.CurrentTrial, Is.Null);
            Assert.That(queue.TryAcceptResult(trial.SelectionId, 0, "target-a", trial.PageEpoch,
                out BciPagedQueueEntry _, out string rejection), Is.False);
            Assert.That(rejection, Is.EqualTo("STALE_SELECTION_REJECTED"));
        }

        [Test]
        public void SharedM19RequestFixtureMatchesQuestSlotContract()
        {
            string path = Path.Combine(Application.dataPath,
                "Resources/BCI/M19/m19_decode_request_v1.json");
            Assert.That(File.Exists(path), Is.True, "Python and Unity must read the same protocol fixture.");
            string json = File.ReadAllText(path);
            BciM19DecodeRequestPayload request = JsonUtility.FromJson<BciM19DecodeRequestPayload>(json);
            Assert.That(request.contractId, Is.EqualTo("m19_decode_request_v1"));
            Assert.That(request.protocolVersion, Is.EqualTo(BciSelectionTransportMessage.ProtocolVersion));
            Assert.That(request.messageType, Is.EqualTo("eeg_decode_request"));
            Assert.That(request.selectionId, Is.EqualTo("m19-contract-selection-001"));
            Assert.That(request.trialId, Is.EqualTo("m19-contract-trial-001"));
            Assert.That(request.pageId, Is.EqualTo("m16-page-1"));
            Assert.That(request.pageIndex, Is.EqualTo(0));
            Assert.That(request.pageEpoch, Is.EqualTo(7));
            Assert.That(request.activeSlotCount, Is.EqualTo(3));
            Assert.That(request.slots, Has.Length.EqualTo(3));
            Assert.That(request.slots[0].slotIndex, Is.EqualTo(0));
            Assert.That(request.slots[0].frequencyHz, Is.EqualTo(7.2f).Within(0.001f));
            Assert.That(request.slots[0].targetId, Is.EqualTo("m9-vblock-yellow-01"));
            Assert.That(request.slots[1].slotIndex, Is.EqualTo(1));
            Assert.That(request.slots[1].frequencyHz, Is.EqualTo(9f).Within(0.001f));
            Assert.That(request.slots[1].targetId, Is.EqualTo("m9-vblock-blue-01"));
            Assert.That(request.slots[2].slotIndex, Is.EqualTo(2));
            Assert.That(request.slots[2].frequencyHz, Is.EqualTo(12f).Within(0.001f));
            Assert.That(request.slots[2].targetId, Is.EqualTo("m9-vblock-green-01"));
        }

        [Test]
        public void LeftToRightSlotMappingDoesNotDependOnColorOrSemanticLabel()
        {
            var queue = new BciPagedSelectionQueue(new[]
            {
                new BciPagedCandidate("logical-yellow", "target-yellow", "Yellow"),
                new BciPagedCandidate("logical-blue", "target-blue", "Blue"),
                new BciPagedCandidate("logical-green", "target-green", "Green")
            });
            Assert.That(queue.CurrentPage.Candidates[0].Candidate.Label, Is.EqualTo("Yellow"));
            Assert.That(queue.CurrentPage.Candidates[0].SlotIndex, Is.EqualTo(0));
            Assert.That(queue.CurrentPage.Candidates[0].NominalFrequencyHz, Is.EqualTo(7.2f).Within(0.001f));
            Assert.That(queue.CurrentPage.Candidates[1].Candidate.Label, Is.EqualTo("Blue"));
            Assert.That(queue.CurrentPage.Candidates[1].SlotIndex, Is.EqualTo(1));
            Assert.That(queue.CurrentPage.Candidates[1].NominalFrequencyHz, Is.EqualTo(9f).Within(0.001f));
            Assert.That(queue.CurrentPage.Candidates[2].Candidate.Label, Is.EqualTo("Green"));
            Assert.That(queue.CurrentPage.Candidates[2].SlotIndex, Is.EqualTo(2));
            Assert.That(queue.CurrentPage.Candidates[2].NominalFrequencyHz, Is.EqualTo(12f).Within(0.001f));
        }

        private static BciPagedSelectionQueue NewQueue(int count)
        {
            var candidates = new List<BciPagedCandidate>();
            for (int index = 0; index < count; index++)
            {
                char suffix = (char)('a' + index);
                candidates.Add(new BciPagedCandidate(
                    "block_" + suffix + "_" + (index + 1),
                    "target-" + suffix,
                    suffix.ToString().ToUpperInvariant()));
            }
            return new BciPagedSelectionQueue(candidates);
        }

        private static void Select(BciPagedSelectionQueue queue, int slot)
        {
            BciPagedSelectionTrial trial = queue.OpenTrial();
            BciPagedTrialCandidate candidate = trial.CandidateForSlot(slot);
            Assert.That(candidate, Is.Not.Null);
            Assert.That(queue.TryAcceptResult(
                trial.SelectionId,
                slot,
                candidate.Candidate.TargetId,
                trial.PageEpoch,
                out BciPagedQueueEntry _,
                out string rejection), Is.True, rejection);
        }

        private static string[] TargetIds(IReadOnlyList<BciPagedQueueEntry> entries)
        {
            var ids = new string[entries.Count];
            for (int index = 0; index < entries.Count; index++)
                ids[index] = entries[index].TargetId;
            return ids;
        }
    }
}
