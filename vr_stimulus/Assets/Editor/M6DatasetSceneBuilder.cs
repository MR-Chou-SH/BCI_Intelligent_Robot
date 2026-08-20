using BCIIntelligentRobot.VRStimulus;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEditor.Build.Reporting;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class M6DatasetSceneBuilder
{
    private const string SourceScene = "Assets/Scenes/M5_2_QuestPcSynchronization.unity";
    private const string DatasetScene = "Assets/Scenes/M6_1b_ThreeClassDataset.unity";

    [MenuItem("BCI/M6.1b/Create Three-Class Dataset Scene")]
    public static void CreateScene()
    {
        Scene scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
        GameObject session = GameObject.Find("M5_StimulusSessionController");
        if (session == null) throw new System.InvalidOperationException("M5 session controller was not found");
        M5TrialStimulusController stimulus = session.GetComponent<M5TrialStimulusController>();
        StimulusEventTransportClient transport = session.GetComponent<StimulusEventTransportClient>();
        if (stimulus == null || transport == null) throw new System.InvalidOperationException("M5 stimulus/transport missing");
        M5FrameDelayedTrialStarter delayedStarter = session.GetComponent<M5FrameDelayedTrialStarter>();
        if (delayedStarter != null) delayedStarter.enabled = false;
        M6DatasetTrialController dataset = session.GetComponent<M6DatasetTrialController>();
        if (dataset == null) dataset = session.AddComponent<M6DatasetTrialController>();
        Camera camera = Object.FindFirstObjectByType<Camera>();
        var serialized = new SerializedObject(dataset);
        serialized.FindProperty("m_Stimulus").objectReferenceValue = stimulus;
        serialized.FindProperty("m_Transport").objectReferenceValue = transport;
        serialized.FindProperty("m_CueAnchor").objectReferenceValue = camera == null ? null : camera.transform;
        serialized.FindProperty("m_VisualDemoMode").boolValue = false;
        serialized.ApplyModifiedPropertiesWithoutUndo();
        EditorSceneManager.SaveScene(scene, DatasetScene);
        EditorBuildSettings.scenes = new[] {
            new EditorBuildSettingsScene(DatasetScene, true),
            new EditorBuildSettingsScene(SourceScene, true),
        };
        AssetDatabase.SaveAssets();
        Debug.Log("Created " + DatasetScene);
    }

    [MenuItem("BCI/M6.1b/Enable Visual Demo Mode In Current Scene")]
    public static void EnableVisualDemoModeInCurrentScene()
    {
        M6DatasetTrialController dataset = Object.FindFirstObjectByType<M6DatasetTrialController>();
        if (dataset == null)
            throw new System.InvalidOperationException("M6 dataset controller was not found in the current scene");
        var serialized = new SerializedObject(dataset);
        serialized.FindProperty("m_VisualDemoMode").boolValue = true;
        serialized.ApplyModifiedPropertiesWithoutUndo();
        EditorSceneManager.MarkSceneDirty(dataset.gameObject.scene);
        EditorSceneManager.SaveScene(dataset.gameObject.scene);
        Debug.Log("Enabled M6.1b visual demo mode for " + dataset.gameObject.scene.path);
    }

    public static void PrepareDemoBuild()
    {
        Scene scene = EditorSceneManager.OpenScene(DatasetScene, OpenSceneMode.Single);
        SetDemoMode(scene, true);
    }

    [MenuItem("BCI/M6.1b/Restore Formal Mode In Current Scene")]
    public static void RestoreFormalMode()
    {
        Scene scene = EditorSceneManager.OpenScene(DatasetScene, OpenSceneMode.Single);
        SetDemoMode(scene, false);
    }

    public static void BuildM6VisualDemo()
    {
        PrepareDemoBuild();
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { DatasetScene },
            locationPathName = "D:/M6_1b_visual_demo.apk",
            target = BuildTarget.Android,
            options = BuildOptions.None
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new System.InvalidOperationException("Android demo build failed: " + report.summary.result);
        Debug.Log("Built M6.1b visual demo APK: " + report.summary.outputPath);
    }

    // This build deliberately keeps the existing formal scene configuration;
    // the PC declares diagnostic_live in the received plan, not in scene state.
    public static void BuildM6LiveDiagnostic()
    {
        Scene scene = EditorSceneManager.OpenScene(DatasetScene, OpenSceneMode.Single);
        M6DatasetTrialController dataset = Object.FindFirstObjectByType<M6DatasetTrialController>();
        if (dataset == null)
            throw new System.InvalidOperationException("M6 dataset controller was not found in " + DatasetScene);
        var serialized = new SerializedObject(dataset);
        if (serialized.FindProperty("m_VisualDemoMode").boolValue)
            throw new System.InvalidOperationException("M6 diagnostic build requires the existing formal scene mode");
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { DatasetScene },
            locationPathName = "D:/EEG_Study/m6_6b/M6_6b_live_diagnostic.apk",
            target = BuildTarget.Android,
            options = BuildOptions.StrictMode
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new System.InvalidOperationException("Android diagnostic build failed: " + report.summary.result);
        Debug.Log("Built M6.6b live diagnostic APK: " + report.summary.outputPath);
    }

    private static void SetDemoMode(Scene scene, bool enabled)
    {
        M6DatasetTrialController dataset = Object.FindFirstObjectByType<M6DatasetTrialController>();
        if (dataset == null)
            throw new System.InvalidOperationException("M6 dataset controller was not found in " + scene.path);
        var serialized = new SerializedObject(dataset);
        serialized.FindProperty("m_VisualDemoMode").boolValue = enabled;
        serialized.ApplyModifiedPropertiesWithoutUndo();
        EditorSceneManager.SaveScene(scene);
        Debug.Log((enabled ? "Enabled" : "Restored") + " M6.1b mode in " + scene.path);
    }
}
