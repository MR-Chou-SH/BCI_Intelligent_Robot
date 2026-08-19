using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;
using Debug = UnityEngine.Debug;

namespace BCIIntelligentRobot.VRStimulus
{
    [Serializable]
    internal sealed class StimulusEventEnvelope
    {
        public int protocolVersion = 1;
        public string messageType = "stimulus_event";
        public StimulusEventRecord eventPayload;
    }

    [Serializable]
    internal sealed class ClockSyncRequest
    {
        public int protocolVersion = 1;
        public string messageType = "clock_sync_request";
        public long syncSequence;
        public double q1QuestMonotonicSeconds;
        public string questUtc;
    }

    [Serializable]
    internal sealed class IncomingTransportMessage
    {
        public int protocolVersion;
        public string messageType;
        public string sessionId;
        public long sequence;
        public string connectionId;
        public long syncSequence;
        public double q1QuestMonotonicSeconds;
        public long p2PcReceiveMonotonicNs;
        public long p3PcSendMonotonicNs;
        public string validationStatus;
        public string sequenceStatus;
    }

    [Serializable]
    public sealed class DatasetProtocolTiming
    {
        public float preparationSeconds;
        public float cueSeconds;
        public float preStimulusRestSeconds;
        public float stimulusSeconds;
        public float postStimulusRestSeconds;
        public int[] breakAfterTrials;
        public float breakSeconds;
    }

    [Serializable]
    public sealed class DatasetTrialPlanItem
    {
        public string sessionId;
        public string trialId;
        public int trialIndex;
        public string targetId;
        public string targetSide;
        public float nominalFrequencyHz;
        public float expectedStimulusDurationSeconds;
    }

    [Serializable]
    public sealed class DatasetTrialPlanMessage
    {
        public int protocolVersion;
        public string messageType;
        public string sessionId;
        public DatasetProtocolTiming protocol;
        public DatasetTrialPlanItem[] trials;
    }

    [Serializable]
    internal sealed class ClockSyncResult
    {
        public int protocolVersion = 1;
        public string messageType = "clock_sync_result";
        public long syncSequence;
        public double q1QuestMonotonicSeconds;
        public long p2PcReceiveMonotonicNs;
        public long p3PcSendMonotonicNs;
        public double q4QuestMonotonicSeconds;
        public double roundTripSeconds;
        public double offsetPcMinusQuestSeconds;
        public string offsetSignConvention = "pc_minus_quest";
        public string connectionId;
    }

    [Serializable]
    public sealed class SynchronizationDiagnosticRecord
    {
        public string recordType;
        public string questUtc;
        public double questMonotonicSeconds;
        public string sessionId;
        public long sequence;
        public long syncSequence;
        public string connectionId;
        public string status;
        public string detail;
        public double q1QuestMonotonicSeconds;
        public long p2PcReceiveMonotonicNs;
        public long p3PcSendMonotonicNs;
        public double q4QuestMonotonicSeconds;
        public double roundTripSeconds;
        public double offsetPcMinusQuestSeconds;
        public string offsetSignConvention;
    }

    [DefaultExecutionOrder(-150)]
    [DisallowMultipleComponent]
    public sealed class StimulusEventTransportClient : MonoBehaviour
    {
        [SerializeField] private string m_ServerHost = "127.0.0.1";
        [SerializeField, Min(1)] private int m_ServerPort = 11000;
        [SerializeField, Min(0.2f)] private float m_ReconnectDelaySeconds = 1f;
        [SerializeField, Min(0.5f)] private float m_ClockSyncIntervalSeconds = 2f;
        [SerializeField] private LocalSynchronizationLogger m_DiagnosticLogger;

        private readonly ConcurrentQueue<string> m_OutgoingLines = new ConcurrentQueue<string>();
        private readonly ConcurrentQueue<SynchronizationDiagnosticRecord> m_Diagnostics =
            new ConcurrentQueue<SynchronizationDiagnosticRecord>();
        private readonly ConcurrentQueue<DatasetTrialPlanMessage> m_DatasetPlans =
            new ConcurrentQueue<DatasetTrialPlanMessage>();
        private readonly AutoResetEvent m_WorkAvailable = new AutoResetEvent(false);
        private readonly Dictionary<long, double> m_PendingSync = new Dictionary<long, double>();
        private readonly ConcurrentDictionary<long, double> m_PendingAcks =
            new ConcurrentDictionary<long, double>();
        private Thread m_Worker;
        private volatile bool m_StopRequested;
        private long m_SyncSequence;
        private string m_RetryLine;
        private double m_BaseUnityMonotonic;
        private long m_BaseStopwatchTicks;
        private double m_NextAckTimeoutCheck;

        private void Awake()
        {
            m_BaseUnityMonotonic = Time.realtimeSinceStartupAsDouble;
            m_BaseStopwatchTicks = Stopwatch.GetTimestamp();
            m_StopRequested = false;
            m_Worker = new Thread(NetworkLoop) { IsBackground = true, Name = "M5QuestPcTransport" };
            m_Worker.Start();
        }

