using System.Collections.Generic;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using BCIIntelligentRobot.VirtualManipulation;
using UnityEngine;

namespace PassthroughCameraSamples.MultiObjectDetection
{
    /// <summary>
    /// Builds the controlled M9 virtual tabletop in its dedicated scene and
    /// feeds its explicit targets into the existing M8 ViewLockedHud lifecycle.
    /// It does not start detection, EEG decoding, or a robot action.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class M9VirtualManipulationBootstrap : MonoBehaviour
    {
        private const float TableDistanceMeters = 0.85f;
        private const float TableDropFromViewMeters = 0.70f;
        private const float StimulusSizeMeters = 0.32f;

        [SerializeField] private string m_selectionServerHost = "192.168.43.168";
        [SerializeField, Min(1)] private int m_selectionServerPort = 11001;
        [SerializeField] private Vector3 m_hudLocalCenter = new Vector3(0f, 0.18f, 0.85f);
        [SerializeField, Min(0f)] private float m_hudHorizontalSpacingMeters = 0.32f;
        [SerializeField, Min(0.1f)] private float m_hudStimulusSizeMeters = 0.20f;

        private readonly List<GameObject> m_createdTargets = new List<GameObject>();
        private Transform m_tabletopRoot;
        private Material m_sharedPrimitiveMaterial;
        private bool m_initialized;

        private void Start()
        {
            if (!InitializeVirtualScene())
                enabled = false;
        }

        public bool InitializeVirtualScene()
        {
            if (m_initialized)
                return true;

            M9VirtualBlockCatalogData catalog;
            try
            {
                catalog = M9VirtualBlockCatalog.LoadFromResources();
            }
            catch (System.Exception error)
            {
                Debug.LogError("M9_VIRTUAL catalog_invalid error=" + error.Message, this);
                return false;
            }

            CreateTabletop(catalog);
            List<StableWorldAnchorSnapshot> candidates = CreateBlocks(catalog);
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
            if (!binding.InitializeVirtualTargets(m_tabletopRoot, StimulusSizeMeters))
            {
                Debug.LogError("M9_VIRTUAL binding_initialization_failed", this);
                return false;
            }

            BciSelectionTransportClient transport = GetComponent<BciSelectionTransportClient>();
            if (transport == null)
                transport = gameObject.AddComponent<BciSelectionTransportClient>();
            transport.Initialize(binding, m_selectionServerHost, m_selectionServerPort);

            BciTargetBatchController batchController = GetComponent<BciTargetBatchController>();
            if (batchController == null)
                batchController = gameObject.AddComponent<BciTargetBatchController>();
            batchController.Initialize(binding, transport);

            if (!binding.SetVirtualTargetCandidates(candidates))
            {
                Debug.LogError("M9_VIRTUAL candidate_publication_failed", this);
                return false;
            }

            m_initialized = true;
            Debug.Log("M9_VIRTUAL scene_ready blocks=" + m_createdTargets.Count +
                " active_bci_slots=3 selection=software_or_pc_class_input eeg_decoding=false", this);
            return true;
        }

        private void CreateTabletop(M9VirtualBlockCatalogData catalog)
        {
            Camera viewCamera = Camera.main;
            Vector3 forward = viewCamera != null
                ? Vector3.ProjectOnPlane(viewCamera.transform.forward, Vector3.up)
                : Vector3.forward;
            if (forward.sqrMagnitude < 0.0001f)
                forward = Vector3.forward;
            forward.Normalize();

            Vector3 center = viewCamera != null
                ? viewCamera.transform.position + forward * TableDistanceMeters - Vector3.up * TableDropFromViewMeters
                : new Vector3(0f, 0.75f, TableDistanceMeters);
            m_tabletopRoot = new GameObject("M9_Virtual_Tabletop").transform;
            m_tabletopRoot.SetPositionAndRotation(center, Quaternion.LookRotation(forward, Vector3.up));

            GameObject table = GameObject.CreatePrimitive(PrimitiveType.Cube);
            table.name = "M9_Virtual_Table";
            table.transform.SetParent(m_tabletopRoot, false);
            table.transform.localPosition = new Vector3(0f, -catalog.tableSizeMeters.y * 0.5f, 0f);
            table.transform.localScale = catalog.tableSizeMeters;
            SetRendererColor(table.GetComponent<Renderer>(), new Color(0.30f, 0.31f, 0.34f, 1f));
        }

        private List<StableWorldAnchorSnapshot> CreateBlocks(M9VirtualBlockCatalogData catalog)
        {
            var candidates = new List<StableWorldAnchorSnapshot>(catalog.blocks.Length);
            for (int index = 0; index < catalog.blocks.Length; index++)
            {
                M9VirtualBlockDefinition definition = catalog.blocks[index];
                GameObject block = GameObject.CreatePrimitive(PrimitiveType.Cube);
                block.name = "M9_VirtualBlock_" + definition.targetId;
                block.transform.SetParent(m_tabletopRoot, false);
                block.transform.localPosition = definition.localPositionMeters;
                block.transform.localScale = catalog.blockSizeMeters;

                M9VirtualBlockTarget identity = block.AddComponent<M9VirtualBlockTarget>();
                identity.Configure(definition);
                SetRendererColor(block.GetComponent<Renderer>(), definition.color);
                m_createdTargets.Add(block);
                if (identity.IsBciCandidate)
                    candidates.Add(identity.CreateAnchorSnapshot());
            }

            return candidates;
        }

        private void SetRendererColor(Renderer renderer, Color color)
        {
            if (renderer == null)
                return;
            if (m_sharedPrimitiveMaterial == null)
            {
                Shader shader = Shader.Find("Universal Render Pipeline/Lit") ??
                    Shader.Find("Standard") ??
                    Shader.Find("Unlit/Color");
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

        private void OnDestroy()
        {
            if (m_sharedPrimitiveMaterial != null)
                Destroy(m_sharedPrimitiveMaterial);
            if (m_tabletopRoot != null)
                Destroy(m_tabletopRoot.gameObject);
        }
    }
}
