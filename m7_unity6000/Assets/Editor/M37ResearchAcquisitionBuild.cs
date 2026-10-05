using System;
using System.IO;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using PassthroughCameraSamples.MultiObjectDetection;
using BCIIntelligentRobot.Integration;

/// <summary>
/// Builds a separate M37 Research APK from a copied scene. The project's
/// normal enabled scenes and original Android application identifier remain
/// unchanged after the build.
/// </summary>
public static class M37ResearchAcquisitionBuild
{
    private const string SourceScenePath =
        "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M20DailyAssistiveDesk.unity";
    private const string ResearchScenePath =
        "Assets/BCI/ResearchBuild/M37ResearchAcquisition.unity";
    private const string ResearchApplicationIdentifier =
        "com.samples.passthroughcamera.m37research";
    private const string ResearchProductName = "BCI M37 Research";
    private const int SelectionPort = 11001;

    public static void BuildQuestResearchAcquisition()
    {
        string outputPath = Environment.GetEnvironmentVariable("M37_APK_OUTPUT");
        string questServerHost = Environment.GetEnvironmentVariable("M37_QUEST_SERVER_HOST");
        string previousApplicationIdentifier = null;
        string previousApplicationIdentifierOverride = null;
        string previousProductName = null;
        bool previousForceInternetPermission = false;
        bool previousBuildAppBundle = false;
        bool settingsCaptured = false;
        SceneSetup[] previousSceneSetup = EditorSceneManager.GetSceneManagerSetup();
        int exitCode = 1;

        try
        {
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
                throw new InvalidOperationException(
                    "Start Unity with -buildTarget Android; this build entry will not change the project's active target.");

            if (string.IsNullOrWhiteSpace(outputPath) || !Path.IsPathRooted(outputPath) ||
                !string.Equals(Path.GetExtension(outputPath), ".apk", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("M37_APK_OUTPUT must be an absolute path ending in .apk.");

            IPAddress parsedHost;
            if (!IPAddress.TryParse(questServerHost, out parsedHost) || IPAddress.IsLoopback(parsedHost))
                throw new InvalidOperationException("M37_QUEST_SERVER_HOST must be the reachable PC Wi-Fi IPv4 address.");

            outputPath = Path.GetFullPath(outputPath);
            string reportPath = outputPath + ".json";
            if (File.Exists(outputPath) || File.Exists(reportPath))
                throw new IOException("Refusing to overwrite an existing M37 APK or build report: " + outputPath);
            if (!File.Exists(SourceScenePath))
                throw new FileNotFoundException("The current M20 daily desk scene is missing.", SourceScenePath);

            string researchSceneDirectory = Path.GetDirectoryName(ResearchScenePath);
            Directory.CreateDirectory(researchSceneDirectory);
            if (!AssetDatabase.LoadAssetAtPath<SceneAsset>(ResearchScenePath))
            {
                if (!AssetDatabase.CopyAsset(SourceScenePath, ResearchScenePath))
                    throw new IOException("Could not create the isolated M37 Research scene copy.");
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }

            Scene researchScene = EditorSceneManager.OpenScene(ResearchScenePath, OpenSceneMode.Single);
            M9VirtualManipulationBootstrap bootstrap = UnityEngine.Object.FindObjectOfType<M9VirtualManipulationBootstrap>();
            if (bootstrap == null)
                throw new InvalidOperationException("The copied Research scene has no M9 bootstrap.");

            SerializedObject serializedBootstrap = new SerializedObject(bootstrap);
            SerializedProperty mode = serializedBootstrap.FindProperty("m_selectionInteractionMode");
            SerializedProperty serverHost = serializedBootstrap.FindProperty("m_selectionServerHost");
            SerializedProperty serverPort = serializedBootstrap.FindProperty("m_selectionServerPort");
            SerializedProperty useM20DeskProfile = serializedBootstrap.FindProperty("m_useM20AssistiveDeskProfile");
            if (mode == null || serverHost == null || serverPort == null || useM20DeskProfile == null)
                throw new InvalidOperationException("The copied scene bootstrap is missing an expected serialized field.");

            mode.intValue = (int)BciSelectionInteractionMode.ResearchAcquisitionV1;
            serverHost.stringValue = questServerHost;
            serverPort.intValue = SelectionPort;
            useM20DeskProfile.boolValue = false;
            serializedBootstrap.ApplyModifiedPropertiesWithoutUndo();
            EditorSceneManager.MarkSceneDirty(researchScene);
            if (!EditorSceneManager.SaveScene(researchScene))
                throw new IOException("Could not save the isolated M37 Research scene.");

            previousApplicationIdentifier = PlayerSettings.GetApplicationIdentifier(BuildTargetGroup.Android);
            previousApplicationIdentifierOverride = ReadProjectSettingsValue("overrideDefaultApplicationIdentifier");
            previousProductName = PlayerSettings.productName;
            previousForceInternetPermission = PlayerSettings.Android.forceInternetPermission;
            previousBuildAppBundle = EditorUserBuildSettings.buildAppBundle;
            settingsCaptured = true;

            // These settings apply only while producing this separate APK.
            // Force INTERNET because the project's custom Android manifest is
            // shared with Showcase and intentionally remains untouched.
            PlayerSettings.SetApplicationIdentifier(
                BuildTargetGroup.Android, ResearchApplicationIdentifier);
            PlayerSettings.productName = ResearchProductName;
            PlayerSettings.Android.forceInternetPermission = true;
            EditorUserBuildSettings.buildAppBundle = false;

            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { ResearchScenePath },
                locationPathName = outputPath,
                target = BuildTarget.Android,
                options = BuildOptions.Development
            });

            bool succeeded = report.summary.result == BuildResult.Succeeded && File.Exists(outputPath);
            string apkSha256 = succeeded ? Sha256File(outputPath) : string.Empty;
            File.WriteAllText(reportPath,
                "{\n" +
                "  \"status\": \"" + (succeeded ? "PASS" : "FAIL") + "\",\n" +
                "  \"scene\": \"" + ResearchScenePath + "\",\n" +
                "  \"interactionMode\": \"ResearchAcquisitionV1\",\n" +
                "  \"questServer\": \"" + questServerHost + ":" + SelectionPort + "\",\n" +
                "  \"applicationIdentifier\": \"" + ResearchApplicationIdentifier + "\",\n" +
                "  \"apkPath\": \"" + outputPath.Replace("\\", "\\\\") + "\",\n" +
                "  \"apkSha256\": \"" + apkSha256 + "\",\n" +
                "  \"errors\": " + report.summary.totalErrors + ",\n" +
                "  \"warnings\": " + report.summary.totalWarnings + "\n" +
                "}\n");

            Debug.Log("M37_QUEST_BUILD status=" + (succeeded ? "PASS" : "FAIL") +
                      " result=" + report.summary.result +
                      " errors=" + report.summary.totalErrors +
                      " warnings=" + report.summary.totalWarnings +
                      " mode=ResearchAcquisitionV1 scene=" + ResearchScenePath +
                      " package=" + ResearchApplicationIdentifier +
                      " output=" + outputPath);
            exitCode = succeeded ? 0 : 1;
        }
        catch (Exception error)
        {
            Debug.LogException(error);
            exitCode = 1;
        }
        finally
        {
            if (settingsCaptured)
            {
                try
                {
                    PlayerSettings.SetApplicationIdentifier(
                        BuildTargetGroup.Android, previousApplicationIdentifier);
                    PlayerSettings.productName = previousProductName;
                    PlayerSettings.Android.forceInternetPermission = previousForceInternetPermission;
                    EditorUserBuildSettings.buildAppBundle = previousBuildAppBundle;
                    AssetDatabase.SaveAssets();
                    RestoreProjectSettingsValue(
                        "overrideDefaultApplicationIdentifier", previousApplicationIdentifierOverride);
                }
                catch (Exception settingsRestoreError)
                {
                    Debug.LogError("M37_QUEST_BUILD settings_restore_failed=" + settingsRestoreError.GetType().Name);
                    exitCode = 1;
                }
            }

            try
            {
                if (previousSceneSetup != null && previousSceneSetup.Length > 0)
                    EditorSceneManager.RestoreSceneManagerSetup(previousSceneSetup);
            }
            catch (Exception restoreError)
            {
                Debug.LogError("M37_QUEST_BUILD scene_restore_failed=" + restoreError.GetType().Name);
                exitCode = 1;
            }

            Debug.Log("M37_QUEST_BUILD cleanup_complete=true enabledScenesUntouched=true");
            EditorApplication.Exit(exitCode);
        }
    }

