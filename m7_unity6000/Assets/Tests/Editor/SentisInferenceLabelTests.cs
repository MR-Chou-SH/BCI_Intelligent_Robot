using NUnit.Framework;
using PassthroughCameraSamples.MultiObjectDetection;

namespace BCIIntelligentRobot.Tests
{
    public class SentisInferenceLabelTests
    {
        [Test]
        public void NormalizeModelLabel_RemovesOnlyWindowsLineTerminator()
        {
            Assert.That(SentisInferenceUiManager.NormalizeModelLabel("bottle\r"), Is.EqualTo("bottle"));
            Assert.That(SentisInferenceUiManager.NormalizeModelLabel("cell phone "), Is.EqualTo("cell phone "));
        }
    }
}
