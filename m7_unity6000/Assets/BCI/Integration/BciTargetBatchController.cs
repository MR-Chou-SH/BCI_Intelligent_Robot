using System;
using BCIIntelligentRobot.Vision;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Runtime M8.4 UX bridge. It consumes immutable M8.3 results, owns no
    /// EEG decoding, and uses the sample's existing A/B controller input.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class BciTargetBatchController : MonoBehaviour
    {
        private BciSsvepTargetBinding m_binding;
        private BciSelectionTransportClient m_transport;
        private SentisInferenceUiManager m_detectionVisuals;
        private DetectionManager m_detectionManager;
        private BciTargetGroupCoordinator m_groups;
        private BciPagedTargetQueueController m_pagedQueueController;
        private string m_pendingSelectionId;
        private string m_lastReassociationLogSignature;
        private bool m_initialized;
        private bool m_presentationOnly;
        private int m_lastCandidateCount = -1;

        public bool OwnsBatchInput => m_initialized && !m_presentationOnly &&
            m_binding != null && m_binding.IsBatchGroupModeEnabled;

        public bool SetM13_6ExecutionPresentation(
            bool execution,
            string reason,
            string robotStatus = null)
        {
            if (!m_initialized || m_binding == null)
                return false;
            if (m_presentationOnly && !string.IsNullOrWhiteSpace(robotStatus))
            {
                if (m_pagedQueueController == null)
                    m_pagedQueueController = GetComponent<BciPagedTargetQueueController>();
                if (m_pagedQueueController != null)
                    m_pagedQueueController.SetRobotExecutionStatus(robotStatus);
            }
            return m_binding.SetExecutionPresentationHidden(execution, reason);
        }

        public void Initialize(
            BciSsvepTargetBinding binding,
            BciSelectionTransportClient transport,
            SentisInferenceUiManager detectionVisuals = null)
        {
            if (m_initialized)
                return;
            if (binding == null || transport == null)
            {
                Debug.LogWarning("M8_GROUP initialization rejected: missing binding or selection transport.", this);
                return;
            }
            m_binding = binding;
            m_transport = transport;
            m_detectionVisuals = detectionVisuals;
            m_detectionManager = GetComponent<DetectionManager>();
            m_groups = new BciTargetGroupCoordinator();
            m_binding.HudCandidatesChanged += OnHudCandidatesChanged;
            if (!m_binding.EnableBatchGroupMode())
            {
                m_binding.HudCandidatesChanged -= OnHudCandidatesChanged;
                m_groups = null;
                m_binding = null;
                m_transport = null;
                m_detectionVisuals = null;
                Debug.LogWarning("M8_GROUP initialization rejected: requires the frozen ViewLockedHud presentation mode.", this);
                return;
            }

            m_transport.TargetSelected += OnTargetSelected;
            m_transport.SelectionOpened += OnSelectionOpened;
            m_transport.AuthoritativeSelectionOpening += OnAuthoritativeSelectionOpening;
            m_transport.SelectionTerminated += OnSelectionTerminated;
            m_transport.HostBatchCloseRequested += OnHostBatchCloseRequested;
            m_groups.GroupActivated += OnGroupActivated;
            m_groups.GroupSlotSelectionChanged += OnGroupSlotSelectionChanged;
            m_groups.BatchConfirmed += OnBatchConfirmed;
            m_initialized = true;
            if (m_detectionVisuals != null)
                m_detectionVisuals.SetBciSelectionPresentationActive(true);
            else if (!m_binding.IsVirtualTargetSourceActive)
                Debug.LogWarning("M8_GROUP raw_detection_visual_not_managed reason=missing_SentisInferenceUiManager", this);
            if (m_detectionManager != null)
                m_detectionManager.SetBciTargetPresentationActive(true);
            Debug.Log("M8_GROUP controller_initialized input_owner=batch submit=right_A undo=right_B", this);
        }

        /// <summary>
        /// M16 keeps this component as the existing M13.6 presentation bridge,
        /// but leaves legacy grouped input/event ownership disabled.
        /// </summary>
        public void InitializePresentationBridge(BciSsvepTargetBinding binding)
        {
            if (m_initialized || binding == null)
                return;
            m_binding = binding;
            m_presentationOnly = true;
            m_pagedQueueController = GetComponent<BciPagedTargetQueueController>();
            m_initialized = true;
            Debug.Log("M8_GROUP presentation_bridge_initialized owner=m16_paged_queue", this);
        }

        private void Update()
        {
            if (!m_initialized || m_presentationOnly)
                return;

            if (InputManager.IsButtonBDownOrMiddleFingerPinchStarted())
                UndoLastSelection();
            if (InputManager.IsButtonADownOrPinchStarted())
                SubmitCurrentGroup();
        }

        private void LateUpdate()
        {
            if (m_initialized && !m_presentationOnly)
                m_groups.TryActivateNextGroup();
        }

        private void OnHudCandidatesChanged(System.Collections.Generic.IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            m_groups.UpdateCandidatePool(candidates);
            TryReassociateActiveGroup();
            if (candidates.Count == m_lastCandidateCount)
                return;

            m_lastCandidateCount = candidates.Count;
            int rawCount = m_detectionVisuals != null ? m_detectionVisuals.LastRawDetectionCount : -1;
            Debug.Log(
                "M8_GROUP bci_stable_candidate_count=" + candidates.Count +
                " raw_detection_count=" + rawCount +
                (candidates.Count == 0 ? " reason=no_active_stable_world_anchor" : string.Empty),
                this);
        }

        private void OnGroupActivated(BciActiveTargetGroup group)
        {
            m_lastReassociationLogSignature = null;
            // Keep the group available for authoritative TargetId validation,
            // but do not open its visual/SSVEP presentation until selection_open.
            m_binding.ActivateGroup(group.GroupId, group.Targets, openPresentation: false);
            string mapping = string.Empty;
            for (int slot = 0; slot < group.Targets.Count; slot++)
            {
                if (slot > 0)
                    mapping += " ";
                mapping += "slot" + slot + "_target_id=" + group.Targets[slot].TargetId;
            }
            Debug.Log(
                "M8_GROUP group_activated group_id=" + group.GroupId +
                " group_index=" + group.GroupIndex + " " + mapping,
                this);
        }

        private void OnTargetSelected(BciTargetSelectionResult result)
        {
            if (m_groups.TryAccept(result))
            {
                Debug.Log("M8_GROUP selection_added group_id=" + m_groups.ActiveGroup.Value.GroupId +
                    " selection_id=" + result.SelectionId + " slot=" + result.SlotIndex +
                    " target_id=" + result.TargetId +
                    " selected_count=" + m_groups.CurrentSelections.Count, this);
            }
            else
            {
                Debug.LogWarning("M8_GROUP selection_ignored selection_id=" + result.SelectionId +
                    " target_id=" + result.TargetId + " reason=not_current_or_already_selected", this);
            }
        }

        private void OnGroupSlotSelectionChanged(int slotIndex, bool selected)
        {
            BciActiveTargetGroup? group = m_groups.ActiveGroup;
            if (group.HasValue)
                m_binding.SetGroupSlotSelected(group.Value.GroupId, slotIndex, selected);
        }

        private void OnSelectionOpened(string selectionId)
        {
            m_binding.SetExecutionPresentationHidden(false, "m8_selection_open");
            if (m_groups.HasActiveGroup)
                m_pendingSelectionId = selectionId;
        }

        private bool OnAuthoritativeSelectionOpening(string selectionId, BciSelectionSnapshot snapshot)
        {
            bool accepted = m_groups != null && m_groups.TryApplyAuthoritativeSelectionSnapshot(snapshot);
            if (!accepted)
                Debug.LogWarning("M8_GROUP authoritative_snapshot_rejected selection_id=" + selectionId, this);
            return accepted;
        }

        private void OnSelectionTerminated(string selectionId)
        {
            if (string.Equals(m_pendingSelectionId, selectionId, StringComparison.Ordinal))
                m_pendingSelectionId = null;
        }

        public bool UndoLastSelection()
        {
            if (!m_groups.TryUndoLastSelection(out BciTargetSelectionResult undone))
            {
                Debug.Log("M8_GROUP undo_noop reason=empty_batch", this);
                return false;
            }
            Debug.Log("M8_GROUP selection_undone selection_id=" + undone.SelectionId +
                " slot=" + undone.SlotIndex + " target_id=" + undone.TargetId +
                " selected_count=" + m_groups.CurrentSelections.Count, this);
            if (!m_transport.PublishSelectionUndo(undone))
                Debug.LogWarning("M8_GROUP undo_publish_rejected selection_id=" + undone.SelectionId, this);
            return true;
        }

        public bool SubmitCurrentGroup()
        {
            if (!string.IsNullOrWhiteSpace(m_pendingSelectionId))
            {
                string pendingSelectionId = m_pendingSelectionId;
                if (m_transport.AbortPendingSelectionForGroupSubmit(pendingSelectionId))
                    Debug.Log("M8_GROUP submit_aborted_pending selection_id=" + pendingSelectionId, this);
                else
                    Debug.LogWarning("M8_GROUP submit_pending_abort_rejected selection_id=" + pendingSelectionId, this);
                m_pendingSelectionId = null;
            }

            if (!m_groups.TryConfirmCurrentGroup(out ConfirmedTargetBatch batch))
            {
                Debug.Log("M8_GROUP submit_noop reason=empty_batch", this);
                return false;
            }

            Debug.Log("M8_GROUP submitted batch_id=" + batch.BatchId +
                " group_id=" + batch.GroupId + " selections=" + batch.Selections.Count, this);
            return true;
        }

        private void OnBatchConfirmed(ConfirmedTargetBatch batch)
        {
            CloseConfirmedGroup(batch.GroupId, batch.BatchId, publishToPc: true);
            if (!m_transport.PublishConfirmedTargetBatch(batch))
                Debug.LogWarning("M8_GROUP batch_publish_rejected batch_id=" + batch.BatchId, this);
        }

        private bool OnHostBatchCloseRequested(ConfirmedTargetBatchPayload payload)
        {
            if (payload == null || m_groups == null)
                return false;

            if (!m_groups.TryCloseCurrentGroupFromHost(payload.selections, out string groupId))
            {
                Debug.LogWarning("M8_GROUP host_batch_close_rejected batch_id=" +
                    (payload.batchId ?? "") + " reason=selection_identity_or_order_mismatch", this);
                return false;
            }

            bool closed = CloseConfirmedGroup(groupId, payload.batchId, publishToPc: false);
            if (closed)
                Debug.Log("M8_GROUP host_batch_close_accepted batch_id=" +
                    (payload.batchId ?? "") + " group_id=" + groupId, this);
            return closed;
        }

        private bool CloseConfirmedGroup(string groupId, string batchId, bool publishToPc)
        {
            m_lastReassociationLogSignature = null;
            if (!m_binding.EndActiveGroup(groupId))
            {
                Debug.LogWarning("M8_GROUP batch_close_rejected batch_id=" +
                    (batchId ?? "") + " group_id=" + (groupId ?? "") +
                    " reason=active_group_not_ended", this);
                return false;
            }
            m_binding.SetProcessedTargetIds(m_groups.ProcessedTargetIds, m_groups.SubmittedTargetIds);
            if (!publishToPc && !m_groups.HasActiveGroup)
            {
                // The Showcase sends the next selection_open immediately after
                // receiving batch_ack.  Do not leave the next group waiting for
                // LateUpdate: the host-close ACK is the batch-boundary event.
                bool activated = m_groups.TryActivateNextGroup();
                if (activated)
                {
                    Debug.Log("M8_GROUP next_group_activated_before_host_batch_ack", this);
                }
                else
                {
                    Debug.Log("M8_GROUP no_next_group_before_host_batch_ack", this);
                }
            }
            return true;
        }

        private void TryReassociateActiveGroup()
        {
            if (!m_groups.HasActiveGroup)
                return;

            bool selectionFrozen = m_binding.IsSelectionLayoutFrozen ||
                !string.IsNullOrWhiteSpace(m_pendingSelectionId);
            System.Collections.Generic.IReadOnlyList<BciGroupTargetReassociationDecision> decisions =
                m_groups.EvaluateActiveGroupReassociation(selectionFrozen);
            for (int index = 0; index < decisions.Count; index++)
            {
                BciGroupTargetReassociationDecision decision = decisions[index];
                if (decision.Outcome == BciGroupTargetReassociationOutcome.Accepted)
                {
                    LogReassociationCandidate(decision);
                    BciActiveTargetGroup? group = m_groups.ActiveGroup;
                    if (!group.HasValue ||
                        !m_binding.TryApplyGroupTargetHandover(group.Value.GroupId, decision) ||
                        !m_groups.TryCommitReassociation(decision))
                    {
                        Debug.LogWarning("M8_GROUP reassociation_rejected_ambiguous group_id=" +
                            (group.HasValue ? group.Value.GroupId : "none") +
                            " slot=" + decision.SlotIndex +
                            " old_target_id=" + decision.OldTargetId +
                            " new_target_id=" + decision.NewTarget.TargetId +
                            " reason=atomic_commit_precondition_failed", this);
                    }
                    continue;
                }

                if (decision.Outcome == BciGroupTargetReassociationOutcome.RejectedAmbiguous)
                    LogReassociationAmbiguous(decision);
            }
        }

        private void LogReassociationCandidate(BciGroupTargetReassociationDecision decision)
        {
            string signature = "candidate|" + decision.SlotIndex + "|" + decision.OldTargetId + "|" +
                decision.NewTarget.TargetId + "|" + decision.Outcome;
            if (string.Equals(signature, m_lastReassociationLogSignature, StringComparison.Ordinal))
                return;

            m_lastReassociationLogSignature = signature;
            Debug.Log("M8_GROUP reassociation_candidate slot=" + decision.SlotIndex +
                " old_target_id=" + decision.OldTargetId +
                " new_target_id=" + decision.NewTarget.TargetId +
                " label=" + decision.NewTarget.ClassName +
                " world_distance_m=" + decision.WorldDistanceMeters.ToString("F3") +
                " bbox_iou=" + decision.BoundingBoxIoU.ToString("F3") +
                " time_gap_s=" + decision.TimeGapSeconds.ToString("F3") +
                " competing_candidate_count=" + decision.CompetingCandidateCount +
                " competing_member_count=" + decision.CompetingMemberCount, this);
        }

        private void LogReassociationAmbiguous(BciGroupTargetReassociationDecision decision)
        {
            string signature = "ambiguous|" + decision.SlotIndex + "|" + decision.OldTargetId + "|" +
                decision.NewTarget.TargetId + "|" + decision.CompetingCandidateCount + "|" +
                decision.CompetingMemberCount + "|" + decision.Reason;
            if (string.Equals(signature, m_lastReassociationLogSignature, StringComparison.Ordinal))
                return;

            m_lastReassociationLogSignature = signature;
            Debug.LogWarning("M8_GROUP reassociation_rejected_ambiguous slot=" + decision.SlotIndex +
                " old_target_id=" + decision.OldTargetId +
                " new_target_id=" + decision.NewTarget.TargetId +
                " label=" + decision.OldAnchor.ClassName +
                " world_distance_m=" + decision.WorldDistanceMeters.ToString("F3") +
                " bbox_iou=" + decision.BoundingBoxIoU.ToString("F3") +
                " time_gap_s=" + decision.TimeGapSeconds.ToString("F3") +
                " competing_candidate_count=" + decision.CompetingCandidateCount +
                " competing_member_count=" + decision.CompetingMemberCount +
                " reason=" + decision.Reason, this);
        }

        private void OnDestroy()
        {
            if (!m_initialized)
                return;

            if (m_presentationOnly)
            {
                m_binding = null;
                return;
            }

            m_binding.HudCandidatesChanged -= OnHudCandidatesChanged;
            m_transport.TargetSelected -= OnTargetSelected;
            m_transport.SelectionOpened -= OnSelectionOpened;
            m_transport.AuthoritativeSelectionOpening -= OnAuthoritativeSelectionOpening;
            m_transport.SelectionTerminated -= OnSelectionTerminated;
            m_transport.HostBatchCloseRequested -= OnHostBatchCloseRequested;
            m_groups.GroupActivated -= OnGroupActivated;
            m_groups.GroupSlotSelectionChanged -= OnGroupSlotSelectionChanged;
            m_groups.BatchConfirmed -= OnBatchConfirmed;
            if (m_detectionVisuals != null)
                m_detectionVisuals.SetBciSelectionPresentationActive(false);
            if (m_detectionManager != null)
                m_detectionManager.SetBciTargetPresentationActive(false);
            m_binding.DisableBatchGroupMode();
        }
    }
}