    private static string Sha256File(string path)
    {
        using (SHA256 sha = SHA256.Create())
        using (FileStream stream = File.OpenRead(path))
        {
            byte[] digest = sha.ComputeHash(stream);
            return BitConverter.ToString(digest).Replace("-", string.Empty).ToLowerInvariant();
        }
    }

    private static string ProjectSettingsAssetPath()
    {
        return Path.GetFullPath(Path.Combine(Application.dataPath, "../ProjectSettings/ProjectSettings.asset"));
    }

    private static string ReadProjectSettingsValue(string key)
    {
        string contents = File.ReadAllText(ProjectSettingsAssetPath());
        string prefix = "  " + key + ": ";
        int start = contents.IndexOf(prefix, StringComparison.Ordinal);
        if (start < 0 || contents.IndexOf(prefix, start + prefix.Length, StringComparison.Ordinal) >= 0)
            throw new InvalidOperationException("Could not identify one serialized PlayerSettings value: " + key);
        int valueStart = start + prefix.Length;
        int valueEnd = contents.IndexOfAny(new[] { '\r', '\n' }, valueStart);
        if (valueEnd < 0)
            valueEnd = contents.Length;
        return contents.Substring(valueStart, valueEnd - valueStart);
    }

    private static void RestoreProjectSettingsValue(string key, string value)
    {
        if (value == null)
            return;
        string path = ProjectSettingsAssetPath();
        string contents = File.ReadAllText(path);
        string prefix = "  " + key + ": ";
        int start = contents.IndexOf(prefix, StringComparison.Ordinal);
        if (start < 0 || contents.IndexOf(prefix, start + prefix.Length, StringComparison.Ordinal) >= 0)
            throw new InvalidOperationException("Could not restore one serialized PlayerSettings value: " + key);
        int valueStart = start + prefix.Length;
        int valueEnd = contents.IndexOfAny(new[] { '\r', '\n' }, valueStart);
        if (valueEnd < 0)
            valueEnd = contents.Length;
        string restored = contents.Substring(0, valueStart) + value + contents.Substring(valueEnd);
        File.WriteAllBytes(path, Encoding.UTF8.GetBytes(restored));
    }
}
