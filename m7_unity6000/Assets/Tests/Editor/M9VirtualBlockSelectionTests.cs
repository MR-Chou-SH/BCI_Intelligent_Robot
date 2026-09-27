using System.Collections.Generic;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Vision;
using BCIIntelligentRobot.VirtualManipulation;
using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;
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
        public void M13_6StartupBlocksUseTheCanonicalRuntimeSizeAndTableSupportedCenter()
        {
            M9VirtualBlockCatalogData catalog = M9VirtualBlockCatalog.LoadFromResources();
            Vector3 visualSize = M9VirtualManipulationBootstrap.M13_6StartupBlockVisualSize;
            Assert.That(visualSize.x, Is.EqualTo(0.056f).Within(0.000001f));
            Assert.That(visualSize.y, Is.EqualTo(0.056f).Within(0.000001f));
            Assert.That(visualSize.z, Is.EqualTo(0.056f).Within(0.000001f));

            foreach (M9VirtualBlockDefinition block in catalog.blocks)
            {
                Vector3 startupPosition = M9VirtualManipulationBootstrap
                    .CanonicalM13_6StartupBlockPosition(block.localPositionMeters);
                Assert.That(startupPosition.x, Is.EqualTo(block.localPositionMeters.x));
                Assert.That(startupPosition.y, Is.EqualTo(visualSize.y * 0.5f).Within(0.000001f));
                Assert.That(startupPosition.z, Is.EqualTo(block.localPositionMeters.z));
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

        [Test]
        public void AuthoritativeSnapshotUsesTargetIdentityDespiteLegacyVisualOrder()
        {
            var cameraObject = new GameObject("M13.10e authoritative snapshot camera");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var bindingObject = new GameObject("M13.10e authoritative snapshot binding");
            var contentObject = new GameObject("M13.10e authoritative snapshot content");
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();

            StableWorldAnchorSnapshot blue = Anchor("m9-vblock-blue-01", "blue_block", 0.32f);
            StableWorldAnchorSnapshot yellow = Anchor("m9-vblock-yellow-01", "yellow_block", 0.10f);
            StableWorldAnchorSnapshot green = Anchor("m9-vblock-green-01", "green_block", -0.10f);
            StableWorldAnchorSnapshot red = Anchor("m9-vblock-red-01", "red_block", -0.32f);

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                Assert.That(binding.InitializeVirtualTargets(contentObject.transform, 0.32f), Is.True);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);

                // Deliberately reproduce the deployed legacy order.
                Assert.That(binding.SetVirtualTargetCandidates(new[] { blue, yellow, green, red }), Is.True);
                Assert.That(binding.ActivateGroup("legacy-visual-order", new[] { blue, yellow, green }), Is.True);

                BciSelectionSnapshot snapshot;
                string rejectionReason;
                Assert.That(binding.TryCreateAuthoritativeSelectionSnapshot(
                    "m13.10e-red-first", 1,
                    new[]
                    {
                        Candidate(0, "m9-vblock-red-01", 7.2f),
                        Candidate(1, "m9-vblock-green-01", 9f),
                        Candidate(2, "m9-vblock-yellow-01", 12f),
                    },
                    out snapshot,
                    out rejectionReason), Is.True, rejectionReason);

                Assert.That(snapshot.ResolveClassIndex(0).Target.TargetId, Is.EqualTo("m9-vblock-red-01"));
                Assert.That(snapshot.ResolveClassIndex(1).Target.TargetId, Is.EqualTo("m9-vblock-green-01"));
                Assert.That(snapshot.ResolveClassIndex(2).Target.TargetId, Is.EqualTo("m9-vblock-yellow-01"));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-red-01"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-green-01"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-yellow-01"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-blue-01"), Is.EqualTo(BciCandidateVisualState.Inactive));

                var coordinator = new BciSelectionCoordinator();
                Assert.That(coordinator.Open("m13.10e-selection", snapshot).IsAccepted, Is.True);
                BciSelectionTransportResult resolved = coordinator.Resolve(
                    "m13.10e-selection", 0, "m13.10e-red-first", 1);
                Assert.That(resolved.IsAccepted, Is.True);
                Assert.That(resolved.Target.TargetId, Is.EqualTo("m9-vblock-red-01"));
                Assert.That(binding.SetGroupSlotSelected("legacy-visual-order", 0, true), Is.True);
                Assert.That(binding.GetCandidateVisualState("m9-vblock-red-01"), Is.EqualTo(BciCandidateVisualState.Selected));

                // The PC reuses one frozen candidate snapshot for every
                // within-batch selection. Re-opening that same snapshot must
                // not erase the already provisional BLUE state.
                BciSelectionSnapshot repeatedSnapshot;
                Assert.That(binding.TryCreateAuthoritativeSelectionSnapshot(
                    "m13.10e-red-first", 1,
                    new[]
                    {
                        Candidate(0, "m9-vblock-red-01", 7.2f),
                        Candidate(1, "m9-vblock-green-01", 9f),
                        Candidate(2, "m9-vblock-yellow-01", 12f),
                    },
                    out repeatedSnapshot,
                    out rejectionReason), Is.True, rejectionReason);
                Assert.That(binding.GetCandidateVisualState("m9-vblock-red-01"), Is.EqualTo(BciCandidateVisualState.Selected));
                Assert.That(binding.IsSlotActiveCandidate(0), Is.False);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(contentObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        [Test]
        public void AuthoritativeSnapshotSupportsDynamicNextBatchAndRejectsUnknownIdentity()
        {
            var cameraObject = new GameObject("M13.10e dynamic snapshot camera");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            var bindingObject = new GameObject("M13.10e dynamic snapshot binding");
            var contentObject = new GameObject("M13.10e dynamic snapshot content");
            var binding = bindingObject.AddComponent<BciSsvepTargetBinding>();

            try
            {
                binding.ConfigureLayout(
                    BciSsvepLayoutMode.ViewLockedHud,
                    BciSsvepDisplayLayout.DefaultHudLocalCenter,
                    BciSsvepDisplayLayout.HudHorizontalSpacingMeters,
                    BciSsvepDisplayLayout.HudStimulusSizeMeters);
                Assert.That(binding.InitializeVirtualTargets(contentObject.transform, 0.32f), Is.True);
                Assert.That(binding.EnableBatchGroupMode(), Is.True);

                StableWorldAnchorSnapshot blue = Anchor("m9-vblock-blue-01", "blue_block", 0.32f);
                StableWorldAnchorSnapshot yellow = Anchor("m9-vblock-yellow-01", "yellow_block", 0.10f);
                StableWorldAnchorSnapshot green = Anchor("m9-vblock-green-01", "green_block", -0.10f);
                StableWorldAnchorSnapshot red = Anchor("m9-vblock-red-01", "red_block", -0.32f);
                Assert.That(binding.SetVirtualTargetCandidates(new[] { blue, yellow, green, red }), Is.True);
                Assert.That(binding.ActivateGroup("dynamic-group", new[] { blue, yellow, green }), Is.True);

                BciSelectionSnapshot firstSnapshot;
                string rejectionReason;
                Assert.That(binding.TryCreateAuthoritativeSelectionSnapshot(
                    "m13.10e-first", 1,
                    new[]
                    {
                        Candidate(0, "m9-vblock-red-01", 7.2f),
                        Candidate(1, "m9-vblock-green-01", 9f),
                        Candidate(2, "m9-vblock-yellow-01", 12f),
                    },
                    out firstSnapshot,
                    out rejectionReason), Is.True, rejectionReason);

                BciSelectionSnapshot nextSnapshot;
                Assert.That(binding.TryCreateAuthoritativeSelectionSnapshot(
                    "m13.10e-next", 2,
                    new[]
                    {
                        Candidate(0, "m9-vblock-yellow-01", 7.2f),
                        Candidate(1, "m9-vblock-blue-01", 9f),
                        InactiveCandidate(2, 12f),
                    },
                    out nextSnapshot,
                    out rejectionReason), Is.True, rejectionReason);
                Assert.That(nextSnapshot.ResolveClassIndex(0).Target.TargetId, Is.EqualTo("m9-vblock-yellow-01"));
                Assert.That(nextSnapshot.ResolveClassIndex(1).Target.TargetId, Is.EqualTo("m9-vblock-blue-01"));
                Assert.That(nextSnapshot.ResolveClassIndex(2).Rejection, Is.EqualTo(BciSelectionRejection.EmptySlot));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-yellow-01"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-blue-01"), Is.EqualTo(BciCandidateVisualState.Available));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-red-01"), Is.EqualTo(BciCandidateVisualState.Inactive));
                Assert.That(binding.GetCandidateVisualState("m9-vblock-green-01"), Is.EqualTo(BciCandidateVisualState.Inactive));

                Assert.That(binding.TryCreateAuthoritativeSelectionSnapshot(
                    "m13.10e-invalid", 3,
                    new[]
                    {
                        Candidate(0, "m9-vblock-unknown-99", 7.2f),
                        Candidate(1, "m9-vblock-blue-01", 9f),
                        InactiveCandidate(2, 12f),
                    },
                    out BciSelectionSnapshot rejected,
                    out rejectionReason), Is.False);
                Assert.That(rejected, Is.Null);
                Assert.That(rejectionReason, Is.EqualTo("authoritative_target_not_in_active_local_catalog"));
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(contentObject);
                UnityEngine.Object.DestroyImmediate(bindingObject);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        private static BciSelectionCandidatePayload Candidate(int slotIndex, string targetId, float frequencyHz)
        {
            return new BciSelectionCandidatePayload
            {
                slotIndex = slotIndex,
                targetId = targetId,
                logicalBlockId = CanonicalLogicalBlockId(targetId),
                nominalFrequencyHz = frequencyHz,
                active = true,
            };
        }

        private static string CanonicalLogicalBlockId(string targetId)
        {
            if (targetId == "m9-vblock-red-01")
                return "block_sim_01";
            if (targetId == "m9-vblock-green-01")
                return "block_sim_02";
            if (targetId == "m9-vblock-blue-01")
                return "block_sim_03";
            if (targetId == "m9-vblock-yellow-01")
                return "block_sim_04";
            return "unknown-logical-block";
        }

        private static BciSelectionCandidatePayload InactiveCandidate(int slotIndex, float frequencyHz)
        {
            return new BciSelectionCandidatePayload
            {
                slotIndex = slotIndex,
                targetId = "__inactive_target_" + slotIndex,
                logicalBlockId = "__inactive_slot_" + slotIndex,
                nominalFrequencyHz = frequencyHz,
                active = false,
            };
        }

        private static StableWorldAnchorSnapshot Anchor(string targetId, string className, float x)
        {
            return new StableWorldAnchorSnapshot(
                targetId,
                className,
                StableTargetState.Active,
                new Vector3(x, 0f, 2f),
                0.9f,
                new TargetBoundingBox(x * 10f + 100f, 20f, 30f, 30f),
                0d,
                1d);
        }
    }
}
