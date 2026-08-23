using System;
using System.Collections.Generic;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>Tracks active selection IDs that must keep display geometry fixed.</summary>
    public sealed class BciSelectionLayoutFreezeGate
    {
        private readonly HashSet<string> m_selectionIds = new HashSet<string>(StringComparer.Ordinal);

        public bool IsFrozen => m_selectionIds.Count > 0;

        public bool Begin(string selectionId)
        {
            return !string.IsNullOrWhiteSpace(selectionId) && m_selectionIds.Add(selectionId);
        }

        public bool End(string selectionId)
        {
            return !string.IsNullOrWhiteSpace(selectionId) && m_selectionIds.Remove(selectionId);
        }
    }
}
