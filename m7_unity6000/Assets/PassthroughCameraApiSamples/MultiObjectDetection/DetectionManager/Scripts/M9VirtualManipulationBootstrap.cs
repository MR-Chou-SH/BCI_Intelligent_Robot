using System.Collections;
using System.Collections.Generic;
using System.Text;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using BCIIntelligentRobot.VirtualManipulation;
using UnityEngine;
using UnityEngine.XR;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// Builds the controlled M9 virtual tabletop after the XR camera has a
    /// stable head pose, then feeds explicit targets into the existing M8
    /// ViewLockedHud lifecycle. It does not start EEG decoding or robot action.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class M9VirtualManipulationBootstrap : MonoBehaviour
    {
        private const float WorkspaceDistanceMeters = 1.45f;
        private const float TableSurfaceDropBelowHeadMeters = 0.55f;
        private const float VirtualFloorY = 0f;
        // This runtime-generated label is authored in M9WorkspaceRoot local
        // space. The workspace is presented from the opposite side of the
        // table; its root supplies the presentation half-turn, so keep this
        // label's local pose free of the old standalone yaw compensation.
        private static readonly Vector3 InstructionTextLocalPosition =
            new Vector3(0f, -0.985f, 0.4f);
        private static readonly Vector3 InstructionTextLocalEulerAngles =
            new Vector3(80f, 180f, 0f);
        private const float StimulusSizeMeters = 0.32f;
        private const float InitialPoseTimeoutSeconds = 8f;
        private const float MinimumTrackedHeadHeightMeters = 0.5f;
        private const float EditorFallbackHeadHeightMeters = 1.55f;
        private const string SampleNavigationRootName = "ReturnToStartScene";
        // M13.6 derives the visual root from the same MuJoCo table frame as
        // the block telemetry.  MuJoCo's fixed robot base is (0, 0.4, 0.75)
        // and the table top is z=0.75; the existing 0.7 scale and Rx(-90)
        // basis therefore map that base to table-local (0, 0, -0.28).
        private const float M13_6VisualScale = 0.70f;
        private const float M13_6RobotBaseYMeters = 0.40f;
        private const float M13_6RobotBaseZMeters = 0.75f;
        private const float M13_6TableTopMujocoZMeters = 0.75f;
        private const float M13_6VisualBlockEdgeMeters = 0.056f;
        private static readonly Vector3 M13_6VisualBlockSize =
            new Vector3(M13_6VisualBlockEdgeMeters, M13_6VisualBlockEdgeMeters, M13_6VisualBlockEdgeMeters);
        private static readonly Color VirtualBackgroundColor = new Color(0.30f, 0.37f, 0.44f, 1f);

        [SerializeField] private string m_selectionServerHost = "192.168.43.168";
        [SerializeField, Min(1)] private int m_selectionServerPort = 11001;
        [SerializeField] private BciSelectionInteractionMode m_selectionInteractionMode = BciSelectionInteractionMode.LegacyGrouped;
        [SerializeField] private bool m_useM20AssistiveDeskProfile = false;
        [SerializeField] private Vector3 m_hudLocalCenter = new Vector3(0f, 0.18f, 0.85f);
        [SerializeField, Min(0f)] private float m_hudHorizontalSpacingMeters = 0.32f;
        [SerializeField, Min(0.1f)] private float m_hudStimulusSizeMeters = 0.20f;

        private readonly List<GameObject> m_createdTargets = new List<GameObject>();
        private Transform m_workspaceRoot;
        private Transform m_tableRoot;
        private Transform m_blocksRoot;
        private Transform m_leftRobotAnchor;
        private Transform m_rightRobotAnchor;
        private Transform m_virtualRoomRoot;
        private Material m_sharedPrimitiveMaterial;
        private GameObject m_virtualLightObject;
        private bool m_initialized;

        private IEnumerator Start()
        {
            yield return WaitForInitialHeadPose();
        }

        private IEnumerator WaitForInitialHeadPose()
        {
            float waitStartedAt = Time.realtimeSinceStartup;
            int stablePoseFrames = 0;
            bool waitLogged = false;

            while (Time.realtimeSinceStartup - waitStartedAt < InitialPoseTimeoutSeconds)
            {
                // OVRCameraRig applies the tracking pose in Update. Yielding at
                // least one frame prevents Start-order from capturing its
                // serialized identity pose.
                yield return null;
                Camera viewCamera = Camera.main;
                bool xrExpected = Application.platform == RuntimePlatform.Android || XRSettings.isDeviceActive;

                if (!xrExpected && Application.isEditor)
                {
                    if (viewCamera == null)
                    {
                        stablePoseFrames = 0;
                        continue;
                    }

                    Vector3 editorHeadPosition = viewCamera.transform.position;
                    Vector3 editorHeadForward = HorizontalForward(viewCamera.transform.forward);
                    if (!IsFinite(editorHeadPosition) || editorHeadForward.sqrMagnitude < 0.0001f)
                    {
                        stablePoseFrames = 0;
                        continue;
                    }

                    string poseSource = "editor_camera_world";
                    if (editorHeadPosition.y < MinimumTrackedHeadHeightMeters)
                    {
                        // The non-XR Editor camera often remains at (0,0,0).
                        // Give Editor Play a plausible eye height without ever
                        // using this fallback on an XR runtime.
                        editorHeadPosition.y = EditorFallbackHeadHeightMeters;
                        poseSource = "editor_camera_height_fallback";
                    }

                    if (++stablePoseFrames >= 2)
                    {
                        if (!InitializeVirtualScene(
                                editorHeadPosition,
                                editorHeadForward,
                                poseSource,
                                viewCamera))
                            enabled = false;
                        yield break;
                    }

                    continue;
                }

                InputDevice headDevice = InputDevices.GetDeviceAtXRNode(XRNode.Head);
                bool hasTrackedHeadPose = headDevice.isValid &&
                    headDevice.TryGetFeatureValue(CommonUsages.isTracked, out bool isTracked) && isTracked &&
                    headDevice.TryGetFeatureValue(CommonUsages.devicePosition, out Vector3 trackedPosition) &&
                    IsFinite(trackedPosition) &&
                    headDevice.TryGetFeatureValue(CommonUsages.deviceRotation, out Quaternion trackedRotation) &&
                    IsFinite(trackedRotation) && Quaternion.Dot(trackedRotation, trackedRotation) > 0.5f;

                if (!hasTrackedHeadPose || viewCamera == null)
                {
                    stablePoseFrames = 0;
                    if (!waitLogged && Time.realtimeSinceStartup - waitStartedAt >= 0.5f)
                    {
                        Debug.Log("M9_SPATIAL waiting_for_tracked_hmd device_valid=" + headDevice.isValid +
                            " camera_available=" + (viewCamera != null), this);
                        waitLogged = true;
                    }
                    continue;
                }

                // DevicePosition/Rotation are tracking-space values. Use the
                // XR camera transform for the final world-space pose after the
                // rig has applied the tracked device pose.
                Vector3 hmdWorldPosition = viewCamera.transform.position;
                Vector3 hmdWorldForward = HorizontalForward(viewCamera.transform.forward);
                if (!IsFinite(hmdWorldPosition) ||
                    hmdWorldPosition.y < MinimumTrackedHeadHeightMeters ||
                    hmdWorldForward.sqrMagnitude < 0.0001f)
                {
                    stablePoseFrames = 0;
                    continue;
                }

                if (++stablePoseFrames >= 2)
                {
                    if (!InitializeVirtualScene(
                            hmdWorldPosition,
                            hmdWorldForward,
                            "xr_head_device_camera_world",
                            viewCamera))
                        enabled = false;
                    yield break;
                }
            }

            Debug.LogError("M9_SPATIAL initialization_failed reason=tracked_hmd_pose_timeout seconds=" +
                InitialPoseTimeoutSeconds.ToString("F1"), this);
            enabled = false;
        }

        private bool InitializeVirtualScene(
            Vector3 headPosition,
            Vector3 headForward,
            string poseSource,
            Camera viewCamera)
        {
            if (m_initialized)
                return true;

            if (m_selectionInteractionMode == BciSelectionInteractionMode.ResearchAcquisitionV1)
            {
                DisableM9SampleNavigation();
                ConfigureVirtualBackground(viewCamera);
                BciSelectionTransportClient researchTransport = GetComponent<BciSelectionTransportClient>();
                if (researchTransport == null)
                    researchTransport = gameObject.AddComponent<BciSelectionTransportClient>();
                researchTransport.InitializeResearchOnly(m_selectionServerHost, m_selectionServerPort);

                BciM19ResearchAcquisitionController researchController =
                    GetComponent<BciM19ResearchAcquisitionController>();
                if (researchController == null)
                    researchController = gameObject.AddComponent<BciM19ResearchAcquisitionController>();
                researchController.Initialize(researchTransport);
                m_initialized = researchController.IsInitialized;
                if (!m_initialized)
                    Debug.LogError("M19_RESEARCH initialization_failed", this);
                else
                    Debug.Log("M19_RESEARCH mode_ready fixed_slots=YELLOW_7.2,BLUE_9,GREEN_12 " +
                        "selection_queue=false m9_dispatch=false", this);
                return m_initialized;
            }

            M9VirtualBlockCatalogData catalog;
            M20AssistiveDeskSceneSpec m20SceneSpec = null;
            try
            {
                if (m_useM20AssistiveDeskProfile)
                {
                    M20AssistiveDeskSceneSpec canonicalSpec = M20AssistiveDeskUnityScene.LoadFromResources();
                    int seedOverride;
                    bool hasSeedOverride = M20AssistiveDeskUnityScene.TryGetCommandLineSeedOverride(
                        System.Environment.GetCommandLineArgs(), out seedOverride);
                    m20SceneSpec = M20AssistiveDeskUnityScene.CreateRuntimeSceneSpec(
                        canonicalSpec,
                        hasSeedOverride ? (int?)seedOverride : null);
                    catalog = M20AssistiveDeskUnityScene.CreatePagedQueueCatalog(m20SceneSpec);
                }
                else
                {
                    catalog = M9VirtualBlockCatalog.LoadFromResources();
                }
            }
            catch (System.Exception error)
            {
                Debug.LogError("M9_VIRTUAL catalog_invalid error=" + error.Message, this);
                return false;
            }

            DisableM9SampleNavigation();
            ConfigureVirtualBackground(viewCamera);
            CreateWorkspace(catalog, headPosition, headForward);
            List<StableWorldAnchorSnapshot> candidates;
            if (m20SceneSpec != null)
            {
                List<GameObject> targetRoots;
                M20AssistiveDeskArticulationController articulationController;
                candidates = M20AssistiveDeskUnityScene.CreateTargets(
                    m20SceneSpec,
                    m_blocksRoot,
                    gameObject,
                    SetRendererColor,
                    out targetRoots,
                    out articulationController);
                m_createdTargets.AddRange(targetRoots);
                Debug.Log("M20_ASSISTIVE articulation_ready editor_context_menu_available=" +
                    articulationController.IsInitialized, this);
            }
            else
            {
                candidates = CreateBlocks(catalog);
            }
            if (candidates.Count < 3)
            {
                Debug.LogError("M9_VIRTUAL scene_invalid selectable_candidate_count=" + candidates.Count, this);
                return false;
            }

            BciSsvepTargetBinding binding = GetComponent<BciSsvepTargetBinding>();
            if (binding == null)
                binding = gameObject.AddComponent<BciSsvepTargetBinding>();
            binding.ConfigureLayout(
                BciSsvepLayoutMode.ViewLockedHud,
                m_hudLocalCenter,
                m_hudHorizontalSpacingMeters,
                m_hudStimulusSizeMeters);
            if (!binding.InitializeVirtualTargets(m_workspaceRoot, StimulusSizeMeters))
            {
                Debug.LogError("M9_VIRTUAL binding_initialization_failed", this);
                return false;
            }

            BciSelectionTransportClient transport = GetComponent<BciSelectionTransportClient>();
            if (transport == null)
                transport = gameObject.AddComponent<BciSelectionTransportClient>();
            if (m20SceneSpec != null && !transport.ConfigureM20SceneLayoutSnapshot(
                m20SceneSpec.runtimeSceneId, m20SceneSpec.layoutSnapshotJson))
            {
                Debug.LogError("M20_ASSISTIVE scene_snapshot_transport_configuration_failed scene_id=" +
                    m20SceneSpec.runtimeSceneId, this);
                return false;
            }
            transport.Initialize(binding, m_selectionServerHost, m_selectionServerPort);

            if (m_selectionInteractionMode == BciSelectionInteractionMode.PagedQueueV1)
            {
                BciPagedTargetQueueController pagedController = GetComponent<BciPagedTargetQueueController>();
                if (pagedController == null)
                    pagedController = gameObject.AddComponent<BciPagedTargetQueueController>();
                pagedController.Initialize(binding, transport, catalog, useCatalogCandidateOrder: m20SceneSpec != null);

                BciTargetBatchController presentationBridge = GetComponent<BciTargetBatchController>();
                if (presentationBridge == null)
                    presentationBridge = gameObject.AddComponent<BciTargetBatchController>();
                presentationBridge.InitializePresentationBridge(binding);
            }
            else
            {
                BciTargetBatchController batchController = GetComponent<BciTargetBatchController>();
                if (batchController == null)
                    batchController = gameObject.AddComponent<BciTargetBatchController>();
                batchController.Initialize(binding, transport);
            }

            if (!binding.SetVirtualTargetCandidates(candidates))
            {
                Debug.LogError("M9_VIRTUAL candidate_publication_failed", this);
                return false;
            }

            m_initialized = true;
            Debug.Log("M9_VIRTUAL scene_ready blocks=" + m_createdTargets.Count +
                " active_bci_slots=3 selection=software_or_pc_class_input eeg_decoding=false", this);
            LogSpatialDiagnostics(headPosition, HorizontalForward(headForward), poseSource, viewCamera);
            return true;
        }

        private void CreateWorkspace(
            M9VirtualBlockCatalogData catalog,
            Vector3 headPosition,
            Vector3 headForward)
        {
            Vector3 forward = HorizontalForward(headForward);
            if (forward.sqrMagnitude < 0.0001f)
                forward = Vector3.forward;

            Vector3 workspacePosition = headPosition + forward * WorkspaceDistanceMeters;
            workspacePosition.y = headPosition.y - TableSurfaceDropBelowHeadMeters;
            Quaternion workspaceRotation = Quaternion.LookRotation(-forward, Vector3.up);

            m_workspaceRoot = new GameObject("M9WorkspaceRoot").transform;
            m_workspaceRoot.SetPositionAndRotation(workspacePosition, workspaceRotation);
            m_workspaceRoot.localScale = Vector3.one;
            m_tableRoot = CreateWorkspaceChild("Table");
            m_blocksRoot = CreateWorkspaceChild("Blocks");
            m_leftRobotAnchor = CreateWorkspaceChild("LeftRobotAnchor");
            m_leftRobotAnchor.localPosition = M13_6RobotBaseAnchorLocalPosition();
            m_rightRobotAnchor = CreateWorkspaceChild("RightRobotAnchor");
            m_rightRobotAnchor.localPosition = M13_6RobotBaseAnchorLocalPosition();

            CreateVirtualRoom();
            CreateWoodTable(catalog);
            GameObject robot = M9FrankaVisualFactory.Create(m_leftRobotAnchor, SetRendererColor);
            if (robot == null)
                Debug.LogError("M9_VIRTUAL franka_visual_initialization_failed", this);
            else
                M9FrankaVisualFactory.LogTransformDiagnostics(robot.transform);
            if (!m_useM20AssistiveDeskProfile)
                CreateFloorInstructionText();
            CreateVirtualKeyLight();
        }

        private Transform CreateWorkspaceChild(string childName)
        {
            var child = new GameObject(childName).transform;
            child.SetParent(m_workspaceRoot, false);
            child.localPosition = Vector3.zero;
            child.localRotation = Quaternion.identity;
            child.localScale = Vector3.one;
            return child;
        }

        private static Vector3 M13_6RobotBaseAnchorLocalPosition()
        {
            return new Vector3(
                0f,
                M13_6VisualScale * (M13_6RobotBaseZMeters - M13_6TableTopMujocoZMeters),
                -M13_6VisualScale * M13_6RobotBaseYMeters);
        }

        private void CreateWoodTable(M9VirtualBlockCatalogData catalog)
        {
            float topThickness = catalog.tableSizeMeters.y;
            Color tabletopColor = new Color(0.72f, 0.55f, 0.36f, 1f);
            Color supportColor = new Color(0.53f, 0.37f, 0.23f, 1f);

            CreateFurniturePart(
                "M9_Wood_Tabletop",
                new Vector3(0f, -topThickness * 0.5f, 0f),
                new Vector3(catalog.tableSizeMeters.x, topThickness, catalog.tableSizeMeters.z),
                tabletopColor);

            const float apronHeight = 0.08f;
            const float apronDepth = 0.035f;
            float apronCenterY = -topThickness - apronHeight * 0.5f;
            float halfWidth = catalog.tableSizeMeters.x * 0.5f;
            float halfDepth = catalog.tableSizeMeters.z * 0.5f;
            float apronLength = catalog.tableSizeMeters.x - 0.13f;
            float apronWidth = catalog.tableSizeMeters.z - 0.13f;

            CreateFurniturePart("M9_Table_Apron_Front",
                new Vector3(0f, apronCenterY, -halfDepth + 0.065f),
                new Vector3(apronLength, apronHeight, apronDepth), supportColor);
            CreateFurniturePart("M9_Table_Apron_Back",
                new Vector3(0f, apronCenterY, halfDepth - 0.065f),
                new Vector3(apronLength, apronHeight, apronDepth), supportColor);
            CreateFurniturePart("M9_Table_Apron_Left",
                new Vector3(-halfWidth + 0.065f, apronCenterY, 0f),
                new Vector3(apronDepth, apronHeight, apronWidth), supportColor);
            CreateFurniturePart("M9_Table_Apron_Right",
                new Vector3(halfWidth - 0.065f, apronCenterY, 0f),
                new Vector3(apronDepth, apronHeight, apronWidth), supportColor);

            const float legSize = 0.055f;
            float legLength = Mathf.Max(
                0.45f,
                m_workspaceRoot.position.y - VirtualFloorY - topThickness - apronHeight);
            float legCenterY = -topThickness - apronHeight - legLength * 0.5f;
            float legX = halfWidth - 0.065f;
            float legZ = halfDepth - 0.065f;
            CreateFurniturePart("M9_Table_Leg_FrontLeft", new Vector3(-legX, legCenterY, -legZ),
                new Vector3(legSize, legLength, legSize), supportColor);
            CreateFurniturePart("M9_Table_Leg_FrontRight", new Vector3(legX, legCenterY, -legZ),
                new Vector3(legSize, legLength, legSize), supportColor);
            CreateFurniturePart("M9_Table_Leg_BackLeft", new Vector3(-legX, legCenterY, legZ),
                new Vector3(legSize, legLength, legSize), supportColor);
            CreateFurniturePart("M9_Table_Leg_BackRight", new Vector3(legX, legCenterY, legZ),
                new Vector3(legSize, legLength, legSize), supportColor);
        }

        private void CreateFurniturePart(string name, Vector3 localPosition, Vector3 size, Color color)
        {
            CreateGeometryPart(m_tableRoot, name, localPosition, size, color);
        }

        private void CreateVirtualRoom()
        {
            m_virtualRoomRoot = CreateWorkspaceChild("VirtualRoom");
            float workspaceY = m_workspaceRoot.position.y;
            CreateGeometryPart(
                m_virtualRoomRoot,
                "M9_Virtual_Room_Floor",
                new Vector3(0f, VirtualFloorY - workspaceY - 0.03f, -WorkspaceDistanceMeters),
                new Vector3(10f, 0.06f, 10f),
                new Color(0.32f, 0.37f, 0.42f, 1f));
            CreateGeometryPart(
                m_virtualRoomRoot,
                "M9_Virtual_Room_BackWall",
                new Vector3(0f, 1.7f - workspaceY, 5.5f - WorkspaceDistanceMeters),
                new Vector3(10f, 3.4f, 0.08f),
                new Color(0.40f, 0.44f, 0.49f, 1f));
        }

        private void CreateVirtualKeyLight()
        {
            m_virtualLightObject = new GameObject("M9_Virtual_KeyLight");
            m_virtualLightObject.transform.rotation = Quaternion.Euler(48f, -28f, 0f);
            Light keyLight = m_virtualLightObject.AddComponent<Light>();
            keyLight.type = LightType.Directional;
            keyLight.color = new Color(1f, 0.97f, 0.90f, 1f);
            keyLight.intensity = 1.15f;
            keyLight.shadows = LightShadows.None;
            keyLight.cullingMask = ~0;
            RenderSettings.sun = keyLight;
        }

        private void CreateGeometryPart(Transform parent, string name, Vector3 localPosition, Vector3 size, Color color)
        {
            GameObject part = GameObject.CreatePrimitive(PrimitiveType.Cube);
            part.name = name;
            part.transform.SetParent(parent, false);
            part.transform.localPosition = localPosition;
            part.transform.localScale = size;
            Collider collider = part.GetComponent<Collider>();
            if (collider != null)
                collider.enabled = false;
            SetRendererColor(part.GetComponent<Renderer>(), color);
        }

        private void CreateFloorInstructionText()
        {
            var labelObject = new GameObject("InstructionText");
            labelObject.transform.SetParent(m_workspaceRoot, false);
            labelObject.transform.localPosition = InstructionTextLocalPosition;
            labelObject.transform.localRotation = Quaternion.Euler(InstructionTextLocalEulerAngles);
            labelObject.transform.localScale = Vector3.one;
            var label = labelObject.AddComponent<TextMesh>();
            label.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            label.text = "M9 VIRTUAL WORKSPACE\nSelect one block using the 3 visual slots";
            label.anchor = TextAnchor.MiddleCenter;
            label.alignment = TextAlignment.Center;
            label.characterSize = 0.014f;
            label.fontSize = 64;
            label.color = new Color(0.96f, 0.94f, 0.88f, 1f);
        }

        private void DisableM9SampleNavigation()
        {
            GameObject sampleNavigation = GameObject.Find(SampleNavigationRootName);
            if (sampleNavigation == null)
                return;

            sampleNavigation.SetActive(false);
            Debug.Log("M9_VIRTUAL disabled_sample_navigation root=" + SampleNavigationRootName, this);
        }

        private void ConfigureVirtualBackground(Camera viewCamera)
        {
            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            RenderSettings.ambientLight = new Color(0.72f, 0.75f, 0.80f, 1f);
            if (viewCamera == null)
                return;

            viewCamera.clearFlags = CameraClearFlags.SolidColor;
            viewCamera.backgroundColor = VirtualBackgroundColor;
        }

        private List<StableWorldAnchorSnapshot> CreateBlocks(M9VirtualBlockCatalogData catalog)
        {
            var candidates = new List<StableWorldAnchorSnapshot>(catalog.blocks.Length);
            for (int index = 0; index < catalog.blocks.Length; index++)
            {
                M9VirtualBlockDefinition definition = catalog.blocks[index];
                GameObject block = GameObject.CreatePrimitive(PrimitiveType.Cube);
                block.name = "M9_VirtualBlock_" + definition.targetId;
                block.transform.SetParent(m_blocksRoot, false);
                block.transform.localPosition = CanonicalM13_6StartupBlockPosition(definition.localPositionMeters);
                block.transform.localScale = M13_6VisualBlockSize;

                M9VirtualBlockTarget identity = block.AddComponent<M9VirtualBlockTarget>();
                identity.Configure(definition);
                SetRendererColor(block.GetComponent<Renderer>(), definition.color);
                m_createdTargets.Add(block);
                if (identity.IsBciCandidate)
                    candidates.Add(identity.CreateAnchorSnapshot());
            }

            return candidates;
        }

        public static Vector3 M13_6StartupBlockVisualSize => M13_6VisualBlockSize;

        public static Vector3 CanonicalM13_6StartupBlockPosition(Vector3 catalogLocalPosition)
        {
            // The M13.6 runtime cube is 56 mm tall and rests on the same
            // table-frame origin used by MuJoCo telemetry. Keep the frozen
            // catalog's horizontal anchors, but start its visual center at
            // half the canonical runtime size so the first telemetry frame
            // does not move it vertically.
            return new Vector3(
                catalogLocalPosition.x,
                M13_6VisualBlockSize.y * 0.5f,
                catalogLocalPosition.z);
        }

        private void SetRendererColor(Renderer renderer, Color color)
        {
            if (renderer == null)
                return;
            if (m_sharedPrimitiveMaterial == null)
            {
                // This project uses Unity's Built-in Render Pipeline. Prefer
                // its included shaders so Android does not select a URP-only
                // shader that cannot render in this project.
                Shader shader = Shader.Find("Standard") ?? Shader.Find("Unlit/Color");
                if (shader == null)
                {
                    Debug.LogError("M9_VIRTUAL missing primitive material shader.", this);
                    return;
                }
                m_sharedPrimitiveMaterial = new Material(shader);
            }

            renderer.sharedMaterial = m_sharedPrimitiveMaterial;
            var properties = new MaterialPropertyBlock();
            renderer.GetPropertyBlock(properties);
            properties.SetColor("_BaseColor", color);
            properties.SetColor("_Color", color);
            renderer.SetPropertyBlock(properties);
        }

        private void LogSpatialDiagnostics(
            Vector3 headPosition,
            Vector3 headForward,
            string poseSource,
            Camera viewCamera)
        {
            M9VirtualBlockTarget redBlock = null;
            for (int index = 0; index < m_createdTargets.Count; index++)
            {
                M9VirtualBlockTarget identity = m_createdTargets[index].GetComponent<M9VirtualBlockTarget>();
                if (identity != null && identity.TargetId == "m9-vblock-red-01")
                {
                    redBlock = identity;
                    break;
                }
            }

            Transform frankaRoot = m_leftRobotAnchor != null
                ? m_leftRobotAnchor.Find("FrankaRoot")
                : null;
            Transform instructionText = m_workspaceRoot.Find("InstructionText");
            Vector3 towardHead = instructionText != null
                ? Vector3.ProjectOnPlane(headPosition - instructionText.position, Vector3.up)
                : Vector3.zero;
            float textNormalUpDot = instructionText != null
                ? Vector3.Dot(instructionText.forward, Vector3.up)
                : 0f;
            float textTopTowardHeadDot = towardHead.sqrMagnitude > Mathf.Epsilon && instructionText != null
                ? Vector3.Dot(instructionText.up, towardHead.normalized)
                : 0f;
            float glyphRightDot = instructionText != null && viewCamera != null
                ? Vector3.Dot(instructionText.right, viewCamera.transform.right)
                : 0f;

            string cameraState = viewCamera == null
                ? "missing"
                : "name=" + viewCamera.name +
                    ",culling_mask=" + viewCamera.cullingMask +
                    ",near=" + viewCamera.nearClipPlane.ToString("F3") +
                    ",far=" + viewCamera.farClipPlane.ToString("F1");
            string message = "M9_SPATIAL startup pose_source=" + poseSource +
                " hmd_world_position=" + headPosition.ToString("F3") +
                " hmd_horizontal_forward=" + headForward.ToString("F3") +
                " workspace_world_position=" + m_workspaceRoot.position.ToString("F3") +
                " workspace_world_rotation=" + m_workspaceRoot.rotation.eulerAngles.ToString("F1") +
                " workspace_world_scale=" + m_workspaceRoot.lossyScale.ToString("F3") +
                " table_world_position=" + (m_tableRoot != null ? m_tableRoot.position.ToString("F3") : "missing") +
                " red_block_world_position=" + (redBlock != null ? redBlock.transform.position.ToString("F3") : "missing") +
                " franka_world_position=" + (frankaRoot != null ? frankaRoot.position.ToString("F3") : "missing") +
                " franka_world_scale=" + (frankaRoot != null ? frankaRoot.lossyScale.ToString("F3") : "missing") +
                " instruction_text_world_position=" +
                    (instructionText != null ? instructionText.position.ToString("F3") : "missing") +
                " instruction_text_world_forward=" +
                    (instructionText != null ? instructionText.forward.ToString("F3") : "missing") +
                " instruction_text_floor_clearance_m=" +
                    (instructionText != null ? (instructionText.position.y - VirtualFloorY).ToString("F3") : "missing") +
                " instruction_text_normal_up_dot=" + textNormalUpDot.ToString("F3") +
                " instruction_text_top_toward_head_dot=" + textTopTowardHeadDot.ToString("F3") +
                " instruction_text_glyph_right_dot_camera=" + glyphRightDot.ToString("F3") +
                " camera={" + cameraState + "}" +
                " table_renderers={" + SummarizeRenderers(m_tableRoot, viewCamera) + "}" +
                " block_renderers={" + SummarizeRenderers(m_blocksRoot, viewCamera) + "}" +
                " franka_renderers={" + SummarizeRenderers(frankaRoot, viewCamera) + "}";
            Debug.Log(message, this);
        }

        private static string SummarizeRenderers(Transform root, Camera viewCamera)
        {
            if (root == null)
                return "root_missing";

            Renderer[] renderers = root.GetComponentsInChildren<Renderer>(true);
            int enabledCount = 0;
            int cameraLayerCount = 0;
            int inClipRangeCount = 0;
            int supportedShaderCount = 0;
            var shaderNames = new HashSet<string>();
            for (int index = 0; index < renderers.Length; index++)
            {
                Renderer renderer = renderers[index];
                if (renderer == null)
                    continue;
                bool isEnabled = renderer.enabled && renderer.gameObject.activeInHierarchy;
                if (isEnabled)
                    enabledCount++;
                if (viewCamera != null &&
                    (viewCamera.cullingMask & (1 << renderer.gameObject.layer)) != 0)
                    cameraLayerCount++;

                if (viewCamera == null)
                    inClipRangeCount++;
                else
                {
                    float cameraSpaceDepth = viewCamera.transform.InverseTransformPoint(renderer.bounds.center).z;
                    if (cameraSpaceDepth >= viewCamera.nearClipPlane && cameraSpaceDepth <= viewCamera.farClipPlane)
                        inClipRangeCount++;
                }

                Shader shader = renderer.sharedMaterial != null ? renderer.sharedMaterial.shader : null;
                if (shader != null)
                {
                    shaderNames.Add(shader.name + (renderer.sharedMaterial.shader.isSupported ? ":supported" : ":unsupported"));
                    if (renderer.sharedMaterial.shader.isSupported)
                        supportedShaderCount++;
                }
                else
                    shaderNames.Add("missing");
            }

            var summary = new StringBuilder();
            summary.Append("name=").Append(root.name)
                .Append(",position=").Append(root.position.ToString("F3"))
                .Append(",scale=").Append(root.lossyScale.ToString("F3"))
                .Append(",layer=").Append(root.gameObject.layer)
                .Append(",renderers=").Append(renderers.Length)
                .Append(",enabled_active=").Append(enabledCount)
                .Append(",camera_layer_visible=").Append(cameraLayerCount)
                .Append(",inside_clip=").Append(inClipRangeCount)
                .Append(",supported_shader=").Append(supportedShaderCount)
                .Append(",shaders=").Append(string.Join("|", shaderNames));
            return summary.ToString();
        }

        private static Vector3 HorizontalForward(Vector3 forward)
        {
            Vector3 horizontal = Vector3.ProjectOnPlane(forward, Vector3.up);
            if (horizontal.sqrMagnitude > 0.0001f)
                horizontal.Normalize();
            return horizontal;
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(Quaternion value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z) && IsFinite(value.w);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }

        private void OnDestroy()
        {
            DestroyOwnedObject(m_sharedPrimitiveMaterial);
            if (m_workspaceRoot != null)
                DestroyOwnedObject(m_workspaceRoot.gameObject);
            DestroyOwnedObject(m_virtualLightObject);
        }

        private static void DestroyOwnedObject(Object ownedObject)
        {
            if (ownedObject == null)
                return;
            if (Application.isPlaying)
                Object.Destroy(ownedObject);
            else
                Object.DestroyImmediate(ownedObject);
        }
    }
}
