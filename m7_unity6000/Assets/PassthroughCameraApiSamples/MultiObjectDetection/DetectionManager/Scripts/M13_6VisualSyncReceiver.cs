using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;
using BCIIntelligentRobot.VirtualManipulation;
using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
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
        public M13_6BlockWorldState[] blocks;
    }

    /// <summary>
    /// Receives the independent M13.6 UDP latest-state stream. It never
    /// touches the reliable M8 selection transport or changes target identity.
    /// All Unity Transform writes happen on the main thread in Update.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class M13_6VisualSyncReceiver : MonoBehaviour
    {
        public const int DefaultPort = 11002;
        private const int ProtocolVersion = 1;
        private const string MessageType = "m13_6_robot_world_state";
        private const float DefaultScale = 0.70f;
        private const float TableTopMujocoZ = 0.75f;
        private const float InterpolationDurationSeconds = 1f / 30f;

        [SerializeField, Min(1)] private int m_listenPort = DefaultPort;
        [SerializeField] private bool m_startOnEnable = true;
        [SerializeField] private bool m_showDebugOverlay;
        [SerializeField, Min(0.01f)] private float m_worldScale = DefaultScale;
        [SerializeField] private float m_tableTopMujocoZ = TableTopMujocoZ;
        [SerializeField] private Vector3 m_worldOriginLocal;

        private readonly ConcurrentQueue<byte[]> m_pendingPackets = new ConcurrentQueue<byte[]>();
        private readonly Dictionary<string, M9VirtualBlockTarget> m_targetsByLogicalId =
            new Dictionary<string, M9VirtualBlockTarget>(StringComparer.Ordinal);
        private UdpClient m_udpClient;
        private Thread m_receiveThread;
        private volatile bool m_receiveRunning;
        private Transform m_visualWorldRoot;
        private Transform m_robotRoot;
        private M9FrankaVisualRig m_robotRig;
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
        private float m_lastPacketArrivalTime = -1f;
        private float m_interarrivalSeconds;

        public bool IsReceiving => m_receiveRunning;
        public long LastAcceptedSequence => m_lastAcceptedSequence;
        public int ReceivedPacketCount => m_receivedPacketCount;
        public int AcceptedFrameCount => m_acceptedFrameCount;
        public int StaleFrameCount => m_staleFrameCount;
        public int DuplicateFrameCount => m_duplicateFrameCount;
        public int MalformedPacketCount => m_malformedPacketCount;
        public float ReceiveRateHz => m_interarrivalSeconds > 0f ? 1f / m_interarrivalSeconds : 0f;

        private void OnEnable()
        {
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
                m_udpClient = new UdpClient(m_listenPort);
                m_udpClient.Client.ReceiveTimeout = 250;
                m_receiveRunning = true;
                m_receiveThread = new Thread(ReceiveLoop)
                {
                    IsBackground = true,
                    Name = "M13.6VisualSyncReceiver"
                };
                m_receiveThread.Start();
                Debug.Log("M13_6_VISUAL receiver_started port=" + m_listenPort, this);
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
        }

        private void CloseSocket()
        {
            if (m_udpClient == null)
                return;
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

        private void ReceiveLoop()
        {
            IPEndPoint endpoint = new IPEndPoint(IPAddress.Any, 0);
            while (m_receiveRunning)
            {
                try
                {
                    byte[] payload = m_udpClient.Receive(ref endpoint);
                    if (payload != null && payload.Length > 0)
                        m_pendingPackets.Enqueue(payload);
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

        private void Update()
        {
            ResolveSceneReferences();
            DrainPackets();
            ApplyInterpolatedFrame();
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

            if (m_robotRoot == null)
            {
                Transform anchor = m_visualWorldRoot.Find("LeftRobotAnchor");
                if (anchor != null)
                    m_robotRoot = FindDescendant(anchor, "FrankaRoot");
                if (m_robotRoot != null)
                    m_robotRig = m_robotRoot.GetComponent<M9FrankaVisualRig>();
            }

            if (m_targetsByLogicalId.Count == 4)
                return;

            M9VirtualBlockTarget[] targets = m_visualWorldRoot.GetComponentsInChildren<M9VirtualBlockTarget>(true);
            for (int index = 0; index < targets.Length; index++)
            {
                M9VirtualBlockTarget target = targets[index];
                if (target != null && !string.IsNullOrEmpty(target.LogicalBlockId))
                    m_targetsByLogicalId[target.LogicalBlockId] = target;
            }
        }

        private void DrainPackets()
        {
            byte[] payload;
            while (m_pendingPackets.TryDequeue(out payload))
            {
                m_receivedPacketCount++;
                M13_6RobotWorldStateFrame frame;
                try
                {
                    frame = JsonUtility.FromJson<M13_6RobotWorldStateFrame>(Encoding.UTF8.GetString(payload));
                    ValidateFrame(frame);
                }
                catch (Exception)
                {
                    m_malformedPacketCount++;
                    continue;
                }

                if (frame.sequence <= m_lastAcceptedSequence)
                {
                    if (frame.sequence == m_lastAcceptedSequence)
                        m_duplicateFrameCount++;
                    else
                        m_staleFrameCount++;
                    continue;
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
            m_lastAppliedSequence = m_currentFrame.sequence;
        }

        private void ApplyFrame(
            M13_6RobotWorldStateFrame from,
            M13_6RobotWorldStateFrame to,
            float alpha)
        {
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

            if (m_robotRoot != null)
            {
                Vector3 robotPosition = Vector3.Lerp(MujocoPosition(from.robotBasePositionMujoco), MujocoPosition(to.robotBasePositionMujoco), alpha);
                Quaternion robotRotation = Quaternion.Slerp(MujocoRotation(from.robotBaseQuaternionMujocoWxyz), MujocoRotation(to.robotBaseQuaternionMujocoWxyz), alpha);
                m_robotRoot.position = m_visualWorldRoot.TransformPoint(robotPosition);
                m_robotRoot.rotation = m_visualWorldRoot.rotation * robotRotation;
            }

            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            for (int index = 0; index < to.blocks.Length; index++)
            {
                M13_6BlockWorldState currentBlock = to.blocks[index];
                M13_6BlockWorldState previousBlock = FindBlock(from.blocks, currentBlock.logicalBlockId) ?? currentBlock;
                if (!m_targetsByLogicalId.TryGetValue(currentBlock.logicalBlockId, out M9VirtualBlockTarget target) || target == null)
                    continue;

                Vector3 position = Vector3.Lerp(MujocoPosition(previousBlock.positionMujoco), MujocoPosition(currentBlock.positionMujoco), alpha);
                Quaternion rotation = Quaternion.Slerp(MujocoRotation(previousBlock.quaternionMujocoWxyz), MujocoRotation(currentBlock.quaternionMujocoWxyz), alpha);
                target.transform.position = m_visualWorldRoot.TransformPoint(position);
                target.transform.rotation = m_visualWorldRoot.rotation * rotation;
                targetIds.Add(currentBlock.logicalBlockId);
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
