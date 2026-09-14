using System;
using System.Collections.Generic;
using UnityEngine;

namespace BCIIntelligentRobot.VirtualManipulation
{
    [Serializable]
    public sealed class M9VirtualBlockCatalogData
    {
        public int schemaVersion;
        public Vector3 tableSizeMeters;
        public Vector3 blockSizeMeters;
        public M9VirtualSlotDefinition[] slots;
        public M9VirtualBlockDefinition[] blocks;
    }

    [Serializable]
    public sealed class M9VirtualSlotDefinition
    {
        public int slotIndex;
        public float nominalFrequencyHz;
        public int framesPerHalfCycle;
    }

    /// <summary>
    /// Explicit scene identity and M9 mapping for one virtual manipulation
    /// target. Its TargetId is data, never derived from its Unity object.
    /// </summary>
    [Serializable]
    public sealed class M9VirtualBlockDefinition
    {
        public string targetId;
        public string semanticLabel;
        public string logicalBlockId;
        public string sourceKind;
        public Vector3 localPositionMeters;
        public Color color;
        public bool active;
        public bool selectable;
        public bool slotEligible;
    }

    public static class M9VirtualBlockCatalog
    {
        public const string ResourcesPath = "BCI/M9/virtual_blocks";
        public const string VirtualSourceKind = "virtual_block";

        private static readonly float[] ExpectedFrequenciesHz = { 7.2f, 9f, 12f };
        private static readonly int[] ExpectedFramesPerHalfCycle = { 5, 4, 3 };

        public static M9VirtualBlockCatalogData LoadFromResources()
        {
            TextAsset asset = Resources.Load<TextAsset>(ResourcesPath);
            if (asset == null)
                throw new InvalidOperationException("Missing virtual block catalog Resources/" + ResourcesPath + ".json");
            return Parse(asset.text);
        }

        public static M9VirtualBlockCatalogData Parse(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
                throw new ArgumentException("Virtual block catalog JSON is required.", nameof(json));

            M9VirtualBlockCatalogData catalog = JsonUtility.FromJson<M9VirtualBlockCatalogData>(json);
            Validate(catalog);
            return catalog;
        }

        public static void Validate(M9VirtualBlockCatalogData catalog)
        {
            if (catalog == null || catalog.schemaVersion != 1)
                throw new InvalidOperationException("Virtual block catalog schemaVersion must be 1.");
            if (catalog.tableSizeMeters.x <= 0f || catalog.tableSizeMeters.y <= 0f ||
                catalog.tableSizeMeters.z <= 0f || catalog.blockSizeMeters.x <= 0f ||
                catalog.blockSizeMeters.y <= 0f || catalog.blockSizeMeters.z <= 0f)
                throw new InvalidOperationException("Virtual table and block dimensions must be positive.");

            if (catalog.slots == null || catalog.slots.Length != ExpectedFrequenciesHz.Length)
                throw new InvalidOperationException("The catalog must define exactly the existing three SSVEP slots.");
            for (int index = 0; index < catalog.slots.Length; index++)
            {
                M9VirtualSlotDefinition slot = catalog.slots[index];
                if (slot == null || slot.slotIndex != index ||
                    !Mathf.Approximately(slot.nominalFrequencyHz, ExpectedFrequenciesHz[index]) ||
                    slot.framesPerHalfCycle != ExpectedFramesPerHalfCycle[index])
                    throw new InvalidOperationException("Virtual slot definitions must preserve slot 0/1/2 = 7.2/9/12 Hz and 5/4/3 frames.");
            }

            if (catalog.blocks == null || catalog.blocks.Length < 3)
                throw new InvalidOperationException("The virtual manipulation catalog must contain at least three blocks.");

            var targetIds = new HashSet<string>(StringComparer.Ordinal);
            var logicalBlockIds = new HashSet<string>(StringComparer.Ordinal);
            int eligibleCount = 0;
            for (int index = 0; index < catalog.blocks.Length; index++)
            {
                M9VirtualBlockDefinition block = catalog.blocks[index];
                if (block == null ||
                    string.IsNullOrWhiteSpace(block.targetId) ||
                    string.IsNullOrWhiteSpace(block.semanticLabel) ||
                    string.IsNullOrWhiteSpace(block.logicalBlockId))
                    throw new InvalidOperationException("Every virtual block needs explicit TargetId, semanticLabel and logicalBlockId values.");
                if (block.sourceKind != VirtualSourceKind)
                    throw new InvalidOperationException("Every catalog block must declare sourceKind=virtual_block.");
                if (!targetIds.Add(block.targetId))
                    throw new InvalidOperationException("Virtual block TargetIds must be unique: " + block.targetId);
                if (!logicalBlockIds.Add(block.logicalBlockId))
                    throw new InvalidOperationException("Virtual block logicalBlockIds must be unique: " + block.logicalBlockId);
                if (!IsFinite(block.localPositionMeters))
                    throw new InvalidOperationException("Virtual block local positions must be finite.");
                if (block.active && block.selectable && block.slotEligible)
                    eligibleCount++;
            }

            if (eligibleCount < 3)
                throw new InvalidOperationException("At least three active, selectable, slot-eligible blocks are required.");
        }

        private static bool IsFinite(Vector3 value)
        {
            return IsFinite(value.x) && IsFinite(value.y) && IsFinite(value.z);
        }

        private static bool IsFinite(float value)
        {
            return !float.IsNaN(value) && !float.IsInfinity(value);
        }
    }
}
