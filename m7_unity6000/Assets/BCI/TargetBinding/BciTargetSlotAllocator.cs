using System;
using System.Collections.Generic;

namespace BCIIntelligentRobot.Vision
{
    public enum BciSlotUpdateKind
    {
        Ignored,
        Assigned,
        Retained,
        Released,
        Full
    }

    public readonly struct BciSlotUpdate
    {
        public BciSlotUpdate(BciSlotUpdateKind kind, string targetId, string className, int slotIndex)
        {
            Kind = kind;
            TargetId = targetId;
            ClassName = className;
            SlotIndex = slotIndex;
        }

        public BciSlotUpdateKind Kind { get; }
        public string TargetId { get; }
        public string ClassName { get; }
        public int SlotIndex { get; }
        public bool HasSlot => SlotIndex >= 0;
    }

    /// <summary>
    /// Deterministic first-confirmed-first-assigned allocator for the three BCI stimulus slots.
    /// A TargetId retains its slot through temporary loss and only releases it on Lost.
    /// </summary>
    public sealed class BciTargetSlotAllocator
    {
        public const int SlotCount = 3;

        private readonly Dictionary<string, int> m_slotByTargetId = new Dictionary<string, int>(StringComparer.Ordinal);

        public BciSlotUpdate Update(string targetId, string className, StableTargetState state)
        {
            if (string.IsNullOrWhiteSpace(targetId))
                return new BciSlotUpdate(BciSlotUpdateKind.Ignored, targetId, className, -1);

            if (state == StableTargetState.Lost)
            {
                if (!m_slotByTargetId.TryGetValue(targetId, out int releasedSlot))
                    return new BciSlotUpdate(BciSlotUpdateKind.Ignored, targetId, className, -1);

                m_slotByTargetId.Remove(targetId);
                return new BciSlotUpdate(BciSlotUpdateKind.Released, targetId, className, releasedSlot);
            }

            if (m_slotByTargetId.TryGetValue(targetId, out int retainedSlot))
                return new BciSlotUpdate(BciSlotUpdateKind.Retained, targetId, className, retainedSlot);

            // A target without a localized Active anchor cannot claim a slot while missing.
            if (state != StableTargetState.Active)
                return new BciSlotUpdate(BciSlotUpdateKind.Ignored, targetId, className, -1);

            for (int slot = 0; slot < SlotCount; slot++)
            {
                if (!IsSlotAssigned(slot))
                {
                    m_slotByTargetId.Add(targetId, slot);
                    return new BciSlotUpdate(BciSlotUpdateKind.Assigned, targetId, className, slot);
                }
            }

            return new BciSlotUpdate(BciSlotUpdateKind.Full, targetId, className, -1);
        }

        public bool TryGetSlot(string targetId, out int slotIndex)
        {
            return m_slotByTargetId.TryGetValue(targetId, out slotIndex);
        }

        private bool IsSlotAssigned(int slotIndex)
        {
            foreach (int assignedSlot in m_slotByTargetId.Values)
            {
                if (assignedSlot == slotIndex)
                    return true;
            }

            return false;
        }
    }
}
