using System;
using System.IO;
using BCIIntelligentRobot.Vision;
using Meta.XR;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class M7OfficialLocalizationSceneBuilder
{
    private const string SourceScene = "Assets/Scenes/M7_2_QuestYolo26Spike.unity";
    private const string LocalizationScene = "Assets/Scenes/M7_6_OfficialLocalization.unity";

    [MenuItem("BCI/M7.6/Create Official Localization Scene")]
    public static void CreateScene()
    {
        Scene scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
        Camera mainCamera = Camera.main;
        if (mainCamera == null)
            throw new InvalidOperationException("M7.2 source scene does not contain a tagged Main Camera.");

        QuestYolo26DetectionSpike detector = mainCamera.GetComponent<QuestYolo26DetectionSpike>();
        if (detector == null)
            throw new InvalidOperationException("M7.6 requires the existing QuestYolo26DetectionSpike component.");

        PassthroughCameraAccess cameraAccess = mainCamera.GetComponent<PassthroughCameraAccess>();
        if (cameraAccess == null)
            cameraAccess = mainCamera.gameObject.AddComponent<PassthroughCameraAccess>();
        cameraAccess.CameraPosition = PassthroughCameraAccess.CameraPositionType.Left;
        cameraAccess.RequestedResolution = new Vector2Int(1280, 960);

        EnvironmentRaycastManager environmentRaycastManager =
            UnityEngine.Object.FindAnyObjectByType<EnvironmentRaycastManager>();
        if (environmentRaycastManager == null)
        {
            GameObject raycastObject = new GameObject("M7.6 Environment Raycast Manager");
            environmentRaycastManager = raycastObject.AddComponent<EnvironmentRaycastManager>();
        }

        Transform trackingSpace = GameObject.Find("XRRig")?.transform;
        environmentRaycastManager.CustomTrackingSpace = trackingSpace;

        OfficialStableTargetWorldLocalizer localizer =
            mainCamera.GetComponent<OfficialStableTargetWorldLocalizer>();
        if (localizer == null)
            localizer = mainCamera.gameObject.AddComponent<OfficialStableTargetWorldLocalizer>();
        localizer.Configure(detector, cameraAccess, environmentRaycastManager, trackingSpace);

        EditorSceneManager.SaveScene(scene, LocalizationScene);
        AssetDatabase.SaveAssets();
        Debug.Log("Created " + LocalizationScene + " with official 2D-to-world localization.");
    }

    [MenuItem("BCI/M7.6/Build Official Localization APK")]
    public static void BuildQuestApk()
    {
        if (!File.Exists(LocalizationScene))
            CreateScene();

        string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        string outputDirectory = Path.Combine(projectRoot, "builds");
        Directory.CreateDirectory(outputDirectory);
        string outputPath = Path.Combine(outputDirectory, "M7_6_OfficialLocalization.apk");

        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { LocalizationScene },
            locationPathName = outputPath,
            target = BuildTarget.Android,
            options = BuildOptions.StrictMode
        });

        if (report.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("M7.6 Android build failed: " + report.summary.result);

        Debug.Log("Built M7.6 official localization APK: " + report.summary.outputPath);
    }
}
