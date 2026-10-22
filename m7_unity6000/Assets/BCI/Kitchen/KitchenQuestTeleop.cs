using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using UnityEngine;

namespace BCIIntelligentRobot.Kitchen
{
    [Serializable] public sealed class KitchenQuestHand
    {
        public float[] position;
        public float[] quaternion_wxyz;
        public bool tracked;
        public bool clutch;
        public float trigger;
    }
    [Serializable] public sealed class KitchenQuestHands
    {
        public KitchenQuestHand left;
        public KitchenQuestHand right;
    }
    [Serializable] public sealed class KitchenQuestInputMessage
    {
        public string type = "input";
        public int schema = 1;
        public string frame = "mujoco_world";
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public long clientMonotonicNs;
        public KitchenQuestHands hands;
    }
    [Serializable] sealed class KitchenQuestEnvelope
    {
        public string type;
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public string epoch;
    }
    [Serializable] sealed class KitchenQuestPing
    {
        public string type;
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public string nonce;
        public long pc_send_ns;
    }
    [Serializable] sealed class KitchenQuestHello
    {
        public string type;
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public string epoch;
        public bool requires_rearm;
        public int input_hz;
        public int control_hz;
        public int world_hz;
    }
    [Serializable] sealed class KitchenQuestPong
    {
        public string type = "pong";
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public string nonce;
        public long clientReceiveNs;
        public long clientSendNs;
    }
    [Serializable] sealed class KitchenQuestControl
    {
        public string type;
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public string epoch;
        public long command_seq;
        public string episode_id;
        public string task_id;
        public string node_id;
    }
    [Serializable] sealed class KitchenQuestAck
    {
        public string type;
        public string runtimeSession;
        public string sceneEpoch;
        public string sceneHash;
        public long sequence;
        public long simTick;
        public string epoch;
        public long command_seq;
        public string status;
        public string detail;
        public string episode_id;
        public string recording_status;
        public string[] task_occurrence_node_ids;
    }

    /// <summary>Quest Touch pose ingress. MuJoCo remains the only motion/contact authority.</summary>
    [DefaultExecutionOrder(200)]
    public sealed class KitchenQuestTeleop : MonoBehaviour
    {
        [Header("PC MuJoCo server")]
        [SerializeField] string serverHost = "";
        [SerializeField] int controlPort = 11021;
        [SerializeField] int worldPort = 11022;
        [SerializeField] OVRCameraRig cameraRig;
        [SerializeField, Range(10, 50)] int inputRateHz = 50;
        [Header("Optional V1.1 task annotations")]
        [SerializeField] string taskId = "";
        [Tooltip("Occurrence IDs from the frozen V1.1 task graph, in operator-selectable order.")]
        [SerializeField] string[] taskOccurrenceNodeIds = Array.Empty<string>();

        const int MaxLineBytes = 65536;
        const int MaxQueuedMessages = 256;
        readonly object queueLock = new object();
        readonly object clientLock = new object();
        readonly object worldLock = new object();
        readonly Queue<string> outgoing = new Queue<string>();
        TcpClient activeControlClient;
        TcpClient activeWorldClient;
        Thread worker;
        volatile bool stopping;
        volatile bool connected;
        volatile bool clockReady;
        volatile bool helloReady;
        volatile bool worldReady;
        volatile bool recenterAwaiting;
        volatile string epoch = "";
        volatile string retiredEpoch = "";
        volatile string runtimeSession = "";
        volatile string sceneHash = "";
        volatile string connectionStatus = "NOT_STARTED";
        volatile string recordingStatus = "IDLE";
        volatile string currentEpisodeId = "";
        KitchenWorld pendingWorld;
        long sequence;
        long controlSequence;
        long simTick;
        long worldSequence = -1;
        long pendingTaskCommandSequence = -1;
        string pendingTaskNodeId = "";
        int taskOccurrenceIndex;
        readonly HashSet<string> activeTaskNodes = new HashSet<string>();
        int recenterPending;
        float nextInputTime;
        bool trackingStopSent;

        public string ServerHost { get { return serverHost; } set { serverHost = value; } }
        public int ControlPort { get { return controlPort; } set { controlPort = value; } }
        public int WorldPort { get { return worldPort; } set { worldPort = value; } }
        public string ConnectionStatus { get { return connectionStatus; } }
        public string RecordingStatus { get { return recordingStatus; } }

