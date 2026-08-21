using System;
using System.IO;
using BCIIntelligentRobot.Vision;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using UnityEngine.XR.ARFoundation;
using UnityEngine.XR.OpenXR;
using UnityEngine.XR.OpenXR.Features;
using UnityEngine.XR.OpenXR.Features.Meta;

public static class M7WorldTargetSpikeSceneBuilder
{
    private const string SourceScene = "Assets/Scenes/M7_2_QuestYolo26Spike.unity";
    private const string SpikeScene = "Assets/Scenes/M7_4_TargetWorldSpike.unity";

    [MenuItem("BCI/M7.4/Validate Target World Spike Settings")]
    public static void ValidateSettings()
    {
        GraphicsDeviceType[] graphicsApis = PlayerSettings.GetGraphicsAPIs(BuildTarget.Android);
        if (graphicsApis.Length == 0 || graphicsApis[0] != GraphicsDeviceType.Vulkan)
        {
            throw new InvalidOperationException(
                "M7.4 requires Vulkan as the first Android Graphics API for Meta Quest Occlusion. " +
                "Set it in Project Settings > Player > Android > Other Settings > Graphics APIs.");
        }

        OpenXRSettings settings = OpenXRSettings.GetSettingsForBuildTargetGroup(BuildTargetGroup.Android);
        if (settings == null || !IsEnabled<AROcclusionFeature>(settings) || !IsEnabled<ARRaycastFeature>(settings))
        {
            throw new InvalidOperationException(
                "M7.4 requires Android Meta Quest: Occlusion and Meta Quest: Raycasts OpenXR features to be enabled.");
        }

        // In Meta OpenXR 2.5.1, the build processor adds USE_SCENE when the Raycasts
        // feature is enabled. M7.4 does not add an ARRaycastManager or issue raycasts.
        Debug.Log("M7.4 settings validation passed: Vulkan + Occlusion + Raycasts are enabled.");
    }

    [MenuItem("BCI/M7.4/Create Target World Spike Scene")]
    public static void CreateScene()
    {
        ValidateSettings();

        Scene scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
        Camera mainCamera = Camera.main;
        if (mainCamera == null)
            throw new InvalidOperationException("M7.4 source scene does not contain a tagged Main Camera.");

        QuestYolo26DetectionSpike detector = mainCamera.GetComponent<QuestYolo26DetectionSpike>();
        if (detector == null)
            throw new InvalidOperationException("M7.4 source scene does not contain QuestYolo26DetectionSpike.");

        AROcclusionManager occlusionManager = mainCamera.GetComponent<AROcclusionManager>();
        if (occlusionManager == null)
            occlusionManager = mainCamera.gameObject.AddComponent<AROcclusionManager>();

        StableTargetWorldMapper mapper = mainCamera.GetComponent<StableTargetWorldMapper>();
        if (mapper == null)
            mapper = mainCamera.gameObject.AddComponent<StableTargetWorldMapper>();

        mapper.Configure(detector, occlusionManager);
        // Meta's provider requires USE_SCENE before the occlusion manager may start.
        occlusionManager.enabled = false;

        EditorSceneManager.SaveScene(scene, SpikeScene);
        AssetDatabase.SaveAssets();
        Debug.Log("Created " + SpikeScene + " with M7.4 environment-depth target mapper.");
    }

    [MenuItem("BCI/M7.4/Build Target World Spike APK")]
    public static void BuildTargetWorldSpikeApk()
    {
        ValidateSettings();
        if (!File.Exists(SpikeScene))
            CreateScene();

        string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        string outputDirectory = Path.Combine(projectRoot, "builds");
        Directory.CreateDirectory(outputDirectory);
        string outputPath = Path.Combine(outputDirectory, "M7_4_TargetWorldSpike.apk");

        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { SpikeScene },
            locationPathName = outputPath,
            target = BuildTarget.Android,
            options = BuildOptions.StrictMode
        });

        if (report.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("M7.4 Android build failed: " + report.summary.result);

        Debug.Log("Built M7.4 Target World Spike APK: " + report.summary.outputPath);
    }

    private static bool IsEnabled<TFeature>(OpenXRSettings settings) where TFeature : OpenXRFeature
    {
        TFeature feature = settings.GetFeature<TFeature>();
        return feature != null && feature.enabled;
    }
}
