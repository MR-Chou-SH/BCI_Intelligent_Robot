using System;
using System.Collections.Generic;
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
        private string m_pendingSelectionId;
        private string m_lastReassociationLogSignature;
        private bool m_initialized;
        private StableWorldAnchorSnapshot[] m_latestCandidates = new StableWorldAnchorSnapshot[0];
        private string m_lastCandidateDiagnosticSignature;

        public bool OwnsBatchInput => m_initialized && m_binding != null && m_binding.IsBatchGroupModeEnabled;

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
            m_transport.SelectionTerminated += OnSelectionTerminated;
            m_groups.GroupActivated += OnGroupActivated;
            m_groups.GroupSlotSelectionChanged += OnGroupSlotSelectionChanged;
            m_groups.BatchConfirmed += OnBatchConfirmed;
            m_initialized = true;
            if (m_detectionVisuals != null)
                m_detectionVisuals.SetBciSelectionPresentationActive(true);
            else
                Debug.LogWarning("M8_GROUP raw_detection_visual_not_managed reason=missing_SentisInferenceUiManager", this);
            if (m_detectionManager != null)
                m_detectionManager.SetBciTargetPresentationActive(true);
            Debug.Log("M8_GROUP controller_initialized input_owner=batch submit=right_A undo=right_B", this);
        }

        private void Update()
        {
            if (!m_initialized)
                return;

            if (InputManager.IsButtonBDownOrMiddleFingerPinchStarted())
                UndoLastSelection();
            if (InputManager.IsButtonADownOrPinchStarted())
                SubmitCurrentGroup();
        }

        private void LateUpdate()
        {
            if (m_initialized)
                m_groups.TryActivateNextGroup();
        }

        private void OnHudCandidatesChanged(System.Collections.Generic.IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            m_latestCandidates = CopyCandidates(candidates);
            m_groups.UpdateCandidatePool(candidates);
            TryReassociateActiveGroup();
            LogCandidateGroupState("candidate_pool_changed");
        }

        private void OnGroupActivated(BciActiveTargetGroup group)
        {
            m_lastReassociationLogSignature = null;
            m_binding.ActivateGroup(group.GroupId, group.Targets);
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
            LogCandidateGroupState("group_activated");
        }

        private void OnTargetSelected(BciTargetSelectionResult result)
        {
            if (m_groups.TryAccept(result))
            {
                Debug.Log("M8_GROUP selection_added group_id=" + m_groups.ActiveGroup.Value.GroupId +
                    " selection_id=" + result.SelectionId + " slot=" + result.SlotIndex +
                    " target_id=" + result.TargetId +
                    " selected_count=" + m_groups.CurrentSelections.Count, this);
                LogCandidateGroupState("selection_added");
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
            LogCandidateGroupState(selected ? "slot_selected" : "slot_restored");
        }

        private void OnSelectionOpened(string selectionId)
        {
            if (m_groups.HasActiveGroup)
                m_pendingSelectionId = selectionId;
            LogCandidateGroupState("selection_opened");
        }

        private void OnSelectionTerminated(string selectionId)
        {
            if (string.Equals(m_pendingSelectionId, selectionId, StringComparison.Ordinal))
                m_pendingSelectionId = null;
            LogCandidateGroupState("selection_terminated");
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
            LogCandidateGroupState("selection_undone");
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
            m_lastReassociationLogSignature = null;
            m_binding.EndActiveGroup(batch.GroupId);
            m_binding.SetProcessedTargetIds(m_groups.ProcessedTargetIds, m_groups.SubmittedTargetIds);
            if (!m_transport.PublishConfirmedTargetBatch(batch))
                Debug.LogWarning("M8_GROUP batch_publish_rejected batch_id=" + batch.BatchId, this);
            LogCandidateGroupState("group_submitted");
        }

        private static StableWorldAnchorSnapshot[] CopyCandidates(
            System.Collections.Generic.IReadOnlyList<StableWorldAnchorSnapshot> candidates)
        {
            if (candidates == null || candidates.Count == 0)
                return new StableWorldAnchorSnapshot[0];
            var copy = new StableWorldAnchorSnapshot[candidates.Count];
            for (int index = 0; index < candidates.Count; index++)
                copy[index] = candidates[index];
            return copy;
        }

        private void LogCandidateGroupState(string reason)
        {
            if (m_groups == null)
                return;

            int activeCandidateCount = 0;
            var candidates = new List<string>(m_latestCandidates.Length);
            for (int index = 0; index < m_latestCandidates.Length; index++)
            {
                StableWorldAnchorSnapshot candidate = m_latestCandidates[index];
                if (candidate.State == StableTargetState.Active)
                    activeCandidateCount++;
                candidates.Add(candidate.TargetId + ":" + candidate.ClassName + ":" + candidate.State);
            }

            BciActiveTargetGroup? group = m_groups.ActiveGroup;
            string groupId = group.HasValue ? group.Value.GroupId : "none";
            int groupIndex = group.HasValue ? group.Value.GroupIndex : 0;
            int frozenTargetCount = group.HasValue ? group.Value.Targets.Count : 0;
            var slots = new string[BciTargetSlotAllocator.SlotCount];
            for (int slot = 0; slot < slots.Length; slot++)
            {
                slots[slot] = group.HasValue && slot < group.Value.Targets.Count
                    ? group.Value.Targets[slot].TargetId
                    : "none";
            }

            string selected = DescribeTargetIds(m_groups.CurrentSelections);
            string processed = DescribeTargetIds(m_groups.ProcessedTargetIds);
            string submitted = DescribeTargetIds(m_groups.SubmittedTargetIds);
            int rawCount = m_detectionVisuals != null ? m_detectionVisuals.LastRawDetectionCount : -1;
            string signature = rawCount + "|" + groupId + "|" + groupIndex + "|" + frozenTargetCount + "|" +
                string.Join(",", candidates) + "|" + selected + "|" + processed + "|" + submitted + "|" +
                string.Join(",", slots);
            if (string.Equals(signature, m_lastCandidateDiagnosticSignature, StringComparison.Ordinal))
                return;

            m_lastCandidateDiagnosticSignature = signature;
            Debug.Log("M8_CANDIDATE group_state reason=" + reason +
                " raw_yolo_detection_count=" + rawCount +
                " hud_candidate_count=" + m_latestCandidates.Length +
                " bci_eligible_active_count=" + activeCandidateCount +
                " group_id=" + groupId +
                " group_index=" + groupIndex +
                " frozen_target_count=" + frozenTargetCount +
                " slot0_target_id=" + slots[0] +
                " slot1_target_id=" + slots[1] +
                " slot2_target_id=" + slots[2] +
                " selected_target_ids=" + selected +
                " processed_target_ids=" + processed +
                " submitted_target_ids=" + submitted +
                " candidates=" + string.Join(",", candidates), this);
        }

        private static string DescribeTargetIds(IEnumerable<BciTargetSelectionResult> results)
        {
            var targetIds = new List<string>();
            foreach (BciTargetSelectionResult result in results)
            {
                if (!string.IsNullOrWhiteSpace(result.TargetId))
                    targetIds.Add(result.TargetId);
            }
            return DescribeTargetIds(targetIds);
        }

        private static string DescribeTargetIds(IEnumerable<string> targetIds)
        {
            var values = new List<string>();
            foreach (string targetId in targetIds)
            {
                if (!string.IsNullOrWhiteSpace(targetId))
                    values.Add(targetId);
            }
            values.Sort(StringComparer.Ordinal);
            return values.Count == 0 ? "none" : string.Join(",", values);
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

            m_binding.HudCandidatesChanged -= OnHudCandidatesChanged;
            m_transport.TargetSelected -= OnTargetSelected;
            m_transport.SelectionOpened -= OnSelectionOpened;
            m_transport.SelectionTerminated -= OnSelectionTerminated;
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
