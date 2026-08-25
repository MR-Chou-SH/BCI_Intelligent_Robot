using System;
using System.Collections.Generic;
using UnityEngine;

namespace BCIIntelligentRobot.Vision
{
    public readonly struct BciSelectionTarget
    {
        public BciSelectionTarget(int slotIndex, string targetId, string className, StableTargetState state)
            : this(slotIndex, targetId, className, state, default(Vector3), false)
        {
        }

        public BciSelectionTarget(int slotIndex, StableWorldAnchorSnapshot anchor)
            : this(slotIndex, anchor.TargetId, anchor.ClassName, anchor.State, anchor.WorldPosition, true)
        {
        }

        private BciSelectionTarget(
            int slotIndex,
            string targetId,
            string className,
            StableTargetState state,
            Vector3 worldPosition,
            bool hasWorldPosition)
        {
            SlotIndex = slotIndex;
            TargetId = targetId;
            ClassName = className;
            State = state;
            WorldPosition = worldPosition;
            HasWorldPosition = hasWorldPosition;
        }

        public int SlotIndex { get; }
        public string TargetId { get; }
        public string ClassName { get; }
        public StableTargetState State { get; }
        public Vector3 WorldPosition { get; }
        public bool HasWorldPosition { get; }

        /// <summary>
        /// Keeps the same frozen target facts while exposing a temporary
        /// selection-state overlay to one snapshot.
        /// </summary>
        public BciSelectionTarget WithState(StableTargetState state)
        {
            return new BciSelectionTarget(
                SlotIndex,
                TargetId,
                ClassName,
                state,
                WorldPosition,
                HasWorldPosition);
        }
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
