using System.Reflection;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.VRStimulus;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Tests
{
    public sealed class BciM19ResearchPresentationTests
    {
        private GameObject m_cameraObject;
        private GameObject m_controllerObject;
        private GameObject m_hudObject;
        private GameObject m_stimulusRoot;
        private Camera m_camera;
        private MultiTargetStimulusController m_stimulus;

        [SetUp]
        public void SetUp()
        {
            m_cameraObject = new GameObject("Research presentation test camera");
            m_cameraObject.tag = "MainCamera";
            m_camera = m_cameraObject.AddComponent<Camera>();
            m_camera.transform.SetPositionAndRotation(
                new Vector3(1f, 1.6f, 2f), Quaternion.Euler(0f, 37f, 0f));

            m_controllerObject = new GameObject("Research presentation test controller");
            BciM19ResearchAcquisitionController controller =
                m_controllerObject.AddComponent<BciM19ResearchAcquisitionController>();
            MethodInfo build = typeof(BciM19ResearchAcquisitionController).GetMethod(
                "BuildPresentation", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(build, Is.Not.Null);
            build.Invoke(controller, new object[] { m_camera });

            FieldInfo statusField = typeof(BciM19ResearchAcquisitionController).GetField(
                "m_statusText", BindingFlags.Instance | BindingFlags.NonPublic);
            FieldInfo stimulusField = typeof(BciM19ResearchAcquisitionController).GetField(
                "m_stimulusController", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(statusField, Is.Not.Null);
            Assert.That(stimulusField, Is.Not.Null);
            m_hudObject = ((Text)statusField.GetValue(controller)).transform.root.gameObject;
            m_stimulus = (MultiTargetStimulusController)stimulusField.GetValue(controller);
            m_stimulusRoot = m_stimulus.transform.parent.gameObject;
        }

        [TearDown]
        public void TearDown()
        {
            if (m_stimulusRoot != null)
            {
                foreach (Renderer renderer in m_stimulusRoot.GetComponentsInChildren<Renderer>())
                    if (renderer.sharedMaterial != null)
                        Object.DestroyImmediate(renderer.sharedMaterial);
                Object.DestroyImmediate(m_stimulusRoot);
            }
            if (m_hudObject != null)
                Object.DestroyImmediate(m_hudObject);
            if (m_controllerObject != null)
                Object.DestroyImmediate(m_controllerObject);
            if (m_cameraObject != null)
                Object.DestroyImmediate(m_cameraObject);
        }

        [Test]
        public void HudAndAllThreeSlotsFaceTheHeadWithoutMirroredScale()
        {
            Quaternion expected = Quaternion.LookRotation(m_camera.transform.forward, Vector3.up);
            Assert.That(Quaternion.Angle(m_hudObject.transform.rotation, expected), Is.LessThan(0.01f));
            Assert.That(Quaternion.Angle(m_stimulusRoot.transform.rotation, expected), Is.LessThan(0.01f));
            Vector3 towardHead = (m_camera.transform.position - m_hudObject.transform.position).normalized;
            Assert.That(Vector3.Dot(-m_hudObject.transform.forward, towardHead), Is.GreaterThan(0.999f));
            Assert.That(m_hudObject.transform.lossyScale.x, Is.GreaterThan(0f));
            Assert.That(m_stimulusRoot.transform.lossyScale.x, Is.GreaterThan(0f));

            for (int slot = 0; slot < 3; slot++)
            {
                Transform target = m_stimulusRoot.transform.Find("M19_ResearchStimulus_Slot_" + slot);
                Assert.That(target, Is.Not.Null);
                Assert.That(Quaternion.Angle(target.rotation, expected), Is.LessThan(0.01f));
                Assert.That(target.lossyScale.x, Is.GreaterThan(0f));
            }
        }

        [Test]
        public void ResearchUsesIdenticalBlackWhiteStimuliAndFrozenSlotTiming()
        {
            Assert.That(m_stimulus.IsVisualFlickerEnabled, Is.True);
            MultiTargetStimulusController.TargetRuntimeSnapshot[] targets =
                m_stimulus.GetTargetRuntimeSnapshots();
            Assert.That(targets.Length, Is.EqualTo(3));

            FieldInfo frequenciesField = typeof(BciM19ResearchAcquisitionController).GetField(
                "FrequenciesHz", BindingFlags.Static | BindingFlags.NonPublic);
            Assert.That(frequenciesField, Is.Not.Null);
            float[] frequenciesHz = (float[])frequenciesField.GetValue(null);
            int[] expectedHalfCycleFrames = { 5, 4, 3 };
            float[] expectedFrequenciesHz = { 7.2f, 9f, 12f };
            MethodInfo applyStates = typeof(MultiTargetStimulusController).GetMethod(
                "ApplyStates", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(applyStates, Is.Not.Null);

            for (int slot = 0; slot < 3; slot++)
            {
                Assert.That(targets[slot].TargetIndex, Is.EqualTo(slot));
                Assert.That(targets[slot].FramesPerHalfCycle, Is.EqualTo(expectedHalfCycleFrames[slot]));
                Assert.That(targets[slot].PhaseOffsetFrames, Is.Zero);
                Assert.That(frequenciesHz[slot], Is.EqualTo(expectedFrequenciesHz[slot]));
                Assert.That(72f / (2f * targets[slot].FramesPerHalfCycle),
                    Is.EqualTo(expectedFrequenciesHz[slot]).Within(0.001f));
                Assert.That(m_stimulusRoot.transform.Find("M19_ResearchStimulus_Slot_" + slot), Is.Not.Null);
            }

            applyStates.Invoke(m_stimulus, new object[] { 0 });
            AssertAllStimulusColors(Color.white);
            applyStates.Invoke(m_stimulus, new object[] { 5 });
            AssertAllStimulusColors(Color.black);

            Assert.That(m_hudObject.transform.Find("M19_ResearchYellowLabel"), Is.Null);
            Assert.That(m_hudObject.transform.Find("M19_ResearchBlueLabel"), Is.Null);
            Assert.That(m_hudObject.transform.Find("M19_ResearchGreenLabel"), Is.Null);
        }

        private void AssertAllStimulusColors(Color expected)
        {
            for (int slot = 0; slot < 3; slot++)
            {
                Renderer renderer = m_stimulusRoot.transform
                    .Find("M19_ResearchStimulus_Slot_" + slot).GetComponent<Renderer>();
                var properties = new MaterialPropertyBlock();
                renderer.GetPropertyBlock(properties);
                Color actual = properties.GetColor("_Color");
                Assert.That(actual.r, Is.EqualTo(expected.r).Within(0.001f), "slot " + slot + " red");
                Assert.That(actual.g, Is.EqualTo(expected.g).Within(0.001f), "slot " + slot + " green");
                Assert.That(actual.b, Is.EqualTo(expected.b).Within(0.001f), "slot " + slot + " blue");
                Assert.That(actual.a, Is.EqualTo(expected.a).Within(0.001f), "slot " + slot + " alpha");
            }
        }
    }
}
