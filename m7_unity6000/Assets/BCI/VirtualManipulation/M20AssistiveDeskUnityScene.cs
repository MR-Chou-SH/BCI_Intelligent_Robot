using System;
using System.Collections.Generic;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.VirtualManipulation
{
    [Serializable]
    public sealed class M20AssistiveDeskSceneSpec
    {
        public int schemaVersion;
        public string sceneId;
        public M20AssistiveDeskTable table;
        public M9VirtualSlotDefinition[] slots;
        public string[] candidateOrderFarToNearLeftToRight;
        public int candidatePageSize;
        public M20AssistiveDeskEntity[] entities;
        public M20AssistiveDeskArticulation[] articulations;
    }

    [Serializable]
    public sealed class M20AssistiveDeskTable
    {
        public Vector3 dimensionsMeters;
    }

    [Serializable]
    public sealed class M20AssistiveDeskEntity
    {
        public string semanticId;
        public string targetId;
        public string logicalBlockId;
        public string semanticLabel;
        public string displayLabel;
        public string sourceKind;
        public string role;
        public bool selectable;
        public bool active;
        public bool slotEligible;
        public string geometry;
        public Vector3 positionMeters;
        public Vector3 dimensionsMeters;
        public float yawDegrees;
        public Color color;
        public M20AssistiveDeskConstruction construction;
    }

    [Serializable]
    public sealed class M20AssistiveDeskConstruction
    {
        public string type;
        public float wallThicknessMeters;
        public float bottomThicknessMeters;
        public Vector2 apertureMeters;
        public float capClearanceMeters;
    }

    [Serializable]
    public sealed class M20AssistiveDeskArticulation
    {
        public string jointId;
        public string kind;
        public string entityId;
        public Vector3 pivotPositionMeters;
        public Vector3 axis;
        public M20AssistiveDeskRange rangeDegrees;
        public M20AssistiveDeskRange rangeMeters;
        public float closedDegrees;
        public float openDegrees;
        public float unpressedMeters;
        public float pressedMeters;
    }

    [Serializable]
    public sealed class M20AssistiveDeskRange
    {
        public float minimum;
        public float maximum;
    }

    /// <summary>
    /// Unity consumer for the canonical M20 Resources JSON. All positions and
    /// dimensions are adapted once into the existing M9 workspace frame.
    /// </summary>
    public static class M20AssistiveDeskUnityScene
    {
        public const string ResourcesPath = "BCI/M20/assistive_desk_scene";

        private static readonly string[] ExpectedCandidateOrder =
        {
            "assist_medicine_box",
            "assist_storage_box",
            "assist_phone",
            "assist_button_switch",
            "assist_wireless_charger",
            "assist_user_zone"
        };

        public static M20AssistiveDeskSceneSpec LoadFromResources()
        {
            TextAsset asset = Resources.Load<TextAsset>(ResourcesPath);
            if (asset == null)
                throw new InvalidOperationException("Missing canonical M20 scene Resources/" + ResourcesPath + ".json");
            return Parse(asset.text);
        }

        public static M20AssistiveDeskSceneSpec Parse(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
                throw new ArgumentException("Canonical M20 scene JSON is required.", nameof(json));
            M20AssistiveDeskSceneSpec spec = JsonUtility.FromJson<M20AssistiveDeskSceneSpec>(json);
            Validate(spec);
            return spec;
        }

        public static M9VirtualBlockCatalogData CreatePagedQueueCatalog(M20AssistiveDeskSceneSpec spec)
        {
            Validate(spec);
            var entityById = BuildEntityMap(spec);
            var catalog = new M9VirtualBlockCatalogData
            {
                schemaVersion = 1,
                tableSizeMeters = new Vector3(
                    spec.table.dimensionsMeters.x,
                    spec.table.dimensionsMeters.z,
                    spec.table.dimensionsMeters.y),
                blockSizeMeters = Vector3.one * 0.01f,
                slots = spec.slots,
                blocks = new M9VirtualBlockDefinition[ExpectedCandidateOrder.Length]
            };

            for (int index = 0; index < ExpectedCandidateOrder.Length; index++)
            {
                M20AssistiveDeskEntity entity = entityById[ExpectedCandidateOrder[index]];
                catalog.blocks[index] = new M9VirtualBlockDefinition
                {
                    targetId = entity.targetId,
                    semanticLabel = entity.semanticLabel,
                    logicalBlockId = entity.logicalBlockId,
                    sourceKind = entity.sourceKind,
                    localPositionMeters = ToUnityWorkspacePosition(entity.positionMeters),
                    color = entity.color,
                    active = entity.active,
                    selectable = entity.selectable,
                    slotEligible = entity.slotEligible
                };
            }
            M9VirtualBlockCatalog.Validate(catalog);
            return catalog;
        }

        public static List<StableWorldAnchorSnapshot> CreateTargets(
            M20AssistiveDeskSceneSpec spec,
            Transform blocksRoot,
            GameObject controllerOwner,
            Action<Renderer, Color> setRendererColor,
            out List<GameObject> targetRoots,
            out M20AssistiveDeskArticulationController articulationController)
        {
            Validate(spec);
            if (blocksRoot == null)
                throw new ArgumentNullException(nameof(blocksRoot));
            if (controllerOwner == null)
                throw new ArgumentNullException(nameof(controllerOwner));
            if (setRendererColor == null)
                throw new ArgumentNullException(nameof(setRendererColor));

            var entityById = BuildEntityMap(spec);
            var catalog = CreatePagedQueueCatalog(spec);
            var definitionById = new Dictionary<string, M9VirtualBlockDefinition>(StringComparer.Ordinal);
            foreach (M9VirtualBlockDefinition definition in catalog.blocks)
                definitionById.Add(definition.targetId, definition);

            targetRoots = new List<GameObject>(ExpectedCandidateOrder.Length);
            var candidates = new List<StableWorldAnchorSnapshot>(ExpectedCandidateOrder.Length);
            foreach (string semanticId in ExpectedCandidateOrder)
            {
                M20AssistiveDeskEntity entity = entityById[semanticId];
                GameObject target = new GameObject("M20_" + semanticId);
                target.transform.SetParent(blocksRoot, false);
                target.transform.localPosition = ToUnityWorkspacePosition(entity.positionMeters);
                target.transform.localRotation = Quaternion.AngleAxis(-entity.yawDegrees, Vector3.up);
                target.transform.localScale = Vector3.one;

                M9VirtualBlockTarget identity = target.AddComponent<M9VirtualBlockTarget>();
                identity.Configure(definitionById[entity.targetId]);
                BuildCandidateGeometry(target.transform, entity, setRendererColor);
                targetRoots.Add(target);
                if (identity.IsBciCandidate)
                    candidates.Add(identity.CreateAnchorSnapshot());
            }

            M20AssistiveDeskArticulation hinge = FindArticulation(spec, "assist_storage_lid_hinge");
            Transform lidPivot = CreateStorageLid(entityById, hinge, blocksRoot, setRendererColor);
            Transform buttonCap = CreateButtonCap(entityById, blocksRoot, setRendererColor);
            articulationController = controllerOwner.AddComponent<M20AssistiveDeskArticulationController>();
            articulationController.Initialize(
                lidPivot,
                buttonCap,
                hinge,
                FindArticulation(spec, "assist_button_cap_slide"));
            return candidates;
        }

        public static Vector3 ToUnityWorkspacePosition(Vector3 canonicalPositionMeters)
        {
            return new Vector3(-canonicalPositionMeters.x, canonicalPositionMeters.z, -canonicalPositionMeters.y);
        }

        public static Vector3 ToUnityWorkspaceDimensions(Vector3 canonicalDimensionsMeters)
        {
            return new Vector3(canonicalDimensionsMeters.x, canonicalDimensionsMeters.z, canonicalDimensionsMeters.y);
        }

        private static void Validate(M20AssistiveDeskSceneSpec spec)
        {
            if (spec == null || spec.schemaVersion != 1 || spec.sceneId != "m20_daily_assistive_desk")
                throw new InvalidOperationException("Unsupported canonical M20 scene schema or sceneId.");
            if (spec.table == null || !IsPositive(spec.table.dimensionsMeters))
                throw new InvalidOperationException("Canonical M20 table dimensions must be positive.");
            if (spec.candidatePageSize != 3 || spec.slots == null || spec.slots.Length != 3)
                throw new InvalidOperationException("M20 must preserve three slots and the M16 page size of three.");
            float[] frequencies = { 7.2f, 9f, 12f };
            int[] frames = { 5, 4, 3 };
            for (int index = 0; index < 3; index++)
            {
                M9VirtualSlotDefinition slot = spec.slots[index];
                if (slot == null || slot.slotIndex != index ||
                    !Mathf.Approximately(slot.nominalFrequencyHz, frequencies[index]) ||
                    slot.framesPerHalfCycle != frames[index])
                    throw new InvalidOperationException("M20 slot mapping must remain 0/1/2 = 7.2/9/12 Hz.");
            }
            if (spec.candidateOrderFarToNearLeftToRight == null ||
                spec.candidateOrderFarToNearLeftToRight.Length != ExpectedCandidateOrder.Length)
                throw new InvalidOperationException("M20 candidate order must define the six canonical targets.");
            for (int index = 0; index < ExpectedCandidateOrder.Length; index++)
                if (spec.candidateOrderFarToNearLeftToRight[index] != ExpectedCandidateOrder[index])
                    throw new InvalidOperationException("M20 candidate order changed at index " + index + ".");
            BuildEntityMap(spec);
        }

        private static Dictionary<string, M20AssistiveDeskEntity> BuildEntityMap(M20AssistiveDeskSceneSpec spec)
        {
            if (spec.entities == null)
                throw new InvalidOperationException("Canonical M20 entities are missing.");
            var result = new Dictionary<string, M20AssistiveDeskEntity>(StringComparer.Ordinal);
            foreach (M20AssistiveDeskEntity entity in spec.entities)
            {
                if (entity == null || string.IsNullOrWhiteSpace(entity.semanticId) ||
                    !IsPositive(entity.dimensionsMeters) || !IsFinite(entity.positionMeters) ||
                    !IsFinite(entity.yawDegrees) || !IsFinite(entity.color))
                    throw new InvalidOperationException("Canonical M20 entity has invalid identity or geometry.");
                if (result.ContainsKey(entity.semanticId))
                    throw new InvalidOperationException("Duplicate M20 semanticId: " + entity.semanticId);
                result.Add(entity.semanticId, entity);
            }
            foreach (string semanticId in ExpectedCandidateOrder)
            {
                if (!result.TryGetValue(semanticId, out M20AssistiveDeskEntity entity) ||
                    !entity.selectable || !entity.active || !entity.slotEligible ||
                    entity.targetId != semanticId || string.IsNullOrWhiteSpace(entity.logicalBlockId) ||
                    entity.sourceKind != M9VirtualBlockCatalog.VirtualSourceKind)
                    throw new InvalidOperationException("M20 selection contract is invalid for " + semanticId + ".");
            }
            return result;
        }

        private static void BuildCandidateGeometry(
            Transform target,
            M20AssistiveDeskEntity entity,
            Action<Renderer, Color> setRendererColor)
        {
            Vector3 size = entity.dimensionsMeters;
            if (entity.semanticId == "assist_storage_box")
            {
                BuildHollowStorage(target, size, entity.construction, entity.color, setRendererColor);
                return;
            }
            if (entity.semanticId == "assist_button_switch")
            {
                BuildButtonHousing(target, size, entity.construction, entity.color, setRendererColor);
                return;
            }

            PrimitiveType type = entity.geometry == "cylinder" ? PrimitiveType.Cylinder : PrimitiveType.Cube;
            CreatePrimitivePart(
                target,
                entity.semanticId + "_visual",
                type,
                Vector3.zero,
                size,
                entity.color,
                setRendererColor);
        }

        private static void BuildHollowStorage(
            Transform parent,
            Vector3 fullSize,
            M20AssistiveDeskConstruction construction,
            Color color,
            Action<Renderer, Color> setRendererColor)
        {
            if (construction == null)
                throw new InvalidOperationException("M20 storage box construction data is missing.");
            float hx = fullSize.x / 2f;
            float hy = fullSize.y / 2f;
            float hz = fullSize.z / 2f;
            float wall = construction.wallThicknessMeters;
            float bottom = construction.bottomThicknessMeters;
            float wallHeight = fullSize.z - bottom;
            float wallCenterZ = -hz + bottom + wallHeight / 2f;
            CreateBoxPart(parent, "storage_bottom", new Vector3(0f, 0f, -hz + bottom / 2f),
                new Vector3(fullSize.x, fullSize.y, bottom), color, setRendererColor);
            CreateBoxPart(parent, "storage_left_wall", new Vector3(-hx + wall / 2f, 0f, wallCenterZ),
                new Vector3(wall, fullSize.y, wallHeight), color, setRendererColor);
            CreateBoxPart(parent, "storage_right_wall", new Vector3(hx - wall / 2f, 0f, wallCenterZ),
                new Vector3(wall, fullSize.y, wallHeight), color, setRendererColor);
            CreateBoxPart(parent, "storage_front_wall", new Vector3(0f, -hy + wall / 2f, wallCenterZ),
                new Vector3(fullSize.x - 2f * wall, wall, wallHeight), color, setRendererColor);
            CreateBoxPart(parent, "storage_rear_wall", new Vector3(0f, hy - wall / 2f, wallCenterZ),
                new Vector3(fullSize.x - 2f * wall, wall, wallHeight), color, setRendererColor);
        }

        private static void BuildButtonHousing(
            Transform parent,
            Vector3 fullSize,
            M20AssistiveDeskConstruction construction,
            Color color,
            Action<Renderer, Color> setRendererColor)
        {
            if (construction == null)
                throw new InvalidOperationException("M20 button housing construction data is missing.");
            float hx = fullSize.x / 2f;
            float hy = fullSize.y / 2f;
            float hz = fullSize.z / 2f;
            float holeX = construction.apertureMeters.x;
            float holeY = construction.apertureMeters.y;
            float bottom = construction.bottomThicknessMeters;
            float wallHeight = fullSize.z - bottom;
            float wallCenterZ = -hz + bottom + wallHeight / 2f;
            float xStrip = (fullSize.x - holeX) / 2f;
            float yStrip = (fullSize.y - holeY) / 2f;
            CreateBoxPart(parent, "button_housing_bottom", new Vector3(0f, 0f, -hz + bottom / 2f),
                new Vector3(fullSize.x, fullSize.y, bottom), color, setRendererColor);
            CreateBoxPart(parent, "button_housing_left", new Vector3(-holeX / 2f - xStrip / 2f, 0f, wallCenterZ),
                new Vector3(xStrip, fullSize.y, wallHeight), color, setRendererColor);
            CreateBoxPart(parent, "button_housing_right", new Vector3(holeX / 2f + xStrip / 2f, 0f, wallCenterZ),
                new Vector3(xStrip, fullSize.y, wallHeight), color, setRendererColor);
            CreateBoxPart(parent, "button_housing_front", new Vector3(0f, -holeY / 2f - yStrip / 2f, wallCenterZ),
                new Vector3(holeX, yStrip, wallHeight), color, setRendererColor);
            CreateBoxPart(parent, "button_housing_rear", new Vector3(0f, holeY / 2f + yStrip / 2f, wallCenterZ),
                new Vector3(holeX, yStrip, wallHeight), color, setRendererColor);
        }

        private static Transform CreateStorageLid(
            Dictionary<string, M20AssistiveDeskEntity> entityById,
            M20AssistiveDeskArticulation hinge,
            Transform blocksRoot,
            Action<Renderer, Color> setRendererColor)
        {
            M20AssistiveDeskEntity lid = entityById["assist_storage_lid"];
            GameObject pivot = new GameObject("M20_assist_storage_lid_hinge");
            pivot.transform.SetParent(blocksRoot, false);
            pivot.transform.localPosition = ToUnityWorkspacePosition(hinge.pivotPositionMeters);
            pivot.transform.localRotation = Quaternion.identity;
            pivot.transform.localScale = Vector3.one;
            CreatePrimitivePart(
                pivot.transform,
                "storage_lid_visual",
                PrimitiveType.Cube,
                ToUnityWorkspacePosition(lid.positionMeters - hinge.pivotPositionMeters),
                lid.dimensionsMeters,
                lid.color,
                setRendererColor);
            return pivot.transform;
        }

        private static Transform CreateButtonCap(
            Dictionary<string, M20AssistiveDeskEntity> entityById,
            Transform blocksRoot,
            Action<Renderer, Color> setRendererColor)
        {
            M20AssistiveDeskEntity cap = entityById["assist_button_cap"];
            M20AssistiveDeskEntity housing = entityById["assist_button_switch"];
            GameObject capObject = new GameObject("M20_assist_button_cap_slide");
            capObject.transform.SetParent(blocksRoot, false);
            capObject.transform.localPosition = ToUnityWorkspacePosition(cap.positionMeters);
            capObject.transform.localRotation = Quaternion.identity;
            capObject.transform.localScale = Vector3.one;
            CreatePrimitivePart(
                capObject.transform,
                "button_cap_visual",
                PrimitiveType.Cylinder,
                Vector3.zero,
                cap.dimensionsMeters,
                cap.color,
                setRendererColor);
            if (housing.construction == null ||
                housing.construction.capClearanceMeters < 0f)
                throw new InvalidOperationException("M20 button cap clearance is invalid.");
            return capObject.transform;
        }

        private static M20AssistiveDeskArticulation FindArticulation(M20AssistiveDeskSceneSpec spec, string jointId)
        {
            if (spec.articulations != null)
                foreach (M20AssistiveDeskArticulation articulation in spec.articulations)
                    if (articulation != null && articulation.jointId == jointId)
                        return articulation;
            throw new InvalidOperationException("Canonical M20 articulation is missing: " + jointId);
        }

        private static GameObject CreateBoxPart(
            Transform parent,
            string name,
            Vector3 canonicalOffset,
            Vector3 canonicalDimensions,
            Color color,
            Action<Renderer, Color> setRendererColor)
        {
            return CreatePrimitivePart(parent, name, PrimitiveType.Cube,
                ToUnityWorkspacePosition(canonicalOffset), canonicalDimensions, color, setRendererColor);
        }

        private static GameObject CreatePrimitivePart(
            Transform parent,
            string name,
            PrimitiveType primitive,
            Vector3 unityLocalPosition,
            Vector3 canonicalDimensions,
            Color color,
            Action<Renderer, Color> setRendererColor)
        {
            GameObject part = GameObject.CreatePrimitive(primitive);
            part.name = name;
            part.transform.SetParent(parent, false);
            part.transform.localPosition = unityLocalPosition;
            Vector3 dimensions = ToUnityWorkspaceDimensions(canonicalDimensions);
            part.transform.localScale = primitive == PrimitiveType.Cylinder
                ? new Vector3(dimensions.x, dimensions.y / 2f, dimensions.z)
                : dimensions;
            setRendererColor(part.GetComponent<Renderer>(), color);
            return part;
        }

        private static bool IsPositive(Vector3 value)
        {
            return IsFinite(value) && value.x > 0f && value.y > 0f && value.z > 0f;
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(Color value)
        {
            return IsFinite(value.r) && IsFinite(value.g) && IsFinite(value.b) && IsFinite(value.a);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }
    }

    public sealed class M20AssistiveDeskArticulationController : MonoBehaviour
    {
        private Transform m_storageLidPivot;
        private Transform m_buttonCap;
        private Quaternion m_lidClosedRotation;
        private Vector3 m_buttonUnpressedPosition;
        private Vector3 m_buttonPressedPosition;
        private float m_openDegrees;
        private float m_closedDegrees;
        private float m_hingeAngleSign;
        private Vector3 m_hingeAxisInUnityFrame;
        private bool m_initialized;

        public bool IsInitialized => m_initialized;

        public void Initialize(
            Transform storageLidPivot,
            Transform buttonCap,
            M20AssistiveDeskArticulation hinge,
            M20AssistiveDeskArticulation slide)
        {
            if (storageLidPivot == null || buttonCap == null || hinge == null || slide == null ||
                hinge.rangeDegrees == null || slide.rangeMeters == null)
                throw new ArgumentException("M20 Unity articulations are incomplete.");

            m_storageLidPivot = storageLidPivot;
            m_buttonCap = buttonCap;
            m_lidClosedRotation = storageLidPivot.localRotation;
            m_buttonUnpressedPosition = buttonCap.localPosition;
            m_buttonPressedPosition = buttonCap.localPosition + Vector3.up *
                (slide.pressedMeters - slide.unpressedMeters);
            m_closedDegrees = hinge.closedDegrees;
            m_openDegrees = Mathf.Clamp(hinge.openDegrees, hinge.rangeDegrees.minimum, hinge.rangeDegrees.maximum);
            m_hingeAngleSign = 1f;
            m_hingeAxisInUnityFrame = MapCanonicalAxialAxisToUnity(hinge.axis);
            m_initialized = true;
            SetStorageLidOpen(false);
            SetButtonPressed(false);
        }

        public void SetStorageLidOpen(bool isOpen)
        {
            if (!m_initialized)
                return;
            float angle = isOpen ? m_openDegrees : m_closedDegrees;
            m_storageLidPivot.localRotation =
                Quaternion.AngleAxis(angle * m_hingeAngleSign, m_hingeAxisInUnityFrame) * m_lidClosedRotation;
        }

        public void SetButtonPressed(bool isPressed)
        {
            if (!m_initialized)
                return;
            m_buttonCap.localPosition = isPressed ? m_buttonPressedPosition : m_buttonUnpressedPosition;
        }

        [ContextMenu("M20/Open storage lid")]
        private void OpenStorageLidFromEditor()
        {
            SetStorageLidOpen(true);
        }

        [ContextMenu("M20/Close storage lid")]
        private void CloseStorageLidFromEditor()
        {
            SetStorageLidOpen(false);
        }

        [ContextMenu("M20/Press button")]
        private void PressButtonFromEditor()
        {
            SetButtonPressed(true);
        }

        [ContextMenu("M20/Release button")]
        private void ReleaseButtonFromEditor()
        {
            SetButtonPressed(false);
        }

        private static Vector3 MapCanonicalAxialAxisToUnity(Vector3 canonicalAxis)
        {
            // The position adapter has determinant -1. Rotation axes are
            // pseudovectors, so apply det(A) * A to preserve handed rotation.
            return new Vector3(canonicalAxis.x, -canonicalAxis.z, canonicalAxis.y).normalized;
        }
    }
}
