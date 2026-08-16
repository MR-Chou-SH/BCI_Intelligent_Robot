using System;
using System.Collections.Concurrent;
using System.IO;
using System.Text;
using System.Threading;
using UnityEngine;

namespace BCIIntelligentRobot.VRStimulus
{
    [DefaultExecutionOrder(-200)]
    public sealed class LocalSynchronizationLogger : MonoBehaviour
    {
        private readonly ConcurrentQueue<string> m_Lines = new ConcurrentQueue<string>();
        private readonly AutoResetEvent m_Available = new AutoResetEvent(false);
        private Thread m_Thread;
        private volatile bool m_Stop;
        private string m_Path;

        private void Awake()
        {
            string directory = Path.Combine(Application.persistentDataPath, "M5SynchronizationLogs");
            Directory.CreateDirectory(directory);
            m_Path = Path.Combine(directory, "quest-synchronization-" + Guid.NewGuid().ToString("N") + ".jsonl");
            m_Thread = new Thread(WriteLoop) { IsBackground = true, Name = "M5SyncDiagnosticWriter" };
            m_Thread.Start();
            Debug.Log("M5.2 Quest synchronization log path=" + m_Path, this);
        }

        public void Record(SynchronizationDiagnosticRecord record)
        {
            if (record == null || m_Stop)
                return;
            m_Lines.Enqueue(JsonUtility.ToJson(record));
            m_Available.Set();
        }

        private void WriteLoop()
        {
            try
            {
                using (var writer = new StreamWriter(m_Path, true, new UTF8Encoding(false)))
                {
                    writer.AutoFlush = true;
                    while (!m_Stop || !m_Lines.IsEmpty)
                    {
                        if (m_Lines.TryDequeue(out string line)) writer.WriteLine(line);
                        else m_Available.WaitOne(100);
                    }
                }
            }
            catch (Exception exception) { Debug.LogException(exception); }
        }

        private void OnDestroy()
        {
            m_Stop = true;
            m_Available.Set();
            if (m_Thread != null && m_Thread.IsAlive) m_Thread.Join(1500);
            m_Available.Dispose();
        }
    }
}
