using System;
using System.Collections.Generic;

namespace BCIIntelligentRobot.Integration
{
    public enum BciPagedQueueState
    {
        Selecting,
        Executing
    }

    public sealed class BciPagedCandidate
    {
        public BciPagedCandidate(string logicalBlockId, string targetId, string label = "")
        {
            if (string.IsNullOrWhiteSpace(logicalBlockId) || string.IsNullOrWhiteSpace(targetId))
                throw new ArgumentException("Paged candidates require logicalBlockId and targetId.");
            LogicalBlockId = logicalBlockId;
            TargetId = targetId;
            Label = label ?? string.Empty;
        }

        public string LogicalBlockId { get; }
        public string TargetId { get; }
        public string Label { get; }
    }

    public sealed class BciPagedPageCandidate
    {
        internal BciPagedPageCandidate(BciPagedCandidate candidate, int slotIndex, float frequencyHz, bool selected)
        {
            Candidate = candidate;
            SlotIndex = slotIndex;
            NominalFrequencyHz = frequencyHz;
            Selected = selected;
        }

        public BciPagedCandidate Candidate { get; }
        public int SlotIndex { get; }
        public float NominalFrequencyHz { get; }
        public bool Active => !Selected;
        public bool Selected { get; }
        public string TargetId => Candidate.TargetId;
    }

    public sealed class BciPagedPage
    {
        internal BciPagedPage(string pageId, int pageIndex, int pageCount, int pageEpoch, IReadOnlyList<BciPagedPageCandidate> candidates)
        {
            PageId = pageId;
            PageIndex = pageIndex;
            PageCount = pageCount;
            PageEpoch = pageEpoch;
            Candidates = candidates;
        }

        public string PageId { get; }
        public int PageIndex { get; }
        public int PageCount { get; }
        public int PageEpoch { get; }
        public IReadOnlyList<BciPagedPageCandidate> Candidates { get; }
    }

    public sealed class BciPagedTrialCandidate
    {
        internal BciPagedTrialCandidate(int slotIndex, float frequencyHz, BciPagedCandidate candidate, bool active)
        {
            SlotIndex = slotIndex;
            NominalFrequencyHz = frequencyHz;
            Candidate = candidate;
            Active = active;
        }

        public int SlotIndex { get; }
        public float NominalFrequencyHz { get; }
        public BciPagedCandidate Candidate { get; }
        public bool Active { get; }
    }

    public sealed class BciPagedSelectionTrial
    {
        internal BciPagedSelectionTrial(string selectionId, string pageId, int pageEpoch, IReadOnlyList<BciPagedTrialCandidate> candidates)
        {
            SelectionId = selectionId;
            PageId = pageId;
            PageEpoch = pageEpoch;
            Candidates = candidates;
        }

        public string SelectionId { get; }
        public string PageId { get; }
        public int PageEpoch { get; }
        public IReadOnlyList<BciPagedTrialCandidate> Candidates { get; }

        public BciPagedTrialCandidate CandidateForSlot(int slotIndex)
        {
            for (int index = 0; index < Candidates.Count; index++)
                if (Candidates[index].SlotIndex == slotIndex)
                    return Candidates[index];
            return null;
        }
    }

    public sealed class BciPagedQueueEntry
    {
        internal BciPagedQueueEntry(
            string selectionId,
            BciPagedCandidate candidate,
            string pageId,
            int pageEpoch,
            int slotIndex,
            float frequencyHz,
            int buildSlotIndex)
        {
            SelectionId = selectionId;
            Candidate = candidate;
            PageId = pageId;
            PageEpoch = pageEpoch;
            SlotIndex = slotIndex;
            NominalFrequencyHz = frequencyHz;
            BuildSlotIndex = buildSlotIndex;
        }

        public string SelectionId { get; }
        public BciPagedCandidate Candidate { get; }
        public string PageId { get; }
        public int PageEpoch { get; }
        public int SlotIndex { get; }
        public float NominalFrequencyHz { get; }
        public int BuildSlotIndex { get; }
        public string TargetId => Candidate.TargetId;
    }

