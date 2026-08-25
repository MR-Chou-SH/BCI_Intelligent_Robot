using System;
using BCIIntelligentRobot.Vision;
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
        private BciTargetGroupCoordinator m_groups;
        private string m_pendingSelectionId;
        private bool m_initialized;

        public void Initialize(BciSsvepTargetBinding binding, BciSelectionTransportClient transport)
        {
            if (m_initialized)
                return;
            if (binding == null || transport == null)
            {
                Debug.LogWarning("M8_GROUP initialization rejected: missing binding or selection transport.", this);
                return;
            }
            if (!binding.EnableBatchGroupMode())
            {
                Debug.LogWarning("M8_GROUP requires the frozen ViewLockedHud presentation mode.", this);
                return;
            }

            m_binding = binding;
            m_transport = transport;
            m_groups = new BciTargetGroupCoordinator();
            m_binding.HudCandidatesChanged += OnHudCandidatesChanged;
            m_transport.TargetSelected += OnTargetSelected;
            m_transport.SelectionOpened += OnSelectionOpened;
            m_transport.SelectionTerminated += OnSelectionTerminated;
            m_groups.GroupActivated += OnGroupActivated;
            m_groups.GroupSlotSelectionChanged += OnGroupSlotSelectionChanged;
            m_groups.BatchConfirmed += OnBatchConfirmed;
            m_initialized = true;
            Debug.Log("M8_GROUP controller initialized submit=right_A undo=right_B", this);
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
            m_groups.UpdateCandidatePool(candidates);
        }

        private void OnGroupActivated(BciActiveTargetGroup group)
        {
            m_binding.ActivateGroup(group.GroupId, group.Targets);
        }

        private void OnTargetSelected(BciTargetSelectionResult result)
        {
            if (m_groups.TryAccept(result))
            {
                Debug.Log("M8_GROUP selection_added group_id=" + m_groups.ActiveGroup.Value.GroupId +
                    " selection_id=" + result.SelectionId + " slot=" + result.SlotIndex +
                    " target_id=" + result.TargetId, this);
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
            if (m_groups.HasActiveGroup)
                m_pendingSelectionId = selectionId;
        }

        private void OnSelectionTerminated(string selectionId)
        {
            if (string.Equals(m_pendingSelectionId, selectionId, StringComparison.Ordinal))
                m_pendingSelectionId = null;
        }

        private void UndoLastSelection()
        {
            if (!m_groups.TryUndoLastSelection(out BciTargetSelectionResult undone))
            {
                Debug.Log("M8_GROUP undo_noop reason=empty_batch", this);
                return;
            }
            Debug.Log("M8_GROUP selection_undone selection_id=" + undone.SelectionId +
                " slot=" + undone.SlotIndex + " target_id=" + undone.TargetId, this);
        }

        private void SubmitCurrentGroup()
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
                return;
            }

            Debug.Log("M8_GROUP submitted batch_id=" + batch.BatchId +
                " group_id=" + batch.GroupId + " selections=" + batch.Selections.Count, this);
        }

        private void OnBatchConfirmed(ConfirmedTargetBatch batch)
        {
            m_binding.EndActiveGroup(batch.GroupId);
            m_binding.SetProcessedTargetIds(m_groups.ProcessedTargetIds, m_groups.SubmittedTargetIds);
            if (!m_transport.PublishConfirmedTargetBatch(batch))
                Debug.LogWarning("M8_GROUP batch_publish_rejected batch_id=" + batch.BatchId, this);
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
            m_binding.DisableBatchGroupMode();
        }
    }
}
