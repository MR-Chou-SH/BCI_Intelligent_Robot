using System;
using System.Collections.Generic;
using System.IO;
using BCIIntelligentRobot.Kitchen;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.Rendering;
using UnityEngine.UI;

namespace BCIIntelligentRobot.Kitchen.Editor
{
    // Independent offline-record and PC-authoritative live variants share visual assets only.
    public static class KitchenDemoResearchBuild
    {
        public const string DemoPC="Assets/BCI/Kitchen/BuildScenes/KitchenDemoPC.unity";
        public const string DemoQuest="Assets/BCI/Kitchen/BuildScenes/KitchenDemoQuest.unity";
        public const string DemoPackage="org.bci.kitchendemo.v3";
        public const string ResearchPC="Assets/BCI/Kitchen/BuildScenes/KitchenResearchPC.unity";
        public const string ResearchQuest="Assets/BCI/Kitchen/BuildScenes/KitchenResearchQuest.unity";
        public const string ResearchPackage="org.bci.kitchenresearch.v3";
        static string CatalogResource => Environment.GetEnvironmentVariable("KITCHEN_DEMO_CATALOG")??"KitchenDemoV3/catalog";
        const string ProbeKey="KitchenDemo.Probe";
        static int step;static double next,pausedSeconds,replayStartedAt,replayClockError;
        static KitchenDemoPlayback player;static KitchenDemoUI ui;
        static readonly List<string> checks=new List<string>();
        public static string Output()
        {
            var path=Environment.GetEnvironmentVariable("KITCHEN_DEMO_OUTPUT");
            if(string.IsNullOrEmpty(path))throw new InvalidOperationException("Explicit KITCHEN_DEMO_OUTPUT required");
            Directory.CreateDirectory(path);return path;
        }
        static Camera MakeCamera(string name)
        {
            var camera=new GameObject(name).AddComponent<Camera>();camera.nearClipPlane=.03f;camera.farClipPlane=20;
            camera.clearFlags=CameraClearFlags.SolidColor;camera.backgroundColor=new Color(.65f,.72f,.73f);
            camera.allowHDR=true;return camera;
        }
        static void Light(string name,Vector3 angles,float intensity,Color color)
        {
            var light=new GameObject(name).AddComponent<Light>();light.type=LightType.Directional;
            light.transform.rotation=Quaternion.Euler(angles);light.color=color;light.intensity=intensity;
            light.shadows=LightShadows.Soft;light.shadowStrength=.55f;light.shadowBias=.035f;light.shadowNormalBias=.12f;
        }
        public static void CreateDemoScene(bool quest,string path)
        {
            CreateKitchenScene(quest,path,false);
        }
        public static void CreateResearchScene(bool quest,string path)
        {
            CreateKitchenScene(quest,path,true);
        }
        static void CreateKitchenScene(bool quest,string path,bool research)
        {
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
            var root=new GameObject(research?"KitchenResearch_PC_Authority":"KitchenDemo_OfflineOnly");
            var mirror=root.AddComponent<KitchenSceneMirror>();mirror.resourceRoot="KitchenPlatformV3/ModelCandidate013";
            root.AddComponent<KitchenPlatformArt>().Mirror=mirror;
            var desktop=MakeCamera("DemoDesktopCamera");desktop.tag="MainCamera";
            desktop.transform.position=new Vector3(2.55f,2.65f,-3.35f);desktop.transform.LookAt(new Vector3(0,.8f,.02f));desktop.fieldOfView=44;
            if(research)
            {
                string host=Environment.GetEnvironmentVariable("KITCHEN_RESEARCH_HOST")??"127.0.0.1";
                int worldPort=int.TryParse(Environment.GetEnvironmentVariable("KITCHEN_RESEARCH_WORLD_PORT"),out int wp)?wp:11032;
                int plannerPort=int.TryParse(Environment.GetEnvironmentVariable("KITCHEN_RESEARCH_PLANNER_PORT"),out int pp)?pp:11031;
                var view=root.AddComponent<KitchenPlatformView>();view.Mirror=mirror;view.ServerHost=host;view.WorldPort=worldPort;
                var controls=root.AddComponent<KitchenResearchControls>();controls.ServerHost=host;controls.PlannerPort=plannerPort;
                controls.Mirror=mirror;controls.View=view;controls.DesktopCamera=desktop;controls.Quest=quest;
            }
            else
            {
                var playback=root.AddComponent<KitchenDemoPlayback>();playback.Mirror=mirror;playback.Quest=quest;
                playback.CatalogResource=CatalogResource;
                playback.DesktopCamera=desktop;playback.RecordingCamera=MakeCamera("DemoRecordingCamera");playback.RecordingCamera.enabled=false;
                root.AddComponent<KitchenDemoUI>().Player=playback;
            }
            if(quest)
            {
                var prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Packages/com.meta.xr.sdk.core/Prefabs/OVRCameraRig.prefab");
                if(prefab==null)throw new InvalidOperationException("Existing Meta XR rig missing");
                var rig=(GameObject)PrefabUtility.InstantiatePrefab(prefab);rig.name="KitchenDemoOVRCameraRig";
                rig.transform.position=new Vector3(0,0,-2.55f);desktop.gameObject.SetActive(false);
            }
            RenderSettings.ambientMode=AmbientMode.Trilight;
            RenderSettings.ambientSkyColor=new Color(.62f,.69f,.73f);RenderSettings.ambientEquatorColor=new Color(.47f,.49f,.46f);
            RenderSettings.ambientGroundColor=new Color(.26f,.29f,.28f);
            Light("WindowKey",new Vector3(38,-32,0),1.28f,new Color(1,.96f,.88f));
            Light("CoolFill",new Vector3(46,135,0),.55f,new Color(.81f,.89f,1));
            QualitySettings.shadows=ShadowQuality.All;QualitySettings.shadowDistance=12;
            if(!EditorSceneManager.SaveScene(UnityEngine.SceneManagement.SceneManager.GetActiveScene(),path))throw new IOException("Demo scene save failed");
        }
        [MenuItem("BCI/Kitchen Demo/Open PC Playback")]
        public static void OpenDemoPC()
        {
            if(!File.Exists(DemoPC))CreateDemoScene(false,DemoPC);
            else EditorSceneManager.OpenScene(DemoPC);
        }
        [MenuItem("BCI/Kitchen Demo/Create Dedicated Scenes")]
        public static void CreateDemoScenes()
        {
            CreateDemoScene(true,DemoQuest);
            CreateDemoScene(false,DemoPC);
            AssetDatabase.SaveAssets();
        }
        [MenuItem("BCI/Kitchen Research/Open PC Live View")]
        public static void OpenResearchPC()
        {
            if(!File.Exists(ResearchPC))CreateKitchenScene(false,ResearchPC,true);
            else EditorSceneManager.OpenScene(ResearchPC);
        }
        public static void VerifyDemoInteractive()
        {
            CreateDemoScenes();OpenDemoPC();SessionState.SetBool(ProbeKey,true);SessionState.SetString(ProbeKey+"Output",Output());
            step=0;next=EditorApplication.timeSinceStartup+2;EditorApplication.update-=Probe;EditorApplication.update+=Probe;EditorApplication.isPlaying=true;
        }
        [InitializeOnLoadMethod] static void ResumeProbe()
        {
            EditorApplication.update-=Probe;
            if(SessionState.GetBool(ProbeKey,false)){step=0;next=EditorApplication.timeSinceStartup+2;EditorApplication.update+=Probe;}
        }
        static void Check(bool condition,string name)
        {
            if(!condition)throw new InvalidOperationException("Demo regression failed: "+name);
            checks.Add(name);Debug.Log("KITCHEN_DEMO_CHECK "+name);
        }
        static void Click(Button button)
        {
            ExecuteEvents.Execute(button.gameObject,new PointerEventData(EventSystem.current){button=PointerEventData.InputButton.Left},ExecuteEvents.pointerClickHandler);
        }
        public static void Screenshot(Camera camera,string path)
        {
            Canvas.ForceUpdateCanvases();var target=new RenderTexture(1440,900,24);var old=camera.targetTexture;var active=RenderTexture.active;
            var image=new Texture2D(1440,900,TextureFormat.RGB24,false);
            try{camera.targetTexture=target;camera.Render();RenderTexture.active=target;image.ReadPixels(new Rect(0,0,1440,900),0,0);image.Apply();File.WriteAllBytes(path,image.EncodeToPNG());}
            finally{camera.targetTexture=old;RenderTexture.active=active;target.Release();UnityEngine.Object.DestroyImmediate(target);UnityEngine.Object.DestroyImmediate(image);}
        }
        [Serializable] sealed class ProbeReport
        {
            public string status,error,quest="HARDWARE_REQUIRED",physics="NO_NEW_DYNAMICS",evidence="REAL_UNITY_EDITOR_PLAY_MODE";
            public string[] checks;public long applied_frames;public string unity;public double replay_clock_error_s;
        }
        static void Probe()
        {
            if(!EditorApplication.isPlaying || EditorApplication.timeSinceStartup<next)return;
            string output=SessionState.GetString(ProbeKey+"Output","");
            try
            {
                if(step==0)
                {
                    player=UnityEngine.Object.FindFirstObjectByType<KitchenDemoPlayback>();ui=UnityEngine.Object.FindFirstObjectByType<KitchenDemoUI>();
                    if(player==null || player.Catalog==null || ui.PlayButton==null)return;
                    Check(player.State=="READY" && player.Episode.id=="apple","real_scene_ready_fixed_apple");
                    Check(UnityEngine.Object.FindObjectsByType<KitchenPlatformView>(FindObjectsSortMode.None).Length==0 &&
                        UnityEngine.Object.FindObjectsByType<KitchenQuestTeleop>(FindObjectsSortMode.None).Length==0,"no_live_transport_or_teleop");
                    Check(UnityEngine.Object.FindObjectsByType<Rigidbody>(FindObjectsSortMode.None).Length==0,"no_unity_rigidbody_authority");
                    VerifySeatedLayout(output);
                    MeasurePlaybackCost(output);
                    Screenshot(player.DesktopCamera,Path.Combine(output,"01_ready.png"));Click(ui.PlayButton);
                }
                else if(step==1)
                {
                    if(player.State=="PLAYING" && player.Seconds<=.1 && EditorApplication.timeSinceStartup<next+5)
                    {
                        EditorApplication.QueuePlayerLoopUpdate();return;
                    }
                    Check(player.State=="PLAYING" && player.Seconds>.1,"ui_play_advances state="+player.State+" seconds="+player.Seconds);
                    Click(ui.PlayButton);pausedSeconds=player.Seconds;
                }
                else if(step==2)
                {
                    Check(player.State=="PAUSED" && player.Seconds==pausedSeconds,"ui_pause_holds");
                    // A pre-command Editor stall must not be charged to the new playback clock.
                    System.Threading.Thread.Sleep(600);Click(ui.ReplayButton);replayStartedAt=Time.realtimeSinceStartupAsDouble;
                    Check(player.Seconds==0 && player.State=="PLAYING","ui_replay_resets_epoch");
                }
                else if(step==3)
                {
                    replayClockError=player.Seconds-(Time.realtimeSinceStartupAsDouble-replayStartedAt);
                    Debug.Log("KITCHEN_DEMO_REPLAY_CLOCK_ERROR_S "+replayClockError.ToString("R"));
                    Check(Math.Abs(replayClockError)<.1,"replay_does_not_charge_precommand_stall");
                    double waterTime=(player.Episode.water_events[0].time_s+player.Episode.water_events[1].time_s)/2;
                    Click(ui.PlayButton);ui.Timeline.value=(float)(waterTime/player.Episode.duration_s);
                    Click(ui.ViewButtons[1]);Check(Math.Abs(player.Seconds-waterTime)<.001 && player.ViewIndex==1 && (player.DeviceFlags&1)!=0,"ui_seek_recorded_water_and_sink_view");
                }
                else if(step==4)
                {
                    Canvas.ForceUpdateCanvases();
                    var track=ui.Timeline.GetComponent<RectTransform>();var corners=new Vector3[4];
                    ui.Timeline.fillRect.GetWorldCorners(corners);
                    foreach(var corner in corners)
                    {
                        var local=track.InverseTransformPoint(corner);
                        Check(local.x>=track.rect.xMin-.1f && local.x<=track.rect.xMax+.1f &&
                            local.y>=track.rect.yMin-.1f && local.y<=track.rect.yMax+.1f,"timeline_fill_inside_track");
                    }
                    Check(Mathf.Abs(ui.Timeline.fillRect.rect.height-17)<.1f,"timeline_fill_has_stable_height");
                    Screenshot(player.DesktopCamera,Path.Combine(output,"02_apple_water.png"));player.Capture(output);
                    Click(ui.ActionButtons[1]);Check(player.State=="READY" && player.Seconds==0 && player.Episode.id=="burger","ui_action_switch_independent_episode");
                    ui.Timeline.value=.75f;Click(ui.ViewButtons[2]);
                }
                else if(step==5)
                {
                    Screenshot(player.DesktopCamera,Path.Combine(output,"03_burger_microwave.png"));
                    foreach(var button in ui.ViewButtons)Click(button);
                    Check(player.ViewIndex==3,"all_recording_view_buttons");
                    player.Seek(player.Episode.duration_s);Check(player.Mirror.LastTick==player.RecordedTerminalTick && player.RecordedTerminalTick==64160,"complete_burger_terminal_tick");
                    Click(ui.ActionButtons[0]);player.Seek(player.Episode.duration_s);Check(player.Mirror.LastTick==player.RecordedTerminalTick && player.RecordedTerminalTick>100000,"complete_apple_terminal_tick");
                    Check(KitchenDemoPlayback.SupportedCatalog(player.Catalog),"declared_recorded_control_identity_supported");
                    if(player.Catalog.schema==2)
                    {
                        string saved=player.Episode.control_hash;player.Episode.control_hash=player.Catalog.episodes[1].control_hash;
                        Check(!KitchenDemoPlayback.SupportedCatalog(player.Catalog),"mixed_record_control_mislabel_rejected");player.Episode.control_hash=saved;
                    }
                    Click(ui.ViewButtons[3]);
                }
                else if(step==6)
                {
                    Screenshot(player.DesktopCamera,Path.Combine(output,"04_apple_tray_terminal.png"));
                    var episode=player.Episode;var bytes=Resources.Load<TextAsset>(episode.resource).bytes;bytes[bytes.Length-1]^=1;
                    bool rejected=false;try{KitchenDemoPlayback.Decode(bytes,episode,Array.ConvertAll(player.Mirror.Manifest.bodies,b=>b.id));}catch(InvalidDataException){rejected=true;}
                    Check(rejected,"corrupt_record_sha_rejected");Click(ui.ActionButtons[0]);Click(ui.ViewButtons[0]);
                    var invalidRoot=new GameObject("GoldenWrongModel_NotPhysics");invalidRoot.SetActive(false);
                    try
                    {
                        var wrong=invalidRoot.AddComponent<KitchenSceneMirror>();wrong.resourceRoot="KitchenPlatformV3/ModelCandidate005";
                        invalidRoot.AddComponent<KitchenPlatformArt>().Mirror=wrong;
                        var failed=invalidRoot.AddComponent<KitchenDemoPlayback>();failed.Mirror=wrong;
                        bool mismatch=false;try{failed.Initialize();}catch(InvalidDataException){mismatch=true;}
                        Check(mismatch && failed.Catalog==null && !failed.IsReady && failed.State=="IDENTITY REJECTED","initialization_failure_not_published");
                        failed.Replay();failed.TogglePlay();failed.Seek(10);failed.SelectEpisode(1);failed.Initialize();
                        Check(failed.State=="IDENTITY REJECTED" && failed.AppliedFrames==0 && failed.Catalog==null,"rejected_record_cannot_be_replayed_or_reinitialized");
                    }
                    finally{UnityEngine.Object.DestroyImmediate(invalidRoot);}
                    File.WriteAllText(Path.Combine(output,"editor_interaction_report.json"),JsonUtility.ToJson(new ProbeReport {status="PC_EDITOR_INTERACTION_PASS",checks=checks.ToArray(),applied_frames=player.AppliedFrames,unity=Application.unityVersion,replay_clock_error_s=replayClockError},true));
                    FinishProbe(false);return;
                }
                step++;next=EditorApplication.timeSinceStartup+.65;
            }
            catch(Exception error)
            {
                Debug.LogException(error);
                File.WriteAllText(Path.Combine(output,"editor_interaction_report.json"),JsonUtility.ToJson(new ProbeReport {status="FAIL",error=error.ToString(),checks=checks.ToArray(),replay_clock_error_s=replayClockError},true));
                FinishProbe(true);
            }
        }
        static void FinishProbe(bool error)
        {
            SessionState.SetBool(ProbeKey,false);EditorApplication.update-=Probe;
            EditorApplication.Exit(error?1:0);
        }
        static void VerifySeatedLayout(string output)
        {
            var origin=new GameObject("DemoSeatProbe");var head=new GameObject("HeadProbe");head.transform.SetParent(origin.transform,false);
            var camera=head.AddComponent<Camera>();camera.nearClipPlane=.03f;camera.fieldOfView=95;camera.stereoTargetEye=StereoTargetEyeMask.None;
            try
            {
                foreach(float height in new[]{0f,1.65f})
                {
                    origin.transform.SetPositionAndRotation(new Vector3(0,0,-2.55f),Quaternion.identity);
                    head.transform.localPosition=new Vector3(.22f,height,-.13f);head.transform.localRotation=Quaternion.Euler(8,37,0);
                    Vector3 local=head.transform.localPosition;Quaternion rotation=head.transform.localRotation;
                    KitchenDemoUI.AlignSeatedOrigin(origin.transform,head.transform);
                    Check(Vector3.Distance(head.transform.position,KitchenDemoUI.SeatedEye)<.00001f,"seated_eye_device_and_floor_"+height);
                    Check(head.transform.localPosition==local && Quaternion.Angle(head.transform.localRotation,rotation)<.001f,"tracked_pose_untouched_"+height);
                    Check(Vector3.Angle(Vector3.ProjectOnPlane(head.transform.forward,Vector3.up),Vector3.forward)<.01f,"seated_faces_counter_"+height);
                }
                camera.transform.rotation=Quaternion.identity;
                bool wasQuest=player.Quest;player.Quest=true;var wallRoot=new GameObject("NearPanelProbe");var rigObject=new GameObject("RigProbe");
                try
                {
                    var rig=rigObject.AddComponent<OVRCameraRig>();rig.centerEyeAnchor.SetPositionAndRotation(camera.transform.position,Quaternion.identity);
                    var wall=wallRoot.AddComponent<KitchenDemoUI>();wall.Player=player;wall.Build();wall.Canvas.worldCamera=camera;
                    Check(wall.Canvas.renderMode==RenderMode.WorldSpace && wall.VrPanel.Expanded,"real_demo_generator_near_layout");
                    Check(Vector3.Distance(wall.Canvas.transform.position,camera.transform.position)>=1 &&
                        Vector3.Distance(wall.Canvas.transform.position,camera.transform.position)<=1.3f,"quest_panel_readable_near_distance");
                    Check(Math.Abs(wall.Canvas.transform.position.x-camera.transform.position.x)<.001f,"near_panel_centred_in_seated_view");
                    Check(wall.ActionButtons.Length==2 && wall.ViewButtons.Length==4 && wall.SpeedButtons.Length==3 && wall.PlayButton.interactable && wall.Timeline.interactable,"near_all_controls_retained");
                    Check(wallRoot.GetComponent<KitchenDemoRayInput>().MaxDistance>=1.3f,"demo_ray_reaches_near_panel");
                    Check(wall.ActionButtons[0].image.material.shader.name=="BCI/Kitchen/WorldPanel","near_panel_overlay_material_keeps_buttons_visible");
                    Screenshot(camera,Path.Combine(output,"00_near_panel_expanded.png"));
                    Canvas.ForceUpdateCanvases();var rect=wall.ActionButtons[0].GetComponent<RectTransform>();
                    var pointer=new PointerEventData(EventSystem.current){position=RectTransformUtility.WorldToScreenPoint(camera,rect.TransformPoint(rect.rect.center))};
                    var hits=new List<RaycastResult>();wall.Canvas.GetComponent<GraphicRaycaster>().Raycast(pointer,hits);
                    Check(hits.Exists(hit=>hit.gameObject==wall.ActionButtons[0].gameObject),"near_button_actual_graphic_raycast");
                    Vector3 trackedLocal=rig.centerEyeAnchor.localPosition;Quaternion trackedRotation=rig.centerEyeAnchor.localRotation;
                    Click(wall.PlayButton);Check(player.State=="PLAYING" && !wall.VrPanel.Expanded,"play_collapses_to_compact_controls");
                    Screenshot(camera,Path.Combine(output,"00_near_panel_compact.png"));
                    Click(wall.CompactPlayButton);Check(player.State=="PAUSED","compact_pause_actual_playback");
                    Click(wall.ExpandButton);Check(wall.VrPanel.Expanded,"compact_menu_recalls_full_panel");
                    Click(wall.SpeedButtons[1]);Check(player.Speed==2,"explicit_2x_replay_speed");
                    Click(wall.SpeedButtons[2]);Check(player.Speed==4,"explicit_4x_replay_speed");
                    Click(wall.SpeedButtons[0]);Check(player.Speed==1,"original_realtime_replay_available");
                    Check(rig.centerEyeAnchor.localPosition==trackedLocal && Quaternion.Angle(rig.centerEyeAnchor.localRotation,trackedRotation)<.001f,"menu_never_rewrites_tracked_head");
                    player.SelectEpisode(0);
                }
                finally{player.Quest=wasQuest;UnityEngine.Object.DestroyImmediate(wallRoot);UnityEngine.Object.DestroyImmediate(rigObject);}
            }
            finally{UnityEngine.Object.DestroyImmediate(origin);}
        }
        static void MeasurePlaybackCost(string output)
        {
            long probe=GC.GetAllocatedBytesForCurrentThread();var allocationProbe=new byte[1024];
            bool supported=GC.GetAllocatedBytesForCurrentThread()>probe;GC.KeepAlive(allocationProbe);
            long allocated=GC.GetAllocatedBytesForCurrentThread();var clock=System.Diagnostics.Stopwatch.StartNew();
            const int count=600;
            for(int i=0;i<count;i++)player.Seek(player.Episode.duration_s*i/count);
            clock.Stop();allocated=GC.GetAllocatedBytesForCurrentThread()-allocated;player.SelectEpisode(0);
            File.WriteAllText(Path.Combine(output,"presentation_cost.json"),"{\"evidence\":\"EDITOR_SEEK_APPLY_CPU_ONLY_NOT_QUEST_FPS\",\"calls\":"+count+
                ",\"allocation_counter_supported\":"+(supported?"true":"false")+",\"allocated_bytes\":"+(supported?allocated:-1)+",\"elapsed_ms\":"+clock.Elapsed.TotalMilliseconds.ToString("R",System.Globalization.CultureInfo.InvariantCulture)+"}");
        }
        [Serializable] sealed class DemoBuildReport
        {
            public string status,package,label,scene,catalog_sha256,catalog_resource,control_hash,error,unity,export,research_host;
            public int planner_port,world_port,version_code;public string version_name;
            public bool settings_restored;public string quest="HARDWARE_REQUIRED";
        }
        public static void BuildDemoApkBatch()
        {
            BuildApk(false);
        }
        public static void BuildResearchApkBatch()
        {
            BuildApk(true);
        }
        static void BuildApk(bool research)
        {
            var report=new DemoBuildReport {status="FAIL",package=research?ResearchPackage:DemoPackage,label=research?"Kitchen Research":"Kitchen Demo",
                scene=research?ResearchQuest:DemoQuest,unity=Application.unityVersion};
            string product=PlayerSettings.productName,package=PlayerSettings.GetApplicationIdentifier(NamedBuildTarget.Android),version=PlayerSettings.bundleVersion;
            int versionCode=PlayerSettings.Android.bundleVersionCode;
            bool exportSetting=EditorUserBuildSettings.exportAsGoogleAndroidProject,bundle=EditorUserBuildSettings.buildAppBundle;
            try
            {
                CreateDemoScenes();
                if(research)
                {
                    string host=Environment.GetEnvironmentVariable("KITCHEN_RESEARCH_HOST");
                    if(string.IsNullOrWhiteSpace(host) || host=="localhost" || host=="127.0.0.1" || host=="0.0.0.0")
                        throw new InvalidDataException("Research APK requires explicit PC LAN host");
                    report.research_host=host;
                    report.planner_port=int.Parse(Environment.GetEnvironmentVariable("KITCHEN_RESEARCH_PLANNER_PORT")??"11031");
                    report.world_port=int.Parse(Environment.GetEnvironmentVariable("KITCHEN_RESEARCH_WORLD_PORT")??"11032");
                    CreateResearchScene(true,ResearchQuest);
                    CreateResearchScene(false,ResearchPC);
                }
                var catalog=Resources.Load<TextAsset>(CatalogResource);var spec=JsonUtility.FromJson<DemoCatalog>(catalog.text);
                if(!KitchenDemoPlayback.SupportedCatalog(spec))throw new InvalidDataException("Demo must retain declared per-episode recorded control identities");
                report.control_hash=research?"LIVE_PC_EPISODE_TOKEN_NOT_DEMO_RECORD":spec.control_hash;report.catalog_sha256=KitchenDemoPlayback.Hash(catalog.bytes);
                report.catalog_resource=CatalogResource;
                report.export=Environment.GetEnvironmentVariable("KITCHEN_DEMO_EXPORT");
                if(string.IsNullOrEmpty(report.export) || !Path.IsPathRooted(report.export) || report.export.Length>60 || Directory.Exists(report.export))
                    throw new InvalidDataException("New short D export directory required");
                PlayerSettings.productName=report.label;PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android,report.package);
                PlayerSettings.bundleVersion=research?"0.2.0":"0.2.1";PlayerSettings.Android.bundleVersionCode=research?2:3;report.version_name=PlayerSettings.bundleVersion;report.version_code=PlayerSettings.Android.bundleVersionCode;
                EditorUserBuildSettings.exportAsGoogleAndroidProject=true;EditorUserBuildSettings.buildAppBundle=false;
                var build=BuildPipeline.BuildPlayer(new BuildPlayerOptions {scenes=new[]{report.scene},locationPathName=report.export,target=BuildTarget.Android,
                    options=BuildOptions.StrictMode|BuildOptions.CompressWithLz4HC|BuildOptions.AcceptExternalModificationsToPlayer});
                if(build.summary.result!=BuildResult.Succeeded)throw new InvalidOperationException("Unity Demo export failed: "+build.summary.result);
                report.status=research?"RESEARCH_UNITY_EXPORT_PASS":"DEMO_UNITY_EXPORT_PASS";
            }
            catch(Exception error){report.error=error.ToString();Debug.LogException(error);}
            finally
            {
                PlayerSettings.productName=product;PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android,package);
                PlayerSettings.bundleVersion=version;PlayerSettings.Android.bundleVersionCode=versionCode;
                EditorUserBuildSettings.exportAsGoogleAndroidProject=exportSetting;EditorUserBuildSettings.buildAppBundle=bundle;
                report.settings_restored=PlayerSettings.productName==product && PlayerSettings.GetApplicationIdentifier(NamedBuildTarget.Android)==package &&
                    EditorUserBuildSettings.exportAsGoogleAndroidProject==exportSetting && EditorUserBuildSettings.buildAppBundle==bundle;
                File.WriteAllText(Path.Combine(Output(),"demo_build_report.json"),JsonUtility.ToJson(report,true));
                EditorApplication.Exit(report.status==(research?"RESEARCH_UNITY_EXPORT_PASS":"DEMO_UNITY_EXPORT_PASS") && report.settings_restored?0:1);
            }
        }
    }
}
