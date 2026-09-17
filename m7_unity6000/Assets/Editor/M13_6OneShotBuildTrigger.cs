using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>
/// One-shot, fail-closed build trigger for the already-open M13.6 Unity project.
/// The editor only builds after an external request file is created; opening the
/// project never starts a build by itself. It does not change scenes or settings.
/// </summary>
[InitializeOnLoad]
public static class M13_6OneShotBuildTrigger
{
    private const string RequestPath = "Builds/M9_RoboArm/m13_6_build.request.json";
    private const string ProcessingPath = "Builds/M9_RoboArm/m13_6_build.request.processing.json";
    private const string ResultPath = "Builds/M9_RoboArm/m13_6_editor_build_result.json";
    private const string OutputPath = "Builds/M9_RoboArm/BCI_M9_2.apk";
    private const string ScenePath = "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity";

    private static bool s_buildInProgress;

    static M13_6OneShotBuildTrigger()
    {
        EditorApplication.update -= PollForRequest;
        EditorApplication.update += PollForRequest;
    }

    private static void PollForRequest()
    {
        if (s_buildInProgress || !File.Exists(RequestPath))
        {
            return;
        }

        s_buildInProgress = true;
        EditorApplication.update -= PollForRequest;
        try
        {
            if (File.Exists(ProcessingPath))
            {
                File.Delete(ProcessingPath);
            }

            File.Move(RequestPath, ProcessingPath);
            BuildQuestApk();
        }
        catch (Exception error)
        {
            WriteResult("exception", 1, error.ToString(), 0, 0);
            Debug.LogError("M13_6_EDITOR_BUILD exception=" + error);
        }
        finally
        {
            if (File.Exists(ProcessingPath))
            {
                File.Delete(ProcessingPath);
            }

            s_buildInProgress = false;
        }
    }

    private static void BuildQuestApk()
    {
        if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android)
        {
            WriteResult("blocked_wrong_build_target", 2, "active build target is not Android", 0, 0);
            Debug.LogError("M13_6_EDITOR_BUILD expected active build target Android.");
            return;
        }

        if (!File.Exists(ScenePath))
        {
            WriteResult("blocked_missing_scene", 3, ScenePath, 0, 0);
            Debug.LogError("M13_6_EDITOR_BUILD missing scene=" + ScenePath);
            return;
        }

        string outputDirectory = Path.GetDirectoryName(OutputPath);
        Directory.CreateDirectory(outputDirectory);
        BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
        {
            scenes = new[] { ScenePath },
            locationPathName = OutputPath,
            target = BuildTarget.Android,
            options = BuildOptions.AutoRunPlayer
        });

        int exitCode = report.summary.result == BuildResult.Succeeded ? 0 : 1;
        string message = "result=" + report.summary.result +
                         " totalErrors=" + report.summary.totalErrors +
                         " totalWarnings=" + report.summary.totalWarnings;
        WriteResult(report.summary.result.ToString(), exitCode, message, report.summary.totalErrors, report.summary.totalWarnings);
        Debug.Log("M13_6_EDITOR_BUILD " + message + " output=" + OutputPath);
    }

    private static void WriteResult(string status, int exitCode, string message, int errors, int warnings)
    {
        try
        {
            string directory = Path.GetDirectoryName(ResultPath);
            Directory.CreateDirectory(directory);
            string json = "{\n" +
                          "  \"status\": \"" + Escape(status) + "\",\n" +
                          "  \"exitCode\": " + exitCode + ",\n" +
                          "  \"message\": \"" + Escape(message) + "\",\n" +
                          "  \"outputPath\": \"" + Escape(OutputPath) + "\",\n" +
                          "  \"scenePath\": \"" + Escape(ScenePath) + "\",\n" +
                          "  \"errorCount\": " + errors + ",\n" +
                          "  \"warningCount\": " + warnings + ",\n" +
                          "  \"utc\": \"" + DateTime.UtcNow.ToString("O") + "\"\n" +
                          "}\n";
            File.WriteAllText(ResultPath, json);
        }
        catch (Exception error)
        {
            Debug.LogError("M13_6_EDITOR_BUILD could not write result=" + error);
        }
    }

    private static string Escape(string value)
    {
        return (value ?? string.Empty).Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n");
    }
}
