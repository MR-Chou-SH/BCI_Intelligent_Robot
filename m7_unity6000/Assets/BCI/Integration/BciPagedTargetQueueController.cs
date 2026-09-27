using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Vision;
using BCIIntelligentRobot.VirtualManipulation;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    public enum BciM19PresentationPolicy
    {
        DemoPreview,
        ResearchStrict
    }

    /// <summary>
    /// Explicit opt-in M16 page/queue controller. Legacy grouped selection is
    /// owned by BciTargetBatchController and is not routed through this class.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class BciPagedTargetQueueController : MonoBehaviour
    {
        private BciSsvepTargetBinding m_binding;
        private BciSelectionTransportClient m_transport;
        private BciPagedSelectionQueue m_queue;
        private readonly Dictionary<string, StableWorldAnchorSnapshot> m_candidatesByTargetId =
            new Dictionary<string, StableWorldAnchorSnapshot>(StringComparer.Ordinal);
        private readonly Dictionary<string, BciTargetSelectionResult> m_resultsBySelectionId =
            new Dictionary<string, BciTargetSelectionResult>(StringComparer.Ordinal);
        private readonly Dictionary<string, string> m_logicalBlockIdByTargetId =
            new Dictionary<string, string>(StringComparer.Ordinal);
        private readonly Dictionary<string, int> m_catalogOrderByTargetId =
            new Dictionary<string, int>(StringComparer.Ordinal);
        private bool m_useCatalogCandidateOrder;
        private string m_activePageGroupId;
        private string m_pendingSelectionId;
        private string m_pendingTrialId;
        private float m_pendingTrialStartedAtUnscaledTime;
        [SerializeField] private BciM19PresentationPolicy m_m19PresentationPolicy = BciM19PresentationPolicy.DemoPreview;
        [SerializeField, Min(1f)] private float m_m19PendingTimeoutSeconds = 30f;
        private BciPagedQueueHud m_hud;
        private int m_nextHostBatchIndex;
        private bool m_initialized;
        private string m_robotExecutionStatus = "READY";

        public bool IsInitialized => m_initialized;
        public BciPagedSelectionQueue Queue => m_queue;
        public BciPagedPage CurrentPage => m_queue == null ? null : m_queue.CurrentPage;
        public BciPagedQueueHud Hud => m_hud;
        public string RobotExecutionStatus => m_robotExecutionStatus;
        public BciM19PresentationPolicy M19PresentationPolicy => m_m19PresentationPolicy;
        public float M19PendingTimeoutSeconds => m_m19PendingTimeoutSeconds;
        public static bool HasPendingTrialTimedOut(float startedAt, float now, float timeoutSeconds)
        {
            return timeoutSeconds > 0f && now - startedAt >= timeoutSeconds;
        }
        public bool CanStartTriggeredTrial
        {
            get
            {
                if (m_queue == null || m_queue.State != BciPagedQueueState.Selecting ||
                    m_queue.CurrentTrial != null || !string.IsNullOrWhiteSpace(m_pendingSelectionId))
                    return false;
                BciPagedPage page = m_queue.CurrentPage;
                for (int index = 0; index < page.Candidates.Count; index++)
                    if (page.Candidates[index].Active)
                        return true;
                return false;
            }
        }
        private bool m_runtimeDependencyDiagnosticEmitted;

        private void Update()
        {
            if (string.IsNullOrWhiteSpace(m_pendingTrialId) ||
                !HasPendingTrialTimedOut(
                    m_pendingTrialStartedAtUnscaledTime,
                    Time.unscaledTime,
                    m_m19PendingTimeoutSeconds))
                return;

            string selectionId = m_pendingSelectionId;
            Debug.LogWarning("M19_TRIGGER response_timeout selection_id=" + selectionId +
                " timeout_seconds=" + m_m19PendingTimeoutSeconds.ToString("F1") +
                " action=fail_closed_rearm_requires_gaze_exit", this);
            AbortPendingTrial("decoder_response_timeout");
        }

        /// <summary>
        /// Returns a one-time world-space pose for the M16 command HUD. The
        /// pose is derived from the stable workspace anchors, not from the
        /// camera hierarchy, so the HUD remains fixed while the user looks
        /// around. The camera is used only once to choose an initial facing
        /// direction for readability.
        /// </summary>
        public bool TryGetWorldSpacePanelPose(out Vector3 position, out Quaternion rotation)
        {
            position = Vector3.zero;
            rotation = Quaternion.identity;
            if (m_candidatesByTargetId.Count == 0)
                return false;

            Vector3 center = Vector3.zero;
            foreach (StableWorldAnchorSnapshot anchor in m_candidatesByTargetId.Values)
                center += anchor.WorldPosition;
            center /= m_candidatesByTargetId.Count;
            position = center + Vector3.up * 0.30f;

            Camera camera = Camera.main;
            if (camera == null)
                return false;
            Vector3 towardCamera = camera.transform.position - position;
            towardCamera = Vector3.ProjectOnPlane(towardCamera, Vector3.up);
            if (towardCamera.sqrMagnitude < 0.0001f)
                towardCamera = Vector3.forward;
            rotation = Quaternion.LookRotation(towardCamera.normalized, Vector3.up);
            return true;
        }

        public void Initialize(BciSsvepTargetBinding binding, BciSelectionTransportClient transport)
        {
            M9VirtualBlockCatalogData catalog;
            try
            {
                catalog = M9VirtualBlockCatalog.LoadFromResources();
            }
            catch (Exception error)
            {
                Debug.LogError("M16_PAGE logical_mapping_unavailable error=" + error.Message, this);
                return;
            }
            Initialize(binding, transport, catalog);
        }

        public void Initialize(
            BciSsvepTargetBinding binding,
            BciSelectionTransportClient transport,
            M9VirtualBlockCatalogData catalog,
            bool useCatalogCandidateOrder = false)
        {
            if (m_initialized || binding == null || transport == null || catalog == null || catalog.blocks == null)
                return;

            m_logicalBlockIdByTargetId.Clear();
            m_catalogOrderByTargetId.Clear();
            m_useCatalogCandidateOrder = useCatalogCandidateOrder;
            for (int index = 0; index < catalog.blocks.Length; index++)
            {
                M9VirtualBlockDefinition definition = catalog.blocks[index];
                if (definition == null || string.IsNullOrWhiteSpace(definition.targetId) ||
                    string.IsNullOrWhiteSpace(definition.logicalBlockId) ||
                    definition.sourceKind != M9VirtualBlockCatalog.VirtualSourceKind ||
                    m_logicalBlockIdByTargetId.ContainsKey(definition.targetId))
                {
                    m_logicalBlockIdByTargetId.Clear();
                    Debug.LogError("M16_PAGE logical_mapping_invalid catalog_entry=" + index, this);
                    return;
                }
                m_logicalBlockIdByTargetId.Add(definition.targetId, definition.logicalBlockId);
                if (m_useCatalogCandidateOrder)
                    m_catalogOrderByTargetId.Add(definition.targetId, index);
            }

            m_binding = binding;
            m_transport = transport;
            m_hud = GetComponent<BciPagedQueueHud>();
            if (m_hud == null)
                m_hud = gameObject.AddComponent<BciPagedQueueHud>();
            m_hud.Initialize(this);
            // EnableBatchGroupMode republishes any stable-anchor candidates
            // that existed before this controller subscribed. Mark the
            // controller ready first so the initial page can be built from
            // that authoritative event instead of waiting for another frame.
            m_initialized = true;

            m_binding.HudCandidatesChanged += OnHudCandidatesChanged;
            m_transport.TargetSelected += OnTargetSelected;
            m_transport.SelectionOpenedWithMetadata += OnSelectionOpened;
            m_transport.AuthoritativeSelectionOpeningWithMetadata += ValidatePageOpening;
            m_transport.SelectionTerminated += OnSelectionTerminated;
            m_transport.M19DecodeAcknowledged += OnM19DecodeAcknowledged;
            m_transport.HostBatchCloseRequested += OnHostBatchCloseRequested;

            if (!binding.EnableBatchGroupMode())
            {
                Unsubscribe();
                m_initialized = false;
                Debug.LogWarning("M16_PAGE initialization rejected: requires ViewLockedHud batch presentation.", this);
                return;
            }
            if (!binding.EnablePagedQueueMode())
            {
                Unsubscribe();
                m_initialized = false;
                Debug.LogWarning("M16_PAGE initialization rejected: paged binding mode unavailable.", this);
                return;
            }

            if (m_queue != null)
                RefreshCurrentPagePresentation();
            Debug.Log("M16_PAGE controller_initialized mode=" + BciPagedSelectionQueue.Mode, this);
        }

        public void SetRobotExecutionStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
                return;
            m_robotExecutionStatus = status.Trim().ToUpperInvariant();
            if (m_hud != null)
                m_hud.Refresh();
        }

        private void OnHudCandidatesChanged(IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            if (!m_initialized || candidates == null || candidates.Count == 0)
                return;
            if (m_queue == null)
            {
                List<StableWorldAnchorSnapshot> orderedCandidates = OrderInitialCandidates(candidates);
                var pagedCandidates = new List<BciPagedCandidate>(orderedCandidates.Count);
                for (int index = 0; index < orderedCandidates.Count; index++)
                {
                    StableWorldAnchorSnapshot anchor = orderedCandidates[index];
                    if (!m_logicalBlockIdByTargetId.TryGetValue(anchor.TargetId, out string logicalBlockId))
                    {
                        Debug.LogError("M16_PAGE candidate_rejected reason=explicit_logical_mapping_missing target_id=" +
                            anchor.TargetId, this);
                        return;
                    }
                    m_candidatesByTargetId[anchor.TargetId] = anchor;
                    pagedCandidates.Add(new BciPagedCandidate(logicalBlockId, anchor.TargetId, anchor.ClassName));
                }
                m_queue = new BciPagedSelectionQueue(pagedCandidates);
                ActivateCurrentPage("initial");
                m_hud.Refresh();
                EmitRuntimeDependencyDiagnostic();
                return;
            }

            var appended = new List<BciPagedCandidate>();
            var anchorsByNewTarget = new List<StableWorldAnchorSnapshot>();
            for (int index = 0; index < candidates.Count; index++)
            {
                StableWorldAnchorSnapshot anchor = candidates[index];
                if (m_candidatesByTargetId.ContainsKey(anchor.TargetId))
                {
                    m_candidatesByTargetId[anchor.TargetId] = anchor;
                    continue;
                }
                if (!m_logicalBlockIdByTargetId.TryGetValue(anchor.TargetId, out string logicalBlockId))
                {
                    Debug.LogWarning("M16_PAGE candidate_ignored reason=explicit_logical_mapping_missing target_id=" +
                        anchor.TargetId, this);
                    continue;
                }
                appended.Add(new BciPagedCandidate(logicalBlockId, anchor.TargetId, anchor.ClassName));
                anchorsByNewTarget.Add(anchor);
            }
            if (appended.Count == 0)
                return;

            if (m_useCatalogCandidateOrder)
            {
                var orderedNew = new List<KeyValuePair<BciPagedCandidate, StableWorldAnchorSnapshot>>(appended.Count);
                for (int index = 0; index < appended.Count; index++)
                    orderedNew.Add(new KeyValuePair<BciPagedCandidate, StableWorldAnchorSnapshot>(
                        appended[index], anchorsByNewTarget[index]));
                orderedNew.Sort((left, right) => CompareCatalogOrder(left.Value, right.Value));
                appended.Clear();
                anchorsByNewTarget.Clear();
                for (int index = 0; index < orderedNew.Count; index++)
                {
                    appended.Add(orderedNew[index].Key);
                    anchorsByNewTarget.Add(orderedNew[index].Value);
                }
            }

            AbortPendingTrial("candidate_set_changed");
            if (!m_queue.AppendCandidates(appended))
            {
                Debug.LogWarning("M16_PAGE candidate_append_rejected count=" + appended.Count, this);
                return;
            }
            for (int index = 0; index < anchorsByNewTarget.Count; index++)
                m_candidatesByTargetId[anchorsByNewTarget[index].TargetId] = anchorsByNewTarget[index];
            ActivateCurrentPage("candidates_appended");
            m_hud.Refresh();
        }

        public static List<StableWorldAnchorSnapshot> OrderInitialCandidates(
            IReadOnlyList<StableWorldAnchorSnapshot> candidates,
            M9VirtualBlockCatalogData catalog,
            bool useCatalogOrder)
        {
            if (candidates == null)
                throw new ArgumentNullException(nameof(candidates));
            if (!useCatalogOrder)
                return new List<StableWorldAnchorSnapshot>(candidates);
            if (catalog == null || catalog.blocks == null)
                throw new ArgumentNullException(nameof(catalog));

            var orderByTargetId = new Dictionary<string, int>(StringComparer.Ordinal);
            for (int index = 0; index < catalog.blocks.Length; index++)
                orderByTargetId[catalog.blocks[index].targetId] = index;
            var ordered = new List<StableWorldAnchorSnapshot>(candidates);
            ordered.Sort((left, right) => CompareCatalogOrder(left, right, orderByTargetId));
            return ordered;
        }

        private List<StableWorldAnchorSnapshot> OrderInitialCandidates(
            IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            if (!m_useCatalogCandidateOrder)
                return new List<StableWorldAnchorSnapshot>(candidates);
            var ordered = new List<StableWorldAnchorSnapshot>(candidates);
            ordered.Sort(CompareCatalogOrder);
            return ordered;
        }

        private int CompareCatalogOrder(StableWorldAnchorSnapshot left, StableWorldAnchorSnapshot right)
        {
            return CompareCatalogOrder(left, right, m_catalogOrderByTargetId);
        }

        private static int CompareCatalogOrder(
            StableWorldAnchorSnapshot left,
            StableWorldAnchorSnapshot right,
            IReadOnlyDictionary<string, int> orderByTargetId)
        {
            int leftOrder = orderByTargetId.TryGetValue(left.TargetId, out int leftValue) ? leftValue : int.MaxValue;
            int rightOrder = orderByTargetId.TryGetValue(right.TargetId, out int rightValue) ? rightValue : int.MaxValue;
            int byCatalog = leftOrder.CompareTo(rightOrder);
            return byCatalog != 0
                ? byCatalog
                : string.Compare(left.TargetId, right.TargetId, StringComparison.Ordinal);
        }

        public bool NextPage()
        {
            if (m_queue == null || m_queue.PageIndex >= m_queue.PageCount - 1)
                return false;
            AbortPendingTrial("page_changed");
            return Navigate(m_queue.NextPage());
        }

        public bool PreviousPage()
        {
            if (m_queue == null || m_queue.PageIndex <= 0)
                return false;
            AbortPendingTrial("page_changed");
            return Navigate(m_queue.PreviousPage());
        }

        private bool Navigate(bool changed)
        {
            if (!changed || m_queue == null)
                return false;
            ActivateCurrentPage("page_changed");
            Debug.Log("M16_PAGE event=page_changed page=" + (m_queue.PageIndex + 1) + "/" +
                m_queue.PageCount + " page_epoch=" + m_queue.PageEpoch, this);
            return true;
        }

        public bool BeginUserTriggeredSelection()
        {
            if (!CanStartTriggeredTrial)
                return false;

            BciPagedPage page = m_queue.CurrentPage;
            string selectionId = "m19-selection-" + Guid.NewGuid().ToString("N");
            string trialId = "m19-trial-" + Guid.NewGuid().ToString("N");
            DateTime triggerUtc = DateTime.UtcNow;
            BciPagedSelectionTrial trial = m_queue.OpenTrial(selectionId);
            if (trial == null)
                return false;

            var frozenTargets = new BciSelectionTarget[BciPagedSelectionQueue.PageSize];
            var activeSlots = new List<BciEegDecodeSlotPayload>();
            for (int index = 0; index < trial.Candidates.Count; index++)
            {
                BciPagedTrialCandidate candidate = trial.Candidates[index];
                if (!candidate.Active)
                    continue;
                if (!TryFindAnchor(candidate.Candidate.TargetId, out StableWorldAnchorSnapshot anchor) ||
                    anchor.State != StableTargetState.Active)
                {
                    m_queue.CancelTrial(selectionId);
                    Debug.LogWarning("M19_TRIGGER rejected reason=active_anchor_unavailable target_id=" +
                        candidate.Candidate.TargetId, this);
                    return false;
                }

                frozenTargets[candidate.SlotIndex] = new BciSelectionTarget(candidate.SlotIndex, anchor);
                activeSlots.Add(new BciEegDecodeSlotPayload
                {
                    slotIndex = candidate.SlotIndex,
                    frequencyHz = candidate.NominalFrequencyHz,
                    targetId = candidate.Candidate.TargetId,
                    semanticLabel = candidate.Candidate.Label,
                    provenance = "m16_frozen_page_snapshot"
                });
            }
            if (activeSlots.Count < 1 || activeSlots.Count > BciPagedSelectionQueue.PageSize)
            {
                m_queue.CancelTrial(selectionId);
                return false;
            }

            bool strict = m_m19PresentationPolicy == BciM19PresentationPolicy.ResearchStrict;
            if (strict && !m_binding.BeginM19FormalStimulusEpoch(selectionId))
            {
                m_queue.CancelTrial(selectionId);
                Debug.LogWarning("M19_TRIGGER rejected reason=formal_stimulus_epoch_unavailable", this);
                return false;
            }

            var request = new BciM19DecodeRequestPayload
            {
                protocolVersion = BciSelectionTransportMessage.ProtocolVersion,
                messageType = "eeg_decode_request",
                trialId = trialId,
                selectionId = selectionId,
                pageId = trial.PageId,
                pageIndex = page.PageIndex,
                pageEpoch = trial.PageEpoch,
                createdUtc = triggerUtc.ToString("O"),
                presentationPolicy = strict ? "research_strict" : "demo_preview",
                formalStimulusOnsetUtc = strict ? DateTime.UtcNow.ToString("O") : null,
                activeSlotCount = activeSlots.Count,
                slots = activeSlots.ToArray()
            };
            m_pendingSelectionId = selectionId;
            m_pendingTrialId = trialId;
            m_pendingTrialStartedAtUnscaledTime = Time.unscaledTime;
            var snapshot = new BciSelectionSnapshot(frozenTargets);
            if (!m_transport.BeginM19Trial(request, snapshot))
            {
                m_queue.CancelTrial(selectionId);
                if (strict)
                    m_binding.EndM19FormalStimulusEpoch(selectionId, "request_publish_failed");
                m_pendingSelectionId = null;
                m_pendingTrialId = null;
                m_pendingTrialStartedAtUnscaledTime = 0f;
                Debug.LogWarning("M19_TRIGGER rejected reason=local_snapshot_or_transport_rejected", this);
                return false;
            }

            m_hud.Refresh();
            Debug.Log("M19_TIMING event=TRIGGER_ACTIVATED selection_id=" + selectionId +
                " trial_id=" + trialId +
                " page_id=" + trial.PageId +
                " page_index=" + page.PageIndex +
                " page_epoch=" + trial.PageEpoch +
                " active_slots=" + activeSlots.Count +
                " trigger_utc=" + request.createdUtc +
                " formal_onset_utc=" + (request.formalStimulusOnsetUtc ?? "not_applicable_demo_preview") +
                " timing_authority=software_only", this);
            return true;
        }

        private bool ValidatePageOpening(BciSelectionTransportMessage message, BciSelectionSnapshot snapshot)
        {
            if (m_queue == null || message == null || snapshot == null ||
                string.IsNullOrWhiteSpace(message.pageId) || message.pageEpoch < 0)
                return false;
            BciPagedPage page = m_queue.CurrentPage;
            if (!string.Equals(page.PageId, message.pageId, StringComparison.Ordinal) ||
                page.PageEpoch != message.pageEpoch || message.candidateSnapshot == null ||
                message.candidateSnapshot.Length != BciPagedSelectionQueue.PageSize)
                return false;

            for (int slot = 0; slot < BciPagedSelectionQueue.PageSize; slot++)
            {
                BciSelectionCandidatePayload received = message.candidateSnapshot[slot];
                BciPagedPageCandidate expected = slot < page.Candidates.Count ? page.Candidates[slot] : null;
                if (received == null || received.slotIndex != slot ||
                    Mathf.Abs(received.nominalFrequencyHz - BciPagedSelectionQueue.NominalFrequenciesHz[slot]) > 0.01f ||
                    (expected == null && (received.active || !string.IsNullOrWhiteSpace(received.targetId))) ||
                    (expected != null &&
                     (!string.Equals(received.targetId, expected.TargetId, StringComparison.Ordinal) ||
                      received.active != expected.Active)))
                    return false;
            }
            return true;
        }

        private void OnSelectionOpened(BciSelectionTransportMessage message, BciSelectionSnapshot snapshot)
        {
            if (m_queue == null || message == null)
                return;
            m_binding.SetExecutionPresentationHidden(false, "m16_selection_open");
            BciPagedSelectionTrial trial = m_queue.OpenTrial(message.selectionId);
            if (trial == null)
            {
                Debug.LogWarning("M16_STALE_SELECTION event=open_rejected selection_id=" + message.selectionId, this);
                return;
            }
            m_pendingSelectionId = message.selectionId;
            Debug.Log("M16_SELECTION trial_opened selection_id=" + message.selectionId +
                " page_id=" + trial.PageId + " page_epoch=" + trial.PageEpoch, this);
        }

        private void OnTargetSelected(BciTargetSelectionResult result)
        {
            if (m_queue == null)
                return;
            BciPagedSelectionTrial trial = m_queue.CurrentTrial;
            int epoch = trial == null ? -1 : trial.PageEpoch;
            if (!m_queue.TryAcceptResult(
                    result.SelectionId,
                    result.SlotIndex,
                    result.TargetId,
                    epoch,
                    out BciPagedQueueEntry entry,
                    out string rejection))
            {
                Debug.LogWarning("M16_STALE_SELECTION event=reject selection_id=" + result.SelectionId +
                    " reason=" + rejection, this);
                return;
            }

            m_resultsBySelectionId[result.SelectionId] = result;
            m_binding.SetGroupSlotSelected(m_activePageGroupId, entry.SlotIndex, true);
            if (string.Equals(m_pendingSelectionId, result.SelectionId, StringComparison.Ordinal))
            {
                if (!string.IsNullOrWhiteSpace(m_pendingTrialId) &&
                    m_m19PresentationPolicy == BciM19PresentationPolicy.ResearchStrict)
                    m_binding.EndM19FormalStimulusEpoch(result.SelectionId, "decision_applied");
                m_pendingSelectionId = null;
                m_pendingTrialId = null;
                m_pendingTrialStartedAtUnscaledTime = 0f;
                if (m_hud != null)
                    m_hud.Refresh();
            }
            Debug.Log("M16_QUEUE event=selected target_id=" + entry.TargetId +
                " queue_count=" + m_queue.Queue.Count, this);
        }

        private void OnSelectionTerminated(string selectionId)
        {
            if (!string.Equals(m_pendingSelectionId, selectionId, StringComparison.Ordinal))
                return;
            if (m_queue != null && m_queue.CurrentTrial != null)
                m_queue.CancelTrial(selectionId);
            if (!string.IsNullOrWhiteSpace(m_pendingTrialId) &&
                m_m19PresentationPolicy == BciM19PresentationPolicy.ResearchStrict)
                m_binding.EndM19FormalStimulusEpoch(selectionId, "trial_terminated");
            m_pendingSelectionId = null;
            m_pendingTrialId = null;
            m_pendingTrialStartedAtUnscaledTime = 0f;
            if (m_hud != null)
                m_hud.Refresh();
        }

        private void OnM19DecodeAcknowledged(BciSelectionTransportMessage message)
        {
            if (message == null)
                return;
            Debug.Log("M19_TRIGGER decode_ack selection_id=" + (message.selectionId ?? "") +
                " accepted=" + message.accepted +
                " rejection=" + (message.rejectionReason ?? "None"), this);
        }

        public bool UndoLastSelection()
        {
            if (m_queue == null)
                return false;
            if (!m_queue.UndoEnabled)
            {
                AbortPendingTrial("undo");
                return false;
            }
            AbortPendingTrial("undo");
            if (!m_queue.TryUndoLast(out BciPagedQueueEntry undone))
                return false;
            RefreshCurrentPagePresentation();
            if (m_resultsBySelectionId.TryGetValue(undone.SelectionId, out BciTargetSelectionResult result))
                m_transport.PublishSelectionUndo(result);
            m_resultsBySelectionId.Remove(undone.SelectionId);
            Debug.Log("M16_QUEUE event=undo target_id=" + undone.TargetId, this);
            return true;
        }

        public bool Submit()
        {
            if (m_queue == null)
                return false;
            if (!m_queue.SubmitEnabled)
            {
                AbortPendingTrial("submit");
                return false;
            }
            AbortPendingTrial("submit");
            if (!m_queue.TrySubmit(out BciPagedCommitPlan plan))
                return false;
            m_nextHostBatchIndex = 0;
            SetRobotExecutionStatus("EXECUTING");
            m_binding.SetExecutionPresentationHidden(true, "m16_submit");
            for (int batchIndex = 0; batchIndex < plan.Batches.Count; batchIndex++)
            {
                BciPagedCommitBatch chunk = plan.Batches[batchIndex];
                var selections = new List<BciTargetSelectionResult>(chunk.Entries.Count);
                for (int index = 0; index < chunk.Entries.Count; index++)
                {
                    if (!m_resultsBySelectionId.TryGetValue(chunk.Entries[index].SelectionId, out BciTargetSelectionResult result))
                    {
                        Debug.LogError("M16_QUEUE submit_rejected missing_selection_result selection_id=" +
                            chunk.Entries[index].SelectionId, this);
                        return false;
                    }
                    selections.Add(result);
                }
                var batch = new ConfirmedTargetBatch(
                    "m16-paged-batch-" + (batchIndex + 1),
                    "m16-paged-session",
                    batchIndex,
                    selections,
                    DateTime.UtcNow);
                if (!m_transport.PublishConfirmedTargetBatch(batch))
                    return false;
            }
            Debug.Log("M16_QUEUE event=submit ordered_count=" + plan.OrderedEntries.Count +
                " batch_count=" + plan.Batches.Count, this);
            return true;
        }

        private void AbortPendingTrial(string reason)
        {
            if (string.IsNullOrWhiteSpace(m_pendingSelectionId))
                return;
            string selectionId = m_pendingSelectionId;
            bool isM19 = !string.IsNullOrWhiteSpace(m_pendingTrialId);
            if (isM19)
                m_transport.AbortM19Trial(selectionId, reason);
            else
                m_transport.AbortPendingSelectionForPageChange(selectionId);

            if (m_queue != null && m_queue.CurrentTrial != null)
                m_queue.CancelTrial(selectionId);
            // A successful local transport abort raises SelectionTerminated
            // synchronously, which already clears m_pendingTrialId and ends
            // the strict epoch. Keep this fallback only for failed aborts.
            if (!string.IsNullOrWhiteSpace(m_pendingTrialId) &&
                m_m19PresentationPolicy == BciM19PresentationPolicy.ResearchStrict)
                m_binding.EndM19FormalStimulusEpoch(selectionId, reason);
            if (string.Equals(m_pendingSelectionId, selectionId, StringComparison.Ordinal))
            {
                m_pendingSelectionId = null;
                m_pendingTrialId = null;
                m_pendingTrialStartedAtUnscaledTime = 0f;
            }
            if (m_hud != null)
                m_hud.Refresh();
        }

        /// <summary>
        /// Called by the downstream integration after ordered execution really
        /// completes. Transport ACK alone is not treated as Processed.
        /// </summary>
        public bool CompleteExecution(IReadOnlyList<string> processedTargetIds)
        {
            if (m_queue == null || !m_queue.CompleteExecution(processedTargetIds))
                return false;
            SetRobotExecutionStatus("COMPLETE");
            m_binding.EndActiveGroup(m_activePageGroupId);
            m_binding.SetProcessedTargetIds(m_queue.ProcessedTargetIds, m_queue.ProcessedTargetIds);
            m_resultsBySelectionId.Clear();
            ActivateCurrentPage("execution_complete");
            return true;
        }

        private bool OnHostBatchCloseRequested(ConfirmedTargetBatchPayload payload)
        {
            if (m_queue == null || payload == null)
                return false;
            if (m_queue.State == BciPagedQueueState.Selecting)
            {
                if (!m_queue.TrySubmit(out BciPagedCommitPlan _))
                    return false;
                m_nextHostBatchIndex = 0;
                SetRobotExecutionStatus("EXECUTING");
                m_binding.SetExecutionPresentationHidden(true, "m16_host_submit");
            }
            if (m_queue.State != BciPagedQueueState.Executing)
                return false;
            IReadOnlyList<BciPagedCommitBatch> expectedBatches = m_queue.SubmittedPlan.Batches;
            if (m_nextHostBatchIndex < 0 || m_nextHostBatchIndex >= expectedBatches.Count ||
                payload.selections == null ||
                payload.selections.Length != expectedBatches[m_nextHostBatchIndex].Entries.Count)
                return false;

            BciPagedCommitBatch expected = expectedBatches[m_nextHostBatchIndex];
            for (int index = 0; index < expected.Entries.Count; index++)
            {
                BciPagedQueueEntry expectedEntry = expected.Entries[index];
                ConfirmedTargetSelectionPayload received = payload.selections[index];
                if (received == null ||
                    !string.Equals(received.selectionId, expectedEntry.SelectionId, StringComparison.Ordinal) ||
                    !string.Equals(received.targetId, expectedEntry.TargetId, StringComparison.Ordinal) ||
                    received.slotIndex != expectedEntry.SlotIndex ||
                    received.predictedClassIndex != expectedEntry.SlotIndex)
                    return false;
            }

            m_nextHostBatchIndex++;
            Debug.Log("M16_QUEUE host_batch_close_received batch_id=" + (payload.batchId ?? "") +
                " batch_index=" + (m_nextHostBatchIndex - 1), this);
            return true;
        }

        private void ActivateCurrentPage(string reason)
        {
            if (m_queue == null)
                return;
            if (!string.IsNullOrWhiteSpace(m_activePageGroupId))
                m_binding.EndActiveGroup(m_activePageGroupId);
            BciPagedPage page = m_queue.CurrentPage;
            var anchors = new List<StableWorldAnchorSnapshot>(page.Candidates.Count);
            for (int index = 0; index < page.Candidates.Count; index++)
            {
                StableWorldAnchorSnapshot anchor;
                if (!TryFindAnchor(page.Candidates[index].TargetId, out anchor))
                    continue;
                anchors.Add(anchor);
            }
            if (anchors.Count == 0)
                return;
            m_activePageGroupId = page.PageId + "-epoch-" + page.PageEpoch;
            m_binding.ActivateGroup(m_activePageGroupId, anchors, openPresentation: false);
            RefreshCurrentPagePresentation();
            Debug.Log("M16_PAGE state=GroupReady reason=" + reason +
                " page=" + (page.PageIndex + 1) + "/" + page.PageCount +
                " candidate_count=" + page.Candidates.Count, this);
        }

        private void RefreshCurrentPagePresentation()
        {
            if (m_queue == null || m_binding == null || string.IsNullOrWhiteSpace(m_activePageGroupId))
                return;

            BciPagedPage page = m_queue.CurrentPage;
            for (int index = 0; index < page.Candidates.Count; index++)
                m_binding.SetGroupSlotSelected(
                    m_activePageGroupId,
                    page.Candidates[index].SlotIndex,
                    page.Candidates[index].Selected);
            m_binding.ShowPagedBrowsePresentation();
        }

        private void EmitRuntimeDependencyDiagnostic()
        {
            if (m_runtimeDependencyDiagnosticEmitted)
                return;
            m_runtimeDependencyDiagnosticEmitted = true;

            string reason;
            if (!ValidateRuntimeDependencies(out reason))
            {
                Debug.LogError("M16_UI_READY dependency_check=FAIL reason=" + reason, this);
                return;
            }

            Debug.Log("M16_UI_READY dependency_check=PASS mode=PagedQueueV1", this);
        }

        private bool ValidateRuntimeDependencies(out string reason)
        {
            if (!m_initialized)
            {
                reason = "controller_not_initialized";
                return false;
            }
            if (Camera.main == null)
            {
                reason = "main_camera_missing";
                return false;
            }
            if (m_binding == null || m_transport == null)
            {
                reason = "binding_or_transport_missing";
                return false;
            }
            if (m_candidatesByTargetId.Count == 0)
            {
                reason = "stable_anchor_candidates_missing";
                return false;
            }
            if (m_queue == null || m_queue.CurrentPage == null)
            {
                reason = "page_not_ready";
                return false;
            }
            if (m_hud == null || !m_hud.IsInitialized)
            {
                reason = "hud_not_initialized";
                return false;
            }
            if (m_hud.GazeInteractor == null || !m_hud.GazeInteractor.IsInitialized)
            {
                reason = "gaze_interactor_not_initialized";
                return false;
            }
            if (!m_hud.GazeInteractor.ReticleReady)
            {
                reason = "reticle_not_ready";
                return false;
            }
            if (m_hud.GazeControlCount != 5 || !m_hud.GazeControlsReady)
            {
                reason = "gaze_controls_not_ready";
                return false;
            }
            reason = null;
            return true;
        }

        private bool TryFindAnchor(string targetId, out StableWorldAnchorSnapshot anchor)
        {
            if (m_candidatesByTargetId.TryGetValue(targetId, out anchor))
                return true;
            anchor = default(StableWorldAnchorSnapshot);
            return false;
        }

        private void OnDestroy()
        {
            AbortPendingTrial("session_reset");
            Unsubscribe();
        }

        private void Unsubscribe()
        {
            if (m_binding == null || m_transport == null)
                return;
            m_binding.HudCandidatesChanged -= OnHudCandidatesChanged;
            m_transport.TargetSelected -= OnTargetSelected;
            m_transport.SelectionOpenedWithMetadata -= OnSelectionOpened;
            m_transport.AuthoritativeSelectionOpeningWithMetadata -= ValidatePageOpening;
            m_transport.SelectionTerminated -= OnSelectionTerminated;
            m_transport.M19DecodeAcknowledged -= OnM19DecodeAcknowledged;
            m_transport.HostBatchCloseRequested -= OnHostBatchCloseRequested;
        }
    }
}