        public bool Publish(StimulusEventRecord eventRecord)
        {
            if (eventRecord == null || m_StopRequested)
                return false;
            try
            {
                string line = JsonUtility.ToJson(new StimulusEventEnvelope { eventPayload = eventRecord }) + "\n";
                m_OutgoingLines.Enqueue(line);
                m_PendingAcks[eventRecord.sequence] = QuestMonotonicNow();
                m_WorkAvailable.Set();
                EnqueueDiagnostic("event_queued", eventRecord.sessionId, eventRecord.sequence, "queued", eventRecord.eventType);
                return true;
            }
            catch (Exception exception)
            {
                EnqueueDiagnostic("event_enqueue_error", eventRecord.sessionId, eventRecord.sequence, "error", exception.Message);
                return false;
            }
        }

        public bool TryDequeueDatasetPlan(out DatasetTrialPlanMessage plan)
        {
            return m_DatasetPlans.TryDequeue(out plan);
        }

        private void Update()
        {
            double now = QuestMonotonicNow();
            if (now >= m_NextAckTimeoutCheck)
            {
                m_NextAckTimeoutCheck = now + 1.0;
                foreach (KeyValuePair<long, double> pending in m_PendingAcks)
                {
                    if (now - pending.Value >= 5.0 && m_PendingAcks.TryRemove(pending.Key, out _))
                        EnqueueDiagnostic("event_ack_timeout", "", pending.Key, "error", "no_ack_within_5_seconds");
                }
            }
            while (m_Diagnostics.TryDequeue(out SynchronizationDiagnosticRecord record))
            {
                if (m_DiagnosticLogger != null)
                    m_DiagnosticLogger.Record(record);
                if (record.recordType.StartsWith("dataset_session_plan", StringComparison.Ordinal))
                    Debug.Log("M6DIAG transport " + record.recordType + ": " + record.detail, this);
                if (record.status == "error")
                    Debug.LogWarning($"M5.2 transport {record.recordType}: {record.detail}", this);
            }
        }

        private void NetworkLoop()
        {
            while (!m_StopRequested)
            {
                try
                {
                    using (var client = new TcpClient())
                    {
                        IAsyncResult attempt = client.BeginConnect(m_ServerHost, m_ServerPort, null, null);
                        if (!attempt.AsyncWaitHandle.WaitOne(500) || !client.Connected)
                            throw new SocketException((int)SocketError.TimedOut);
                        client.EndConnect(attempt);
                        client.NoDelay = true;
                        using (NetworkStream stream = client.GetStream())
                        {
                            stream.ReadTimeout = 100;
                            stream.WriteTimeout = 500;
                            lock (m_PendingSync)
                                m_PendingSync.Clear();
                            EnqueueDiagnostic("connection_opened", "", -1, "connected", m_ServerHost + ":" + m_ServerPort);
                            ConnectedLoop(client, stream);
                        }
                    }
                }
                catch (Exception exception)
                {
                    EnqueueDiagnostic("connection_failure", "", -1, "error", exception.Message);
                }
                if (!m_StopRequested)
                    m_WorkAvailable.WaitOne((int)(m_ReconnectDelaySeconds * 1000f));
            }
        }

        private void ConnectedLoop(TcpClient client, NetworkStream stream)
        {
            var receiveBuffer = new byte[4096];
            var textBuffer = new StringBuilder();
            double nextSync = QuestMonotonicNow();
            while (!m_StopRequested && client.Connected)
            {
                double now = QuestMonotonicNow();
                if (now >= nextSync)
                {
                    QueueClockSync(now);
                    nextSync = now + m_ClockSyncIntervalSeconds;
                }
                while (true)
                {
                    string line = Interlocked.Exchange(ref m_RetryLine, null);
                    if (line == null && !m_OutgoingLines.TryDequeue(out line))
                        break;
                    byte[] bytes = Encoding.UTF8.GetBytes(line);
                    try
                    {
                        stream.Write(bytes, 0, bytes.Length);
                    }
                    catch
                    {
                        // Retry this complete line before later queued messages. The PC
                        // deduplicates by sessionId + sequence if the write did land.
                        Interlocked.CompareExchange(ref m_RetryLine, line, null);
                        throw;
                    }
                }
                try
                {
                    if (stream.DataAvailable)
                    {
                        int count = stream.Read(receiveBuffer, 0, receiveBuffer.Length);
                        if (count == 0)
                            return;
                        textBuffer.Append(Encoding.UTF8.GetString(receiveBuffer, 0, count));
                        ProcessCompleteLines(textBuffer);
                    }
                    else
                    {
                        m_WorkAvailable.WaitOne(10);
                    }
                }
                catch (IOException exception) when (exception.InnerException is SocketException socket &&
                    socket.SocketErrorCode == SocketError.TimedOut)
                {
                }
            }
        }

