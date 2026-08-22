using BCIIntelligentRobot.VRStimulus;
using NUnit.Framework;
using UnityEngine;

namespace BCIIntelligentRobot.Tests
{
    public class BciSsvepVisualAssetTests
    {
        [Test]
        public void LegacySsvepMaterial_IsAvailableWithColorProperty()
        {
            Material material = Resources.Load<Material>("BCI/SSVEP/SSVEP_Unlit");

            Assert.That(material, Is.Not.Null);
            Assert.That(material.HasProperty(Shader.PropertyToID("_Color")), Is.True);
        }
    }
}
