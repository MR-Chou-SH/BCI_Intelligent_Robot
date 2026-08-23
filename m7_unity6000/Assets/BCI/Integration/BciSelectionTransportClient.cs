using System;
using System.Collections.Concurrent;
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

        private Thread m_worker;
        private volatile bool m_stopRequested;
        private BciSsvepTargetBinding m_binding;
        private string m_serverHost;
        private int m_serverPort;
        private string m_retryLine;

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
            m_worker = new Thread(NetworkLoop) { IsBackground = true, Name = "M8QuestSelectionTransport" };
            m_worker.Start();
            Debug.Log("M8_SELECTION transport initialized host=" + m_serverHost + " port=" + m_serverPort, this);
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
                SendResult(message, result.Rejection, result.Target);
            }
            else if (message.messageType == "eeg_selection")
            {
                BciSelectionTransportResult result = m_coordinator.Resolve(message.selectionId, message.predictedClassIndex);
                SendResult(message, result.Rejection, result.Target);
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
                            ConnectedLoop(client, stream);
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

        private void ConnectedLoop(TcpClient client, NetworkStream stream)
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
                catch (IOException exception) when (exception.InnerException is SocketException socket && socket.SocketErrorCode == SocketError.TimedOut)
                {
                }
            }
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
            m_stopRequested = true;
            m_workAvailable.Set();
            if (m_worker != null && m_worker.IsAlive)
                m_worker.Join(1500);
            m_workAvailable.Dispose();
        }
    }
}
