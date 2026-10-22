using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using UnityEngine;

namespace BCIIntelligentRobot.Kitchen
{
    // Read-only transport. No input, physics, task execution or animation authority.
    public sealed class KitchenPlatformView : MonoBehaviour
    {
        public string ServerHost = "127.0.0.1";
        public int WorldPort = 11032;
        public KitchenSceneMirror Mirror;
        public bool ConnectOnStart = true;
        public string ConnectionState { get; private set; } = "DISCONNECTED";
        public long AppliedFrames { get; private set; }
        Thread worker;
        volatile bool stopping;
        TcpClient client;
        readonly ConcurrentQueue<string> incoming = new ConcurrentQueue<string>();
        readonly ConcurrentQueue<string> faults = new ConcurrentQueue<string>();

        void Start()
        {
            if (Mirror == null) Mirror = FindFirstObjectByType<KitchenSceneMirror>();
            if (ConnectOnStart) Connect();
        }
        public void Connect()
        {
            if (worker != null || string.IsNullOrWhiteSpace(ServerHost)) return;
            stopping = false;
            worker = new Thread(Read) { IsBackground = true, Name = "KitchenReadOnlyWorld" };
            worker.Start();
        }
        void Read()
        {
            while (!stopping)
            {
                try
                {
                    client = new TcpClient();
                    var pending = client.ConnectAsync(ServerHost, WorldPort);
                    if (!pending.Wait(3000)) throw new TimeoutException("World connection timeout");
                    pending.GetAwaiter().GetResult();
                    client.ReceiveTimeout = 3000;
                    using (var reader = new StreamReader(client.GetStream()))
                    {
                        while (!stopping)
                        {
                            string line = reader.ReadLine();
                            if (line == null) throw new EndOfStreamException("World stream closed");
                            if (line.Length > 8*1024*1024) throw new InvalidDataException("World frame too large");
                            incoming.Enqueue(line);
                            while (incoming.Count > 2) incoming.TryDequeue(out _);
                        }
                    }
                }
                catch (Exception error) { if (!stopping) faults.Enqueue(error.GetType().Name + ": " + error.Message); }
                finally { client?.Close(); client = null; }
                for (int i=0; i<10 && !stopping; i++) Thread.Sleep(100);
            }
        }
        void Update()
        {
            while (faults.TryDequeue(out string error)) { ConnectionState = "DISCONNECTED"; Debug.LogWarning("KITCHEN_VIEW " + error); }
            string latest = null;
            while (incoming.TryDequeue(out string line)) latest = line;
            if (latest == null) return;
            try
            {
                Mirror.Apply(JsonUtility.FromJson<KitchenWorld>(latest));
                AppliedFrames++;
                ConnectionState = "STREAMING";
            }
            catch (Exception error) { ConnectionState = "IDENTITY_OR_FRAME_REJECTED"; Debug.LogError("KITCHEN_VIEW " + error.Message); }
        }
        void OnDestroy()
        {
            stopping = true;
            client?.Close();
            worker?.Join(4000);
        }
    }
}
