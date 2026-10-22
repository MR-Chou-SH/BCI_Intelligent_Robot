using System;
using System.IO;
using System.Net;
using System.Net.Sockets;
using BCIIntelligentRobot.Kitchen;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace BCIIntelligentRobot.Kitchen.Editor
{
    public static class KitchenBuild
    {
        public const string ScenePath = "Assets/Scenes/KitchenTeleop.unity";
        const string RigPrefabPath = "Packages/com.meta.xr.sdk.core/Prefabs/OVRCameraRig.prefab";

        public static void CreateAndValidateScene()
        {
            CreateScene();
            ValidateSceneGeometry();
        }

        static void ValidateSceneGeometry()
        {
            var mirror = UnityEngine.Object.FindFirstObjectByType<KitchenSceneMirror>();
            if (mirror == null) throw new InvalidOperationException("Kitchen scene mirror missing");
            mirror.Build();
            int renderers = mirror.GetComponentsInChildren<Renderer>(true).Length;
            int colliders = mirror.GetComponentsInChildren<Collider>(true).Length;
            int rigidbodies = mirror.GetComponentsInChildren<Rigidbody>(true).Length;
            var teleop = UnityEngine.Object.FindFirstObjectByType<KitchenQuestTeleop>();
            var rig = UnityEngine.Object.FindFirstObjectByType<OVRCameraRig>();
            if (!mirror.Built || renderers < 170 || colliders != 0 || rigidbodies != 0 || teleop == null || rig == null)
                throw new InvalidOperationException("Kitchen XR visual/authority contract failed");
            string reportPath = Environment.GetEnvironmentVariable("KITCHEN_UNITY_REPORT");
            if (!string.IsNullOrEmpty(reportPath))
                File.WriteAllText(reportPath, "{\"status\":\"UNITY_EDITOR_GEOMETRY_PASS\",\"renderers\":" + renderers +
                    ",\"colliders\":" + colliders + ",\"rigidbodies\":" + rigidbodies +
                    ",\"xr_rig\":\"OVRCameraRig\",\"tcp_component\":\"KitchenQuestTeleop\",\"quest_hardware\":\"NOT_VERIFIED\"}");
            Debug.Log("KITCHEN_UNITY_EDITOR_GEOMETRY_PASS renderers=" + renderers + " xrRig=OVRCameraRig tcpComponent=true");
        }

        [MenuItem("BCI/Kitchen/Create actual-model visual scene")]
        public static void CreateScene()
        {
            CreateScene(null, 11021, 11022);
        }

        [MenuItem("BCI/Kitchen/Build Quest Kitchen APK")]
        public static void BuildQuestApk()
        {
            string host = Environment.GetEnvironmentVariable("KITCHEN_QUEST_SERVER_HOST");
            if (!IPAddress.TryParse(host, out IPAddress parsedHost) ||
                parsedHost.AddressFamily != AddressFamily.InterNetwork || IPAddress.IsLoopback(parsedHost))
                throw new InvalidOperationException("Set KITCHEN_QUEST_SERVER_HOST to the reachable PC IPv4 before building.");
            string controlPortText = Environment.GetEnvironmentVariable("KITCHEN_QUEST_CONTROL_PORT");
            string worldPortText = Environment.GetEnvironmentVariable("KITCHEN_QUEST_WORLD_PORT");
            int controlPort = string.IsNullOrWhiteSpace(controlPortText) ? 11021 : int.Parse(controlPortText);
            int worldPort = string.IsNullOrWhiteSpace(worldPortText) ? 11022 : int.Parse(worldPortText);
            if (controlPort < 1 || controlPort > 65535 || worldPort < 1 || worldPort > 65535 || controlPort == worldPort)
                throw new InvalidOperationException("Invalid Quest control/world TCP ports.");
            CreateScene(host, controlPort, worldPort);
            ValidateSceneGeometry();

            string output = Environment.GetEnvironmentVariable("KITCHEN_BUILD_OUTPUT");
            if (string.IsNullOrWhiteSpace(output))
                output = @"D:\VLA_Kitchen_Data\unity_builds\kitchen_platform\KitchenTeleop.apk";
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output)));
            bool previousInternetPermission = PlayerSettings.Android.forceInternetPermission;
            BuildReport report;
            try
            {
                PlayerSettings.Android.forceInternetPermission = true;
                var options = new BuildPlayerOptions
                {
                    scenes = new[] { ScenePath },
                    locationPathName = output,
                    target = BuildTarget.Android,
                    options = BuildOptions.StrictMode | BuildOptions.CompressWithLz4HC
                };
                report = BuildPipeline.BuildPlayer(options);
            }
            finally
            {
                PlayerSettings.Android.forceInternetPermission = previousInternetPermission;
            }
            string reportPath = Environment.GetEnvironmentVariable("KITCHEN_BUILD_REPORT");
            if (string.IsNullOrWhiteSpace(reportPath))
                reportPath = @"D:\VLA_Kitchen_Data\unity_builds\kitchen_platform\build_report.json";
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(reportPath)));
            File.WriteAllText(reportPath, "{\"result\":\"" + report.summary.result +
                "\",\"total_errors\":" + report.summary.totalErrors +
                ",\"total_warnings\":" + report.summary.totalWarnings +
                ",\"apk\":\"" + output.Replace("\\", "\\\\") +
                "\",\"pc_server_host\":\"" + host +
                "\",\"pc_control_port\":" + controlPort +
                ",\"pc_world_port\":" + worldPort +
                ",\"quest_hardware\":\"NOT_VERIFIED\"}");
            if (report.summary.result != BuildResult.Succeeded)
                throw new InvalidOperationException("Quest Android build failed: " + report.summary.result);
            Debug.Log("KITCHEN_QUEST_ANDROID_BUILD_PASS apk=" + output + " server=" + host +
                " control=" + controlPort + " world=" + worldPort);
        }

        static void CreateScene(string serverHost, int controlPort, int worldPort)
        {
            var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var root = new GameObject("MuJoCoKitchenMirror");
            root.AddComponent<KitchenSceneMirror>();

            GameObject rigPrefab = AssetDatabase.LoadAssetAtPath<GameObject>(RigPrefabPath);
            if (rigPrefab == null) throw new InvalidOperationException("Meta OVRCameraRig prefab is missing: " + RigPrefabPath);
            var rigObject = (GameObject)PrefabUtility.InstantiatePrefab(rigPrefab);
            rigObject.name = "KitchenOVRCameraRig";
            rigObject.transform.position = new Vector3(0, 0, -1.55f);
            rigObject.transform.rotation = Quaternion.identity;
            var teleopObject = new GameObject("KitchenQuestTeleop");
            var teleop = teleopObject.AddComponent<KitchenQuestTeleop>();
            teleop.ServerHost = serverHost ?? string.Empty;
            teleop.ControlPort = controlPort;
            teleop.WorldPort = worldPort;

            var light = new GameObject("KitchenLight").AddComponent<Light>();
            light.type = LightType.Directional; light.intensity = 1.3f;
            light.transform.rotation = Quaternion.Euler(45, -30, 0);
            RenderSettings.ambientLight = new Color(.4f, .4f, .4f);
            Directory.CreateDirectory("Assets/Scenes");
            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.SaveAssets();
            Debug.Log("KITCHEN_SCENE_CREATED geometryOnly=true xrRig=OVRCameraRig hardware=NOT_VERIFIED");
        }
    }
}
