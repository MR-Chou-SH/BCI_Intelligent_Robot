using System;
using System.Collections.Generic;

namespace BCIIntelligentRobot.Vision
{
    public readonly struct BciSelectionTarget
    {
        public BciSelectionTarget(int slotIndex, string targetId, string className, StableTargetState state)
        {
            SlotIndex = slotIndex;
            TargetId = targetId;
            ClassName = className;
            State = state;
        }

        public int SlotIndex { get; }
        public string TargetId { get; }
        public string ClassName { get; }
        public StableTargetState State { get; }
    }

    public enum BciSelectionRejection
    {
        None,
        InvalidClassIndex,
        EmptySlot,
        TargetInvalid
    }

    public readonly struct BciSelectionResolution
    {
        internal BciSelectionResolution(BciSelectionTarget target, BciSelectionRejection rejection)
        {
            Target = target;
            Rejection = rejection;
        }

        public BciSelectionTarget Target { get; }
        public BciSelectionRejection Rejection { get; }
        public bool IsAccepted => Rejection == BciSelectionRejection.None;
    }

    /// <summary>Immutable slot-to-target view captured for one EEG decision.</summary>
    public sealed class BciSelectionSnapshot
    {
        private readonly BciSelectionTarget[] m_targets;

        public BciSelectionSnapshot(IReadOnlyList<BciSelectionTarget> targets)
        {
            m_targets = new BciSelectionTarget[BciTargetSlotAllocator.SlotCount];
            for (int slot = 0; slot < m_targets.Length; slot++)
                m_targets[slot] = targets[slot];
        }

        public const int SlotCount = BciTargetSlotAllocator.SlotCount;

        public BciSelectionResolution ResolveClassIndex(int classIndex)
        {
            if (classIndex < 0 || classIndex >= SlotCount)
                return Reject(BciSelectionRejection.InvalidClassIndex);

            BciSelectionTarget target = m_targets[classIndex];
            if (string.IsNullOrWhiteSpace(target.TargetId))
                return Reject(BciSelectionRejection.EmptySlot);
            if (target.State != StableTargetState.Active)
                return Reject(BciSelectionRejection.TargetInvalid);
            return new BciSelectionResolution(target, BciSelectionRejection.None);
        }

        private static BciSelectionResolution Reject(BciSelectionRejection reason)
        {
            return new BciSelectionResolution(default(BciSelectionTarget), reason);
        }
    }
}
