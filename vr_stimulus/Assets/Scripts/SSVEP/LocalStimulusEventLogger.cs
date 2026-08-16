using System;
using System.Collections.Concurrent;
using System.IO;
using System.Text;
using System.Threading;
using UnityEngine;

namespace BCIIntelligentRobot.VRStimulus
{
    [DefaultExecutionOrder(-200)]
    [DisallowMultipleComponent]
    public sealed class LocalStimulusEventLogger : MonoBehaviour
    {
        private const string LogDirectoryName = "M5TimingLogs";

        private readonly ConcurrentQueue<string> m_PendingLines = new ConcurrentQueue<string>();
        private readonly AutoResetEvent m_LineAvailable = new AutoResetEvent(false);
        private Thread m_WriterThread;
        private volatile bool m_StopRequested;
        private string m_LogPath;
        private volatile string m_WriterError;
        private bool m_IsInitialized;

        public string LogPath => m_LogPath;
        public bool IsInitialized => m_IsInitialized;

        public bool BeginSession(string sessionId)
        {
            if (m_IsInitialized)
                return true;

            if (string.IsNullOrWhiteSpace(sessionId))
            {
                Debug.LogError("Local stimulus event logger requires a non-empty session ID.", this);
                return false;
            }

            try
            {
                string directory = Path.Combine(Application.persistentDataPath, LogDirectoryName);
                Directory.CreateDirectory(directory);
                m_LogPath = Path.Combine(directory, $"stimulus-events-{sessionId}.jsonl");
                m_StopRequested = false;
                m_WriterThread = new Thread(WriterLoop)
                {
                    IsBackground = true,
                    Name = "M5StimulusEventWriter"
                };
                m_WriterThread.Start();
                m_IsInitialized = true;
                Debug.Log($"M5 local stimulus event log path={m_LogPath}", this);
                return true;
            }
            catch (Exception exception)
            {
                Debug.LogError($"Failed to initialize M5 local stimulus event log: {exception}", this);
                return false;
            }
        }

        public bool Record(StimulusEventRecord eventRecord)
        {
            if (!m_IsInitialized || eventRecord == null)
                return false;

            string jsonLine = JsonUtility.ToJson(eventRecord, false);
            m_PendingLines.Enqueue(jsonLine);
            m_LineAvailable.Set();
            return true;
        }

        private void Update()
        {
            if (string.IsNullOrEmpty(m_WriterError))
                return;

            Debug.LogError($"M5 local stimulus event writer stopped: {m_WriterError}", this);
            m_WriterError = null;
        }

        private void WriterLoop()
        {
            try
            {
                using (var writer = new StreamWriter(m_LogPath, true, new UTF8Encoding(false)))
                {
                    writer.AutoFlush = true;
                    while (!m_StopRequested || !m_PendingLines.IsEmpty)
                    {
                        if (m_PendingLines.TryDequeue(out string line))
                        {
                            writer.WriteLine(line);
                            continue;
                        }

                        m_LineAvailable.WaitOne(100);
                    }
                }
            }
            catch (Exception exception)
            {
                m_WriterError = exception.ToString();
            }
        }

        private void OnDestroy()
        {
            StopWriter();
            m_LineAvailable.Dispose();
        }

        private void StopWriter()
        {
            if (!m_IsInitialized)
                return;

            m_StopRequested = true;
            m_LineAvailable.Set();
            if (m_WriterThread != null && m_WriterThread.IsAlive)
                m_WriterThread.Join(2000);

            m_IsInitialized = false;
        }
    }
}