        void Start()
        {
            if (cameraRig == null) cameraRig = FindFirstObjectByType<OVRCameraRig>();
            if (cameraRig == null)
            {
                connectionStatus = "XR_RIG_MISSING";
                UnityEngine.Debug.LogError("KITCHEN_QUEST XR rig is missing; no pose input will be sent.");
                enabled = false;
                return;
            }
            if (string.IsNullOrWhiteSpace(serverHost))
            {
                connectionStatus = "PC_HOST_NOT_CONFIGURED";
                UnityEngine.Debug.LogWarning("KITCHEN_QUEST set the PC MuJoCo server IPv4 address before Quest build.");
                return;
            }
            KitchenSceneMirror mirror = FindFirstObjectByType<KitchenSceneMirror>();
            if (mirror == null || mirror.Manifest == null || string.IsNullOrWhiteSpace(mirror.Manifest.model_source_sha256))
            {
                connectionStatus = "KITCHEN_SCENE_MODEL_HASH_MISSING";
                UnityEngine.Debug.LogError("KITCHEN_QUEST requires the exported MuJoCo model hash before connecting.");
                return;
            }
            string manifestLayout = string.IsNullOrWhiteSpace(mirror.Manifest.layout_id)
                ? "canonical" : mirror.Manifest.layout_id;
            if (!string.IsNullOrWhiteSpace(mirror.Manifest.model_scene_sha256))
                sceneHash = mirror.Manifest.model_scene_sha256;
            else if (manifestLayout == "canonical")
                sceneHash = mirror.Manifest.model_source_sha256; // Legacy canonical resource bundle.
            else
            {
                connectionStatus = "KITCHEN_SCENE_IDENTITY_INCOMPLETE";
                UnityEngine.Debug.LogError("KITCHEN_QUEST refuses a variant without its scene identity.");
                return;
            }
            if (OVRManager.display != null) OVRManager.display.RecenteredPose += OnRecenteredPose;
            stopping = false;
            worker = new Thread(NetworkLoop) { IsBackground = true, Name = "KitchenMuJoCoTCP" };
            worker.Start();
        }

        void Update()
        {
            if (cameraRig == null) return;
            if (Interlocked.Exchange(ref recenterPending, 0) != 0)
            {
                recenterAwaiting = true;
                trackingStopSent = true;
                connected = false;
                EnqueueControl("recenter");
                connectionStatus = "RECENTER_STOPPING_WAITING_FOR_NEW_EPOCH";
                return;
            }
            if (recenterAwaiting) return;
            if (OVRInput.GetDown(OVRInput.Button.Start, OVRInput.Controller.LTouch))
                EnqueueControl("stop");
            if (!connected) return;
            if (OVRInput.GetDown(OVRInput.Button.Two, OVRInput.Controller.RTouch))
            {
                if (recordingStatus == "RECORDING") EnqueueControl("record_stop");
                else
                {
                    taskOccurrenceIndex = 0;
                    activeTaskNodes.Clear();
                    EnqueueControl("record_start");
                }
            }
            if (OVRInput.GetDown(OVRInput.Button.One, OVRInput.Controller.LTouch))
                EnqueueControl("review_success");
            if (OVRInput.GetDown(OVRInput.Button.Two, OVRInput.Controller.LTouch))
                EnqueueControl("review_failure");
            if (recordingStatus == "RECORDING")
            {
                if (OVRInput.GetDown(OVRInput.Button.PrimaryThumbstick, OVRInput.Controller.LTouch))
                    CycleTaskOccurrence();
                if (OVRInput.GetDown(OVRInput.Button.PrimaryThumbstick, OVRInput.Controller.RTouch))
                    ToggleTaskOccurrence();
            }
            else
            {
                if (OVRInput.GetDown(OVRInput.Button.PrimaryThumbstick, OVRInput.Controller.LTouch))
                    EnqueueControl("keep");
                if (OVRInput.GetDown(OVRInput.Button.PrimaryThumbstick, OVRInput.Controller.RTouch))
                    EnqueueControl("discard");
            }
            if (!clockReady) return;

            bool leftTracked = OVRInput.GetControllerPositionTracked(OVRInput.Controller.LTouch)
                               && OVRInput.GetControllerPositionValid(OVRInput.Controller.LTouch);
            bool rightTracked = OVRInput.GetControllerPositionTracked(OVRInput.Controller.RTouch)
                                && OVRInput.GetControllerPositionValid(OVRInput.Controller.RTouch);
            if (!leftTracked || !rightTracked || cameraRig.leftHandAnchor == null || cameraRig.rightHandAnchor == null)
            {
                if (!trackingStopSent) EnqueueControl("stop");
                trackingStopSent = true;
                return;
            }
            trackingStopSent = false;

            float period = 1f / Mathf.Clamp(inputRateHz, 10, 50);
            if (Time.unscaledTime < nextInputTime) return;
            nextInputTime = Time.unscaledTime + period;
            bool leftClutch = OVRInput.Get(OVRInput.Button.PrimaryHandTrigger, OVRInput.Controller.LTouch);
            bool rightClutch = OVRInput.Get(OVRInput.Button.PrimaryHandTrigger, OVRInput.Controller.RTouch);
            var message = new KitchenQuestInputMessage
            {
                runtimeSession = runtimeSession,
                sceneEpoch = epoch,
                sceneHash = sceneHash,
                sequence = Interlocked.Increment(ref sequence) - 1,
                simTick = Interlocked.Read(ref simTick),
                clientMonotonicNs = MonotonicNanoseconds(),
                hands = new KitchenQuestHands
                {
                    left = MakeHand(cameraRig.leftHandAnchor, OVRInput.Controller.LTouch, leftClutch),
                    right = MakeHand(cameraRig.rightHandAnchor, OVRInput.Controller.RTouch, rightClutch)
                }
            };
            Enqueue(JsonUtility.ToJson(message));

            // Explicit two-step safety: hold either grip to establish fresh anchors, then press A to arm.
            if ((leftClutch || rightClutch) && OVRInput.GetDown(OVRInput.Button.One, OVRInput.Controller.RTouch))
                EnqueueControl("rearm");
        }

