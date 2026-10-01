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
        public M20AssistiveDeskRobot robot;
        public string runtimeSceneId;
        public int layoutSeed;
        public string layoutSnapshotJson;
    }

    [Serializable]
    public sealed class M20AssistiveDeskTable
    {
        public Vector3 dimensionsMeters;
        public float mujocoTopSurfaceWorldZMeters;
        public float topSurfaceAtTableLocalZMeters;
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
        public string[] affordanceTags;
        public Vector3 positionMeters;
        public Vector3 dimensionsMeters;
        public float yawDegrees;
        public Color color;
        public M20AssistiveDeskConstruction construction;
        public M20AssistiveDeskPlacement[] placements;
    }

    [Serializable]
    public sealed class M20AssistiveDeskRobot
    {
        public string entityId;
        public string modelSource;
        public Vector3 basePositionMeters;
        public bool identityPreserved;
    }

    [Serializable]
    public sealed class M20AssistiveDeskPlacement
    {
        public string targetId;
        public Vector3 positionMeters;
        public float yawDegrees;
    }

    [Serializable]
    public sealed class M20SceneLayoutSnapshot
    {
        public int schemaVersion;
        public string sceneId;
        public string templateId;
        public int randomSeed;
        public string createdUtc;
        public string randomizationMethod;
        public bool fallbackUsed;
        public M20SceneCoordinateFrame coordinateFrame;
        public M20SceneLayoutTable table;
        public string[] candidateOrderFarToNearLeftToRight;
        public M20SceneLayoutObject[] objects;
    }

    [Serializable]
    public sealed class M20SceneCoordinateFrame
    {
        public string id;
        public string unit;
        public string origin;
        public string x;
        public string y;
        public string z;
        public string unityTableRootLocalPositionAdapter;
        public string mujocoWorldPositionAdapter;
    }

    [Serializable]
    public sealed class M20SceneLayoutTable
    {
        public Vector3 dimensionsMeters;
        public float mujocoTopSurfaceWorldZMeters;
    }

    [Serializable]
    public sealed class M20SceneLayoutObject
    {
        public string semanticId;
        public string targetId;
        public string logicalBlockId;
        public string objectType;
        public string role;
        public string sourceKind;
        public bool selectable;
        public bool fixedPose;
        public Vector3 positionMeters;
        public Vector3 dimensionsMeters;
        public float yawDegrees;
        public string[] affordanceTags;
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

        private static readonly string[] RandomizedObjectIds =
        {
            "assist_storage_box",
            "assist_phone",
            "assist_button_switch",
            "assist_medicine_box",
            "assist_wireless_charger"
        };

        private static readonly Vector2[] FallbackPositions =
        {
            new Vector2(-0.28f, 0.13f),
            new Vector2(0.00f, 0.12f),
            new Vector2(0.28f, 0.03f),
            new Vector2(-0.24f, 0.015f),
            new Vector2(0.25f, 0.17f)
        };

        private const float LayoutEdgeClearanceMeters = 0.025f;
        private const float ObjectClearanceMeters = 0.02f;
        private const float RobotBaseKeepOutRadiusMeters = 0.085f;
        private const float GraspSourceMinX = -0.24f;
        private const float GraspSourceMaxX = 0.24f;
        private const float GraspSourceMinY = 0f;
        private const float GraspSourceMaxY = 0.14f;
        private const int MaximumLayoutAttempts = 96;
        private const int MaximumPositionAttempts = 256;

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
                blocks = new M9VirtualBlockDefinition[spec.candidateOrderFarToNearLeftToRight.Length]
            };

            for (int index = 0; index < spec.candidateOrderFarToNearLeftToRight.Length; index++)
            {
                M20AssistiveDeskEntity entity = entityById[spec.candidateOrderFarToNearLeftToRight[index]];
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

            targetRoots = new List<GameObject>(spec.candidateOrderFarToNearLeftToRight.Length);
            var candidates = new List<StableWorldAnchorSnapshot>(spec.candidateOrderFarToNearLeftToRight.Length);
            foreach (string semanticId in spec.candidateOrderFarToNearLeftToRight)
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

        public static M20AssistiveDeskSceneSpec CreateRuntimeSceneSpec(
            M20AssistiveDeskSceneSpec canonicalSpec,
            int? deterministicSeed = null,
            string sceneId = null)
        {
            Validate(canonicalSpec);
            int seed = deterministicSeed.HasValue ? deterministicSeed.Value : CreateFreshSeed();
            if (seed < 0)
                throw new ArgumentOutOfRangeException(nameof(deterministicSeed), "M20 layout seed must be non-negative.");

            string runtimeSceneId = string.IsNullOrWhiteSpace(sceneId)
                ? "m20-" + Guid.NewGuid().ToString("N")
                : sceneId.Trim();
            if (runtimeSceneId == canonicalSpec.sceneId)
                throw new InvalidOperationException("Runtime M20 sceneId must differ from the template ID.");

            System.Random random = new System.Random(seed);
            M20AssistiveDeskSceneSpec runtimeSpec = null;
            for (int layoutAttempt = 0; layoutAttempt < MaximumLayoutAttempts; layoutAttempt++)
            {
                M20AssistiveDeskSceneSpec candidate = CloneSpec(canonicalSpec);
                if (!TryRandomizePositions(candidate, random))
                    continue;
                candidate.candidateOrderFarToNearLeftToRight = ComputeSpatialOrder(candidate);
                try
                {
                    Validate(candidate);
                    runtimeSpec = candidate;
                    break;
                }
                catch (InvalidOperationException)
                {
                    // Reject the entire candidate layout; never accept a
                    // partial or silently invalid sample.
                }
            }

            bool fallbackUsed = runtimeSpec == null;
            if (fallbackUsed)
            {
                runtimeSpec = CloneSpec(canonicalSpec);
                var entityById = BuildEntityMap(runtimeSpec);
                for (int index = 0; index < RandomizedObjectIds.Length; index++)
                {
                    Vector3 position = entityById[RandomizedObjectIds[index]].positionMeters;
                    Vector2 fallback = FallbackPositions[index];
                    entityById[RandomizedObjectIds[index]].positionMeters = new Vector3(fallback.x, fallback.y, position.z);
                }
                TranslateArticulatedPartsAndPlacements(runtimeSpec, canonicalSpec);
                runtimeSpec.candidateOrderFarToNearLeftToRight = ComputeSpatialOrder(runtimeSpec);
                Validate(runtimeSpec);
                Debug.LogWarning("M20_SCENE_LAYOUT bounded_sampling_fallback seed=" + seed +
                    " fallback=validated_fixed_positions", null);
            }

            runtimeSpec.runtimeSceneId = runtimeSceneId;
            runtimeSpec.layoutSeed = seed;
            M20SceneLayoutSnapshot snapshot = BuildSnapshot(runtimeSpec, seed, runtimeSceneId, fallbackUsed);
            runtimeSpec.layoutSnapshotJson = JsonUtility.ToJson(snapshot);
            Debug.Log("M20_SCENE_LAYOUT_SNAPSHOT " + runtimeSpec.layoutSnapshotJson);
            Debug.Log("M20_SCENE_LAYOUT frozen scene_id=" + runtimeSceneId + " seed=" + seed +
                " objects=" + snapshot.objects.Length + " fallback=" + fallbackUsed +
                " order=" + string.Join(",", runtimeSpec.candidateOrderFarToNearLeftToRight));
            return runtimeSpec;
        }

        public static bool TryGetCommandLineSeedOverride(string[] arguments, out int seed)
        {
            seed = 0;
            if (arguments == null)
                return false;
            for (int index = 0; index < arguments.Length - 1; index++)
            {
                if (!string.Equals(arguments[index], "-m20-layout-seed", StringComparison.Ordinal))
                    continue;
                if (!int.TryParse(arguments[index + 1], System.Globalization.NumberStyles.Integer,
                    System.Globalization.CultureInfo.InvariantCulture, out seed) || seed < 0)
                    throw new ArgumentException("-m20-layout-seed must be a non-negative integer.");
                return true;
            }
            return false;
        }

        public static Vector3 ToUnityWorkspacePosition(Vector3 canonicalPositionMeters)
        {
            return new Vector3(-canonicalPositionMeters.x, canonicalPositionMeters.z, -canonicalPositionMeters.y);
        }

        public static Vector3 ToUnityWorkspaceDimensions(Vector3 canonicalDimensionsMeters)
        {
            return new Vector3(canonicalDimensionsMeters.x, canonicalDimensionsMeters.z, canonicalDimensionsMeters.y);
        }

        private static int CreateFreshSeed()
        {
            unchecked
            {
                return (int)(DateTime.UtcNow.Ticks ^ Guid.NewGuid().GetHashCode()) & 0x7fffffff;
            }
        }

        private static M20AssistiveDeskSceneSpec CloneSpec(M20AssistiveDeskSceneSpec source)
        {
            M20AssistiveDeskSceneSpec clone = JsonUtility.FromJson<M20AssistiveDeskSceneSpec>(JsonUtility.ToJson(source));
            if (clone == null)
                throw new InvalidOperationException("Unable to clone the canonical M20 scene spec.");
            return clone;
        }

        private static bool TryRandomizePositions(M20AssistiveDeskSceneSpec spec, System.Random random)
        {
            var entityById = BuildEntityMap(spec);
            M20AssistiveDeskEntity zone = entityById["assist_user_zone"];
            Vector3 tableSize = spec.table.dimensionsMeters;
            Vector3 robotBase = spec.robot != null ? spec.robot.basePositionMeters : new Vector3(0f, 0.4f, 0f);
            var occupiedCenters = new List<Vector3> { zone.positionMeters };
            var occupiedSizes = new List<Vector3> { zone.dimensionsMeters };

            foreach (string semanticId in RandomizedObjectIds)
            {
                M20AssistiveDeskEntity entity = entityById[semanticId];
                Vector3 size = entity.dimensionsMeters;
                float minX = -tableSize.x * 0.5f + size.x * 0.5f + LayoutEdgeClearanceMeters;
                float maxX = tableSize.x * 0.5f - size.x * 0.5f - LayoutEdgeClearanceMeters;
                float minY = Mathf.Max(
                    -tableSize.y * 0.5f + size.y * 0.5f + LayoutEdgeClearanceMeters,
                    zone.positionMeters.y + zone.dimensionsMeters.y * 0.5f + size.y * 0.5f + ObjectClearanceMeters);
                float maxY = tableSize.y * 0.5f - size.y * 0.5f - LayoutEdgeClearanceMeters;
                if (semanticId == "assist_medicine_box" || semanticId == "assist_phone")
                {
                    minX = Mathf.Max(minX, GraspSourceMinX);
                    maxX = Mathf.Min(maxX, GraspSourceMaxX);
                    minY = Mathf.Max(minY, GraspSourceMinY);
                    maxY = Mathf.Min(maxY, GraspSourceMaxY);
                }
                if (minX > maxX || minY > maxY)
                    return false;

                bool placed = false;
                for (int attempt = 0; attempt < MaximumPositionAttempts; attempt++)
                {
                    float x = minX + (float)random.NextDouble() * (maxX - minX);
                    float y = minY + (float)random.NextDouble() * (maxY - minY);
                    Vector3 candidate = new Vector3(x, y, entity.positionMeters.z);
                    if (!HasObjectClearance(candidate, size, zone.positionMeters, zone.dimensionsMeters) ||
                        !IsClearOfRobotBase(candidate, size, robotBase))
                        continue;

                    bool clear = true;
                    for (int index = 0; index < occupiedCenters.Count; index++)
                    {
                        if (HasObjectClearance(candidate, size, occupiedCenters[index], occupiedSizes[index]))
                            continue;
                        clear = false;
                        break;
                    }
                    if (!clear)
                        continue;

                    entity.positionMeters = candidate;
                    occupiedCenters.Add(candidate);
                    occupiedSizes.Add(size);
                    placed = true;
                    break;
                }
                if (!placed)
                    return false;
            }

            TranslateArticulatedPartsAndPlacements(spec, null);
            return true;
        }

        private static void TranslateArticulatedPartsAndPlacements(
            M20AssistiveDeskSceneSpec runtimeSpec,
            M20AssistiveDeskSceneSpec canonicalSpec)
        {
            var runtimeEntities = BuildEntityMap(runtimeSpec);
            var canonicalEntities = canonicalSpec != null ? BuildEntityMap(canonicalSpec) : null;
            Vector3 storageDelta = runtimeEntities["assist_storage_box"].positionMeters -
                (canonicalEntities != null
                    ? canonicalEntities["assist_storage_box"].positionMeters
                    : new Vector3(0f, 0.10f, 0.0375f));
            Vector3 buttonDelta = runtimeEntities["assist_button_switch"].positionMeters -
                (canonicalEntities != null
                    ? canonicalEntities["assist_button_switch"].positionMeters
                    : new Vector3(-0.24f, -0.03f, 0.011f));
            M20AssistiveDeskEntity storageLid = runtimeEntities["assist_storage_lid"];
            M20AssistiveDeskEntity buttonCap = runtimeEntities["assist_button_cap"];
            storageLid.positionMeters += storageDelta;
            buttonCap.positionMeters += buttonDelta;

            foreach (M20AssistiveDeskArticulation articulation in runtimeSpec.articulations)
            {
                if (articulation.jointId == "assist_storage_lid_hinge")
                    articulation.pivotPositionMeters += storageDelta;
            }

            foreach (M20AssistiveDeskEntity entity in runtimeSpec.entities)
            {
                if (entity.placements == null)
                    continue;
                for (int index = 0; index < entity.placements.Length; index++)
                {
                    M20AssistiveDeskPlacement placement = entity.placements[index];
                    Vector3 targetDelta = Vector3.zero;
                    if (placement.targetId == "assist_storage_box")
                        targetDelta = storageDelta;
                    else if (placement.targetId == "assist_phone")
                        targetDelta = runtimeEntities["assist_phone"].positionMeters -
                            (canonicalEntities != null
                                ? canonicalEntities["assist_phone"].positionMeters
                                : new Vector3(0.24f, 0.10f, 0.004f));
                    else if (placement.targetId == "assist_button_switch")
                        targetDelta = buttonDelta;
                    else if (placement.targetId == "assist_wireless_charger")
                        targetDelta = runtimeEntities["assist_wireless_charger"].positionMeters -
                            (canonicalEntities != null
                                ? canonicalEntities["assist_wireless_charger"].positionMeters
                                : new Vector3(0.24f, -0.03f, 0.006f));
                    placement.positionMeters += targetDelta;
                }
            }
        }

        private static string[] ComputeSpatialOrder(M20AssistiveDeskSceneSpec spec)
        {
            var entities = BuildEntityMap(spec);
            var sorted = new List<M20AssistiveDeskEntity>(ExpectedCandidateOrder.Length);
            foreach (string semanticId in ExpectedCandidateOrder)
                sorted.Add(entities[semanticId]);
            sorted.Sort((left, right) =>
            {
                int byY = right.positionMeters.y.CompareTo(left.positionMeters.y);
                if (byY != 0)
                    return byY;
                int byX = left.positionMeters.x.CompareTo(right.positionMeters.x);
                return byX != 0 ? byX : string.CompareOrdinal(left.semanticId, right.semanticId);
            });

            var ordered = new List<string>(sorted.Count);
            int start = 0;
            while (start < sorted.Count)
            {
                float rowAnchorY = sorted[start].positionMeters.y;
                int end = start + 1;
                while (end < sorted.Count && Mathf.Abs(sorted[end].positionMeters.y - rowAnchorY) <= 0.02f)
                    end++;
                sorted.Sort(start, end - start, Comparer<M20AssistiveDeskEntity>.Create((left, right) =>
                {
                    int byX = left.positionMeters.x.CompareTo(right.positionMeters.x);
                    return byX != 0 ? byX : string.CompareOrdinal(left.semanticId, right.semanticId);
                }));
                for (int index = start; index < end; index++)
                    ordered.Add(sorted[index].semanticId);
                start = end;
            }
            return ordered.ToArray();
        }

        private static M20SceneLayoutSnapshot BuildSnapshot(
            M20AssistiveDeskSceneSpec spec,
            int seed,
            string runtimeSceneId,
            bool fallbackUsed)
        {
            var objects = new M20SceneLayoutObject[spec.entities.Length];
            for (int index = 0; index < spec.entities.Length; index++)
            {
                M20AssistiveDeskEntity entity = spec.entities[index];
                objects[index] = new M20SceneLayoutObject
                {
                    semanticId = entity.semanticId,
                    targetId = entity.targetId ?? string.Empty,
                    logicalBlockId = entity.logicalBlockId ?? string.Empty,
                    objectType = string.IsNullOrWhiteSpace(entity.semanticLabel) ? entity.sourceKind : entity.semanticLabel,
                    role = entity.role,
                    sourceKind = entity.sourceKind,
                    selectable = entity.selectable,
                    fixedPose = entity.semanticId == "assist_user_zone",
                    positionMeters = entity.positionMeters,
                    dimensionsMeters = entity.dimensionsMeters,
                    yawDegrees = entity.yawDegrees,
                    affordanceTags = entity.affordanceTags ?? new string[0]
                };
            }
            return new M20SceneLayoutSnapshot
            {
                schemaVersion = 1,
                sceneId = runtimeSceneId,
                templateId = spec.sceneId,
                randomSeed = seed,
                createdUtc = DateTime.UtcNow.ToString("O"),
                randomizationMethod = fallbackUsed ? "validated_fallback_v1" : "bounded_uniform_rejection_v1",
                fallbackUsed = fallbackUsed,
                coordinateFrame = new M20SceneCoordinateFrame
                {
                    id = "m20_table_local_to_mujoco_v1",
                    unit = "meter",
                    origin = "center of the tabletop top surface",
                    x = "user visual right",
                    y = "away from user toward robot",
                    z = "up",
                    unityTableRootLocalPositionAdapter = "(-x, z, -y)",
                    mujocoWorldPositionAdapter = "(x, y, tableTopWorldZ + z)"
                },
                table = new M20SceneLayoutTable
                {
                    dimensionsMeters = spec.table.dimensionsMeters,
                    mujocoTopSurfaceWorldZMeters = spec.table.mujocoTopSurfaceWorldZMeters
                },
                candidateOrderFarToNearLeftToRight = spec.candidateOrderFarToNearLeftToRight,
                objects = objects
            };
        }

        private static void Validate(M20AssistiveDeskSceneSpec spec)
        {
            if (spec == null || spec.schemaVersion != 1 || spec.sceneId != "m20_daily_assistive_desk")
                throw new InvalidOperationException("Unsupported canonical M20 scene schema or sceneId.");
            if (spec.table == null || !IsPositive(spec.table.dimensionsMeters) ||
                !IsFinite(spec.table.mujocoTopSurfaceWorldZMeters) || spec.robot == null ||
                !IsFinite(spec.robot.basePositionMeters))
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
            var expectedIds = new HashSet<string>(ExpectedCandidateOrder, StringComparer.Ordinal);
            if (new HashSet<string>(spec.candidateOrderFarToNearLeftToRight, StringComparer.Ordinal).SetEquals(expectedIds) == false)
                throw new InvalidOperationException("M20 candidate order must contain each canonical target exactly once.");
            var entities = BuildEntityMap(spec);
            Vector3 zonePosition = entities["assist_user_zone"].positionMeters;
            if ((zonePosition - new Vector3(0f, -0.19f, 0.001f)).sqrMagnitude > 0.0000000001f)
                throw new InvalidOperationException("M20 USER ZONE pose is fixed and may not be randomized.");

            Vector3 tableSize = spec.table.dimensionsMeters;
            var candidateCenters = new List<Vector3>(ExpectedCandidateOrder.Length);
            var candidateSizes = new List<Vector3>(ExpectedCandidateOrder.Length);
            foreach (string semanticId in ExpectedCandidateOrder)
            {
                M20AssistiveDeskEntity entity = entities[semanticId];
                Vector3 center = entity.positionMeters;
                Vector3 size = entity.dimensionsMeters;
                bool zone = semanticId == "assist_user_zone";
                if (Mathf.Abs(center.x) + size.x / 2f > tableSize.x / 2f + 0.000001f ||
                    Mathf.Abs(center.y) + size.y / 2f > tableSize.y / 2f + 0.000001f)
                    throw new InvalidOperationException("M20 candidate exceeds table bounds: " + semanticId + ".");
                if (!zone && (Mathf.Abs(center.x) + size.x / 2f > tableSize.x / 2f - LayoutEdgeClearanceMeters + 0.000001f ||
                    Mathf.Abs(center.y) + size.y / 2f > tableSize.y / 2f - LayoutEdgeClearanceMeters + 0.000001f))
                    throw new InvalidOperationException("M20 candidate violates tabletop edge clearance: " + semanticId + ".");
                if ((semanticId == "assist_medicine_box" || semanticId == "assist_phone") &&
                    (center.x < GraspSourceMinX - 0.000001f || center.x > GraspSourceMaxX + 0.000001f ||
                     center.y < GraspSourceMinY - 0.000001f || center.y > GraspSourceMaxY + 0.000001f))
                    throw new InvalidOperationException("M20 grasp source lies outside the tested M9 reachability envelope: " + semanticId + ".");
                if (!zone && !IsClearOfRobotBase(center, size, spec.robot.basePositionMeters))
                    throw new InvalidOperationException("M20 candidate overlaps the robot base exclusion region: " + semanticId + ".");
                candidateCenters.Add(center);
                candidateSizes.Add(size);
            }
            for (int left = 0; left < candidateCenters.Count; left++)
                for (int right = left + 1; right < candidateCenters.Count; right++)
                    if (!HasObjectClearance(candidateCenters[left], candidateSizes[left], candidateCenters[right], candidateSizes[right]))
                        throw new InvalidOperationException("M20 candidates overlap or violate clearance: " +
                            ExpectedCandidateOrder[left] + " / " + ExpectedCandidateOrder[right] + ".");

            string[] spatialOrder = ComputeSpatialOrder(spec);
            for (int index = 0; index < spatialOrder.Length; index++)
                if (spec.candidateOrderFarToNearLeftToRight[index] != spatialOrder[index])
                    throw new InvalidOperationException("M20 candidate order does not match randomized spatial positions.");
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

        private static bool HasObjectClearance(Vector3 center, Vector3 size, Vector3 otherCenter, Vector3 otherSize)
        {
            return Mathf.Abs(center.x - otherCenter.x) >= (size.x + otherSize.x) / 2f + ObjectClearanceMeters ||
                   Mathf.Abs(center.y - otherCenter.y) >= (size.y + otherSize.y) / 2f + ObjectClearanceMeters ||
                   Mathf.Abs(center.z - otherCenter.z) >= (size.z + otherSize.z) / 2f + ObjectClearanceMeters;
        }

        private static bool IsClearOfRobotBase(Vector3 center, Vector3 size, Vector3 robotBase)
        {
            float objectRadius = Mathf.Sqrt(size.x * size.x + size.y * size.y) / 2f;
            float requiredDistance = RobotBaseKeepOutRadiusMeters + objectRadius + ObjectClearanceMeters;
            float deltaX = center.x - robotBase.x;
            float deltaY = center.y - robotBase.y;
            return deltaX * deltaX + deltaY * deltaY >= requiredDistance * requiredDistance;
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
