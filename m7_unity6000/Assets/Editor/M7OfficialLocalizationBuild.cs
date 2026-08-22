using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>
/// One-off command-line Android build entry point for the independent official-sample validation.
/// It preserves the sample's enabled scenes and does not alter its runtime localization chain.
/// </summary>
public static class M7OfficialLocalizationBuild
{
    private const string OutputDirectory = "Builds/M7OfficialLocalization";
    private const string OutputApkName = "M7OfficialLocalization.apk";

    public static void BuildQuestValidationApk()
    {
        if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
        {
            Debug.LogError("M7_OFFICIAL_BUILD expected active build target Android.");
            EditorApplication.Exit(2);
            return;
        }

        string outputDirectory = Path.GetFullPath(OutputDirectory);
        Directory.CreateDirectory(outputDirectory);
        string outputPath = Path.Combine(outputDirectory, OutputApkName);
        string[] scenes = EditorBuildSettings.scenes
            .Where(scene => scene.enabled)
            .Select(scene => scene.path)
            .ToArray();

        if (scenes.Length == 0)
        {
            Debug.LogError("M7_OFFICIAL_BUILD no enabled scenes in EditorBuildSettings.");
            EditorApplication.Exit(3);
            return;
        }

        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = scenes,
            locationPathName = outputPath,
            target = BuildTarget.Android,
            options = BuildOptions.Development
        });

        Debug.Log("M7_OFFICIAL_BUILD result=" + report.summary.result +
                  " total_errors=" + report.summary.totalErrors +
                  " output=" + outputPath);
        EditorApplication.Exit(report.summary.result == BuildResult.Succeeded ? 0 : 1);
    }
}