        void LateUpdate()
        {
            KitchenWorld world = null;
            lock (worldLock)
            {
                world = pendingWorld;
                pendingWorld = null;
            }
            if (world == null) return;
            KitchenSceneMirror mirror = FindFirstObjectByType<KitchenSceneMirror>();
            if (mirror != null) mirror.Apply(world);
        }

        static long MonotonicNanoseconds()
        {
            return (long)(Stopwatch.GetTimestamp() * (1_000_000_000.0 / Stopwatch.Frequency));
        }

        static KitchenQuestHand MakeHand(Transform anchor, OVRInput.Controller controller, bool clutch)
        {
            Vector3 p = anchor.position;
            Quaternion q = anchor.rotation;
            // Unity C=(x,z,y) basis: convert world position by C and orientation by C R C^T.
            return new KitchenQuestHand
            {
                position = new[] { p.x, p.z, p.y },
                quaternion_wxyz = new[] { q.w, -q.x, -q.z, -q.y },
                tracked = true,
                clutch = clutch,
                trigger = Mathf.Clamp01(OVRInput.Get(OVRInput.Axis1D.PrimaryIndexTrigger, controller))
            };
        }

        void EnqueueControl(string kind, string nodeId = null)
        {
            long commandSequence = Interlocked.Increment(ref controlSequence) - 1;
            Enqueue(JsonUtility.ToJson(new KitchenQuestControl { type = kind,
                runtimeSession = runtimeSession, sceneEpoch = epoch, sceneHash = sceneHash,
                sequence = commandSequence, simTick = Interlocked.Read(ref simTick),
                epoch = epoch, command_seq = commandSequence,
                episode_id = currentEpisodeId,
                task_id = kind == "record_start" && !string.IsNullOrWhiteSpace(taskId) ? taskId : null,
                node_id = nodeId }));
            if (kind == "task_segment_start" || kind == "task_segment_end")
            {
                pendingTaskCommandSequence = commandSequence;
                pendingTaskNodeId = nodeId ?? "";
            }
        }

        void CycleTaskOccurrence()
        {
            if (taskOccurrenceNodeIds == null || taskOccurrenceNodeIds.Length == 0)
            {
                connectionStatus = "TASK_OCCURRENCE_LIST_NOT_CONFIGURED";
                return;
            }
            taskOccurrenceIndex = (taskOccurrenceIndex + 1) % taskOccurrenceNodeIds.Length;
            connectionStatus = "TASK_OCCURRENCE_SELECTED_" + taskOccurrenceNodeIds[taskOccurrenceIndex];
        }

