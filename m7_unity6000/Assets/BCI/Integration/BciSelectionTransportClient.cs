using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    [Serializable]
    public sealed class BciSelectionCandidatePayload
    {
        public int slotIndex = -1;
        public string targetId;
        public string logicalBlockId;
        public float nominalFrequencyHz;
        public bool active = true;
    }

    [Serializable]
    public sealed class BciEegDecodeSlotPayload
    {
        public int slotIndex = -1;
        public float frequencyHz;
        public string targetId;
        public string semanticLabel;
        public string provenance;
    }

    [Serializable]
    public sealed class BciM19DecodeRequestPayload
    {
        public string contractId = "m19_decode_request_v1";
        public int protocolVersion = BciSelectionTransportMessage.ProtocolVersion;
        public string messageType = "eeg_decode_request";
        public string trialId;
        public string selectionId;
        public string pageId;
        public int pageIndex = -1;
        public int pageEpoch = -1;
        public string createdUtc;
        public int activeSlotCount;
        public BciEegDecodeSlotPayload[] slots;
        public string questUtc;
        public string presentationPolicy;
        public string formalStimulusOnsetUtc;
    }

    [Serializable]
    public sealed class BciSelectionTransportMessage
    {
        public const int ProtocolVersion = 1;
        public int protocolVersion = ProtocolVersion;
        public string messageType;
        public string sessionId;
        public string attemptId;
        public string blockId;
        public string nextBlockId;
        public string targetLabel;
        public string status;
        public string eventUtc;
        public string reason;
        public int ordinal = -1;
        public float frequencyHz;
        public int cueBeepCount;
        public int softwareFrame = -1;
        public long eventMonotonicNs;
        public string selectionId;
        public int predictedClassIndex;
        public string predictedLabel;
        public long pcMonotonicNs;
        public string pcUtc;
        public bool accepted;
        public string rejectionReason;
        public int resolvedSlot = -1;
        public string resolvedTargetId;
        public string resolvedClassName;
        public string questUtc;
        public string batchId;
        public string candidateSnapshotId;
        public int candidateSnapshotVersion;
        public BciSelectionCandidatePayload[] candidateSnapshot;
        // Optional M16 metadata. Legacy grouped messages leave these fields
        // unset; they never participate in legacy snapshot validation.
        public string pageId;
        public int pageIndex = -1;
        public int pageEpoch = -1;
        public string trialId;
        public string createdUtc;
        public int activeSlotCount;
        public BciEegDecodeSlotPayload[] slots;
        public bool hasDecisionMade;
        public bool decisionMade;
        public int classIndex = -1;
        public int slotIndex = -1;
        public string abortReason;
        public string timingJson;
        public ConfirmedTargetBatchPayload confirmedBatch;
    }

    /// <summary>Quest TCP client for minimal PC-to-Quest EEG selection messages.</summary>
    [DisallowMultipleComponent]
    public sealed class BciSelectionTransportClient : MonoBehaviour
    {
        private readonly ConcurrentQueue<BciSelectionTransportMessage> m_incoming = new ConcurrentQueue<BciSelectionTransportMessage>();
        private readonly ConcurrentQueue<string> m_outgoingLines = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<string> m_diagnostics = new ConcurrentQueue<string>();
        private readonly AutoResetEvent m_workAvailable = new AutoResetEvent(false);
        private readonly BciSelectionCoordinator m_coordinator = new BciSelectionCoordinator();
        private readonly BciPendingBatchDelivery m_pendingBatchDelivery = new BciPendingBatchDelivery();
        private readonly HashSet<string> m_publishedBatchIds = new HashSet<string>(StringComparer.Ordinal);
        private readonly Dictionary<string, M19LocalTrialContext> m_m19TrialsBySelectionId =
            new Dictionary<string, M19LocalTrialContext>(StringComparer.Ordinal);

        private sealed class M19LocalTrialContext
        {
            public string TrialId;
            public string PageId;
            public int PageIndex;
            public int PageEpoch;
        }

        private Thread m_worker;
        private volatile bool m_stopRequested;
        private BciSsvepTargetBinding m_binding;
        private string m_serverHost;
        private int m_serverPort;
        private string m_retryLine;
        private long m_nextConnectionId;
        private string m_sceneId;
        private string m_sceneLayoutSnapshotJson;

        public bool ConfigureM20SceneLayoutSnapshot(string sceneId, string snapshotJson)
        {
            if (string.IsNullOrWhiteSpace(sceneId) || string.IsNullOrWhiteSpace(snapshotJson) ||
                !snapshotJson.Contains("\"sceneId\":\"" + sceneId + "\""))
                return false;
            if (!string.IsNullOrWhiteSpace(m_sceneId) && !string.Equals(m_sceneId, sceneId, StringComparison.Ordinal))
                return false;
            m_sceneId = sceneId;
            m_sceneLayoutSnapshotJson = snapshotJson;
            return true;
        }

        /// <summary>
        /// Downstream boundary for a resolved target. Subscribers receive only
        /// an accepted result created from the Quest-owned frozen snapshot.
        /// </summary>
        public event Action<BciTargetSelectionResult> TargetSelected
        {
            add => m_coordinator.TargetSelected += value;
            remove => m_coordinator.TargetSelected -= value;
        }

        /// <summary>Raised only when Quest has accepted an opened snapshot.</summary>
        public event Action<string> SelectionOpened;

        /// <summary>Raised after an accepted open with optional M16 page metadata.</summary>
        public event Action<BciSelectionTransportMessage, BciSelectionSnapshot> SelectionOpenedWithMetadata;

        /// <summary>
        /// Gives the existing M8 batch controller the same authoritative
        /// TargetId-to-slot view before the selection is opened. A false
        /// result fails closed and does not create a pending selection.
        /// </summary>
        public event Func<string, BciSelectionSnapshot, bool> AuthoritativeSelectionOpening;

        /// <summary>Optional M16 page/session validation before legacy open handling.</summary>
        public event Func<BciSelectionTransportMessage, BciSelectionSnapshot, bool> AuthoritativeSelectionOpeningWithMetadata;

        /// <summary>Raised when an accepted selection becomes terminal locally.</summary>
        public event Action<string> SelectionTerminated;

        /// <summary>
        /// Handles the Showcase's existing target_batch_confirmed contract as a
        /// host-driven batch close. The batch controller validates the current
        /// Quest selections before clearing the active group.
        /// </summary>
        public event Func<ConfirmedTargetBatchPayload, bool> HostBatchCloseRequested;

        /// <summary>Raised when an outbound confirmed batch receives its host ACK.</summary>
        public event Action<string, bool> BatchAcknowledged;

        /// <summary>Raised after the PC explicitly accepts/rejects a local M19 trigger request.</summary>
        public event Action<BciSelectionTransportMessage> M19DecodeAcknowledged;

        /// <summary>Raised after the PC confirms an abort or reports it stale.</summary>
        public event Action<BciSelectionTransportMessage> M19DecodeAbortAcknowledged;

        /// <summary>PC-to-Quest Research offers and Quest-to-PC Research lifecycle events.</summary>
        public event Action<BciSelectionTransportMessage> ResearchMessageReceived;

        /// <summary>
        /// Open a Quest-owned immutable slot snapshot and publish the same
        /// frozen page mapping as an M19 user-triggered decode request.
        /// </summary>
        public bool BeginM19Trial(BciM19DecodeRequestPayload request, BciSelectionSnapshot snapshot)
        {
            if (!ValidateM19DecodeRequest(request) || snapshot == null ||
                m_m19TrialsBySelectionId.ContainsKey(request.selectionId))
                return false;

            BciSelectionTransportResult opened = m_coordinator.Open(request.selectionId, snapshot);
            if (!opened.IsAccepted)
                return false;

            m_binding.FreezeLayout(request.selectionId);
            m_m19TrialsBySelectionId.Add(request.selectionId, new M19LocalTrialContext
            {
                TrialId = request.trialId,
                PageId = request.pageId,
                PageIndex = request.pageIndex,
                PageEpoch = request.pageEpoch
            });
            request.questUtc = DateTime.UtcNow.ToString("O");
            m_outgoingLines.Enqueue(JsonUtility.ToJson(request) + "\n");
            m_workAvailable.Set();
            Debug.Log("M19_TRIGGER request_published trial_id=" + request.trialId +
                " selection_id=" + request.selectionId +
                " page_id=" + request.pageId +
                " page_epoch=" + request.pageEpoch +
                " active_slots=" + request.activeSlotCount, this);
            return true;
        }

        public bool PublishResearchMessage(BciSelectionTransportMessage message)
        {
            if (message == null || message.protocolVersion != BciSelectionTransportMessage.ProtocolVersion ||
                (message.messageType != "m19_research_ready" &&
                 message.messageType != "m19_research_offer_ack" &&
                 message.messageType != "m19_research_trigger" &&
                 message.messageType != "m19_research_cancel" &&
                 message.messageType != "m19_research_resume"))
                return false;
            if ((message.messageType == "m19_research_trigger" ||
                 message.messageType == "m19_research_cancel" ||
                 message.messageType == "m19_research_offer_ack") &&
                (string.IsNullOrWhiteSpace(message.sessionId) || string.IsNullOrWhiteSpace(message.trialId) ||
                 string.IsNullOrWhiteSpace(message.attemptId)))
                return false;
            if (message.messageType == "m19_research_resume" &&
                (string.IsNullOrWhiteSpace(message.sessionId) || string.IsNullOrWhiteSpace(message.blockId)))
                return false;

            message.eventUtc = string.IsNullOrWhiteSpace(message.eventUtc)
                ? DateTime.UtcNow.ToString("O")
                : message.eventUtc;
            message.eventMonotonicNs = (long)(Time.realtimeSinceStartupAsDouble * 1000000000.0);
            message.softwareFrame = Time.frameCount;
            m_outgoingLines.Enqueue(JsonUtility.ToJson(message) + "\n");
            m_workAvailable.Set();
            Debug.Log("M19_RESEARCH event_published type=" + message.messageType +
                " session_id=" + (message.sessionId ?? "") +
                " trial_id=" + (message.trialId ?? "") +
                " attempt_id=" + (message.attemptId ?? ""), this);
            return true;
        }

        /// <summary>Locally invalidate a trigger trial before navigation or queue actions, then notify PC.</summary>
        public bool AbortM19Trial(string selectionId, string reason)
        {
            if (!m_m19TrialsBySelectionId.TryGetValue(selectionId, out M19LocalTrialContext context))
                return false;
            if (!CloseM19TrialLocally(selectionId))
                return false;
            var message = new BciSelectionTransportMessage
            {
                protocolVersion = BciSelectionTransportMessage.ProtocolVersion,
                messageType = "eeg_decode_abort",
                selectionId = selectionId,
                trialId = context.TrialId,
                pageId = context.PageId,
                pageIndex = context.PageIndex,
                pageEpoch = context.PageEpoch,
                abortReason = string.IsNullOrWhiteSpace(reason) ? "cancelled" : reason,
                questUtc = DateTime.UtcNow.ToString("O")
            };
            m_outgoingLines.Enqueue(JsonUtility.ToJson(message) + "\n");
            m_workAvailable.Set();
            Debug.Log("M19_TRIGGER abort_published selection_id=" + selectionId +
                " reason=" + message.abortReason, this);
            return true;
        }

        private bool CloseM19TrialLocally(string selectionId)
        {
            if (!m_m19TrialsBySelectionId.Remove(selectionId))
                return false;
            BciSelectionTransportResult aborted = m_coordinator.Abort(selectionId);
            m_binding.ReleaseLayout(selectionId);
            if (aborted.IsAccepted)
                SelectionTerminated?.Invoke(selectionId);
            return aborted.IsAccepted;
        }

        private static bool ValidateM19DecodeRequest(BciM19DecodeRequestPayload request)
        {
            if (request == null || request.protocolVersion != BciSelectionTransportMessage.ProtocolVersion ||
                !string.Equals(request.contractId, "m19_decode_request_v1", StringComparison.Ordinal) ||
                !string.Equals(request.messageType, "eeg_decode_request", StringComparison.Ordinal) ||
                string.IsNullOrWhiteSpace(request.trialId) || string.IsNullOrWhiteSpace(request.selectionId) ||
                string.IsNullOrWhiteSpace(request.pageId) || request.pageIndex < 0 || request.pageEpoch < 0 ||
                !IsIsoTimestampWithOffset(request.createdUtc) ||
                (request.presentationPolicy != "demo_preview" && request.presentationPolicy != "research_strict") ||
                (request.presentationPolicy == "research_strict" &&
                    !IsIsoTimestampWithOffset(request.formalStimulusOnsetUtc)) ||
                request.slots == null || request.activeSlotCount < 1 || request.activeSlotCount > 3 ||
                request.slots.Length != request.activeSlotCount)
                return false;

            float[] frequencies = BciPagedSelectionQueue.NominalFrequenciesHz;
            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            var slotIndices = new HashSet<int>();
            for (int index = 0; index < request.slots.Length; index++)
            {
                BciEegDecodeSlotPayload slot = request.slots[index];
                if (slot == null || slot.slotIndex < 0 || slot.slotIndex >= frequencies.Length ||
                    Mathf.Abs(slot.frequencyHz - frequencies[slot.slotIndex]) > 0.01f ||
                    string.IsNullOrWhiteSpace(slot.targetId) ||
                    !slotIndices.Add(slot.slotIndex) || !targetIds.Add(slot.targetId))
                    return false;
            }
            return true;
        }

        private static bool IsIsoTimestampWithOffset(string value)
        {
            if (string.IsNullOrWhiteSpace(value) || value.IndexOf('T') < 0)
                return false;
            if (!DateTime.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out DateTime parsed))
                return false;
            if (value.EndsWith("Z", StringComparison.OrdinalIgnoreCase))
                return true;
            int offsetStart = value.Length - 6;
            return offsetStart > value.IndexOf('T') &&
                (value[offsetStart] == '+' || value[offsetStart] == '-') &&
                value[value.Length - 3] == ':' &&
                parsed.Kind != DateTimeKind.Unspecified;
        }

        public void Initialize(BciSsvepTargetBinding binding, string serverHost, int serverPort)
        {
            if (m_worker != null)
                return;
            if (binding == null || string.IsNullOrWhiteSpace(serverHost) || serverPort < 1)
            {
                Debug.LogWarning("M8_SELECTION transport initialization rejected: missing binding, host, or port.", this);
                return;
            }

            m_binding = binding;
            m_serverHost = serverHost;
            m_serverPort = serverPort;
            m_coordinator.TargetSelected += LogTargetSelected;
            m_worker = new Thread(NetworkLoop) { IsBackground = true, Name = "M8QuestSelectionTransport" };
            m_worker.Start();
            Debug.Log("M8_SELECTION transport initialized host=" + m_serverHost + " port=" + m_serverPort, this);
        }

        public void InitializeResearchOnly(string serverHost, int serverPort)
        {
            if (m_worker != null)
                return;
            if (string.IsNullOrWhiteSpace(serverHost) || serverPort < 1)
            {
                Debug.LogWarning("M19_RESEARCH transport initialization rejected: missing host or port.", this);
                return;
            }

            m_binding = null;
            m_serverHost = serverHost;
            m_serverPort = serverPort;
            m_worker = new Thread(NetworkLoop) { IsBackground = true, Name = "M19ResearchTransport" };
            m_worker.Start();
            Debug.Log("M19_RESEARCH transport initialized host=" + m_serverHost + " port=" + m_serverPort, this);
        }

        /// <summary>
        /// Submit has priority over a PC trial that is still open. The existing
        /// coordinator marks it terminal so a delayed eeg_selection is rejected.
        /// </summary>
        public bool AbortPendingSelectionForGroupSubmit(string selectionId)
        {
            BciSelectionTransportResult result = m_coordinator.Abort(selectionId);
            if (!result.IsAccepted)
                return false;

            m_binding.ReleaseLayout(selectionId);
            SelectionTerminated?.Invoke(selectionId);
            Debug.Log("M8_GROUP pending_selection_aborted selection_id=" + selectionId, this);
            return true;
        }

        /// <summary>
        /// M16 page navigation uses the same one-shot abort seam as submit,
        /// but keeps the reason explicit for stale-result diagnostics.
        /// </summary>
        public bool AbortPendingSelectionForPageChange(string selectionId)
        {
            BciSelectionTransportResult result = m_coordinator.Abort(selectionId);
            if (!result.IsAccepted)
                return false;

            m_binding.ReleaseLayout(selectionId);
            SelectionTerminated?.Invoke(selectionId);
            Debug.Log("M16_SELECTION aborted reason=page_changed selection_id=" + selectionId, this);
            return true;
        }

        /// <summary>
        /// Publishes the existing controller B/undo action to the PC on the
        /// same M8 newline transport.  This is a notification only: it does
        /// not change the frozen selection or batch protocol.
        /// </summary>
        public bool PublishSelectionUndo(BciTargetSelectionResult undone)
        {
            if (string.IsNullOrWhiteSpace(undone.SelectionId))
                return false;

            var message = new BciSelectionTransportMessage
            {
                protocolVersion = BciSelectionTransportMessage.ProtocolVersion,
                messageType = "selection_undo",
                selectionId = undone.SelectionId,
                predictedClassIndex = undone.PredictedClassIndex,
                resolvedSlot = undone.SlotIndex,
                resolvedTargetId = undone.TargetId,
                questUtc = DateTime.UtcNow.ToString("O")
            };
            // ConcurrentQueue.Enqueue is a mutation API and intentionally returns void.
            m_outgoingLines.Enqueue(JsonUtility.ToJson(message) + "\n");
            m_workAvailable.Set();
            Debug.Log("M8_GROUP undo_published selection_id=" + undone.SelectionId +
                " slot=" + undone.SlotIndex + " target_id=" + undone.TargetId, this);
            return true;
        }

        /// <summary>
        /// Queues exactly one Quest-originated batch notification on the existing
        /// newline-delimited transport. No robot semantics are added here.
        /// </summary>
        public bool PublishConfirmedTargetBatch(ConfirmedTargetBatch batch)
        {
            if (batch == null || string.IsNullOrWhiteSpace(batch.BatchId) || !m_publishedBatchIds.Add(batch.BatchId))
                return false;

            var message = new BciSelectionTransportMessage
            {
                protocolVersion = BciSelectionTransportMessage.ProtocolVersion,
                messageType = "target_batch_confirmed",
                batchId = batch.BatchId,
                confirmedBatch = ConfirmedTargetBatchPayload.From(batch),
                questUtc = DateTime.UtcNow.ToString("O")
            };
            if (!string.IsNullOrWhiteSpace(m_sceneId))
            {
                message.confirmedBatch.sceneId = m_sceneId;
                message.confirmedBatch.sceneLayoutSnapshotJson = m_sceneLayoutSnapshotJson;
            }
            if (!m_pendingBatchDelivery.Queue(batch.BatchId, JsonUtility.ToJson(message) + "\n"))
                return false;
            m_workAvailable.Set();
            Debug.Log(
                "M8_BATCH pending batch_id=" + batch.BatchId +
                " group_id=" + batch.GroupId +
                " group_index=" + batch.GroupIndex +
                " selection_count=" + batch.Selections.Count +
                " provenance=" + batch.Provenance,
                this);
            return true;
        }

        private void Update()
        {
            while (m_diagnostics.TryDequeue(out string diagnostic))
                Debug.Log(diagnostic, this);
            while (m_incoming.TryDequeue(out BciSelectionTransportMessage message))
                Process(message);
        }

        private void Process(BciSelectionTransportMessage message)
        {
            if (message == null)
            {
                m_diagnostics.Enqueue("M8_SELECTION malformed_message null_json_payload");
                return;
            }
            if (message.protocolVersion != BciSelectionTransportMessage.ProtocolVersion)
            {
                SendResult(message, BciSelectionTransportRejection.InvalidSelectionId, default(BciSelectionTarget));
                return;
            }

            if (!string.IsNullOrWhiteSpace(message.messageType) &&
                message.messageType.StartsWith("m19_research_", StringComparison.Ordinal))
            {
                ResearchMessageReceived?.Invoke(message);
                return;
            }

            if (message.messageType == "eeg_decode_ack")
            {
                if (!m_m19TrialsBySelectionId.TryGetValue(message.selectionId, out M19LocalTrialContext context) ||
                    !string.Equals(message.trialId, context.TrialId, StringComparison.Ordinal) ||
                    !string.Equals(message.pageId, context.PageId, StringComparison.Ordinal) ||
                    message.pageEpoch != context.PageEpoch)
                {
                    Debug.LogWarning("STALE_SELECTION_REJECTED message=eeg_decode_ack selection_id=" +
                        (message.selectionId ?? ""), this);
                    return;
                }
                if (!message.accepted)
                    CloseM19TrialLocally(message.selectionId);
                M19DecodeAcknowledged?.Invoke(message);
            }
            else if (message.messageType == "eeg_decode_abort_ack")
            {
                M19DecodeAbortAcknowledged?.Invoke(message);
                Debug.Log("M19_TRIGGER abort_ack selection_id=" + (message.selectionId ?? "") +
                    " accepted=" + message.accepted +
                    " reason=" + (message.rejectionReason ?? ""), this);
            }
            else if (message.messageType == "selection_open")
            {
                BciSelectionSnapshot snapshot;
                bool authoritativeSnapshot = !string.IsNullOrWhiteSpace(message.candidateSnapshotId) &&
                    message.candidateSnapshot != null;
                if (authoritativeSnapshot)
                {
                    string rejectionReason;
                    if (!m_binding.TryCreateAuthoritativeSelectionSnapshot(
                            message.candidateSnapshotId,
                            message.candidateSnapshotVersion,
                            message.candidateSnapshot,
                            out snapshot,
                            out rejectionReason))
                    {
                        Debug.LogWarning("M8_SELECTION authoritative_snapshot_rejected selection_id=" +
                            message.selectionId + " reason=" + rejectionReason, this);
                        SendResult(message, BciSelectionTransportRejection.TargetInvalid, default(BciSelectionTarget));
                        return;
                    }

                    if (AuthoritativeSelectionOpeningWithMetadata != null &&
                        !AuthoritativeSelectionOpeningWithMetadata.Invoke(message, snapshot))
                    {
                        Debug.LogWarning("M8_SELECTION authoritative_snapshot_rejected selection_id=" +
                            message.selectionId + " reason=page_metadata_rejected", this);
                        SendResult(message, BciSelectionTransportRejection.TargetInvalid, default(BciSelectionTarget));
                        return;
                    }

                    if (AuthoritativeSelectionOpening != null &&
                        !AuthoritativeSelectionOpening.Invoke(message.selectionId, snapshot))
                    {
                        Debug.LogWarning("M8_SELECTION authoritative_snapshot_rejected selection_id=" +
                            message.selectionId + " reason=batch_controller_mapping_rejected", this);
                        SendResult(message, BciSelectionTransportRejection.TargetInvalid, default(BciSelectionTarget));
                        return;
                    }
                }
                else
                {
                    snapshot = m_binding.CreateSelectionSnapshot();
                }
                BciSelectionTransportResult result = m_coordinator.Open(message.selectionId, snapshot);
                if (result.IsAccepted)
                {
                    m_binding.FreezeLayout(message.selectionId);
                    SelectionOpened?.Invoke(message.selectionId);
                    SelectionOpenedWithMetadata?.Invoke(message, snapshot);
                }
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "eeg_selection")
            {
                bool isM19Trial = m_m19TrialsBySelectionId.TryGetValue(
                    message.selectionId, out M19LocalTrialContext m19Context);
                if (isM19Trial &&
                    (!message.hasDecisionMade || !message.decisionMade ||
                     !string.Equals(message.trialId, m19Context.TrialId, StringComparison.Ordinal) ||
                     !string.Equals(message.pageId, m19Context.PageId, StringComparison.Ordinal) ||
                     message.pageEpoch != m19Context.PageEpoch ||
                     message.classIndex != message.predictedClassIndex ||
                     message.slotIndex != message.predictedClassIndex ||
                     !string.IsNullOrWhiteSpace(message.resolvedTargetId)))
                {
                    CloseM19TrialLocally(message.selectionId);
                    Debug.LogWarning("STALE_SELECTION_REJECTED message=eeg_selection selection_id=" +
                        (message.selectionId ?? "") + " reason=frozen_page_or_result_authority_mismatch", this);
                    SendResult(message, BciSelectionTransportRejection.TargetInvalid, default(BciSelectionTarget));
                    return;
                }
                BciSelectionTransportResult result = m_coordinator.Resolve(
                    message.selectionId,
                    message.predictedClassIndex,
                    message.candidateSnapshotId,
                    message.candidateSnapshotVersion);
                if (isM19Trial)
                    m_m19TrialsBySelectionId.Remove(message.selectionId);
                m_binding.ReleaseLayout(message.selectionId);
                if (result.Rejection != BciSelectionTransportRejection.UnknownSelectionId &&
                    result.Rejection != BciSelectionTransportRejection.DuplicateDecision)
                    SelectionTerminated?.Invoke(message.selectionId);
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "selection_abort")
            {
                bool wasM19Trial = m_m19TrialsBySelectionId.ContainsKey(message.selectionId);
                BciSelectionTransportResult result = m_coordinator.Abort(message.selectionId);
                if (wasM19Trial)
                    m_m19TrialsBySelectionId.Remove(message.selectionId);
                if (result.IsAccepted)
                {
                    m_binding.ReleaseLayout(message.selectionId);
                    SelectionTerminated?.Invoke(message.selectionId);
                }
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "target_batch_confirmed")
            {
                bool accepted = false;
                string rejectionReason = "HostBatchCloseUnavailable";
                try
                {
                    if (HostBatchCloseRequested != null && message.confirmedBatch != null)
                    {
                        accepted = HostBatchCloseRequested.Invoke(message.confirmedBatch);
                        rejectionReason = accepted ? "None" : "HostBatchCloseRejected";
                    }
                }
                catch (Exception exception)
                {
                    rejectionReason = "HostBatchCloseException:" + exception.GetType().Name;
                    Debug.LogWarning("M8_GROUP host_batch_close_exception batch_id=" +
                        (message.batchId ?? "") + " reason=" + rejectionReason, this);
                }
                SendBatchAck(message, accepted, rejectionReason);
            }
            else if (message.messageType == "batch_ack")
            {
                bool acknowledged = m_pendingBatchDelivery.Acknowledge(message.batchId);
                bool accepted = message.accepted || string.IsNullOrWhiteSpace(message.rejectionReason);
                BatchAcknowledged?.Invoke(message.batchId, acknowledged && accepted);
                Debug.Log("M8_BATCH ack batch_id=" + (message.batchId ?? "") +
                    " accepted=" + acknowledged +
                    " pending_count=" + m_pendingBatchDelivery.PendingCount, this);
            }
            else
            {
                SendResult(message, BciSelectionTransportRejection.InvalidSelectionId, default(BciSelectionTarget));
            }
        }

        private void SendBatchAck(BciSelectionTransportMessage request, bool accepted, string rejectionReason)
        {
            var result = new BciSelectionTransportMessage
            {
                protocolVersion = BciSelectionTransportMessage.ProtocolVersion,
                messageType = "batch_ack",
                batchId = request.batchId ?? (request.confirmedBatch != null ? request.confirmedBatch.batchId : null),
                accepted = accepted,
                rejectionReason = rejectionReason,
                questUtc = DateTime.UtcNow.ToString("O")
            };
            m_outgoingLines.Enqueue(JsonUtility.ToJson(result) + "\n");
            m_workAvailable.Set();
            Debug.Log("M8_GROUP host_batch_close batch_id=" + (result.batchId ?? "") +
                " accepted=" + accepted + " rejection=" + rejectionReason, this);
        }

        private void SendResult(BciSelectionTransportMessage request, BciSelectionTransportRejection rejection, BciSelectionTarget target)
        {
            var result = new BciSelectionTransportMessage
            {
                messageType = "selection_ack",
                selectionId = request.selectionId,
                predictedClassIndex = request.predictedClassIndex,
                accepted = rejection == BciSelectionTransportRejection.None,
                rejectionReason = rejection.ToString(),
                resolvedSlot = rejection == BciSelectionTransportRejection.None ? target.SlotIndex : -1,
                resolvedTargetId = rejection == BciSelectionTransportRejection.None ? target.TargetId : null,
                resolvedClassName = rejection == BciSelectionTransportRejection.None ? target.ClassName : null,
                candidateSnapshotId = request.candidateSnapshotId,
                candidateSnapshotVersion = request.candidateSnapshotVersion,
                pageId = request.pageId,
                pageEpoch = request.pageEpoch,
                questUtc = DateTime.UtcNow.ToString("O")
            };
            m_outgoingLines.Enqueue(JsonUtility.ToJson(result) + "\n");
            m_workAvailable.Set();
            Debug.Log("M8_SELECTION selection_id=" + result.selectionId +
                " predicted_class=" + result.predictedClassIndex +
                " accepted=" + result.accepted +
                " slot=" + result.resolvedSlot +
                " target_id=" + (result.resolvedTargetId ?? "") +
                " class=" + (result.resolvedClassName ?? "") +
                " rejection=" + result.rejectionReason, this);
        }

        private void LogTargetSelected(BciTargetSelectionResult result)
        {
            Debug.Log(
                "M8_TARGET_SELECTED selection_id=" + result.SelectionId +
                " predicted_class=" + result.PredictedClassIndex +
                " slot=" + result.SlotIndex +
                " target_id=" + result.TargetId +
                " class=" + result.SemanticLabel +
                " has_world_position=" + result.HasWorldPosition +
                " world_position=" + result.WorldPosition.ToString("F4") +
                " provenance=" + result.Provenance,
                this);
        }

        private void NetworkLoop()
        {
            while (!m_stopRequested)
            {
                try
                {
                    using (var client = new TcpClient())
                    {
                        client.Connect(m_serverHost, m_serverPort);
                        client.NoDelay = true;
                        using (NetworkStream stream = client.GetStream())
                        {
                            stream.ReadTimeout = 100;
                            stream.WriteTimeout = 500;
                            m_diagnostics.Enqueue("M8_SELECTION connection_opened " + m_serverHost + ":" + m_serverPort);
                            ConnectedLoop(client, stream, Interlocked.Increment(ref m_nextConnectionId));
                        }
                    }
                }
                catch (Exception exception)
                {
                    m_diagnostics.Enqueue("M8_SELECTION connection_failure " + exception.Message);
                }
                if (!m_stopRequested)
                    m_workAvailable.WaitOne(1000);
            }
        }

        private void ConnectedLoop(TcpClient client, NetworkStream stream, long connectionId)
        {
            var receiveBuffer = new byte[4096];
            var textBuffer = new StringBuilder();
            // Read is authoritative for EOF and socket errors. TcpClient.Connected can
            // report false after a transient timed read even while this stream is usable.
            while (!m_stopRequested)
            {
                while (true)
                {
                    string line = Interlocked.Exchange(ref m_retryLine, null);
                    if (line == null && !m_outgoingLines.TryDequeue(out line))
                        break;
                    byte[] bytes = Encoding.UTF8.GetBytes(line);
                    try { stream.Write(bytes, 0, bytes.Length); }
                    catch
                    {
                        Interlocked.CompareExchange(ref m_retryLine, line, null);
                        throw;
                    }
                }

                IReadOnlyList<string> pendingBatchLines = m_pendingBatchDelivery.GetUnsentLinesForConnection(connectionId);
                for (int index = 0; index < pendingBatchLines.Count; index++)
                {
                    byte[] bytes = Encoding.UTF8.GetBytes(pendingBatchLines[index]);
                    stream.Write(bytes, 0, bytes.Length);
                }

                try
                {
                    // Only call Read when the socket is actually readable.
                    // This prevents the worker thread from blocking here while
                    // the Unity main thread queues outbound M19 requests.
                    if (client.Client.Poll(0, SelectMode.SelectRead))
                    {
                        int count = stream.Read(receiveBuffer, 0, receiveBuffer.Length);
                        if (count == 0)
                        {
                            m_diagnostics.Enqueue("M8_SELECTION connection_closed remote_eof");
                            return;
                        }

                        textBuffer.Append(Encoding.UTF8.GetString(receiveBuffer, 0, count));
                        DequeueCompleteLines(textBuffer);
                    }
                    else
                    {
                        // Wakes immediately when outbound work is queued.
                        m_workAvailable.WaitOne(10);
                    }
                }
                catch (IOException exception) when (IsTransientReadException(exception))
                {
                    m_workAvailable.WaitOne(10);
                }
            }
        }

        public static bool IsTransientReadException(IOException exception)
        {
            var socket = exception == null ? null : exception.InnerException as SocketException;
            if (socket == null)
                return false;

            return socket.SocketErrorCode == SocketError.TimedOut ||
                   socket.SocketErrorCode == SocketError.WouldBlock;
        }

        private void DequeueCompleteLines(StringBuilder buffer)
        {
            while (true)
            {
                string all = buffer.ToString();
                int newline = all.IndexOf('\n');
                if (newline < 0)
                    return;
                string line = all.Substring(0, newline).TrimEnd('\r');
                buffer.Remove(0, newline + 1);
                if (line.Length == 0)
                    continue;
                try { m_incoming.Enqueue(JsonUtility.FromJson<BciSelectionTransportMessage>(line)); }
                catch (Exception exception) { m_diagnostics.Enqueue("M8_SELECTION malformed_message " + exception.Message); }
            }
        }

        private void OnDestroy()
        {
            m_coordinator.TargetSelected -= LogTargetSelected;
            m_stopRequested = true;
            m_workAvailable.Set();
            if (m_worker != null && m_worker.IsAlive)
                m_worker.Join(1500);
            m_workAvailable.Dispose();
        }
    }
}