    public sealed class BciPagedCommitBatch
    {
        internal BciPagedCommitBatch(int batchIndex, IReadOnlyList<BciPagedQueueEntry> entries)
        {
            BatchIndex = batchIndex;
            Entries = entries;
        }

        public int BatchIndex { get; }
        public IReadOnlyList<BciPagedQueueEntry> Entries { get; }
    }

    public sealed class BciPagedCommitPlan
    {
        internal BciPagedCommitPlan(IReadOnlyList<BciPagedQueueEntry> orderedEntries, IReadOnlyList<BciPagedCommitBatch> batches)
        {
            OrderedEntries = orderedEntries;
            Batches = batches;
        }

        public IReadOnlyList<BciPagedQueueEntry> OrderedEntries { get; }
        public IReadOnlyList<BciPagedCommitBatch> Batches { get; }
    }

    public sealed class BciPagedSelectionQueue
    {
        public const string Mode = "paged_queue_v1";
        public const int PageSize = 3;
        public static readonly float[] NominalFrequenciesHz = { 7.2f, 9f, 12f };

        private readonly List<IReadOnlyList<BciPagedCandidate>> m_pages;
        private readonly Dictionary<string, BciPagedQueueEntry> m_selected =
            new Dictionary<string, BciPagedQueueEntry>(StringComparer.Ordinal);
        private readonly List<BciPagedQueueEntry> m_queue = new List<BciPagedQueueEntry>();
        private readonly HashSet<string> m_processed = new HashSet<string>(StringComparer.Ordinal);
        private readonly List<BciPagedCandidate> m_allCandidates;
        private int m_pageIndex;
        private int m_pageEpoch;
        private int m_nextSelectionNumber = 1;
        private BciPagedSelectionTrial m_currentTrial;
        private BciPagedCommitPlan m_submittedPlan;

        public BciPagedSelectionQueue(IReadOnlyList<BciPagedCandidate> candidates)
        {
            if (candidates == null || candidates.Count == 0)
                throw new ArgumentException("At least one paged candidate is required.");
            m_allCandidates = new List<BciPagedCandidate>(candidates);
            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            var logicalIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < m_allCandidates.Count; index++)
            {
                if (!targetIds.Add(m_allCandidates[index].TargetId) ||
                    !logicalIds.Add(m_allCandidates[index].LogicalBlockId))
                    throw new ArgumentException("Paged candidate identities must be unique.");
            }
            m_pages = BuildPages(m_allCandidates);
        }

        public BciPagedQueueState State { get; private set; } = BciPagedQueueState.Selecting;
        public int PageIndex => m_pageIndex;
        public int PageCount => m_pages.Count;
        public int PageEpoch => m_pageEpoch;
        public BciPagedSelectionTrial CurrentTrial => m_currentTrial;
        public BciPagedCommitPlan SubmittedPlan => m_submittedPlan;
        public IReadOnlyList<BciPagedQueueEntry> Queue => m_queue;
        public IReadOnlyCollection<string> ProcessedTargetIds => m_processed;
        public BciPagedPage CurrentPage => BuildPage();
        public bool NavigationEnabled => State == BciPagedQueueState.Selecting;
        public bool UndoEnabled => NavigationEnabled && m_queue.Count > 0;
        public bool SubmitEnabled => NavigationEnabled && m_queue.Count > 0;

        public bool NextPage()
        {
            return NavigationEnabled && m_pageIndex < m_pages.Count - 1 && NavigateTo(m_pageIndex + 1);
        }

        public bool PreviousPage()
        {
            return NavigationEnabled && m_pageIndex > 0 && NavigateTo(m_pageIndex - 1);
        }

        /// <summary>Invalidate one pending trigger trial without changing page state.</summary>
        public bool CancelTrial(string selectionId)
        {
            if (m_currentTrial == null ||
                !string.Equals(selectionId, m_currentTrial.SelectionId, StringComparison.Ordinal))
                return false;
            m_currentTrial = null;
            return true;
        }

