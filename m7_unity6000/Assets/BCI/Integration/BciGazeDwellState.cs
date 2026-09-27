using System;

namespace BCIIntelligentRobot.Integration
{
    /// <summary>
    /// Small, frame-rate independent state machine for a single head-gaze
    /// control. It deliberately has no Unity or XR dependency so the exact
    /// dwell/exit semantics can be tested in EditMode without a device.
    /// </summary>
    public sealed class BciGazeDwellState
    {
        private readonly float m_dwellSeconds;
        private string m_controlId;
        private float m_enteredAt;
        private bool m_fired;

        public BciGazeDwellState(float dwellSeconds)
        {
            if (dwellSeconds <= 0f)
                throw new ArgumentOutOfRangeException(nameof(dwellSeconds));
            m_dwellSeconds = dwellSeconds;
        }

        public string ControlId => m_controlId;
        public float DwellSeconds => m_dwellSeconds;

        public float Progress(float now)
        {
            if (string.IsNullOrEmpty(m_controlId))
                return 0f;
            if (m_fired)
                return 1f;
            float elapsed = now - m_enteredAt;
            if (elapsed <= 0f)
                return 0f;
            return Math.Min(1f, elapsed / m_dwellSeconds);
        }

        /// <summary>
        /// Returns true exactly once after the same enabled control has been
        /// continuously gazed at for the configured dwell duration. A null,
        /// disabled, or different control starts a fresh dwell interval.
        /// </summary>
        public bool Update(string controlId, bool enabled, float now)
        {
            if (!enabled || string.IsNullOrEmpty(controlId))
            {
                Reset();
                return false;
            }

            if (!string.Equals(controlId, m_controlId, StringComparison.Ordinal))
            {
                m_controlId = controlId;
                m_enteredAt = now;
                m_fired = false;
                return false;
            }

            if (m_fired || now - m_enteredAt < m_dwellSeconds)
                return false;

            m_fired = true;
            return true;
        }

        public void Reset()
        {
            m_controlId = null;
            m_enteredAt = 0f;
            m_fired = false;
        }
    }
}