        private void QueueClockSync(double q1)
        {
            long sequence = Interlocked.Increment(ref m_SyncSequence) - 1;
            lock (m_PendingSync)
                m_PendingSync[sequence] = q1;
            var request = new ClockSyncRequest
            {
                syncSequence = sequence,
                q1QuestMonotonicSeconds = q1,
                questUtc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture)
            };
            m_OutgoingLines.Enqueue(JsonUtility.ToJson(request) + "\n");
        }

        private void ProcessCompleteLines(StringBuilder buffer)
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
                IncomingTransportMessage message;
                try { message = JsonUtility.FromJson<IncomingTransportMessage>(line); }
                catch (Exception exception)
                {
                    EnqueueDiagnostic("malformed_response", "", -1, "error", exception.Message);
                    continue;
                }
                if (message.messageType == "ack")
                {
                    m_PendingAcks.TryRemove(message.sequence, out _);
                    EnqueueDiagnostic("event_acknowledged", message.sessionId, message.sequence,
                        message.validationStatus == "valid" ? "ok" : "error",
                        message.sequenceStatus + ";" + message.validationStatus, message.connectionId);
                }
                else if (message.messageType == "dataset_session_plan")
                {
                    DatasetTrialPlanMessage plan = JsonUtility.FromJson<DatasetTrialPlanMessage>(line);
                    if (plan != null && plan.trials != null)
                    {
                        m_DatasetPlans.Enqueue(plan);
                        int left = 0, center = 0, right = 0;
                        foreach (DatasetTrialPlanItem item in plan.trials)
                        {
                            if (item.targetId == "target_left") left++;
                            else if (item.targetId == "target_center") center++;
                            else if (item.targetId == "target_right") right++;
                        }
                        EnqueueDiagnostic("dataset_session_plan_received", plan.sessionId, -1, "ok",
                            "trialCount=" + plan.trials.Length + ";left=" + left + ";center=" + center + ";right=" + right);
                    }
                    else
                    {
                        EnqueueDiagnostic("dataset_session_plan_rejected", "", -1, "error", "missing_trials");
                    }
                }
                else if (message.messageType == "clock_sync_response")
                {
                    HandleClockSyncResponse(message);
                }
                else
                {
                    EnqueueDiagnostic("unexpected_response", "", -1, "error", message.messageType);
                }
            }
        }

        private void HandleClockSyncResponse(IncomingTransportMessage response)
        {
            double q4 = QuestMonotonicNow();
            double q1;
            lock (m_PendingSync)
            {
                if (!m_PendingSync.TryGetValue(response.syncSequence, out q1))
                    return;
                m_PendingSync.Remove(response.syncSequence);
            }
            double p2 = response.p2PcReceiveMonotonicNs / 1e9;
            double p3 = response.p3PcSendMonotonicNs / 1e9;
            double rtt = (q4 - q1) - (p3 - p2);
            double offset = ((p2 - q1) + (p3 - q4)) / 2.0;
            var result = new ClockSyncResult
            {
                syncSequence = response.syncSequence,
                q1QuestMonotonicSeconds = q1,
                p2PcReceiveMonotonicNs = response.p2PcReceiveMonotonicNs,
                p3PcSendMonotonicNs = response.p3PcSendMonotonicNs,
                q4QuestMonotonicSeconds = q4,
                roundTripSeconds = rtt,
                offsetPcMinusQuestSeconds = offset,
                connectionId = response.connectionId
            };
            m_OutgoingLines.Enqueue(JsonUtility.ToJson(result) + "\n");
            m_Diagnostics.Enqueue(new SynchronizationDiagnosticRecord
            {
                recordType = "clock_sync_sample", questUtc = DateTime.UtcNow.ToString("O"),
                questMonotonicSeconds = q4, syncSequence = response.syncSequence,
                connectionId = response.connectionId, status = rtt >= 0 ? "ok" : "error",
                q1QuestMonotonicSeconds = q1, p2PcReceiveMonotonicNs = response.p2PcReceiveMonotonicNs,
                p3PcSendMonotonicNs = response.p3PcSendMonotonicNs, q4QuestMonotonicSeconds = q4,
                roundTripSeconds = rtt, offsetPcMinusQuestSeconds = offset,
                offsetSignConvention = "pc_minus_quest"
            });
        }

        private double QuestMonotonicNow()
        {
            return m_BaseUnityMonotonic +
                (Stopwatch.GetTimestamp() - m_BaseStopwatchTicks) / (double)Stopwatch.Frequency;
        }

        private void EnqueueDiagnostic(string type, string session, long sequence, string status, string detail,
            string connection = "")
        {
            m_Diagnostics.Enqueue(new SynchronizationDiagnosticRecord
            {
                recordType = type, questUtc = DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture),
                questMonotonicSeconds = QuestMonotonicNow(), sessionId = session, sequence = sequence,
                status = status, detail = detail, connectionId = connection
            });
        }

        private void OnDestroy()
        {
            m_StopRequested = true;
            m_WorkAvailable.Set();
            if (m_Worker != null && m_Worker.IsAlive)
                m_Worker.Join(1500);
            m_WorkAvailable.Dispose();
        }
    }
}