        /// <summary>
        /// Append new targets after the current candidate set. Existing page
        /// membership and slot assignments never move; only the former final
        /// partial page may receive new entries in its empty slots.
        /// </summary>
        public bool AppendCandidates(IReadOnlyList<BciPagedCandidate> candidates)
        {
            if (!NavigationEnabled || candidates == null || candidates.Count == 0)
                return false;

            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            var logicalIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < m_allCandidates.Count; index++)
            {
                targetIds.Add(m_allCandidates[index].TargetId);
                logicalIds.Add(m_allCandidates[index].LogicalBlockId);
            }
            for (int index = 0; index < candidates.Count; index++)
            {
                BciPagedCandidate candidate = candidates[index];
                if (candidate == null || !targetIds.Add(candidate.TargetId) ||
                    !logicalIds.Add(candidate.LogicalBlockId))
                    throw new ArgumentException("Appended paged candidates must have unique TargetIds and logical IDs.");
            }

            m_currentTrial = null;
            var flattened = new List<BciPagedCandidate>();
            for (int pageIndex = 0; pageIndex < m_pages.Count; pageIndex++)
                for (int slotIndex = 0; slotIndex < m_pages[pageIndex].Count; slotIndex++)
                    flattened.Add(m_pages[pageIndex][slotIndex]);
            flattened.AddRange(candidates);

