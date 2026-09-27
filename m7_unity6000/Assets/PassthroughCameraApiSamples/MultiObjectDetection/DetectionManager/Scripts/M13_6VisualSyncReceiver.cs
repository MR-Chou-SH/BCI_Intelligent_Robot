using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.VirtualManipulation;
using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    public enum M13_6VisualTransportMode
    {
        Auto,
        Udp,
        Tcp
    }

    [Serializable]
    public sealed class M13_6BlockWorldState
    {
        public string logicalBlockId;
        public float[] positionMujoco;
        public float[] quaternionMujocoWxyz;
    }

    [Serializable]
    public sealed class M13_6RobotWorldStateFrame
    {
        public int protocolVersion;
        public string messageType;
        public string streamSource;
        public string mappingVersion;
        public long sequence;
        public double simulationTimestampSeconds;
        public double sentTimestampUnixSeconds;
        public string simulationState;
        public float[] jointPositionsRadians;
        public float leftFingerPositionMeters;
        public float rightFingerPositionMeters;
        public float gripperOpeningMeters;
        public float[] robotBasePositionMujoco;
        public float[] robotBaseQuaternionMujocoWxyz;
        public float[] visualBlockSizeQuestMeters;
        public M13_6BlockWorldState[] blocks;
    }

    /// <summary>
    /// Receives the independent M13.6 visual latest-state stream over UDP,
    /// TCP, or both in Auto mode. It never touches the reliable M8 selection
    /// transport or changes target identity.
    /// All Unity Transform writes happen on the main thread in Update.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class M13_6VisualSyncReceiver : MonoBehaviour
    {
        public const int DefaultPort = 11002;
        private const int ProtocolVersion = 1;
        private const string MessageType = "m13_6_robot_world_state";
        private const string BuildIdentity = "m13.6-diagnostics-20260916";
        private const float DefaultScale = 0.70f;
        private const float TableTopMujocoZ = 0.75f;
        private const float InterpolationDurationSeconds = 1f / 30f;

        [SerializeField, Min(1)] private int m_listenPort = DefaultPort;
        [SerializeField] private M13_6VisualTransportMode m_transportMode = M13_6VisualTransportMode.Auto;
        [SerializeField] private bool m_startOnEnable = true;
        [SerializeField] private bool m_showDebugOverlay;
        [SerializeField, Min(0.01f)] private float m_worldScale = DefaultScale;
        [SerializeField] private float m_tableTopMujocoZ = TableTopMujocoZ;
        [SerializeField] private Vector3 m_worldOriginLocal;

        private readonly object m_pendingPacketLock = new object();
        private byte[] m_latestPendingPacket;
        private M13_6VisualTransportMode m_latestPendingTransport;
        private readonly Dictionary<string, M9VirtualBlockTarget> m_targetsByLogicalId =
            new Dictionary<string, M9VirtualBlockTarget>(StringComparer.Ordinal);
        private UdpClient m_udpClient;
        private Thread m_receiveThread;
        private TcpListener m_tcpListener;
        private Thread m_tcpAcceptThread;
        private TcpClient m_activeTcpClient;
        private volatile bool m_receiveRunning;
        private Transform m_visualWorldRoot;
        private Transform m_tableRoot;
        private Transform m_robotRoot;
        private M9FrankaVisualRig m_robotRig;
        private BciTargetBatchController m_batchController;
        private Vector3 m_robotAnchorInitialLocalPosition;
        private Quaternion m_robotAnchorInitialLocalRotation;
        private bool m_robotAnchorCaptured;
        private M13_6RobotWorldStateFrame m_previousFrame;
        private M13_6RobotWorldStateFrame m_currentFrame;
        private float m_currentFrameArrivalTime;
        private long m_lastAcceptedSequence = -1;
        private long m_lastAppliedSequence = -1;
        private int m_receivedPacketCount;
        private int m_acceptedFrameCount;
        private int m_staleFrameCount;
        private int m_duplicateFrameCount;
        private int m_malformedPacketCount;
        private int m_latestReplacementCount;
        private int m_appliedFrameCount;
        private bool m_loggedFirstAcceptedFrame;
        private bool m_loggedFirstTcpPacket;
        private bool m_loggedBindingStatus;
        private float m_lastPacketArrivalTime = -1f;
        private float m_interarrivalSeconds;
        private float m_nextDiagnosticLogTime;

        public bool IsReceiving => m_receiveRunning;
        public M13_6VisualTransportMode TransportMode => m_transportMode;
        public long LastAcceptedSequence => m_lastAcceptedSequence;
        public int ReceivedPacketCount => m_receivedPacketCount;
        public int AcceptedFrameCount => m_acceptedFrameCount;
        public int StaleFrameCount => m_staleFrameCount;
        public int DuplicateFrameCount => m_duplicateFrameCount;
        public int MalformedPacketCount => m_malformedPacketCount;
        public float ReceiveRateHz => m_interarrivalSeconds > 0f ? 1f / m_interarrivalSeconds : 0f;

        private void OnEnable()
        {
            m_nextDiagnosticLogTime = Time.realtimeSinceStartup + 2f;
            if (m_startOnEnable)
                StartReceiver();
        }

        private void OnDisable()
        {
            StopReceiver();
        }

        private void OnDestroy()
        {
            StopReceiver();
        }

        public void StartReceiver()
        {
            if (m_receiveRunning)
                return;

            try
            {
                if (m_transportMode == M13_6VisualTransportMode.Auto ||
                    m_transportMode == M13_6VisualTransportMode.Udp)
                {
                    m_udpClient = new UdpClient(m_listenPort);
                    m_udpClient.Client.ReceiveTimeout = 250;
                }

                if (m_transportMode == M13_6VisualTransportMode.Auto ||
                    m_transportMode == M13_6VisualTransportMode.Tcp)
                {
                    m_tcpListener = new TcpListener(IPAddress.Any, m_listenPort);
                    m_tcpListener.Start();
                }

                m_receiveRunning = true;
                if (m_udpClient != null)
                {
                    m_receiveThread = new Thread(UdpReceiveLoop)
                    {
                        IsBackground = true,
                        Name = "M13.6VisualSyncUdpReceiver"
                    };
                    m_receiveThread.Start();
                }

                if (m_tcpListener != null)
                {
                    m_tcpAcceptThread = new Thread(TcpAcceptLoop)
                    {
                        IsBackground = true,
                        Name = "M13.6VisualSyncTcpReceiver"
                    };
                    m_tcpAcceptThread.Start();
                }

                Debug.Log("M13_6_VISUAL receiver_started build=" + BuildIdentity +
                    " protocol=" + ProtocolVersion + " port=" + m_listenPort +
                    " transport=" + m_transportMode +
                    " udp=" + (m_udpClient != null) + " tcp=" + (m_tcpListener != null), this);
            }
            catch (Exception error)
            {
                m_receiveRunning = false;
                CloseSocket();
                Debug.LogError("M13_6_VISUAL receiver_start_failed error=" + error.GetType().Name, this);
            }
        }

        public void StopReceiver()
        {
            m_receiveRunning = false;
            CloseSocket();
            if (m_receiveThread != null && m_receiveThread.IsAlive)
                m_receiveThread.Join(400);
            m_receiveThread = null;
            if (m_tcpAcceptThread != null && m_tcpAcceptThread.IsAlive)
                m_tcpAcceptThread.Join(400);
            m_tcpAcceptThread = null;
        }

        private void CloseSocket()
        {
            if (m_udpClient != null)
            {
                try
                {
                    m_udpClient.Close();
                }
                catch (Exception)
                {
                    // Shutdown is best effort; no selection/control state is tied to this socket.
                }
                m_udpClient = null;
            }

            if (m_tcpListener != null)
            {
                try
                {
                    m_tcpListener.Stop();
                }
                catch (Exception)
                {
                    // Shutdown is best effort; no selection/control state is tied to this socket.
                }
                m_tcpListener = null;
            }

            if (m_activeTcpClient != null)
            {
                try
                {
                    m_activeTcpClient.Close();
                }
                catch (Exception)
                {
                    // Shutdown is best effort; the receive loop owns the client lifecycle.
                }
                m_activeTcpClient = null;
            }
        }

        private void UdpReceiveLoop()
        {
            IPEndPoint endpoint = new IPEndPoint(IPAddress.Any, 0);
            while (m_receiveRunning)
            {
                try
                {
                    byte[] payload = m_udpClient.Receive(ref endpoint);
                    if (payload != null && payload.Length > 0)
                        PublishLatestPacket(payload, M13_6VisualTransportMode.Udp);
                }
                catch (SocketException)
                {
                    // ReceiveTimeout lets the loop observe shutdown without blocking forever.
                }
                catch (ObjectDisposedException)
                {
                    break;
                }
                catch (Exception)
                {
                    if (!m_receiveRunning)
                        break;
                }
            }
        }

        private void TcpAcceptLoop()
        {
            while (m_receiveRunning)
            {
                TcpClient client = null;
                try
                {
                    client = m_tcpListener.AcceptTcpClient();
                    client.NoDelay = true;
                    m_activeTcpClient = client;
                    Debug.Log("M13_6_VISUAL tcp_client_connected", this);
                    using (client)
                    using (NetworkStream stream = client.GetStream())
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8, false, 4096, true))
                    {
                        while (m_receiveRunning)
                        {
                            string line = reader.ReadLine();
                            if (line == null)
                                break;
                            if (line.Length > 0)
                                PublishLatestPacket(Encoding.UTF8.GetBytes(line), M13_6VisualTransportMode.Tcp);
                        }
                    }
                    Debug.Log("M13_6_VISUAL tcp_client_disconnected", this);
                }
                catch (SocketException)
                {
                    if (!m_receiveRunning)
                        break;
                }
                catch (IOException)
                {
                    if (!m_receiveRunning)
                        break;
                }
                catch (ObjectDisposedException)
                {
                    break;
                }
                catch (Exception error)
                {
                    if (m_receiveRunning)
                        Debug.LogWarning("M13_6_VISUAL tcp_receive_error=" + error.GetType().Name, this);
                }
                finally
                {
                    if (client != null)
                        client.Close();
                    if (m_activeTcpClient == client)
                        m_activeTcpClient = null;
                }
            }
        }

        private void PublishLatestPacket(byte[] payload, M13_6VisualTransportMode transport)
        {
            lock (m_pendingPacketLock)
            {
                if (m_latestPendingPacket != null)
                    m_latestReplacementCount++;
                m_latestPendingPacket = payload;
                m_latestPendingTransport = transport;
            }
            Interlocked.Increment(ref m_receivedPacketCount);
            if (transport == M13_6VisualTransportMode.Tcp && !m_loggedFirstTcpPacket)
            {
                m_loggedFirstTcpPacket = true;
                Debug.Log("M13_6_VISUAL tcp_packet_received bytes=" + payload.Length, this);
            }
        }

        private bool TryTakeLatestPacket(out byte[] payload, out M13_6VisualTransportMode transport)
        {
            lock (m_pendingPacketLock)
            {
                payload = m_latestPendingPacket;
                transport = m_latestPendingTransport;
                m_latestPendingPacket = null;
                return payload != null;
            }
        }

        private void Update()
        {
            ResolveSceneReferences();
            DrainPackets();
            ApplyInterpolatedFrame();
            EmitDiagnosticSummaryIfDue();
        }

        private void ResolveSceneReferences()
        {
            if (m_visualWorldRoot == null)
            {
                GameObject rootObject = GameObject.Find("M9WorkspaceRoot");
                if (rootObject != null)
                    m_visualWorldRoot = rootObject.transform;
            }

            if (m_visualWorldRoot == null)
                return;

            if (m_batchController == null)
                m_batchController = GetComponent<BciTargetBatchController>();

            if (m_tableRoot == null)
                m_tableRoot = m_visualWorldRoot.Find("Table");

            if (m_robotRoot == null)
            {
                Transform anchor = m_visualWorldRoot.Find("LeftRobotAnchor");
                if (anchor != null)
                    m_robotRoot = FindDescendant(anchor, "FrankaRoot");
                if (m_robotRoot != null)
                {
                    m_robotRig = m_robotRoot.GetComponent<M9FrankaVisualRig>();
                    m_robotAnchorInitialLocalPosition = m_robotRoot.localPosition;
                    // The factory root and all block poses use the same
                    // MuJoCo table-frame basis.  The MuJoCo base quaternion
                    // is the identity in this fixed scene; derive the
                    // resulting Unity orientation through that basis instead
                    // of applying an unrelated presentation yaw.
                    m_robotRoot.localRotation = MujocoRotation(
                        new[] { 1f, 0f, 0f, 0f });
                    m_robotAnchorInitialLocalRotation = m_robotRoot.localRotation;
                    m_robotAnchorCaptured = true;
                }
            }

            if (m_targetsByLogicalId.Count == 4)
            {
                LogBindingStatusIfReady();
                return;
            }

            M9VirtualBlockTarget[] targets = m_visualWorldRoot.GetComponentsInChildren<M9VirtualBlockTarget>(true);
            for (int index = 0; index < targets.Length; index++)
            {
                M9VirtualBlockTarget target = targets[index];
                if (target != null && !string.IsNullOrEmpty(target.LogicalBlockId))
                    m_targetsByLogicalId[target.LogicalBlockId] = target;
            }
            LogBindingStatusIfReady();
        }

        private void LogBindingStatusIfReady()
        {
            if (m_loggedBindingStatus || m_visualWorldRoot == null || m_tableRoot == null ||
                m_robotRoot == null || m_robotRig == null || m_targetsByLogicalId.Count != 4)
                return;

            M9FrankaVisualJoint[] joints = m_robotRoot.GetComponentsInChildren<M9FrankaVisualJoint>(true);
            bool hasLeftFinger = false;
            bool hasRightFinger = false;
            for (int index = 0; index < joints.Length; index++)
            {
                if (joints[index] == null)
                    continue;
                hasLeftFinger |= joints[index].JointId == "umi_left_finger_joint";
                hasRightFinger |= joints[index].JointId == "umi_right_finger_joint";
            }

            m_loggedBindingStatus = true;
            Debug.Log("M13_6_VISUAL scene_bindings world_root=" + m_visualWorldRoot.name +
                " table_root=" + m_tableRoot.name + " robot_root=" + m_robotRoot.name + " joints=" + joints.Length +
                " gripper_left=" + hasLeftFinger + " gripper_right=" + hasRightFinger +
                " blocks=" + m_targetsByLogicalId.Count +
                " robot_mode=unified_table_frame" +
                " robot_frame_calibration=unified_table_transform" +
                " block_mapping=unified_table_transform", this);
        }

        private void DrainPackets()
        {
            byte[] payload;
            M13_6VisualTransportMode transport;
            if (TryTakeLatestPacket(out payload, out transport))
            {
                M13_6RobotWorldStateFrame frame;
                try
                {
                    frame = JsonUtility.FromJson<M13_6RobotWorldStateFrame>(Encoding.UTF8.GetString(payload));
                    ValidateFrame(frame);
                }
                catch (Exception error)
                {
                    m_malformedPacketCount++;
                    if (transport == M13_6VisualTransportMode.Tcp)
                        Debug.LogWarning("M13_6_VISUAL tcp_frame_rejected error=" + error.GetType().Name, this);
                    return;
                }

                if (frame.sequence <= m_lastAcceptedSequence)
                {
                    if (frame.sequence == m_lastAcceptedSequence)
                        m_duplicateFrameCount++;
                    else
                        m_staleFrameCount++;
                    return;
                }

                float now = Time.realtimeSinceStartup;
                if (m_lastPacketArrivalTime >= 0f)
                {
                    float interval = now - m_lastPacketArrivalTime;
                    m_interarrivalSeconds = m_interarrivalSeconds <= 0f
                        ? interval
                        : Mathf.Lerp(m_interarrivalSeconds, interval, 0.15f);
                }
                m_lastPacketArrivalTime = now;
                m_lastAcceptedSequence = frame.sequence;
                m_previousFrame = m_currentFrame;
                m_currentFrame = frame;
                m_currentFrameArrivalTime = now;
                m_acceptedFrameCount++;
                if (!m_loggedFirstAcceptedFrame)
                {
                    m_loggedFirstAcceptedFrame = true;
                    Debug.Log("M13_6_VISUAL frame_accepted transport=" + transport +
                        " sequence=" + frame.sequence + " accepted=" + m_acceptedFrameCount, this);
                }
            }
        }

        private static void ValidateFrame(M13_6RobotWorldStateFrame frame)
        {
            if (frame == null || frame.protocolVersion != ProtocolVersion ||
                frame.messageType != MessageType || frame.streamSource != "mujoco" ||
                frame.jointPositionsRadians == null || frame.jointPositionsRadians.Length != 7 ||
                frame.robotBasePositionMujoco == null || frame.robotBasePositionMujoco.Length != 3 ||
                frame.robotBaseQuaternionMujocoWxyz == null || frame.robotBaseQuaternionMujocoWxyz.Length != 4 ||
                frame.blocks == null || frame.blocks.Length != 4)
                throw new InvalidOperationException("invalid M13.6 frame shape");

            if (frame.visualBlockSizeQuestMeters != null &&
                (frame.visualBlockSizeQuestMeters.Length != 3 ||
                 frame.visualBlockSizeQuestMeters[0] <= 0f ||
                 frame.visualBlockSizeQuestMeters[1] <= 0f ||
                 frame.visualBlockSizeQuestMeters[2] <= 0f ||
                 float.IsNaN(frame.visualBlockSizeQuestMeters[0]) ||
                 float.IsNaN(frame.visualBlockSizeQuestMeters[1]) ||
                 float.IsNaN(frame.visualBlockSizeQuestMeters[2]) ||
                 float.IsInfinity(frame.visualBlockSizeQuestMeters[0]) ||
                 float.IsInfinity(frame.visualBlockSizeQuestMeters[1]) ||
                 float.IsInfinity(frame.visualBlockSizeQuestMeters[2])))
                throw new InvalidOperationException("invalid M13.6 visual block size");

            var seen = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < frame.blocks.Length; index++)
            {
                M13_6BlockWorldState block = frame.blocks[index];
                if (block == null || string.IsNullOrEmpty(block.logicalBlockId) ||
                    block.logicalBlockId.StartsWith("obj_", StringComparison.Ordinal) ||
                    block.positionMujoco == null || block.positionMujoco.Length != 3 ||
                    block.quaternionMujocoWxyz == null || block.quaternionMujocoWxyz.Length != 4 ||
                    !seen.Add(block.logicalBlockId))
                    throw new InvalidOperationException("invalid M13.6 block state");
            }
        }

        private void ApplyInterpolatedFrame()
        {
            if (m_currentFrame == null || m_visualWorldRoot == null)
                return;

            float alpha = m_previousFrame == null
                ? 1f
                : Mathf.Clamp01((Time.realtimeSinceStartup - m_currentFrameArrivalTime) / InterpolationDurationSeconds);
            ApplyFrame(m_previousFrame ?? m_currentFrame, m_currentFrame, alpha);
            if (m_lastAppliedSequence != m_currentFrame.sequence)
            {
                m_lastAppliedSequence = m_currentFrame.sequence;
                m_appliedFrameCount++;
            }
        }

        private void EmitDiagnosticSummaryIfDue()
        {
            float now = Time.realtimeSinceStartup;
            if (now < m_nextDiagnosticLogTime)
                return;

            m_nextDiagnosticLogTime = now + 5f;
            float baseDrift = m_robotAnchorCaptured && m_robotRoot != null
                ? Vector3.Distance(m_robotAnchorInitialLocalPosition, m_robotRoot.localPosition)
                : -1f;
            float baseRotationDrift = m_robotAnchorCaptured && m_robotRoot != null
                ? Quaternion.Angle(m_robotAnchorInitialLocalRotation, m_robotRoot.localRotation)
                : -1f;
            Debug.Log("M13_6_VISUAL diagnostics build=" + BuildIdentity +
                " running=" + m_receiveRunning +
                " udp=" + (m_udpClient != null) + " tcp=" + (m_tcpListener != null) +
                " tcpClient=" + (m_activeTcpClient != null) +
                " received=" + m_receivedPacketCount + " accepted=" + m_acceptedFrameCount +
                " applied=" + m_appliedFrameCount + " stale=" + m_staleFrameCount +
                " duplicate=" + m_duplicateFrameCount + " malformed=" + m_malformedPacketCount +
                " replaced=" + m_latestReplacementCount + " recvHz=" + ReceiveRateHz.ToString("F1") +
                " lastSeq=" + m_lastAcceptedSequence + " bindings=" + m_loggedBindingStatus +
                " robot_mode=unified_table_frame baseDriftM=" + baseDrift.ToString("F4") +
                " baseRotDriftDeg=" + baseRotationDrift.ToString("F2") +
                " robot_frame_calibration=unified_table_transform" +
                " block_mapping=unified_table_transform", this);
        }

        private void ApplyFrame(
            M13_6RobotWorldStateFrame from,
            M13_6RobotWorldStateFrame to,
            float alpha)
        {
            UpdatePresentationForSimulationState(to.simulationState);
            ApplyVisualBlockSize(to.visualBlockSizeQuestMeters);
            if (m_robotRig != null)
            {
                for (int index = 0; index < 7; index++)
                {
                    float value = Mathf.Lerp(from.jointPositionsRadians[index], to.jointPositionsRadians[index], alpha);
                    m_robotRig.TrySetJointValue("fr3_joint" + (index + 1), value);
                }
                m_robotRig.TrySetJointValue("umi_left_finger_joint", Mathf.Lerp(from.leftFingerPositionMeters, to.leftFingerPositionMeters, alpha));
                m_robotRig.TrySetJointValue("umi_right_finger_joint", Mathf.Lerp(from.rightFingerPositionMeters, to.rightFingerPositionMeters, alpha));
            }

            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < to.blocks.Length; index++)
            {
                M13_6BlockWorldState currentBlock = to.blocks[index];
                M13_6BlockWorldState previousBlock = FindBlock(from.blocks, currentBlock.logicalBlockId) ?? currentBlock;
                if (!m_targetsByLogicalId.TryGetValue(currentBlock.logicalBlockId, out M9VirtualBlockTarget target) || target == null)
                    continue;

                Vector3 previousPosition = MujocoPosition(previousBlock.positionMujoco);
                Vector3 currentPosition = MujocoPosition(currentBlock.positionMujoco);
                Quaternion previousRotation = MujocoRotation(previousBlock.quaternionMujocoWxyz);
                Quaternion currentRotation = MujocoRotation(currentBlock.quaternionMujocoWxyz);
                Transform tableFrame = m_tableRoot != null ? m_tableRoot : m_visualWorldRoot;
                target.transform.position = tableFrame.TransformPoint(Vector3.Lerp(previousPosition, currentPosition, alpha));
                target.transform.rotation = tableFrame.rotation * Quaternion.Slerp(previousRotation, currentRotation, alpha);
                targetIds.Add(currentBlock.logicalBlockId);
            }
        }

        private void UpdatePresentationForSimulationState(string simulationState)
        {
            if (m_batchController == null || string.IsNullOrWhiteSpace(simulationState))
                return;

            bool hideSelectionPresentation;
            string robotStatus;
            if (string.Equals(simulationState, "executing", StringComparison.OrdinalIgnoreCase))
            {
                hideSelectionPresentation = true;
                robotStatus = "EXECUTING";
            }
            else if (string.Equals(simulationState, "completed", StringComparison.OrdinalIgnoreCase))
            {
                // Keep the terminal M16 batch locked and its selection stimuli
                // hidden, while making successful completion visible in HUD.
                hideSelectionPresentation = true;
                robotStatus = "COMPLETE";
            }
            else if (string.Equals(simulationState, "execution_failed", StringComparison.OrdinalIgnoreCase))
            {
                hideSelectionPresentation = true;
                robotStatus = "FAILED";
            }
            else if (string.Equals(simulationState, "usb_synthetic", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(simulationState, "selection_open", StringComparison.OrdinalIgnoreCase))
            {
                hideSelectionPresentation = false;
                robotStatus = "READY";
            }
            else
                return;

            m_batchController.SetM13_6ExecutionPresentation(
                hideSelectionPresentation,
                "visual_telemetry_state=" + simulationState,
                robotStatus);
        }

        private void ApplyVisualBlockSize(float[] sizeMeters)
        {
            if (sizeMeters == null || sizeMeters.Length != 3)
                return;

            Vector3 size = new Vector3(sizeMeters[0], sizeMeters[1], sizeMeters[2]);
            if (size.x <= 0f || size.y <= 0f || size.z <= 0f ||
                float.IsNaN(size.x) || float.IsNaN(size.y) || float.IsNaN(size.z) ||
                float.IsInfinity(size.x) || float.IsInfinity(size.y) || float.IsInfinity(size.z))
                return;

            foreach (M9VirtualBlockTarget target in m_targetsByLogicalId.Values)
            {
                if (target != null)
                {
                    target.transform.localScale = size;
                    // The M13.6 physical fixture rests on the table.  The
                    // shared catalog's 85 mm center height is not reused when
                    // the opt-in runtime visual size is smaller.
                    Vector3 localPosition = target.transform.localPosition;
                    target.transform.localPosition = new Vector3(
                        localPosition.x, size.y * 0.5f, localPosition.z);
                }
            }
        }

        private Vector3 MujocoPosition(float[] position)
        {
            return m_worldOriginLocal + new Vector3(
                m_worldScale * position[0],
                m_worldScale * (position[2] - m_tableTopMujocoZ),
                -m_worldScale * position[1]);
        }

        private static Quaternion MujocoRotation(float[] quaternionWxyz)
        {
            Quaternion mujocoRotation = new Quaternion(
                quaternionWxyz[1], quaternionWxyz[2], quaternionWxyz[3], quaternionWxyz[0]);
            return Quaternion.Euler(-90f, 0f, 0f) * mujocoRotation;
        }

        private static M13_6BlockWorldState FindBlock(M13_6BlockWorldState[] blocks, string logicalBlockId)
        {
            for (int index = 0; index < blocks.Length; index++)
                if (blocks[index] != null && blocks[index].logicalBlockId == logicalBlockId)
                    return blocks[index];
            return null;
        }

        private static Transform FindDescendant(Transform root, string name)
        {
            Transform[] transforms = root.GetComponentsInChildren<Transform>(true);
            for (int index = 0; index < transforms.Length; index++)
                if (transforms[index] != root && transforms[index].name == name)
                    return transforms[index];
            return null;
        }

        private void OnGUI()
        {
            if (!m_showDebugOverlay)
                return;
            GUI.Label(new Rect(12f, 12f, 520f, 100f),
                "M13.6 visual stream\n" +
                "connected=" + m_receiveRunning + " recvHz=" + ReceiveRateHz.ToString("F1") +
                " seq=" + m_lastAcceptedSequence + " accepted=" + m_acceptedFrameCount +
                " stale=" + m_staleFrameCount + " duplicate=" + m_duplicateFrameCount +
                " malformed=" + m_malformedPacketCount);
        }
    }
}
