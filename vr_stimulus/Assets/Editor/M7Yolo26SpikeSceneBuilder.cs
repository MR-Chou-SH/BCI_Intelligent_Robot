using System;
using System.IO;
using BCIIntelligentRobot.Vision;
using Unity.InferenceEngine;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.XR.ARFoundation;

public static class M7Yolo26SpikeSceneBuilder
{
    private const string SourceScene = "Assets/Scenes/M7_1_QuestCameraSpike.unity";
    private const string SpikeScene = "Assets/Scenes/M7_2_QuestYolo26Spike.unity";
    private const string ModelPath = "Assets/Models/yolo26n.onnx";

    [MenuItem("BCI/M7.2/Create Quest YOLO26n Spike Scene")]
    public static void CreateScene()
    {
        ModelAsset modelAsset = AssetDatabase.LoadAssetAtPath<ModelAsset>(ModelPath);
        if (modelAsset == null)
            throw new InvalidOperationException("M7.2 requires an imported ModelAsset at " + ModelPath);

        Scene scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
        Camera mainCamera = Camera.main;
        if (mainCamera == null)
            throw new InvalidOperationException("M7.2 source scene does not contain a tagged Main Camera.");

        ARCameraManager cameraManager = mainCamera.GetComponent<ARCameraManager>();
        if (cameraManager == null)
            throw new InvalidOperationException("M7.2 source scene does not contain ARCameraManager.");

        QuestCameraFrameSpike m71Spike = mainCamera.GetComponent<QuestCameraFrameSpike>();
        if (m71Spike != null)
            UnityEngine.Object.DestroyImmediate(m71Spike);

        QuestYolo26DetectionSpike m72Spike = mainCamera.GetComponent<QuestYolo26DetectionSpike>();
        if (m72Spike == null)
            m72Spike = mainCamera.gameObject.AddComponent<QuestYolo26DetectionSpike>();

        m72Spike.ConfigureModelAsset(modelAsset);
        cameraManager.enabled = false;
        EditorSceneManager.SaveScene(scene, SpikeScene);
        AssetDatabase.SaveAssets();
        Debug.Log("Created " + SpikeScene + " with YOLO26n ModelAsset.");
    }

    [MenuItem("BCI/M7.2/Build Quest YOLO26n Spike APK")]
    public static void BuildQuestYolo26SpikeApk()
    {
        if (!File.Exists(SpikeScene))
            CreateScene();

        string projectRoot = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".."));
        string outputDirectory = Path.Combine(projectRoot, "builds");
        Directory.CreateDirectory(outputDirectory);
        string outputPath = Path.Combine(outputDirectory, "M7_2_QuestYolo26Spike.apk");

        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { SpikeScene },
            locationPathName = outputPath,
            target = BuildTarget.Android,
            options = BuildOptions.StrictMode
        });

        if (report.summary.result != BuildResult.Succeeded)
            throw new InvalidOperationException("M7.2 Android build failed: " + report.summary.result);

        Debug.Log("Built M7.2 Quest YOLO26n Spike APK: " + report.summary.outputPath);
    }
}