        void ToggleTaskOccurrence()
        {
            if (string.IsNullOrWhiteSpace(currentEpisodeId) || taskOccurrenceNodeIds == null ||
                taskOccurrenceNodeIds.Length == 0 || pendingTaskCommandSequence >= 0)
            {
                connectionStatus = "TASK_OCCURRENCE_NOT_READY";
                return;
            }
            string nodeId = taskOccurrenceNodeIds[taskOccurrenceIndex];
            string kind = activeTaskNodes.Contains(nodeId) ? "task_segment_end" : "task_segment_start";
            EnqueueControl(kind, nodeId);
        }

        void Enqueue(string json)
        {
            lock (queueLock)
            {
                if (outgoing.Count >= MaxQueuedMessages)
                {
                    connectionStatus = "OUTBOUND_BACKPRESSURE";
                    return;
                }
                outgoing.Enqueue(json + "\n");
            }
        }

        string Dequeue()
        {
            lock (queueLock) return outgoing.Count == 0 ? null : outgoing.Dequeue();
        }

        void NetworkLoop()
        {
            while (!stopping)
            {
                TcpClient controlClient = new TcpClient { NoDelay = true, ReceiveTimeout = 100, SendTimeout = 500 };
                TcpClient worldClient = new TcpClient { NoDelay = true, ReceiveTimeout = 100, SendTimeout = 500 };
                try
                {
                    connectionStatus = "CONNECTING_CONTROL_11021";
                    Connect(controlClient, serverHost, controlPort);
                    connectionStatus = "CONNECTING_WORLD_11022";
                    Connect(worldClient, serverHost, worldPort);
                    lock (clientLock) { activeControlClient = controlClient; activeWorldClient = worldClient; }
                    lock (queueLock) outgoing.Clear();
                    epoch = "";
                    runtimeSession = "";
                    worldSequence = -1;
                    Interlocked.Exchange(ref simTick, 0);
                    clockReady = false;
                    connected = false;
                    helloReady = false;
                    worldReady = false;
                    Interlocked.Exchange(ref sequence, 0);
                    Interlocked.Exchange(ref controlSequence, 0);
                    currentEpisodeId = "";
                    recordingStatus = "IDLE";
                    using (NetworkStream controlStream = controlClient.GetStream())
                    using (NetworkStream worldStream = worldClient.GetStream())
                    {
                        var pendingControl = new StringBuilder();
                        var pendingWorld = new StringBuilder();
                        byte[] controlBuffer = new byte[16384];
                        byte[] worldBuffer = new byte[65536];
                        connectionStatus = "TCP_CONNECTED_WAITING_FOR_SESSION_AND_WORLD";
                        while (!stopping)
                        {
                            string line;
                            while ((line = Dequeue()) != null)
                            {
                                byte[] bytes = Encoding.UTF8.GetBytes(line);
                                controlStream.Write(bytes, 0, bytes.Length);
                            }
                            if (controlStream.DataAvailable)
                            {
                                int count = controlStream.Read(controlBuffer, 0, controlBuffer.Length);
                                if (count <= 0) break;
                                pendingControl.Append(Encoding.UTF8.GetString(controlBuffer, 0, count));
                                ReadLines(pendingControl, json => HandleControlMessage(json, controlStream));
                            }
                            if (worldStream.DataAvailable)
                            {
                                int count = worldStream.Read(worldBuffer, 0, worldBuffer.Length);
                                if (count <= 0) break;
                                pendingWorld.Append(Encoding.UTF8.GetString(worldBuffer, 0, count));
                                ReadLines(pendingWorld, HandleWorldMessage);
                            }
                            if (Disconnected(controlClient) || Disconnected(worldClient)) break;
                            if (helloReady && worldReady && !recenterAwaiting) connected = true;
                            Thread.Sleep(2);
                        }
                    }
                    connectionStatus = "DISCONNECTED";
                }
                catch (Exception error)
                {
                    if (!stopping) connectionStatus = "RECONNECTING_" + error.GetType().Name;
                }
                finally
                {
                    connected = false;
                    clockReady = false;
                    epoch = "";
                    helloReady = false;
                    worldReady = false;
                    lock (clientLock)
                    {
                        if (activeControlClient == controlClient) activeControlClient = null;
                        if (activeWorldClient == worldClient) activeWorldClient = null;
                    }
                    controlClient.Close();
                    worldClient.Close();
                }
                if (!stopping) Thread.Sleep(500);
            }
            connectionStatus = "STOPPED";
        }

        static int IndexOfNewline(StringBuilder value)
        {
            for (int i = 0; i < value.Length; i++) if (value[i] == '\n') return i;
            return -1;
        }

