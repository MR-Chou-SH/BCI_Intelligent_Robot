using System;
using System.IO;
using BCIIntelligentRobot.Vision;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.OpenXR;
using UnityEngine.XR.OpenXR.Features.Meta;

public static class M7CameraSpikeSceneBuilder
{
    private const string SourceScene = "Assets/Scenes/M2_1_Passthrough.unity";
    private const string SpikeScene = "Assets/Scenes/M7_1_QuestCameraSpike.unity";

    [MenuItem("BCI/M7.1/Create Quest Camera Spike Scene")]
    public static void CreateScene()
    {
        EnableAndroidCameraImageSupport();

        Scene scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
        Camera mainCamera = Camera.main;
        if (mainCamera == null)
            throw new InvalidOperationException("M7.1 source scene does not contain a tagged Main Camera.");

        ARCameraManager cameraManager = mainCamera.GetComponent<ARCameraManager>();
        if (cameraManager == null)
            throw new InvalidOperationException("M7.1 source scene does not contain ARCameraManager.");

        QuestCameraFrameSpike spike = mainCamera.GetComponent<QuestCameraFrameSpike>();
        if (spike == null)
            spike = mainCamera.gameObject.AddComponent<QuestCameraFrameSpike>();

        cameraManager.enabled = false;
        EditorSceneManager.SaveScene(scene, SpikeScene);
        AssetDatabase.SaveAssets();
        Debug.Log("Created " + SpikeScene + " with Camera Image Support enabled.");
    }

    [MenuItem("BCI/M7.1/Build Quest Camera Spike APK")]
    public static void BuildQuestCameraSpikeApk()
    {
        if (!File.Exists(SpikeScene))
            CreateScene();

        string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        string outputDirectory = Path.Combine(projectRoot, "builds");
        Directory.CreateDirectory(outputDirectory);
        string outputPath = Path.Combine(outputDirectory, "M7_1_QuestCameraSpike.apk");

        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { SpikeScene },
            locationPathName = outputPath,
            target = BuildTarget.Android,
            options = BuildOptions.StrictMode
        });

        if (report.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("M7.1 Android build failed: " + report.summary.result);

        Debug.Log("Built M7.1 Quest Camera Spike APK: " + report.summary.outputPath);
    }

    private static void EnableAndroidCameraImageSupport()
    {
        OpenXRSettings settings = OpenXRSettings.GetSettingsForBuildTargetGroup(BuildTargetGroup.Android);
        if (settings == null)
            throw new InvalidOperationException("Android OpenXR settings were not found.");

        ARCameraFeature cameraFeature = settings.GetFeature<ARCameraFeature>();
        if (cameraFeature == null || !cameraFeature.enabled)
            throw new InvalidOperationException("Meta Quest: Camera (Passthrough) must be enabled for Android.");

        cameraFeature.cameraImageSupportEnabled = true;
        EditorUtility.SetDirty(cameraFeature);
    }
}
