using System;
using System.Collections.Generic;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Process-lifetime delivery state for confirmed batches. A batch remains
    /// pending until its matching PC acknowledgement arrives; a reconnect gets
    /// one resend of every still-pending batch in original publish order.
    /// </summary>
    public sealed class BciPendingBatchDelivery
    {
        private sealed class PendingBatch
        {
            public PendingBatch(string batchId, string line)
            {
                BatchId = batchId;
                Line = line;
                LastSentConnectionId = long.MinValue;
            }

            public string BatchId { get; }
            public string Line { get; }
            public long LastSentConnectionId { get; set; }
        }

        private readonly object m_gate = new object();
        private readonly List<PendingBatch> m_pending = new List<PendingBatch>();

        public int PendingCount
        {
            get
            {
                lock (m_gate)
                    return m_pending.Count;
            }
        }

        public IReadOnlyList<string> PendingBatchIds
        {
            get
            {
                lock (m_gate)
                {
                    var ids = new string[m_pending.Count];
                    for (int index = 0; index < ids.Length; index++)
                        ids[index] = m_pending[index].BatchId;
                    return ids;
                }
            }
        }

        public bool Queue(string batchId, string line)
        {
            if (string.IsNullOrWhiteSpace(batchId) || string.IsNullOrEmpty(line))
                return false;

            lock (m_gate)
            {
                for (int index = 0; index < m_pending.Count; index++)
                {
                    if (string.Equals(m_pending[index].BatchId, batchId, StringComparison.Ordinal))
                        return false;
                }

                m_pending.Add(new PendingBatch(batchId, line));
                return true;
            }
        }

        public IReadOnlyList<string> GetUnsentLinesForConnection(long connectionId)
        {
            lock (m_gate)
            {
                var lines = new List<string>();
                for (int index = 0; index < m_pending.Count; index++)
                {
                    PendingBatch pending = m_pending[index];
                    if (pending.LastSentConnectionId == connectionId)
                        continue;

                    pending.LastSentConnectionId = connectionId;
                    lines.Add(pending.Line);
                }
                return lines;
            }
        }

        public bool Acknowledge(string batchId)
        {
            if (string.IsNullOrWhiteSpace(batchId))
                return false;

            lock (m_gate)
            {
                for (int index = 0; index < m_pending.Count; index++)
                {
                    if (!string.Equals(m_pending[index].BatchId, batchId, StringComparison.Ordinal))
                        continue;

                    m_pending.RemoveAt(index);
                    return true;
                }
                return false;
            }
        }
    }
}