        static void Connect(TcpClient client, string host, int port)
        {
            IAsyncResult connect = client.BeginConnect(host, port, null, null);
            WaitHandle wait = connect.AsyncWaitHandle;
            bool ready = wait.WaitOne(1500);
            wait.Close();
            if (!ready) throw new TimeoutException("CONNECT_TIMEOUT_" + port);
            client.EndConnect(connect);
        }

        static bool Disconnected(TcpClient client)
        {
            return client.Client.Poll(0, SelectMode.SelectRead) && client.Client.Available == 0;
        }

        static void ReadLines(StringBuilder pending, Action<string> consume)
        {
            if (Encoding.UTF8.GetByteCount(pending.ToString()) > MaxLineBytes * 2)
                throw new InvalidOperationException("INBOUND_BUFFER_LIMIT");
            int newline;
            while ((newline = IndexOfNewline(pending)) >= 0)
            {
                if (Encoding.UTF8.GetByteCount(pending.ToString(0, newline)) > MaxLineBytes)
                    throw new InvalidOperationException("INBOUND_LINE_LIMIT");
                string line = pending.ToString(0, newline);
                pending.Remove(0, newline + 1);
                consume(line);
            }
        }

        void ValidateIdentity(KitchenQuestEnvelope envelope, bool allowNewEpoch = false)
        {
            if (envelope == null || string.IsNullOrEmpty(envelope.runtimeSession) ||
                string.IsNullOrEmpty(envelope.sceneEpoch) || string.IsNullOrEmpty(envelope.sceneHash) ||
                envelope.sequence < 0 || envelope.simTick < 0)
                throw new InvalidOperationException("INCOMPLETE_RUNTIME_CONTRACT");
            if (envelope.runtimeSession != runtimeSession || envelope.sceneHash != sceneHash)
                throw new InvalidOperationException("RUNTIME_SESSION_OR_SCENE_HASH_MISMATCH");
            if (!allowNewEpoch && envelope.sceneEpoch != epoch)
                throw new InvalidOperationException("SCENE_EPOCH_MISMATCH");
        }

