using System;
using System.Collections.Generic;
using BCIIntelligentRobot.VirtualManipulation;
using PassthroughCameraSamples.MultiObjectDetection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace BCIIntelligentRobot.Editor
{
    /// <summary>
    /// Creates a dedicated M9 copy of the existing Quest scene. The original
    /// M7 real-target scene remains available and is never edited by this tool.
    /// </summary>
    public static class M9VirtualManipulationSceneBuilder
    {
        private const string SourceScenePath =
            "Assets/PassthroughCameraApiSamples/MultiObjectDetection/MultiObjectDetection.unity";
        private const string VirtualScenePath =
            "Assets/PassthroughCameraApiSamples/MultiObjectDetection/M9VirtualManipulation.unity";
        private const string DetectionRootName = "DetectionManagerPrefab";
        private const string BootstrapRootName = "M9VirtualManipulationBootstrap";

        [MenuItem("Tools/M9/Build Virtual Manipulation Scene")]
        public static void GenerateOrUpdate()
        {
            if (AssetDatabase.LoadAssetAtPath<SceneAsset>(VirtualScenePath) == null)
            {
                if (!AssetDatabase.CopyAsset(SourceScenePath, VirtualScenePath))
                    throw new InvalidOperationException("Could not copy the existing M7 Quest scene to the M9 scene path.");
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
            }

            Scene scene = EditorSceneManager.OpenScene(VirtualScenePath, OpenSceneMode.Single);
            DetectionManager detector = FindComponentInScene<DetectionManager>(scene);
            if (detector == null || detector.gameObject.name != DetectionRootName)
                throw new InvalidOperationException("Expected the copied scene to contain its DetectionManagerPrefab root.");
            detector.gameObject.SetActive(false);
            SetRootActive(scene, "DetectionUiMenuPrefab", false);
            SetRootActive(scene, "SentisInferenceManagerPrefab", false);
            SetRootActive(scene, "PassthroughCameraAccessPrefab", false);
            SetRootActive(scene, "EnvironmentRaycastPrefab", false);

            GameObject bootstrapRoot = FindRoot(scene, BootstrapRootName);
            if (bootstrapRoot == null)
                bootstrapRoot = new GameObject(BootstrapRootName);
            if (bootstrapRoot.GetComponent<M9VirtualManipulationBootstrap>() == null)
                bootstrapRoot.AddComponent<M9VirtualManipulationBootstrap>();

            EditorSceneManager.MarkSceneDirty(scene);
            if (!EditorSceneManager.SaveScene(scene))
                throw new InvalidOperationException("Could not save the M9 virtual manipulation scene.");

            var buildScenes = new List<EditorBuildSettingsScene>
            {
                new EditorBuildSettingsScene(VirtualScenePath, true)
            };
            EditorBuildSettingsScene[] existing = EditorBuildSettings.scenes;
            for (int index = 0; index < existing.Length; index++)
            {
                if (string.Equals(existing[index].path, VirtualScenePath, StringComparison.Ordinal))
                    continue;
                buildScenes.Add(existing[index]);
            }
            EditorBuildSettings.scenes = buildScenes.ToArray();
            Debug.Log("M9_VIRTUAL scene_ready path=" + VirtualScenePath +
                " default_build_scene=true original_real_target_scene_preserved=" + SourceScenePath);
        }

        private static T FindComponentInScene<T>(Scene scene) where T : Component
        {
            GameObject[] roots = scene.GetRootGameObjects();
            for (int rootIndex = 0; rootIndex < roots.Length; rootIndex++)
            {
                T component = roots[rootIndex].GetComponentInChildren<T>(true);
                if (component != null)
                    return component;
            }
            return null;
        }

        private static GameObject FindRoot(Scene scene, string rootName)
        {
            GameObject[] roots = scene.GetRootGameObjects();
            for (int index = 0; index < roots.Length; index++)
            {
                if (string.Equals(roots[index].name, rootName, StringComparison.Ordinal))
                    return roots[index];
            }
            return null;
        }

        private static void SetRootActive(Scene scene, string rootName, bool active)
        {
            GameObject root = FindRoot(scene, rootName);
            if (root == null)
                throw new InvalidOperationException("Expected copied scene root was not found: " + rootName);
            root.SetActive(active);
        }
    }
}
