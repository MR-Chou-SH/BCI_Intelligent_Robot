using System;
using BCIIntelligentRobot.Vision;
using UnityEngine;

namespace BCIIntelligentRobot.VirtualManipulation
{
    /// <summary>
    /// Stable, explicit identity attached to one scene block. The existing
    /// frozen M8 contract receives only its stable anchor snapshot.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class M9VirtualBlockTarget : MonoBehaviour
    {
        [SerializeField] private string m_targetId;
        [SerializeField] private string m_semanticLabel;
        [SerializeField] private string m_logicalBlockId;
        [SerializeField] private string m_sourceKind = M9VirtualBlockCatalog.VirtualSourceKind;
        [SerializeField] private bool m_active = true;
        [SerializeField] private bool m_selectable = true;
        [SerializeField] private bool m_slotEligible = true;

        public string TargetId => m_targetId;
        public string SemanticLabel => m_semanticLabel;
        public string LogicalBlockId => m_logicalBlockId;
        public string SourceKind => m_sourceKind;
        public bool IsActive => m_active && gameObject.activeInHierarchy;
        public bool IsSelectable => m_selectable;
        public bool IsSlotEligible => m_slotEligible;
        public bool IsBciCandidate => IsActive && IsSelectable && IsSlotEligible;

        public void Configure(M9VirtualBlockDefinition definition)
        {
            if (definition == null)
                throw new ArgumentNullException(nameof(definition));

            m_targetId = definition.targetId;
            m_semanticLabel = definition.semanticLabel;
            m_logicalBlockId = definition.logicalBlockId;
            m_sourceKind = definition.sourceKind;
            m_active = definition.active;
            m_selectable = definition.selectable;
            m_slotEligible = definition.slotEligible;
        }

        public StableWorldAnchorSnapshot CreateAnchorSnapshot()
        {
            if (string.IsNullOrWhiteSpace(m_targetId))
                throw new InvalidOperationException("Virtual block TargetId has not been configured.");

            return new StableWorldAnchorSnapshot(
                m_targetId,
                m_semanticLabel,
                IsBciCandidate ? StableTargetState.Active : StableTargetState.TemporarilyMissing,
                transform.position);
        }
    }
}
