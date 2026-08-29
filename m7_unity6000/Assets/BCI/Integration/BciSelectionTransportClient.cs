using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.Integration
{
    [Serializable]
    public sealed class BciSelectionTransportMessage
    {
        public const int ProtocolVersion = 1;
        public int protocolVersion = ProtocolVersion;
        public string messageType;
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

        private Thread m_worker;
        private volatile bool m_stopRequested;
        private BciSsvepTargetBinding m_binding;
        private string m_serverHost;
        private int m_serverPort;
        private string m_retryLine;
        private long m_nextConnectionId;

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

        /// <summary>Raised when an accepted selection becomes terminal locally.</summary>
        public event Action<string> SelectionTerminated;

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

        /// <summary>
        /// Notify the active PC live-selection orchestration that a locally
        /// accepted selection was undone. This is an M8 interaction event only;
        /// it does not alter the immutable accepted selection result.
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
                resolvedSlot = undone.SlotIndex,
                resolvedTargetId = undone.TargetId,
                questUtc = DateTime.UtcNow.ToString("O")
            };
            m_outgoingLines.Enqueue(JsonUtility.ToJson(message) + "\n");
            m_workAvailable.Set();
            Debug.Log("M8_SELECTION selection_undo selection_id=" + undone.SelectionId +
                " slot=" + undone.SlotIndex + " target_id=" + undone.TargetId, this);
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

            if (message.messageType == "selection_open")
            {
                BciSelectionTransportResult result = m_coordinator.Open(message.selectionId, m_binding.CreateSelectionSnapshot());
                if (result.IsAccepted)
                {
                    m_binding.FreezeLayout(message.selectionId);
                    SelectionOpened?.Invoke(message.selectionId);
                }
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "eeg_selection")
            {
                BciSelectionTransportResult result = m_coordinator.Resolve(message.selectionId, message.predictedClassIndex);
                m_binding.ReleaseLayout(message.selectionId);
                if (result.Rejection != BciSelectionTransportRejection.UnknownSelectionId &&
                    result.Rejection != BciSelectionTransportRejection.DuplicateDecision)
                    SelectionTerminated?.Invoke(message.selectionId);
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "selection_abort")
            {
                BciSelectionTransportResult result = m_coordinator.Abort(message.selectionId);
                if (result.IsAccepted)
                {
                    m_binding.ReleaseLayout(message.selectionId);
                    SelectionTerminated?.Invoke(message.selectionId);
                }
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "batch_ack")
            {
                bool acknowledged = m_pendingBatchDelivery.Acknowledge(message.batchId);
                Debug.Log("M8_BATCH ack batch_id=" + (message.batchId ?? "") +
                    " accepted=" + acknowledged +
                    " pending_count=" + m_pendingBatchDelivery.PendingCount, this);
            }
            else
            {
                SendResult(message, BciSelectionTransportRejection.InvalidSelectionId, default(BciSelectionTarget));
            }
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
                        IAsyncResult attempt = client.BeginConnect(m_serverHost, m_serverPort, null, null);
                        if (!attempt.AsyncWaitHandle.WaitOne(500) || !client.Connected)
                            throw new SocketException((int)SocketError.TimedOut);
                        client.EndConnect(attempt);
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
            while (!m_stopRequested && client.Connected)
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
                    int count = stream.Read(receiveBuffer, 0, receiveBuffer.Length);
                    if (count == 0)
                    {
                        m_diagnostics.Enqueue("M8_SELECTION connection_closed remote_eof");
                        return;
                    }
                    textBuffer.Append(Encoding.UTF8.GetString(receiveBuffer, 0, count));
                    DequeueCompleteLines(textBuffer);
                }
                catch (IOException exception) when (IsTransientReadException(exception))
                {
                    // WouldBlock can return immediately on Android; avoid a worker-thread busy-spin.
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
