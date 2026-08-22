using System;
using System.Collections.Generic;

namespace BCIIntelligentRobot.Vision
{
    /// <summary>
    /// Small allowlist boundary between raw detector classes and BCI candidates.
    /// It intentionally defaults to reject so background classes never reach tracking or stimulation.
    /// </summary>
    public sealed class BciTargetEligibilityFilter
    {
        public static readonly string[] DefaultAllowedClasses =
        {
            "cup",
            "bottle",
            "book",
            "mouse",
            "cell phone",
            "keyboard"
        };

        private readonly HashSet<string> m_allowedClasses;

        public BciTargetEligibilityFilter(IEnumerable<string> allowedClasses = null)
        {
            m_allowedClasses = new HashSet<string>(StringComparer.Ordinal);
            IEnumerable<string> source = allowedClasses ?? DefaultAllowedClasses;
            foreach (string className in source)
            {
                string normalized = NormalizeClassName(className);
                if (!string.IsNullOrEmpty(normalized))
                    m_allowedClasses.Add(normalized);
            }
        }

        public bool IsEligible(string className)
        {
            return m_allowedClasses.Contains(NormalizeClassName(className));
        }

        public static string NormalizeClassName(string className)
        {
            if (string.IsNullOrWhiteSpace(className))
                return string.Empty;

            string withSpaces = className.Trim().Replace('_', ' ');
            string[] words = withSpaces.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            return string.Join(" ", words).ToLowerInvariant();
        }
    }
}