        void HandleControlMessage(string json, NetworkStream stream)
        {
            KitchenQuestEnvelope envelope = JsonUtility.FromJson<KitchenQuestEnvelope>(json);
            if (envelope == null) throw new InvalidOperationException("INVALID_SERVER_JSON");
            if (envelope.type == "hello")
            {
                KitchenQuestHello hello = JsonUtility.FromJson<KitchenQuestHello>(json);
                if (string.IsNullOrEmpty(hello.runtimeSession) || string.IsNullOrEmpty(hello.sceneEpoch) ||
                    hello.sceneHash != sceneHash || hello.sequence < 0 || hello.simTick < 0)
                    throw new InvalidOperationException("HELLO_RUNTIME_CONTRACT_OR_SCENE_HASH_MISMATCH");
                runtimeSession = hello.runtimeSession;
                epoch = hello.sceneEpoch;
                Interlocked.Exchange(ref simTick, hello.simTick);
                worldSequence = -1;
                Interlocked.Exchange(ref controlSequence, 0);
                currentEpisodeId = "";
                recordingStatus = "IDLE";
                helloReady = true;
                connectionStatus = "SESSION_READY_WAITING_FOR_WORLD_FRAME";
            }
            else if (envelope.type == "ping")
            {
                if (envelope.runtimeSession != runtimeSession || envelope.sceneHash != sceneHash)
                    throw new InvalidOperationException("PING_SESSION_OR_SCENE_HASH_MISMATCH");
                if (envelope.sceneEpoch != epoch) return;
                ValidateIdentity(envelope);
                KitchenQuestPing ping = JsonUtility.FromJson<KitchenQuestPing>(json);
                long received = MonotonicNanoseconds();
                var pong = new KitchenQuestPong
                {
                    runtimeSession = runtimeSession,
                    sceneEpoch = epoch,
                    sceneHash = sceneHash,
                    sequence = ping.sequence,
                    simTick = ping.simTick,
                    nonce = ping.nonce,
                    clientReceiveNs = received,
                    clientSendNs = MonotonicNanoseconds()
                };
                byte[] bytes = Encoding.UTF8.GetBytes(JsonUtility.ToJson(pong) + "\n");
                stream.Write(bytes, 0, bytes.Length);
                clockReady = true;
                connectionStatus = worldReady ? "CONNECTED_CLOCK_SYNCED" : "CLOCK_SYNCED_WAITING_FOR_WORLD";
            }
            else if (envelope.type == "ack")
            {
                if (envelope.runtimeSession != runtimeSession || envelope.sceneHash != sceneHash)
                    throw new InvalidOperationException("ACK_SESSION_OR_SCENE_HASH_MISMATCH");
                if (envelope.sceneEpoch != epoch) return;
                ValidateIdentity(envelope);
                KitchenQuestAck ack = JsonUtility.FromJson<KitchenQuestAck>(json);
                if (!string.IsNullOrEmpty(ack.recording_status)) recordingStatus = ack.recording_status;
                if (!string.IsNullOrEmpty(ack.episode_id)) currentEpisodeId = ack.episode_id;
                if (ack.task_occurrence_node_ids != null)
                    taskOccurrenceNodeIds = ack.task_occurrence_node_ids;
                if (ack.status == "RECORDING_STARTED")
                {
                    taskOccurrenceIndex = 0;
                    activeTaskNodes.Clear();
                }
                if (ack.command_seq == pendingTaskCommandSequence && pendingTaskCommandSequence >= 0)
                {
                    if (ack.status == "TASK_SEGMENT_STARTED") activeTaskNodes.Add(pendingTaskNodeId);
                    else if (ack.status == "TASK_SEGMENT_ENDED") activeTaskNodes.Remove(pendingTaskNodeId);
                    pendingTaskCommandSequence = -1;
                    pendingTaskNodeId = "";
                }
                connectionStatus = ack.status == "REJECTED"
                    ? "COMMAND_REJECTED_" + ack.detail : "CONNECTED_" + ack.status;
            }
            else if (envelope.type == "epoch")
            {
                ValidateIdentity(envelope, allowNewEpoch: true);
                retiredEpoch = epoch;
                epoch = envelope.sceneEpoch;
                Interlocked.Exchange(ref simTick, envelope.simTick);
                clockReady = false;
                connected = false;
                worldReady = false;
                worldSequence = -1;
                recenterAwaiting = false;
                lock (queueLock) outgoing.Clear();
                Interlocked.Exchange(ref sequence, 0);
                Interlocked.Exchange(ref controlSequence, 0);
                currentEpisodeId = "";
                connectionStatus = "RECENTER_REARM_REQUIRED";
            }
            else if (envelope.type == "error")
            {
                throw new InvalidOperationException("SERVER_REJECTED_INPUT: " + json);
            }
        }

        void HandleWorldMessage(string json)
        {
            KitchenWorld world = JsonUtility.FromJson<KitchenWorld>(json);
            if (world == null || world.type != "world") throw new InvalidOperationException("INVALID_WORLD_FRAME");
            if (string.IsNullOrEmpty(runtimeSession)) return;
            if (world.runtimeSession != runtimeSession || world.sceneHash != sceneHash)
                throw new InvalidOperationException("WORLD_SESSION_OR_SCENE_HASH_MISMATCH");
            if (world.sceneEpoch != epoch)
            {
                if (world.sceneEpoch == retiredEpoch) return;
                if (recenterAwaiting) return;
                throw new InvalidOperationException("WORLD_SCENE_EPOCH_MISMATCH");
            }
            if (world.sequence <= worldSequence) return;
            if (world.simTick < Interlocked.Read(ref simTick))
                throw new InvalidOperationException("WORLD_SIM_TICK_REGRESSION");
            worldSequence = world.sequence;
            Interlocked.Exchange(ref simTick, world.simTick);
            if (!string.IsNullOrEmpty(world.recording_status)) recordingStatus = world.recording_status;
            if (!string.IsNullOrEmpty(world.episode_id)) currentEpisodeId = world.episode_id;
            lock (worldLock) pendingWorld = world;
            worldReady = true;
        }

        void OnRecenteredPose()
        {
            Interlocked.Exchange(ref recenterPending, 1);
        }

        void OnApplicationPause(bool paused)
        {
            if (paused && connected) EnqueueControl("stop");
        }

        void OnDestroy()
        {
            stopping = true;
            if (OVRManager.display != null) OVRManager.display.RecenteredPose -= OnRecenteredPose;
            TcpClient controlClient, worldClient;
            lock (clientLock) { controlClient = activeControlClient; worldClient = activeWorldClient; }
            if (controlClient != null) controlClient.Close();
            if (worldClient != null) worldClient.Close();
            if (worker != null && worker.IsAlive) worker.Join(500);
        }
    }
}

