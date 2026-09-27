#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using BCIIntelligentRobot.Integration;
using BCIIntelligentRobot.Tests;
using BCIIntelligentRobot.VirtualManipulation;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace BCIIntelligentRobot.Editor
{
    /// <summary>
    /// One-shot M20 Task 1 validation in the already-running Unity Editor.
    /// It runs only after an explicit request file appears under the M20 run
    /// directory; importing the project does not start tests or capture images.
    /// </summary>
    [InitializeOnLoad]
    public static class M20OvernightTask1EditorValidation
    {
        private const string SceneAssetPath =
            "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M20DailyAssistiveDesk.unity";
        private const string DriverRequestRelativePath =
            "docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task1/editor-validation-v4.request";
        private const string ResultRelativePath =
            "docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task1/unity-editor-validation-v4.json";
        private const string ScreenshotDirectoryRelativePath =
            "docs/agent/overnight/runs/m20-daily-assistive-desk-20260926/task1/unity-screenshots-v4";

        private static bool s_started;
        private static string s_repositoryRoot;
        private static string s_requestProcessingPath;
        private static TestRunnerApi s_testRunner;
        private static Task1Callbacks s_callbacks;
        private static ValidationReport s_report;
        private static bool s_captureOnly;

        static M20OvernightTask1EditorValidation()
        {
            EditorApplication.update -= PollForRequest;
            EditorApplication.update += PollForRequest;
        }

        private static void PollForRequest()
        {
            if (s_started)
                return;

            s_repositoryRoot = Directory.GetParent(Application.dataPath).Parent.FullName;
            string requestPath = Path.Combine(s_repositoryRoot, DriverRequestRelativePath);
            if (!File.Exists(requestPath))
                return;

            string requestText;
            s_requestProcessingPath = requestPath + ".processing";
            try
            {
                requestText = File.ReadAllText(requestPath);
                if (File.Exists(s_requestProcessingPath))
                    throw new IOException("A prior M20 validation processing marker still exists.");
                File.Move(requestPath, s_requestProcessingPath);
            }
            catch (IOException)
            {
                // File watchers and endpoint scanners can briefly hold a new
                // request marker. Keep polling instead of consuming a failure.
                return;
            }

            s_started = true;
            EditorApplication.update -= PollForRequest;
            s_captureOnly = requestText.IndexOf("\"captureOnly\":true", StringComparison.OrdinalIgnoreCase) >= 0;
            s_report = new ValidationReport
            {
                recordType = "m20_task1_unity_editor_validation",
                unityVersion = Application.unityVersion,
                sceneAssetPath = SceneAssetPath,
                startedUtc = DateTime.UtcNow.ToString("O")
            };

            try
            {
                if (s_captureOnly)
                {
                    s_report.testRunId = "capture-only";
                    s_report.testResult = "direct EditMode assertions will run";
                    EditorApplication.delayCall += CompleteValidation;
                    return;
                }

                s_testRunner = ScriptableObject.CreateInstance<TestRunnerApi>();
                s_callbacks = new Task1Callbacks();
                s_testRunner.RegisterCallbacks(s_callbacks);
                var filter = new Filter
                {
                    testMode = TestMode.EditMode,
                    assemblyNames = new[] { "Assembly-CSharp-Editor" },
                    groupNames = new[] { "^BCIIntelligentRobot\\.Tests\\.M20AssistiveDeskSceneTests\\." }
                };
                var settings = new ExecutionSettings(filter) { runSynchronously = true };
                string runId = s_testRunner.Execute(settings);
                s_report.testRunId = runId;
                Debug.Log("M20_TASK1_EDITOR validation_started testRunId=" + runId);
            }
            catch (Exception error)
            {
                s_report.error = error.ToString();
                EditorApplication.delayCall += CompleteValidation;
            }
        }

        private static void CompleteValidation()
        {
            try
            {
                if (s_report.testPassed == 0 && s_report.testFailed == 0)
                {
                    s_report.directEditModeFallback = true;
                    var fixture = new M20AssistiveDeskSceneTests();
                    fixture.CanonicalResourceBuildsPagedCatalogWithFrozenSlotsAndSixExplicitTargets();
                    fixture.GeneratedGeometryUsesPositiveScaleAndMatchesHingeAndButtonArticulation();
                    s_report.testPassed = 2;
                    s_report.testResult = "direct_EditMode_assertions_passed";
                }

                InspectSceneAsset();
                CaptureUnityViewsAndArticulation();
                bool testsPass = s_report.testPassed == 2 && s_report.testFailed == 0;
                s_report.status = testsPass && s_report.sceneProfileValid && s_report.capturePass
                    ? "PASS"
                    : "FAIL";
            }
            catch (Exception error)
            {
                s_report.status = "FAIL";
                s_report.error = error.ToString();
                Debug.LogError("M20_TASK1_EDITOR validation_failed " + error);
            }
            finally
            {
                s_report.completedUtc = DateTime.UtcNow.ToString("O");
                WriteReport();
                if (!string.IsNullOrEmpty(s_requestProcessingPath) && File.Exists(s_requestProcessingPath))
                    File.Delete(s_requestProcessingPath);
                Debug.Log("M20_TASK1_EDITOR validation_finished status=" + s_report.status +
                    " passed=" + s_report.testPassed + " failed=" + s_report.testFailed +
                    " screenshots=" + s_report.screenshotPaths.Count);
            }
        }

        private static void InspectSceneAsset()
        {
            if (AssetDatabase.LoadAssetAtPath<SceneAsset>(SceneAssetPath) == null)
                throw new FileNotFoundException("M20 scene asset was not importable.", SceneAssetPath);

            string relativeAssetPath = SceneAssetPath.Substring("Assets/".Length)
                .Replace('/', Path.DirectorySeparatorChar);
            string sceneText = File.ReadAllText(Path.Combine(Application.dataPath, relativeAssetPath));
            int pagedQueueEnumValue = Array.IndexOf(
                Enum.GetNames(typeof(BciSelectionInteractionMode)), "PagedQueueV1");
            if (pagedQueueEnumValue < 0)
                throw new InvalidOperationException("The M16 PagedQueueV1 interaction mode is missing.");

            s_report.sceneProfile = "PagedQueueV1";
            s_report.sceneProfileValid = sceneText.Contains("m_useM20AssistiveDeskProfile: 1") &&
                sceneText.Contains("m_selectionInteractionMode: " + pagedQueueEnumValue);
            if (!s_report.sceneProfileValid)
                throw new InvalidOperationException("M20 scene YAML does not select the M20 PagedQueueV1 profile.");
        }

        private static void CaptureUnityViewsAndArticulation()
        {
            Scene previewScene = SceneManager.GetActiveScene();
            if (!previewScene.IsValid() || !previewScene.isLoaded)
                throw new InvalidOperationException("Unity Editor has no loaded scene for an isolated M20 preview.");

            UnityEngine.Rendering.AmbientMode previousAmbientMode = RenderSettings.ambientMode;
            Color previousAmbientLight = RenderSettings.ambientLight;
            Light previousSun = RenderSettings.sun;
            Material previewMaterial = null;
            GameObject bootstrapObject = null;
            GameObject workspaceObject = null;
            GameObject previewLightObject = null;
            try
            {
                bootstrapObject = new GameObject("M20 Task 1 production-scene preview");
                var bootstrap = bootstrapObject.AddComponent<M9VirtualManipulationBootstrap>();
                FieldInfo profileField = typeof(M9VirtualManipulationBootstrap).GetField(
                    "m_useM20AssistiveDeskProfile", BindingFlags.Instance | BindingFlags.NonPublic);
                profileField.SetValue(bootstrap, true);

                M20AssistiveDeskSceneSpec spec = M20AssistiveDeskUnityScene.LoadFromResources();
                M9VirtualBlockCatalogData catalog = M20AssistiveDeskUnityScene.CreatePagedQueueCatalog(spec);
                MethodInfo createWorkspace = typeof(M9VirtualManipulationBootstrap).GetMethod(
                    "CreateWorkspace", BindingFlags.Instance | BindingFlags.NonPublic);
                createWorkspace.Invoke(bootstrap, new object[]
                {
                    catalog,
                    new Vector3(0f, 1.55f, 0f),
                    Vector3.forward
                });

                workspaceObject = ((Transform)typeof(M9VirtualManipulationBootstrap)
                    .GetField("m_workspaceRoot", BindingFlags.Instance | BindingFlags.NonPublic)
                    .GetValue(bootstrap)).gameObject;
                previewLightObject = (GameObject)typeof(M9VirtualManipulationBootstrap)
                    .GetField("m_virtualLightObject", BindingFlags.Instance | BindingFlags.NonPublic)
                    .GetValue(bootstrap);

                Transform blocksRoot = (Transform)typeof(M9VirtualManipulationBootstrap)
                    .GetField("m_blocksRoot", BindingFlags.Instance | BindingFlags.NonPublic)
                    .GetValue(bootstrap);
                MethodInfo setRendererColorMethod = typeof(M9VirtualManipulationBootstrap).GetMethod(
                    "SetRendererColor", BindingFlags.Instance | BindingFlags.NonPublic);
                var setRendererColor = (Action<Renderer, Color>)Delegate.CreateDelegate(
                    typeof(Action<Renderer, Color>), bootstrap, setRendererColorMethod);
                M20AssistiveDeskUnityScene.CreateTargets(
                    spec,
                    blocksRoot,
                    bootstrapObject,
                    setRendererColor,
                    out List<GameObject> targetRoots,
                    out M20AssistiveDeskArticulationController controller);
                s_report.runtimeCandidateCount = targetRoots.Count;

                Transform lidPivot = blocksRoot.Find("M20_assist_storage_lid_hinge");
                Quaternion closedRotation = lidPivot.localRotation;
                controller.SetStorageLidOpen(true);
                s_report.storageLidOpenDegrees = Quaternion.Angle(closedRotation, lidPivot.localRotation);
                controller.SetStorageLidOpen(false);
                s_report.storageLidClosedErrorDegrees = Quaternion.Angle(closedRotation, lidPivot.localRotation);

                Transform buttonCap = blocksRoot.Find("M20_assist_button_cap_slide");
                float unpressedY = buttonCap.localPosition.y;
                controller.SetButtonPressed(true);
                s_report.buttonTravelMeters = unpressedY - buttonCap.localPosition.y;
                controller.SetButtonPressed(false);
                s_report.buttonReleasedErrorMeters = Mathf.Abs(unpressedY - buttonCap.localPosition.y);

                previewMaterial = (Material)typeof(M9VirtualManipulationBootstrap)
                    .GetField("m_sharedPrimitiveMaterial", BindingFlags.Instance | BindingFlags.NonPublic)
                    .GetValue(bootstrap);

                Transform workspaceRoot = blocksRoot.parent;
                SetLayerRecursively(workspaceRoot.gameObject, 31);
                string screenshotDirectory = Path.Combine(s_repositoryRoot, ScreenshotDirectoryRelativePath);
                Directory.CreateDirectory(screenshotDirectory);
                s_report.screenshotPaths.Add(CaptureView(
                    previewScene,
                    workspaceRoot,
                    Path.Combine(screenshotDirectory, "unity_topdown.png"),
                    true));
                s_report.screenshotPaths.Add(CaptureView(
                    previewScene,
                    workspaceRoot,
                    Path.Combine(screenshotDirectory, "unity_perspective.png"),
                    false));

                s_report.capturePass = s_report.runtimeCandidateCount == 6 &&
                    Mathf.Abs(s_report.storageLidOpenDegrees - 100f) <= 0.02f &&
                    s_report.storageLidClosedErrorDegrees <= 0.02f &&
                    Mathf.Abs(s_report.buttonTravelMeters - 0.005f) <= 0.000001f &&
                    s_report.buttonReleasedErrorMeters <= 0.000001f;
                if (!s_report.capturePass)
                    throw new InvalidOperationException("M20 Unity articulation or candidate capture check failed.");
            }
            finally
            {
                if (workspaceObject != null)
                    UnityEngine.Object.DestroyImmediate(workspaceObject);
                if (previewLightObject == null && bootstrapObject != null)
                    previewLightObject = (GameObject)typeof(M9VirtualManipulationBootstrap)
                        .GetField("m_virtualLightObject", BindingFlags.Instance | BindingFlags.NonPublic)
                        .GetValue(bootstrapObject.GetComponent<M9VirtualManipulationBootstrap>());
                if (previewLightObject != null)
                    UnityEngine.Object.DestroyImmediate(previewLightObject);
                if (bootstrapObject != null)
                    UnityEngine.Object.DestroyImmediate(bootstrapObject);
                RenderSettings.sun = previousSun;
                RenderSettings.ambientMode = previousAmbientMode;
                RenderSettings.ambientLight = previousAmbientLight;
                if (previewMaterial != null)
                    UnityEngine.Object.DestroyImmediate(previewMaterial);
            }
        }

        private static string CaptureView(Scene scene, Transform workspaceRoot, string path, bool topDown)
        {
            var cameraObject = new GameObject(topDown ? "M20 validation top-down camera" : "M20 validation perspective camera");
            SceneManager.MoveGameObjectToScene(cameraObject, scene);
            Camera camera = cameraObject.AddComponent<Camera>();
            camera.enabled = false;
            camera.cullingMask = 1 << 31;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.20f, 0.24f, 0.29f, 1f);
            camera.nearClipPlane = 0.01f;
            camera.farClipPlane = 25f;
            camera.aspect = 4f / 3f;

            if (topDown)
            {
                camera.orthographic = true;
                camera.orthographicSize = 0.43f;
                Vector3 position = workspaceRoot.TransformPoint(new Vector3(0f, 3f, 0f));
                camera.transform.SetPositionAndRotation(position,
                    Quaternion.LookRotation(-workspaceRoot.up, -workspaceRoot.forward));
            }
            else
            {
                camera.orthographic = false;
                camera.fieldOfView = 42f;
                Vector3 position = workspaceRoot.TransformPoint(new Vector3(0f, 1.35f, 1.75f));
                Vector3 target = workspaceRoot.TransformPoint(new Vector3(0f, 0.33f, -0.04f));
                camera.transform.SetPositionAndRotation(position,
                    Quaternion.LookRotation(target - position, workspaceRoot.up));
            }

            var renderTexture = new RenderTexture(1280, 960, 24, RenderTextureFormat.ARGB32);
            var image = new Texture2D(1280, 960, TextureFormat.RGB24, false);
            RenderTexture previousActiveTexture = RenderTexture.active;
            try
            {
                camera.targetTexture = renderTexture;
                camera.Render();
                RenderTexture.active = renderTexture;
                image.ReadPixels(new Rect(0f, 0f, 1280f, 960f), 0, 0);
                image.Apply(false, false);
                File.WriteAllBytes(path, image.EncodeToPNG());
                return path.Substring(s_repositoryRoot.Length + 1).Replace('\\', '/');
            }
            finally
            {
                RenderTexture.active = previousActiveTexture;
                camera.targetTexture = null;
                renderTexture.Release();
                UnityEngine.Object.DestroyImmediate(image);
                UnityEngine.Object.DestroyImmediate(renderTexture);
                UnityEngine.Object.DestroyImmediate(cameraObject);
            }
        }

        private static void SetLayerRecursively(GameObject root, int layer)
        {
            root.layer = layer;
            foreach (Transform child in root.transform)
                SetLayerRecursively(child.gameObject, layer);
        }

        private static void WriteReport()
        {
            string path = Path.Combine(s_repositoryRoot, ResultRelativePath);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, JsonUtility.ToJson(s_report, true) + Environment.NewLine);
        }

        [Serializable]
        private sealed class ValidationReport
        {
            public string recordType;
            public string status;
            public string unityVersion;
            public string sceneAssetPath;
            public string sceneProfile;
            public string testRunId;
            public string testResult;
            public string error;
            public string startedUtc;
            public string completedUtc;
            public bool sceneProfileValid;
            public bool capturePass;
            public bool directEditModeFallback;
            public int testPassed;
            public int testFailed;
            public int testSkipped;
            public int testAssertCount;
            public int runtimeCandidateCount;
            public float storageLidOpenDegrees;
            public float storageLidClosedErrorDegrees;
            public float buttonTravelMeters;
            public float buttonReleasedErrorMeters;
            public List<string> screenshotPaths = new List<string>();
        }

        private sealed class Task1Callbacks : ICallbacks
        {
            public void RunStarted(ITestAdaptor testsToRun) { }
            public void TestStarted(ITestAdaptor test) { }
            public void TestFinished(ITestResultAdaptor result) { }

            public void RunFinished(ITestResultAdaptor result)
            {
                s_report.testPassed = result.PassCount;
                s_report.testFailed = result.FailCount;
                s_report.testSkipped = result.SkipCount;
                s_report.testAssertCount = result.AssertCount;
                s_report.testResult = result.ResultState;
                EditorApplication.delayCall += CompleteValidation;
            }
        }
    }
}
#endif
