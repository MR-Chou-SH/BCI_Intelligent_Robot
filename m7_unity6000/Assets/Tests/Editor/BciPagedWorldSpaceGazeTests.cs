using System;
using System.Collections.Generic;
using System.Reflection;
using BCIIntelligentRobot.Integration;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Tests
{
    public sealed class BciPagedWorldSpaceGazeTests
    {
        private static readonly List<GameObject> s_retaggedMainCameras =
            new List<GameObject>();

        [Test]
        public void HudPanelIsWorldLockedWhileReticleRemainsHeadLocked()
        {
            GameObject cameraObject = CreateMainCamera();
            GameObject host = new GameObject("M16_TestHudHost");
            BciPagedQueueHud hud = host.AddComponent<BciPagedQueueHud>();
            try
            {
                InvokeBuildCanvas(hud, new Vector3(0f, 1.2f, 2f));
                Transform panel = hud.WorldSpaceCanvasTransform;
                Transform reticle = hud.GazeInteractor.ReticleTransform;
                Assert.That(panel, Is.Not.Null);
                Assert.That(panel.parent, Is.Null, "HUD panel must not be camera-parented.");
                Assert.That(reticle, Is.Not.Null);
                Assert.That(reticle.parent, Is.SameAs(cameraObject.transform),
                    "Only the reticle may be head-locked.");

                Vector3 panelPosition = panel.position;
                Quaternion panelRotation = panel.rotation;
                cameraObject.transform.SetPositionAndRotation(
                    new Vector3(1.1f, 1.6f, -0.4f),
                    Quaternion.Euler(12f, 137f, 0f));

                Assert.That(panel.position, Is.EqualTo(panelPosition));
                Assert.That(panel.rotation, Is.EqualTo(panelRotation));
            }
            finally
            {
                DestroyTestObject(hud, host, cameraObject);
            }
        }

        [Test]
        public void PagedHudTextUsesStableQuestFacingRotationAndPagePosition()
        {
            GameObject cameraObject = CreateMainCamera();
            GameObject host = new GameObject("M16_TestTextHost");
            BciPagedQueueHud hud = host.AddComponent<BciPagedQueueHud>();
            try
            {
                InvokeBuildCanvas(hud, new Vector3(0f, 1.2f, 2f));
                Transform root = hud.WorldSpaceCanvasTransform;
                Transform page = root.Find("M16_Text_Page");
                Assert.That(page, Is.Not.Null);
                Assert.That(page.localRotation, Is.EqualTo(Quaternion.Euler(0f, 180f, 0f)));
                Assert.That(page.GetComponent<RectTransform>().anchoredPosition.y, Is.EqualTo(155f));
                Assert.That(page.GetComponent<Text>().text, Does.Not.Contain("Selection Queue:"));
                Transform trigger = root.Find("M16_EegTriggerBand/M16_Text_Button_eeg_trigger");
                Assert.That(trigger, Is.Not.Null);
                Assert.That(trigger.GetComponent<Text>().text, Does.Contain("START EEG SELECTION"));
                Assert.That(trigger.parent.GetComponent<RectTransform>().sizeDelta.x, Is.GreaterThan(700f));
                Assert.That(trigger.parent.GetComponent<RectTransform>().anchoredPosition.y,
                    Is.GreaterThan(page.GetComponent<RectTransform>().anchoredPosition.y));
                Assert.That(hud.GazeControlCount, Is.EqualTo(5));
                Assert.That(hud.GazeControlsReady, Is.True);
                hud.Refresh();
                hud.Refresh();
                Assert.That(page.localRotation, Is.EqualTo(Quaternion.Euler(0f, 180f, 0f)));

                string[] buttonNames =
                {
                    "M16_Button_Next_>/M16_Text_Button_next",
                    "M16_Button_<Previous/M16_Text_Button_previous",
                    "M16_Button_Submit/M16_Text_Button_submit",
                    "M16_Button_Undo_Last/M16_Text_Button_undo"
                };
                for (int index = 0; index < buttonNames.Length; index++)
                {
                    Transform buttonText = root.Find(buttonNames[index]);
                    Assert.That(buttonText, Is.Not.Null, buttonNames[index]);
                    Assert.That(buttonText.localRotation, Is.EqualTo(Quaternion.Euler(0f, 180f, 0f)));
                }

                // The generated transform is assigned absolutely. Re-reading
                // it after repeated runtime refreshes cannot accumulate turns.
                Assert.That(page.localEulerAngles.y, Is.EqualTo(180f).Within(0.01f));
            }
            finally
            {
                DestroyTestObject(hud, host, cameraObject);
            }
        }

        [Test]
        public void GazeDwellSwitchResetAndReentryUseDedicatedControls()
        {
            GameObject cameraObject = CreateMainCamera();
            GameObject host = new GameObject("M16_TestGazeHost");
            BciPagedQueueHud hud = host.AddComponent<BciPagedQueueHud>();
            GameObject firstObject = null;
            GameObject secondObject = null;
            try
            {
                InvokeBuildCanvas(hud, new Vector3(10f, 10f, 10f));
                int firstActivations = 0;
                int secondActivations = 0;
                Vector3 firstPosition = new Vector3(0f, 0f, 2f);
                Vector3 secondPosition = new Vector3(0.65f, 0f, 2f);
                BciPagedGazeControl first = CreateControl(
                    hud, "first", firstPosition, () => firstActivations++);
                BciPagedGazeControl second = CreateControl(
                    hud, "second", secondPosition, () => secondActivations++);
                firstObject = first.gameObject;
                secondObject = second.gameObject;

                Assert.That(first.IsEnabled, Is.True,
                    "First gaze control fixture must be enabled before raycast.");
                Assert.That(second.IsEnabled, Is.True,
                    "Second gaze control fixture must be enabled before raycast.");
                Assert.That(first.IsColliderEnabled, Is.True,
                    "First gaze control collider must be enabled before raycast.");
                Assert.That(second.IsColliderEnabled, Is.True,
                    "Second gaze control collider must be enabled before raycast.");
                Assert.That(Camera.main, Is.SameAs(cameraObject.GetComponent<Camera>()));

                cameraObject.transform.rotation = LookAt(firstPosition);
                AssertRayHitsControl(first);
                ProcessGaze(hud.GazeInteractor, 0.0f);
                ProcessGaze(hud.GazeInteractor, 0.4f);

                // A -> B must discard A's partial dwell. B cannot activate at
                // the old elapsed time and needs a complete fresh interval.
                cameraObject.transform.rotation = LookAt(secondPosition);
                AssertRayHitsControl(second);
                ProcessGaze(hud.GazeInteractor, 0.5f);
                ProcessGaze(hud.GazeInteractor, 1.29f);
                Assert.That(secondActivations, Is.EqualTo(0));
                ProcessGaze(hud.GazeInteractor, 1.31f);
                Assert.That(secondActivations, Is.EqualTo(1));

                // Continuous gaze is one activation per entry.
                ProcessGaze(hud.GazeInteractor, 2.5f);
                Assert.That(secondActivations, Is.EqualTo(1));

                // Exit and re-entry re-arms the same control.
                cameraObject.transform.rotation = Quaternion.LookRotation(Vector3.left, Vector3.up);
                ProcessGaze(hud.GazeInteractor, 2.6f);
                cameraObject.transform.rotation = LookAt(secondPosition);
                ProcessGaze(hud.GazeInteractor, 2.7f);
                ProcessGaze(hud.GazeInteractor, 3.51f);
                Assert.That(secondActivations, Is.EqualTo(2));

                // A disabled target cannot activate, even after a full dwell.
                second.SetEnabled(false);
                ProcessGaze(hud.GazeInteractor, 4.0f);
                ProcessGaze(hud.GazeInteractor, 5.0f);
                Assert.That(secondActivations, Is.EqualTo(2));
                Assert.That(firstActivations, Is.EqualTo(0));
            }
            finally
            {
                DestroyTestObject(secondObject, firstObject, hud, host, cameraObject);
            }
        }

        [Test]
        public void NonM16ColliderCannotActivateGazeCommand()
        {
            GameObject cameraObject = CreateMainCamera();
            GameObject host = new GameObject("M16_TestForeignColliderHost");
            BciPagedQueueHud hud = host.AddComponent<BciPagedQueueHud>();
            GameObject foreignObject = null;
            try
            {
                InvokeBuildCanvas(hud, new Vector3(10f, 10f, 10f));
                foreignObject = GameObject.CreatePrimitive(PrimitiveType.Cube);
                foreignObject.name = "M9_BlockOrSsvepPanelCollider";
                foreignObject.transform.position = new Vector3(0f, 0f, 1.5f);
                int activations = 0;
                cameraObject.transform.rotation = Quaternion.identity;

                ProcessGaze(hud.GazeInteractor, 0.0f);
                ProcessGaze(hud.GazeInteractor, 1.0f);

                Assert.That(activations, Is.EqualTo(0));
                Assert.That(hud.GazeInteractor.DwellState.ControlId, Is.Null);
            }
            finally
            {
                DestroyTestObject(foreignObject, hud, host, cameraObject);
            }
        }

        [Test]
        public void TriggerBandStaysLatchedUntilPhysicalGazeExitAndReentry()
        {
            GameObject cameraObject = CreateMainCamera();
            GameObject host = new GameObject("M19_TestTriggerHost");
            BciPagedQueueHud hud = host.AddComponent<BciPagedQueueHud>();
            try
            {
                InvokeBuildCanvas(hud, new Vector3(0f, 0f, 2f));
                Transform triggerTransform = hud.WorldSpaceCanvasTransform.Find("M16_EegTriggerBand");
                Assert.That(triggerTransform, Is.Not.Null);
                Button button = triggerTransform.GetComponent<Button>();
                BciPagedGazeControl trigger = triggerTransform.GetComponent<BciPagedGazeControl>();
                int activations = 0;
                trigger.Initialize(hud, "eeg_trigger", () => activations++, () => button.interactable,
                    requireExitToRearm: true, trackWhileDisabled: true, dwellSeconds: 1.5f);
                trigger.SetEnabled(true);
                cameraObject.transform.rotation = LookAt(triggerTransform.position);
                AssertRayHitsControl(trigger);

                ProcessGaze(hud.GazeInteractor, 0f);
                ProcessGaze(hud.GazeInteractor, 0.79f);
                Assert.That(activations, Is.EqualTo(0));
                ProcessGaze(hud.GazeInteractor, 1.51f);
                Assert.That(activations, Is.EqualTo(1));
                Assert.That(trigger.IsWaitingForGazeExit, Is.True);

                trigger.SetEnabled(false);
                Assert.That(trigger.CanReceiveGaze, Is.True,
                    "The trigger collider must remain observable so gaze exit can re-arm it.");
                ProcessGaze(hud.GazeInteractor, 1.8f);
                trigger.SetEnabled(true);
                ProcessGaze(hud.GazeInteractor, 1.9f);
                ProcessGaze(hud.GazeInteractor, 2.8f);
                Assert.That(activations, Is.EqualTo(1), "Holding gaze must not start a second trial.");

                cameraObject.transform.rotation = Quaternion.LookRotation(Vector3.left, Vector3.up);
                ProcessGaze(hud.GazeInteractor, 2.9f);
                Assert.That(trigger.IsWaitingForGazeExit, Is.False);
                cameraObject.transform.rotation = LookAt(triggerTransform.position);
                ProcessGaze(hud.GazeInteractor, 3.0f);
                ProcessGaze(hud.GazeInteractor, 4.51f);
                Assert.That(activations, Is.EqualTo(2), "A physical exit and re-entry may start the next trial.");
            }
            finally
            {
                DestroyTestObject(hud, host, cameraObject);
            }
        }

        private static GameObject CreateMainCamera()
        {
            // The EditMode runner may leave the project's scene camera(s) in
            // the test scene. Camera.main would otherwise select one of those
            // cameras instead of this test-owned camera, making the ray and
            // reticle assertions depend on editor scene state.
            Camera[] existingCameras = UnityEngine.Object.FindObjectsByType<Camera>(
                FindObjectsInactive.Include,
                FindObjectsSortMode.None);
            for (int index = 0; index < existingCameras.Length; index++)
            {
                Camera existing = existingCameras[index];
                if (existing != null && existing.CompareTag("MainCamera"))
                {
                    existing.gameObject.tag = "Untagged";
                    s_retaggedMainCameras.Add(existing.gameObject);
                }
            }

            GameObject cameraObject = new GameObject("M16_TestMainCamera");
            cameraObject.tag = "MainCamera";
            cameraObject.AddComponent<Camera>();
            Assert.That(Camera.main, Is.SameAs(cameraObject.GetComponent<Camera>()));
            return cameraObject;
        }

        private static BciPagedGazeControl CreateControl(
            BciPagedQueueHud hud,
            string controlId,
            Vector3 position,
            Action action)
        {
            GameObject controlObject = new GameObject("M16_TestControl_" + controlId);
            controlObject.transform.position = position;
            controlObject.AddComponent<Button>();
            BoxCollider collider = controlObject.AddComponent<BoxCollider>();
            collider.size = new Vector3(0.35f, 0.35f, 0.25f);
            BciPagedGazeControl control = controlObject.AddComponent<BciPagedGazeControl>();
            InvokePrivateAwake(control);
            control.Initialize(hud, controlId, () => action(), () => true);
            control.SetEnabled(true);
            return control;
        }

        private static void InvokePrivateAwake(BciPagedGazeControl control)
        {
            // BciPagedGazeControl is a runtime MonoBehaviour and is not marked
            // ExecuteAlways. AddComponent in EditMode does not provide the
            // normal Play Mode Awake callback, so the test must reproduce that
            // lifecycle before calling the public initializer.
            MethodInfo method = typeof(BciPagedGazeControl).GetMethod(
                "Awake", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(method, Is.Not.Null);
            method.Invoke(control, null);
        }

        private static Quaternion LookAt(Vector3 target)
        {
            return Quaternion.LookRotation(target, Vector3.up);
        }

        private static void InvokeBuildCanvas(BciPagedQueueHud hud, Vector3 position)
        {
            MethodInfo method = typeof(BciPagedQueueHud).GetMethod(
                "BuildCanvas", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(method, Is.Not.Null);
            method.Invoke(hud, new object[] { position, Quaternion.identity });
            // BuildCanvas is the construction seam; Refresh is the production
            // method that flips m_canvasInitializationCompleted after it
            // returns. The helper must assert the objects created by this
            // direct seam, not a post-Refresh state flag.
            Assert.That(hud.WorldSpaceCanvasTransform, Is.Not.Null);
            Assert.That(hud.GazeInteractor, Is.Not.Null);
            Assert.That(hud.GazeInteractor.IsInitialized, Is.True);
        }

        private static void ProcessGaze(BciPagedGazeInteractor interactor, float now)
        {
            // Transform changes made immediately before a Physics.RaycastAll
            // are not guaranteed to be synchronized while running EditMode
            // tests. This is test-harness synchronization, not runtime timing.
            Physics.SyncTransforms();
            Camera camera = Camera.main;
            Ray ray = new Ray(camera.transform.position, camera.transform.forward);
            RaycastHit[] hits = Physics.RaycastAll(
                ray,
                20f,
                BciPagedGazeInteractor.RaycastLayerMask,
                QueryTriggerInteraction.Collide);
            Debug.Log("M16_TEST_GAZE time=" + now.ToString("F2") +
                " origin=" + camera.transform.position.ToString("F3") +
                " direction=" + camera.transform.forward.ToString("F3") +
                " hits=" + DescribeHits(hits));
            MethodInfo method = typeof(BciPagedGazeInteractor).GetMethod(
                "ProcessGaze", BindingFlags.Instance | BindingFlags.NonPublic);
            Assert.That(method, Is.Not.Null);
            method.Invoke(interactor, new object[] { now });
        }

        private static void AssertRayHitsControl(BciPagedGazeControl expected)
        {
            Physics.SyncTransforms();
            Camera camera = Camera.main;
            Ray ray = new Ray(camera.transform.position, camera.transform.forward);
            RaycastHit[] hits = Physics.RaycastAll(
                ray,
                20f,
                BciPagedGazeInteractor.RaycastLayerMask,
                QueryTriggerInteraction.Collide);
            bool found = false;
            for (int index = 0; index < hits.Length; index++)
            {
                BciPagedGazeControl candidate =
                    hits[index].collider.GetComponentInParent<BciPagedGazeControl>();
                if (candidate == expected)
                {
                    found = true;
                    break;
                }
            }
            Assert.That(found, Is.True,
                "Expected gaze control was not hit. expected=" + DescribeControl(expected) +
                " ray_origin=" + camera.transform.position.ToString("F3") +
                " ray_direction=" + camera.transform.forward.ToString("F3") +
                " hits=" + DescribeHits(hits));
        }

        private static string DescribeHits(RaycastHit[] hits)
        {
            if (hits == null || hits.Length == 0)
                return "[]";
            string result = "[";
            for (int index = 0; index < hits.Length; index++)
            {
                if (index > 0)
                    result += "; ";
                Collider collider = hits[index].collider;
                BciPagedGazeControl control = collider == null
                    ? null
                    : collider.GetComponentInParent<BciPagedGazeControl>();
                result += "object=" + (collider == null ? "<null>" : collider.gameObject.name) +
                    "#" + (collider == null ? 0 : collider.gameObject.GetInstanceID()) +
                    " distance=" + hits[index].distance.ToString("F3") +
                    " collider=" + (collider == null ? "<null>" : collider.GetType().Name) +
                    " control=" + DescribeControl(control);
            }
            return result + "]";
        }

        private static string DescribeControl(BciPagedGazeControl control)
        {
            if (control == null)
                return "<none>";
            return control.gameObject.name + "#" + control.gameObject.GetInstanceID() +
                " command=" + control.ControlId +
                " enabled=" + control.IsEnabled +
                " colliderEnabled=" + control.IsColliderEnabled;
        }

        private static void DestroyTestObject(params object[] values)
        {
            for (int index = values.Length - 1; index >= 0; index--)
            {
                GameObject gameObject = values[index] as GameObject;
                if (gameObject != null)
                {
                    bool restoreMainCameraTags = gameObject.name == "M16_TestMainCamera";
                    UnityEngine.Object.DestroyImmediate(gameObject);
                    if (restoreMainCameraTags)
                        RestoreMainCameraTags();
                    continue;
                }

                BciPagedQueueHud hud = values[index] as BciPagedQueueHud;
                if (hud != null && hud.WorldSpaceCanvasTransform != null)
                    UnityEngine.Object.DestroyImmediate(hud.WorldSpaceCanvasTransform.gameObject);
                if (hud != null)
                    UnityEngine.Object.DestroyImmediate(hud.gameObject);
            }
        }

        private static void RestoreMainCameraTags()
        {
            for (int index = 0; index < s_retaggedMainCameras.Count; index++)
            {
                GameObject cameraObject = s_retaggedMainCameras[index];
                if (cameraObject != null)
                    cameraObject.tag = "MainCamera";
            }
            s_retaggedMainCameras.Clear();
        }
    }
}
