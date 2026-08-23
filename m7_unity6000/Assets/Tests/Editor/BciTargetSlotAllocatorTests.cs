using BCIIntelligentRobot.Vision;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciTargetSlotAllocatorTests
    {
        [Test]
        public void ActiveTargets_UseFirstConfirmedLowestFreeSlots()
        {
            var allocator = new BciTargetSlotAllocator();

            Assert.That(allocator.Update("target-1", "cup", StableTargetState.Active).SlotIndex, Is.EqualTo(0));
            Assert.That(allocator.Update("target-2", "bottle", StableTargetState.Active).SlotIndex, Is.EqualTo(1));
            Assert.That(allocator.Update("target-3", "book", StableTargetState.Active).SlotIndex, Is.EqualTo(2));
            Assert.That(
                allocator.Update("target-4", "mouse", StableTargetState.Active).Kind,
                Is.EqualTo(BciSlotUpdateKind.Full));
        }

        [Test]
        public void TemporaryMissing_RetainsSlot_AndLostReleasesIt()
        {
            var allocator = new BciTargetSlotAllocator();
            allocator.Update("target-1", "cup", StableTargetState.Active);
            allocator.Update("target-2", "bottle", StableTargetState.Active);

            BciSlotUpdate retained = allocator.Update("target-1", "cup", StableTargetState.TemporarilyMissing);
            Assert.That(retained.Kind, Is.EqualTo(BciSlotUpdateKind.Retained));
            Assert.That(retained.SlotIndex, Is.EqualTo(0));

            BciSlotUpdate released = allocator.Update("target-1", "cup", StableTargetState.Lost);
            Assert.That(released.Kind, Is.EqualTo(BciSlotUpdateKind.Released));
            Assert.That(released.SlotIndex, Is.EqualTo(0));

            BciSlotUpdate replacement = allocator.Update("target-3", "book", StableTargetState.Active);
            Assert.That(replacement.Kind, Is.EqualTo(BciSlotUpdateKind.Assigned));
            Assert.That(replacement.SlotIndex, Is.EqualTo(0));
        }

        [Test]
        public void SelectionSnapshot_ResolvesClassIndexToTargetAndIsStable()
        {
            var targets = new[]
            {
                new BciSelectionTarget(0, "target-1", "cup", StableTargetState.Active),
                new BciSelectionTarget(1, "target-2", "bottle", StableTargetState.Active),
                new BciSelectionTarget(2, "target-3", "book", StableTargetState.Active)
            };
            var snapshot = new BciSelectionSnapshot(targets);
            targets[0] = new BciSelectionTarget(0, "replacement", "mouse", StableTargetState.Active);

            Assert.That(snapshot.ResolveClassIndex(0).Target.TargetId, Is.EqualTo("target-1"));
            Assert.That(snapshot.ResolveClassIndex(1).Target.TargetId, Is.EqualTo("target-2"));
            Assert.That(snapshot.ResolveClassIndex(2).Target.TargetId, Is.EqualTo("target-3"));
        }

        [Test]
        public void SelectionSnapshot_RejectsInvalidClassEmptySlotAndInvalidTarget()
        {
            var snapshot = new BciSelectionSnapshot(new[]
            {
                new BciSelectionTarget(0, "target-1", "cup", StableTargetState.Active),
                default(BciSelectionTarget),
                new BciSelectionTarget(2, "target-3", "book", StableTargetState.TemporarilyMissing)
            });

            Assert.That(snapshot.ResolveClassIndex(-1).Rejection, Is.EqualTo(BciSelectionRejection.InvalidClassIndex));
            Assert.That(snapshot.ResolveClassIndex(3).Rejection, Is.EqualTo(BciSelectionRejection.InvalidClassIndex));
            Assert.That(snapshot.ResolveClassIndex(1).Rejection, Is.EqualTo(BciSelectionRejection.EmptySlot));
            Assert.That(snapshot.ResolveClassIndex(2).Rejection, Is.EqualTo(BciSelectionRejection.TargetInvalid));
        }

        [Test]
        public void SelectionSnapshot_PreservesTheStableWorldPositionCapturedAtOpen()
        {
            var source = new[]
            {
                new BciSelectionTarget(0, new StableWorldAnchorSnapshot(
                    "target-1", "cup", StableTargetState.Active, new Vector3(1f, 2f, 3f))),
                new BciSelectionTarget(1, "target-2", "bottle", StableTargetState.Active),
                new BciSelectionTarget(2, "target-3", "book", StableTargetState.Active)
            };
            var snapshot = new BciSelectionSnapshot(source);
            source[0] = new BciSelectionTarget(0, new StableWorldAnchorSnapshot(
                "replacement", "cup", StableTargetState.Active, new Vector3(9f, 9f, 9f)));

            BciSelectionTarget resolved = snapshot.ResolveClassIndex(0).Target;
            Assert.That(resolved.TargetId, Is.EqualTo("target-1"));
            Assert.That(resolved.HasWorldPosition, Is.True);
            Assert.That(resolved.WorldPosition, Is.EqualTo(new Vector3(1f, 2f, 3f)));
        }
    }
}
