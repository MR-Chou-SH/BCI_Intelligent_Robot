using System.Collections.Generic;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using BCIIntelligentRobot.VirtualManipulation;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public sealed class M9VirtualBlockSelectionTests
    {
        [Test]
        public void CatalogDefinesFourExplicitVirtualTargetsAndTheFrozenThreeSlots()
        {
            M9VirtualBlockCatalogData catalog = M9VirtualBlockCatalog.LoadFromResources();
            Assert.That(catalog.blocks.Length, Is.EqualTo(4));
            Assert.That(catalog.slots.Length, Is.EqualTo(3));
            Assert.That(catalog.slots[0].nominalFrequencyHz, Is.EqualTo(7.2f));
            Assert.That(catalog.slots[1].nominalFrequencyHz, Is.EqualTo(9f));
            Assert.That(catalog.slots[2].nominalFrequencyHz, Is.EqualTo(12f));
            Assert.That(catalog.slots[0].framesPerHalfCycle, Is.EqualTo(5));
            Assert.That(catalog.slots[1].framesPerHalfCycle, Is.EqualTo(4));
            Assert.That(catalog.slots[2].framesPerHalfCycle, Is.EqualTo(3));

            var targetIds = new HashSet<string>();
            var logicalIds = new HashSet<string>();
            foreach (M9VirtualBlockDefinition block in catalog.blocks)
            {
                Assert.That(block.sourceKind, Is.EqualTo(M9VirtualBlockCatalog.VirtualSourceKind));
                Assert.That(block.active && block.selectable && block.slotEligible, Is.True);
                Assert.That(block.targetId, Does.StartWith("m9-vblock-"));
                Assert.That(block.targetId, Does.Not.Contain("obj_"));
                Assert.That(targetIds.Add(block.targetId), Is.True);
                Assert.That(logicalIds.Add(block.logicalBlockId), Is.True);
                Assert.That(block.logicalBlockId, Does.StartWith("block_"));
            }
        }

        [Test]
        public void SyntheticClassUsesVirtualCandidatesFrozenM8BatchAndExactM9Identity()
        {
            M9VirtualBlockCatalogData catalog = M9VirtualBlockCatalog.LoadFromResources();
            var candidates = new List<StableWorldAnchorSnapshot>();
            var virtualBlocks = new List<GameObject>();
            var cameraObject = new GameObject("M9 virtual selection test camera");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var bindingObject = new GameObject("M9 virtual selection test binding");
            var contentObject = new GameObject("M9 virtual selection test content");
            try
            {
                for (int index = 0; index < catalog.blocks.Length; index++)
                {
                    M9VirtualBlockDefinition block = catalog.blocks[index];
                    var virtualBlockObject = new GameObject("M9 virtual block component test");
                    virtualBlocks.Add(virtualBlockObject);
                    virtualBlockObject.transform.position = block.localPositionMeters;
                    M9VirtualBlockTarget identity = virtualBlockObject.AddComponent<M9VirtualBlockTarget>();
                    identity.Configure(block);
                    Assert.That(identity.TargetId, Is.EqualTo(block.targetId));
                    Assert.That(identity.LogicalBlockId, Is.EqualTo(block.logicalBlockId));
                    Assert.That(identity.SourceKind, Is.EqualTo(M9VirtualBlockCatalog.VirtualSourceKind));
                    candidates.Add(identity.CreateAnchorSnapshot());
                }

                BciSsvepTargetBinding binding = bindingObject.AddComponent<BciSsvepTargetBinding>();
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                Assert.That(binding.InitializeVirtualTargets(contentObject.transform, 0.32f), Is.True);
                Assert.That(binding.IsVirtualTargetSourceActive, Is.True);

                var groups = new BciTargetGroupCoordinator();
                binding.HudCandidatesChanged += groups.UpdateCandidatePool;
                groups.GroupActivated += group => binding.ActivateGroup(group.GroupId, group.Targets);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);
                Assert.That(binding.SetVirtualTargetCandidates(candidates), Is.True);
                Assert.That(groups.TryActivateNextGroup(), Is.True);
                Assert.That(groups.ActiveGroup.Value.Targets.Count, Is.EqualTo(3));

                BciSelectionSnapshot frozenSnapshot = binding.CreateSelectionSnapshot();
                var selection = new BciSelectionCoordinator();
                BciTargetSelectionResult accepted = default(BciTargetSelectionResult);
                bool groupAccepted = false;
                selection.TargetSelected += result =>
                {
                    accepted = result;
                    groupAccepted = groups.TryAccept(result);
                };

                Assert.That(selection.Open("m9-virtual-selection-001", frozenSnapshot).IsAccepted, Is.True);
                BciSelectionTransportResult resolved = selection.Resolve("m9-virtual-selection-001", 1);
                Assert.That(resolved.IsAccepted, Is.True);
                Assert.That(accepted.PredictedClassIndex, Is.EqualTo(1));
                Assert.That(accepted.SlotIndex, Is.EqualTo(1));
                Assert.That(accepted.TargetId, Is.EqualTo("m9-vblock-green-01"));
                Assert.That(accepted.SemanticLabel, Is.EqualTo("green_block"));
                Assert.That(accepted.HasWorldPosition, Is.True);
                Assert.That(accepted.Provenance, Is.EqualTo(BciTargetSelectionResult.FrozenSnapshotProvenance));
                Assert.That(groupAccepted, Is.True);

                Assert.That(groups.TryConfirmCurrentGroup(out ConfirmedTargetBatch batch), Is.True);
                ConfirmedTargetBatchPayload payload = ConfirmedTargetBatchPayload.From(batch);
                Assert.That(payload.provenance, Is.EqualTo(ConfirmedTargetBatch.ProvenanceValue));
                Assert.That(payload.selections.Length, Is.EqualTo(1));
                Assert.That(payload.selections[0].targetId, Is.EqualTo("m9-vblock-green-01"));
                Assert.That(payload.selections[0].semanticLabel, Is.EqualTo("green_block"));
                Assert.That(payload.selections[0].slotIndex, Is.EqualTo(1));
                Assert.That(payload.selections[0].provenance,
                    Is.EqualTo(BciTargetSelectionResult.FrozenSnapshotProvenance));
            }
            finally
            {
                foreach (GameObject virtualBlock in virtualBlocks)
                    Object.DestroyImmediate(virtualBlock);
                Object.DestroyImmediate(contentObject);
                Object.DestroyImmediate(bindingObject);
                Object.DestroyImmediate(cameraObject);
            }
        }
    }
}