            m_pages.Clear();
            m_pages.AddRange(BuildPages(flattened));
            if (m_pages.Count == 0)
                m_pages.Add(new List<BciPagedCandidate>());
            m_allCandidates.AddRange(candidates);
            m_pageIndex = Math.Min(m_pageIndex, m_pages.Count - 1);
            m_pageEpoch++;
            return true;
        }

        public BciPagedSelectionTrial OpenTrial(string selectionId = null)
        {
            if (!NavigationEnabled || m_currentTrial != null)
                return null;
            if (string.IsNullOrWhiteSpace(selectionId))
                selectionId = "m16-selection-" + (m_nextSelectionNumber++).ToString("D4");
            var candidates = new List<BciPagedTrialCandidate>();
            BciPagedPage page = CurrentPage;
            for (int index = 0; index < page.Candidates.Count; index++)
            {
                BciPagedPageCandidate candidate = page.Candidates[index];
                candidates.Add(new BciPagedTrialCandidate(
                    candidate.SlotIndex,
                    candidate.NominalFrequencyHz,
                    candidate.Candidate,
                    candidate.Active));
            }
            m_currentTrial = new BciPagedSelectionTrial(selectionId, page.PageId, page.PageEpoch, candidates);
            return m_currentTrial;
        }

        public bool TryAcceptResult(
            string selectionId,
            int slotIndex,
            string targetId,
            int pageEpoch,
            out BciPagedQueueEntry entry,
            out string rejection)
        {
            entry = null;
            rejection = null;
            if (m_currentTrial == null || !string.Equals(selectionId, m_currentTrial.SelectionId, StringComparison.Ordinal))
            {
                rejection = "STALE_SELECTION_REJECTED";
                return false;
            }
            if (pageEpoch != m_currentTrial.PageEpoch)
            {
                rejection = "STALE_SELECTION_REJECTED";
                return false;
            }
            BciPagedTrialCandidate candidate = m_currentTrial.CandidateForSlot(slotIndex);
            if (candidate == null || !string.Equals(candidate.Candidate.TargetId, targetId, StringComparison.Ordinal))
            {
                rejection = "STALE_SELECTION_REJECTED";
                return false;
            }
            if (!candidate.Active)
            {
                m_currentTrial = null;
                rejection = m_selected.ContainsKey(targetId) ? "DUPLICATE_TARGET" : "TARGET_INACTIVE";
                return false;
            }
            if (m_selected.ContainsKey(targetId))
            {
                m_currentTrial = null;
                rejection = "DUPLICATE_TARGET";
                return false;
            }

            int buildSlot = m_queue.Count < 4 ? m_queue.Count : -1;
            entry = new BciPagedQueueEntry(
                selectionId,
                candidate.Candidate,
                m_currentTrial.PageId,
                m_currentTrial.PageEpoch,
                candidate.SlotIndex,
                candidate.NominalFrequencyHz,
                buildSlot);
            m_queue.Add(entry);
            m_selected.Add(targetId, entry);
            m_currentTrial = null;
            return true;
        }

        public bool TryUndoLast(out BciPagedQueueEntry undone)
        {
            undone = null;
            if (!UndoEnabled)
                return false;
            int last = m_queue.Count - 1;
            undone = m_queue[last];
            m_queue.RemoveAt(last);
            m_selected.Remove(undone.TargetId);
            return true;
        }

        public bool TrySubmit(out BciPagedCommitPlan plan)
        {
            plan = null;
            if (!SubmitEnabled)
                return false;
            var batches = new List<BciPagedCommitBatch>();
            var current = new List<BciPagedQueueEntry>();
            var slots = new HashSet<int>();
            for (int index = 0; index < m_queue.Count; index++)
            {
                BciPagedQueueEntry entry = m_queue[index];
                if (current.Count > 0 && (current.Count >= PageSize || slots.Contains(entry.SlotIndex)))
                {
                    batches.Add(new BciPagedCommitBatch(batches.Count, current.ToArray()));
                    current = new List<BciPagedQueueEntry>();
                    slots.Clear();
                }
                current.Add(entry);
                slots.Add(entry.SlotIndex);
            }
            if (current.Count > 0)
                batches.Add(new BciPagedCommitBatch(batches.Count, current.ToArray()));
            plan = new BciPagedCommitPlan(m_queue.ToArray(), batches.ToArray());
            m_submittedPlan = plan;
            State = BciPagedQueueState.Executing;
            m_currentTrial = null;
            return true;
        }

        public bool CompleteExecution(IReadOnlyList<string> processedTargetIds)
        {
            if (State != BciPagedQueueState.Executing || m_submittedPlan == null || processedTargetIds == null)
                return false;
            if (processedTargetIds.Count != m_submittedPlan.OrderedEntries.Count)
                return false;
            for (int index = 0; index < processedTargetIds.Count; index++)
            {
                if (!string.Equals(processedTargetIds[index], m_submittedPlan.OrderedEntries[index].TargetId, StringComparison.Ordinal))
                    return false;
            }
            for (int index = 0; index < processedTargetIds.Count; index++)
                m_processed.Add(processedTargetIds[index]);
            m_queue.Clear();
            m_selected.Clear();
            m_submittedPlan = null;
            State = BciPagedQueueState.Selecting;
            m_pageEpoch++;
            List<BciPagedCandidate> remaining = m_allCandidates.FindAll(
                candidate => !m_processed.Contains(candidate.TargetId));
            m_pages.Clear();
            m_pages.AddRange(BuildPages(remaining));
            if (m_pages.Count == 0)
                m_pages.Add(new List<BciPagedCandidate>());
            m_pageIndex = 0;
            return true;
        }

        private bool NavigateTo(int pageIndex)
        {
            m_currentTrial = null;
            m_pageIndex = pageIndex;
            m_pageEpoch++;
            return true;
        }

        private BciPagedPage BuildPage()
        {
            var candidates = new List<BciPagedPageCandidate>();
            IReadOnlyList<BciPagedCandidate> source = m_pages[m_pageIndex];
            for (int index = 0; index < source.Count; index++)
            {
                bool selected = m_selected.ContainsKey(source[index].TargetId);
                candidates.Add(new BciPagedPageCandidate(source[index], index, NominalFrequenciesHz[index], selected));
            }
            return new BciPagedPage("m16-page-" + (m_pageIndex + 1), m_pageIndex, m_pages.Count, m_pageEpoch, candidates);
        }

        private static List<IReadOnlyList<BciPagedCandidate>> BuildPages(IReadOnlyList<BciPagedCandidate> candidates)
        {
            var pages = new List<IReadOnlyList<BciPagedCandidate>>();
            for (int start = 0; start < candidates.Count; start += PageSize)
            {
                int count = Math.Min(PageSize, candidates.Count - start);
                var page = new List<BciPagedCandidate>();
                for (int index = 0; index < count; index++)
                    page.Add(candidates[start + index]);
                pages.Add(page);
            }
            return pages;
        }
    }
}
